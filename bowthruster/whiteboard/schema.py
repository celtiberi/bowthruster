"""Spec 22A §2.1: Whiteboard & nD Lattice Container, SQLite Persistence, and Invariant Verifier."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from bowthruster.graph_core import (
    DEFAULT_K_CAP,
    DEFAULT_MAX_RING,
    MAX_META_KEY_WORDS,
    MAX_META_SLOTS,
    MAX_META_SLOT_WORDS,
    MAX_META_TOTAL_WORDS,
    SQLiteGraphStore,
    _TYPE_TO_FACET,
    clamp_rule_meta,
    format_compact_view,
    token_cost,
    tokenize_content_words,
)
from .graph_types import (
    ArtifactEntry,
    ArtifactSpec,
    BUILD_STATUSES,
    ContextGraphInvariantError,
    FlowEntry,
    LATTICE_KINDS,
    RuleEntry,
    ScopeNode,
    ScopeSpec,
    _ARTIFACT_KINDS,
    _AttrDict,
    _RULE_STORE_KINDS,
    _SCOPE_SBF_DEFAULTS,
    _apply_entry_defaults,
    _clean_opt_str,
    _clean_str_list,
    _ensure_scope_node_nosync,
    _normalize_scope_entry,
    normalize_board_graph,
)


class ContextGraph(dict[str, Any]):
    """Persistent Bounded Whiteboard / nD Lattice Graph container with optional SQLite binding."""

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        *,
        store: SQLiteGraphStore | None = None,
        **kwargs: Any,
    ) -> None:
        from bowthruster.whiteboard.migrations import BOARD_SCHEMA_VERSION, migrate_board_data

        inherited_store = store if store is not None else getattr(data, "_sqlite_store", None)
        init_d = (
            migrate_board_data(dict(data), target_version=BOARD_SCHEMA_VERSION)[0]
            if data
            else {}
        )
        super().__init__(init_d, **kwargs)
        self._sqlite_store: SQLiteGraphStore | None = inherited_store
        turn_val = int(self.get("turn", self.get("turn_index", 0)))
        _apply_entry_defaults(
            self,
            (
                ("schema_version", BOARD_SCHEMA_VERSION),
                ("schema_migrations", []),
                ("version", 1),
                ("turn", turn_val),
                ("turn_index", turn_val),
                ("k_cap", DEFAULT_K_CAP),
                ("max_ring", DEFAULT_MAX_RING),
                ("scopes", {}),
                ("flows", []),
                ("lattice_artifacts", []),
                ("ambient_notes", []),
                ("active_tasks", []),
                ("open_blockers", []),
                ("alias_index", {}),
            ),
            with_turns=False,
        )
        if isinstance(self.get("scopes"), dict):
            for sid, sc in list(self["scopes"].items()):
                if not isinstance(sc, ScopeNode):
                    self["scopes"][sid] = ScopeNode({"id": sid, **sc})
        normalize_board_graph(self)

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
    """Pure SBF store classifier governed by Bend 2 Law 4 (`lattice_sbf_never_enters_rule_store`)."""
    k = str(lattice_kind or "").strip().upper()
    if k in ("COMPONENT", "FLOW"):
        return "SCOPES" if k == "COMPONENT" else "FLOWS"
    if k in _ARTIFACT_KINDS:
        return "ARTIFACTS"
    if k in ("TASK", "ACTION", "COMMIT"):
        return "TASKS"
    if k in _RULE_STORE_KINDS:
        return "RULES"
    raise ValueError(f"Unknown lattice_kind={lattice_kind!r}, expected one of {LATTICE_KINDS}")


def should_register_as_scope(
    is_primary_scope: bool,
    is_flow_endpoint: bool,
    is_explicit_component: bool,
    is_bound_or_payload_val: bool = False,
) -> bool:
    """Pure ghost-scope exclusion guard governed by Bend 2 Law 5 (`ghost_scope_exclusion_law`)."""
    _ = is_bound_or_payload_val
    return bool(is_primary_scope or is_flow_endpoint or is_explicit_component)


def step_component_lifecycle_on_edit(cur_status: str | None, is_spec_mutated: bool) -> str:
    """Pure component build-lifecycle transition on spec edit governed by Bend 2 Law 6."""
    st = str(cur_status or "").strip().upper() or "DRAFT"
    if st not in BUILD_STATUSES:
        raise ValueError(f"Unknown build status {cur_status!r}, expected one of {BUILD_STATUSES}")
    return "STALE" if (bool(is_spec_mutated) and st in ("VERIFIED", "IN_QA")) else st


def resolve_packet_lifecycle_status(
    cur_status: str | None, is_lattice_proven: bool, all_deps_verified: bool
) -> str:
    """Pure dependency-gated packet readiness classifier governed by Bend 2 Law 7."""
    st = str(cur_status or "").strip().upper() or "DRAFT"
    if st not in BUILD_STATUSES:
        raise ValueError(f"Unknown build status {cur_status!r}, expected one of {BUILD_STATUSES}")
    if st in ("IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE"):
        return st
    if not bool(is_lattice_proven):
        return "DRAFT"
    return "READY" if bool(all_deps_verified) else "BLOCKED_BY_DEP"


def _invalidate_scope_on_edit(graph: dict[str, Any], *scope_ids: str) -> None:
    """Transitions mutated scopes and their `depends_on` / `parent` downstream dependents."""
    if not isinstance(graph, dict) or not isinstance(scopes := graph.get("scopes"), dict):
        return
    queue = [s for raw_s in scope_ids if (s := str(raw_s or "").strip())]
    queue.extend(s.split("::")[0] for s in list(queue) if "::" in s)
    visited: set[str] = set()
    while queue:
        if (sid := queue.pop(0)) in visited:
            continue
        visited.add(sid)
        if isinstance(sc := scopes.get(sid), dict):
            cur_st = (
                sc.get("status")
                if sc.get("status") not in (None, "", "DRAFT")
                else (sc.get("build_status") or "DRAFT")
            )
            next_st = step_component_lifecycle_on_edit(cur_st, True)
            sc["status"] = sc["build_status"] = next_st
            if next_st != "VERIFIED":
                sc["verified_untested"] = False
            if bool(sc.get("attributes")):
                for k_st in ("status", "build_status"):
                    if k_st in sc["attributes"]:
                        sc["attributes"][k_st] = sc["status"]
        for dep_sid, dep_sc in scopes.items():
            if bool(dep_sc) and dep_sid not in visited:
                if sid in (dep_sc.get("depends_on") or []) or dep_sc.get("parent") == sid:
                    queue.append(dep_sid)


def _partition_rules_for_mitosis(
    rules: list[dict[str, Any]], k_cap: int = DEFAULT_K_CAP
) -> dict[str, list[dict[str, Any]]]:
    """Partitions `rules` in Spec 22A §2.2 priority order: prefix, `facet`, `target_prop`, `page_N`."""
    if not rules:
        return {}
    sub_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        tp = str(r.get("target_prop") or "").strip().lower()
        if "/" in tp:
            prefix = tp.split("/")[0].strip() or "general"
        elif "_" in tp:
            prefix = tp.split("_")[0].strip() or "general"
        else:
            prefix = tp or "general"
        sub_groups.setdefault(prefix, []).append(r)
    if len(sub_groups) >= 2 and max(len(g) for g in sub_groups.values()) < len(rules):
        return sub_groups
    facet_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        default_facet = _TYPE_TO_FACET.get(str(r.get("type", "OBLIGATION")), "obligations")
        facet = str(r.get("facet") if r.get("facet") else default_facet).strip()
        facet_groups.setdefault(facet, []).append(r)
    if len(facet_groups) >= 2:
        return facet_groups
    prop_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        prop_key = str(r.get("target_prop") or "general").strip() or "general"
        prop_groups.setdefault(prop_key, []).append(r)
    if len(prop_groups) >= 2:
        return prop_groups
    step = max(1, int(k_cap))
    return {
        f"page_{idx // step + 1}": rules[idx : idx + step]
        for idx in range(0, len(rules), step)
    }


def sync_to_sqlite(graph: dict[str, Any]) -> None:
    """Syncs `graph` into its attached `SQLiteGraphStore` whenever one is present."""
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
    k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING,
    store: SQLiteGraphStore | None = None,
    db_path: str | Path | None = None,
) -> ContextGraph:
    """Initializes a clean `ContextGraph` and syncs initial state to SQLite (Spec 22A §2.1)."""
    if isinstance(k_cap, bool) or not isinstance(k_cap, int) or k_cap < 1:
        raise ValueError(f"k_cap must be a positive int, got {k_cap!r}")
    if isinstance(max_ring, bool) or not isinstance(max_ring, int) or max_ring < 1:
        raise ValueError(f"max_ring must be a positive int, got {max_ring!r}")
    active_store = (
        store if store is not None else (SQLiteGraphStore(db_path) if db_path is not None else None)
    )
    graph = ContextGraph({"k_cap": k_cap, "max_ring": max_ring}, store=active_store)
    sync_to_sqlite(graph)
    return graph


create_empty_lattice = create_empty_graph


def _validate_scope_rules_and_meta(
    scopes: dict[str, Any], k_cap: int, errors: list[str]
) -> dict[str, tuple[str, dict[str, Any]]]:
    all_rules: dict[str, tuple[str, dict[str, Any]]] = {}
    for sid, scope in scopes.items():
        if not isinstance(scope, dict) or not isinstance(rules := scope.get("rules", []), list):
            errors.append(f"scope {sid!r} must be a dict with rules list")
            continue
        if len(rules) > k_cap:
            errors.append(f"scope {sid!r} has {len(rules)} direct rules, exceeding k_cap={k_cap}")
        raw_meta = scope.get("_meta", {})
        scope_meta = raw_meta if isinstance(raw_meta, dict) else {}
        if not isinstance(raw_meta, dict):
            errors.append(f"scope {sid!r} _meta must be a dict")
        for r in rules:
            if not isinstance(r, dict) or not isinstance(rid := r.get("id"), str) or not rid:
                errors.append(f"scope {sid!r} contains malformed rule {r!r}")
                continue
            if rid in all_rules:
                prev_sid = all_rules[rid][0]
                errors.append(f"duplicate rule id {rid!r} across scopes {prev_sid!r} and {sid!r}")
            all_rules[rid] = (sid, r)
            for ex in r.get("exceptions", []):
                if isinstance(ex, str) or not isinstance(ex, dict):
                    raise ValueError(
                        f"rule {rid!r} exceptions entry must be a structured dict, got {ex!r}"
                    )
                has_rid = isinstance(ex.get("rule_id"), str) and bool(ex["rule_id"])
                if not has_rid or not isinstance(ex.get("note"), str):
                    errors.append(
                        f"rule {rid!r} exception entry missing 'rule_id' or 'note': {ex!r}"
                    )
            mkeys = r.get("meta_keys", [])
            if not isinstance(mkeys, list) or len(mkeys) > MAX_META_SLOTS:
                errors.append(f"rule {rid!r} meta_keys exceeds {MAX_META_SLOTS} slots")
            elif any(len(str(mk).split()) > MAX_META_KEY_WORDS for mk in mkeys):
                errors.append(f"rule {rid!r} meta_key exceeds {MAX_META_KEY_WORDS} words")
            if isinstance(rmeta := scope_meta.get(rid, {}), dict):
                if len(rmeta) > MAX_META_SLOTS:
                    errors.append(
                        f"rule {rid!r} _meta has {len(rmeta)} slots, exceeding {MAX_META_SLOTS}"
                    )
                if any(len(str(mv).split()) > MAX_META_SLOT_WORDS for mv in rmeta.values()):
                    errors.append(f"rule {rid!r} _meta slot exceeds {MAX_META_SLOT_WORDS} words")
                total_w = sum(len(str(mv).split()) for mv in rmeta.values())
                if total_w > MAX_META_TOTAL_WORDS:
                    errors.append(
                        f"rule {rid!r} _meta has {total_w} total words, exceeding {MAX_META_TOTAL_WORDS}"
                    )
    return all_rules


def _validate_flows_artifacts_and_notes(
    graph: dict[str, Any], max_ring: int, errors: list[str]
) -> set[str]:
    flows, flow_ids = graph.get("flows", []), set()
    if not isinstance(flows, list):
        errors.append("graph['flows'] must be a list")
    else:
        for idx, fl in enumerate(flows):
            if not fl:
                errors.append(f"flows[{idx}] must be a dict")
                continue
            if isinstance(fl.get("id"), str) and fl["id"]:
                flow_ids.add(fl["id"])
            src, tgt = fl.get("source"), fl.get("target")
            valid_ends = (
                isinstance(src, str) and bool(src.strip())
                and isinstance(tgt, str) and bool(tgt.strip())
            )
            if not valid_ends:
                errors.append(f"flows[{idx}] must have non-empty 'source' and 'target' strings")
    artifacts = graph.get("lattice_artifacts", [])
    if not isinstance(artifacts, list):
        errors.append("graph['lattice_artifacts'] must be a list")
    else:
        for idx, art in enumerate(artifacts):
            if not art:
                errors.append(f"lattice_artifacts[{idx}] must be a dict")
            elif art.get("kind") not in _ARTIFACT_KINDS:
                errors.append(
                    f"lattice_artifacts[{idx}] kind={art.get('kind')!r} must be SCHEMA/INTERFACE/LAW"
                )
            elif not isinstance(art.get("name"), str) or not art["name"].strip():
                errors.append(f"lattice_artifacts[{idx}] must have a non-empty 'name' str")
    ambient = graph.get("ambient_notes", [])
    if not isinstance(ambient, list):
        errors.append("graph['ambient_notes'] must be a list")
    else:
        if len(ambient) > max_ring:
            errors.append(f"len(ambient_notes)={len(ambient)} exceeds max_ring={max_ring}")
        for idx, note in enumerate(ambient):
            aturns = note.get("access_turns") if isinstance(note, dict) else None
            if not isinstance(aturns, list) or not aturns:
                errors.append(f"ambient_notes[{idx}] must contain non-empty access_turns: list[int]")
            elif not all(isinstance(t, int) and not isinstance(t, bool) for t in aturns):
                errors.append(f"ambient_notes[{idx}] must contain non-empty access_turns: list[int]")
    return flow_ids


def validate_context_graph(
    graph: dict[str, Any], *, k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING
) -> list[str]:
    """Validates all Spec 22A §2.1 structural, nD Lattice, and thermodynamic invariants."""
    if not isinstance(graph, dict) or not isinstance(scopes := graph.get("scopes"), dict):
        return ["graph and graph['scopes'] must be dicts"]
    normalize_board_graph(graph)
    errors: list[str] = []
    all_rules = _validate_scope_rules_and_meta(scopes, k_cap, errors)
    for rid, (_, r) in all_rules.items():
        for target_scope in r.get("refs", []):
            if target_scope not in scopes:
                errors.append(f"rule {rid!r} refs nonexistent target scope {target_scope!r}")
            elif rid not in scopes[target_scope].get("in_edges", []):
                errors.append(
                    f"rule {rid!r} refs {target_scope!r}, but missing from {target_scope!r}.in_edges"
                )
        for ov_id in r.get("overrides", []):
            if ov_id not in all_rules:
                errors.append(f"rule {rid!r} overrides nonexistent rule {ov_id!r}")
            elif not any(
                ex.get("rule_id") == rid for ex in all_rules[ov_id][1].get("exceptions", [])
            ):
                errors.append(
                    f"rule {rid!r} overrides {ov_id!r}, but {ov_id!r}.exceptions lacks rule_id={rid!r}"
                )
        for ex in [x for x in r.get("exceptions", []) if isinstance(x, dict)]:
            if (ex_rid := ex.get("rule_id")) not in all_rules:
                errors.append(f"rule {rid!r} lists nonexistent exception rule_id={ex_rid!r}")
            elif rid not in all_rules[ex_rid][1].get("overrides", []):
                errors.append(
                    f"rule {rid!r} has exception {ex_rid!r}, but {ex_rid!r}.overrides lacks {rid!r}"
                )
    flow_ids = _validate_flows_artifacts_and_notes(graph, max_ring, errors)
    for sid, scope in [(k, v) for k, v in scopes.items() if isinstance(v, dict)]:
        for in_rid in scope.get("in_edges", []):
            if in_rid in all_rules and sid not in all_rules[in_rid][1].get("refs", []):
                errors.append(
                    f"scope {sid!r}.in_edges contains {in_rid!r}, but {in_rid!r}.refs lacks {sid!r}"
                )
            elif in_rid not in all_rules and in_rid not in scopes and in_rid not in flow_ids:
                errors.append(f"scope {sid!r}.in_edges references nonexistent rule {in_rid!r}")
    return errors


verify_graph_invariants = validate_context_graph
