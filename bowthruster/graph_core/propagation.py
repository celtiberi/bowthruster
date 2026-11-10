"""Spec 21 §1.3: Parameterized 5-Family Multi-Hop Propagation, Index Scanner, and Node Expander (`propagation.py`)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .activation import (
    _build_rule_index, _tau_min_for_temperature, compute_seed_activations, compute_tau, infer_query_temperature,
)
from .formatting import (
    _finalize_view, _pack_active_rules_and_lod2, format_compact_view,
    matches_nd_slice, token_cost, tokenize_content_words,
)

ConflictDetector = Callable[[dict[str, Any], set[str] | list[str] | None], list[dict[str, Any]]]


class PropagationPhase(str, Enum):
    """Explicit lifecycle phase of the Spec 21 §1.3 5-family propagation engine."""
    SEEDED = "SEEDED"
    NO_SEED_MATCH = "NO_SEED_MATCH"
    RELAXING = "RELAXING"
    CONVERGED = "CONVERGED"
    MAX_HOPS_REACHED = "MAX_HOPS_REACHED"
    CONFLICT = "CONFLICT"


def _build_five_family_edges(graph: dict[str, Any]) -> list[tuple[str, str, float, str]]:
    """Constructs directed weighted edges `(u, v, w_uv, family)` across all 5 Spec 21 edge families."""
    edges: list[tuple[str, str, float, str]] = []
    scopes = graph.get("scopes", {})
    rule_idx = _build_rule_index(graph)
    for sid, scope in scopes.items():
        if not scope:
            continue
        parent = scope.get("parent")
        if parent in scopes:
            edges.extend(((sid, parent, 0.65, "hierarchy"), (parent, sid, 0.55, "hierarchy")))
        for ch in scope.get("children", []):
            if ch in scopes:
                edges.extend(((sid, ch, 0.55, "hierarchy"), (ch, sid, 0.65, "hierarchy")))
        for r in scope.get("rules", []):
            if not r or not r.get("id"):
                continue
            rid = str(r["id"])
            edges.extend(((sid, rid, 0.85, "hierarchy"), (rid, sid, 0.85, "hierarchy")))
            for ref_s in r.get("refs", []):
                if ref_s in scopes and ref_s != sid:
                    edges.extend(((rid, ref_s, 0.80, "refs"), (ref_s, rid, 0.70, "in_edges")))
                    edges.append((sid, ref_s, 0.75, "refs"))
            for ov_id in r.get("overrides", []):
                if ov_id in rule_idx:
                    ov_sid = rule_idx[ov_id][0]
                    edges.extend(((rid, ov_id, 0.90, "overrides"), (ov_id, rid, 0.90, "exceptions")))
                    if ov_sid != sid:
                        edges.extend(((sid, ov_sid, 0.80, "overrides"), (ov_sid, sid, 0.80, "exceptions")))
            for ex in r.get("exceptions", []):
                if isinstance(ex.get("rule_id"), str) and ex["rule_id"] in rule_idx:
                    ex_id = ex["rule_id"]
                    edges.extend(((rid, ex_id, 0.90, "exceptions"), (ex_id, rid, 0.90, "overrides")))
        for in_rid in scope.get("in_edges", []):
            if in_rid in rule_idx:
                src_sid = rule_idx[in_rid][0]
                edges.extend(((sid, in_rid, 0.75, "in_edges"), (in_rid, sid, 0.80, "refs")))
                if src_sid != sid:
                    edges.append((sid, src_sid, 0.75, "in_edges"))
        for dep_s in scope.get("depends_on", []):
            if dep_s in scopes and dep_s != sid:
                edges.extend(((sid, dep_s, 0.80, "depends_on"), (dep_s, sid, 0.75, "depends_on")))
    for fl in graph.get("flows", []):
        if fl:
            src, tgt = fl.get("source"), fl.get("target")
            if isinstance(src, str) and isinstance(tgt, str) and src in scopes and tgt in scopes and src != tgt:
                edges.extend(((src, tgt, 0.85, "flows"), (tgt, src, 0.85, "flows")))
    for t in graph.get("active_tasks", []):
        if not t or not t.get("id"):
            continue
        tid, tsid = str(t["id"]), t.get("scope")
        if tsid in scopes:
            edges.extend(((tid, tsid, 0.85, "hierarchy"), (tsid, tid, 0.75, "hierarchy")))
        for dep_s in t.get("depends_on", []):
            if dep_s in scopes:
                edges.extend(((tid, dep_s, 0.85, "depends_on"), (dep_s, tid, 0.75, "depends_on")))
                if tsid in scopes and tsid != dep_s:
                    edges.append((tsid, dep_s, 0.80, "depends_on"))
        for gov_rid in t.get("governed_by", []):
            if gov_rid in rule_idx:
                gov_sid = rule_idx[gov_rid][0]
                edges.extend(((tid, gov_rid, 0.85, "governed_by"), (gov_rid, tid, 0.80, "governed_by")))
                if gov_sid in scopes:
                    edges.append((tid, gov_sid, 0.80, "governed_by"))
    return edges


def _unresolved_energy(
    activations: dict[str, float], edges: list[tuple[str, str, float, str]], tau: float, gamma: float,
) -> float:
    """Computes unresolved boundary energy $E^{(t)}$ across active nodes $A^{(t)}(u) \\ge \\tau$."""
    active = {u for u, a in activations.items() if a >= tau}
    total_e = 0.0
    for u, v, w_uv, _ in edges:
        if u in active and v not in active:
            au, av = activations.get(u, 0.0), activations.get(v, 0.0)
            potential = gamma * w_uv * au
            if potential >= tau and av < tau:
                total_e += max(0.0, potential - av)
    return round(total_e, 6)


def _build_scope_headers(scopes: dict[str, Any], active_scopes: list[str], q_tokens: set[str]) -> list[str]:
    """Builds compact `@ SCOPE [<sid>] attrs: ...` headers for top-activated scopes with table-fused attributes."""
    headers: list[str] = []
    for sid in active_scopes:
        scope_obj = scopes.get(sid)
        if not bool(scope_obj) or not isinstance(attrs := scope_obj.get("attributes"), dict) or not attrs:
            continue
        attr_parts = [f"{k}={v}" for k, v in sorted(attrs.items()) if k != "updated_turn" and v is not None]
        if not attr_parts:
            continue
        attr_toks = set(tokenize_content_words(" ".join(attr_parts)))
        if bool(q_tokens & attr_toks) or len(active_scopes) <= 3:
            headers.append(f"@ SCOPE [{sid}] attrs: {', '.join(attr_parts)}")
        if len(headers) >= 3:
            break
    return headers


@dataclass
class PropagationStateMachine:
    """Explicit state machine managing multi-hop relaxation and energy convergence (Spec 21 §1.3)."""
    phase: PropagationPhase
    temperature: float
    tau: float
    gamma: float
    max_hops: int
    activations: dict[str, float]
    edges: list[tuple[str, str, float, str]]
    hops_taken: int = 0
    energy_trace: list[float] = field(default_factory=list)

    @classmethod
    def seed(
        cls, graph: dict[str, Any], query: str, *, temperature: float | None, gamma: float, max_hops: int,
    ) -> PropagationStateMachine:
        temp_val = float(temperature) if temperature is not None else infer_query_temperature(graph, query)
        if not (0.05 <= temp_val <= 1.00):
            raise ValueError(f"temperature {temp_val} out of valid range [0.05, 1.00]")
        seed_acts, _ = compute_seed_activations(graph, query)
        h_max = max(seed_acts.values(), default=0.0)
        tau_min = _tau_min_for_temperature(temp_val)
        if not seed_acts or h_max < tau_min:
            return cls(
                phase=PropagationPhase.NO_SEED_MATCH, temperature=temp_val, tau=tau_min,
                gamma=gamma, max_hops=max_hops, activations={}, edges=[], hops_taken=0, energy_trace=[0.0],
            )
        tau = compute_tau(h_max, temp_val)
        edges = _build_five_family_edges(graph)
        act = dict(seed_acts)
        e0 = _unresolved_energy(act, edges, tau, gamma)
        initial_phase = PropagationPhase.CONVERGED if e0 == 0.0 else PropagationPhase.SEEDED
        return cls(
            phase=initial_phase, temperature=temp_val, tau=tau, gamma=gamma,
            max_hops=max_hops, activations=act, edges=edges, hops_taken=0, energy_trace=[e0],
        )

    def step(self) -> None:
        if self.phase not in (PropagationPhase.SEEDED, PropagationPhase.RELAXING):
            return
        if self.hops_taken >= self.max_hops:
            self.phase = PropagationPhase.MAX_HOPS_REACHED
            return
        self.phase = PropagationPhase.RELAXING
        next_act = dict(self.activations)
        changed = False
        for u, v, w_uv, _ in self.edges:
            au = self.activations.get(u, 0.0)
            if au < self.tau:
                continue
            cand = round(self.gamma * w_uv * au, 6)
            if cand > next_act.get(v, 0.0) and cand >= self.tau * 0.85:
                next_act[v] = min(1.0, cand)
                changed = True
        self.activations = next_act
        self.hops_taken += 1
        et = _unresolved_energy(self.activations, self.edges, self.tau, self.gamma)
        self.energy_trace.append(et)
        delta_small = len(self.energy_trace) >= 2 and abs(self.energy_trace[-1] - self.energy_trace[-2]) < 1e-6
        if et == 0.0 or not changed or delta_small:
            self.phase = PropagationPhase.CONVERGED
        elif self.hops_taken >= self.max_hops:
            self.phase = PropagationPhase.MAX_HOPS_REACHED

    def run(self) -> None:
        while self.phase in (PropagationPhase.SEEDED, PropagationPhase.RELAXING):
            self.step()

    def resolve_conflicts(self, conflicts: list[dict[str, Any]]) -> None:
        if conflicts:
            self.phase = PropagationPhase.CONFLICT

    @property
    def converged(self) -> bool:
        return self.phase != PropagationPhase.NO_SEED_MATCH and (
            self.energy_trace[-1] == 0.0 or len(self.energy_trace) <= self.max_hops + 1
        )

    @property
    def status(self) -> str:
        if self.phase == PropagationPhase.NO_SEED_MATCH:
            return "NO_SEED_MATCH"
        return "CONFLICT" if self.phase == PropagationPhase.CONFLICT else "OK"


def _collect_lattice_view_items(
    graph: dict[str, Any], active_scopes: list[str], q_tokens: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Collects `(component_specs, active_flows, active_artifacts)` for `active_scopes` and `q_tokens`."""
    scopes = graph.get("scopes", {}) if isinstance(graph, dict) else {}
    active_set = set(active_scopes)
    comp_specs: list[dict[str, Any]] = []
    sbf_keys = ("role", "inputs", "outputs", "depends_on", "owner", "stage")
    for sid in active_scopes:
        sc = scopes.get(sid)
        if (any(sc.get(k) for k in sbf_keys) or sc.get("kind") == "COMPONENT"):
            comp_specs.append({
                "id": sid, "scope_id": sid, "parent": sc.get("parent"),
                "children": list(sc.get("children", [])), "role": str(sc.get("role") or ""),
                "inputs": list(sc.get("inputs", [])), "outputs": list(sc.get("outputs", [])),
                "depends_on": list(sc.get("depends_on", [])), "owner": sc.get("owner"),
                "stage": sc.get("stage"), "status": sc.get("status", "ACTIVE"),
            })
    active_flows: list[dict[str, Any]] = []
    for fl in ((graph or {}).get("flows", []) or []):
        if fl:
            fl_blob = f"{fl.get('source', '')} {fl.get('target', '')} {fl.get('payload', '')} {fl.get('text', '')}"
            f_toks = set(tokenize_content_words(fl_blob))
            in_scope = fl.get("source") in active_set or fl.get("target") in active_set
            if matches_nd_slice(in_scope, bool(q_tokens and (q_tokens & f_toks))):
                active_flows.append(copy.deepcopy(fl))
    active_artifacts: list[dict[str, Any]] = []
    for art in ((graph or {}).get("lattice_artifacts", []) or []):
        if art:
            a_scope = str(next((_v for _v in (art.get("scope_id"), art.get("scope")) if _v), None) or "")
            art_blob = (
                f"{art.get('name', '')} {art.get('kind', '')} {art.get('spec', '')} "
                f"{art.get('text', '')} {art.get('owner', '')} {art.get('stage', '')}"
            )
            a_toks = set(tokenize_content_words(art_blob))
            if matches_nd_slice(a_scope in active_set, bool(q_tokens and (q_tokens & a_toks))):
                active_artifacts.append(copy.deepcopy(art))
    return comp_specs, active_flows, active_artifacts


def propagate_nodes(
    graph: dict[str, Any],
    query: str,
    temperature: float | None = None,
    token_budget: int = 400,
    max_lines: int = 35,
    gamma: float = 0.75,
    max_hops: int = 4,
    **options: Any,
) -> dict[str, Any]:
    """Executes 5-family multi-hop relaxation parameterized by optional `conflict_detector` (Spec 21 §1.3)."""
    max_steps = options.get("max_steps")
    max_tokens = options.get("max_tokens")
    conflict_detector: ConflictDetector | None = options.get("conflict_detector")
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    eff_hops = int(max_steps if max_steps is not None else min(3, max_hops))
    sm = PropagationStateMachine.seed(graph, query, temperature=temperature, gamma=gamma, max_hops=eff_hops)
    if sm.phase == PropagationPhase.NO_SEED_MATCH:
        return {
            "status": sm.status, "temperature": sm.temperature, "tau": sm.tau,
            "hops": 0, "steps_taken": 0, "converged": True, "unresolved_energy": 0.0,
            "energy_trace": list(sm.energy_trace), "activations": {}, "active_scopes": [],
            "active_rules": [], "active_tasks": [], "component_specs": [], "active_flows": [],
            "active_artifacts": [], "lod2_meta": {}, "conflicts": [],
            "open_blockers": list(graph.get("open_blockers", [])),
            "text": "[FAIL_CLOSED: NO_SEED_MATCH]", "token_count": 1, "line_count": 1,
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
    conflicts = (
        list(conflict_detector(graph, set(candidate_rids) if candidate_rids else None))
        if conflict_detector is not None else []
    )
    for b in graph.get("open_blockers", []):
        if b.get("type") == "CONFLICT":
            if b.get("scope") in active_scopes or any(r in candidate_rids for r in b.get("rule_ids", [])):
                if not any(c.get("id") == b.get("id") for c in conflicts):
                    conflicts.append(copy.deepcopy(b))
    sm.resolve_conflicts(conflicts)
    q_tokens = set(tokenize_content_words(query))
    packed_rules, lod2_meta = _pack_active_rules_and_lod2(
        scopes, rule_idx, candidate_rids, act, q_tokens, conflicts, eff_budget, max_lines,
    )
    comp_specs, active_flows, active_artifacts = _collect_lattice_view_items(graph, active_scopes, q_tokens)
    matched_tasks: list[dict[str, Any]] = []
    for t in graph.get("active_tasks", []):
        if not t or not t.get("id"):
            continue
        tid = str(t["id"])
        has_active_dep = any(d in active_scopes for d in t.get("depends_on", []))
        if act.get(tid, 0.0) >= tau or t.get("scope") in active_scopes or has_active_dep:
            t_copy = copy.deepcopy(t)
            t_copy["activation"] = round(act.get(tid, 0.0), 6)
            matched_tasks.append(t_copy)
    matched_tasks.sort(key=lambda t: (-act.get(str(t.get("id", "")), 0.0), str(t.get("id", ""))))
    view: dict[str, Any] = {
        "status": sm.status, "temperature": sm.temperature, "tau": sm.tau,
        "hops": sm.hops_taken, "steps_taken": sm.hops_taken, "converged": sm.converged,
        "unresolved_energy": sm.energy_trace[-1], "energy_trace": list(sm.energy_trace),
        "activations": dict(act), "active_scopes": active_scopes, "active_rules": packed_rules,
        "active_tasks": matched_tasks, "component_specs": comp_specs, "active_flows": active_flows,
        "active_artifacts": active_artifacts, "lod2_meta": lod2_meta, "conflicts": conflicts,
        "open_blockers": list(graph.get("open_blockers", [])),
    }
    return _finalize_view(
        view, max_lines=max_lines, token_budget=eff_budget,
        scope_headers=_build_scope_headers(scopes, active_scopes, q_tokens),
    )


def scan_nodes(
    graph: dict[str, Any],
    query: str = "",
    token_budget: int = 400,
    max_lines: int = 35,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    conflict_detector: ConflictDetector | None = None,
) -> dict[str, Any]:
    """Executes a 1-hop (`L_max = 1`) index scan over `graph` within `token_budget` and `max_lines` (Spec 21 §1.3)."""
    eff_budget = int(max_tokens if max_tokens is not None else token_budget)
    if isinstance(query, str) and query.strip():
        return propagate_nodes(
            graph, query, temperature=temperature, token_budget=eff_budget,
            max_lines=max_lines, max_hops=1, max_steps=1, conflict_detector=conflict_detector,
        )
    scopes = graph.get("scopes", {}) if isinstance(graph, dict) else {}
    all_rules = [
        copy.deepcopy(r)
        for sid in sorted(scopes.keys()) if scopes[sid]
        for r in scopes[sid].get("rules", [])
    ]
    conflicts = list(conflict_detector(graph, None)) if conflict_detector is not None else []
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
    comp_specs, active_flows, active_artifacts = _collect_lattice_view_items(graph, active_scopes, set())
    temp_val = float(temperature) if temperature is not None else 0.30
    view: dict[str, Any] = {
        "status": "CONFLICT" if conflicts else "OK", "temperature": temp_val,
        "tau": _tau_min_for_temperature(temp_val), "hops": 1, "steps_taken": 1,
        "converged": True, "unresolved_energy": 0.0, "energy_trace": [0.0],
        "activations": {}, "active_scopes": active_scopes, "active_rules": packed_rules,
        "active_tasks": copy.deepcopy(list(graph.get("active_tasks", []))),
        "component_specs": comp_specs, "active_flows": active_flows, "active_artifacts": active_artifacts,
        "lod2_meta": {}, "conflicts": conflicts, "open_blockers": copy.deepcopy(list(graph.get("open_blockers", []))),
    }
    return _finalize_view(
        view, max_lines=max_lines, token_budget=eff_budget,
        scope_headers=_build_scope_headers(scopes, active_scopes, set()),
    )


def expand_graph_node(
    graph: dict[str, Any],
    node_id: str,
    query: str = "",
    token_budget: int = 400,
    max_lines: int = 35,
    *,
    conflict_detector: ConflictDetector | None = None,
) -> dict[str, Any]:
    """Expands a single scope or rule `node_id` in `graph` (`LOD 0 -> 1 -> 2`) (Spec 21 §1.3)."""
    if not isinstance(graph, dict) or not isinstance(node_id, str) or not node_id.strip():
        raise ValueError(f"node_id must be a non-empty str, got {node_id!r}")
    scopes = graph.get("scopes", {})
    if node_id in scopes and isinstance(scopes[node_id], dict):
        scope = scopes[node_id]
        rules = copy.deepcopy(scope.get("rules", []))
        lod2 = copy.deepcopy(scope.get("_meta") or {})
        rids = {str(r["id"]) for r in rules if r.get("id")}
        conflicts = list(conflict_detector(graph, rids)) if conflict_detector is not None else []
        scope_tasks = [
            copy.deepcopy(t) for t in graph.get("active_tasks", [])
            if (t.get("scope") == node_id or node_id in t.get("depends_on", []))
        ]
        comp_specs, active_flows, active_artifacts = _collect_lattice_view_items(graph, [node_id], set())
        view: dict[str, Any] = {
            "status": "CONFLICT" if conflicts else "OK", "node_type": "SCOPE", "node_id": node_id,
            "scope": copy.deepcopy(scope), "active_scopes": [node_id], "active_rules": rules,
            "active_tasks": scope_tasks, "component_specs": comp_specs, "active_flows": active_flows,
            "active_artifacts": active_artifacts, "lod2_meta": lod2, "conflicts": conflicts,
            "open_blockers": [
                copy.deepcopy(b) for b in graph.get("open_blockers", [])
                if b.get("scope") == node_id
            ],
        }
        return _finalize_view(view, max_lines=max_lines, token_budget=token_budget)
    rule_idx = _build_rule_index(graph)
    if node_id in rule_idx:
        sid, r = rule_idx[node_id]
        scope_meta = scopes.get(sid, {}).get("_meta", {})
        rmeta = copy.deepcopy(scope_meta.get(node_id, {})) if isinstance(scope_meta, dict) else {}
        ext_links = copy.deepcopy(r.get("external_links") or [])
        if not ext_links and isinstance(graph.get("external_links"), dict):
            ext_links = copy.deepcopy(graph["external_links"].get(node_id, []))
        view = {
            "status": "OK", "node_type": "RULE", "node_id": node_id, "scope_id": sid,
            "rule": copy.deepcopy(r), "meta": rmeta, "external_links": ext_links,
            "active_scopes": [sid], "active_rules": [copy.deepcopy(r)],
            "lod2_meta": {node_id: rmeta} if rmeta else {}, "conflicts": [], "open_blockers": [],
        }
        return _finalize_view(view, max_lines=max_lines, token_budget=token_budget)
    raise KeyError(f"node_id {node_id!r} not found in graph scopes or rules")


propagate_graph = propagate_nodes
scan_graph = scan_nodes
expand_node = expand_graph_node
