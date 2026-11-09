"""Spec 22C §2.1: Whiteboard Read Engine & Bend 2-Verified Task Readiness (`read_engine.py`)."""

from __future__ import annotations

import ast, copy, os
from pathlib import Path
from typing import Any

from lattice.graph_core import (
    _build_rule_index, expand_graph_node, format_compact_view, matches_nd_slice,
    propagate_nodes, scan_nodes, token_cost, tokenize_content_words,
)
from lattice.whiteboard.frustration import detect_frustration


class ContextView(dict[str, Any]):
    """Sliced nD Lattice context view (`status`, `component_specs`, `active_flows`, `compact_view`)."""


def _norm_files_list(raw_files: Any) -> list[str]:
    if isinstance(raw_files, str): return [x.strip() for x in raw_files.replace(";", ",").split(",") if x.strip() and x.strip().lower() not in ("none", "-", "—", "n/a", "null")]
    return [str(x).strip() for x in raw_files if str(x).strip()] if isinstance(raw_files, list) else []


def _insert_tag_into_line(ln: str, extra_tag: str) -> str:
    if (b_end := ln.find("]")) < 0: return ln
    if ln[b_end + 1 : b_end + 3] == " (":
        depth = 0
        for idx in range(b_end + 2, len(ln)):
            depth += (1 if ln[idx] == "(" else (-1 if ln[idx] == ")" else 0))
            if depth == 0: return f"{ln[:idx]}, {extra_tag}{ln[idx:]}"
    return f"{ln[: b_end + 1]} ({extra_tag}){ln[b_end + 1 :]}"


def _enrich_view_compact_lines(graph: dict[str, Any], view: dict[str, Any], *, eff_budget: int = 400) -> dict[str, Any]:
    scopes = graph.get("scopes", {}) if isinstance(graph, dict) and isinstance(graph.get("scopes"), dict) else {}
    comp_files: dict[str, list[str]] = {}
    for comp in view.get("component_specs", []):
        if isinstance(comp, dict):
            cid = str(comp.get("id") or comp.get("scope_id") or comp.get("scope") or "")
            sc = scopes.get(cid, {}) if isinstance(scopes.get(cid), dict) else {}
            if flist := _norm_files_list(comp.get("files") or sc.get("files") or (sc.get("attributes") or {}).get("files")):
                comp["files"] = comp_files[cid] = flist
    iface_by_name, law_by_name = {}, {}
    for art in list(graph.get("lattice_artifacts", []) if isinstance(graph, dict) else []) + list(view.get("active_artifacts", [])):
        if isinstance(art, dict) and (aname := str(art.get("name") or art.get("id") or "").lower()):
            akind = str(art.get("kind") or "").upper()
            if akind in ("INTERFACE", "SCHEMA"): iface_by_name[aname] = art
            elif akind == "LAW": law_by_name[aname] = art
    raw_txt = str(view.get("compact_view") or view.get("text") or "")
    if not raw_txt and not view.get("matched_symbols"): return view
    out_lines: list[str] = []
    for ln in raw_txt.splitlines():
        cand_ln = ln
        if ln.startswith("@ COMPONENT [") and "]" in ln:
            cid = ln[len("@ COMPONENT [") : ln.find("]")]
            if (flist := comp_files.get(cid) or _norm_files_list((scopes.get(cid) or {}).get("files"))) and "files=[" not in ln:
                cand_ln = _insert_tag_into_line(ln, f"files=[{', '.join(flist)}]")
        elif ln.startswith(("$ INTERFACE [", "# SCHEMA [")) and "]" in ln:
            art = iface_by_name.get(ln[ln.find("[") + 1 : ln.find("]")].lower())
            if (fval := str((art or {}).get("file") or ((art or {}).get("dimensions") or {}).get("file") or "").strip()) and "file=" not in ln:
                cand_ln = _insert_tag_into_line(ln, f"file={fval}")
            if isinstance(calls := (art or {}).get("calls"), list) and calls and "calls=[" not in cand_ln:
                cand_ln = _insert_tag_into_line(cand_ln, f"calls=[{', '.join(str(x) for x in calls[:6])}]")
        elif ln.startswith("% LAW [") and "]" in ln and (art := law_by_name.get(ln[len("% LAW [") : ln.find("]")].lower())):
            dims = art.get("dimensions") if isinstance(art.get("dimensions"), dict) else {}
            if ltags := [f"{k}={v}" for k in ("bend_dir", "args", "lhs", "rhs") if (v := str(art.get(k) or dims.get(k) or "").strip()) and f"{k}=" not in ln]:
                cand_ln = _insert_tag_into_line(ln, " ".join(ltags))
        out_lines.append(cand_ln)
    for m in (view.get("matched_symbols") or []):
        if isinstance(m, dict) and m.get("symbol"):
            sym_ln = f"@ AST_SYMBOL [{m['symbol']}] (scope={m.get('scope')}, file={m.get('file')})"
            if sym_ln not in out_lines: out_lines.append(sym_ln)
            if m.get("code"): out_lines.append(f"[CODE {m.get('file')}] ({m['symbol']})\n{m['code']}")
    enriched_txt = "\n".join(out_lines)
    if eff_budget >= 400 or token_cost(enriched_txt) <= eff_budget:
        view.update({"compact_view": enriched_txt, "text": enriched_txt, "compact_lines": out_lines, "token_count": token_cost(enriched_txt), "line_count": len(out_lines)})
    else: view["compact_lines"] = raw_txt.splitlines()
    return view


def prohibition_blocks_task(
    rule_has_prop: bool, task_has_prop: bool, props_match: bool, mentions_prohibited_val: bool,
) -> bool:
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
    r_val_raw = r.get("value") if isinstance(r.get("value"), str) and r.get("value").strip() else (r.get("bound_val") if isinstance(r.get("bound_val"), str) else "")
    r_val = str(r_val_raw or "").strip().lower()
    t_summary = str(task.get("summary") or task.get("text") or "").lower()
    t_bval = str(task.get("bound_value") if task.get("bound_value") is not None else (task.get("bound_val") or "")).lower()
    return prohibition_blocks_task(rule_has_prop, task_has_prop, props_match, bool(r_val and (r_val in t_summary or r_val in t_bval)))


def _evaluate_task_flags(
    graph: dict[str, Any], task: dict[str, Any], conflicts: list[dict[str, Any]],
) -> tuple[bool, bool, bool]:
    """Derives `(has_conflict, has_prohibition, has_unmet_dep)` for `task` against `graph` and `conflicts`."""
    t_scope, gov_ids = str(task.get("scope") or "global_defaults"), {str(g) for g in task.get("governed_by", []) if g}
    rule_idx, scopes = (_build_rule_index(graph) if isinstance(graph, dict) else {}), (graph.get("scopes", {}) if isinstance(graph, dict) else {})
    active_tasks = graph.get("active_tasks", []) if isinstance(graph, dict) else []
    open_blockers = [b for b in (graph.get("open_blockers", []) if isinstance(graph, dict) else []) if isinstance(b, dict) and b.get("status") != "RESOLVED"]
    all_conflicts = list(conflicts) + [b for b in open_blockers if b.get("type") == "CONFLICT"]
    has_conflict = bool(task.get("has_conflict", False)) or any(isinstance(c, dict) and (c.get("scope") == t_scope or (isinstance(c.get("scope"), str) and c["scope"] and (t_scope.startswith(f"{c['scope']}::") or c["scope"].startswith(f"{t_scope}::"))) or bool(set(c.get("rule_ids", [])) & gov_ids)) for c in all_conflicts)
    scope_rules = scopes.get(t_scope, {}).get("rules", []) if isinstance(scopes.get(t_scope), dict) else []
    cand_rules = [rule_idx[rid][1] for rid in gov_ids if rid in rule_idx] + [r for r in scope_rules if isinstance(r, dict)]
    has_prohibition = bool(task.get("has_prohibition", False)) or any(isinstance(r, dict) and _rule_prohibits_task(r, task) for r in cand_rules)
    has_unmet_dep = bool(task.get("has_unmet_dep", False)) or any(b.get("type") in ("CLARIFICATION", "QUESTION") and b.get("scope") == t_scope for b in open_blockers)
    if not has_unmet_dep:
        for dep_id in (str(d).strip() for d in task.get("depends_on", []) if str(d).strip()):
            if any(isinstance(ot, dict) and ot.get("id") != task.get("id") and (ot.get("id") == dep_id or ot.get("scope") == dep_id) and not ot.get("completed", False) for ot in active_tasks) or any(b.get("id") == dep_id or b.get("scope") == dep_id for b in open_blockers) or (dep_id not in scopes and dep_id not in rule_idx and f"fact_{dep_id}" not in rule_idx):
                has_unmet_dep = True; break

    return has_conflict, has_prohibition, has_unmet_dep


def annotate_active_tasks(
    graph: dict[str, Any],
    conflicts: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Annotates each task in `tasks` (and `graph['active_tasks']`) with its `status` via `classify_task_status`."""
    if not isinstance(graph, dict):
        return []
    eff_conflicts = conflicts if conflicts is not None else detect_frustration(graph)
    for t in graph.get("active_tasks", []):
        if isinstance(t, dict):
            hc, hp, hd = _evaluate_task_flags(graph, t, eff_conflicts)
            t["status"] = classify_task_status(hc, hp, hd)
    if tasks is not None:
        for t in tasks:
            if isinstance(t, dict):
                hc, hp, hd = _evaluate_task_flags(graph, t, eff_conflicts)
                t["status"] = classify_task_status(hc, hp, hd)
        return tasks
    return list(graph.get("active_tasks", []))


def _mark_file_scopes_as_components(graph: dict[str, Any]) -> None:
    if isinstance(graph, dict) and isinstance(graph.get("scopes"), dict):
        for sc in graph["scopes"].values():
            if isinstance(sc, dict) and sc.get("files") and not sc.get("kind"):
                sc["kind"] = "COMPONENT"


def scan_graph(
    graph: dict[str, Any], query: str = "", temperature: float | None = None,
    token_budget: int = 400, max_lines: int = 35, *, max_tokens: int | None = None,
) -> dict[str, Any]:
    """Executes a 1-hop index scan over `graph` via `scan_nodes(..., conflict_detector=detect_frustration)`
    and annotates `active_tasks` via `classify_task_status` (Spec 22C §2.1)."""
    _mark_file_scopes_as_components(graph)
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    view = scan_nodes(
        graph, query, token_budget=token_budget, max_lines=max_lines,
        max_tokens=max_tokens, temperature=temperature, conflict_detector=detect_frustration,
    )
    annotate_active_tasks(graph, view.get("conflicts", []), view.get("active_tasks", []))
    return _enrich_view_compact_lines(graph, view, eff_budget=eff_budget)


def propagate_graph(
    graph: dict[str, Any], query: str, temperature: float | None = None,
    token_budget: int = 400, max_lines: int = 35, gamma: float = 0.75, max_hops: int = 4,
    *, max_steps: int | None = None, max_tokens: int | None = None,
) -> dict[str, Any]:
    """Executes 5-family multi-hop propagation via `propagate_nodes(..., conflict_detector=detect_frustration)`
    and annotates `active_tasks` via `classify_task_status` (Spec 22C §2.1)."""
    _mark_file_scopes_as_components(graph)
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    view = propagate_nodes(
        graph, query, temperature=temperature, token_budget=token_budget, max_lines=max_lines,
        gamma=gamma, max_hops=max_hops, max_steps=max_steps, max_tokens=max_tokens,
        conflict_detector=detect_frustration,
    )
    annotate_active_tasks(graph, view.get("conflicts", []), view.get("active_tasks", []))
    return _enrich_view_compact_lines(graph, view, eff_budget=eff_budget)


def expand_node(
    graph: dict[str, Any], node_id: str, query: str = "", token_budget: int = 400, max_lines: int = 35,
) -> dict[str, Any]:
    """Expands a single scope or rule `node_id` via `expand_graph_node(..., conflict_detector=detect_frustration)`
    and annotates `active_tasks` via `classify_task_status` (Spec 22C §2.1)."""
    _mark_file_scopes_as_components(graph)
    view = expand_graph_node(
        graph, node_id, query=query, token_budget=token_budget,
        max_lines=max_lines, conflict_detector=detect_frustration,
    )
    annotate_active_tasks(graph, view.get("conflicts", []), view.get("active_tasks", []))
    return _enrich_view_compact_lines(graph, view, eff_budget=token_budget)


def _scope_chain_values(scopes: dict[str, Any], start_sid: str | None, field_name: str) -> list[str]:
    """Collects lowercase values for `field_name` along `start_sid` and its ancestor scopes."""
    out: list[str] = []
    cur = str(start_sid or "").strip()
    visited: set[str] = set()
    for _ in range(8):
        if not cur or cur in visited or not isinstance(scopes.get(cur), dict):
            break
        visited.add(cur)
        sc = scopes[cur]
        for cand in (sc.get(field_name), (sc.get("attributes") or {}).get(field_name) if isinstance(sc.get("attributes"), dict) else None):
            if cand is not None and str(cand).strip():
                out.append(str(cand).strip().lower())
        cur = str(sc.get("parent") or "").strip()
    return out


def _node_matches_slice_dims(
    node: dict[str, Any],
    node_kinds: set[str],
    scopes: dict[str, Any],
    alias_idx: dict[str, str],
    *,
    sid: str | None,
    component: str | None,
    division: str | None,
    stage: str | None,
    schema: str | None,
    kind: str | None,
    q_tokens: set[str],
    raw_query_low: str,
) -> bool:
    """Evaluates whether `node` satisfies all 5 nD coordinates via `matches_nd_slice` plus `query` overlap (Spec 22C §2.1)."""
    scope_obj = scopes.get(sid, {}) if (sid and isinstance(scopes.get(sid), dict)) else {}

    comp_unc = not bool(component and str(component).strip())
    comp_ov = False
    if not comp_unc:
        c_low = str(component).strip().lower()
        c_res = str(alias_idx.get(c_low, c_low)).lower()
        comp_cands = [str(node[k]).strip().lower() for k in ("scope", "scope_id", "id", "parent", "source", "target") if node.get(k)]
        for list_k in ("depends_on", "children", "refs", "aliases"):
            comp_cands.extend(str(item).strip().lower() for item in (node.get(list_k) or []) if str(item).strip())
        if isinstance(scope_obj, dict) and scope_obj:
            comp_cands.extend(str(scope_obj[k]).strip().lower() for k in ("id", "parent") if scope_obj.get(k))
            comp_cands.extend(str(al).strip().lower() for al in (scope_obj.get("aliases") or []) if str(al).strip())
        comp_ov = any(v in (c_low, c_res) or v.startswith(f"{c_low}::") or v.startswith(f"{c_res}::") for v in comp_cands)
    if not matches_nd_slice(comp_unc, comp_ov):
        return False

    div_unc = not bool(division and str(division).strip())
    div_ov = False
    if not div_unc:
        d_low = str(division).strip().lower()
        dims_d = node.get("dimensions") if isinstance(node.get("dimensions"), dict) else {}
        attrs_d = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
        div_cands = [
            str(v).strip().lower()
            for v in (node.get("owner"), node.get("division"), dims_d.get("owner"), dims_d.get("division"), attrs_d.get("owner"), attrs_d.get("division"))
            if v is not None and str(v).strip()
        ]
        for chain_sid in (sid, node.get("source"), node.get("target")):
            div_cands.extend(_scope_chain_values(scopes, chain_sid, "owner"))
            div_cands.extend(_scope_chain_values(scopes, chain_sid, "division"))
        div_ov = any(d_low == v or d_low in v for v in div_cands)
    if not matches_nd_slice(div_unc, div_ov):
        return False

    stg_unc = not bool(stage and str(stage).strip())
    stg_ov = False
    if not stg_unc:
        s_low = str(stage).strip().lower()
        dims_s = node.get("dimensions") if isinstance(node.get("dimensions"), dict) else {}
        attrs_s = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
        stg_cands = [
            str(v).strip().lower()
            for v in (node.get("stage"), dims_s.get("stage"), attrs_s.get("stage"))
            if v is not None and str(v).strip()
        ]
        for chain_sid in (sid, node.get("source"), node.get("target")):
            stg_cands.extend(_scope_chain_values(scopes, chain_sid, "stage"))
        stg_ov = any(s_low == v or s_low in v for v in stg_cands)
    if not matches_nd_slice(stg_unc, stg_ov):
        return False

    sch_unc = not bool(schema and str(schema).strip())
    sch_ov = False
    if not sch_unc:
        sch_low = str(schema).strip().lower()
        sch_cands = [
            str(v).strip().lower()
            for v in (node.get("payload"), node.get("name"), node.get("target_prop"))
            if v is not None and str(v).strip()
        ]
        for list_k in ("inputs", "outputs"):
            for item in node.get(list_k) or []:
                if str(item).strip():
                    sch_cands.append(str(item).strip().lower())
            if isinstance(scope_obj, dict):
                for item in scope_obj.get(list_k) or []:
                    if str(item).strip():
                        sch_cands.append(str(item).strip().lower())
        sch_ov = any(sch_low == v or sch_low in v for v in sch_cands)
    if not matches_nd_slice(sch_unc, sch_ov):
        return False

    knd_unc = not bool(kind and str(kind).strip())
    knd_ov = (str(kind).strip().upper() in node_kinds) if not knd_unc else False
    if not matches_nd_slice(knd_unc, knd_ov):
        return False

    if raw_query_low:
        blob = " ".join(
            str(v)
            for v in (
                node.get("id"), node.get("name"), node.get("scope"), node.get("scope_id"),
                node.get("source"), node.get("target"), node.get("payload"), node.get("role"), node.get("why"),
                (node.get("attributes") if isinstance(node.get("attributes"), dict) else {}).get("why"),
                node.get("spec"), node.get("text"), node.get("target_prop"), node.get("owner"), node.get("stage"),
                node.get("files"), node.get("file"), node.get("bend_dir"), node.get("args"), node.get("lhs"), node.get("rhs"),
            )
            if v is not None
        )
        node_toks = set(tokenize_content_words(blob))
        if not (raw_query_low in blob.lower() or (q_tokens and bool(q_tokens & node_toks))):
            return False

    return True


def _format_code_window(src_lines: list[str], s: int, e: int, q_sub: list[str]) -> str:
    for tok in q_sub:
        if "-" in tok and all(p.lstrip("l").isdigit() for p in tok.split("-", 1)):
            ws, we = (max(1, min(len(src_lines), int(p.lstrip("l")))) for p in tok.split("-", 1))
            return f"[WINDOW #L{ws}-L{we}]\n" + "\n".join(src_lines[ws - 1 : min(we, ws + 29)])
    if q_sub and (hit := next((i + 1 for i in range(max(0, s - 1), min(len(src_lines), e)) if any(k in src_lines[i].lower() for k in q_sub)), None)):
        ws, we = max(s, hit - 2), min(e, max(s, hit - 2) + 25)
        return f"[WINDOW #L{ws}-L{we}]\n" + "\n".join(src_lines[ws - 1 : we])
    full_blk = "\n".join(src_lines[max(0, s - 1) : e])
    if e - s + 1 <= 40 and len(full_blk) <= 1650: return full_blk
    head = src_lines[max(0, s - 1) : s + 11]
    lms = [f"#   L{i}: {st[:76]}" for i in range(s + 12, min(len(src_lines), e) + 1) if (st := src_lines[i - 1].strip()).startswith(("if ", "elif ", "else:", "for ", "while ", "try:", "except", "def ", "class "))][:16]
    return "\n".join([*head, f"# ... [LANDMARKS #L{s + 12}-L{e} — pass query='<symbol> <keyword>' or '<symbol> <start>-<end>' for exact window]:", *lms])


_AST_SPAN_CACHE: dict[str, tuple[int, list[tuple[str, int, int]], list[str]]] = {}
def _scan_ast_symbols_on_disk(scopes: dict[str, Any], raw_query_low: str, component: str | None, active_artifacts: list[dict[str, Any]], include_code: bool) -> list[dict[str, Any]]:
    if not raw_query_low and not include_code: return []
    lat_home = Path(__file__).resolve().parents[2]
    root = Path(os.environ["LATTICE_PROJECT_ROOT"]).resolve() if os.environ.get("LATTICE_PROJECT_ROOT") else lat_home
    res_fp = lambda cp: next((c for c in (root / cp, lat_home / cp, lat_home.parent / cp) if c.is_file()), root / cp)
    matched, seen = [], set()
    if raw_query_low:
        q_parts = [p for p in raw_query_low.replace("#l", " ").replace(":", " ").split() if p]
        q_clean, q_sub = (q_parts[0] if q_parts else raw_query_low.strip()), q_parts[1:]
        for tok_filter in ([q_clean], ([t for t in q_clean.split("_") if len(t) >= 3] if "_" in q_clean else [])):
            if not tok_filter or matched: break
            for sid, sc in scopes.items():
                if not isinstance(sc, dict) or (component and sid.lower() != str(component).strip().lower()): continue
                for f_raw in _norm_files_list(sc.get("files") or (sc.get("attributes") or {}).get("files")):
                    cp = f_raw.removeprefix("uri://file/").removeprefix("file://").removeprefix("./").split("#", 1)[0].split(":", 1)[0].strip()
                    if (fp := res_fp(cp)).is_file():
                        if _AST_SPAN_CACHE.get(ckey := str(fp), (0, [], []))[0] == (mt := fp.stat().st_mtime_ns): _, sym_spans, src_lines = _AST_SPAN_CACHE[ckey]
                        else:
                            src_low = (src := fp.read_text(encoding="utf-8", errors="ignore")).lower()
                            if not (any(("def " in ln or "class " in ln) and all(t in f"{cp.lower()} {ln}" for t in tok_filter) for ln in src_low.splitlines()) if cp.endswith(".py") else all(t in f"{cp.lower()}\n{src_low}" for t in tok_filter)): continue
                            src_lines, sym_spans = src.splitlines(), []
                            if cp.endswith(".py"):
                                try: sym_spans = [(n.name, int(n.lineno), int(getattr(n, "end_lineno", None) or n.lineno)) for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and getattr(n, "lineno", 0) > 0]
                                except SyntaxError: pass
                            else:
                                from lattice.nlp import _extract_generic_source_symbols
                                sym_spans = [(nm, s, e) for nm, (s, e) in _extract_generic_source_symbols(fp)[0].items()]
                            _AST_SPAN_CACHE[ckey] = (mt, sym_spans, src_lines)
                        for sym_name, s, e in sym_spans:
                            nl = sym_name.lower()
                            if (cp, sym_name) not in seen and ((nl == q_clean or (len(q_clean) >= 4 and q_clean in nl)) if len(tok_filter) == 1 else all(t in f"{cp.lower()} {nl}" for t in tok_filter)):
                                seen.add((cp, sym_name))
                                matched.append({"symbol": sym_name, "scope": sid, "file": f"{cp}#L{s}-L{e}", "start_line": s, "end_line": e, "exact": nl == q_clean or len(tok_filter) > 1, **({"code": _format_code_window(src_lines, s, e, q_sub)} if include_code else {})})
                                if len(tok_filter) > 1: break
                if len(tok_filter) > 1 and matched: break
        if any(m.get("exact") for m in matched):
            matched = [m for m in matched if m.get("exact")]
    if include_code and not matched:
        for art in active_artifacts[:3]:
            if isinstance(art, dict) and (af := str(art.get("file") or "").strip()) and "#L" in af:
                cp, rng = af.split("#L", 1)
                if "-" in rng and all(p.lstrip("Ll").isdigit() for p in rng.split("-", 1)) and (fp := res_fp(cp)).is_file():
                    s, e = (int(p.lstrip("Ll")) for p in rng.split("-", 1))
                    sym = str(art.get("name") or art.get("id") or "")
                    if (cp, sym) not in seen:
                        seen.add((cp, sym))
                        matched.append({"symbol": sym, "scope": str(art.get("scope_id") or art.get("scope") or ""), "file": af, "start_line": s, "end_line": e, "code": _format_code_window(fp.read_text(encoding="utf-8", errors="ignore").splitlines(), s, e, [])})
    return matched[:3]


def slice_lattice(
    graph: dict[str, Any], query: str = "", *, component: str | None = None, scope: str | None = None,
    division: str | None = None, stage: str | None = None, schema: str | None = None,
    kind: str | None = None, token_budget: int = 400, max_lines: int = 35, include_code: bool = False,
) -> dict[str, Any]:
    """Computes an nD Hyperplane Slice over `graph` governed by Spec 21 Law 10 (`matches_nd_slice`, Spec 22C §2.1)."""
    if not isinstance(graph, dict):
        raise ValueError("graph must be a dict")
    if component is None and scope is not None:
        component = scope

    conflicts = detect_frustration(graph)
    annotate_active_tasks(graph, conflicts)
    scopes = graph.get("scopes", {}) if isinstance(graph.get("scopes"), dict) else {}
    if component and component not in scopes:
        component = next((s for s in scopes if s != "global_defaults" and (s.lower() in component.lower() or component.lower() in s.lower())), None if query else component)
    alias_idx = graph.get("alias_index", {}) if isinstance(graph.get("alias_index"), dict) else {}
    raw_query_low = str(query or "").strip().lower()
    q_tokens = set(tokenize_content_words(query)) if raw_query_low else set()
    dim_kw = {
        "component": component, "division": division, "stage": stage,
        "schema": schema, "kind": kind, "q_tokens": q_tokens, "raw_query_low": raw_query_low,
    }

    component_specs: list[dict[str, Any]] = []
    active_rules: list[dict[str, Any]] = []
    for sid, sc in scopes.items():
        if not isinstance(sc, dict):
            continue
        is_sbf_comp = (
            str(sc.get("kind") or "").strip().upper() == "COMPONENT"
            or bool(
                sc.get("role") or sc.get("owner") or sc.get("stage") or sc.get("depends_on")
                or sc.get("children") or sc.get("parent") or sc.get("inputs") or sc.get("outputs") or sc.get("files")
            )
        )
        if sid != "global_defaults" or is_sbf_comp or len(scopes) == 1:
            sc_kinds = {"COMPONENT", "SCOPE"} if is_sbf_comp else {"SCOPE"}
            if sc.get("kind"):
                sc_kinds.add(str(sc["kind"]).strip().upper())
            if _node_matches_slice_dims(sc, sc_kinds, scopes, alias_idx, sid=sid, **dim_kw):
                component_specs.append(copy.deepcopy(sc))
        for r in sc.get("rules", []):
            if isinstance(r, dict):
                rtype = str(r.get("type") or "OBLIGATION").strip().upper()
                r_kinds = {rtype, "FACT"} if rtype in ("FACT", "GOAL", "PROPOSAL") else {rtype, "CONSTRAINT", "RULE"}
                if _node_matches_slice_dims(r, r_kinds, scopes, alias_idx, sid=str(r.get("scope") or sid), **dim_kw):
                    active_rules.append(copy.deepcopy(r))

    raw_flows = [copy.deepcopy(fl) for fl in graph.get("flows", []) if isinstance(fl, dict) and _node_matches_slice_dims(fl, {"FLOW", str(fl.get("kind") or "FLOW").strip().upper()}, scopes, alias_idx, sid=str(fl.get("source") or ""), **dim_kw)]
    active_flows = list({(str(fl.get("source") or ""), str(fl.get("target") or ""), str(fl.get("payload") or fl.get("condition") or "")): fl for fl in raw_flows}.values())
    active_artifacts = [
        copy.deepcopy(art) for art in graph.get("lattice_artifacts", [])
        if isinstance(art, dict) and _node_matches_slice_dims(art, {str(art.get("kind") or "SCHEMA").strip().upper(), "ARTIFACT"}, scopes, alias_idx, sid=str(art.get("scope_id") or art.get("scope") or ""), **dim_kw)
    ]
    active_tasks = [
        copy.deepcopy(t) for t in graph.get("active_tasks", [])
        if isinstance(t, dict) and _node_matches_slice_dims(t, {"TASK", "ACTION"}, scopes, alias_idx, sid=str(t.get("scope") or ""), **dim_kw)
    ]
    matched_symbols = _scan_ast_symbols_on_disk(scopes, raw_query_low, component, active_artifacts, include_code)
    if any(m.get("exact") for m in matched_symbols):
        exact_sids = {str(m.get("scope") or "") for m in matched_symbols if m.get("exact")}
        exact_syms = {str(m.get("symbol") or "").lower() for m in matched_symbols if m.get("exact")}
        component_specs = [copy.deepcopy(scopes[s]) for s in exact_sids if s in scopes]
        active_flows = [copy.deepcopy(fl) for fl in graph.get("flows", []) if isinstance(fl, dict) and (fl.get("source") in exact_sids or fl.get("target") in exact_sids)]
        sc_arts = [copy.deepcopy(a) for a in graph.get("lattice_artifacts", []) if isinstance(a, dict) and str(a.get("scope_id") or a.get("scope") or "") in exact_sids]
        active_artifacts = (([a for a in sc_arts if str(a.get("name") or a.get("id") or "").lower() in exact_syms] or [a for a in sc_arts if any(any(es in str(c).lower() for es in exact_syms) for c in (a.get("calls") or []))])[:2] or sc_arts[:2]) if include_code else sc_arts
        active_rules = [r for r in active_rules if str(r.get("scope") or "") in exact_sids]
    else:
        comp_ids = {str(c.get("id") or "") for c in component_specs if isinstance(c, dict)}
        for m in matched_symbols:
            if (msid := str(m.get("scope") or "")) in scopes and msid not in comp_ids:
                comp_ids.add(msid)
                component_specs.append(copy.deepcopy(scopes[msid]))
                active_flows.extend(copy.deepcopy(fl) for fl in graph.get("flows", []) if isinstance(fl, dict) and msid in (fl.get("source"), fl.get("target")) and fl not in active_flows)
                active_artifacts.extend(copy.deepcopy(a) for a in graph.get("lattice_artifacts", []) if isinstance(a, dict) and str(a.get("scope_id") or a.get("scope") or "") == msid and a not in active_artifacts)
    open_blockers = [copy.deepcopy(b) for b in graph.get("open_blockers", []) if isinstance(b, dict) and b.get("status") != "RESOLVED"]
    all_blockers = list(conflicts) + [b for b in open_blockers if b.get("type") != "CONFLICT"]

    status = "CONFLICT" if conflicts else ("BLOCKED" if open_blockers else "OK")
    view: dict[str, Any] = {"status": status, "component_specs": component_specs, "active_flows": active_flows, "active_artifacts": active_artifacts, "active_rules": active_rules, "active_tasks": active_tasks, "matched_symbols": matched_symbols, "blockers": all_blockers, "conflicts": conflicts, "open_blockers": open_blockers}
    compact_str = format_compact_view(view, max_lines=max_lines, token_budget=token_budget)
    view.update({"compact_view": compact_str, "text": compact_str, "token_count": token_cost(compact_str), "line_count": len(compact_str.splitlines()) if compact_str else 0})
    return _enrich_view_compact_lines(graph, view, eff_budget=max(token_budget, 2400) if (include_code or matched_symbols) else token_budget)
