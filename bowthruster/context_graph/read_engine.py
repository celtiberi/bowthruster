"""Spec 20 §1.4 & §8: 5-Family Multi-Hop Message Passing (`propagate_graph`), Index Scanner (`scan_graph`), and Node Expander (`expand_node`)."""

from __future__ import annotations

import copy
from typing import Any

from .activation import (
    _build_rule_index,
    _tau_min_for_temperature,
    compute_seed_activations,
    compute_tau,
    infer_query_temperature,
)
from .formatter import (
    _finalize_view,
    _pack_active_rules_and_lod2,
    format_compact_view,
    token_cost,
    tokenize_content_words,
)
from .frustration import detect_frustration
from .propagation_state import (
    PropagationPhase,
    PropagationStateMachine,
    _build_five_family_edges,
    _build_scope_headers,
    _unresolved_energy,
)


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
    """Executes multi-hop message passing across all 5 edge families to $E^{(t)} = 0$ or $\\Delta E^{(t)} = 0$,
    packing active nodes in single-pass `(-A, id)` order and failing closed on `CONFLICT` or `0` seed matches (Spec 20 §1.4)."""
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    eff_hops = int(max_steps if max_steps is not None else min(3, max_hops))

    sm = PropagationStateMachine.seed(graph, query, temperature=temperature, gamma=gamma, max_hops=eff_hops)
    if sm.phase == PropagationPhase.NO_SEED_MATCH:
        return {
            "status": sm.status,
            "temperature": sm.temperature,
            "tau": sm.tau,
            "hops": 0,
            "steps_taken": 0,
            "converged": True,
            "unresolved_energy": 0.0,
            "energy_trace": list(sm.energy_trace),
            "activations": {},
            "active_scopes": [],
            "active_rules": [],
            "active_tasks": [],
            "lod2_meta": {},
            "conflicts": [],
            "open_blockers": list(graph.get("open_blockers", [])),
            "text": "[FAIL_CLOSED: NO_SEED_MATCH]",
            "token_count": 1,
            "line_count": 1,
        }

    sm.run()
    act, tau = sm.activations, sm.tau
    scopes = graph.get("scopes", {})
    active_scopes = sorted([sid for sid in scopes if act.get(sid, 0.0) >= tau], key=lambda s: (-act.get(s, 0.0), s))
    rule_idx = _build_rule_index(graph)
    candidate_rids = sorted(
        [rid for rid, (sid, _) in rule_idx.items() if act.get(rid, 0.0) >= tau or sid in active_scopes],
        key=lambda rid: (-act.get(rid, 0.0), rid),
    )

    conflicts = detect_frustration(graph, set(candidate_rids) if candidate_rids else None)
    for b in graph.get("open_blockers", []):
        if isinstance(b, dict) and b.get("type") == "CONFLICT":
            if b.get("scope") in active_scopes or any(r in candidate_rids for r in b.get("rule_ids", [])):
                if not any(c.get("id") == b.get("id") for c in conflicts):
                    conflicts.append(copy.deepcopy(b))
    sm.resolve_conflicts(conflicts)

    q_tokens = set(tokenize_content_words(query))
    packed_rules, lod2_meta = _pack_active_rules_and_lod2(
        scopes, rule_idx, candidate_rids, act, q_tokens, conflicts, eff_budget, max_lines
    )

    matched_tasks: list[dict[str, Any]] = []
    for t in graph.get("active_tasks", []):
        if not isinstance(t, dict) or not t.get("id"):
            continue
        tid = str(t["id"])
        if act.get(tid, 0.0) >= tau or t.get("scope") in active_scopes or any(d in active_scopes for d in t.get("depends_on", [])):
            t_copy = copy.deepcopy(t)
            t_copy["activation"] = round(act.get(tid, 0.0), 6)
            matched_tasks.append(t_copy)
    matched_tasks.sort(key=lambda t: (-act.get(str(t.get("id", "")), 0.0), str(t.get("id", ""))))

    view: dict[str, Any] = {
        "status": sm.status,
        "temperature": sm.temperature,
        "tau": sm.tau,
        "hops": sm.hops_taken,
        "steps_taken": sm.hops_taken,
        "converged": sm.converged,
        "unresolved_energy": sm.energy_trace[-1],
        "energy_trace": list(sm.energy_trace),
        "activations": dict(act),
        "active_scopes": active_scopes,
        "active_rules": packed_rules,
        "active_tasks": matched_tasks,
        "lod2_meta": lod2_meta,
        "conflicts": conflicts,
        "open_blockers": list(graph.get("open_blockers", [])),
    }
    return _finalize_view(
        view,
        max_lines=max_lines,
        token_budget=eff_budget,
        scope_headers=_build_scope_headers(scopes, active_scopes, q_tokens),
    )


def scan_graph(
    graph: dict[str, Any],
    query: str = "",
    token_budget: int = 400,
    max_lines: int = 35,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Executes a 1-hop (`L_max = 1`) index scan over `graph` within `token_budget` and `max_lines` (Spec 20 §1.4)."""
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    if isinstance(query, str) and query.strip():
        return propagate_graph(
            graph, query, temperature=temperature, token_budget=eff_budget, max_lines=max_lines, max_hops=1, max_steps=1
        )

    scopes = graph.get("scopes", {}) if isinstance(graph, dict) else {}
    all_rules = [
        copy.deepcopy(r)
        for sid in sorted(scopes.keys())
        if isinstance(scopes[sid], dict)
        for r in scopes[sid].get("rules", [])
        if isinstance(r, dict)
    ]
    conflicts = detect_frustration(graph)
    packed_rules: list[dict[str, Any]] = []
    used_tokens = 10 + token_cost(conflicts)
    used_lines = 1 + len(conflicts)
    for r in all_rules:
        if used_lines >= max_lines:
            break
        c = token_cost(r.get("text", "")) + 6
        if used_tokens + c > eff_budget and packed_rules:
            break
        packed_rules.append(r)
        used_tokens += c
        used_lines += 1

    active_scopes = sorted(scopes.keys())
    temp_val = float(temperature) if temperature is not None else 0.30
    view: dict[str, Any] = {
        "status": "CONFLICT" if conflicts else "OK",
        "temperature": temp_val,
        "tau": _tau_min_for_temperature(temp_val),
        "hops": 1,
        "steps_taken": 1,
        "converged": True,
        "unresolved_energy": 0.0,
        "energy_trace": [0.0],
        "activations": {},
        "active_scopes": active_scopes,
        "active_rules": packed_rules,
        "active_tasks": copy.deepcopy(list(graph.get("active_tasks", []))),
        "lod2_meta": {},
        "conflicts": conflicts,
        "open_blockers": copy.deepcopy(list(graph.get("open_blockers", []))),
    }
    return _finalize_view(
        view,
        max_lines=max_lines,
        token_budget=eff_budget,
        scope_headers=_build_scope_headers(scopes, active_scopes, set()),
    )


def expand_node(
    graph: dict[str, Any],
    node_id: str,
    query: str = "",
    token_budget: int = 400,
    max_lines: int = 35,
) -> dict[str, Any]:
    """Expands a single scope or rule `node_id` in `graph`, including its attributes, rules, `_meta`, and edges (Spec 20 §1.4)."""
    if not isinstance(graph, dict) or not isinstance(node_id, str) or not node_id.strip():
        raise ValueError(f"node_id must be a non-empty str, got {node_id!r}")

    scopes = graph.get("scopes", {})
    if node_id in scopes and isinstance(scopes[node_id], dict):
        scope = scopes[node_id]
        rules = copy.deepcopy(scope.get("rules", []))
        lod2 = copy.deepcopy(scope.get("_meta", {})) if isinstance(scope.get("_meta"), dict) else {}
        conflicts = detect_frustration(graph, {str(r["id"]) for r in rules if isinstance(r, dict) and r.get("id")})
        scope_tasks = [
            copy.deepcopy(t)
            for t in graph.get("active_tasks", [])
            if isinstance(t, dict) and (t.get("scope") == node_id or node_id in t.get("depends_on", []))
        ]
        view: dict[str, Any] = {
            "status": "CONFLICT" if conflicts else "OK",
            "node_type": "SCOPE",
            "node_id": node_id,
            "scope": copy.deepcopy(scope),
            "active_scopes": [node_id],
            "active_rules": rules,
            "active_tasks": scope_tasks,
            "lod2_meta": lod2,
            "conflicts": conflicts,
            "open_blockers": [
                copy.deepcopy(b) for b in graph.get("open_blockers", []) if isinstance(b, dict) and b.get("scope") == node_id
            ],
        }
        return _finalize_view(view, max_lines=max_lines, token_budget=token_budget)

    rule_idx = _build_rule_index(graph)
    if node_id in rule_idx:
        sid, r = rule_idx[node_id]
        scope_meta = scopes.get(sid, {}).get("_meta", {})
        rmeta = copy.deepcopy(scope_meta.get(node_id, {})) if isinstance(scope_meta, dict) else {}
        view = {
            "status": "OK",
            "node_type": "RULE",
            "node_id": node_id,
            "scope_id": sid,
            "rule": copy.deepcopy(r),
            "meta": rmeta,
            "active_scopes": [sid],
            "active_rules": [copy.deepcopy(r)],
            "lod2_meta": {node_id: rmeta} if rmeta else {},
            "conflicts": [],
            "open_blockers": [],
        }
        return _finalize_view(view, max_lines=max_lines, token_budget=token_budget)

    raise KeyError(f"node_id {node_id!r} not found in graph scopes or rules")
