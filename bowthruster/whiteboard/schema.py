"""Spec 22A §2.1: Whiteboard & nD Lattice Data Structures, SQLite Persistence, and Invariant Verifier (`schema.py`)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ScopeSpec:
    """Canonical normalized board scope specification."""
    id: str = "global_defaults"
    status: str = "DRAFT"
    files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class ArtifactSpec:
    """Canonical normalized board artifact (`SCHEMA` / `INTERFACE` / `LAW`) specification."""
    id: str = ""
    kind: str = "SCHEMA"
    name: str = ""
    scope_id: str = "global_defaults"
    file: str = ""
    spec: str = ""

from bowthruster.graph_core import (DEFAULT_K_CAP, DEFAULT_MAX_RING, MAX_META_KEY_WORDS, MAX_META_SLOTS,
    MAX_META_SLOT_WORDS, MAX_META_TOTAL_WORDS, SQLiteGraphStore, _TYPE_TO_FACET,
    clamp_rule_meta, format_compact_view, token_cost, tokenize_content_words)
from bowthruster.nlp import restore_ascii_dots

LATTICE_KINDS = ("COMPONENT", "FLOW", "SCHEMA", "INTERFACE", "LAW", "CONSTRAINT", "FACT", "TASK")
BUILD_STATUSES = ("DRAFT", "BLOCKED_BY_DEP", "READY", "IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE")
_ARTIFACT_KINDS = frozenset({"SCHEMA", "INTERFACE", "LAW"})
_RULE_STORE_KINDS = frozenset({
    "CONSTRAINT", "FACT", "BOUND", "PROHIBITION", "OBLIGATION", "POLICY", "GOAL", "PROPOSAL", "REVOCATION"})
_SCOPE_SBF_DEFAULTS: tuple[tuple[str, Any], ...] = (
    ("role", None), ("inputs", []), ("outputs", []), ("depends_on", []), ("files", []), ("tests", []),
    ("owner", None), ("stage", None), ("kind", None), ("status", "DRAFT"), ("build_status", "DRAFT"),
    ("build_tag", None), ("core_tag", None), ("failure_diagnostic", None))

def _apply_entry_defaults(target: dict[str, Any], defaults: tuple[tuple[str, Any], ...], *, with_turns: bool = True,
    ) -> None:
    for k, v in defaults:
        target.setdefault(k, copy.deepcopy(v))
    if with_turns:
        turn_val = int(target.get("turn", target.get("updated_turn", target.get("created_turn", 1))))
        target.setdefault("turn", turn_val)
        target.setdefault("created_turn", int(target.get("created_turn", turn_val)))
        target.setdefault("updated_turn", int(target.get("updated_turn", turn_val)))

def _clean_str_list(items: list[str] | str | None) -> list[str]:
    raw_parts = ([x.strip() for x in items.replace(";", ",").split(",")] if isinstance(items, str)
        else ([str(x).strip() for x in items] if isinstance(items, list) else []))
    out: list[str] = []
    for x in raw_parts:
        s = restore_ascii_dots(x).strip()
        if s and s.lower() not in ("none", "-", "—", "n/a", "null") and s not in out:
            out.append(s)
    return out

def _clean_opt_str(val: Any) -> str | None:
    return (restore_ascii_dots(str(val)).strip() or None) if val is not None else None

class ContextGraphInvariantError(ValueError):
    """Raised when a Whiteboard / ContextGraph invariant is violated (Spec 22A §2.1)."""
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
            ("overrides", []), ("exceptions", []), ("meta_keys", []), ("meta_tokens", [])))

class FlowEntry(_AttrDict):
    """Dict-compatible directed flow / behavior edge in `graph['flows']` (Spec 22A §2.1)."""
    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        _apply_entry_defaults(self, (("id", ""), ("kind", "FLOW"), ("source", ""), ("target", ""), ("payload", None),
            ("action", None), ("condition", None), ("step", None), ("text", ""), ("dimensions", {})))

class ArtifactEntry(_AttrDict):
    """Dict-compatible first-class `SCHEMA` / `INTERFACE` / `LAW` entry in `graph['lattice_artifacts']`."""
    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        sid = str(next((_v for _v in (self.get("scope_id"), self.get("scope")) if _v), None) or "global_defaults")
        _apply_entry_defaults(self, (("id", ""), ("kind", "SCHEMA"), ("name", ""), ("scope_id", sid), ("scope", sid),
            ("spec", ""), ("inputs", []), ("outputs", []), ("owner", None), ("stage", None),
            ("dimensions", {}), ("text", "")))

class ScopeNode(_AttrDict):
    """Dict-compatible component / scope node inside a `ContextGraph` / `LatticeGraph` (Spec 22A §2.1)."""
    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        _apply_entry_defaults(self, (("id", "global_defaults"), ("parent", None), ("children", []), ("aliases", []),
            ("attributes", {}), ("rules", []), ("in_edges", []), ("_meta", {}), *_SCOPE_SBF_DEFAULTS,
            ), with_turns=False)
        self["status"] = self["build_status"] = str(self.get("status", self.get("build_status")) or "DRAFT")
    def __getattr__(self, name: str) -> Any:
        return self.get("_meta", {}) if name == "_meta" else super().__getattr__(name)

def _normalize_scope_entry(sid: str, sc: dict[str, Any]) -> None:
    sc.setdefault("id", sid)
    if not bool(sc.get("attributes")):
        sc["attributes"] = {}
    attrs, raw_st = sc["attributes"], sc.get("status")
    b_st = next((_v for _v in (sc.get("build_status"), attrs.get("status")) if _v), None) or attrs.get("build_status")
    sc["status"] = sc["build_status"] = str(
        b_st if raw_st in (None, "", "DRAFT") and b_st else (raw_st or b_st or "DRAFT") ).strip().upper()
    raw_files = sc.get("files") if sc.get("files") is not None else attrs.get("files")
    raw_tests = sc.get("tests")
    if raw_tests is None:
        raw_tests = sc.get("test_files") if sc.get("test_files") is not None else (
            attrs.get("tests") if attrs.get("tests") is not None else attrs.get("test_files"))
    files_list, tests_list = [], []
    for f_entry in _clean_str_list(raw_files):
        if f_entry.lower().startswith("tests:"):
            if (t_val := f_entry.split(":", 1)[1].strip()) and t_val not in tests_list:
                tests_list.append(t_val)
        elif f_entry not in files_list:
            files_list.append(f_entry)
    for t_entry in _clean_str_list(raw_tests):
        clean_t = t_entry.split(":", 1)[1].strip() if t_entry.lower().startswith("tests:") else t_entry
        if clean_t and clean_t not in tests_list:
            tests_list.append(clean_t)
    sc["files"], sc["tests"] = files_list, tests_list
    sc.pop("test_files", None)
    sc["depends_on"] = _clean_str_list(sc.get("depends_on"))
    for k in ("rules", "children", "aliases", "in_edges"):
        if sc.get(k) is None:
            sc[k] = []
    if sc.get("_meta") is None:
        sc["_meta"] = {}

def normalize_board_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Normalizes `scopes`, `flows`, `lattice_artifacts`, and `external_links` in-place (Spec 22A §2.1)."""
    if not isinstance(graph, dict):
        return graph
    if (scopes := graph.get("scopes")) is None:
        scopes = graph["scopes"] = {}
    if isinstance(scopes, dict):
        for sid, sc in scopes.items():
            if sc:
                _normalize_scope_entry(str(sid), sc)
    if graph.get("flows") is None:
        graph["flows"] = []
    else:
        for fl in [f for f in graph["flows"]]:
            fl.setdefault("kind", "FLOW")
            fl.setdefault("source", "")
            fl.setdefault("target", "")
            if not isinstance(fl.get("dimensions"), dict):
                fl["dimensions"] = {}
    if graph.get("lattice_artifacts") is None:
        graph["lattice_artifacts"] = []
    else:
        for art in [a for a in graph["lattice_artifacts"]]:
            art["scope_id"] = art["scope"] = str(art.get("scope_id", art.get("scope")) or "global_defaults")
            art["name"] = str(art.get("name", art.get("id")) or "")
            if not isinstance(art.get("dimensions"), dict):
                art["dimensions"] = {}
            dims = art["dimensions"]
            if not art.get("spec") and dims.get("spec", art.get("text")):
                art["spec"] = str(dims.get("spec", art.get("text")) or "")
            if not art.get("file") and dims.get("file"):
                art["file"] = str(dims.get("file"))
    ext = graph.get("external_links")
    if isinstance(ext, list):
        by_node: dict[str, list[dict[str, Any]]] = {}
        for lk in [x for x in ext if isinstance(x, dict) and x.get("node_id")]:
            by_node.setdefault(str(lk["node_id"]), []).append(lk)
        graph["external_links"] = by_node
    elif not isinstance(ext, dict):
        graph["external_links"] = {}
    return graph

def _ensure_scope_node_nosync(graph: dict[str, Any], scope_id: str, *, parent: str | None = None) -> dict[str, Any]:
    """Internal helper ensuring `scopes[scope_id]` exists with SBF defaults and wiring `parent`/`children`."""
    if not isinstance(scope_id, str) or not scope_id.strip():
        raise ValueError(f"scope_id must be a non-empty str, got {scope_id!r}")
    sid, scopes = scope_id.strip(), graph.setdefault("scopes", {})
    if sid not in scopes or not isinstance(scopes[sid], dict):
        scopes[sid] = ScopeNode({"id": sid, "parent": parent})
    else:
        scope = scopes[sid]
        _apply_entry_defaults(scope, (("id", sid), ("parent", parent), ("children", []), ("aliases", []),
            ("attributes", {}), ("rules", []), ("in_edges", []), ("_meta", {}), *_SCOPE_SBF_DEFAULTS,
            ), with_turns=False)
        _normalize_scope_entry(sid, scope)
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
    """Persistent Bounded Whiteboard / nD Lattice Graph container with optional `SQLiteGraphStore` binding."""
    def __init__(self, data: dict[str, Any] | None = None, *, store: SQLiteGraphStore | None = None, **kwargs: Any,
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
    """Pure SBF store classifier governed by Bend 2 Law 4 (`lattice_sbf_never_enters_rule_store`, Spec 22A)."""
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
    is_primary_scope: bool, is_flow_endpoint: bool, is_explicit_component: bool, is_bound_or_payload_val: bool = False,
) -> bool:
    """Pure ghost-scope exclusion guard governed by Bend 2 Law 5 (`ghost_scope_exclusion_law`, Spec 22A)."""
    _ = is_bound_or_payload_val
    return bool(is_primary_scope or is_flow_endpoint or is_explicit_component)

def step_component_lifecycle_on_edit(cur_status: str | None, is_spec_mutated: bool) -> str:
    """Pure component build-lifecycle transition on spec edit governed by Bend 2 Law 6 (Spec 22A §2.1)."""
    st = str(cur_status or "").strip().upper() or "DRAFT"
    if st not in BUILD_STATUSES:
        raise ValueError(f"Unknown build status {cur_status!r}, expected one of {BUILD_STATUSES}")
    return "STALE" if (bool(is_spec_mutated) and st in ("VERIFIED", "IN_QA")) else st

def resolve_packet_lifecycle_status(cur_status: str | None, is_lattice_proven: bool, all_deps_verified: bool) -> str:
    """Pure dependency-gated packet readiness classifier governed by Bend 2 Law 7 (Spec 22A §2.1)."""
    st = str(cur_status or "").strip().upper() or "DRAFT"
    if st not in BUILD_STATUSES:
        raise ValueError(f"Unknown build status {cur_status!r}, expected one of {BUILD_STATUSES}")
    if st in ("IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE"):
        return st
    return ("READY" if bool(all_deps_verified) else "BLOCKED_BY_DEP") if bool(is_lattice_proven) else "DRAFT"

def _invalidate_scope_on_edit(graph: dict[str, Any], *scope_ids: str) -> None:
    """Transitions mutated scopes and their `depends_on` / `parent` downstream dependents (Spec 22A Law 6)."""
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
            cur_st = sc.get("status") if sc.get("status") not in (None, "", "DRAFT") else (
                sc.get("build_status") or "DRAFT")
            sc["status"] = sc["build_status"] = step_component_lifecycle_on_edit(cur_st, True)
            if bool(sc.get("attributes")):
                for k_st in ("status", "build_status"):
                    if k_st in sc["attributes"]:
                        sc["attributes"][k_st] = sc["status"]
        for dep_sid, dep_sc in scopes.items():
            if bool(dep_sc) and dep_sid not in visited:
                if sid in (dep_sc.get("depends_on") or []) or dep_sc.get("parent") == sid:
                    queue.append(dep_sid)

def _partition_rules_for_mitosis(rules: list[dict[str, Any]], k_cap: int = DEFAULT_K_CAP,
    ) -> dict[str, list[dict[str, Any]]]:
    """Partitions `rules` in Spec 22A §2.2 priority order: prefix, `facet`, `target_prop`, or `page_N`."""
    if not rules:
        return {}
    sub_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        tp = str(r.get("target_prop") or "").strip().lower()
        prefix = (tp.split("/")[0].strip() or "general") if "/" in tp else (
            (tp.split("_")[0].strip() or "general") if "_" in tp else (tp or "general"))
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
        prop_groups.setdefault(str(r.get("target_prop") or "general").strip() or "general", []).append(r)
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

def create_empty_graph(k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING,
    store: SQLiteGraphStore | None = None, db_path: str | Path | None = None) -> ContextGraph:
    """Initializes a clean `ContextGraph` / `LatticeGraph` and syncs initial state to SQLite (Spec 22A §2.1)."""
    if isinstance(k_cap, bool) or not isinstance(k_cap, int) or k_cap < 1:
        raise ValueError(f"k_cap must be a positive int, got {k_cap!r}")
    if isinstance(max_ring, bool) or not isinstance(max_ring, int) or max_ring < 1:
        raise ValueError(f"max_ring must be a positive int, got {max_ring!r}")
    active_store = store if store is not None else (SQLiteGraphStore(db_path) if db_path is not None else None)
    graph = ContextGraph({"k_cap": k_cap, "max_ring": max_ring}, store=active_store)
    sync_to_sqlite(graph)
    return graph

create_empty_lattice = create_empty_graph

def _validate_scope_rules_and_meta(scopes: dict[str, Any], k_cap: int, errors: list[str],
    ) -> dict[str, tuple[str, dict[str, Any]]]:
    all_rules: dict[str, tuple[str, dict[str, Any]]] = {}
    for sid, scope in scopes.items():
        if not isinstance(scope, dict) or not isinstance(rules := scope.get("rules", []), list):
            errors.append(f"scope {sid!r} must be a dict with rules list")
            continue
        if len(rules) > k_cap:
            errors.append(f"scope {sid!r} has {len(rules)} direct rules, exceeding k_cap={k_cap}")
        scope_meta = scope.get("_meta", {}) if isinstance(scope.get("_meta", {}), dict) else {}
        if not isinstance(scope.get("_meta", {}), dict):
            errors.append(f"scope {sid!r} _meta must be a dict")
        for r in rules:
            if not isinstance(r, dict) or not isinstance(rid := r.get("id"), str) or not rid:
                errors.append(f"scope {sid!r} contains malformed rule {r!r}")
                continue
            if rid in all_rules:
                errors.append(f"duplicate rule id {rid!r} across scopes {all_rules[rid][0]!r} and {sid!r}")
            all_rules[rid] = (sid, r)
            for ex in r.get("exceptions", []):
                if isinstance(ex, str) or not isinstance(ex, dict):
                    raise ValueError(f"rule {rid!r} exceptions entry must be a structured dict, got {ex!r}")
                if not isinstance(ex.get("rule_id"), str) or not ex["rule_id"] or not isinstance(ex.get("note"), str):
                    errors.append(f"rule {rid!r} exception entry missing non-empty 'rule_id' or 'note': {ex!r}")
            mkeys = r.get("meta_keys", [])
            if not isinstance(mkeys, list) or len(mkeys) > MAX_META_SLOTS:
                errors.append(f"rule {rid!r} meta_keys exceeds {MAX_META_SLOTS} slots")
            elif any(len(str(mk).split()) > MAX_META_KEY_WORDS for mk in mkeys):
                errors.append(f"rule {rid!r} meta_key exceeds {MAX_META_KEY_WORDS} words")
            if isinstance(rmeta := scope_meta.get(rid, {}), dict):
                if len(rmeta) > MAX_META_SLOTS:
                    errors.append(f"rule {rid!r} _meta has {len(rmeta)} slots, exceeding {MAX_META_SLOTS}")
                if any(len(str(mv).split()) > MAX_META_SLOT_WORDS for mv in rmeta.values()):
                    errors.append(f"rule {rid!r} _meta slot exceeds {MAX_META_SLOT_WORDS} words")
                if (total_w := sum(len(str(mv).split()) for mv in rmeta.values())) > MAX_META_TOTAL_WORDS:
                    errors.append(f"rule {rid!r} _meta has {total_w} total words, exceeding {MAX_META_TOTAL_WORDS}")
    return all_rules

def _validate_flows_artifacts_and_notes(graph: dict[str, Any], max_ring: int, errors: list[str]) -> set[str]:
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
            if not isinstance(src, str) or not src.strip() or not isinstance(tgt, str) or not tgt.strip():
                errors.append(f"flows[{idx}] must have non-empty 'source' and 'target' strings")
    artifacts = graph.get("lattice_artifacts", [])
    if not isinstance(artifacts, list):
        errors.append("graph['lattice_artifacts'] must be a list")
    else:
        for idx, art in enumerate(artifacts):
            if not art:
                errors.append(f"lattice_artifacts[{idx}] must be a dict")
            elif art.get("kind") not in _ARTIFACT_KINDS:
                errors.append(f"lattice_artifacts[{idx}] kind={art.get('kind')!r} must be SCHEMA/INTERFACE/LAW")
            elif not isinstance(art.get("name"), str) or not art["name"].strip():
                errors.append(f"lattice_artifacts[{idx}] must have a non-empty 'name' str")
    ambient = graph.get("ambient_notes", [])
    if not isinstance(ambient, list):
        errors.append("graph['ambient_notes'] must be a list")
    else:
        if len(ambient) > max_ring:
            errors.append(f"len(ambient_notes)={len(ambient)} exceeds max_ring={max_ring}")
        for idx, note in enumerate(ambient):
            if not isinstance(note, dict) or not isinstance(aturns := note.get("access_turns"), list) or not aturns:
                errors.append(f"ambient_notes[{idx}] must contain non-empty access_turns: list[int]")
            elif not all(isinstance(t, int) and not isinstance(t, bool) for t in aturns):
                errors.append(f"ambient_notes[{idx}] must contain non-empty access_turns: list[int]")
    return flow_ids

def validate_context_graph(graph: dict[str, Any], *, k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING,
    ) -> list[str]:
    """Validates all Spec 22A §2.1 structural, nD Lattice, and thermodynamic invariants on `graph`."""
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
                errors.append(f"rule {rid!r} refs {target_scope!r}, but missing from {target_scope!r}.in_edges")
        for ov_id in r.get("overrides", []):
            if ov_id not in all_rules:
                errors.append(f"rule {rid!r} overrides nonexistent rule {ov_id!r}")
            elif not any(
                ex.get("rule_id") == rid for ex in all_rules[ov_id][1].get("exceptions", [])
            ):
                errors.append(f"rule {rid!r} overrides {ov_id!r}, but {ov_id!r}.exceptions lacks rule_id={rid!r}")
        for ex in [x for x in r.get("exceptions", []) if isinstance(x, dict)]:
            if (ex_rid := ex.get("rule_id")) not in all_rules:
                errors.append(f"rule {rid!r} lists nonexistent exception rule_id={ex_rid!r}")
            elif rid not in all_rules[ex_rid][1].get("overrides", []):
                errors.append(f"rule {rid!r} has exception {ex_rid!r}, but {ex_rid!r}.overrides lacks {rid!r}")
    flow_ids = _validate_flows_artifacts_and_notes(graph, max_ring, errors)
    for sid, scope in [(k, v) for k, v in scopes.items() if isinstance(v, dict)]:
        for in_rid in scope.get("in_edges", []):
            if in_rid in all_rules and sid not in all_rules[in_rid][1].get("refs", []):
                errors.append(f"scope {sid!r}.in_edges contains {in_rid!r}, but {in_rid!r}.refs lacks {sid!r}")
            elif in_rid not in all_rules and in_rid not in scopes and in_rid not in flow_ids:
                errors.append(f"scope {sid!r}.in_edges references nonexistent rule {in_rid!r}")
    return errors

verify_graph_invariants = validate_context_graph
