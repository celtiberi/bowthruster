"""Spec 22C §2.2: Whiteboard Stage 4A Entity/Scope Resolution, Alias Indexing, and Table-Attribute Fusion (`rewriter.py`)."""

from __future__ import annotations

import copy
from enum import Enum
from pathlib import Path
from typing import Any

from lattice.graph_core import SQLiteGraphStore
from lattice.nlp import restore_ascii_dots
from lattice.whiteboard.mutations import ensure_scope_node, prune_dysfluency_and_upsert_rule, upsert_component_spec, upsert_flow_edge, upsert_lattice_artifact
from lattice.whiteboard.schema import ContextGraph, DEFAULT_K_CAP, DEFAULT_MAX_RING, WhiteboardGraph, create_empty_graph, should_register_as_scope, sync_to_sqlite

class IngestionStatus(str, Enum):
    """Explicit state machine states for Stage 4C ingestion status (Spec 22C §2.2, Law 6)."""
    COMMITTED = "COMMITTED"
    CONFLICT = "CONFLICT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"

VALID_INGESTION_STATUSES = frozenset(s.value for s in IngestionStatus)
_CANONICAL_HEADERS_MSG = (
    "Unrecognized pipe-table headers. Expected canonical Lattice pipe-table headers:\n"
    "  - | Component | Role | Owner | Stage | Depends_On | Files |\n"
    "  - | Flow | Source | Target | Payload | Condition |\n"
    "  - | Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |\n"
    "  - | Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |\n"
    "  - | Schema | Component | Fields | Owner | Stage | File | Spec |\n"
    "  - | Rule | Component | Type | Property | Op | Value | Condition |"
)

def is_table_schema_valid(has_valid_table_headers: bool = True, table_accepted: bool = True) -> bool:
    return bool(has_valid_table_headers or (not table_accepted))
def table_header_schema_law(has_valid_table_headers: bool = True, table_accepted: bool = True) -> bool:
    return is_table_schema_valid(has_valid_table_headers, table_accepted)
def is_flow_or_rule_table_fused(has_flow_or_rule_table: bool = True, fused_into_graph: bool = True) -> bool:
    return bool((not has_flow_or_rule_table) or fused_into_graph)
def flow_and_rule_table_fusion_law(has_flow_or_rule_table: bool = True, fused_into_graph: bool = True) -> bool:
    return is_flow_or_rule_table_fused(has_flow_or_rule_table, fused_into_graph)

class Phase4InvariantError(ValueError):
    """Raised when a Whiteboard ingestion or ContextGraph invariant check fails (Spec 22C §2.2)."""
    def __init__(self, code: str, errors: list[str]) -> None:
        self.code, self.errors = code, list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")

class IngestionResult(dict[str, Any]):
    """Dict-compatible return object of `ingest_intake_packet` supporting both dict and attribute access (Spec 22C §2.2)."""
    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name) or name not in self:
            raise AttributeError(name)
        return self[name]


def _slugify_scope_id(raw: str) -> str:
    """Normalizes a surface entity/scope string into a canonical snake_case/path scope ID."""
    cleaned = restore_ascii_dots(str(raw)).strip().strip("\"`'").strip().rstrip("/")
    chars = [ch if (ch.isalnum() or ch in ("_", "/", ".")) else ("_" if ch in ("-", " ", ":") else " ") for ch in cleaned.lower()]
    joined = "_".join(part for part in "".join(chars).split() if part)
    while "__" in joined: joined = joined.replace("__", "_")
    return joined.strip("_/")

def _extract_canonical_and_aliases(raw_name: str) -> tuple[str, list[str]]:
    """Extracts `(canonical_slug, surface_aliases)` from a raw scope/entity string (Spec 22C §2.2)."""
    if not (clean_raw := restore_ascii_dots(str(raw_name)).strip().strip("\"`'").strip().rstrip("/")):
        return "global_defaults", ["global_defaults"]
    aliases, canonical_cand, bt1 = [clean_raw], clean_raw, clean_raw.find("`")
    if bt1 >= 0 and (bt2 := clean_raw.find("`", bt1 + 1)) > bt1 + 1 and (inner_bt := clean_raw[bt1 + 1 : bt2].strip()) and " " not in inner_bt:
        canonical_cand = inner_bt
        if inner_bt not in aliases: aliases.append(inner_bt)
    elif "(" in clean_raw and clean_raw.endswith(")") and (inner_p := clean_raw[clean_raw.rfind("(") + 1 : -1].strip().strip("\"`'*").strip()) and " " not in inner_p:
        canonical_cand = inner_p
        if inner_p not in aliases: aliases.append(inner_p)
    if (prefix := clean_raw.split("(", 1)[0].strip().strip("*").strip()) and prefix not in aliases:
        aliases.append(prefix)
    for sep in ("—", "–", " - "):
        if sep in prefix:
            for seg in prefix.split(sep):
                if (seg_clean := seg.strip().strip("*").strip()) and seg_clean not in aliases: aliases.append(seg_clean)
    return _slugify_scope_id(canonical_cand) or "global_defaults", aliases

def _parse_scalar_cell(val_str: str) -> int | float | bool | str:
    """Parses a Markdown table cell string into an `int`, `float`, `bool`, or normalized `str`."""
    s, low = restore_ascii_dots(str(val_str)).strip().strip("\"`'").strip(), restore_ascii_dots(str(val_str)).strip().strip("\"`'").strip().lower()
    if low in ("true", "false"): return low == "true"
    if s and (s.isdigit() or (s.startswith("-") and s[1:].isdigit())): return int(s)
    if s and s.count(".") == 1:
        left, right = s.split(".", 1)
        if (left[1:] if left.startswith("-") else left).isdigit() and right.isdigit(): return float(s)
    return s

def _parse_list_cell(val_str: str) -> list[str]:
    """Splits a comma- or semicolon-separated Markdown table cell into clean non-placeholder tokens."""
    if not (raw := restore_ascii_dots(str(val_str)).strip()) or raw.lower() in ("none", "-", "—", "n/a", "null"): return []
    chunks, buf, depth = [], [], 0
    for ch in raw:
        depth += 1 if ch in "([{" else (-1 if ch in ")]}" and depth > 0 else 0)
        if ch in (",", ";") and depth == 0: chunks.append("".join(buf)); buf = []
        else: buf.append(ch)
    chunks.append("".join(buf))
    items: list[str] = []
    for chunk in chunks:
        if (tok := chunk.strip().strip("\"`'*").strip()) and tok.lower() not in ("none", "-", "—", "n/a", "null") and tok not in items:
            items.append(tok)
    return items

def create_empty_context_graph(k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING, store: SQLiteGraphStore | None = None, db_path: str | Path | None = None) -> WhiteboardGraph:
    """Creates a fresh `ContextGraph` / `WhiteboardGraph` delegating to `create_empty_graph` (Spec 22C §2.2)."""
    return create_empty_graph(k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)

def _register_scope_aliases(graph: dict[str, Any], scope_id: str, aliases: list[str]) -> None:
    """Registers all surface and slugified forms of `aliases` for `scope_id` in `scope['aliases']` and `graph['alias_index']`."""
    scope, s_aliases, alias_idx = graph["scopes"][scope_id], graph["scopes"][scope_id].setdefault("aliases", []), graph.setdefault("alias_index", {})
    alias_idx[scope_id.lower()] = alias_idx[_slugify_scope_id(scope_id)] = scope_id
    for al in (str(a).strip() for a in aliases if str(a).strip()):
        if al != scope_id and al not in s_aliases: s_aliases.append(al)
        alias_idx[al.lower()] = scope_id
        if al_slug := _slugify_scope_id(al): alias_idx[al_slug] = scope_id


def _resolve_or_register_scope(graph: dict[str, Any], raw_name: str, *, create: bool = True) -> str:
    """Resolves `raw_name` via `graph['alias_index']` or `graph['scopes']`, creating the scope if `create=True`."""
    scopes, alias_idx = graph.setdefault("scopes", {}), graph.setdefault("alias_index", {})
    canonical_slug, aliases = _extract_canonical_and_aliases(raw_name)
    for cand in [canonical_slug, *aliases]:
        c_low, c_slug = cand.lower().strip(), _slugify_scope_id(cand)
        for key in (cand, alias_idx.get(c_low), c_slug, alias_idx.get(c_slug)):
            if key and key in scopes:
                _register_scope_aliases(graph, key, aliases)
                return key
    for sid in scopes:
        base_sid = sid.split("::")[0]
        if (base_sid == canonical_slug or sid.endswith(f"/{canonical_slug}") or canonical_slug.endswith(f"/{base_sid}")) and base_sid in scopes:
            _register_scope_aliases(graph, base_sid, aliases)
            return base_sid
    if create:
        ensure_scope_node(graph, canonical_slug)
        _register_scope_aliases(graph, canonical_slug, aliases)
        sync_to_sqlite(graph)
    return canonical_slug


def _is_existing_component_scope(sc: Any, sid: str) -> bool:
    """Returns True if `sc` is an existing non-default SBF component scope."""
    return isinstance(sc, dict) and bool(sid) and sid != "global_defaults" and (
        str(sc.get("kind") or "").strip().upper() == "COMPONENT" or str(sc.get("status") or "DRAFT").strip().upper() != "DRAFT"
        or any(sc.get(k) for k in ("role", "owner", "stage", "depends_on", "children", "parent", "files", "inputs", "outputs"))
    )


def _resolve_known_component_scope(
    graph: dict[str, Any], raw_primary: str, mcu_text: str, parsed_mcu: dict[str, Any] | None = None,
) -> str:
    """Resolves `raw_primary` to an existing component `c` in `graph['scopes']` when `raw_primary`
    is `'global_defaults'` (or not an existing component) and `mcu_text` mentions `c` (Spec 22C §2.2)."""
    scopes = graph.get("scopes", {}) if isinstance(graph.get("scopes"), dict) else {}
    sbf_comps = [sid for sid, sc in scopes.items() if _is_existing_component_scope(sc, sid)]
    existing_comps = sbf_comps if sbf_comps else [sid for sid, sc in scopes.items() if isinstance(sc, dict) and sid != "global_defaults"]
    cand = _resolve_or_register_scope(graph, raw_primary, create=False)
    if cand in existing_comps:
        return _resolve_or_register_scope(graph, raw_primary, create=True)
    pm = parsed_mcu or {}
    lp = pm.get("lattice_payload") if isinstance(pm.get("lattice_payload"), dict) else {}
    lkind = str(pm.get("lattice_kind") or lp.get("kind") or str(lp.get("op") or "").removeprefix("UPSERT_") or "").strip().upper()
    if lkind == "COMPONENT" and cand and cand != "global_defaults":
        return _resolve_or_register_scope(graph, raw_primary, create=True)

    if existing_comps and mcu_text:
        text_low = restore_ascii_dots(mcu_text).lower()
        text_toks = "".join(ch if (ch.isalnum() or ch == "_") else " " for ch in text_low).split()
        matches: list[tuple[int, int, str]] = []
        for c in existing_comps:
            c_low, c_base = c.lower(), c.lower().split("::")[0]
            c_aliases = [str(a).strip().lower() for a in (scopes.get(c, {}).get("aliases") or []) if str(a).strip()]
            names = [n for n in dict.fromkeys([c_low, c_base, *c_aliases]) if n and " " not in n]
            if not any(n in text_toks or any(f"{n}_{sfx}" in text_toks for sfx in ("module", "component", "system", "subsystem", "service")) or cand.lower().startswith(f"{n}_") for n in names):
                continue
            tok_pos = min((i for i, t in enumerate(text_toks) if t in names or any(t.startswith(f"{n}_") for n in names)), default=9999)
            in_phrase = (
                any(p in text_low for n in names for p in (f"the {n} module", f"the {n} component", f"the {n} system", f"the {n} service", f"in {n}", f"for {n}", f"on {n}", f"under {n}", f"within {n}"))
                or tok_pos == 0 or (tok_pos == 1 and text_toks[0] in ("the", "in", "for", "on"))
            )
            cand_pos = text_low.find(cand.lower()) if cand and cand != "global_defaults" else 99999
            c_pos = min((text_low.find(n) for n in names if n in text_low), default=99999)
            if cand == "global_defaults" or not cand or bool(sbf_comps) or in_phrase or cand.lower().startswith(f"{c_low}_") or c_pos < cand_pos:
                matches.append((0 if in_phrase else 1, tok_pos, c))
        if matches:
            matches.sort(key=lambda x: (x[0], x[1], x[2]))
            return matches[0][2]

    if lkind in ("SCHEMA", "INTERFACE", "LAW") and cand not in scopes:
        lpayload = (parsed_mcu or {}).get("lattice_payload") or {}
        if cand == _slugify_scope_id(str(lpayload.get("name") or lpayload.get("id") or "")):
            return "global_defaults"
    return _resolve_or_register_scope(graph, raw_primary, create=True)


def _get_table_cell(row: dict[str, Any], headers: list[Any], norm_headers: list[str], keys: tuple[str, ...]) -> str:
    for k in keys:
        if k in norm_headers:
            idx = norm_headers.index(k)
            if (raw_hdr := headers[idx]) in row and row[raw_hdr] is not None:
                return str(row[raw_hdr])
            if k in row and row[k] is not None:
                return str(row[k])
    return next((str(row[k]) for k in keys if k in row and row[k] is not None), "")


def fuse_table_attributes(
    graph: dict[str, Any], doc_tables: list[dict[str, Any]], turn_index: int = 1, *, source_turn: int | None = None,
) -> list[str]:
    """Stage 4A.3: Fuses Component, Flow, Contract (`interface`), Law, Schema, and Rule tables into `graph` (Spec 22C §2.2)."""
    if not isinstance(graph, dict) or not isinstance(doc_tables, list):
        raise ValueError("graph must be a dict and doc_tables must be a list")
    if source_turn is not None: turn_index = int(source_turn)
    fused_scopes, has_flow_or_rule = [], False
    for t_idx, table in enumerate(doc_tables):
        if not isinstance(table, dict): raise ValueError(f"doc_tables[{t_idx}] must be a dict")
        headers, rows = table.get("headers"), table.get("rows")
        if headers is None and rows is None and any(k in table for k in ("component", "interface", "law", "flow", "schema", "rule")):
            headers, rows = list(table.keys()), [table]
        if not isinstance(headers, list) or not headers or not isinstance(rows, list):
            raise ValueError(f"doc_tables[{t_idx}] must have non-empty 'headers' and 'rows' lists")
        norm_headers = [_slugify_scope_id(str(h)) for h in headers]
        if any(not nh for nh in norm_headers):
            raise ValueError(f"doc_tables[{t_idx}] contains empty/unmapped structural header in {headers!r}")
        has_comp_col = any(k in norm_headers for k in ("component", "scope", "scope_id", "module"))
        is_charter_t = "charter" in norm_headers or "project" in norm_headers or ("summary" in norm_headers and any(k in norm_headers for k in ("principles", "purpose", "why", "non_goals")))
        is_flow_t = "flow" in norm_headers or ("source" in norm_headers and "target" in norm_headers)
        is_rule_t = "rule" in norm_headers or "bound" in norm_headers or ("type" in norm_headers and any(k in norm_headers for k in ("property", "target_prop", "value", "bound_val")))
        if is_charter_t: valid_hdrs = any(k in norm_headers for k in ("summary", "purpose", "why", "principles", "description"))
        elif "interface" in norm_headers: valid_hdrs = has_comp_col
        elif "law" in norm_headers: valid_hdrs = has_comp_col and any(k in norm_headers for k in ("lhs", "rhs", "theorem", "equation", "formula", "invariant", "spec", "description"))
        elif "schema" in norm_headers: valid_hdrs = any(k in norm_headers for k in ("fields", "field", "component", "scope", "scope_id", "module", "spec", "description"))
        elif is_flow_t: valid_hdrs = "source" in norm_headers and "target" in norm_headers
        elif is_rule_t: valid_hdrs = any(k in norm_headers for k in ("property", "target_prop", "value", "bound_val", "condition", "rule", "bound", "spec", "description"))
        else:
            valid_hdrs = len(norm_headers) >= 2 and any(k in norm_headers for k in ("component", "scope", "scope_id", "module", "service", "division", "division_persona")) and (not ("component" in norm_headers) or any(k in norm_headers for k in ("role", "purpose", "responsibility", "why", "rationale", "owner", "division", "stage", "phase", "depends_on", "deps", "dependencies", "files", "file", "description", "tier", "media", "inputs", "outputs", "parent", "children")))
        if not (is_table_schema_valid(valid_hdrs, True) and table_header_schema_law(valid_hdrs, True) and valid_hdrs):
            raise ValueError(f"doc_tables[{t_idx}] headers {headers!r}: {_CANONICAL_HEADERS_MSG}")

        if is_charter_t:
            for row in (r for r in rows if isinstance(r, dict)):
                cname = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("charter", "project", "board", "name")) or str(row.get(headers[0], ""))).strip().strip("\"`'*").strip() or "project"
                csum = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("summary", "purpose", "description", "why"))).strip()
                cprin = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("principles", "non_goals", "rationale"))).strip()
                graph["charter"] = {"name": cname, "summary": csum, "principles": cprin}
                ensure_scope_node(graph, "global_defaults")["attributes"]["charter"] = dict(graph["charter"])
                if "global_defaults" not in fused_scopes: fused_scopes.append("global_defaults")
            continue

        if is_flow_t:
            has_flow_or_rule = True
            for row in (r for r in rows if isinstance(r, dict)):
                raw_src = _get_table_cell(row, headers, norm_headers, ("source", "from", "sender", "producer"))
                raw_tgt = _get_table_cell(row, headers, norm_headers, ("target", "to", "recipient", "consumer"))
                if not raw_src.strip() or not raw_tgt.strip(): continue
                src, tgt = _resolve_or_register_scope(graph, raw_src, create=True), _resolve_or_register_scope(graph, raw_tgt, create=True)
                for sid in (src, tgt):
                    if sid not in fused_scopes: fused_scopes.append(sid)
                raw_fnm = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("flow", "id", "name"))).strip().strip("\"`'*").strip()
                raw_pay = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("payload", "data", "artifact", "message", "flow"))).strip().strip("\"`'*").strip()
                pay = None if raw_pay.lower() in ("", "none", "-", "—", "n/a", "null") else raw_pay
                raw_cond = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("condition", "when", "guard"))).strip().strip("\"`'*").strip()
                cond = None if raw_cond.lower() in ("", "none", "-", "—", "n/a", "null") else raw_cond
                upsert_flow_edge(graph, src, tgt, payload=pay, condition=cond, text=f"{src} -> {tgt}" + (f" ({pay})" if pay else ""), dimensions={"flow_name": raw_fnm} if raw_fnm and raw_fnm != raw_pay else None, current_turn=int(turn_index))
            continue

        if is_rule_t and "component" not in norm_headers[:1]:
            has_flow_or_rule = True
            for row in (r for r in rows if isinstance(r, dict)):
                raw_comp = _get_table_cell(row, headers, norm_headers, ("component", "scope", "scope_id", "module"))
                comp_sid = _resolve_or_register_scope(graph, raw_comp, create=True) if raw_comp.strip() else "global_defaults"
                if comp_sid not in fused_scopes: fused_scopes.append(comp_sid)
                raw_type = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("type", "kind", "facet"))).strip().upper() or "BOUND"
                rtype = raw_type if raw_type in ("BOUND", "PROHIBITION", "OBLIGATION", "FACT", "POLICY", "GOAL", "PROPOSAL", "PERMISSION") else "BOUND"
                raw_prop = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("property", "target_prop", "field", "metric", "rule", "bound"))).strip().strip("\"`'*").strip()
                t_prop = _slugify_scope_id(raw_prop) if raw_prop and raw_prop.lower() not in ("none", "-", "—", "n/a", "null") else None
                raw_op = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("op", "bound_op", "operator"))).strip().strip("\"`'*").strip()
                b_op = raw_op if raw_op and raw_op.lower() not in ("none", "-", "—", "n/a", "null") else ("==" if t_prop else None)
                raw_val = _get_table_cell(row, headers, norm_headers, ("value", "bound_val", "limit", "target"))
                b_val = None if restore_ascii_dots(raw_val).strip().lower() in ("", "none", "-", "—", "n/a", "null") else _parse_scalar_cell(raw_val)
                if not t_prop and b_val is None: continue
                conds = _parse_list_cell(_get_table_cell(row, headers, norm_headers, ("condition", "conditions", "when")))
                r_text = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("spec", "description", "text", "summary"))).strip() or f"{comp_sid}.{t_prop or 'rule'} {b_op or '=='} {b_val}"
                prune_dysfluency_and_upsert_rule(graph, comp_sid, {"type": rtype, "text": r_text, "target_prop": t_prop, "bound_op": b_op, "bound_val": b_val, "conditions": conds, "turn": int(turn_index)})
            continue

        if "schema" in norm_headers or "interface" in norm_headers:
            art_kind = "SCHEMA" if "schema" in norm_headers else "INTERFACE"
            name_keys = ("schema", "name", "id") if art_kind == "SCHEMA" else ("interface", "name", "id")
            in_keys = ("fields", "field", "inputs", "input", "args", "params") if art_kind == "SCHEMA" else ("inputs", "input", "args", "params")
            for row in (r for r in rows if isinstance(r, dict)):
                raw_art = _get_table_cell(row, headers, norm_headers, name_keys) or str(row.get(headers[0], ""))
                if not (art_name := restore_ascii_dots(raw_art).strip().strip("\"`'*").strip()): continue
                raw_comp = _get_table_cell(row, headers, norm_headers, ("component", "scope", "scope_id", "module"))
                comp_sid = _resolve_or_register_scope(graph, raw_comp, create=True) if raw_comp.strip() else "global_defaults"
                if comp_sid not in fused_scopes: fused_scopes.append(comp_sid)
                raw_file = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("file", "files", "path"))).strip().strip("\"`'*").strip()
                clean_file = "" if raw_file.lower() in ("none", "-", "—", "n/a", "null") else raw_file
                inputs_list = _parse_list_cell(_get_table_cell(row, headers, norm_headers, in_keys))
                outputs_list = [] if art_kind == "SCHEMA" else _parse_list_cell(_get_table_cell(row, headers, norm_headers, ("output", "outputs", "return", "returns")))
                raw_owner = _get_table_cell(row, headers, norm_headers, ("owner", "division"))
                owner_val = (_extract_canonical_and_aliases(raw_owner)[0] if ("`" in raw_owner or "(" in raw_owner) else restore_ascii_dots(raw_owner).strip().strip("\"`'*").strip()) if raw_owner.strip() else None
                raw_stage = _get_table_cell(row, headers, norm_headers, ("stage", "phase"))
                stage_val = restore_ascii_dots(raw_stage).strip().strip("\"`'*").strip() if raw_stage.strip() else None
                raw_spec = _get_table_cell(row, headers, norm_headers, ("spec", "description", "role", "summary"))
                spec_str = restore_ascii_dots(raw_spec).strip() if raw_spec.strip() else (f"{art_name}({', '.join(inputs_list)})" if art_kind == "SCHEMA" else f"{art_name}({', '.join(inputs_list)}) -> {', '.join(outputs_list)}")
                art_entry = upsert_lattice_artifact(
                    graph, art_kind, art_name, scope_id=comp_sid, spec=spec_str, inputs=inputs_list, outputs=outputs_list,
                    owner=owner_val, stage=stage_val, dimensions={"_from_table": True, **({"file": clean_file} if clean_file else {})},
                    text=spec_str, current_turn=int(turn_index),
                )
                if clean_file: art_entry["file"] = clean_file
            continue

        if "law" in norm_headers:
            for row in (r for r in rows if isinstance(r, dict)):
                raw_law = _get_table_cell(row, headers, norm_headers, ("law", "name", "id", "theorem")) or str(row.get(headers[0], ""))
                if not (law_name := restore_ascii_dots(raw_law).strip().strip("\"`'*").strip()): continue
                raw_comp = _get_table_cell(row, headers, norm_headers, ("component", "scope", "scope_id", "module"))
                comp_sid = _resolve_or_register_scope(graph, raw_comp, create=True) if raw_comp.strip() else "global_defaults"
                if comp_sid not in fused_scopes: fused_scopes.append(comp_sid)
                law_fields = {
                    lkey: rv for lkey in ("bend_dir", "args", "lhs", "rhs")
                    if (rv := restore_ascii_dots(_get_table_cell(row, headers, norm_headers, (lkey,))).strip().strip("\"`'").strip())
                    and rv.lower() not in ("none", "-", "—", "n/a", "null")
                }
                if ("lhs" not in law_fields or "rhs" not in law_fields) and "==" in (raw_thm := restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("theorem", "equation", "formula", "invariant"))).strip().strip("\"`'").strip()):
                    lhs_p, rhs_p = raw_thm.split("==", 1)
                    law_fields.setdefault("lhs", (lhs_p.split(":", 1)[1] if ("->" in lhs_p and ":" in lhs_p) else lhs_p).strip().strip("\"`'").strip())
                    law_fields.setdefault("rhs", rhs_p.strip().strip("\"`'").strip())
                raw_owner = _get_table_cell(row, headers, norm_headers, ("owner", "division"))
                owner_val = (_extract_canonical_and_aliases(raw_owner)[0] if ("`" in raw_owner or "(" in raw_owner) else restore_ascii_dots(raw_owner).strip().strip("\"`'*").strip()) if raw_owner.strip() else None
                raw_stage = _get_table_cell(row, headers, norm_headers, ("stage", "phase"))
                stage_val = restore_ascii_dots(raw_stage).strip().strip("\"`'*").strip() if raw_stage.strip() else None
                raw_spec = _get_table_cell(row, headers, norm_headers, ("spec", "description", "summary"))
                spec_str = restore_ascii_dots(raw_spec).strip() if raw_spec.strip() else (f"{law_fields['lhs']} == {law_fields['rhs']}" if ("lhs" in law_fields and "rhs" in law_fields) else f"LAW {law_name}")
                art_entry = upsert_lattice_artifact(
                    graph, "LAW", law_name, scope_id=comp_sid, spec=spec_str, owner=owner_val,
                    stage=stage_val, dimensions={"_from_table": True, **law_fields}, text=spec_str, current_turn=int(turn_index),
                )
                for lkey, lval in law_fields.items(): art_entry[lkey] = lval
            continue

        primary_col_idx = next((norm_headers.index(k) for k in ("component", "scope", "scope_id", "module") if k in norm_headers), 0)
        primary_hdr, heading_path = str(headers[primary_col_idx]), table.get("heading_path")
        non_alias_cols = frozenset({"role", "purpose", "responsibility", "why", "rationale", "owner", "division", "stage", "phase", "parent", "depends_on", "deps", "dependencies", "children", "inputs", "input", "outputs", "output", "files", "file"})
        for row in (r for r in rows if isinstance(r, dict)):
            if not (raw_entity := str(row.get(primary_hdr, row.get(norm_headers[primary_col_idx], ""))).strip()): continue
            scope_id = _resolve_or_register_scope(graph, raw_entity, create=True)
            scope = graph["scopes"][scope_id]
            if scope_id not in fused_scopes: fused_scopes.append(scope_id)
            attrs = scope.setdefault("attributes", {})
            if isinstance(heading_path, str) and heading_path.strip(): attrs.setdefault("heading_path", heading_path.strip())
            attrs["updated_turn"] = int(turn_index)
            sbf_kwargs, files_list = {}, None
            for col_idx, raw_hdr in enumerate(headers):
                if col_idx == primary_col_idx or (raw_hdr not in row and norm_headers[col_idx] not in row): continue
                col_key, raw_cell_str = norm_headers[col_idx], str(row.get(raw_hdr, row.get(norm_headers[col_idx], "")))
                cell_val = _parse_scalar_cell(raw_cell_str)
                attrs[col_key] = cell_val
                if col_key not in non_alias_cols and isinstance(cell_val, str) and cell_val and ("`" in raw_cell_str or "/" in cell_val):
                    _register_scope_aliases(graph, scope_id, _extract_canonical_and_aliases(cell_val)[1])
                clean_str = str(cell_val).strip()
                is_empty_cell = clean_str.lower() in ("", "none", "-", "—", "n/a", "null")
                if col_key in ("role", "purpose", "responsibility") and not is_empty_cell:
                    sbf_kwargs["role"] = attrs["_table_role"] = clean_str
                elif col_key in ("why", "rationale") and not is_empty_cell:
                    scope["why"] = attrs["why"] = clean_str
                elif col_key in ("owner", "division") and not is_empty_cell:
                    sbf_kwargs["owner"] = _extract_canonical_and_aliases(raw_cell_str)[0] if ("`" in raw_cell_str or "(" in raw_cell_str) else clean_str
                elif col_key in ("stage", "phase") and not is_empty_cell: sbf_kwargs["stage"] = clean_str
                elif col_key == "parent" and not is_empty_cell: sbf_kwargs["parent"] = _slugify_scope_id(clean_str) or clean_str
                elif col_key in ("depends_on", "deps", "dependencies"):
                    dep_sids = [_resolve_or_register_scope(graph, d, create=True) for d in _parse_list_cell(raw_cell_str) if d]
                    sbf_kwargs["depends_on"] = [d for d in dep_sids if d != scope_id]
                    attrs["_table_depends_on"] = list(sbf_kwargs["depends_on"])
                elif col_key == "children": sbf_kwargs["children"] = [_resolve_or_register_scope(graph, c, create=True) for c in _parse_list_cell(raw_cell_str) if c]
                elif col_key in ("inputs", "input"): sbf_kwargs["inputs"] = _parse_list_cell(raw_cell_str)
                elif col_key in ("outputs", "output"): sbf_kwargs["outputs"] = _parse_list_cell(raw_cell_str)
                elif col_key in ("files", "file"):
                    files_list = _parse_list_cell(raw_cell_str)
                    scope["files"] = attrs["files"] = files_list
                    attrs["_table_files"] = list(files_list)
            if sbf_kwargs or files_list is not None:
                upsert_component_spec(
                    graph, scope_id, role=sbf_kwargs.get("role"), inputs=sbf_kwargs.get("inputs"),
                    outputs=sbf_kwargs.get("outputs"), depends_on=sbf_kwargs.get("depends_on"),
                    children=sbf_kwargs.get("children"), parent=sbf_kwargs.get("parent"),
                    owner=sbf_kwargs.get("owner"), stage=sbf_kwargs.get("stage"),
                    kind="COMPONENT", current_turn=int(turn_index),
                )
                if files_list is not None: scope["files"] = files_list

    if not (is_flow_or_rule_table_fused(has_flow_or_rule, True) and flow_and_rule_table_fusion_law(has_flow_or_rule, True)):
        raise ValueError("flow_and_rule_table_fusion_law violated")
    sync_to_sqlite(graph)
    return fused_scopes


def _filter_non_dep_tokens(
    graph: dict[str, Any], scope_id: str, raw_deps: list[Any],
    mcu_text: str, lpayload: dict[str, Any], parsed_mcu: dict[str, Any],
) -> tuple[list[str], set[str]]:
    """Filters `raw_deps` to exclude `role`, `files`, `.py`/`.bend` filenames, and prose role tails."""
    sc = graph.get("scopes", {}).get(scope_id, {}) if isinstance(graph.get("scopes"), dict) else {}
    attrs = sc.get("attributes", {}) if isinstance(sc, dict) and isinstance(sc.get("attributes"), dict) else {}
    non_dep_low: set[str] = {"files", "file", "role", "purpose", "responsibility", "laws.bend", "proof.bend", "main.bend"}
    for rv in (sc.get("role"), attrs.get("role"), attrs.get("_table_role"), lpayload.get("role"), parsed_mcu.get("role")):
        if isinstance(rv, str) and rv.strip() and " " not in rv.strip():
            non_dep_low.update((rv.strip().lower(), _slugify_scope_id(rv)))
    for flist in (sc.get("files"), attrs.get("files"), attrs.get("_table_files"), lpayload.get("files"), parsed_mcu.get("files")):
        for fitem in (_parse_list_cell(flist) if isinstance(flist, str) else (flist if isinstance(flist, list) else [])):
            if fs := str(fitem).strip().lower():
                non_dep_low.update((fs, _slugify_scope_id(fs)))
    text_low = restore_ascii_dots(mcu_text or "").lower()
    role_tail_pos = min((text_low.find(m) for m in ("serves as a ", "serves as ", "has the role ", "with role ", "with files ", "and files ") if m in text_low), default=-1)
    if "_table_depends_on" in attrs and int(attrs.get("updated_turn", 0)) == int(graph.get("turn_index", 0)):
        return list(attrs["_table_depends_on"]), non_dep_low
    clean_deps: list[str] = []
    for d in raw_deps:
        if not (ds := str(d).strip().strip("\"`'*").strip()): continue
        d_low, d_slug = ds.lower(), _slugify_scope_id(ds)
        if d_low in non_dep_low or d_slug in non_dep_low or "." in ds or any(d_low.endswith(ext) for ext in ("_py", "_bend", "_mjs", "_json")) or (role_tail_pos >= 0 and text_low.find(d_low) > role_tail_pos and text_low.find(d_low, 0, role_tail_pos) < 0):
            non_dep_low.update((d_low, d_slug)); continue
        clean_deps.append(ds)
    return clean_deps, non_dep_low


def resolve_mcu_scopes_and_refs(graph: dict[str, Any], parsed_mcu: dict[str, Any]) -> tuple[str, list[str]]:
    """Stage 4A.4: Resolves `parsed_mcu['primary_scope']` and `secondary_refs` to canonical `scope_id`s,
    enforcing Spec 22A Law 5 (`should_register_as_scope`) to prevent ghost scopes (Spec 22C §2.2)."""
    if not isinstance(graph, dict) or not isinstance(parsed_mcu, dict):
        raise ValueError("graph and parsed_mcu must be dicts")

    raw_primary = str(parsed_mcu.get("primary_scope") or parsed_mcu.get("scope") or parsed_mcu.get("scope_id") or "global_defaults")
    mcu_text = restore_ascii_dots(str(parsed_mcu.get("text") or parsed_mcu.get("canonical_text") or parsed_mcu.get("raw_text") or "")).strip()
    primary_scope = _resolve_known_component_scope(graph, raw_primary, mcu_text, parsed_mcu)
    target_prop = str(parsed_mcu.get("target_prop") or "").strip()
    if primary_scope != raw_primary and raw_primary != "global_defaults" and "_" in raw_primary and (not target_prop or target_prop in ("value", primary_scope) or target_prop.startswith(f"{primary_scope}_")):
        parsed_mcu["target_prop"] = target_prop = raw_primary
    parsed_mcu["primary_scope"] = primary_scope
    bound_val_str = str(parsed_mcu.get("bound_val") if parsed_mcu.get("bound_val") is not None else "").strip()
    lpayload = parsed_mcu.get("lattice_payload") if isinstance(parsed_mcu.get("lattice_payload"), dict) else {}
    flow_payload_str = str(lpayload.get("payload") or "").strip()
    scalar_excluded = {s.lower() for s in (target_prop, bound_val_str, flow_payload_str) if s}

    raw_deps_all = list(parsed_mcu.get("depends_on") or []) + list(lpayload.get("depends_on") or []) + list(lpayload.get("children") or [])
    clean_deps, non_dep_low = _filter_non_dep_tokens(graph, primary_scope, raw_deps_all, mcu_text, lpayload, parsed_mcu)
    artifact_and_io_tokens = [
        target_prop, bound_val_str, flow_payload_str, str(lpayload.get("id") or "").strip(),
        str(lpayload.get("name") or "").strip(), str(parsed_mcu.get("name") or "").strip(),
        str(lpayload.get("owner") or "").strip(), str(parsed_mcu.get("owner") or "").strip(),
        str(lpayload.get("stage") or "").strip(), str(parsed_mcu.get("stage") or "").strip(),
        "laws.bend", "proof.bend", *[str(x).strip() for x in (lpayload.get("ids") or [])],
        *[str(x).strip() for x in (lpayload.get("inputs") or [])], *[str(x).strip() for x in (lpayload.get("outputs") or [])],
        *[str(x).strip() for x in (parsed_mcu.get("inputs") or [])], *[str(x).strip() for x in (parsed_mcu.get("outputs") or [])],
    ]
    excluded_vals = {"eng0", "eng1", "eng2", "eng3", "bethe", "bobo", "mike", "raymand", "sarah", "groves"} | non_dep_low
    for tok in artifact_and_io_tokens:
        if tok:
            excluded_vals.add(tok.lower())
            if slug_tok := _slugify_scope_id(tok):
                excluded_vals.add(slug_tok)

    flow_src, flow_tgt = str(lpayload.get("source") or "").strip(), str(lpayload.get("target") or "").strip()
    flow_endpoints_low = {s.lower() for s in (flow_src, flow_tgt, _slugify_scope_id(flow_src), _slugify_scope_id(flow_tgt)) if s}
    deps_low = {str(d).strip().lower() for d in clean_deps if str(d).strip()} | {_slugify_scope_id(str(d)) for d in clean_deps if str(d).strip()}
    resolved_refs, scopes = [], graph.get("scopes", {})

    for raw_ref in parsed_mcu.get("secondary_refs", []):
        ref_str = str(raw_ref).strip().strip("\"`'").strip()
        if not ref_str or ref_str.lower() in scalar_excluded:
            continue
        existing = _resolve_or_register_scope(graph, ref_str, create=False)
        if existing and existing in scopes:
            ref_scope_id = existing
        else:
            ref_low, ref_slug = ref_str.lower(), _slugify_scope_id(ref_str)
            is_flow_endpoint = ref_low in flow_endpoints_low or ref_slug in flow_endpoints_low
            is_explicit_component = ref_low in deps_low or ref_slug in deps_low
            is_bound_or_payload_val = (
                parsed_mcu.get("type") == "ACTION" or ref_low in excluded_vals or ref_slug in excluded_vals
                or (parsed_mcu.get("type") in ("PROHIBITION", "OBLIGATION", "BOUND", "FACT") and not target_prop and not bound_val_str and not is_flow_endpoint)
            )
            is_hex_digest = len(ref_str) >= 6 and any(c.isdigit() for c in ref_str) and all(c in "0123456789abcdef" for c in ref_low)
            if should_register_as_scope(False, is_flow_endpoint, is_explicit_component, is_bound_or_payload_val) and not is_bound_or_payload_val and len(ref_str) >= 3 and not any(ch in ref_str for ch in (".", "*", "<", ">", ":", " ", "-")) and not is_hex_digest:
                ref_scope_id = _resolve_or_register_scope(graph, ref_str, create=True)
            else:
                continue
        if ref_scope_id and ref_scope_id in scopes and ref_scope_id != primary_scope and ref_scope_id not in resolved_refs:
            resolved_refs.append(ref_scope_id)
    return primary_scope, resolved_refs
