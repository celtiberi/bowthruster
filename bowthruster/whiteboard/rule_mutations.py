"""Spec 22A §2.2: Whiteboard Rule Upserts, Lex Specialis Wiring, Ambient Ring, and Mitosis."""

from __future__ import annotations

import copy
from typing import Any

from bowthruster.graph_core import actr_base_level_activation, evict_from_ring
from bowthruster.nlp import restore_ascii_dots
from .schema import (
    DEFAULT_K_CAP,
    DEFAULT_MAX_RING,
    RuleEntry,
    _TYPE_TO_FACET,
    _ensure_scope_node_nosync,
    _invalidate_scope_on_edit,
    _partition_rules_for_mitosis,
    clamp_rule_meta,
    sync_to_sqlite,
)


def wire_bidirectional_refs(
    graph: dict[str, Any], rule: dict[str, Any], ref_scopes: list[str]
) -> None:
    """Wires `rule['refs']` and target `scope['in_edges']` symmetrically and syncs to SQLite."""
    rid, rule_scope = str(rule["id"]), str(rule.get("scope", ""))
    scopes = graph.setdefault("scopes", {})
    for old_target in list(rule.get("refs", [])):
        if old_target in scopes and old_target not in ref_scopes:
            if rid in scopes[old_target].get("in_edges", []):
                scopes[old_target]["in_edges"].remove(rid)
    clean_refs: list[str] = []
    for target in ref_scopes:
        if not target or target == rule_scope:
            continue
        _ensure_scope_node_nosync(graph, target)
        if target not in clean_refs:
            clean_refs.append(target)
        if rid not in (in_e := scopes[target].setdefault("in_edges", [])):
            in_e.append(rid)
    rule["refs"] = clean_refs
    sync_to_sqlite(graph)


def wire_lex_specialis_override(
    graph: dict[str, Any],
    exception_rule: dict[str, Any],
    general_rule: dict[str, Any],
    note: str = "conditional exception",
) -> None:
    """Wires bidirectional `overrides` on `exception_rule` and `exceptions` on `general_rule`."""
    ex_id, gen_id = str(exception_rule["id"]), str(general_rule["id"])
    if ex_id == gen_id:
        return
    if gen_id not in (ov_list := exception_rule.setdefault("overrides", [])):
        ov_list.append(gen_id)
    ex_list = general_rule.setdefault("exceptions", [])
    raw_note = restore_ascii_dots(str(note or "conditional exception")).split()[:15]
    note_clean = " ".join(raw_note) or "conditional exception"
    if not any(isinstance(e, dict) and e.get("rule_id") == ex_id for e in ex_list):
        ex_list.append({"rule_id": ex_id, "note": note_clean})
    sync_to_sqlite(graph)


def markovian_overwrite_rule(
    graph: dict[str, Any],
    scope_id: str,
    old_rule_id: str,
    new_rule_data: dict[str, Any],
) -> dict[str, Any]:
    """Mutates `old_rule_id` in place with `new_rule_data`, venting superseded values."""
    scope = _ensure_scope_node_nosync(graph, scope_id)
    target_rule = next(
        (r for r in scope.get("rules", []) if r.get("id") == old_rule_id), None
    )
    if target_rule is None:
        raise KeyError(f"rule {old_rule_id!r} not found in scope {scope_id!r}")
    raw_txt = new_rule_data.get("text", target_rule.get("text", ""))
    target_rule["text"] = restore_ascii_dots(str(raw_txt)).strip()
    for field in ("type", "facet", "target_prop", "bound_op", "bound_val"):
        nullable = field in ("target_prop", "bound_op", "bound_val")
        if field in new_rule_data and (nullable or new_rule_data[field]):
            target_rule[field] = new_rule_data[field]
    if "conditions" in new_rule_data:
        target_rule["conditions"] = list(new_rule_data["conditions"])
    if "is_exception" in new_rule_data:
        target_rule["is_exception"] = bool(new_rule_data["is_exception"])
    if "turn" in new_rule_data:
        target_rule["turn"] = int(new_rule_data["turn"])
        target_rule["updated_turn"] = int(
            new_rule_data.get("updated_turn", new_rule_data["turn"])
        )
    elif "updated_turn" in new_rule_data:
        target_rule["updated_turn"] = int(new_rule_data["updated_turn"])
    raw_meta = dict(scope.setdefault("_meta", {}).get(old_rule_id, {}))
    if isinstance(incoming_meta := new_rule_data.get("meta"), dict):
        if incoming_meta.get("rationale"):
            raw_meta["transition_rationale"] = str(incoming_meta["rationale"])
        for mk, mv in incoming_meta.items():
            skip_rat = mk == "rationale" and "rationale" in raw_meta
            if not skip_rat and mk != "revision_note" and mv:
                raw_meta[mk] = str(mv)
    clamped_meta, meta_keys, meta_tokens = clamp_rule_meta(raw_meta)
    if clamped_meta:
        scope["_meta"][old_rule_id] = clamped_meta
    else:
        scope.get("_meta", {}).pop(old_rule_id, None)
    target_rule["meta_keys"], target_rule["meta_tokens"] = meta_keys, meta_tokens
    _invalidate_scope_on_edit(graph, scope_id)
    if isinstance(new_rule_data.get("refs"), list):
        wire_bidirectional_refs(graph, target_rule, new_rule_data["refs"])
    else:
        sync_to_sqlite(graph)
    return target_rule


def prune_dysfluency_and_upsert_rule(
    graph: dict[str, Any], scope_id: str, rule_data: dict[str, Any]
) -> dict[str, Any]:
    """Upserts a clean rule into `scopes[scope_id]`, enforcing `_meta` caps and SQLite sync."""
    scope = _ensure_scope_node_nosync(graph, scope_id)
    all_sc = graph.get("scopes") or {}
    conds = list(rule_data.get("conditions") or [])
    tp = rule_data.get("target_prop")
    if not rule_data.get("id") and tp is not None and not rule_data.get("is_exception"):
        match = next(
            (
                (s, r["id"])
                for s, sc in all_sc.items()
                if (s == scope_id or s.startswith(f"{scope_id}::"))
                for r in sc.get("rules", [])
                if r.get("target_prop") == tp
                and not r.get("is_exception")
                and list(r.get("conditions") or []) == conds
                and r.get("id")
            ),
            None,
        )
        if match is not None:
            return markovian_overwrite_rule(graph, match[0], str(match[1]), rule_data)
    all_rids = {
        str(r.get("id"))
        for sc in all_sc.values()
        if sc
        for r in sc.get("rules", [])
        if r.get("id")
    }
    base_cnt = len(scope.get("rules", []))
    rid = str(
        rule_data.get("id")
        or next(
            f"r_{scope_id}_{i}"
            for i in range(base_cnt + 1, len(all_rids) + base_cnt + 5)
            if f"r_{scope_id}_{i}" not in all_rids
        )
    )
    m_sid = next(
        (
            s
            for s, sc in all_sc.items()
            if (s == scope_id or s.startswith(f"{scope_id}::"))
            and any(r.get("id") == rid for r in sc.get("rules", []))
        ),
        None,
    )
    if m_sid:
        return markovian_overwrite_rule(graph, m_sid, rid, rule_data)
    rtype = str(rule_data.get("type", "OBLIGATION"))
    rfacet = next(
        (_v for _v in (rule_data.get("facet"), _TYPE_TO_FACET.get(rtype, "obligations")) if _v),
        None,
    )
    clamped_meta, meta_keys, meta_tokens = clamp_rule_meta(rule_data.get("meta"))
    if not meta_tokens and isinstance(rule_data.get("meta_tokens"), list):
        meta_tokens = [str(x).lower() for x in rule_data["meta_tokens"] if str(x).strip()]
    if not meta_keys and isinstance(rule_data.get("meta_keys"), list):
        meta_keys = [str(x) for x in rule_data["meta_keys"][:3]]
    raw_g_turn = (
        graph.get("turn") if graph.get("turn") is not None else graph.get("turn_index")
    )
    default_t = max(1, int(raw_g_turn if raw_g_turn is not None else 1))
    turn_val = int(rule_data.get("turn", default_t))
    entry = RuleEntry({
        "id": rid,
        "type": rtype,
        "facet": rfacet,
        "scope": scope_id,
        "text": restore_ascii_dots(str(rule_data.get("text", ""))).strip(),
        "target_prop": tp,
        "bound_op": rule_data.get("bound_op"),
        "bound_val": rule_data.get("bound_val"),
        "conditions": conds,
        "is_exception": bool(rule_data.get("is_exception", False)),
        "refs": [],
        "overrides": list(rule_data.get("overrides", [])),
        "exceptions": copy.deepcopy(rule_data.get("exceptions", [])),
        "meta_keys": meta_keys,
        "meta_tokens": meta_tokens,
        "turn": turn_val,
        "created_turn": int(rule_data.get("created_turn", turn_val)),
        "updated_turn": int(rule_data.get("updated_turn", turn_val)),
    })
    if isinstance(rule_data.get("external_links"), list):
        entry["external_links"] = copy.deepcopy(rule_data["external_links"])
    for ex in entry["exceptions"]:
        if isinstance(ex, str) or not isinstance(ex, dict):
            raise ValueError(f"exceptions entry must be a dict, got {ex!r}")
    scope.setdefault("rules", []).append(entry)
    if clamped_meta:
        scope.setdefault("_meta", {})[rid] = clamped_meta
    _invalidate_scope_on_edit(graph, scope_id)
    wire_bidirectional_refs(graph, entry, list(rule_data.get("refs", [])))
    return entry


def revoke_rule(graph: dict[str, Any], rule_id: str) -> dict[str, Any] | None:
    """Revokes `rule_id` (or `attr:<sid>.<prop>`) from `graph`, invalidating its scope."""
    if not isinstance(graph, dict) or not isinstance(rule_id, str) or not rule_id.strip():
        return None
    rid, scopes = rule_id.strip(), (graph.get("scopes") or {})
    if rid.startswith("attr:") and "." in rid[5:]:
        sid, prop = rid[5:].split(".", 1)
        sc = scopes.get(sid)
        if bool((sc or {}).get("attributes")) and prop in sc["attributes"]:
            val = sc["attributes"].pop(prop)
            _invalidate_scope_on_edit(graph, sid)
            sync_to_sqlite(graph)
            return {"id": rid, "scope": sid, "target_prop": prop, "bound_val": val}
        return None
    for sid, sc in scopes.items():
        if not sc:
            continue
        idx = next(
            (i for i, r in enumerate(sc.get("rules", [])) if r.get("id") == rid), None
        )
        if idx is None:
            continue
        removed = sc["rules"].pop(idx)
        if bool(sc.get("_meta")):
            sc["_meta"].pop(rid, None)
        for osc in scopes.values():
            if rid in osc.get("in_edges", []):
                osc["in_edges"].remove(rid)
            for ro in osc.get("rules", []):
                if rid in ro.get("overrides", []):
                    ro["overrides"].remove(rid)
                if isinstance(ro.get("exceptions"), list):
                    ro["exceptions"] = [
                        ex for ex in ro["exceptions"] if ex.get("rule_id") != rid
                    ]
        _invalidate_scope_on_edit(graph, sid)
        sync_to_sqlite(graph)
        return removed
    return None


def replace_rule(
    graph: dict[str, Any],
    old_rule_id: str,
    new_rule_data: dict[str, Any] | None = None,
    *,
    scope_id: str | None = None,
) -> dict[str, Any] | None:
    """Replaces `old_rule_id` with `new_rule_data` (or revokes if `None`)."""
    if new_rule_data is None:
        return revoke_rule(graph, old_rule_id)
    scopes = graph.get("scopes") or {}
    target_sid = scope_id or next(
        (
            sid
            for sid, sc in scopes.items()
            if any(r.get("id") == old_rule_id for r in sc.get("rules", []))
        ),
        str(new_rule_data.get("scope") or "global_defaults"),
    )
    if target_sid in scopes and any(
        r.get("id") == old_rule_id for r in scopes[target_sid].get("rules", [])
    ):
        if new_rule_data.get("id") in (None, "", old_rule_id):
            return markovian_overwrite_rule(graph, target_sid, old_rule_id, new_rule_data)
    revoke_rule(graph, old_rule_id)
    return prune_dysfluency_and_upsert_rule(graph, target_sid, new_rule_data)


def graduate_task_to_fact(
    graph: dict[str, Any], task_id: str, current_turn: int = 1
) -> dict[str, Any]:
    """Graduates a completed task in `graph['active_tasks']` into a persistent `FACT` rule."""
    tasks = graph.setdefault("active_tasks", [])
    idx = next((i for i, t in enumerate(tasks) if t.get("id") == task_id), None)
    if idx is None:
        raise KeyError(f"task {task_id!r} not found in active_tasks")
    task = tasks.pop(idx)
    t_turn = int(current_turn)
    fact_rule = prune_dysfluency_and_upsert_rule(
        graph,
        str(task.get("scope") or "global_defaults"),
        {
            "id": f"fact_{task_id}",
            "type": "FACT",
            "facet": "context_facts",
            "text": restore_ascii_dots(str(task.get("text", ""))).strip(),
            "target_prop": task.get("target_prop"),
            "bound_op": task.get("bound_op"),
            "bound_val": task.get("bound_val"),
            "conditions": [task["condition"]] if task.get("condition") else [],
            "turn": t_turn,
            "created_turn": int(task.get("created_turn", t_turn)),
            "updated_turn": t_turn,
        },
    )
    sync_to_sqlite(graph)
    return fact_rule


def upsert_ambient_note(
    graph: dict[str, Any],
    note: dict[str, Any] | str,
    current_turn: int | None = None,
    max_ring: int = DEFAULT_MAX_RING,
) -> dict[str, Any]:
    """Upserts an ambient note into `graph['ambient_notes']` with `evict_from_ring`."""
    bad_turn = isinstance(current_turn, bool) or (
        current_turn is not None and not isinstance(current_turn, int)
    )
    if bad_turn:
        raise ValueError(f"current_turn must be an int or None, got {current_turn!r}")
    raw_g_turn = (
        graph.get("turn") if graph.get("turn") is not None else graph.get("turn_index")
    )
    default_t = max(1, int(raw_g_turn if raw_g_turn is not None else 1))
    turn_val = int(current_turn if current_turn is not None else default_t)
    if turn_val < 0:
        raise ValueError(f"current_turn must be >= 0, got {turn_val}")
    eff_max_ring = int(
        max_ring if max_ring is not None else graph.get("max_ring", DEFAULT_MAX_RING)
    )
    if eff_max_ring < 1:
        raise ValueError(f"max_ring must be >= 1, got {eff_max_ring}")
    ring = graph.setdefault("ambient_notes", [])
    if isinstance(note, str):
        note_text, note_id, incoming_turns = restore_ascii_dots(note).strip(), None, [turn_val]
    elif isinstance(note, dict):
        note_text = restore_ascii_dots(str(note.get("text", ""))).strip()
        note_id = str(note["id"]) if note.get("id") else None
        raw_turns = note.get("access_turns")
        if isinstance(raw_turns, list):
            if not all(isinstance(t, int) and not isinstance(t, bool) for t in raw_turns):
                raise ValueError(f"access_turns entries must be ints, got {raw_turns!r}")
            incoming_turns = [int(t) for t in raw_turns] or [int(note.get("turn", turn_val))]
            if current_turn is not None and turn_val not in incoming_turns:
                incoming_turns.append(turn_val)
        else:
            incoming_turns = [int(note.get("turn", turn_val))]
    else:
        raise ValueError(f"note must be a dict or str, got {type(note).__name__}")
    existing = next(
        (
            n
            for n in ring
            if n
            and (
                (note_id is not None and n.get("id") == note_id)
                or (note_text and n.get("text") == note_text)
            )
        ),
        None,
    )
    if existing is not None:
        if note_text:
            existing["text"] = note_text
        aturns = sorted(set(list(existing.get("access_turns", [])) + incoming_turns))
        existing["access_turns"], existing["turn"] = aturns, max(aturns)
        target_id = str(existing.get("id") or f"note_{len(ring)}")
        existing["id"] = target_id
    else:
        target_id = note_id or f"note_{len(ring) + 1}"
        aturns = sorted(set(incoming_turns)) or [turn_val]
        ring.append({
            "id": target_id,
            "text": note_text,
            "access_turns": aturns,
            "turn": max(aturns),
        })
    eval_turn = max(
        [turn_val] + [max(n.get("access_turns") or [turn_val]) for n in ring if n]
    )
    evicted_ring = evict_from_ring(ring, eval_turn, max_ring=eff_max_ring)
    for n in evicted_ring:
        if n:
            nturns = list(n.get("access_turns") or [int(n.get("turn", eval_turn))])
            n["access_turns"], n["turn"] = nturns, max(nturns)
            n["activation"] = actr_base_level_activation(nturns, max(eval_turn, n["turn"]))
    graph["ambient_notes"] = evicted_ring
    fallback = (
        evicted_ring[-1]
        if evicted_ring
        else {
            "id": target_id,
            "text": note_text,
            "access_turns": [turn_val],
            "turn": turn_val,
            "activation": 0.0,
        }
    )
    target_entry = next(
        (n for n in evicted_ring if n and n.get("id") == target_id), fallback
    )
    sync_to_sqlite(graph)
    return target_entry
