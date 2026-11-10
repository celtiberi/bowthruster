"""Spec 22C §2.1: Task Readiness Classifier & Scan/Propagate/Expand Engine (`task_readiness.py`)."""

from __future__ import annotations

from typing import Any

from bowthruster.graph_core import (
    _build_rule_index,
    expand_graph_node,
    propagate_nodes,
    scan_nodes,
)
from bowthruster.whiteboard.frustration import detect_frustration
from .table_helpers import _enrich_view_compact_lines


def prohibition_blocks_task(
    rule_has_prop: bool,
    task_has_prop: bool,
    props_match: bool,
    mentions_prohibited_val: bool,
) -> bool:
    """Returns True iff a PROHIBITION rule blocks a task per Bend 2 Laws 9–10."""
    if not rule_has_prop:
        return True
    return bool((task_has_prop and props_match) or mentions_prohibited_val)


def classify_task_status(
    has_conflict: bool, has_prohibition: bool, has_unmet_dep: bool
) -> str:
    """Classifies task readiness per Bend 2 Law 7 (`task_ready_iff_all_clear`)."""
    if has_conflict:
        return "BLOCKED_BY_CONFLICT"
    if has_prohibition:
        return "BLOCKED_BY_RULE"
    return "WAITING_ON_DEPENDENCY" if has_unmet_dep else "READY"


def _rule_prohibits_task(r: dict[str, Any], task: dict[str, Any]) -> bool:
    """Evaluates whether rule `r` blocks `task` via `prohibition_blocks_task`."""
    if r.get("type") != "PROHIBITION" or r.get("exceptions"):
        return False
    r_prop, t_prop = r.get("target_prop"), task.get("target_prop")
    rule_has_prop = r_prop is not None and str(r_prop).strip() != ""
    task_has_prop = t_prop is not None and str(t_prop).strip() != ""
    props_match = bool(rule_has_prop and task_has_prop and r_prop == t_prop)
    if isinstance(r.get("value"), str) and r.get("value").strip():
        r_val_raw = r.get("value")
    elif isinstance(r.get("bound_val"), str):
        r_val_raw = r.get("bound_val")
    else:
        r_val_raw = ""
    r_val = str(r_val_raw or "").strip().lower()
    t_summary = str(
        next((_v for _v in (task.get("summary"), task.get("text")) if _v), None) or ""
    ).lower()
    raw_tbv = (
        task.get("bound_value")
        if task.get("bound_value") is not None
        else (task.get("bound_val") or "")
    )
    t_bval = str(raw_tbv).lower()
    mentions = bool(r_val and (r_val in t_summary or r_val in t_bval))
    return prohibition_blocks_task(rule_has_prop, task_has_prop, props_match, mentions)


def _evaluate_task_flags(
    graph: dict[str, Any], task: dict[str, Any], conflicts: list[dict[str, Any]]
) -> tuple[bool, bool, bool]:
    """Derives `(has_conflict, has_prohibition, has_unmet_dep)` for `task`."""
    t_scope = str(task.get("scope") or "global_defaults")
    gov_ids = {str(g) for g in task.get("governed_by", []) if g}
    rule_idx = _build_rule_index(graph) if isinstance(graph, dict) else {}
    scopes = graph.get("scopes", {}) if isinstance(graph, dict) else {}
    active_tasks = graph.get("active_tasks", []) if isinstance(graph, dict) else []
    open_blockers = [
        b
        for b in ((graph or {}).get("open_blockers", []) or [])
        if b.get("status") != "RESOLVED"
    ]
    all_conflicts = list(conflicts) + [
        b for b in open_blockers if b.get("type") == "CONFLICT"
    ]
    has_conflict = bool(task.get("has_conflict", False)) or any(
        (
            c.get("scope") == t_scope
            or (
                isinstance(c.get("scope"), str)
                and c["scope"]
                and (
                    t_scope.startswith(f"{c['scope']}::")
                    or c["scope"].startswith(f"{t_scope}::")
                )
            )
            or bool(set(c.get("rule_ids", [])) & gov_ids)
        )
        for c in all_conflicts
    )
    scope_rules = scopes.get(t_scope, {}).get("rules", []) if scopes.get(t_scope) else []
    cand_rules = [rule_idx[rid][1] for rid in gov_ids if rid in rule_idx] + list(scope_rules)
    has_prohibition = bool(task.get("has_prohibition", False)) or any(
        _rule_prohibits_task(r, task) for r in cand_rules
    )
    has_unmet_dep = bool(task.get("has_unmet_dep", False)) or any(
        b.get("type") in ("CLARIFICATION", "QUESTION") and b.get("scope") == t_scope
        for b in open_blockers
    )
    if not has_unmet_dep:
        for dep_id in (str(d).strip() for d in task.get("depends_on", []) if str(d).strip()):
            unmet_task = any(
                isinstance(ot, dict)
                and ot.get("id") != task.get("id")
                and (ot.get("id") == dep_id or ot.get("scope") == dep_id)
                and not ot.get("completed", False)
                for ot in active_tasks
            )
            unmet_blocker = any(
                b.get("id") == dep_id or b.get("scope") == dep_id for b in open_blockers
            )
            missing_dep = (
                dep_id not in scopes
                and dep_id not in rule_idx
                and f"fact_{dep_id}" not in rule_idx
            )
            if unmet_task or unmet_blocker or missing_dep:
                has_unmet_dep = True
                break
    return has_conflict, has_prohibition, has_unmet_dep


def annotate_active_tasks(
    graph: dict[str, Any],
    conflicts: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Annotates each task in `tasks` and `graph['active_tasks']` with its `status`."""
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


def scan_graph(
    graph: dict[str, Any],
    query: str = "",
    temperature: float | None = None,
    token_budget: int = 400,
    max_lines: int = 35,
    *,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Executes a 1-hop index scan over `graph` and annotates `active_tasks`."""
    _mark_file_scopes_as_components(graph)
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    view = scan_nodes(
        graph,
        query,
        token_budget=token_budget,
        max_lines=max_lines,
        max_tokens=max_tokens,
        temperature=temperature,
        conflict_detector=detect_frustration,
    )
    annotate_active_tasks(graph, view.get("conflicts", []), view.get("active_tasks", []))
    return _enrich_view_compact_lines(graph, view, eff_budget=eff_budget)


def propagate_graph(
    graph: dict[str, Any],
    query: str,
    temperature: float | None = None,
    token_budget: int = 400,
    max_lines: int = 35,
    gamma: float = 0.75,
    max_hops: int = 4,
    *,
    max_steps: int | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Executes 5-family multi-hop propagation and annotates `active_tasks`."""
    _mark_file_scopes_as_components(graph)
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    view = propagate_nodes(
        graph,
        query,
        temperature=temperature,
        token_budget=token_budget,
        max_lines=max_lines,
        gamma=gamma,
        max_hops=max_hops,
        max_steps=max_steps,
        max_tokens=max_tokens,
        conflict_detector=detect_frustration,
    )
    annotate_active_tasks(graph, view.get("conflicts", []), view.get("active_tasks", []))
    return _enrich_view_compact_lines(graph, view, eff_budget=eff_budget)


def expand_node(
    graph: dict[str, Any],
    node_id: str,
    query: str = "",
    token_budget: int = 400,
    max_lines: int = 35,
) -> dict[str, Any]:
    """Expands a single scope or rule `node_id` and annotates `active_tasks`."""
    _mark_file_scopes_as_components(graph)
    view = expand_graph_node(
        graph,
        node_id,
        query=query,
        token_budget=token_budget,
        max_lines=max_lines,
        conflict_detector=detect_frustration,
    )
    annotate_active_tasks(graph, view.get("conflicts", []), view.get("active_tasks", []))
    return _enrich_view_compact_lines(graph, view, eff_budget=token_budget)
