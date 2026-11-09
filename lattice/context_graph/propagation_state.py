"""Spec 20 §1.4: 5-Family Edge Builder, Unresolved Boundary Energy, and Explicit `PropagationStateMachine`."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .activation import (
    _tau_min_for_temperature,
    compute_seed_activations,
    compute_tau,
    infer_query_temperature,
)
from .formatter import tokenize_content_words


class PropagationPhase(str, Enum):
    """Explicit state machine phases for 5-family thermodynamic relaxation (Spec 20 §1.4)."""

    SEEDING = "SEEDING"
    RELAXING = "RELAXING"
    CONVERGED = "CONVERGED"
    NO_SEED_MATCH = "NO_SEED_MATCH"
    CONFLICT = "CONFLICT"


def _build_five_family_edges(graph: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Builds directed edges `(u, v, family)` across all 5 edge families:
    `hierarchy`, `refs`/`in_edges`, `overrides`/`exceptions`, `depends_on`, and `governed_by` (Spec 20 §1.4)."""
    edges: list[tuple[str, str, str]] = []
    scopes = graph.get("scopes", {})
    if not isinstance(scopes, dict):
        return edges

    for sid, scope in scopes.items():
        if not isinstance(scope, dict):
            continue
        parent = scope.get("parent")
        if isinstance(parent, str) and parent in scopes:
            edges.append((sid, parent, "hierarchy"))
            edges.append((parent, sid, "hierarchy"))
        for child in scope.get("children", []):
            if isinstance(child, str) and child in scopes:
                edges.append((sid, child, "hierarchy"))
                edges.append((child, sid, "hierarchy"))
        for in_rid in scope.get("in_edges", []):
            if isinstance(in_rid, str) and in_rid:
                edges.append((sid, in_rid, "in_edges"))
        for r in scope.get("rules", []):
            if not isinstance(r, dict) or not r.get("id"):
                continue
            rid = str(r["id"])
            edges.append((sid, rid, "hierarchy"))
            edges.append((rid, sid, "hierarchy"))
            for ref_s in r.get("refs", []):
                if isinstance(ref_s, str) and ref_s in scopes:
                    edges.append((rid, ref_s, "refs"))
            for ov_id in r.get("overrides", []):
                if isinstance(ov_id, str) and ov_id:
                    edges.append((rid, ov_id, "overrides"))
            for ex in r.get("exceptions", []):
                if isinstance(ex, dict) and isinstance(ex.get("rule_id"), str) and ex["rule_id"]:
                    edges.append((rid, ex["rule_id"], "exceptions"))

    for task in graph.get("active_tasks", []):
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id", "")).strip()
        if not tid:
            continue
        tsid = str(task.get("scope") or "")
        if tsid in scopes:
            edges.append((tid, tsid, "hierarchy"))
            edges.append((tsid, tid, "hierarchy"))
        for dep_id in task.get("depends_on", []):
            if dep_id:
                edges.append((tid, str(dep_id), "depends_on"))
                edges.append((str(dep_id), tid, "depends_on"))
        for gov_id in task.get("governed_by", []):
            if gov_id:
                edges.append((tid, str(gov_id), "governed_by"))
                edges.append((str(gov_id), tid, "governed_by"))

    return edges


def _unresolved_energy(
    activations: dict[str, float],
    edges: list[tuple[str, str, str]],
    tau: float,
    gamma: float,
) -> float:
    """Computes 5-family unresolved thermodynamic boundary energy $E^{(t)}$ across all directed edges $(u, v)$ (Spec 20 §1.4)."""
    energy = 0.0
    for u, v, _ in edges:
        au = activations.get(u, 0.0)
        av = activations.get(v, 0.0)
        if au >= tau and av < tau:
            diff = gamma * au - av
            if diff > 0.0:
                energy += diff
    return round(energy, 6)


def _build_scope_headers(
    scopes: dict[str, Any],
    active_scopes: list[str],
    q_tokens: set[str],
) -> list[str]:
    """Builds up to 3 `@ SCOPE [<sid>] attrs: ...` header lines for active scopes with non-`updated_turn` attributes."""
    headers: list[str] = []
    for sid in active_scopes:
        if len(headers) >= 3:
            break
        scope_obj = scopes.get(sid)
        if not isinstance(scope_obj, dict):
            continue
        attrs = scope_obj.get("attributes")
        if not isinstance(attrs, dict):
            continue
        attr_parts = [f"{k}={v}" for k, v in sorted(attrs.items()) if k != "updated_turn" and v is not None]
        if not attr_parts:
            continue
        attr_tokens = set(tokenize_content_words(f"{sid} {' '.join(attr_parts)}"))
        if len(active_scopes) <= 3 or not q_tokens or bool(q_tokens & attr_tokens):
            headers.append(f"@ SCOPE [{sid}] attrs: {', '.join(attr_parts)}")
    return headers


@dataclass
class PropagationStateMachine:
    """Explicit state machine tracking seed initialization, 5-family hop relaxation, and convergence (Spec 20 §1.4)."""

    phase: PropagationPhase = PropagationPhase.SEEDING
    temperature: float = 0.30
    tau: float = 0.20
    tau_min: float = 0.20
    gamma: float = 0.75
    max_hops: int = 3
    hops_taken: int = 0
    activations: dict[str, float] = field(default_factory=dict)
    energy_trace: list[float] = field(default_factory=list)
    edges: list[tuple[str, str, str]] = field(default_factory=list)

    @classmethod
    def seed(
        cls,
        graph: dict[str, Any],
        query: str,
        *,
        temperature: float | None = None,
        gamma: float = 0.75,
        max_hops: int = 3,
    ) -> PropagationStateMachine:
        """Initializes seed activations $A^{(0)}$, adaptive threshold $\\tau(T)$, and initial boundary energy $E^{(0)}$."""
        seed_acts, _ = compute_seed_activations(graph, query)
        temp = infer_query_temperature(graph, query) if temperature is None else float(temperature)
        h_max = max(seed_acts.values(), default=0.0)
        tau = compute_tau(h_max, temp)
        tau_min = _tau_min_for_temperature(temp)

        if not seed_acts or h_max < tau_min:
            return cls(
                phase=PropagationPhase.NO_SEED_MATCH,
                temperature=temp,
                tau=tau,
                tau_min=tau_min,
                gamma=gamma,
                max_hops=max_hops,
                hops_taken=0,
                activations={},
                energy_trace=[0.0],
                edges=[],
            )

        edges = _build_five_family_edges(graph)
        act = dict(seed_acts)
        e0 = _unresolved_energy(act, edges, tau, gamma)
        initial_phase = PropagationPhase.CONVERGED if e0 == 0.0 else PropagationPhase.RELAXING
        return cls(
            phase=initial_phase,
            temperature=temp,
            tau=tau,
            tau_min=tau_min,
            gamma=gamma,
            max_hops=max_hops,
            hops_taken=0,
            activations=act,
            energy_trace=[e0],
            edges=edges,
        )

    def step(self) -> bool:
        """Executes a single message-passing hop across `self.edges` and updates `self.phase`."""
        if self.phase != PropagationPhase.RELAXING:
            return False
        nxt = dict(self.activations)
        for u, v, _ in self.edges:
            au = self.activations.get(u, 0.0)
            if au >= self.tau:
                cand = round(self.gamma * au, 6)
                if cand > nxt.get(v, 0.0):
                    nxt[v] = cand
        self.activations = nxt
        self.hops_taken += 1
        et = _unresolved_energy(self.activations, self.edges, self.tau, self.gamma)
        self.energy_trace.append(et)
        if et == 0.0 or abs(et - self.energy_trace[-2]) < 1e-9:
            self.phase = PropagationPhase.CONVERGED
        return True

    def run(self) -> PropagationStateMachine:
        """Runs relaxation steps up to `self.max_hops` until `CONVERGED`."""
        for _ in range(self.max_hops):
            if self.phase != PropagationPhase.RELAXING:
                break
            self.step()
        return self

    def resolve_conflicts(self, conflicts: list[dict[str, Any]]) -> None:
        """Transitions state machine to `CONFLICT` when active rules are frustrated."""
        if conflicts:
            self.phase = PropagationPhase.CONFLICT

    @property
    def converged(self) -> bool:
        """Returns `True` iff boundary energy $E^{(t)} == 0$ or $\\Delta E^{(t)} == 0$."""
        if not self.energy_trace:
            return True
        return self.energy_trace[-1] == 0.0 or (
            len(self.energy_trace) >= 2 and abs(self.energy_trace[-1] - self.energy_trace[-2]) < 1e-9
        )

    @property
    def status(self) -> str:
        """Maps current `PropagationPhase` to view status string (`NO_SEED_MATCH`, `CONFLICT`, or `OK`)."""
        if self.phase == PropagationPhase.NO_SEED_MATCH:
            return "NO_SEED_MATCH"
        if self.phase == PropagationPhase.CONFLICT:
            return "CONFLICT"
        return "OK"
