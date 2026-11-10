"""Spec 20 §1.2 & §8: Write-Time ContextGraph Rule & Edge Mutation Operators."""

from __future__ import annotations

import copy
from typing import Any

from bowthruster.nlp import restore_ascii_dots
from .mitosis import (
    _partition_rules_for_mitosis,
    actr_base_level_activation,
    ensure_scope_node,
    evict_from_ring,
    two_axis_mitosis,
    upsert_ambient_note,
)
from .schema import (
    DEFAULT_K_CAP,
    DEFAULT_MAX_RING,
    RuleEntry,
    ScopeNode,
    _TYPE_TO_FACET,
    clamp_rule_meta,
    tokenize_content_words,
)


def wire_bidirectional_refs(graph: dict[str, Any], rule: dict[str, Any], ref_scopes: list[str]) -> None:
    """Wires `rule['refs']` and target `scope['in_edges']` symmetrically (Spec 20 §1.1)."""
    rid = str(rule["id"])
    rule_scope = str(rule.get("scope", ""))
    scopes = graph.setdefault("scopes", {})

    old_refs = list(rule.get("refs", []))
    for old_target in old_refs:
        if old_target in scopes and old_target not in ref_scopes:
            in_e = scopes[old_target].get("in_edges", [])
            if rid in in_e:
                in_e.remove(rid)

    clean_refs: list[str] = []
    for target in ref_scopes:
        if not target or target == rule_scope:
            continue
        ensure_scope_node(graph, target)
        if target not in clean_refs:
            clean_refs.append(target)
        in_e = scopes[target].setdefault("in_edges", [])
        if rid not in in_e:
            in_e.append(rid)
    rule["refs"] = clean_refs


def wire_lex_specialis_override(
    graph: dict[str, Any],
    exception_rule: dict[str, Any],
    general_rule: dict[str, Any],
    note: str = "conditional exception",
) -> None:
    """Wires bidirectional `overrides` on `exception_rule` and structured `exceptions` on `general_rule` (Spec 20 §1.1)."""
    ex_id = str(exception_rule["id"])
    gen_id = str(general_rule["id"])
    if ex_id == gen_id:
        return
    ov_list = exception_rule.setdefault("overrides", [])
    if gen_id not in ov_list:
        ov_list.append(gen_id)

    ex_list = general_rule.setdefault("exceptions", [])
    note_clean = " ".join(restore_ascii_dots(str(note or "conditional exception")).split()[:15]) or "conditional exception"
    if not any(isinstance(e, dict) and e.get("rule_id") == ex_id for e in ex_list):
        ex_list.append({"rule_id": ex_id, "note": note_clean})


def markovian_overwrite_rule(
    graph: dict[str, Any],
    scope_id: str,
    old_rule_id: str,
    new_rule_data: dict[str, Any],
) -> dict[str, Any]:
    """Mutates `old_rule_id` in place with `new_rule_data`, venting superseded values and bounding `_meta` (Spec 20 §1.2)."""
    scope = ensure_scope_node(graph, scope_id)
    target_rule = next((r for r in scope.get("rules", []) if r.get("id") == old_rule_id), None)
    if target_rule is None:
        raise KeyError(f"rule {old_rule_id!r} not found in scope {scope_id!r}")

    new_text = restore_ascii_dots(str(new_rule_data.get("text", target_rule.get("text", "")))).strip()
    target_rule["text"] = new_text
    if "type" in new_rule_data and new_rule_data["type"]:
        target_rule["type"] = new_rule_data["type"]
    if "facet" in new_rule_data and new_rule_data["facet"]:
        target_rule["facet"] = new_rule_data["facet"]
    if "target_prop" in new_rule_data:
        target_rule["target_prop"] = new_rule_data["target_prop"]
    if "bound_op" in new_rule_data:
        target_rule["bound_op"] = new_rule_data["bound_op"]
    if "bound_val" in new_rule_data:
        target_rule["bound_val"] = new_rule_data["bound_val"]
    if "conditions" in new_rule_data:
        target_rule["conditions"] = list(new_rule_data["conditions"])
    if "is_exception" in new_rule_data:
        target_rule["is_exception"] = bool(new_rule_data["is_exception"])
    if "turn" in new_rule_data:
        target_rule["turn"] = int(new_rule_data["turn"])

    raw_meta = dict(scope.setdefault("_meta", {}).get(old_rule_id, {}))
    incoming_meta = new_rule_data.get("meta")
    if isinstance(incoming_meta, dict):
        if "rationale" in incoming_meta and incoming_meta["rationale"]:
            raw_meta["transition_rationale"] = str(incoming_meta["rationale"])
        for mk, mv in incoming_meta.items():
            if mk == "rationale" and "rationale" in raw_meta:
                continue
            if mk not in ("revision_note",) and mv:
                raw_meta[mk] = str(mv)

    clamped_meta, meta_keys, meta_tokens = clamp_rule_meta(raw_meta)
    if clamped_meta:
        scope["_meta"][old_rule_id] = clamped_meta
    elif old_rule_id in scope.get("_meta", {}):
        del scope["_meta"][old_rule_id]
    target_rule["meta_keys"] = meta_keys
    target_rule["meta_tokens"] = meta_tokens

    if "refs" in new_rule_data and isinstance(new_rule_data["refs"], list):
        wire_bidirectional_refs(graph, target_rule, new_rule_data["refs"])
    return target_rule


def prune_dysfluency_and_upsert_rule(
    graph: dict[str, Any],
    scope_id: str,
    rule_data: dict[str, Any],
) -> dict[str, Any]:
    """Upserts a clean rule into `scopes[scope_id]`, enforcing `_meta` caps and bidirectional `refs` (Spec 20 §1.2)."""
    scope = ensure_scope_node(graph, scope_id)
    rid = str(rule_data.get("id") or f"r_{scope_id}_{len(scope.get('rules', [])) + 1}")
    existing = next((r for r in scope.get("rules", []) if r.get("id") == rid), None)
    if existing is not None:
        return markovian_overwrite_rule(graph, scope_id, rid, rule_data)

    rtype = str(rule_data.get("type", "OBLIGATION"))
    rfacet = rule_data.get("facet") or _TYPE_TO_FACET.get(rtype, "obligations")
    clamped_meta, meta_keys, meta_tokens = clamp_rule_meta(rule_data.get("meta"))
    if not meta_tokens and isinstance(rule_data.get("meta_tokens"), list):
        meta_tokens = [str(x).lower() for x in rule_data["meta_tokens"] if str(x).strip()]
    if not meta_keys and isinstance(rule_data.get("meta_keys"), list):
        meta_keys = [str(x) for x in rule_data["meta_keys"][:3]]

    entry = RuleEntry({
        "id": rid,
        "type": rtype,
        "facet": rfacet,
        "text": restore_ascii_dots(str(rule_data.get("text", ""))).strip(),
        "scope": scope_id,
        "target_prop": rule_data.get("target_prop"),
        "bound_op": rule_data.get("bound_op"),
        "bound_val": rule_data.get("bound_val"),
        "conditions": list(rule_data.get("conditions", [])),
        "is_exception": bool(rule_data.get("is_exception", False)),
        "refs": [],
        "overrides": list(rule_data.get("overrides", [])),
        "exceptions": copy.deepcopy(rule_data.get("exceptions", [])),
        "meta_keys": meta_keys,
        "meta_tokens": meta_tokens,
        "turn": int(rule_data.get("turn", max(1, int(graph.get("turn_index", 1))))),
    })
    for ex in entry["exceptions"]:
        if isinstance(ex, str) or not isinstance(ex, dict):
            raise ValueError(f"exceptions entry must be a dict, got {ex!r}")

    scope.setdefault("rules", []).append(entry)
    if clamped_meta:
        scope.setdefault("_meta", {})[rid] = clamped_meta
    wire_bidirectional_refs(graph, entry, list(rule_data.get("refs", [])))
    return entry


def graduate_task_to_fact(
    graph: dict[str, Any],
    task_id: str,
    current_turn: int = 1,
) -> dict[str, Any]:
    """Graduates a completed task in `graph['active_tasks']` into a persistent `FACT` rule on its scope (Spec 20 §1.2)."""
    tasks = graph.setdefault("active_tasks", [])
    idx = next((i for i, t in enumerate(tasks) if isinstance(t, dict) and t.get("id") == task_id), None)
    if idx is None:
        raise KeyError(f"task {task_id!r} not found in active_tasks")
    task = tasks.pop(idx)
    scope_id = str(task.get("scope") or "global_defaults")
    fact_rule = prune_dysfluency_and_upsert_rule(
        graph,
        scope_id,
        {
            "id": f"fact_{task_id}",
            "type": "FACT",
            "facet": "context_facts",
            "text": restore_ascii_dots(str(task.get("text", ""))).strip(),
            "target_prop": task.get("target_prop"),
            "bound_op": task.get("bound_op"),
            "bound_val": task.get("bound_val"),
            "conditions": [task["condition"]] if task.get("condition") else [],
            "turn": int(current_turn),
        },
    )
    return fact_rule
