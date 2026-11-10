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
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("`", "\"", "'") and s[0] not in s[1:-1]:
        s = s[1:-1].strip()
    return s.replace("`", "").strip()


def _split_list_cell(val: Any) -> list[str]:
    if isinstance(val, list):
        return [_clean_cell_val(x) for x in val if _clean_cell_val(x) and _clean_cell_val(x).lower() not in ("none", "-", "—", "n/a", "null", "[]")]
    raw = _clean_cell_val(val).strip("[]")
    if not raw or raw.lower() in ("none", "-", "—", "n/a", "null"):
        return []
    out: list[str] = []
    for part in raw.replace(";", ",").split(","):
        tok = part.strip().strip("\"`'*").strip()
        if tok and tok.lower() not in ("none", "-", "—", "n/a", "null") and tok not in out:
            out.append(tok)
    return out


def _parse_scalar_val(raw: Any) -> int | float | str | None:
    s = _clean_cell_val(raw)
    if not s or s.lower() in ("none", "-", "—", "n/a", "null"):
        return None
    if s.lstrip("+-").isdigit():
        return int(s)
    try:
        return float(s)
    except ValueError:
        return s


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
                state.candidate_headers = [restore_ascii_dots(c).strip().strip("\"`'").strip() for c in cells]
                state.mode = TableScanMode.HEADER_CANDIDATE
            elif state.mode == TableScanMode.HEADER_CANDIDATE:
                if state.candidate_headers and _is_separator_row(cells):
                    state.current_rows = []
                    state.mode = TableScanMode.IN_TABLE_ROWS
                else:
                    state.candidate_headers = [restore_ascii_dots(c).strip().strip("\"`'").strip() for c in cells]
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
                state.update_heading(lvl, restore_ascii_dots(stripped_lead[lvl + 1 :].strip()).strip("#").strip())
    state.flush_table()
    return state.tables


def extract_flow_sentences(raw_text: str) -> list[dict[str, Any]]:
    """Extracts deterministic flow sentences (`The <source> component sends <Payload> to <target>.`) outside pipe tables."""
    if not isinstance(raw_text, str):
        return []
    flows: list[dict[str, Any]] = []
    for raw_line in raw_text.splitlines():
        s = raw_line.strip()
        if not s or s.startswith(("|", "#")):
            continue
        for sent in [p.strip() for p in re.split(r"(?<=\.)\s+", s) if p.strip()]:
            if m := _FLOW_ARROW_RE.match(sent):
                src, pay, tgt, cond = m.group(1).strip(), m.group(2).strip() or None, m.group(3).strip(), (m.group(4) or "").strip() or None
                flows.append({"source": src, "target": tgt, "payload": pay, "condition": cond, "text": sent})
            elif m := _FLOW_SENTENCE_RE.match(sent):
                src, pay, tgt, cond = m.group(1).strip(), m.group(2).strip() or None, m.group(3).strip(), (m.group(4) or "").strip() or None
                flows.append({"source": src, "target": tgt, "payload": pay, "condition": cond, "text": sent})
    return flows


def _classify_table_kind(hset: set[str]) -> str:
    if "charter" in hset or "project" in hset or ("summary" in hset and any(k in hset for k in ("principles", "purpose", "why", "non_goals"))):
        return "CHARTER"
    if "flow" in hset or ("source" in hset and "target" in hset):
        return "FLOW"
    if "rule" in hset or "bound" in hset or ("type" in hset and ("property" in hset or "target_prop" in hset or "value" in hset)):
        return "RULE"
    if "law" in hset or "theorem" in hset or "bend_dir" in hset or ("lhs" in hset and "rhs" in hset):
        return "LAW"
    if "interface" in hset or "endpoint" in hset or "signature" in hset or "function" in hset or (
        "inputs" in hset and ("output" in hset or "outputs" in hset) and "depends_on" not in hset and "role" not in hset
    ):
        return "INTERFACE"
    if "schema" in hset or "typeddict" in hset or "dataclass" in hset or "packet" in hset or "fields" in hset:
        return "SCHEMA"
    if "component" in hset or "subsystem" in hset or "depends_on" in hset or ("owner" in hset and "stage" in hset) or ("role" in hset and "files" in hset):
        return "COMPONENT"
    return "GENERIC"


def extract_structured_table_rows(doc_tables: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    """Normalizes Markdown `doc_tables` into structured row dicts with `'table_kind'`, `'scope'`, and column attributes."""
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
                    col_map[n_hdr] = _split_list_cell(raw_v) if n_hdr in ("depends_on", "dependencies", "children") else _clean_cell_val(raw_v)
            first_val = _clean_cell_val(raw_row.get(raw_headers[0], "")) if raw_headers else ""
            if t_kind == "COMPONENT":
                scope = str(col_map.get("component") or col_map.get("scope") or col_map.get("subsystem") or first_val or "global_defaults")
            elif t_kind == "FLOW":
                scope = str(col_map.get("source") or col_map.get("from") or first_val or "global_defaults")
            elif t_kind in ("INTERFACE", "LAW", "SCHEMA", "RULE"):
                scope = str(col_map.get("component") or col_map.get("scope") or col_map.get("module") or "global_defaults")
            elif t_kind == "CHARTER":
                scope = "global_defaults"
            else:
                scope = str(col_map.get("scope") or col_map.get("component") or first_val or "global_defaults")
            row_entry: dict[str, Any] = {"table_kind": t_kind, "scope": scope, **col_map}
            if isinstance(hp, str) and hp.strip():
                row_entry.setdefault("heading_path", hp.strip())
            structured_rows.append(row_entry)
    return structured_rows


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
        owner = str(srow.get("owner") or srow.get("division") or "").strip() or None
        stage = str(srow.get("stage") or srow.get("phase") or "").strip() or None
        if t_kind == "FLOW":
            src = str(srow.get("source") or srow.get("from") or scope).strip()
            tgt = str(srow.get("target") or srow.get("to") or srow.get("recipient") or "").strip()
            if not src or not tgt:
                continue
            fnm = str(srow.get("flow") or srow.get("id") or srow.get("name") or "").strip() or None
            pay = str(srow.get("payload") or srow.get("object") or srow.get("data") or "").strip() or None
            raw_c = str(srow.get("condition") or srow.get("when") or "").strip()
            cond = None if raw_c.lower() in ("", "none", "-", "—", "n/a", "null") else raw_c
            txt = f"The {src} component sends {pay or 'data'} to {tgt}."
            mcus.append({
                "mcu_id": next_id, "head_id": next_id, "source_chunk_ids": [next_id],
                "text": txt, "canonical_text": txt, "type": "FACT", "facet": "context_facts",
                "primary_scope": src, "secondary_refs": [r for r in (tgt, pay) if r and r != src],
                "subject": f"{src} component", "predicate": "sends", "object": pay, "recipient": tgt, "condition": cond,
                "target_prop": None, "bound_op": None, "bound_val": None,
                "conditions": [cond] if cond else [], "is_exception": False, "superseded_literals": [],
                "meta_keys": [], "meta": {}, "unclear_spans": [], "grounding_status": "GROUNDED",
                "lattice_kind": "FLOW",
                "lattice_payload": {"source": src, "target": tgt, "payload": pay, "condition": cond, "summary": txt, **({"flow_name": fnm} if fnm and fnm != pay else {})},
            })
            next_id += 1
        elif t_kind == "RULE":
            raw_type = str(srow.get("type") or srow.get("facet") or ("BOUND" if (srow.get("op") or srow.get("bound_op") or srow.get("value") or srow.get("bound_val")) else "OBLIGATION")).strip().upper()
            rtype = "FACT" if raw_type == "CONTEXT_FACTS" else (raw_type if raw_type in ("BOUND", "PROHIBITION", "OBLIGATION", "FACT", "POLICY", "GOAL", "PROPOSAL", "ACTION") else "BOUND")
            prop = str(srow.get("property") or srow.get("target_prop") or srow.get("prop") or srow.get("rule") or srow.get("bound") or "").strip() or None
            op = str(srow.get("op") or srow.get("bound_op") or ("==" if rtype in ("FACT", "OBLIGATION") else ("!=" if rtype == "PROHIBITION" else "<="))).strip() or None
            val = _parse_scalar_val(srow.get("value", srow.get("bound_val")))
            raw_c = str(srow.get("condition") or srow.get("when") or "").strip()
            cond = None if raw_c.lower() in ("", "none", "-", "—", "n/a", "null") else raw_c
            rid = str(srow.get("rule") or srow.get("id") or srow.get("name") or "").strip()
            spec_txt = str(srow.get("spec") or srow.get("text") or srow.get("description") or "").strip()
            txt = spec_txt or (f"In `{scope}`, `{prop}` {op} {val}" + (f" when {cond}." if cond else ".") if prop and val is not None else f"Rule {rid or next_id} on `{scope}`.")
            facet = "Numeric_Bounds" if rtype == "BOUND" else ("prohibitions" if rtype == "PROHIBITION" else ("context_facts" if rtype == "FACT" else "obligations"))
            is_exc = bool(cond and cond.lower().startswith(("unless", "except")))
            mcus.append({
                "mcu_id": next_id, "head_id": next_id, "source_chunk_ids": [next_id],
                "text": txt, "canonical_text": txt, "type": rtype, "facet": facet,
                "primary_scope": scope, "secondary_refs": [],
                "target_prop": prop, "bound_op": op if val is not None else None, "bound_val": val,
                "conditions": [cond] if cond else [], "is_exception": is_exc, "superseded_literals": [],
                "meta_keys": [], "meta": {}, "unclear_spans": [], "grounding_status": "GROUNDED",
                "lattice_kind": "CONSTRAINT" if rtype in ("BOUND", "PROHIBITION", "OBLIGATION") else "FACT",
                "lattice_payload": {k: v for k, v in (("id", rid), ("owner", owner), ("stage", stage)) if v},
            })
            next_id += 1
        elif t_kind == "CHARTER":
            cname = str(srow.get("charter") or srow.get("project") or srow.get("board") or "project").strip()
            csum = str(srow.get("summary") or srow.get("purpose") or srow.get("why") or "").strip()
            cprin = str(srow.get("principles") or srow.get("non_goals") or "").strip()
            txt = f"Charter [{cname}]: {csum}" + (f" | Principles: {cprin}" if cprin else "")
            mcus.append({
                "mcu_id": next_id, "head_id": next_id, "source_chunk_ids": [next_id],
                "text": txt, "canonical_text": txt, "type": "FACT", "facet": "context_facts",
                "primary_scope": "global_defaults", "secondary_refs": [],
                "target_prop": None, "bound_op": None, "bound_val": None,
                "conditions": [], "is_exception": False, "superseded_literals": [],
                "meta_keys": [], "meta": {}, "unclear_spans": [], "grounding_status": "GROUNDED",
                "lattice_kind": "CHARTER",
                "lattice_payload": {"name": cname, "summary": csum, "principles": cprin},
            })
            next_id += 1
        elif t_kind == "COMPONENT":
            deps = _split_list_cell(srow.get("depends_on") or srow.get("dependencies") or srow.get("deps"))
            files = _split_list_cell(srow.get("files") or srow.get("file"))
            role = str(srow.get("role") or srow.get("description") or srow.get("purpose") or f"Component {scope}").strip()
            why = str(srow.get("why") or srow.get("rationale") or "").strip() or None
            mcus.append({
                "mcu_id": next_id, "head_id": next_id, "source_chunk_ids": [next_id],
                "text": f"{scope}: {role}", "canonical_text": f"{scope}: {role}", "type": "FACT", "facet": "context_facts",
                "primary_scope": scope, "secondary_refs": list(deps),
                "target_prop": None, "bound_op": None, "bound_val": None,
                "conditions": [], "is_exception": False, "superseded_literals": [],
                "meta_keys": [], "meta": {}, "unclear_spans": [], "grounding_status": "GROUNDED",
                "lattice_kind": "COMPONENT",
                "lattice_payload": {"scope": scope, "role": role, "why": why, "depends_on": deps, "files": files, "owner": owner, "stage": stage},
            })
            next_id += 1
        elif t_kind in ("INTERFACE", "LAW", "SCHEMA"):
            name = str(srow.get(t_kind.lower()) or srow.get("name") or srow.get("id") or scope).strip()
            spec_s = str(srow.get("spec") or srow.get("description") or srow.get("theorem") or f"{t_kind} {name} ({scope})").strip()
            ins = _split_list_cell(srow.get("inputs") or srow.get("fields") or srow.get("args"))
            outs = _split_list_cell(srow.get("outputs") or srow.get("output") or srow.get("returns"))
            mcus.append({
                "mcu_id": next_id, "head_id": next_id, "source_chunk_ids": [next_id],
                "text": spec_s, "canonical_text": spec_s, "type": "FACT", "facet": "context_facts",
                "primary_scope": scope, "secondary_refs": [],
                "target_prop": None, "bound_op": None, "bound_val": None,
                "conditions": [], "is_exception": False, "superseded_literals": [],
                "meta_keys": [], "meta": {}, "unclear_spans": [], "grounding_status": "GROUNDED",
                "lattice_kind": t_kind,
                "lattice_payload": {"id": name, "name": name, "scope": scope, "spec": spec_s, "inputs": ins, "outputs": outs, "owner": owner, "stage": stage, "file": str(srow.get("file") or "").strip() or None},
            })
            next_id += 1

    for fl in (flow_sentences or []):
        src, tgt, pay, cond, txt = fl["source"], fl["target"], fl.get("payload"), fl.get("condition"), fl["text"]
        mcus.append({
            "mcu_id": next_id, "head_id": next_id, "source_chunk_ids": [next_id],
            "text": txt, "canonical_text": txt, "type": "FACT", "facet": "context_facts",
            "primary_scope": src, "secondary_refs": [r for r in (tgt, pay) if r and r != src],
            "subject": f"{src} component", "predicate": "sends", "object": pay, "recipient": tgt, "condition": cond,
            "target_prop": None, "bound_op": None, "bound_val": None,
            "conditions": [cond] if cond else [], "is_exception": False, "superseded_literals": [],
            "meta_keys": [], "meta": {}, "unclear_spans": [], "grounding_status": "GROUNDED",
            "lattice_kind": "FLOW",
            "lattice_payload": {"source": src, "target": tgt, "payload": pay, "condition": cond, "summary": txt},
        })
        next_id += 1
    return mcus
