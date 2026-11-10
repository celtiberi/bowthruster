"""Spec 22C §2.2: Whiteboard Stage 4A Entity/Scope Resolution & Table-Attribute Fusion."""

from __future__ import annotations

import copy
from enum import Enum
from pathlib import Path
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore
from bowthruster.nlp import restore_ascii_dots
from bowthruster.whiteboard.mutations import ensure_scope_node
from bowthruster.whiteboard.schema import (
    DEFAULT_K_CAP,
    DEFAULT_MAX_RING,
    WhiteboardGraph,
    create_empty_graph,
    should_register_as_scope,
    sync_to_sqlite,
)
from .row_fusers import (
    _extract_owner_and_stage,
    _fuse_artifact_or_law_rows,
    _fuse_component_rows,
    _fuse_flow_or_rule_rows,
    _reconcile_bootstrapped_file_claims,
)
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


class IngestionStatus(str, Enum):
    """Explicit state machine states for Stage 4C ingestion status (Spec 22C §2.2, Law 6)."""

    COMMITTED = "COMMITTED"
    CONFLICT = "CONFLICT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


VALID_INGESTION_STATUSES = frozenset(s.value for s in IngestionStatus)


class Phase4InvariantError(ValueError):
    """Raised when a Whiteboard ingestion or ContextGraph invariant check fails."""

    def __init__(self, code: str, errors: list[str]) -> None:
        self.code, self.errors = code, list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


class IngestionResult(dict[str, Any]):
    """Dict-compatible return object of `ingest_intake_packet`."""

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name) or name not in self:
            raise AttributeError(name)
        return self[name]


def _slugify_scope_id(raw: str) -> str:
    """Normalizes a surface entity/scope string into a canonical snake_case/path scope ID."""
    cleaned = restore_ascii_dots(str(raw)).strip().strip("\"`'").strip().rstrip("/")
    chars = [
        ch
        if (ch.isalnum() or ch in ("_", "/", "."))
        else ("_" if ch in ("-", " ", ":") else " ")
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


def fuse_table_attributes(
    graph: dict[str, Any],
    doc_tables: list[dict[str, Any]],
    turn_index: int = 1,
    *,
    source_turn: int | None = None,
) -> list[str]:
    """Stage 4A.3: Fuses Component, Flow, Contract, Law, Schema, and Rule tables into `graph`."""
    if not isinstance(graph, dict) or not isinstance(doc_tables, list):
        raise ValueError("graph must be a dict and doc_tables must be a list")
    if source_turn is not None:
        turn_index = int(source_turn)
    fused_scopes: list[str] = []
    for t_idx, table in enumerate(doc_tables):
        if not isinstance(table, dict):
            raise ValueError(f"doc_tables[{t_idx}] must be a dict")
        headers, rows = table.get("headers"), table.get("rows")
        flat_keys = ("component", "interface", "law", "flow", "schema", "rule")
        if headers is None and rows is None and any(k in table for k in flat_keys):
            headers, rows = list(table.keys()), [table]
        if not isinstance(headers, list) or not headers or not isinstance(rows, list):
            raise ValueError(f"doc_tables[{t_idx}] must have non-empty 'headers' and 'rows' lists")
        norm_headers = [_slugify_scope_id(str(h)) for h in headers]
        if any(not nh for nh in norm_headers):
            raise ValueError(
                f"doc_tables[{t_idx}] contains empty/unmapped structural header in {headers!r}"
            )
        is_charter_t, is_flow_t, is_rule_t = _validate_table_headers(t_idx, headers, norm_headers)
        if is_charter_t:
            for row in rows:
                raw_c = _get_table_cell(
                    row, headers, norm_headers, ("charter", "project", "board", "name")
                ) or str(row.get(headers[0], ""))
                cname = restore_ascii_dots(raw_c).strip().strip("\"`'*").strip() or "project"
                csum = _clean_cell_text(
                    row, headers, norm_headers, ("summary", "purpose", "description", "why")
                )
                cprin = _clean_cell_text(
                    row, headers, norm_headers, ("principles", "non_goals", "rationale")
                )
                graph["charter"] = {"name": cname, "summary": csum, "principles": cprin}
                ensure_scope_node(graph, "global_defaults")["attributes"]["charter"] = dict(
                    graph["charter"]
                )
                if "global_defaults" not in fused_scopes:
                    fused_scopes.append("global_defaults")
        elif is_flow_t or (is_rule_t and "component" not in norm_headers[:1]):
            _fuse_flow_or_rule_rows(
                graph, rows, headers, norm_headers, is_flow_t, turn_index, fused_scopes
            )
        elif "schema" in norm_headers or "interface" in norm_headers or "law" in norm_headers:
            _fuse_artifact_or_law_rows(
                graph, rows, headers, norm_headers, turn_index, fused_scopes
            )
        else:
            _fuse_component_rows(
                graph, table, rows, headers, norm_headers, turn_index, fused_scopes
            )
    sync_to_sqlite(graph)
    return fused_scopes


def _filter_non_dep_tokens(
    graph: dict[str, Any],
    scope_id: str,
    raw_deps: list[Any],
    mcu_text: str,
    lpayload: dict[str, Any],
    parsed_mcu: dict[str, Any],
) -> tuple[list[str], set[str]]:
    """Filters `raw_deps` to exclude `role`, `files`, filenames, and prose role tails."""
    sc = graph.get("scopes", {}).get(scope_id, {})
    attrs = sc.get("attributes", {}) if bool(sc.get("attributes")) else {}
    non_dep_low = {
        "files", "file", "role", "purpose", "responsibility",
        "laws.bend", "proof.bend", "main.bend",
    }
    role_sources = (
        sc.get("role"),
        attrs.get("role"),
        attrs.get("_table_role"),
        lpayload.get("role"),
        parsed_mcu.get("role"),
    )
    for rv in role_sources:
        if isinstance(rv, str) and rv.strip() and " " not in rv.strip():
            non_dep_low.update((rv.strip().lower(), _slugify_scope_id(rv)))
    file_sources = (
        sc.get("files"),
        attrs.get("files"),
        attrs.get("_table_files"),
        lpayload.get("files"),
        parsed_mcu.get("files"),
    )
    for flist in file_sources:
        items = (
            _parse_list_cell(flist)
            if isinstance(flist, str)
            else (flist if isinstance(flist, list) else [])
        )
        for fitem in items:
            if fs := str(fitem).strip().lower():
                non_dep_low.update((fs, _slugify_scope_id(fs)))
    text_low = restore_ascii_dots(mcu_text or "").lower()
    role_markers = (
        "serves as a ", "serves as ", "has the role ",
        "with role ", "with files ", "and files ",
    )
    role_tail_pos = min((text_low.find(m) for m in role_markers if m in text_low), default=-1)
    same_turn = int(attrs.get("updated_turn", 0)) == int(graph.get("turn_index", 0))
    if "_table_depends_on" in attrs and same_turn:
        return list(attrs["_table_depends_on"]), non_dep_low
    clean_deps: list[str] = []
    for d in raw_deps:
        if not (ds := str(d).strip().strip("\"`'*").strip()):
            continue
        d_low, d_slug = ds.lower(), _slugify_scope_id(ds)
        is_role_tail = (
            role_tail_pos >= 0
            and text_low.find(d_low) > role_tail_pos
            and text_low.find(d_low, 0, role_tail_pos) < 0
        )
        has_ext = any(d_low.endswith(ext) for ext in ("_py", "_bend", "_mjs", "_json"))
        if d_low in non_dep_low or d_slug in non_dep_low or "." in ds or has_ext or is_role_tail:
            non_dep_low.update((d_low, d_slug))
            continue
        clean_deps.append(ds)
    return clean_deps, non_dep_low


def resolve_mcu_scopes_and_refs(
    graph: dict[str, Any], parsed_mcu: dict[str, Any]
) -> tuple[str, list[str]]:
    """Stage 4A.4: Resolves `parsed_mcu['primary_scope']` and `secondary_refs`."""
    if not isinstance(graph, dict) or not isinstance(parsed_mcu, dict):
        raise ValueError("graph and parsed_mcu must be dicts")
    raw_primary = str(
        parsed_mcu.get("primary_scope", parsed_mcu.get("scope", parsed_mcu.get("scope_id")))
        or "global_defaults"
    )
    raw_text = (
        parsed_mcu.get("text", parsed_mcu.get("canonical_text", parsed_mcu.get("raw_text", "")))
        or ""
    )
    mcu_text = restore_ascii_dots(str(raw_text)).strip()
    primary_scope = _resolve_known_component_scope(graph, raw_primary, mcu_text, parsed_mcu)
    target_prop = str(parsed_mcu.get("target_prop") or "").strip()
    prop_is_default = (
        not target_prop
        or target_prop in ("value", primary_scope)
        or target_prop.startswith(f"{primary_scope}_")
    )
    if (
        primary_scope != raw_primary
        and raw_primary != "global_defaults"
        and "_" in raw_primary
        and prop_is_default
    ):
        parsed_mcu["target_prop"] = target_prop = raw_primary
    parsed_mcu["primary_scope"] = primary_scope
    raw_bv = parsed_mcu.get("bound_val")
    bound_val_str = str(raw_bv if raw_bv is not None else "").strip()
    lpayload = parsed_mcu.get("lattice_payload") or {}
    flow_payload_str = str(lpayload.get("payload") or "").strip()
    scalar_excluded = {s.lower() for s in (target_prop, bound_val_str, flow_payload_str) if s}
    raw_deps_all = (
        list(parsed_mcu.get("depends_on") or [])
        + list(lpayload.get("depends_on") or [])
        + list(lpayload.get("children") or [])
    )
    clean_deps, non_dep_low = _filter_non_dep_tokens(
        graph, primary_scope, raw_deps_all, mcu_text, lpayload, parsed_mcu
    )
    artifact_and_io_tokens = [
        target_prop,
        bound_val_str,
        flow_payload_str,
        str(lpayload.get("id") or "").strip(),
        str(lpayload.get("name") or "").strip(),
        str(parsed_mcu.get("name") or "").strip(),
        str(lpayload.get("owner") or "").strip(),
        str(parsed_mcu.get("owner") or "").strip(),
        str(lpayload.get("stage") or "").strip(),
        str(parsed_mcu.get("stage") or "").strip(),
        "laws.bend",
        "proof.bend",
        *[str(x).strip() for x in (lpayload.get("ids") or [])],
        *[str(x).strip() for x in (lpayload.get("inputs") or [])],
        *[str(x).strip() for x in (lpayload.get("outputs") or [])],
        *[str(x).strip() for x in (parsed_mcu.get("inputs") or [])],
        *[str(x).strip() for x in (parsed_mcu.get("outputs") or [])],
    ]
    scope_owners = {
        str(s.get("owner") or "").strip().lower()
        for s in (graph.get("scopes") or {}).values()
        if s.get("owner")
    }
    excluded_vals = scope_owners | non_dep_low
    for tok in artifact_and_io_tokens:
        if tok:
            excluded_vals.add(tok.lower())
            if slug_tok := _slugify_scope_id(tok):
                excluded_vals.add(slug_tok)
    flow_src = str(lpayload.get("source") or "").strip()
    flow_tgt = str(lpayload.get("target") or "").strip()
    flow_endpoints_low = {
        s.lower()
        for s in (flow_src, flow_tgt, _slugify_scope_id(flow_src), _slugify_scope_id(flow_tgt))
        if s
    }
    deps_low = {str(d).strip().lower() for d in clean_deps if str(d).strip()} | {
        _slugify_scope_id(str(d)) for d in clean_deps if str(d).strip()
    }
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
            is_owner_id = (
                (ref_low.startswith("eng") and ref_low[3:].isdigit())
                or ref_low in excluded_vals
                or ref_slug in excluded_vals
            )
            is_rule_type = parsed_mcu.get("type") in ("PROHIBITION", "OBLIGATION", "BOUND", "FACT")
            is_bound_val = (
                parsed_mcu.get("type") == "ACTION"
                or is_owner_id
                or (is_rule_type and not target_prop and not bound_val_str and not is_flow_ep)
            )
            is_hex = (
                len(ref_str) >= 6
                and any(c.isdigit() for c in ref_str)
                and all(c in "0123456789abcdef" for c in ref_low)
            )
            has_bad_ch = any(ch in ref_str for ch in (".", "*", "<", ">", ":", " ", "-"))
            can_reg = (
                should_register_as_scope(False, is_flow_ep, is_exp_comp, is_bound_val)
                and not is_bound_val
                and len(ref_str) >= 3
                and not has_bad_ch
                and not is_hex
            )
            if can_reg:
                ref_scope_id = _resolve_or_register_scope(graph, ref_str, create=True)
            else:
                continue
        if (
            ref_scope_id
            and ref_scope_id in scopes
            and ref_scope_id != primary_scope
            and ref_scope_id not in resolved_refs
        ):
            resolved_refs.append(ref_scope_id)
    return primary_scope, resolved_refs
