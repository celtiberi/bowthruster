"""Spec 22A §2.1: Whiteboard & nD Lattice Data Structures, SQLite Persistence Binding, and Invariant Verifier (`schema.py`)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from bowthruster.graph_core import (
    DEFAULT_K_CAP, DEFAULT_MAX_RING, MAX_META_KEY_WORDS, MAX_META_SLOTS,
    MAX_META_SLOT_WORDS, MAX_META_TOTAL_WORDS, SQLiteGraphStore, _TYPE_TO_FACET,
    clamp_rule_meta, format_compact_view, token_cost, tokenize_content_words,
)
from bowthruster.nlp import restore_ascii_dots

LATTICE_KINDS = ("COMPONENT", "FLOW", "SCHEMA", "INTERFACE", "LAW", "CONSTRAINT", "FACT", "TASK")
BUILD_STATUSES = ("DRAFT", "BLOCKED_BY_DEP", "READY", "IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE")
_ARTIFACT_KINDS = frozenset({"SCHEMA", "INTERFACE", "LAW"})
_RULE_STORE_KINDS = frozenset({
    "CONSTRAINT", "FACT", "BOUND", "PROHIBITION", "OBLIGATION", "POLICY", "GOAL", "PROPOSAL", "REVOCATION",
})
_SCOPE_SBF_DEFAULTS: tuple[tuple[str, Any], ...] = (
    ("role", None), ("inputs", []), ("outputs", []), ("depends_on", []),
    ("owner", None), ("stage", None), ("kind", None), ("status", "DRAFT"),
    ("build_tag", None), ("core_tag", None), ("failure_diagnostic", None),
)


def _apply_entry_defaults(
    target: dict[str, Any], defaults: tuple[tuple[str, Any], ...],
    *, with_turns: bool = True
) -> None:
    for k, v in defaults:
        target.setdefault(k, copy.deepcopy(v))
    if with_turns:
        turn_val = int(target.get("turn", target.get("updated_turn", target.get("created_turn", 1))))
        target.setdefault("turn", turn_val)
        target.setdefault("created_turn", int(target.get("created_turn", turn_val)))
        target.setdefault("updated_turn", int(target.get("updated_turn", turn_val)))


def _clean_str_list(items: list[str] | None) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for x in items:
        if (s := restore_ascii_dots(str(x)).strip()) and s not in out:
            out.append(s)
    return out


def _clean_opt_str(val: Any) -> str | None:
    return (restore_ascii_dots(str(val)).strip() or None) if val is not None else None


class ContextGraphInvariantError(ValueError):
    """Raised when a Whiteboard / ContextGraph structural or thermodynamic invariant is violated (Spec 22A §2.1)."""

    def __init__(self, code: str, errors: list[str]) -> None:
        self.code, self.errors = code, list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


class _AttrDict(dict[str, Any]):
    """Base dict supporting `.to_dict()` and attribute access for Lattice / Whiteboard entries."""

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


class RuleEntry(_AttrDict):
    """Dict-compatible structured rule node inside a `ScopeNode` (Spec 22A §2.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        rtype = str(self.get("type", "OBLIGATION"))
        _apply_entry_defaults(self, (
            ("id", ""), ("type", "OBLIGATION"), ("facet", _TYPE_TO_FACET.get(rtype, "obligations")),
            ("text", ""), ("scope", "global_defaults"), ("target_prop", None), ("bound_op", None),
            ("bound_val", None), ("conditions", []), ("is_exception", False), ("refs", []),
            ("overrides", []), ("exceptions", []), ("meta_keys", []), ("meta_tokens", []),
        ))


class FlowEntry(_AttrDict):
    """Dict-compatible directed flow / behavior edge in `graph['flows']` (Spec 22A §2.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        _apply_entry_defaults(self, (
            ("id", ""), ("kind", "FLOW"), ("source", ""), ("target", ""), ("payload", None),
            ("action", None), ("condition", None), ("step", None), ("text", ""), ("dimensions", {}),
        ))


class ArtifactEntry(_AttrDict):
    """Dict-compatible first-class `SCHEMA` / `INTERFACE` / `LAW` entry in `graph['lattice_artifacts']` (Spec 22A §2.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        sid = str(self.get("scope_id") or self.get("scope") or "global_defaults")
        _apply_entry_defaults(self, (
            ("id", ""), ("kind", "SCHEMA"), ("name", ""), ("scope_id", sid), ("scope", sid),
            ("spec", ""), ("inputs", []), ("outputs", []), ("owner", None), ("stage", None),
            ("dimensions", {}), ("text", ""),
        ))


class ScopeNode(_AttrDict):
    """Dict-compatible component / scope node inside a `ContextGraph` / `LatticeGraph` (Spec 22A §2.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        _apply_entry_defaults(self, (
            ("id", "global_defaults"), ("parent", None), ("children", []), ("aliases", []),
            ("attributes", {}), ("rules", []), ("in_edges", []), ("_meta", {}), *_SCOPE_SBF_DEFAULTS,
        ), with_turns=False)
        if not self.get("status"):
            self["status"] = "DRAFT"

    def __getattr__(self, name: str) -> Any:
        return self.get("_meta", {}) if name == "_meta" else super().__getattr__(name)


def _ensure_scope_node_nosync(graph: dict[str, Any], scope_id: str, *, parent: str | None = None) -> dict[str, Any]:
    """Internal helper ensuring `scopes[scope_id]` exists with SBF defaults, wiring `parent`/`children`, and indexing `alias_index`."""
    if not isinstance(scope_id, str) or not scope_id.strip():
        raise ValueError(f"scope_id must be a non-empty str, got {scope_id!r}")
    sid = scope_id.strip()
    scopes = graph.setdefault("scopes", {})
    if sid not in scopes or not isinstance(scopes[sid], dict):
        scopes[sid] = ScopeNode({"id": sid, "parent": parent})
    else:
        scope = scopes[sid]
        _apply_entry_defaults(scope, (
            ("id", sid), ("parent", parent), ("children", []), ("aliases", []),
            ("attributes", {}), ("rules", []), ("in_edges", []), ("_meta", {}), *_SCOPE_SBF_DEFAULTS,
        ), with_turns=False)
        if not scope.get("status"):
            scope["status"] = "DRAFT"
        if parent is not None and parent != sid:
            scope["parent"] = parent

    alias_idx = graph.setdefault("alias_index", {})
    alias_idx[sid.lower()] = sid
    for al in scopes[sid].get("aliases", []):
        if isinstance(al, str) and al.strip():
            alias_idx[al.strip().lower()] = sid

    eff_parent = scopes[sid].get("parent")
    if isinstance(eff_parent, str) and eff_parent.strip() and eff_parent.strip() != sid:
        p_children = _ensure_scope_node_nosync(graph, eff_parent.strip()).setdefault("children", [])
        if sid not in p_children:
            p_children.append(sid)
    return scopes[sid]


class ContextGraph(dict[str, Any]):
    """Persistent Bounded Whiteboard / nD Lattice Graph container with optional `SQLiteGraphStore` binding (Spec 22A §2.1)."""

    def __init__(
        self, data: dict[str, Any] | None = None,
        *, store: SQLiteGraphStore | None = None,
        **kwargs: Any
    ) -> None:
        inherited_store = store if store is not None else getattr(data, "_sqlite_store", None)
        super().__init__(data or {}, **kwargs)
        self._sqlite_store: SQLiteGraphStore | None = inherited_store
        turn_val = int(self.get("turn", self.get("turn_index", 0)))
        _apply_entry_defaults(self, (
            ("version", 1), ("turn", turn_val), ("turn_index", turn_val), ("k_cap", DEFAULT_K_CAP),
            ("max_ring", DEFAULT_MAX_RING), ("scopes", {}), ("flows", []), ("lattice_artifacts", []),
            ("ambient_notes", []), ("active_tasks", []), ("open_blockers", []), ("alias_index", {}),
        ), with_turns=False)
        if isinstance(self.get("scopes"), dict):
            for sid, sc in list(self["scopes"].items()):
                if isinstance(sc, dict) and not isinstance(sc, ScopeNode):
                    self["scopes"][sid] = ScopeNode({"id": sid, **sc})

    @property
    def store(self) -> SQLiteGraphStore | None:
        return self._sqlite_store

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __deepcopy__(self, memo: dict[int, Any]) -> ContextGraph:
        copied = ContextGraph(copy.deepcopy(dict(self), memo), store=self._sqlite_store)
        memo[id(self)] = copied
        return copied

    def __getattr__(self, name: str) -> Any:
        if name == "_sqlite_store":
            return object.__getattribute__(self, "_sqlite_store")
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


WhiteboardGraph = ContextGraph
LatticeGraph = ContextGraph


def classify_lattice_store(lattice_kind: str) -> str:
    """Pure SBF store classifier governed by Bend 2 Law 4 (`lattice_sbf_never_enters_rule_store`, Spec 22A §2.1)."""
    k = str(lattice_kind or "").strip().upper()
    if k == "COMPONENT":
        return "SCOPES"
    if k == "FLOW":
        return "FLOWS"
    if k in _ARTIFACT_KINDS:
        return "ARTIFACTS"
    if k in ("TASK", "ACTION", "COMMIT"):
        return "TASKS"
    if k in _RULE_STORE_KINDS:
        return "RULES"
    raise ValueError(f"Unknown lattice_kind={lattice_kind!r}, expected one of {LATTICE_KINDS}")


def should_register_as_scope(
    is_primary_scope: bool, is_flow_endpoint: bool, is_explicit_component: bool, is_bound_or_payload_val: bool = False,
) -> bool:
    """Pure ghost-scope exclusion guard governed by Bend 2 Law 5 (`ghost_scope_exclusion_law`, Spec 22A §2.1)."""
    _ = is_bound_or_payload_val
    return bool(is_primary_scope or is_flow_endpoint or is_explicit_component)


def step_component_lifecycle_on_edit(cur_status: str | None, is_spec_mutated: bool) -> str:
    """Pure component build-lifecycle transition on spec edit governed by Bend 2 Law 6 (`verified_component_becomes_stale_on_spec_edit`, Spec 22A §2.1)."""
    st = str(cur_status or "").strip().upper() or "DRAFT"
    if st not in BUILD_STATUSES:
        raise ValueError(f"Unknown build status {cur_status!r}, expected one of {BUILD_STATUSES}")
    return "STALE" if (bool(is_spec_mutated) and st in ("VERIFIED", "IN_QA")) else st


def resolve_packet_lifecycle_status(cur_status: str | None, is_lattice_proven: bool, all_deps_verified: bool) -> str:
    """Pure dependency-gated packet readiness classifier governed by Bend 2 Law 7 (`resolve_packet_lifecycle_status_law`, Spec 22A §2.1)."""
    st = str(cur_status or "").strip().upper() or "DRAFT"
    if st not in BUILD_STATUSES:
        raise ValueError(f"Unknown build status {cur_status!r}, expected one of {BUILD_STATUSES}")
    if st in ("IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE"):
        return st
    if not bool(is_lattice_proven):
        return "DRAFT"
    return "READY" if bool(all_deps_verified) else "BLOCKED_BY_DEP"


def _invalidate_scope_on_edit(graph: dict[str, Any], *scope_ids: str) -> None:
    """Transitions mutated scopes and their `depends_on` / `parent` downstream dependents via `step_component_lifecycle_on_edit` (Spec 22A §2.2, Law 6)."""
    scopes = graph.get("scopes") if isinstance(graph, dict) else None
    if not isinstance(scopes, dict):
        return
    queue: list[str] = []
    for raw_s in scope_ids:
        if s := str(raw_s or "").strip():
            queue.append(s)
            if "::" in s:
                queue.append(s.split("::")[0])
    visited: set[str] = set()
    while queue:
        sid = queue.pop(0)
        if sid in visited:
            continue
        visited.add(sid)
        if isinstance(sc := scopes.get(sid), dict):
            cur_st = sc.get("status") if sc.get("status") not in (None, "", "DRAFT") else (sc.get("build_status") or "DRAFT")
            sc["status"] = sc["build_status"] = step_component_lifecycle_on_edit(cur_st, True)
            if isinstance(sc.get("attributes"), dict):
                for k_st in ("status", "build_status"):
                    if k_st in sc["attributes"]:
                        sc["attributes"][k_st] = sc["status"]
        for dep_sid, dep_sc in scopes.items():
            if isinstance(dep_sc, dict) and dep_sid not in visited:
                if sid in (dep_sc.get("depends_on") or []) or dep_sc.get("parent") == sid:
                    queue.append(dep_sid)


def _partition_rules_for_mitosis(rules: list[dict[str, Any]], k_cap: int = DEFAULT_K_CAP) -> dict[str, list[dict[str, Any]]]:
    """Partitions `rules` in Spec 22A §2.2 priority order: Axis 1 (`/` or `_` prefix), Axis 2 (`facet`), Axis 3 (`target_prop`), or `page_N`."""
    if not rules:
        return {}
    sub_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        tp = str(r.get("target_prop") or "").strip().lower()
        prefix = (tp.split("/")[0].strip() or "general") if "/" in tp else (
            (tp.split("_")[0].strip() or "general")
            if "_" in tp else (tp or "general")
        )
        sub_groups.setdefault(prefix, []).append(r)
    if len(sub_groups) >= 2 and max(len(g) for g in sub_groups.values()) < len(rules):
        return sub_groups
    facet_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        facet = str(r.get("facet") or _TYPE_TO_FACET.get(str(r.get("type", "OBLIGATION")), "obligations")).strip()
        facet_groups.setdefault(facet, []).append(r)
    if len(facet_groups) >= 2:
        return facet_groups
    prop_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        prop = str(r.get("target_prop") or "general").strip() or "general"
        prop_groups.setdefault(prop, []).append(r)
    if len(prop_groups) >= 2:
        return prop_groups
    step = max(1, int(k_cap))
    return {f"page_{idx // step + 1}": rules[idx : idx + step] for idx in range(0, len(rules), step)}


def sync_to_sqlite(graph: dict[str, Any]) -> None:
    """Syncs `graph` into its attached `SQLiteGraphStore` whenever one is present (Spec 22A §2.1)."""
    if not isinstance(graph, dict):
        return
    if "turn_index" in graph and "turn" not in graph:
        graph["turn"] = int(graph["turn_index"])
    elif "turn" in graph and "turn_index" in graph:
        graph["turn"] = max(int(graph.get("turn", 0)), int(graph.get("turn_index", 0)))
    store = getattr(graph, "_sqlite_store", None) or graph.get("_sqlite_store")
    if isinstance(store, SQLiteGraphStore):
        store.save_graph(graph)


def create_empty_graph(
    k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING,
    store: SQLiteGraphStore | None = None, db_path: str | Path | None = None,
) -> ContextGraph:
    """Initializes a clean `ContextGraph` / `LatticeGraph`, attaching a `SQLiteGraphStore` when `store` or `db_path`
    is provided and syncing the initial state to SQLite (Spec 22A §2.1)."""
    if isinstance(k_cap, bool) or not isinstance(k_cap, int) or k_cap < 1:
        raise ValueError(f"k_cap must be a positive int, got {k_cap!r}")
    if isinstance(max_ring, bool) or not isinstance(max_ring, int) or max_ring < 1:
        raise ValueError(f"max_ring must be a positive int, got {max_ring!r}")
    active_store = store if store is not None else (SQLiteGraphStore(db_path) if db_path is not None else None)
    graph = ContextGraph({"k_cap": k_cap, "max_ring": max_ring}, store=active_store)
    sync_to_sqlite(graph)
    return graph


create_empty_lattice = create_empty_graph


def _validate_scope_rules_and_meta(
    scopes: dict[str, Any], k_cap: int, errors: list[str]
) -> dict[str, tuple[str, dict[str, Any]]]:
    all_rules: dict[str, tuple[str, dict[str, Any]]] = {}
    for sid, scope in scopes.items():
        if not isinstance(scope, dict):
            errors.append(f"scope {sid!r} must be a dict")
            continue
        rules = scope.get("rules", [])
        if not isinstance(rules, list):
            errors.append(f"scope {sid!r} rules must be a list")
            continue
        if len(rules) > k_cap:
            errors.append(f"scope {sid!r} has {len(rules)} direct rules, exceeding k_cap={k_cap}")
        scope_meta = scope.get("_meta", {})
        if not isinstance(scope_meta, dict):
            errors.append(f"scope {sid!r} _meta must be a dict")
            scope_meta = {}

        for r in rules:
            if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"]:
                errors.append(f"scope {sid!r} contains malformed rule {r!r}")
                continue
            rid = r["id"]
            if rid in all_rules:
                errors.append(f"duplicate rule id {rid!r} across scopes {all_rules[rid][0]!r} and {sid!r}")
            all_rules[rid] = (sid, r)
            for ex in r.get("exceptions", []):
                if isinstance(ex, str) or not isinstance(ex, dict):
                    raise ValueError(f"rule {rid!r} exceptions entry must be a structured dict {{'rule_id', 'note'}}, got {ex!r}")
                if not isinstance(ex.get("rule_id"), str) or not ex["rule_id"] or not isinstance(ex.get("note"), str):
                    errors.append(f"rule {rid!r} exception entry missing non-empty 'rule_id' or 'note': {ex!r}")
            mkeys = r.get("meta_keys", [])
            if not isinstance(mkeys, list) or len(mkeys) > MAX_META_SLOTS:
                errors.append(f"rule {rid!r} meta_keys exceeds {MAX_META_SLOTS} slots")
            else:
                for mk in mkeys:
                    if len(str(mk).split()) > MAX_META_KEY_WORDS:
                        errors.append(f"rule {rid!r} meta_key {mk!r} exceeds {MAX_META_KEY_WORDS} words")
            if isinstance(rmeta := scope_meta.get(rid, {}), dict):
                if len(rmeta) > MAX_META_SLOTS:
                    errors.append(f"rule {rid!r} _meta has {len(rmeta)} slots, exceeding {MAX_META_SLOTS}")
                total_w = 0
                for mk, mv in rmeta.items():
                    w_cnt = len(str(mv).split())
                    total_w += w_cnt
                    if w_cnt > MAX_META_SLOT_WORDS:
                        errors.append(f"rule {rid!r} _meta[{mk!r}] has {w_cnt} words, exceeding {MAX_META_SLOT_WORDS}")
                if total_w > MAX_META_TOTAL_WORDS:
                    errors.append(f"rule {rid!r} _meta has {total_w} total words, exceeding {MAX_META_TOTAL_WORDS}")
    return all_rules


def validate_context_graph(
    graph: dict[str, Any], *, k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING,
) -> list[str]:
    """Validates all Spec 22A §2.1 structural, nD Lattice, and thermodynamic invariants on `graph`."""
    if not isinstance(graph, dict):
        return [f"graph must be a dict, got {type(graph).__name__}"]
    scopes = graph.get("scopes")
    if not isinstance(scopes, dict):
        return ["graph['scopes'] must be a dict"]

    errors: list[str] = []
    all_rules = _validate_scope_rules_and_meta(scopes, k_cap, errors)

    for rid, (sid, r) in all_rules.items():
        for target_scope in r.get("refs", []):
            if target_scope not in scopes:
                errors.append(f"rule {rid!r} refs nonexistent target scope {target_scope!r}")
            elif rid not in scopes[target_scope].get("in_edges", []):
                errors.append(f"rule {rid!r} refs {target_scope!r}, but {rid!r} is missing from {target_scope!r}.in_edges")
        for ov_id in r.get("overrides", []):
            if ov_id not in all_rules:
                errors.append(f"rule {rid!r} overrides nonexistent rule {ov_id!r}")
            elif not any(isinstance(ex, dict) and ex.get("rule_id") == rid for ex in all_rules[ov_id][1].get("exceptions", [])):
                errors.append(f"rule {rid!r} overrides {ov_id!r}, but {ov_id!r}.exceptions lacks rule_id={rid!r}")
        for ex in r.get("exceptions", []):
            if isinstance(ex, dict):
                ex_rid = ex.get("rule_id")
                if ex_rid not in all_rules:
                    errors.append(f"rule {rid!r} lists nonexistent exception rule_id={ex_rid!r}")
                elif rid not in all_rules[ex_rid][1].get("overrides", []):
                    errors.append(f"rule {rid!r} has exception {ex_rid!r}, but {ex_rid!r}.overrides lacks {rid!r}")

    flows = graph.get("flows", [])
    flow_ids: set[str] = set()
    if not isinstance(flows, list):
        errors.append("graph['flows'] must be a list")
    else:
        for idx, fl in enumerate(flows):
            if not isinstance(fl, dict):
                errors.append(f"flows[{idx}] must be a dict")
                continue
            if isinstance(fl.get("id"), str) and fl["id"]:
                flow_ids.add(fl["id"])
            src, tgt = fl.get("source"), fl.get("target")
            if not isinstance(src, str) or not src.strip() or not isinstance(tgt, str) or not tgt.strip():
                errors.append(f"flows[{idx}] must have non-empty 'source' and 'target' strings")

    artifacts = graph.get("lattice_artifacts", [])
    if not isinstance(artifacts, list):
        errors.append("graph['lattice_artifacts'] must be a list")
    else:
        for idx, art in enumerate(artifacts):
            if not isinstance(art, dict):
                errors.append(f"lattice_artifacts[{idx}] must be a dict")
                continue
            if art.get("kind") not in _ARTIFACT_KINDS:
                errors.append(f"lattice_artifacts[{idx}] kind={art.get('kind')!r} must be in ('SCHEMA', 'INTERFACE', 'LAW')")
            if not isinstance(art.get("name"), str) or not art["name"].strip():
                errors.append(f"lattice_artifacts[{idx}] must have a non-empty 'name' str")

    for sid, scope in scopes.items():
        if not isinstance(scope, dict):
            continue
        for in_rid in scope.get("in_edges", []):
            if in_rid in all_rules:
                if sid not in all_rules[in_rid][1].get("refs", []):
                    errors.append(f"scope {sid!r}.in_edges contains {in_rid!r}, but {in_rid!r}.refs lacks {sid!r}")
            elif in_rid not in scopes and in_rid not in flow_ids:
                errors.append(f"scope {sid!r}.in_edges references nonexistent rule {in_rid!r}")

    ambient = graph.get("ambient_notes", [])
    if not isinstance(ambient, list):
        errors.append("graph['ambient_notes'] must be a list")
    else:
        if len(ambient) > max_ring:
            errors.append(f"len(ambient_notes)={len(ambient)} exceeds max_ring={max_ring}")
        for idx, note in enumerate(ambient):
            if not isinstance(note, dict):
                errors.append(f"ambient_notes[{idx}] must be a dict")
            elif not isinstance(aturns := note.get("access_turns"), list) or not aturns or not all(isinstance(t, int) and not isinstance(t, bool) for t in aturns):
                errors.append(f"ambient_notes[{idx}] must contain non-empty access_turns: list[int]")

    return errors


verify_graph_invariants = validate_context_graph
