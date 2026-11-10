"""Deterministic Markdown Pipe-Table & Flow Sentence Parser (`lattice.regiment.markdown_tables`).

Zero LLM calls, zero `spacy`/`torch`/`onnxruntime` dependencies (`< 5ms` per turn).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any

from bowthruster.nlp import restore_ascii_dots

CANONICAL_PIPE_TABLE_HEADERS: tuple[str, ...] = (
    "| Charter | Summary | Principles |",
    "| Component | Role | Owner | Stage | Depends_On | Files |",
    "| Flow | Source | Target | Payload | Condition |",
    "| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |",
    "| Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |",
    "| Schema | Component | Fields | Owner | Stage | File | Spec |",
    "| Rule | Component | Type | Property | Op | Value | Condition |",
)
CANONICAL_HEADERS_ERROR_MSG = (
    "Input contains no valid Markdown pipe tables (or deterministic FLOW sentences); "
    "expected one or more canonical pipe tables:\n  - "
    + "\n  - ".join(CANONICAL_PIPE_TABLE_HEADERS)
)

_EMPTY_TOKENS = frozenset({"", "none", "-", "—", "n/a", "null", "[]"})

_FLOW_SENTENCE_RE = re.compile(
    r"^(?:During\s+[^,]+,\s*)?(?:The\s+)?`?([A-Za-z0-9_:-]+)`?(?:\s+component|\s+module)?\s+"
    r"(?:sends|returns|routes|passes|emits|forwards|delivers)\s+"
    r"`?([A-Za-z0-9_.,\s/-]+?)`?\s+to\s+(?:the\s+)?`?([A-Za-z0-9_:-]+)`?(?:\s+component|\s+module)?"
    r"(?:\s+when\s+(.+?))?\.?$",
    re.IGNORECASE,
)
_FLOW_ARROW_RE = re.compile(
    r"^(?:>\s*FLOW\s+)?`?([A-Za-z0-9_:-]+)`?\s*--\[([^\]]*)\]-->\s*`?([A-Za-z0-9_:-]+)`?(?:\s*:\s*(.*))?$"
)


def _pick(mapping: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for k in keys:
        if val := mapping.get(k):
            return val
    return default


def _base_mcu(
    next_id: int,
    txt: str,
    scope: str,
    lkind: str,
    payload: dict[str, Any],
    **overrides: Any,
) -> dict[str, Any]:
    mcu: dict[str, Any] = {
        "mcu_id": next_id, "head_id": next_id, "source_chunk_ids": [next_id],
        "text": txt, "canonical_text": txt, "type": "FACT", "facet": "context_facts",
        "primary_scope": scope, "secondary_refs": [],
        "target_prop": None, "bound_op": None, "bound_val": None,
        "conditions": [], "is_exception": False, "superseded_literals": [],
        "meta_keys": [], "meta": {}, "unclear_spans": [], "grounding_status": "GROUNDED",
        "lattice_kind": lkind, "lattice_payload": payload,
    }
    mcu.update(overrides)
    return mcu


class TableScanMode(str, Enum):
    SCANNING = "SCANNING"
    HEADER_CANDIDATE = "HEADER_CANDIDATE"
    IN_TABLE_ROWS = "IN_TABLE_ROWS"


@dataclass
class TableParserState:
    mode: TableScanMode = TableScanMode.SCANNING
    heading_stack: list[tuple[int, str]] = field(default_factory=list)
    current_heading_path: str | None = None
    candidate_headers: list[str] = field(default_factory=list)
    current_rows: list[dict[str, str]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)

    def update_heading(self, lvl: int, title: str) -> None:
        while self.heading_stack and self.heading_stack[-1][0] >= lvl:
            self.heading_stack.pop()
        if title:
            self.heading_stack.append((lvl, title))
        self.current_heading_path = " > ".join(t for _, t in self.heading_stack) if self.heading_stack else None

    def flush_table(self) -> None:
        if self.mode == TableScanMode.IN_TABLE_ROWS and any(self.candidate_headers) and self.current_rows:
            self.tables.append({
                "headers": list(self.candidate_headers),
                "rows": list(self.current_rows),
                "heading_path": self.current_heading_path,
            })
        self.mode = TableScanMode.SCANNING
        self.candidate_headers = []
        self.current_rows = []


def _split_pipe_row(line: str) -> list[str]:
    s = line.strip()
    return [c.strip() for c in s[1:-1].split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(bool(c) and "-" in c and not c.strip(" :-") for c in cells)


def _is_pipe_line(s: str) -> bool:
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _norm_col_key(raw_hdr: str) -> str:
    cleaned = restore_ascii_dots(str(raw_hdr or "")).strip().strip("\"`'*").strip().lower()
    chars = ["_" if ch in ("-", " ", "/", ":") else (ch if (ch.isalnum() or ch == "_") else " ") for ch in cleaned]
    return "_".join(part for part in "".join(chars).split() if part).strip("_")


def _clean_cell_val(raw_val: Any) -> str:
    s = restore_ascii_dots(str(raw_val if raw_val is not None else "")).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("`", '"', "'") and s[0] not in s[1:-1]:
        s = s[1:-1].strip()
    return s.replace("`", "").strip()


def _split_list_cell(val: Any) -> list[str]:
    if isinstance(val, list):
        return [c for x in val if (c := _clean_cell_val(x)) and c.lower() not in _EMPTY_TOKENS]
    raw = _clean_cell_val(val).strip("[]")
    if not raw or raw.lower() in _EMPTY_TOKENS:
        return []
    out: list[str] = []
    for part in raw.replace(";", ",").split(","):
        tok = part.strip().strip("\"`'*").strip()
        if tok and tok.lower() not in _EMPTY_TOKENS and tok not in out:
            out.append(tok)
    return out


def _split_test_paths(val: Any) -> list[str]:
    if isinstance(val, list):
        pieces = [tok for item in val for tok in _clean_cell_val(item).replace(";", " ").replace(",", " ").split()]
    else:
        pieces = _clean_cell_val(val).strip("[]").replace(";", " ").replace(",", " ").split()
    out: list[str] = []
    for raw_tok in pieces:
        tok = raw_tok.strip().strip("\"`'*").strip()
        if tok and tok.lower() not in _EMPTY_TOKENS and tok not in out:
            out.append(tok)
    return out


def _parse_scalar_val(raw: Any) -> int | float | str | None:
    s = _clean_cell_val(raw)
    if not s or s.lower() in _EMPTY_TOKENS:
        return None
    if s.lstrip("+-").isdigit():
        return int(s)
    try:
        return float(s)
    except ValueError:
        return s


def _dedupe_headers(raw_cells: list[str]) -> list[str]:
    out, seen = [], set()
    for c in raw_cells:
        h = restore_ascii_dots(c).strip().strip("\"`'").strip()
        if (nk := _norm_col_key(h)) and nk in seen:
            h = f"{h}_spec"
        if nk:
            seen.add(nk)
        out.append(h)
    return out


def _decompose_rule_expr(
    raw_expr: Any, default_type: str = "BOUND",
) -> tuple[str | None, str | None, Any, list[str]]:
    s = _clean_cell_val(raw_expr)
    if not s or s.lower() in _EMPTY_TOKENS:
        return None, None, None, []
    extra_conds: list[str] = []
    if "->" in s:
        lhs_c, rhs_s = s.split("->", 1)
        c_clean = lhs_c.strip()
        if c_clean.lower().startswith(("when ", "if ")):
            c_clean = c_clean.split(None, 1)[1].strip()
        if c_clean:
            extra_conds.append(c_clean)
        s = rhs_s.strip()
    if s.lower().startswith(("no import ", "forbid import ", "ban import ")):
        return "imports", "!=", s.split(None, 2)[-1].strip().strip(","), extra_conds
    for op in ("<=", ">=", "!=", "==", "<", ">"):
        if op in s:
            lhs_p, rhs_v = s.split(op, 1)
            prop, val = _norm_col_key(lhs_p), _parse_scalar_val(rhs_v)
            if prop and val is not None:
                return prop, op, val, extra_conds
    return _norm_col_key(s) or s, ("!=" if default_type == "PROHIBITION" else None), None, extra_conds


def extract_markdown_tables(raw_text: str) -> list[dict[str, Any]]:
    """Extracts Markdown pipe tables and their active `#` `heading_path` from `raw_text`."""
    if not isinstance(raw_text, str):
        raise ValueError(f"raw_text must be a str, got {type(raw_text).__name__}")
    state = TableParserState()
    for line in raw_text.splitlines():
        s = line.strip()
        if _is_pipe_line(s):
            cells = _split_pipe_row(s)
            if state.mode == TableScanMode.SCANNING:
                state.candidate_headers = _dedupe_headers(cells)
                state.mode = TableScanMode.HEADER_CANDIDATE
            elif state.mode == TableScanMode.HEADER_CANDIDATE:
                if state.candidate_headers and _is_separator_row(cells):
                    state.current_rows = []
                    state.mode = TableScanMode.IN_TABLE_ROWS
                else:
                    state.candidate_headers = _dedupe_headers(cells)
            elif state.mode == TableScanMode.IN_TABLE_ROWS and not _is_separator_row(cells):
                row_dict = {
                    state.candidate_headers[col_idx]: restore_ascii_dots(cells[col_idx].strip())
                    for col_idx in range(min(len(state.candidate_headers), len(cells)))
                    if state.candidate_headers[col_idx]
                }
                if any(v for v in row_dict.values()):
                    state.current_rows.append(row_dict)
            continue
        state.flush_table()
        stripped_lead = line.lstrip()
        if stripped_lead.startswith("#"):
            lvl = len(stripped_lead) - len(stripped_lead.lstrip("#"))
            if 1 <= lvl <= 6 and len(stripped_lead) > lvl and stripped_lead[lvl] == " ":
                title = restore_ascii_dots(stripped_lead[lvl + 1 :].strip()).strip("#").strip()
                state.update_heading(lvl, title)
    state.flush_table()
    return state.tables


def extract_flow_sentences(raw_text: str) -> list[dict[str, Any]]:
    """Extracts deterministic flow sentences outside pipe tables."""
    if not isinstance(raw_text, str):
        return []
    flows: list[dict[str, Any]] = []
    for raw_line in raw_text.splitlines():
        s = raw_line.strip()
        if not s or s.startswith(("|", "#")):
            continue
        for sent in [p.strip() for p in re.split(r"(?<=\.)\s+", s) if p.strip()]:
            if m := (_FLOW_ARROW_RE.match(sent) or _FLOW_SENTENCE_RE.match(sent)):
                src, pay = m.group(1).strip(), (m.group(2).strip() or None)
                tgt, cond = m.group(3).strip(), ((m.group(4) or "").strip() or None)
                flows.append({"source": src, "target": tgt, "payload": pay, "condition": cond, "text": sent})
    return flows


def _classify_table_kind(hset: set[str]) -> str:
    if "charter" in hset or "project" in hset or (
        "summary" in hset and bool(hset & {"principles", "purpose", "why", "non_goals"})
    ):
        return "CHARTER"
    if "flow" in hset or ("source" in hset and "target" in hset):
        return "FLOW"
    if "rule" in hset or "bound" in hset or (
        "type" in hset and bool(hset & {"property", "target_prop", "value"})
    ):
        return "RULE"
    if bool(hset & {"law", "theorem", "bend_dir"}) or ("lhs" in hset and "rhs" in hset):
        return "LAW"
    if bool(hset & {"interface", "endpoint", "signature", "function"}) or (
        "inputs" in hset and bool(hset & {"output", "outputs"}) and not bool(hset & {"depends_on", "role"})
    ):
        return "INTERFACE"
    if bool(hset & {"schema", "typeddict", "dataclass", "packet", "fields"}):
        return "SCHEMA"
    if bool(hset & {"component", "subsystem", "depends_on"}) or (
        "owner" in hset and "stage" in hset
    ) or ("role" in hset and bool(hset & {"files", "tests", "test_files"})):
        return "COMPONENT"
    return "GENERIC"


def extract_structured_table_rows(doc_tables: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    """Normalizes Markdown `doc_tables` into structured row dicts with `'table_kind'` and `'scope'`."""
    tables = extract_markdown_tables(doc_tables) if isinstance(doc_tables, str) else doc_tables
    if not isinstance(tables, list):
        raise ValueError(f"doc_tables must be a list or str, got {type(tables).__name__}")
    structured_rows: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        raw_headers = [str(h) for h in (table.get("headers") or []) if str(h).strip()]
        norm_headers = [_norm_col_key(h) for h in raw_headers]
        hset = {nh for nh in norm_headers if nh}
        if not hset:
            continue
        t_kind = _classify_table_kind(hset)
        hp = table.get("heading_path")
        for raw_row in (table.get("rows") or []):
            if not isinstance(raw_row, dict):
                continue
            col_map: dict[str, Any] = {}
            for r_hdr, n_hdr in zip(raw_headers, norm_headers):
                if n_hdr:
                    raw_v = raw_row.get(r_hdr, raw_row.get(n_hdr, ""))
                    col_map[n_hdr] = (
                        _split_list_cell(raw_v)
                        if n_hdr in ("depends_on", "dependencies", "children")
                        else _clean_cell_val(raw_v)
                    )
            first_val = _clean_cell_val(raw_row.get(raw_headers[0], "")) if raw_headers else ""
            if t_kind == "COMPONENT":
                scope = str(_pick(col_map, "component", "scope", "subsystem", default=first_val or "global_defaults"))
            elif t_kind == "FLOW":
                scope = str(_pick(col_map, "source", "from", default=first_val or "global_defaults"))
            elif t_kind in ("INTERFACE", "LAW", "SCHEMA", "RULE"):
                scope = str(_pick(col_map, "component", "scope", "module", default="global_defaults"))
            elif t_kind == "CHARTER":
                scope = "global_defaults"
            else:
                scope = str(_pick(col_map, "scope", "component", default=first_val or "global_defaults"))
            row_entry: dict[str, Any] = {
                "table_kind": t_kind, **col_map, "scope": scope,
                **({"raw_scope": col_map["scope"]} if "scope" in col_map and col_map["scope"] != scope else {}),
            }
            if isinstance(hp, str) and hp.strip():
                row_entry.setdefault("heading_path", hp.strip())
            structured_rows.append(row_entry)
    return structured_rows


def _build_rule_mcu_from_row(
    srow: dict[str, Any], next_id: int, scope: str, owner: str | None, stage: str | None,
) -> dict[str, Any]:
    has_bound_cols = bool(_pick(srow, "op", "bound_op", "value", "bound_val"))
    raw_type = str(_pick(srow, "type", "facet", default="BOUND" if has_bound_cols else "OBLIGATION")).strip().upper()
    valid_rtypes = ("BOUND", "PROHIBITION", "OBLIGATION", "FACT", "POLICY", "GOAL", "PROPOSAL", "ACTION")
    rtype = "FACT" if raw_type == "CONTEXT_FACTS" else (raw_type if raw_type in valid_rtypes else "BOUND")
    prop_raw = str(_pick(srow, "property", "target_prop", "prop")).strip()
    op = str(_pick(srow, "op", "bound_op")).strip() or None
    val = _parse_scalar_val(srow.get("value", srow.get("bound_val")))
    expr_cell = _pick(srow, "rule_spec", "bound") or (srow.get("rule") if not prop_raw else None)
    extra_c: list[str] = []
    if (not prop_raw or val is None) and expr_cell:
        d_prop, d_op, d_val, extra_c = _decompose_rule_expr(expr_cell, rtype)
        prop_raw, op = (prop_raw or (d_prop or "")), (op or d_op)
        val = d_val if val is None else val
    prop = _norm_col_key(prop_raw) if prop_raw else (str(srow.get("rule") or "").strip() or None)
    op = op or ("==" if rtype in ("FACT", "OBLIGATION") else ("!=" if rtype == "PROHIBITION" else "<="))
    raw_c = str(_pick(srow, "condition", "when")).strip()
    cond_list = ([] if raw_c.lower() in _EMPTY_TOKENS else _split_list_cell(raw_c)) + [c for c in extra_c if c]
    cond = cond_list[0] if cond_list else None
    rid = str(_pick(srow, "rule", "id", "name")).strip()
    spec_txt = str(_pick(srow, "spec", "text", "description")).strip()
    cond_sfx = f" when {', '.join(cond_list)}." if cond_list else "."
    default_txt = (
        f"In `{scope}`, `{prop}` {op} {val}{cond_sfx}"
        if prop and val is not None else f"Rule {rid or next_id} on `{scope}`."
    )
    txt = spec_txt or default_txt
    f_map = {"BOUND": "Numeric_Bounds", "PROHIBITION": "prohibitions", "FACT": "context_facts"}
    facet = f_map.get(rtype, "obligations")
    lkind = "CONSTRAINT" if rtype in ("BOUND", "PROHIBITION", "OBLIGATION") else "FACT"
    payload = {k: v for k, v in (("id", rid), ("owner", owner), ("stage", stage)) if v}
    return _base_mcu(
        next_id, txt, scope, lkind, payload,
        type=rtype, facet=facet, target_prop=prop,
        bound_op=op if val is not None else None, bound_val=val,
        conditions=cond_list, is_exception=bool(cond and cond.lower().startswith(("unless", "except"))),
    )


def _compile_component_row(
    srow: dict[str, Any], next_id: int = 1, scope: str | None = None,
    owner: str | None = None, stage: str | None = None,
) -> dict[str, Any]:
    eff_scope = str(scope or _pick(srow, "scope", "component", default="global_defaults")).strip()
    eff_owner = owner or str(_pick(srow, "owner", "division")).strip() or "core"
    eff_stage = stage or str(_pick(srow, "stage", "phase")).strip() or None
    deps = _split_list_cell(_pick(srow, "depends_on", "dependencies", "deps"))
    files = _split_list_cell(_pick(srow, "files", "file", "raw_scope"))
    tests = _split_test_paths(_pick(srow, "tests", "test_files", "test"))
    has_test_col = any(k in srow for k in ("tests", "test_files", "test"))
    role = str(_pick(srow, "role", "description", "purpose", default=f"Component {eff_scope}")).strip()
    why = str(_pick(srow, "why", "rationale")).strip() or None
    attrs: dict[str, Any] = {"files": list(files), "depends_on": list(deps)}
    payload: dict[str, Any] = {
        "scope": eff_scope, "role": role, "why": why,
        "depends_on": deps, "files": files, "owner": eff_owner, "stage": eff_stage,
    }
    if tests or has_test_col:
        attrs["tests"] = list(tests)
        payload["tests"] = list(tests)
    mcu = _base_mcu(
        next_id, f"{eff_scope}: {role}", eff_scope, "COMPONENT", payload,
        secondary_refs=list(deps), meta=dict(attrs), attributes=attrs,
    )
    if tests or has_test_col:
        mcu["tests"] = list(tests)
    return mcu


def _build_artifact_or_charter_mcu(
    srow: dict[str, Any], next_id: int, t_kind: str, scope: str, owner: str | None, stage: str | None,
) -> dict[str, Any]:
    if t_kind == "CHARTER":
        cname = str(_pick(srow, "charter", "project", "board", default="project")).strip()
        csum = str(_pick(srow, "summary", "purpose", "why")).strip()
        cprin = str(_pick(srow, "principles", "non_goals")).strip()
        txt = f"Charter [{cname}]: {csum}" + (f" | Principles: {cprin}" if cprin else "")
        c_pay = {"name": cname, "summary": csum, "principles": cprin}
        return _base_mcu(next_id, txt, "global_defaults", "CHARTER", c_pay)
    if t_kind == "COMPONENT":
        return _compile_component_row(srow, next_id, scope, owner, stage)
    name = str(_pick(srow, t_kind.lower(), "name", "id", default=scope)).strip()
    spec_s = str(_pick(srow, "spec", "description", "theorem", default=f"{t_kind} {name} ({scope})")).strip()
    ins = _split_list_cell(_pick(srow, "inputs", "fields", "args"))
    outs = _split_list_cell(_pick(srow, "outputs", "output", "returns"))
    payload = {
        "id": name, "name": name, "scope": scope, "spec": spec_s,
        "inputs": ins, "outputs": outs, "owner": owner, "stage": stage,
        "file": str(srow.get("file") or "").strip() or None,
    }
    return _base_mcu(next_id, spec_s, scope, t_kind, payload)


def build_mcus_from_tables_and_flows(
    structured_rows: list[dict[str, Any]],
    flow_sentences: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Builds deterministic `parsed_mcus` directly from structured pipe-table rows and flow sentences."""
    mcus: list[dict[str, Any]] = []
    next_id = 1
    for srow in structured_rows:
        t_kind = str(srow.get("table_kind") or "GENERIC")
        scope = str(srow.get("scope") or "global_defaults").strip() or "global_defaults"
        owner = str(_pick(srow, "owner", "division")).strip() or None
        stage = str(_pick(srow, "stage", "phase")).strip() or None
        if t_kind == "FLOW":
            src = str(_pick(srow, "source", "from", default=scope)).strip()
            tgt = str(_pick(srow, "target", "to", "recipient")).strip()
            if not src or not tgt:
                continue
            fnm = str(_pick(srow, "flow", "id", "name")).strip() or None
            pay = str(_pick(srow, "payload", "contract", "schema", "object", "data")).strip() or None
            raw_c = str(_pick(srow, "condition", "when")).strip()
            cond = None if raw_c.lower() in _EMPTY_TOKENS else raw_c
            txt = f"The {src} component sends {pay or 'data'} to {tgt}."
            payload = {
                "source": src, "target": tgt, "payload": pay, "condition": cond, "summary": txt,
                **({"flow_name": fnm} if fnm and fnm != pay else {}),
            }
            mcus.append(_base_mcu(
                next_id, txt, src, "FLOW", payload,
                secondary_refs=[r for r in (tgt, pay) if r and r != src],
                subject=f"{src} component", predicate="sends", object=pay, recipient=tgt,
                condition=cond, conditions=[cond] if cond else [],
            ))
            next_id += 1
        elif t_kind == "RULE":
            mcus.append(_build_rule_mcu_from_row(srow, next_id, scope, owner, stage))
            next_id += 1
        elif t_kind in ("CHARTER", "COMPONENT", "INTERFACE", "LAW", "SCHEMA"):
            mcus.append(_build_artifact_or_charter_mcu(srow, next_id, t_kind, scope, owner, stage))
            next_id += 1

    for fl in (flow_sentences or []):
        src, tgt, pay, cond, txt = fl["source"], fl["target"], fl.get("payload"), fl.get("condition"), fl["text"]
        payload = {"source": src, "target": tgt, "payload": pay, "condition": cond, "summary": txt}
        mcus.append(_base_mcu(
            next_id, txt, src, "FLOW", payload,
            secondary_refs=[r for r in (tgt, pay) if r and r != src],
            subject=f"{src} component", predicate="sends", object=pay, recipient=tgt,
            condition=cond, conditions=[cond] if cond else [],
        ))
        next_id += 1
    return mcus
