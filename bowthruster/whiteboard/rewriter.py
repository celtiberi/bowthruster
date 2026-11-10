"""Spec 22C §2.2: Whiteboard Stage 4A Entity/Scope Resolution, Alias Indexing, and Table-Attribute Fusion."""

from __future__ import annotations

import copy
from enum import Enum
from pathlib import Path
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore
from bowthruster.nlp import restore_ascii_dots
from bowthruster.regiment.markdown_tables import _decompose_rule_expr
from bowthruster.whiteboard.mutations import (ensure_scope_node, prune_dysfluency_and_upsert_rule,
    upsert_component_spec, upsert_flow_edge, upsert_lattice_artifact)
from bowthruster.whiteboard.schema import (DEFAULT_K_CAP, DEFAULT_MAX_RING, WhiteboardGraph,
    create_empty_graph, should_register_as_scope, sync_to_sqlite)

class IngestionStatus(str, Enum):
    """Explicit state machine states for Stage 4C ingestion status (Spec 22C §2.2, Law 6)."""
    COMMITTED = "COMMITTED"
    CONFLICT = "CONFLICT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"

VALID_INGESTION_STATUSES = frozenset(s.value for s in IngestionStatus)
_CANONICAL_HEADERS_MSG = ("Unrecognized pipe-table headers. Expected canonical Lattice pipe-table headers:\n"
    "  - | Component | Role | Owner | Stage | Depends_On | Files |\n"
    "  - | Flow | Source | Target | Payload | Condition |\n"
    "  - | Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |\n"
    "  - | Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |\n"
    "  - | Schema | Component | Fields | Owner | Stage | File | Spec |\n"
    "  - | Rule | Component | Type | Property | Op | Value | Condition |")
_EMPTY_TOKENS = frozenset({"", "none", "-", "—", "n/a", "null"})

class Phase4InvariantError(ValueError):
    """Raised when a Whiteboard ingestion or ContextGraph invariant check fails (Spec 22C §2.2)."""
    def __init__(self, code: str, errors: list[str]) -> None:
        self.code, self.errors = code, list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")

class IngestionResult(dict[str, Any]):
    """Dict-compatible return object of `ingest_intake_packet` supporting both dict and attribute access."""
    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name) or name not in self:
            raise AttributeError(name)
        return self[name]

from .table_helpers import (
    _CANONICAL_HEADERS_MSG,
    _EMPTY_TOKENS,
    _clean_cell_text,
    _extract_canonical_and_aliases,
    _get_table_cell,
    _is_existing_component_scope,
    _parse_list_cell,
    _parse_scalar_cell,
    _register_scope_aliases,
    _resolve_known_component_scope,
    _resolve_or_register_scope,
    _validate_table_headers,
)


def _slugify_scope_id(raw: str) -> str:
    """Normalizes a surface entity/scope string into a canonical snake_case/path scope ID."""
    cleaned = restore_ascii_dots(str(raw)).strip().strip("\"`'").strip().rstrip("/")
    chars = [
        ch if (ch.isalnum() or ch in ("_", "/", ".")) else ("_" if ch in ("-", " ", ":") else " ")
        for ch in cleaned.lower()
    ]
    joined = "_".join(part for part in "".join(chars).split() if part)
    while "__" in joined:
        joined = joined.replace("__", "_")
    return joined.strip("_/")



def create_empty_context_graph(
    k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING,
    store: SQLiteGraphStore | None = None,
    db_path: str | Path | None = None,
) -> WhiteboardGraph:
    """Creates a fresh `ContextGraph` / `WhiteboardGraph` delegating to `create_empty_graph`."""
    return create_empty_graph(k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)

def _fuse_flow_or_rule_rows(graph: dict[str, Any], rows: list[Any], headers: list[Any], norm_headers: list[str],
    is_flow_t: bool, turn_index: int, fused_scopes: list[str]) -> None:
    if is_flow_t:
        for row in (r for r in rows):
            raw_src = _get_table_cell(row, headers, norm_headers, ("source", "from", "sender", "producer"))
            raw_tgt = _get_table_cell(row, headers, norm_headers, ("target", "to", "recipient", "consumer"))
            if not raw_src.strip() or not raw_tgt.strip():
                continue
            src = _resolve_or_register_scope(graph, raw_src, create=True)
            tgt = _resolve_or_register_scope(graph, raw_tgt, create=True)
            for sid in (src, tgt):
                if sid not in fused_scopes:
                    fused_scopes.append(sid)
            raw_fnm = _clean_cell_text(row, headers, norm_headers, ("flow", "id", "name"))
            raw_pay = _clean_cell_text(
                row, headers, norm_headers, ("payload", "contract", "schema", "data", "artifact", "message"))
            pay = None if raw_pay.lower() in _EMPTY_TOKENS else raw_pay
            raw_cond = _clean_cell_text(row, headers, norm_headers, ("condition", "when", "guard"))
            cond = None if raw_cond.lower() in _EMPTY_TOKENS else raw_cond
            upsert_flow_edge(graph, src, tgt, payload=pay, condition=cond, current_turn=int(turn_index),
                text=f"{src} -> {tgt}" + (f" ({pay})" if pay else ""),
                dimensions={"flow_name": raw_fnm} if raw_fnm and raw_fnm != raw_pay else None)
        return
    valid_rtypes = ("BOUND", "PROHIBITION", "OBLIGATION", "FACT", "POLICY", "GOAL", "PROPOSAL", "PERMISSION")
    for row in (r for r in rows):
        raw_comp = _get_table_cell(row, headers, norm_headers, ("component", "scope", "scope_id", "module"))
        comp_sid = _resolve_or_register_scope(graph, raw_comp, create=True) if raw_comp.strip() else "global_defaults"
        if comp_sid not in fused_scopes:
            fused_scopes.append(comp_sid)
        raw_type = _clean_cell_text(row, headers, norm_headers, ("type", "kind", "facet")).upper() or "BOUND"
        rtype = raw_type if raw_type in valid_rtypes else "BOUND"
        raw_prop = _clean_cell_text(
            row, headers, norm_headers, ("property", "target_prop", "field", "metric", "rule", "bound"))
        t_prop = _slugify_scope_id(raw_prop) if raw_prop and raw_prop.lower() not in _EMPTY_TOKENS else None
        raw_op = _clean_cell_text(row, headers, norm_headers, ("op", "bound_op", "operator"))
        b_op = raw_op if raw_op and raw_op.lower() not in _EMPTY_TOKENS else ("==" if t_prop else None)
        raw_val = _get_table_cell(row, headers, norm_headers, ("value", "bound_val", "limit", "target"))
        b_val = None if restore_ascii_dots(raw_val).strip().lower() in _EMPTY_TOKENS else _parse_scalar_cell(raw_val)
        conds = _parse_list_cell(_get_table_cell(row, headers, norm_headers, ("condition", "conditions", "when")))
        if b_val is None or any(sym in raw_prop for sym in ("<=", ">=", "==", "!=", "<", ">", "import ")):
            expr_cell = _clean_cell_text(row, headers, norm_headers, ("rule_spec", "bound", "rule", "spec")) or raw_prop
            ep, eo, ev, econds = _decompose_rule_expr(expr_cell, rtype)
            t_prop = _slugify_scope_id(ep) if ep else t_prop
            b_op = eo or b_op
            b_val = ev if ev is not None else b_val
            conds = [*conds, *[c for c in econds if c not in conds]]
        if not t_prop and b_val is None:
            continue
        r_text = (_clean_cell_text(row, headers, norm_headers, ("spec", "description", "text", "summary"))
            or f"{comp_sid}.{t_prop or 'rule'} {b_op or '=='} {b_val}")
        prune_dysfluency_and_upsert_rule(
            graph, comp_sid, {"type": rtype, "text": r_text, "target_prop": t_prop, "bound_op": b_op,
            "bound_val": b_val, "conditions": conds, "turn": int(turn_index)})

def _extract_owner_and_stage(row: dict[str, Any], headers: list[Any], norm_headers: list[str],
    ) -> tuple[str | None, str | None]:
    raw_owner = _get_table_cell(row, headers, norm_headers, ("owner", "division"))
    owner_val = ((_extract_canonical_and_aliases(raw_owner)[0] if ("`" in raw_owner or "(" in raw_owner)
        else restore_ascii_dots(raw_owner).strip().strip("\"`'*").strip()) if raw_owner.strip() else None)
    raw_stage = _clean_cell_text(row, headers, norm_headers, ("stage", "phase"))
    return owner_val, (raw_stage or None)

def _fuse_artifact_or_law_rows(graph: dict[str, Any], rows: list[Any], headers: list[Any], norm_headers: list[str],
    turn_index: int, fused_scopes: list[str]) -> None:
    if "schema" in norm_headers or "interface" in norm_headers:
        art_kind = "SCHEMA" if "schema" in norm_headers else "INTERFACE"
        name_keys = ("schema", "name", "id") if art_kind == "SCHEMA" else ("interface", "name", "id")
        in_keys = (("fields", "field", "inputs", "input", "args", "params") if art_kind == "SCHEMA"
            else ("inputs", "input", "args", "params"))
        for row in (r for r in rows):
            raw_art = _get_table_cell(row, headers, norm_headers, name_keys) or str(row.get(headers[0], ""))
            if not (art_name := restore_ascii_dots(raw_art).strip().strip("\"`'*").strip()):
                continue
            raw_comp = _get_table_cell(row, headers, norm_headers, ("component", "scope", "scope_id", "module"))
            comp_sid = (
                _resolve_or_register_scope(graph, raw_comp, create=True) if raw_comp.strip() else "global_defaults")
            if comp_sid not in fused_scopes:
                fused_scopes.append(comp_sid)
            raw_file = _clean_cell_text(row, headers, norm_headers, ("file", "files", "path"))
            clean_file = "" if raw_file.lower() in _EMPTY_TOKENS else raw_file
            inputs_list = _parse_list_cell(_get_table_cell(row, headers, norm_headers, in_keys))
            out_cell = _get_table_cell(row, headers, norm_headers, ("output", "outputs", "return", "returns"))
            outputs_list = [] if art_kind == "SCHEMA" else _parse_list_cell(out_cell)
            owner_val, stage_val = _extract_owner_and_stage(row, headers, norm_headers)
            raw_spec = restore_ascii_dots(
                _get_table_cell(row, headers, norm_headers, ("spec", "description", "role", "summary")) ).strip()
            spec_str = raw_spec or (f"{art_name}({', '.join(inputs_list)})" if art_kind == "SCHEMA"
                else f"{art_name}({', '.join(inputs_list)}) -> {', '.join(outputs_list)}")
            dims: dict[str, Any] = {"_from_table": True, "spec_missing": not bool(raw_spec)}
            if clean_file:
                dims["file"] = clean_file
            if inputs_list:
                dims["fields" if art_kind == "SCHEMA" else "inputs"] = list(inputs_list)
            if outputs_list:
                dims["outputs"] = list(outputs_list)
            art_entry = upsert_lattice_artifact(
                graph, art_kind, art_name, scope_id=comp_sid, spec=spec_str,
                inputs=inputs_list or None, outputs=outputs_list or None,
                owner=owner_val, stage=stage_val, dimensions=dims, text=spec_str, current_turn=int(turn_index))
            if clean_file:
                art_entry["file"] = clean_file
        return
    for row in (r for r in rows):
        raw_law = _get_table_cell(row, headers, norm_headers, ("law", "name", "id", "theorem")) or str(
            row.get(headers[0], ""))
        if not (law_name := restore_ascii_dots(raw_law).strip().strip("\"`'*").strip()):
            continue
        raw_comp = _get_table_cell(row, headers, norm_headers, ("component", "scope", "scope_id", "module"))
        comp_sid = _resolve_or_register_scope(graph, raw_comp, create=True) if raw_comp.strip() else "global_defaults"
        if comp_sid not in fused_scopes:
            fused_scopes.append(comp_sid)
        law_fields = {
            lkey: rv for lkey in ("bend_dir", "args", "lhs", "rhs")
            if (rv := _clean_cell_text(row, headers, norm_headers, (lkey,))) and rv.lower() not in _EMPTY_TOKENS
        }
        raw_thm = _clean_cell_text(row, headers, norm_headers, ("theorem", "equation", "formula", "invariant"))
        if ("lhs" not in law_fields or "rhs" not in law_fields) and "==" in raw_thm:
            lhs_p, rhs_p = raw_thm.split("==", 1)
            lhs_clean = (lhs_p.split(":", 1)[1] if ("->" in lhs_p and ":" in lhs_p) else lhs_p).strip().strip("\"`'")
            law_fields.setdefault("lhs", lhs_clean.strip())
            law_fields.setdefault("rhs", rhs_p.strip().strip("\"`'").strip())
        owner_val, stage_val = _extract_owner_and_stage(row, headers, norm_headers)
        raw_spec = restore_ascii_dots(_get_table_cell(row, headers, norm_headers, ("spec", "description", "summary")))
        spec_str = raw_spec.strip() or (f"{law_fields['lhs']} == {law_fields['rhs']}"
            if ("lhs" in law_fields and "rhs" in law_fields) else f"LAW {law_name}")
        art_entry = upsert_lattice_artifact(graph, "LAW", law_name, scope_id=comp_sid, spec=spec_str, owner=owner_val,
            stage=stage_val, dimensions={"_from_table": True, **law_fields},
            text=spec_str, current_turn=int(turn_index))
        for lkey, lval in law_fields.items():
            art_entry[lkey] = lval

def _reconcile_bootstrapped_file_claims(graph: dict[str, Any], scope_id: str, claimed_files: list[str]) -> None:
    if not graph.get("bootstrapped") or not claimed_files:
        return
    claimed_set = set(claimed_files)
    scopes = graph.get("scopes") or {}
    artifacts = graph.get("lattice_artifacts") or []
    for other_sid, other_sc in list(scopes.items()):
        if other_sid in (scope_id, "global_defaults"):
            continue
        other_files = list(other_sc.get("files") or [])
        overlap = [f for f in other_files if f in claimed_set]
        if not overlap:
            continue
        for art in artifacts:
            if (art.get("scope") or art.get("scope_id")) == other_sid:
                af = str(art.get("file") or (art.get("dimensions") or {}).get("file") or "").split("#", 1)[0]
                if af in claimed_set:
                    art["scope"] = art["scope_id"] = scope_id
        rem_files = [f for f in other_files if f not in claimed_set]
        other_sc["files"] = rem_files
        other_sc.setdefault("attributes", {})["files"] = rem_files
        if not rem_files and other_sc.get("_bootstrapped"):
            scopes.pop(other_sid, None)
            for art in artifacts:
                if (art.get("scope") or art.get("scope_id")) == other_sid:
                    art["scope"] = art["scope_id"] = scope_id
            for snode in scopes.values():
                if isinstance(snode.get("depends_on"), list) and other_sid in snode["depends_on"]:
                    snode["depends_on"] = [
                        d for d in (scope_id if x == other_sid else x for x in snode["depends_on"])
                        if d != snode.get("id")
                    ]


def _fuse_component_rows(graph: dict[str, Any], table: dict[str, Any], rows: list[Any], headers: list[Any],
    norm_headers: list[str], turn_index: int, fused_scopes: list[str]) -> None:
    primary_col_idx = next(
        (norm_headers.index(k) for k in ("component", "scope", "scope_id", "module") if k in norm_headers), 0)
    primary_hdr, heading_path = str(headers[primary_col_idx]), table.get("heading_path")
    non_alias_cols = frozenset({
        "role", "purpose", "responsibility", "why", "rationale", "owner", "division", "stage", "phase",
        "parent", "depends_on", "deps", "dependencies", "children", "inputs", "input",
        "outputs", "output", "files", "file", "scope"})
    for row in (r for r in rows):
        if not (raw_entity := str(row.get(primary_hdr, row.get(norm_headers[primary_col_idx], ""))).strip()):
            continue
        scope_id = _resolve_or_register_scope(graph, raw_entity, create=True)
        scope = graph["scopes"][scope_id]
        if scope_id not in fused_scopes:
            fused_scopes.append(scope_id)
        attrs = scope.setdefault("attributes", {})
        if isinstance(heading_path, str) and heading_path.strip():
            attrs.setdefault("heading_path", heading_path.strip())
        attrs["updated_turn"] = int(turn_index)
        sbf_kwargs: dict[str, Any] = {}
        files_list: list[str] | None = None
        for col_idx, raw_hdr in enumerate(headers):
            if col_idx == primary_col_idx or (raw_hdr not in row and norm_headers[col_idx] not in row):
                continue
            col_key = norm_headers[col_idx]
            raw_cell_str = str(row.get(raw_hdr, row.get(col_key, "")))
            cell_val = _parse_scalar_cell(raw_cell_str)
            attrs[col_key] = cell_val
            if col_key not in non_alias_cols and isinstance(cell_val, str) and cell_val:
                if "`" in raw_cell_str or "/" in cell_val:
                    _register_scope_aliases(graph, scope_id, _extract_canonical_and_aliases(cell_val)[1])
            clean_str = str(cell_val).strip()
            is_empty_cell = clean_str.lower() in _EMPTY_TOKENS
            if col_key in ("role", "purpose", "responsibility"):
                if not is_empty_cell:
                    sbf_kwargs["role"] = attrs["_table_role"] = clean_str
                    scope["_role_missing"] = False
                elif not scope.get("role"):
                    scope["_role_missing"] = True
            elif col_key in ("why", "rationale"):
                if not is_empty_cell:
                    scope["why"] = attrs["why"] = clean_str
                    scope["_why_missing"] = False
                elif not scope.get("why"):
                    scope["_why_missing"] = True
            elif col_key in ("owner", "division") and not is_empty_cell:
                sbf_kwargs["owner"] = (_extract_canonical_and_aliases(raw_cell_str)[0]
                    if ("`" in raw_cell_str or "(" in raw_cell_str) else clean_str)
            elif col_key in ("stage", "phase") and not is_empty_cell:
                sbf_kwargs["stage"] = clean_str
            elif col_key == "parent" and not is_empty_cell:
                sbf_kwargs["parent"] = _slugify_scope_id(clean_str) or clean_str
            elif col_key in ("depends_on", "deps", "dependencies") and (
                not is_empty_cell or not scope.get("depends_on")
            ):
                dep_sids = [
                    _resolve_or_register_scope(graph, d, create=True) for d in _parse_list_cell(raw_cell_str) if d]
                sbf_kwargs["depends_on"] = [d for d in dep_sids if d != scope_id]
                attrs["_table_depends_on"] = list(sbf_kwargs["depends_on"])
            elif col_key == "children":
                sbf_kwargs["children"] = [
                    _resolve_or_register_scope(graph, c, create=True) for c in _parse_list_cell(raw_cell_str) if c]
            elif col_key in ("inputs", "input"):
                sbf_kwargs["inputs"] = _parse_list_cell(raw_cell_str)
            elif col_key in ("outputs", "output"):
                sbf_kwargs["outputs"] = _parse_list_cell(raw_cell_str)
            elif col_key in ("files", "file") or (col_key == "scope" and files_list is None and not is_empty_cell):
                parsed_f = _parse_list_cell(raw_cell_str)
                if parsed_f or not scope.get("files"):
                    files_list = parsed_f
                    scope["files"] = attrs["files"] = files_list
                    attrs["_table_files"] = list(files_list)
                    _reconcile_bootstrapped_file_claims(graph, scope_id, files_list)
            elif col_key in ("tests", "test", "test_files") and not is_empty_cell:
                t_list = _parse_list_cell(raw_cell_str)
                scope["tests"] = attrs["tests"] = sbf_kwargs["tests"] = t_list
        if sbf_kwargs or files_list is not None:
            eff_owner = next((_v for _v in (sbf_kwargs.get("owner"), scope.get("owner")) if _v), None) or "core"
            upsert_component_spec(graph, scope_id, role=sbf_kwargs.get("role"), inputs=sbf_kwargs.get("inputs"),
                outputs=sbf_kwargs.get("outputs"), depends_on=sbf_kwargs.get("depends_on"),
                current_turn=int(turn_index), children=sbf_kwargs.get("children"),
                parent=sbf_kwargs.get("parent"), owner=eff_owner, stage=sbf_kwargs.get("stage"),
                kind="COMPONENT", files=files_list, tests=sbf_kwargs.get("tests"))

def fuse_table_attributes(
    graph: dict[str, Any], doc_tables: list[dict[str, Any]], turn_index: int = 1, *, source_turn: int | None = None,
) -> list[str]:
    """Stage 4A.3: Fuses Component, Flow, Contract, Law, Schema, and Rule tables into `graph` (Spec 22C §2.2)."""
    if not isinstance(graph, dict) or not isinstance(doc_tables, list):
        raise ValueError("graph must be a dict and doc_tables must be a list")
    if source_turn is not None:
        turn_index = int(source_turn)
    fused_scopes: list[str] = []
    for t_idx, table in enumerate(doc_tables):
        if not isinstance(table, dict):
            raise ValueError(f"doc_tables[{t_idx}] must be a dict")
        headers, rows = table.get("headers"), table.get("rows")
        if headers is None and rows is None and any(
            k in table for k in ("component", "interface", "law", "flow", "schema", "rule")):
            headers, rows = list(table.keys()), [table]
        if not isinstance(headers, list) or not headers or not isinstance(rows, list):
            raise ValueError(f"doc_tables[{t_idx}] must have non-empty 'headers' and 'rows' lists")
        norm_headers = [_slugify_scope_id(str(h)) for h in headers]
        if any(not nh for nh in norm_headers):
            raise ValueError(f"doc_tables[{t_idx}] contains empty/unmapped structural header in {headers!r}")
        is_charter_t, is_flow_t, is_rule_t = _validate_table_headers(t_idx, headers, norm_headers)
        if is_charter_t:
            for row in (r for r in rows):
                raw_c = _get_table_cell(row, headers, norm_headers, ("charter", "project", "board", "name")) or str(
                    row.get(headers[0], ""))
                cname = restore_ascii_dots(raw_c).strip().strip("\"`'*").strip() or "project"
                csum = _clean_cell_text(row, headers, norm_headers, ("summary", "purpose", "description", "why"))
                cprin = _clean_cell_text(row, headers, norm_headers, ("principles", "non_goals", "rationale"))
                graph["charter"] = {"name": cname, "summary": csum, "principles": cprin}
                ensure_scope_node(graph, "global_defaults")["attributes"]["charter"] = dict(graph["charter"])
                if "global_defaults" not in fused_scopes:
                    fused_scopes.append("global_defaults")
        elif is_flow_t or (is_rule_t and "component" not in norm_headers[:1]):
            _fuse_flow_or_rule_rows(graph, rows, headers, norm_headers, is_flow_t, turn_index, fused_scopes)
        elif "schema" in norm_headers or "interface" in norm_headers or "law" in norm_headers:
            _fuse_artifact_or_law_rows(graph, rows, headers, norm_headers, turn_index, fused_scopes)
        else:
            _fuse_component_rows(graph, table, rows, headers, norm_headers, turn_index, fused_scopes)
    sync_to_sqlite(graph)
    return fused_scopes

def _filter_non_dep_tokens(graph: dict[str, Any], scope_id: str, raw_deps: list[Any],
    mcu_text: str, lpayload: dict[str, Any], parsed_mcu: dict[str, Any]) -> tuple[list[str], set[str]]:
    """Filters `raw_deps` to exclude `role`, `files`, `.py`/`.bend` filenames, and prose role tails."""
    sc = graph.get("scopes", {}).get(scope_id, {})
    attrs = sc.get("attributes", {}) if bool(sc.get("attributes")) else {}
    non_dep_low = {"files", "file", "role", "purpose", "responsibility", "laws.bend", "proof.bend", "main.bend"}
    for rv in (
        sc.get("role"), attrs.get("role"), attrs.get("_table_role"), lpayload.get("role"), parsed_mcu.get("role")):
        if isinstance(rv, str) and rv.strip() and " " not in rv.strip():
            non_dep_low.update((rv.strip().lower(), _slugify_scope_id(rv)))
    for flist in (
        sc.get("files"), attrs.get("files"), attrs.get("_table_files"), lpayload.get("files"), parsed_mcu.get("files"),
    ):
        items = _parse_list_cell(flist) if isinstance(flist, str) else (flist if isinstance(flist, list) else [])
        for fitem in items:
            if fs := str(fitem).strip().lower():
                non_dep_low.update((fs, _slugify_scope_id(fs)))
    text_low = restore_ascii_dots(mcu_text or "").lower()
    role_markers = ("serves as a ", "serves as ", "has the role ", "with role ", "with files ", "and files ")
    role_tail_pos = min((text_low.find(m) for m in role_markers if m in text_low), default=-1)
    if "_table_depends_on" in attrs and int(attrs.get("updated_turn", 0)) == int(graph.get("turn_index", 0)):
        return list(attrs["_table_depends_on"]), non_dep_low
    clean_deps: list[str] = []
    for d in raw_deps:
        if not (ds := str(d).strip().strip("\"`'*").strip()):
            continue
        d_low, d_slug = ds.lower(), _slugify_scope_id(ds)
        is_role_tail = (
            role_tail_pos >= 0 and text_low.find(d_low) > role_tail_pos and text_low.find(d_low, 0, role_tail_pos) < 0
        )
        if (d_low in non_dep_low or d_slug in non_dep_low or "." in ds
            or any(d_low.endswith(ext) for ext in ("_py", "_bend", "_mjs", "_json")) or is_role_tail):
            non_dep_low.update((d_low, d_slug))
            continue
        clean_deps.append(ds)
    return clean_deps, non_dep_low

def resolve_mcu_scopes_and_refs(graph: dict[str, Any], parsed_mcu: dict[str, Any]) -> tuple[str, list[str]]:
    """Stage 4A.4: Resolves `parsed_mcu['primary_scope']` and `secondary_refs` to canonical `scope_id`s."""
    if not isinstance(graph, dict) or not isinstance(parsed_mcu, dict):
        raise ValueError("graph and parsed_mcu must be dicts")
    raw_primary = str(
        parsed_mcu.get("primary_scope", parsed_mcu.get("scope", parsed_mcu.get("scope_id"))) or "global_defaults")
    raw_text = parsed_mcu.get("text", parsed_mcu.get("canonical_text", parsed_mcu.get("raw_text", ""))) or ""
    mcu_text = restore_ascii_dots(str(raw_text)).strip()
    primary_scope = _resolve_known_component_scope(graph, raw_primary, mcu_text, parsed_mcu)
    target_prop = str(parsed_mcu.get("target_prop") or "").strip()
    if primary_scope != raw_primary and raw_primary != "global_defaults" and "_" in raw_primary and (
        not target_prop or target_prop in ("value", primary_scope) or target_prop.startswith(f"{primary_scope}_")):
        parsed_mcu["target_prop"] = target_prop = raw_primary
    parsed_mcu["primary_scope"] = primary_scope
    bound_val_str = str(parsed_mcu.get("bound_val") if parsed_mcu.get("bound_val") is not None else "").strip()
    lpayload = (parsed_mcu.get("lattice_payload") or {})
    flow_payload_str = str(lpayload.get("payload") or "").strip()
    scalar_excluded = {s.lower() for s in (target_prop, bound_val_str, flow_payload_str) if s}
    raw_deps_all = (list(parsed_mcu.get("depends_on") or []) + list(lpayload.get("depends_on") or [])
        + list(lpayload.get("children") or []))
    clean_deps, non_dep_low = _filter_non_dep_tokens(
        graph, primary_scope, raw_deps_all, mcu_text, lpayload, parsed_mcu)
    artifact_and_io_tokens = [target_prop, bound_val_str, flow_payload_str, str(lpayload.get("id") or "").strip(),
        str(lpayload.get("name") or "").strip(), str(parsed_mcu.get("name") or "").strip(),
        str(lpayload.get("owner") or "").strip(), str(parsed_mcu.get("owner") or "").strip(),
        str(lpayload.get("stage") or "").strip(), str(parsed_mcu.get("stage") or "").strip(),
        "laws.bend", "proof.bend", *[str(x).strip() for x in (lpayload.get("ids") or [])],
        *[str(x).strip() for x in (lpayload.get("inputs") or [])],
        *[str(x).strip() for x in (lpayload.get("outputs") or [])],
        *[str(x).strip() for x in (parsed_mcu.get("inputs") or [])],
        *[str(x).strip() for x in (parsed_mcu.get("outputs") or [])]]
    scope_owners = {str(s.get("owner") or "").strip().lower()
        for s in (graph.get("scopes") or {}).values() if s.get("owner")}
    excluded_vals = scope_owners | non_dep_low
    for tok in artifact_and_io_tokens:
        if tok:
            excluded_vals.add(tok.lower())
            if slug_tok := _slugify_scope_id(tok):
                excluded_vals.add(slug_tok)
    flow_src, flow_tgt = str(lpayload.get("source") or "").strip(), str(lpayload.get("target") or "").strip()
    flow_endpoints_low = {
        s.lower() for s in (flow_src, flow_tgt, _slugify_scope_id(flow_src), _slugify_scope_id(flow_tgt)) if s}
    deps_low = {str(d).strip().lower() for d in clean_deps if str(d).strip()} | {
        _slugify_scope_id(str(d)) for d in clean_deps if str(d).strip()}
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
            is_flow_ep = ref_low in flow_endpoints_low or ref_slug in flow_endpoints_low
            is_exp_comp = ref_low in deps_low or ref_slug in deps_low
            is_owner_id = ((ref_low.startswith("eng") and ref_low[3:].isdigit())
                or ref_low in excluded_vals or ref_slug in excluded_vals)
            is_bound_val = (parsed_mcu.get("type") == "ACTION" or is_owner_id
                or (parsed_mcu.get("type") in ("PROHIBITION", "OBLIGATION", "BOUND", "FACT")
                and not target_prop and not bound_val_str and not is_flow_ep))
            is_hex = (len(ref_str) >= 6 and any(c.isdigit() for c in ref_str)
                and all(c in "0123456789abcdef" for c in ref_low))
            has_bad_ch = any(ch in ref_str for ch in (".", "*", "<", ">", ":", " ", "-"))
            if (should_register_as_scope(False, is_flow_ep, is_exp_comp, is_bound_val)
                and not is_bound_val and len(ref_str) >= 3 and not has_bad_ch and not is_hex):
                ref_scope_id = _resolve_or_register_scope(graph, ref_str, create=True)
            else:
                continue
        if (ref_scope_id and ref_scope_id in scopes
            and ref_scope_id != primary_scope and ref_scope_id not in resolved_refs):
            resolved_refs.append(ref_scope_id)
    return primary_scope, resolved_refs
