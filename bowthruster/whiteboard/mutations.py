"""Spec 22A §2.2: Write-Time Whiteboard & nD Lattice Component, Flow, Artifact, & Lifecycle Upserts."""

from __future__ import annotations

import copy
from typing import Any

from bowthruster.nlp import restore_ascii_dots
from .rule_mutations import (
    graduate_task_to_fact,
    markovian_overwrite_rule,
    prune_dysfluency_and_upsert_rule,
    replace_rule,
    revoke_rule,
    upsert_ambient_note,
    wire_bidirectional_refs,
    wire_lex_specialis_override,
)
from .schema import (
    ArtifactEntry,
    BUILD_STATUSES,
    DEFAULT_K_CAP,
    DEFAULT_MAX_RING,
    FlowEntry,
    _ARTIFACT_KINDS,
    _clean_opt_str,
    _clean_str_list,
    _ensure_scope_node_nosync,
    _invalidate_scope_on_edit,
    _partition_rules_for_mitosis,
    sync_to_sqlite,
)


def ensure_scope_node(
    graph: dict[str, Any], scope_id: str, *, parent: str | None = None
) -> dict[str, Any]:
    """Ensures `scopes[scope_id]` exists as a `ScopeNode` with SBF defaults and syncs to SQLite."""
    scope = _ensure_scope_node_nosync(graph, scope_id, parent=parent)
    sync_to_sqlite(graph)
    return scope


def upsert_component_spec(
    graph: dict[str, Any],
    scope_id: str,
    *,
    role: str | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    depends_on: list[str] | None = None,
    current_turn: int = 1,
    parent: str | None = None,
    children: list[str] | None = None,
    owner: str | None = None,
    stage: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    files: list[str] | str | None = None,
    tests: list[str] | str | None = None,
) -> dict[str, Any]:
    """Upserts SBF structural/functional fields on `scopes[scope_id]` and syncs to SQLite."""
    scope = _ensure_scope_node_nosync(graph, scope_id, parent=parent)
    sid = str(scope["id"])
    scalar_fields = (
        ("role", role),
        ("owner", owner),
        ("stage", stage),
        ("kind", kind),
        ("status", status),
    )
    for key, val in scalar_fields:
        if val is not None:
            cleaned = _clean_opt_str(val)
            if key == "status" and cleaned:
                scope["status"] = scope["build_status"] = (
                    cleaned.upper() if cleaned.upper() in BUILD_STATUSES else cleaned
                )
            else:
                scope[key] = cleaned
    list_fields = (("inputs", inputs), ("outputs", outputs), ("files", files), ("tests", tests))
    for key, lst in list_fields:
        if lst is not None:
            scope[key] = _clean_str_list(lst)
    for ch_id in (_clean_str_list(children) if children is not None else []):
        if ch_id != sid:
            _ensure_scope_node_nosync(graph, ch_id, parent=sid)
    if depends_on is not None:
        clean_deps = [d for d in _clean_str_list(depends_on) if d != sid]
        for dep in clean_deps:
            if sid not in (in_e := _ensure_scope_node_nosync(graph, dep).setdefault("in_edges", [])):
                in_e.append(sid)
        scope["depends_on"] = clean_deps
    if status is None:
        _invalidate_scope_on_edit(graph, sid)
    scope["updated_turn"] = int(current_turn)
    scope.setdefault("created_turn", int(current_turn))
    sync_to_sqlite(graph)
    return scope


def upsert_flow_edge(
    graph: dict[str, Any],
    source: str,
    target: str,
    *,
    payload: str | None = None,
    action: str | None = None,
    condition: str | None = None,
    current_turn: int = 1,
    step: int | None = None,
    text: str = "",
    dimensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upserts a directed SBF `FlowEntry` in `graph['flows']` without creating a payload scope."""
    valid_ends = (
        isinstance(source, str) and bool(source.strip())
        and isinstance(target, str) and bool(target.strip())
    )
    if not valid_ends:
        raise ValueError(
            f"source and target must be non-empty strings, got source={source!r}, target={target!r}"
        )
    src_sid, tgt_sid = source.strip(), target.strip()
    src_scope = _ensure_scope_node_nosync(graph, src_sid)
    tgt_scope = _ensure_scope_node_nosync(graph, tgt_sid)
    cond_val, pay_val = _clean_opt_str(condition), _clean_opt_str(payload)
    text_val = restore_ascii_dots(str(text or "")).strip()
    fnm = (
        _clean_opt_str((dimensions or {}).get("flow_name"))
        if isinstance(dimensions, dict)
        else None
    )
    flows = graph.setdefault("flows", [])
    entry = next(
        (
            f
            for f in flows
            if (f.get("source"), f.get("target")) == (src_sid, tgt_sid)
            and (
                (pay_val is not None and _clean_opt_str(f.get("payload")) == pay_val)
                or (fnm is not None and _clean_opt_str((f.get("dimensions") or {}).get("flow_name")) == fnm)
                or (pay_val is None and f.get("condition") == cond_val)
            )
        ),
        None,
    )
    if entry is None:
        entry = FlowEntry({
            "id": f"flow_{len(flows) + 1}",
            "kind": "FLOW",
            "source": src_sid,
            "target": tgt_sid,
            "condition": cond_val,
            "created_turn": int(current_turn),
        })
        flows.append(entry)
    elif cond_val is not None:
        entry["condition"] = cond_val
    for key, val in (("payload", payload), ("action", action)):
        if val is not None:
            entry[key] = _clean_opt_str(val)
    if step is not None:
        entry["step"] = int(step)
    if text_val or not entry.get("text"):
        entry["text"] = text_val or f"{src_sid} -> {tgt_sid}"
    if isinstance(dimensions, dict):
        entry.setdefault("dimensions", {}).update(copy.deepcopy(dimensions))
    entry["turn"] = entry["updated_turn"] = int(current_turn)
    if tgt_sid != src_sid:
        for sc, peer in ((src_scope, tgt_sid), (tgt_scope, src_sid)):
            if peer not in (in_e := sc.setdefault("in_edges", [])):
                in_e.append(peer)
    _invalidate_scope_on_edit(graph, src_sid, tgt_sid)
    sync_to_sqlite(graph)
    return entry


def upsert_lattice_artifact(
    graph: dict[str, Any],
    kind: str,
    name: str,
    *,
    scope_id: str = "global_defaults",
    spec: str = "",
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    owner: str | None = None,
    stage: str | None = None,
    dimensions: dict[str, Any] | None = None,
    text: str = "",
    current_turn: int = 1,
) -> dict[str, Any]:
    """Upserts a `SCHEMA` / `INTERFACE` / `LAW` `ArtifactEntry` in `graph['lattice_artifacts']`."""
    kind_clean = str(kind or "").strip().upper()
    if kind_clean not in _ARTIFACT_KINDS:
        raise ValueError(f"artifact kind must be in ('SCHEMA', 'INTERFACE', 'LAW'), got {kind!r}")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"artifact name must be a non-empty str, got {name!r}")
    eff_turn = int(current_turn)
    name_clean = restore_ascii_dots(name).strip()
    sid = str(scope_id or "global_defaults").strip() or "global_defaults"
    _ensure_scope_node_nosync(graph, sid)
    artifacts = graph.setdefault("lattice_artifacts", [])
    entry = next(
        (
            a
            for a in artifacts
            if (
                a.get("kind"),
                a.get("scope_id") or a.get("scope"),
                str(a.get("name", "")).lower(),
            )
            == (kind_clean, sid, name_clean.lower())
        ),
        None,
    )
    if entry is None:
        entry = ArtifactEntry({
            "id": f"{kind_clean.lower()}_{sid}_{name_clean.lower()}",
            "kind": kind_clean,
            "name": name_clean,
            "scope_id": sid,
            "scope": sid,
            "created_turn": eff_turn,
        })
        artifacts.append(entry)
    spec_clean = restore_ascii_dots(str(spec or "")).strip()
    text_clean = restore_ascii_dots(str(text or "")).strip()
    entry["name"] = name_clean
    if spec_clean:
        entry["spec"] = spec_clean
    for key, lst in (("inputs", inputs), ("outputs", outputs)):
        if lst is not None and (lst or not entry.get(key)):
            entry[key] = _clean_str_list(lst)
    for key, val in (("owner", owner), ("stage", stage)):
        if val is not None:
            entry[key] = _clean_opt_str(val)
    if isinstance(dimensions, dict):
        entry.setdefault("dimensions", {}).update(copy.deepcopy(dimensions))
    if text_clean or not entry.get("text"):
        entry["text"] = text_clean or entry.get("spec") or f"{kind_clean} {name_clean}"
    entry["turn"] = entry["updated_turn"] = eff_turn
    _invalidate_scope_on_edit(graph, sid)
    sync_to_sqlite(graph)
    return entry


def _resolve_component_scope(graph: dict[str, Any], scope_id: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(graph, dict):
        raise ValueError("graph must be a dict")
    scopes = graph.setdefault("scopes", {})
    raw_sid = str(scope_id or "").strip()
    sid = raw_sid if raw_sid in scopes else (graph.get("alias_index") or {}).get(raw_sid.lower())
    if not sid or sid not in scopes or not isinstance(scopes[sid], dict):
        raise KeyError(f"Component scope {scope_id!r} not found in graph['scopes']")
    return sid, scopes[sid]


def claim_component_scope(
    graph: dict[str, Any],
    scope_id: str,
    agent_id: str | None = None,
    *,
    release: bool = False,
    force: bool = False,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    """Atomically claims or releases a non-VERIFIED component scope for multi-agent work."""
    sid, scope = _resolve_component_scope(graph, scope_id)
    clean_agent = None if release else _clean_opt_str(agent_id)
    if clean_agent is None or clean_agent.lower() in ("none", "null"):
        scope["claimed_by"] = scope["claimed_at"] = None
    else:
        if str(scope.get("status") or "").upper() == "VERIFIED":
            raise ValueError(f"Cannot claim VERIFIED component {sid!r}; mark it STALE first")
        cur = _clean_opt_str(scope.get("claimed_by"))
        if cur and cur != clean_agent and not force:
            raise ValueError(f"Component {sid!r} is already claimed by {cur!r}")
        scope["claimed_by"] = clean_agent
        if timestamp_utc is not None or not scope.get("claimed_at"):
            scope["claimed_at"] = _clean_opt_str(timestamp_utc)
    sync_to_sqlite(graph)
    return scope


def transition_component_build_status(
    graph: dict[str, Any],
    scope_id: str,
    new_status: str,
    *,
    build_tag: str | None = None,
    core_tag: str | None = None,
    failure_diagnostic: str | None = None,
    current_turn: int = 1,
) -> dict[str, Any]:
    """Transitions `scopes[scope_id]['status']` to `new_status` in `BUILD_STATUSES`."""
    st = str(new_status or "").strip().upper()
    if st not in BUILD_STATUSES:
        raise ValueError(f"Unknown build status {new_status!r}, expected one of {BUILD_STATUSES}")
    _, scope = _resolve_component_scope(graph, scope_id)
    scope["status"] = scope["build_status"] = st
    if st != "VERIFIED":
        scope["verified_untested"] = False
    if "_table_status" in scope:
        scope["_table_status"] = st
    if isinstance(scope.get("attributes"), dict):
        for k_st in ("status", "build_status"):
            if k_st in scope["attributes"]:
                scope["attributes"][k_st] = st
    for key, val in (("build_tag", build_tag), ("core_tag", core_tag)):
        if val is not None:
            scope[key] = _clean_opt_str(val)
    if st == "VERIFIED":
        scope["claimed_by"] = scope["claimed_at"] = None
    if st in ("READY", "IN_PROGRESS", "IN_QA", "VERIFIED"):
        scope["failure_diagnostic"] = None
    elif failure_diagnostic is not None:
        scope["failure_diagnostic"] = _clean_opt_str(failure_diagnostic)
    scope["updated_turn"] = int(current_turn)
    sync_to_sqlite(graph)
    return scope


def two_axis_mitosis(
    graph: dict[str, Any],
    scope_id: str | int | None = None,
    k_cap: int = DEFAULT_K_CAP,
) -> dict[str, Any]:
    """Recursively splits any scope `sid` exceeding `k_cap` rules into child scopes."""
    if isinstance(scope_id, int) and not isinstance(scope_id, bool):
        k_cap, scope_id = scope_id, None
    if isinstance(k_cap, bool) or not isinstance(k_cap, int) or k_cap < 1:
        raise ValueError(f"k_cap must be a positive int, got {k_cap!r}")
    scopes = graph.setdefault("scopes", {})
    count_before = sum(len(s.get("rules", [])) for s in scopes.values())
    splits_log: list[dict[str, Any]] = []
    while True:
        oversized_sids = [
            sid
            for sid, s in list(scopes.items())
            if len(s.get("rules", [])) > k_cap
            and (scope_id is None or sid == scope_id or sid.startswith(f"{scope_id}::"))
        ]
        if not oversized_sids:
            break
        for sid in oversized_sids:
            scope = scopes[sid]
            rules = list(scope.get("rules", []))
            if len(rules) <= k_cap:
                continue
            groups = _partition_rules_for_mitosis(rules, k_cap)
            part_count = sum(len(g) for g in groups.values())
            if part_count != len(rules):
                raise ValueError(
                    f"mitosis_conserves_rule_count violated on {sid!r}: {part_count} != {len(rules)}"
                )
            parent_meta = dict(scope.get("_meta") or {})
            scope["rules"], scope["_meta"] = [], {}
            child_ids: list[str] = []
            for g_key, g_rules in groups.items():
                child_sid = f"{sid}::{g_key}"
                child_ids.append(child_sid)
                child_scope = _ensure_scope_node_nosync(graph, child_sid, parent=sid)
                child_meta = child_scope.setdefault("_meta", {})
                for r in g_rules:
                    rid = str(r.get("id", ""))
                    r["scope"] = child_sid
                    if rid in parent_meta:
                        child_meta[rid] = parent_meta[rid]
                    if child_sid in r.get("refs", []):
                        r["refs"] = [ref for ref in r["refs"] if ref != child_sid]
                        if rid in child_scope.get("in_edges", []):
                            child_scope["in_edges"].remove(rid)
                    child_scope.setdefault("rules", []).append(r)
            splits_log.append({
                "parent_scope": sid, "children": child_ids, "rule_count": len(rules),
            })
    count_after = sum(len(s.get("rules", [])) for s in scopes.values())
    if count_after != count_before:
        raise ValueError(
            f"mitosis_conserves_rule_count violated: {count_after} != {count_before}"
        )
    graph["mitosis_splits"] = splits_log
    sync_to_sqlite(graph)
    return {
        "graph": graph,
        "scopes": graph["scopes"],
        "splits": splits_log,
        "mitosis_splits": splits_log,
        "count_before": count_before,
        "count_after": count_after,
    }


from .frustration import resolve_open_blocker
