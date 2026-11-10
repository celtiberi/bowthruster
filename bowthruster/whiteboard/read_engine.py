"""Spec 22C §2.1: Whiteboard Read Engine & Bend 2-Verified Task Readiness (`read_engine.py`)."""

from __future__ import annotations

import ast
import copy
import os
from pathlib import Path
from typing import Any

from bowthruster.graph_core import (_build_rule_index, expand_graph_node, format_compact_view, matches_nd_slice,
    propagate_nodes, scan_nodes, token_cost, tokenize_content_words)
from bowthruster.whiteboard.frustration import _node_coord_overlap, _scope_chain_values, detect_frustration
from bowthruster.whiteboard.schema import _clean_str_list, normalize_board_graph

class ContextView(dict[str, Any]):
    """Sliced nD Lattice context view (`status`, `component_specs`, `active_flows`, `compact_view`)."""

from .table_helpers import (
    _enrich_view_compact_lines,
    _norm_files_list,
    _strip_file_uri,
)


def _insert_tag_into_line(ln: str, extra_tag: str) -> str:
    if (b_end := ln.find("]")) < 0:
        return ln
    if ln[b_end + 1 : b_end + 3] == " (":
        depth = 0
        for idx in range(b_end + 2, len(ln)):
            depth += 1 if ln[idx] == "(" else (-1 if ln[idx] == ")" else 0)
            if depth == 0:
                return f"{ln[:idx]}, {extra_tag}{ln[idx:]}"
    return f"{ln[: b_end + 1]} ({extra_tag}){ln[b_end + 1 :]}"



def _node_matches_slice_dims(
    node: dict[str, Any], node_kinds: set[str], sid: str | None, dims: dict[str, Any]
) -> bool:
    """Evaluates whether `node` satisfies all 5 nD coordinates via `matches_nd_slice` plus `query` overlap."""
    scopes = dims["scopes"]
    scope_obj = scopes.get(sid, {}) if (sid and isinstance(scopes.get(sid), dict)) else {}
    comp_ov, div_ov, stg_ov, sch_ov = _node_coord_overlap(node, scope_obj, sid, dims)
    component, division, stage, schema = dims["component"], dims["division"], dims["stage"], dims["schema"]
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
    if not matches_nd_slice(knd_unc, (str(kind).strip().upper() in node_kinds) if not knd_unc else False):
        return False
    raw_query_low = dims["raw_query_low"]
    if raw_query_low:
        attrs = (node.get("attributes") or {})
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

def prohibition_blocks_task(
    rule_has_prop: bool, task_has_prop: bool, props_match: bool, mentions_prohibited_val: bool) -> bool:
    """Returns True iff a PROHIBITION rule blocks a task per Spec 22C §2.1 and Bend 2 Laws 9–10."""
    return True if not rule_has_prop else bool((task_has_prop and props_match) or mentions_prohibited_val)

def classify_task_status(has_conflict: bool, has_prohibition: bool, has_unmet_dep: bool) -> str:
    """Classifies task readiness per Spec 22C §2.1 and Bend 2 Law 7 (`task_ready_iff_all_clear`)."""
    if has_conflict:
        return "BLOCKED_BY_CONFLICT"
    if has_prohibition:
        return "BLOCKED_BY_RULE"
    return "WAITING_ON_DEPENDENCY" if has_unmet_dep else "READY"

def _rule_prohibits_task(r: dict[str, Any], task: dict[str, Any]) -> bool:
    """Evaluates whether rule `r` blocks `task` via `prohibition_blocks_task` (Spec 22C §2.1)."""
    if r.get("type") != "PROHIBITION" or r.get("exceptions"):
        return False
    r_prop, t_prop = r.get("target_prop"), task.get("target_prop")
    rule_has_prop = r_prop is not None and str(r_prop).strip() != ""
    task_has_prop = t_prop is not None and str(t_prop).strip() != ""
    props_match = bool(rule_has_prop and task_has_prop and r_prop == t_prop)
    r_val_raw = (r.get("value") if isinstance(r.get("value"), str) and r.get("value").strip()
        else (r.get("bound_val") if isinstance(r.get("bound_val"), str) else ""))
    r_val = str(r_val_raw or "").strip().lower()
    t_summary = str(next((_v for _v in (task.get("summary"), task.get("text")) if _v), None) or "").lower()
    t_bval = str(task.get("bound_value") if task.get("bound_value") is not None else (task.get("bound_val") or "")
        ).lower()
    return prohibition_blocks_task(
        rule_has_prop, task_has_prop, props_match, bool(r_val and (r_val in t_summary or r_val in t_bval)))

def _evaluate_task_flags(graph: dict[str, Any], task: dict[str, Any], conflicts: list[dict[str, Any]],
    ) -> tuple[bool, bool, bool]:
    """Derives `(has_conflict, has_prohibition, has_unmet_dep)` for `task` against `graph` and `conflicts`."""
    t_scope = str(task.get("scope") or "global_defaults")
    gov_ids = {str(g) for g in task.get("governed_by", []) if g}
    rule_idx = _build_rule_index(graph) if isinstance(graph, dict) else {}
    scopes = graph.get("scopes", {}) if isinstance(graph, dict) else {}
    active_tasks = graph.get("active_tasks", []) if isinstance(graph, dict) else []
    open_blockers = [b for b in ((graph or {}).get("open_blockers", []) or [])
        if b.get("status") != "RESOLVED"]
    all_conflicts = list(conflicts) + [b for b in open_blockers if b.get("type") == "CONFLICT"]
    has_conflict = bool(task.get("has_conflict", False)) or any(
        (c.get("scope") == t_scope or (isinstance(c.get("scope"), str) and c["scope"]
        and (t_scope.startswith(f"{c['scope']}::") or c["scope"].startswith(f"{t_scope}::")))
        or bool(set(c.get("rule_ids", [])) & gov_ids)) for c in all_conflicts)
    scope_rules = scopes.get(t_scope, {}).get("rules", []) if scopes.get(t_scope) else []
    cand_rules = [rule_idx[rid][1] for rid in gov_ids if rid in rule_idx] + [
        r for r in scope_rules]
    has_prohibition = bool(task.get("has_prohibition", False)) or any(
        _rule_prohibits_task(r, task) for r in cand_rules)
    has_unmet_dep = bool(task.get("has_unmet_dep", False)) or any(
        b.get("type") in ("CLARIFICATION", "QUESTION") and b.get("scope") == t_scope for b in open_blockers)
    if not has_unmet_dep:
        for dep_id in (str(d).strip() for d in task.get("depends_on", []) if str(d).strip()):
            unmet_task = any(isinstance(ot, dict) and ot.get("id") != task.get("id")
                and (ot.get("id") == dep_id or ot.get("scope") == dep_id) and not ot.get("completed", False)
                for ot in active_tasks)
            unmet_blocker = any(b.get("id") == dep_id or b.get("scope") == dep_id for b in open_blockers)
            missing_dep = dep_id not in scopes and dep_id not in rule_idx and f"fact_{dep_id}" not in rule_idx
            if unmet_task or unmet_blocker or missing_dep:
                has_unmet_dep = True
                break
    return has_conflict, has_prohibition, has_unmet_dep

def annotate_active_tasks(graph: dict[str, Any], conflicts: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Annotates each task in `tasks` (and `graph['active_tasks']`) with its `status` via `classify_task_status`."""
    if not isinstance(graph, dict):
        return []
    eff_conflicts = conflicts if conflicts is not None else detect_frustration(graph)
    for t in graph.get("active_tasks", []):
        if t:
            t["status"] = classify_task_status(*_evaluate_task_flags(graph, t, eff_conflicts))
    if tasks is not None:
        for t in tasks:
            if t:
                t["status"] = classify_task_status(*_evaluate_task_flags(graph, t, eff_conflicts))
        return tasks
    return list(graph.get("active_tasks", []))

def _mark_file_scopes_as_components(graph: dict[str, Any]) -> None:
    if isinstance(graph, dict):
        for sc in graph["scopes"].values():
            if sc.get("files") and not sc.get("kind"):
                sc["kind"] = "COMPONENT"

def scan_graph(graph: dict[str, Any], query: str = "", temperature: float | None = None,
    token_budget: int = 400, max_lines: int = 35, *, max_tokens: int | None = None) -> dict[str, Any]:
    """Executes a 1-hop index scan over `graph` and annotates `active_tasks` (Spec 22C §2.1)."""
    _mark_file_scopes_as_components(graph)
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    view = scan_nodes(graph, query, token_budget=token_budget, max_lines=max_lines,
        max_tokens=max_tokens, temperature=temperature, conflict_detector=detect_frustration)
    annotate_active_tasks(graph, view.get("conflicts", []), view.get("active_tasks", []))
    return _enrich_view_compact_lines(graph, view, eff_budget=eff_budget)

def propagate_graph(graph: dict[str, Any], query: str, temperature: float | None = None, token_budget: int = 400,
    max_lines: int = 35, gamma: float = 0.75, max_hops: int = 4, **options: Any) -> dict[str, Any]:
    """Executes 5-family multi-hop propagation and annotates `active_tasks` (Spec 22C §2.1)."""
    _mark_file_scopes_as_components(graph)
    max_steps = options.get("max_steps")
    max_tokens = options.get("max_tokens")
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    view = propagate_nodes(graph, query, temperature=temperature, token_budget=token_budget, max_lines=max_lines,
        gamma=gamma, max_hops=max_hops, max_steps=max_steps, max_tokens=max_tokens,
        conflict_detector=detect_frustration)
    annotate_active_tasks(graph, view.get("conflicts", []), view.get("active_tasks", []))
    return _enrich_view_compact_lines(graph, view, eff_budget=eff_budget)

def expand_node(graph: dict[str, Any], node_id: str, query: str = "", token_budget: int = 400, max_lines: int = 35,
    ) -> dict[str, Any]:
    """Expands a single scope or rule `node_id` and annotates `active_tasks` (Spec 22C §2.1)."""
    _mark_file_scopes_as_components(graph)
    view = expand_graph_node(graph, node_id, query=query, token_budget=token_budget,
        max_lines=max_lines, conflict_detector=detect_frustration)
    annotate_active_tasks(graph, view.get("conflicts", []), view.get("active_tasks", []))
    return _enrich_view_compact_lines(graph, view, eff_budget=token_budget)

def _format_code_window(src_lines: list[str], s: int, e: int, q_sub: list[str]) -> str:
    for tok in q_sub:
        if "-" in tok and all(p.lstrip("l").isdigit() for p in tok.split("-", 1)):
            ws, we = (max(1, min(len(src_lines), int(p.lstrip("l")))) for p in tok.split("-", 1))
            return f"[WINDOW #L{ws}-L{we}]\n" + "\n".join(src_lines[ws - 1 : we])
    if q_sub:
        hit = next(
            (i + 1 for i in range(max(0, s - 1), min(len(src_lines), e)) if any(k in src_lines[i].lower() for k
                in q_sub)),
            None)
        if hit:
            ws, we = max(s, hit - 5), min(e, max(s, hit - 5) + 90)
            return f"[WINDOW #L{ws}-L{we}]\n" + "\n".join(src_lines[ws - 1 : we])
    full_blk = "\n".join(src_lines[max(0, s - 1) : e])
    if len(full_blk) <= 15000:
        return full_blk
    head = src_lines[max(0, s - 1) : min(e, s + 59)]
    lm_prefixes = ("if ", "elif ", "else:", "for ", "while ", "try:", "except", "def ", "class ")
    lms = [
        f"#   L{i}: {st[:76]}" for i in range(s + 60, min(len(src_lines), e) + 1)
        if (st := src_lines[i - 1].strip()).startswith(lm_prefixes)
    ][:20]
    hdr = f"# ... [LANDMARKS #L{s + 60}-L{e} — pass query='<symbol> <keyword>' or '<symbol> <start>-<end>']:"
    return "\n".join([*head, hdr, *lms])

_AST_SPAN_CACHE: dict[str, tuple[int, list[tuple[str, int, int]], list[str], dict[str, list[str]]]] = {}

def _resolve_slice_file(cp: str) -> Path:
    lat_home = Path(__file__).resolve().parents[2]
    env_root = os.environ.get("BOWTHRUSTER_PROJECT_ROOT")
    cwd = Path.cwd().resolve()
    root = Path(env_root).resolve() if env_root else (cwd if (cwd / ".whiteboard").is_dir() else lat_home)
    cands = (root / cp, cwd / cp, lat_home / cp, lat_home.parent / cp)
    return next((c for c in cands if c.is_file()), root / cp)

def _load_cached_file_ast(fp: Path, cp: str) -> tuple[list[tuple[str, int, int]], list[str], dict[str, list[str]]]:
    ckey = str(fp)
    mt = fp.stat().st_mtime_ns
    cached = _AST_SPAN_CACHE.get(ckey)
    if cached is not None and cached[0] == mt:
        return cached[1], cached[2], cached[3]
    src = fp.read_text(encoding="utf-8", errors="ignore")
    src_lines = src.splitlines()
    sym_spans: list[tuple[str, int, int]] = []
    calls_map: dict[str, list[str]] = {}
    if cp.endswith(".py"):
        try:
            tree = ast.parse(src)
            nodes = list(ast.walk(tree))
            sym_spans = [(n.name, int(n.lineno), int(getattr(n, "end_lineno", None) or n.lineno)) for n in nodes
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and getattr(n, "lineno", 0) > 0
                ]
            sym_dict = {nm: (s, e) for nm, s, e in sym_spans}
            for fn in nodes:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    calls_map[fn.name] = sorted({
                        f"{c}#L{sym_dict[c][0]}-L{sym_dict[c][1]}"
                        for s in ast.walk(fn)
                        if isinstance(s, ast.Call) and isinstance(s.func, ast.Name)
                        and (c := s.func.id) in sym_dict and c != fn.name and c.startswith("_")
                    })
        except SyntaxError:
            pass
    else:
        from bowthruster.nlp import _extract_generic_source_symbols
        raw_syms, calls_map = _extract_generic_source_symbols(fp)
        sym_spans = [(nm, s, e) for nm, (s, e) in raw_syms.items()]
    _AST_SPAN_CACHE[ckey] = (mt, sym_spans, src_lines, calls_map)
    return sym_spans, src_lines, calls_map

def _refresh_artifacts_from_disk(active_artifacts: list[dict[str, Any]]) -> None:
    for art in active_artifacts:
        if not isinstance(art, dict) or not (af := str(art.get("file") or "").strip()):
            continue
        cp = _strip_file_uri(af)
        fp = _resolve_slice_file(cp)
        if not fp.is_file():
            continue
        sym_spans, _, calls_map = _load_cached_file_ast(fp, cp)
        sym_dict = {nm: (s, e) for nm, s, e in sym_spans}
        asym = str(next((_v for _v in (art.get("name"), art.get("id")) if _v), None) or "").strip()
        if asym in sym_dict:
            s, e = sym_dict[asym]
            art["file"] = f"{cp}#L{s}-L{e}"
        if asym in calls_map:
            art["calls"] = list(calls_map[asym])

def _scan_ast_symbols_on_disk(scopes: dict[str, Any], raw_query_low: str,
    component: str | None, active_artifacts: list[dict[str, Any]], include_code: bool) -> list[dict[str, Any]]:
    _refresh_artifacts_from_disk(active_artifacts)
    if not raw_query_low and not include_code:
        return []
    matched: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if raw_query_low:
        q_parts = [p for p in raw_query_low.replace("#l", " ").replace(":", " ").split() if p]
        q_clean, q_sub = (q_parts[0] if q_parts else raw_query_low.strip()), q_parts[1:]
        tok_filters = ([q_clean], ([t for t in q_clean.split("_") if len(t) >= 3] if "_" in q_clean else []))
        for tok_filter in tok_filters:
            if not tok_filter or matched:
                break
            for sid, sc in scopes.items():
                if not isinstance(sc, dict) or (component and sid.lower() != str(component).strip().lower()):
                    continue
                for f_raw in _norm_files_list(sc.get("files") or (sc.get("attributes") or {}).get("files")):
                    cp = _strip_file_uri(f_raw)
                    fp = _resolve_slice_file(cp)
                    if not fp.is_file():
                        continue
                    sym_spans, src_lines, _ = _load_cached_file_ast(fp, cp)
                    for sym_name, s, e in sym_spans:
                        nl = sym_name.lower()
                        is_hit = ((nl == q_clean or (len(q_clean) >= 4 and q_clean in nl))
                            if len(tok_filter) == 1 else all(t in f"{cp.lower()} {nl}" for t in tok_filter))
                        if (cp, sym_name) not in seen and is_hit:
                            seen.add((cp, sym_name))
                            matched.append({
                                "symbol": sym_name, "scope": sid, "file": f"{cp}#L{s}-L{e}",
                                "start_line": s, "end_line": e, "exact": nl == q_clean or len(tok_filter) > 1,
                                **({"code": _format_code_window(src_lines, s, e, q_sub)} if include_code else {}),
                            })
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
                    "symbol": sym or cp, "scope": str(art.get("scope_id") or ""),
                    "file": f"{cp}#L{s}-L{e}", "start_line": s, "end_line": e,
                    "code": _format_code_window(src_lines, s, e, []),
                })
        if not matched:
            for sid, sc in scopes.items():
                if not isinstance(sc, dict) or sid == "global_defaults":
                    continue
                if component and sid.lower() != str(component).strip().lower():
                    continue
                for f_raw in _norm_files_list(sc.get("files") or (sc.get("attributes") or {}).get("files")):
                    cp = _strip_file_uri(f_raw)
                    fp = _resolve_slice_file(cp)
                    if fp.is_file():
                        sym_spans, src_lines, _ = _load_cached_file_ast(fp, cp)
                        for sym_name, s, e in (sym_spans or [(Path(cp).stem, 1, len(src_lines))]):
                            if (cp, sym_name) not in seen and src_lines:
                                seen.add((cp, sym_name))
                                matched.append({
                                    "symbol": sym_name, "scope": sid, "file": f"{cp}#L{s}-L{e}",
                                    "start_line": s, "end_line": e, "code": _format_code_window(src_lines, s, e, []),
                                })
    return matched

def _merge_matched_symbol_context(graph: dict[str, Any], scopes: dict[str, Any],
    matched_symbols: list[dict[str, Any]],
    slice_lists: tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
    include_code: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    component_specs, active_flows, active_artifacts, active_rules = slice_lists
    if any(m.get("exact") for m in matched_symbols):
        exact_sids = {str(m.get("scope") or "") for m in matched_symbols if m.get("exact")}
        exact_syms = {str(m.get("symbol") or "").lower() for m in matched_symbols if m.get("exact")}
        component_specs = [copy.deepcopy(scopes[s]) for s in exact_sids if s in scopes]
        active_flows = [copy.deepcopy(fl) for fl in graph.get("flows", [])
            if (fl.get("source") in exact_sids or fl.get("target") in exact_sids)]
        sc_arts = [copy.deepcopy(a) for a in graph.get("lattice_artifacts", [])
            if str(a.get("scope_id") or "") in exact_sids]
        _refresh_artifacts_from_disk(sc_arts)
        active_artifacts = ([a for a in sc_arts if str(a.get("name") or "").lower() in exact_syms]
            or [a for a in sc_arts
            if any(any(es in str(c).lower() for es in exact_syms) for c in (a.get("calls") or []))] or sc_arts
            ) if include_code else sc_arts
        active_rules = [
            copy.deepcopy(r) for s in exact_sids for r in (scopes.get(s, {}).get("rules") or [])
        ]
    else:
        comp_ids = {str(c.get("id") or "") for c in component_specs}
        for m in matched_symbols:
            if (msid := str(m.get("scope") or "")) in scopes and msid not in comp_ids:
                comp_ids.add(msid)
                component_specs.append(copy.deepcopy(scopes[msid]))
                active_flows.extend(copy.deepcopy(fl) for fl in graph.get("flows", [])
                    if msid in (fl.get("source"), fl.get("target")) and fl not in active_flows
                    )
                active_artifacts.extend(copy.deepcopy(a) for a in graph.get("lattice_artifacts", [])
                    if str(a.get("scope_id") or "") == msid
                    and a not in active_artifacts)
                active_rules.extend(copy.deepcopy(r) for r in (scopes[msid].get("rules") or [])
                    if r not in active_rules)
        _refresh_artifacts_from_disk(active_artifacts)
    return component_specs, active_flows, active_artifacts, active_rules

def slice_lattice(graph: dict[str, Any], query: str = "", *, component: str | None = None, scope: str | None = None,
    division: str | None = None, stage: str | None = None, schema: str | None = None, **options: Any,
    ) -> dict[str, Any]:
    """Computes an nD Hyperplane Slice over `graph` governed by Spec 21 Law 10 (`matches_nd_slice`, Spec 22C §2.1)."""
    if not isinstance(graph, dict):
        raise ValueError("graph must be a dict")
    kind = options.get("kind")
    token_budget = int(options.get("token_budget", 2400))
    max_lines = int(options.get("max_lines", 120))
    include_code = bool(options.get("include_code", False))
    if component is None and scope is not None:
        component = scope
    normalize_board_graph(graph)
    conflicts = detect_frustration(graph)
    annotate_active_tasks(graph, conflicts)
    scopes = graph["scopes"]
    if component and component not in scopes:
        component = next((s for s in scopes if s != "global_defaults"
            and (s.lower() in component.lower() or component.lower() in s.lower())), None if query else component)
    alias_idx = (graph.get("alias_index") or {})
    raw_query_low = str(query or "").strip().lower()
    q_tokens = set(tokenize_content_words(query)) if raw_query_low else set()
    dims = {"scopes": scopes, "alias_idx": alias_idx, "component": component, "division": division,
        "stage": stage, "schema": schema, "kind": kind, "q_tokens": q_tokens, "raw_query_low": raw_query_low}
    sbf_keys = ("role", "owner", "stage", "depends_on", "children", "parent", "inputs", "outputs", "files")
    component_specs: list[dict[str, Any]] = []
    active_rules: list[dict[str, Any]] = []
    for sid, sc in scopes.items():
        if not sc:
            continue
        is_non_default = sid != "global_defaults" and not str(sid).startswith("global_defaults::")
        is_sbf_comp = is_non_default and (
            str(sc.get("kind") or "").strip().upper() == "COMPONENT" or any(sc.get(k) for k in sbf_keys))
        if is_non_default or is_sbf_comp or len(scopes) == 1:
            sc_kinds = {"COMPONENT", "SCOPE"} if is_sbf_comp else {"SCOPE"}
            if sc.get("kind"):
                sc_kinds.add(str(sc["kind"]).strip().upper())
            if _node_matches_slice_dims(sc, sc_kinds, sid, dims):
                component_specs.append(copy.deepcopy(sc))
        for r in sc.get("rules", []):
            if r:
                rtype = str(r.get("type") or "OBLIGATION").strip().upper()
                r_kinds = {rtype, "FACT"} if rtype in ("FACT", "GOAL", "PROPOSAL") else {rtype, "CONSTRAINT", "RULE"}
                if _node_matches_slice_dims(r, r_kinds, str(r.get("scope") or sid), dims):
                    active_rules.append(copy.deepcopy(r))
    raw_flows = [copy.deepcopy(fl) for fl in graph["flows"] if _node_matches_slice_dims(
        fl, {"FLOW", str(fl.get("kind") or "FLOW").strip().upper()}, str(fl.get("source") or ""), dims)]
    active_flows = list({
        (str(fl.get("source") or ""), str(fl.get("target") or ""), str(fl.get("payload", fl.get("condition"))
            or "")): fl
        for fl in raw_flows }.values())
    active_artifacts = [copy.deepcopy(art) for art in graph["lattice_artifacts"]
        if _node_matches_slice_dims(
        art, {str(art.get("kind") or "SCHEMA").strip().upper(), "ARTIFACT"},
        str(next((_v for _v in (art.get("scope_id"), art.get("scope")) if _v), None) or ""), dims)]
    active_tasks = [copy.deepcopy(t) for t in graph.get("active_tasks", [])
        if _node_matches_slice_dims(t, {"TASK", "ACTION"}, str(t.get("scope") or ""), dims)]
    matched_symbols = _scan_ast_symbols_on_disk(scopes, raw_query_low, component, active_artifacts, include_code)
    component_specs, active_flows, active_artifacts, active_rules = _merge_matched_symbol_context(
        graph, scopes, matched_symbols, (component_specs, active_flows, active_artifacts, active_rules), include_code)
    open_blockers = [copy.deepcopy(b) for b in graph.get("open_blockers", [])
        if b.get("status") != "RESOLVED"]
    all_blockers = list(conflicts) + [b for b in open_blockers if b.get("type") != "CONFLICT"]
    status = "CONFLICT" if conflicts else ("BLOCKED" if open_blockers else "OK")
    view: dict[str, Any] = {"status": status, "component_specs": component_specs, "active_flows": active_flows,
        "active_artifacts": active_artifacts, "active_rules": active_rules, "active_tasks": active_tasks,
        "matched_symbols": matched_symbols, "blockers": all_blockers,
        "conflicts": conflicts, "open_blockers": open_blockers}
    compact_str = format_compact_view(view, max_lines=max_lines, token_budget=token_budget)
    view.update({"compact_view": compact_str, "text": compact_str, "token_count": token_cost(compact_str),
        "line_count": len(compact_str.splitlines()) if compact_str else 0})
    eff_b = max(token_budget, 2400) if (include_code or matched_symbols) else token_budget
    return _enrich_view_compact_lines(graph, view, eff_budget=eff_b)
