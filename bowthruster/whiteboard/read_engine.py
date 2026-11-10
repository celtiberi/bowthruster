"""Spec 22C §2.1: Whiteboard Read Engine & Hyperplane Slice (`read_engine.py`)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from bowthruster.graph_core import format_compact_view, matches_nd_slice, token_cost, tokenize_content_words
from bowthruster.whiteboard.frustration import (
    _node_coord_overlap,
    _scope_chain_values,
    detect_frustration,
)
from bowthruster.whiteboard.schema import normalize_board_graph
from .ast_slice import (
    _AST_SPAN_CACHE,
    _format_code_window,
    _load_cached_file_ast,
    _merge_matched_symbol_context,
    _refresh_artifacts_from_disk,
    _resolve_slice_file,
)
from .table_helpers import (
    _enrich_view_compact_lines,
    _insert_tag_into_line,
    _norm_files_list,
    _strip_file_uri,
)
from .task_readiness import (
    _evaluate_task_flags,
    _mark_file_scopes_as_components,
    _rule_prohibits_task,
    annotate_active_tasks,
    classify_task_status,
    expand_node,
    prohibition_blocks_task,
    propagate_graph,
    scan_graph,
)


class ContextView(dict[str, Any]):
    """Sliced nD Lattice context view (`status`, `component_specs`, `active_flows`, `compact_view`)."""


def _node_matches_slice_dims(
    node: dict[str, Any], node_kinds: set[str], sid: str | None, dims: dict[str, Any]
) -> bool:
    """Evaluates whether `node` satisfies all 5 nD coordinates via `matches_nd_slice`."""
    scopes = dims["scopes"]
    scope_obj = scopes.get(sid, {}) if (sid and isinstance(scopes.get(sid), dict)) else {}
    comp_ov, div_ov, stg_ov, sch_ov = _node_coord_overlap(node, scope_obj, sid, dims)
    component, division = dims["component"], dims["division"]
    stage, schema = dims["stage"], dims["schema"]
    if not matches_nd_slice(not bool(component and str(component).strip()), comp_ov):
        return False
    if not matches_nd_slice(not bool(division and str(division).strip()), div_ov):
        return False
    if not matches_nd_slice(not bool(stage and str(stage).strip()), stg_ov):
        return False
    if not matches_nd_slice(not bool(schema and str(schema).strip()), sch_ov):
        return False
    kind = dims["kind"]
    knd_unc = not bool(kind and str(kind).strip())
    knd_hit = (str(kind).strip().upper() in node_kinds) if not knd_unc else False
    if not matches_nd_slice(knd_unc, knd_hit):
        return False
    raw_query_low = dims["raw_query_low"]
    if raw_query_low:
        attrs = node.get("attributes") or {}
        blob = " ".join(
            str(v)
            for v in (
                node.get("id"),
                node.get("name"),
                node.get("scope"),
                node.get("scope_id"),
                node.get("source"),
                node.get("target"),
                node.get("payload"),
                node.get("role"),
                node.get("why"),
                attrs.get("why"),
                node.get("spec"),
                node.get("text"),
                node.get("target_prop"),
                node.get("owner"),
                node.get("stage"),
                node.get("files"),
                node.get("file"),
                node.get("bend_dir"),
                node.get("args"),
                node.get("lhs"),
                node.get("rhs"),
            )
            if v is not None
        )
        node_toks = set(tokenize_content_words(blob))
        q_tokens = dims["q_tokens"]
        if not (raw_query_low in blob.lower() or (q_tokens and bool(q_tokens & node_toks))):
            return False
    return True


def _scan_ast_symbols_on_disk(
    scopes: dict[str, Any],
    raw_query_low: str,
    component: str | None,
    active_artifacts: list[dict[str, Any]],
    include_code: bool,
) -> list[dict[str, Any]]:
    _refresh_artifacts_from_disk(active_artifacts)
    if not raw_query_low and not include_code:
        return []
    matched: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if raw_query_low:
        q_parts = [p for p in raw_query_low.replace("#l", " ").replace(":", " ").split() if p]
        q_clean = q_parts[0] if q_parts else raw_query_low.strip()
        q_sub = q_parts[1:]
        sub_toks = [t for t in q_clean.split("_") if len(t) >= 3] if "_" in q_clean else []
        for tok_filter in ([q_clean], sub_toks):
            if not tok_filter or matched:
                break
            for sid, sc in scopes.items():
                comp_mismatch = component and sid.lower() != str(component).strip().lower()
                if not isinstance(sc, dict) or comp_mismatch:
                    continue
                raw_sc_files = sc.get("files") or (sc.get("attributes") or {}).get("files")
                for f_raw in _norm_files_list(raw_sc_files):
                    cp = _strip_file_uri(f_raw)
                    fp = _resolve_slice_file(cp)
                    if not fp.is_file():
                        continue
                    sym_spans, src_lines, _ = _load_cached_file_ast(fp, cp)
                    for sym_name, s, e in sym_spans:
                        nl = sym_name.lower()
                        is_hit = (
                            (nl == q_clean or (len(q_clean) >= 4 and q_clean in nl))
                            if len(tok_filter) == 1
                            else all(t in f"{cp.lower()} {nl}" for t in tok_filter)
                        )
                        if (cp, sym_name) not in seen and is_hit:
                            seen.add((cp, sym_name))
                            entry: dict[str, Any] = {
                                "symbol": sym_name,
                                "scope": sid,
                                "file": f"{cp}#L{s}-L{e}",
                                "start_line": s,
                                "end_line": e,
                                "exact": nl == q_clean or len(tok_filter) > 1,
                            }
                            if include_code:
                                entry["code"] = _format_code_window(src_lines, s, e, q_sub)
                            matched.append(entry)
                            if len(tok_filter) > 1:
                                break
                if len(tok_filter) > 1 and matched:
                    break
        if any(m.get("exact") for m in matched):
            matched = [m for m in matched if m.get("exact")]
    if include_code and not matched:
        for art in active_artifacts:
            if not isinstance(art, dict) or not (af := str(art.get("file") or "").strip()):
                continue
            cp = _strip_file_uri(af)
            fp = _resolve_slice_file(cp)
            if not fp.is_file():
                continue
            sym_spans, src_lines, _ = _load_cached_file_ast(fp, cp)
            sym_dict = {nm: (s, e) for nm, s, e in sym_spans}
            sym = str(next((_v for _v in (art.get("name"), art.get("id")) if _v), None) or "")
            s, e = sym_dict.get(sym, (1, len(src_lines)))
            if sym not in sym_dict and "#L" in af:
                rng = af.split("#L", 1)[1]
                if "-" in rng and all(p.lstrip("Ll").isdigit() for p in rng.split("-", 1)):
                    s, e = (int(p.lstrip("Ll")) for p in rng.split("-", 1))
            if (cp, sym) not in seen and src_lines:
                seen.add((cp, sym))
                matched.append({
                    "symbol": sym or cp,
                    "scope": str(art.get("scope_id") or ""),
                    "file": f"{cp}#L{s}-L{e}",
                    "start_line": s,
                    "end_line": e,
                    "code": _format_code_window(src_lines, s, e, []),
                })
        if not matched:
            for sid, sc in scopes.items():
                if not isinstance(sc, dict) or sid == "global_defaults":
                    continue
                if component and sid.lower() != str(component).strip().lower():
                    continue
                raw_sc_files = sc.get("files") or (sc.get("attributes") or {}).get("files")
                for f_raw in _norm_files_list(raw_sc_files):
                    cp = _strip_file_uri(f_raw)
                    fp = _resolve_slice_file(cp)
                    if fp.is_file():
                        sym_spans, src_lines, _ = _load_cached_file_ast(fp, cp)
                        fallback_spans = [(Path(cp).stem, 1, len(src_lines))]
                        for sym_name, s, e in (sym_spans or fallback_spans):
                            if (cp, sym_name) not in seen and src_lines:
                                seen.add((cp, sym_name))
                                matched.append({
                                    "symbol": sym_name,
                                    "scope": sid,
                                    "file": f"{cp}#L{s}-L{e}",
                                    "start_line": s,
                                    "end_line": e,
                                    "code": _format_code_window(src_lines, s, e, []),
                                })
    return matched


def slice_lattice(
    graph: dict[str, Any],
    query: str = "",
    *,
    component: str | None = None,
    scope: str | None = None,
    division: str | None = None,
    stage: str | None = None,
    schema: str | None = None,
    kind: str | None = None,
    token_budget: int = 2400,
    max_lines: int = 120,
    include_code: bool = False,
) -> dict[str, Any]:
    """Computes an nD Hyperplane Slice over `graph` governed by Spec 21 Law 10."""
    if not isinstance(graph, dict):
        raise ValueError("graph must be a dict")
    eff_budget = int(token_budget)
    eff_max_lines = int(max_lines)
    if component is None and scope is not None:
        component = scope
    normalize_board_graph(graph)
    conflicts = detect_frustration(graph)
    annotate_active_tasks(graph, conflicts)
    scopes = graph["scopes"]
    if component and component not in scopes:
        component = next(
            (
                s
                for s in scopes
                if s != "global_defaults"
                and (s.lower() in component.lower() or component.lower() in s.lower())
            ),
            None if query else component,
        )
    alias_idx = graph.get("alias_index") or {}
    raw_query_low = str(query or "").strip().lower()
    q_tokens = set(tokenize_content_words(query)) if raw_query_low else set()
    dims = {
        "scopes": scopes,
        "alias_idx": alias_idx,
        "component": component,
        "division": division,
        "stage": stage,
        "schema": schema,
        "kind": kind,
        "q_tokens": q_tokens,
        "raw_query_low": raw_query_low,
    }
    sbf_keys = (
        "role", "owner", "stage", "depends_on", "children",
        "parent", "inputs", "outputs", "files",
    )
    component_specs: list[dict[str, Any]] = []
    active_rules: list[dict[str, Any]] = []
    for sid, sc in scopes.items():
        if not sc:
            continue
        is_non_default = sid != "global_defaults" and not str(sid).startswith("global_defaults::")
        is_sbf_comp = is_non_default and (
            str(sc.get("kind") or "").strip().upper() == "COMPONENT"
            or any(sc.get(k) for k in sbf_keys)
        )
        if is_non_default or is_sbf_comp or len(scopes) == 1:
            sc_kinds = {"COMPONENT", "SCOPE"} if is_sbf_comp else {"SCOPE"}
            if sc.get("kind"):
                sc_kinds.add(str(sc["kind"]).strip().upper())
            if _node_matches_slice_dims(sc, sc_kinds, sid, dims):
                component_specs.append(copy.deepcopy(sc))
        for r in sc.get("rules", []):
            if r:
                rtype = str(r.get("type") or "OBLIGATION").strip().upper()
                r_kinds = (
                    {rtype, "FACT"}
                    if rtype in ("FACT", "GOAL", "PROPOSAL")
                    else {rtype, "CONSTRAINT", "RULE"}
                )
                if _node_matches_slice_dims(r, r_kinds, str(r.get("scope") or sid), dims):
                    active_rules.append(copy.deepcopy(r))
    raw_flows = [
        copy.deepcopy(fl)
        for fl in graph["flows"]
        if _node_matches_slice_dims(
            fl,
            {"FLOW", str(fl.get("kind") or "FLOW").strip().upper()},
            str(fl.get("source") or ""),
            dims,
        )
    ]
    active_flows = list({
        (
            str(fl.get("source") or ""),
            str(fl.get("target") or ""),
            str(fl.get("payload", fl.get("condition")) or ""),
        ): fl
        for fl in raw_flows
    }.values())
    active_artifacts = [
        copy.deepcopy(art)
        for art in graph["lattice_artifacts"]
        if _node_matches_slice_dims(
            art,
            {str(art.get("kind") or "SCHEMA").strip().upper(), "ARTIFACT"},
            str(next((_v for _v in (art.get("scope_id"), art.get("scope")) if _v), None) or ""),
            dims,
        )
    ]
    active_tasks = [
        copy.deepcopy(t)
        for t in graph.get("active_tasks", [])
        if _node_matches_slice_dims(t, {"TASK", "ACTION"}, str(t.get("scope") or ""), dims)
    ]
    matched_symbols = _scan_ast_symbols_on_disk(
        scopes, raw_query_low, component, active_artifacts, bool(include_code)
    )
    component_specs, active_flows, active_artifacts, active_rules = _merge_matched_symbol_context(
        graph,
        scopes,
        matched_symbols,
        (component_specs, active_flows, active_artifacts, active_rules),
        bool(include_code),
    )
    open_blockers = [
        copy.deepcopy(b)
        for b in graph.get("open_blockers", [])
        if b.get("status") != "RESOLVED"
    ]
    all_blockers = list(conflicts) + [b for b in open_blockers if b.get("type") != "CONFLICT"]
    status = "CONFLICT" if conflicts else ("BLOCKED" if open_blockers else "OK")
    view: dict[str, Any] = {
        "status": status,
        "component_specs": component_specs,
        "active_flows": active_flows,
        "active_artifacts": active_artifacts,
        "active_rules": active_rules,
        "active_tasks": active_tasks,
        "matched_symbols": matched_symbols,
        "blockers": all_blockers,
        "conflicts": conflicts,
        "open_blockers": open_blockers,
    }
    compact_str = format_compact_view(view, max_lines=eff_max_lines, token_budget=eff_budget)
    view.update({
        "compact_view": compact_str,
        "text": compact_str,
        "token_count": token_cost(compact_str),
        "line_count": len(compact_str.splitlines()) if compact_str else 0,
    })
    enrich_budget = max(eff_budget, 2400) if (include_code or matched_symbols) else eff_budget
    return _enrich_view_compact_lines(graph, view, eff_budget=enrich_budget)
