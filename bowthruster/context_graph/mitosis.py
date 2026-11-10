"""Spec 20 §1.2 & §8: Scope Creation (`ensure_scope_node`), ACT-R Ambient Ring Eviction (`evict_from_ring`), and Two-Axis Scope Mitosis (`two_axis_mitosis`)."""

from __future__ import annotations

import copy
import math
from typing import Any

from bowthruster.nlp import restore_ascii_dots
from .formatter import DEFAULT_K_CAP, DEFAULT_MAX_RING, _TYPE_TO_FACET
from .schema import ScopeNode


def ensure_scope_node(graph: dict[str, Any], scope_id: str, *, parent: str | None = None) -> dict[str, Any]:
    """Ensures `scope_id` exists in `graph['scopes']` and registers it in `alias_index`."""
    scopes = graph.setdefault("scopes", {})
    alias_idx = graph.setdefault("alias_index", {})
    if scope_id not in scopes:
        scopes[scope_id] = ScopeNode({"id": scope_id, "parent": parent})
    scope = scopes[scope_id]
    if parent is not None:
        if scope.get("parent") is None:
            scope["parent"] = parent
        if parent in scopes and scope_id not in scopes[parent].setdefault("children", []):
            scopes[parent]["children"].append(scope_id)
    alias_idx[scope_id.lower()] = scope_id
    return scope


def actr_base_level_activation(access_turns: list[int], current_turn: int) -> float:
    """Computes ACT-R base-level activation $B_i(t) = \\ln \\sum_{t_k \\in \\text{access\\_turns}} (\\max(1, t - t_k))^{-0.5}$ (Spec 20 §1.2)."""
    if not isinstance(access_turns, list) or not access_turns:
        raise ValueError("access_turns must be a non-empty list[int]")
    total = 0.0
    for tk in access_turns:
        if not isinstance(tk, int) or isinstance(tk, bool):
            raise ValueError(f"access_turns elements must be ints, got {tk!r}")
        dt = max(1, int(current_turn) - tk)
        total += dt ** -0.5
    return math.log(max(total, 1e-12))


def evict_from_ring(
    ring: list[dict[str, Any]],
    current_turn: int,
    max_ring: int = DEFAULT_MAX_RING,
) -> list[dict[str, Any]]:
    """Evicts lowest-activation entries from `ring` using ACT-R decay tie-broken by `(B_i(t), id)` until `len <= max_ring` (Spec 20 §1.2)."""
    if not isinstance(ring, list):
        raise ValueError(f"ring must be a list, got {type(ring).__name__}")
    if not isinstance(current_turn, int) or isinstance(current_turn, bool):
        raise ValueError(f"current_turn must be an int, got {current_turn!r}")
    if not isinstance(max_ring, int) or isinstance(max_ring, bool) or max_ring < 1:
        raise ValueError(f"max_ring must be a positive int, got {max_ring!r}")

    items: list[dict[str, Any]] = []
    for idx, entry in enumerate(ring):
        if not isinstance(entry, dict):
            raise ValueError(f"ring[{idx}] must be a dict, got {type(entry).__name__}")
        note = copy.deepcopy(entry)
        aturns = note.get("access_turns")
        if not isinstance(aturns, list) or not aturns:
            turn_val = int(note.get("turn", current_turn))
            aturns = [turn_val]
            note["access_turns"] = aturns
        note["activation"] = round(actr_base_level_activation(aturns, current_turn), 6)
        note.setdefault("id", f"note_{idx + 1}")
        items.append(note)

    while len(items) > max_ring:
        victim = min(
            items,
            key=lambda n: (
                actr_base_level_activation(n["access_turns"], current_turn),
                str(n.get("id", "")),
            ),
        )
        items.remove(victim)
    return items


def upsert_ambient_note(
    graph: dict[str, Any],
    note: dict[str, Any] | str,
    current_turn: int | None = None,
    max_ring: int = DEFAULT_MAX_RING,
) -> dict[str, Any]:
    """Upserts an ambient note into `graph['ambient_notes']` and bounds the ring via `evict_from_ring` (Spec 20 §1.2)."""
    t = int(current_turn if current_turn is not None else max(1, int(graph.get("turn_index", 1))))
    ring = list(graph.get("ambient_notes", []))
    if isinstance(note, str):
        note_text = restore_ascii_dots(note).strip()
        note_id = f"note_{len(ring) + 1}_{t}"
        note_dict: dict[str, Any] = {"id": note_id, "text": note_text, "turn": t, "access_turns": [t]}
    elif isinstance(note, dict):
        note_dict = copy.deepcopy(note)
        note_dict["text"] = restore_ascii_dots(str(note_dict.get("text", ""))).strip()
        note_dict.setdefault("id", f"note_{len(ring) + 1}_{t}")
        note_dict.setdefault("turn", t)
        aturns = note_dict.get("access_turns")
        if not isinstance(aturns, list) or not aturns:
            note_dict["access_turns"] = [int(note_dict["turn"])]
    else:
        raise ValueError(f"note must be a dict or str, got {type(note).__name__}")

    matched = False
    for existing in ring:
        if existing.get("id") == note_dict["id"] or (
            note_dict["text"] and existing.get("text") == note_dict["text"]
        ):
            at = list(existing.get("access_turns", []))
            if t not in at:
                at.append(t)
            existing["access_turns"] = at
            existing["turn"] = t
            matched = True
            note_dict = existing
            break
    if not matched:
        ring.append(note_dict)

    graph["ambient_notes"] = evict_from_ring(ring, t, max_ring=max_ring)
    return note_dict


def _partition_rules_for_mitosis(rules: list[dict[str, Any]], k_cap: int) -> dict[str, list[dict[str, Any]]]:
    """Partitions `rules` (`len > k_cap`) along Axis 1 (sub-entity prefix), Axis 2 (`facet`), `target_prop`, or balanced pages."""
    sub_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        tp = str(r.get("target_prop") or "")
        prefix = tp.split("/")[0].split("_")[0].strip() if ("/" in tp or "_" in tp) else ""
        sub_groups.setdefault(prefix or "general", []).append(r)
    if 2 <= len(sub_groups) and max(len(g) for g in sub_groups.values()) < len(rules):
        return sub_groups

    facet_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        f_key = str(r.get("facet") or _TYPE_TO_FACET.get(str(r.get("type", "")), "obligations"))
        facet_groups.setdefault(f_key, []).append(r)
    if len(facet_groups) >= 2:
        return facet_groups

    prop_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        p_key = str(r.get("target_prop") or "general")
        prop_groups.setdefault(p_key, []).append(r)
    if len(prop_groups) >= 2:
        return prop_groups

    pages: dict[str, list[dict[str, Any]]] = {}
    for i in range(0, len(rules), k_cap):
        page_num = (i // k_cap) + 1
        pages[f"page_{page_num}"] = rules[i : i + k_cap]
    return pages


def two_axis_mitosis(
    graph: dict[str, Any],
    scope_id: str | int | None = None,
    k_cap: int = DEFAULT_K_CAP,
) -> dict[str, Any]:
    """Recursively splits any scope in `graph['scopes']` with `len(rules) > k_cap` along Axis 1 (sub-entity) and
    Axis 2 (functional facet), enforcing 100% lossless rule conservation (`count_after == count_before`) (Spec 20 §1.2)."""
    if isinstance(scope_id, int) and not isinstance(scope_id, bool):
        k_cap = scope_id
        scope_id = None
    if not isinstance(k_cap, int) or isinstance(k_cap, bool) or k_cap < 1:
        raise ValueError(f"k_cap must be a positive int, got {k_cap!r}")

    scopes = graph.setdefault("scopes", {})
    count_before = sum(len(s.get("rules", [])) for s in scopes.values() if isinstance(s, dict))
    splits_log: list[dict[str, Any]] = []

    while True:
        over_ids = [
            sid
            for sid, s in list(scopes.items())
            if isinstance(s, dict)
            and len(s.get("rules", [])) > k_cap
            and (scope_id is None or sid == scope_id or sid.startswith(f"{scope_id}::"))
        ]
        if not over_ids:
            break

        for sid in over_ids:
            parent_scope = scopes[sid]
            rules = list(parent_scope.get("rules", []))
            if len(rules) <= k_cap:
                continue
            parent_meta = dict(parent_scope.get("_meta", {}))
            groups = _partition_rules_for_mitosis(rules, k_cap)
            parent_scope["rules"] = []
            parent_scope["_meta"] = {}
            child_ids: list[str] = []

            for g_key, g_rules in groups.items():
                child_id = f"{sid}::{g_key}"
                child_scope = ensure_scope_node(graph, child_id, parent=sid)
                child_ids.append(child_id)
                for r in g_rules:
                    rid = str(r["id"])
                    r["scope"] = child_id
                    if child_id in r.get("refs", []):
                        r["refs"].remove(child_id)
                        if rid in child_scope.get("in_edges", []):
                            child_scope["in_edges"].remove(rid)
                    child_scope.setdefault("rules", []).append(r)
                    if rid in parent_meta:
                        child_scope.setdefault("_meta", {})[rid] = parent_meta[rid]

            splits_log.append({
                "parent_scope": sid,
                "children": child_ids,
                "rule_count": len(rules),
            })

    count_after = sum(len(s.get("rules", [])) for s in scopes.values() if isinstance(s, dict))
    if count_after != count_before:
        raise ValueError(f"two_axis_mitosis rule conservation violated: count_after={count_after} != count_before={count_before}")

    graph["mitosis_splits"] = splits_log
    return {
        "graph": graph,
        "scopes": graph["scopes"],
        "splits": splits_log,
        "mitosis_splits": splits_log,
        "count_before": count_before,
        "count_after": count_after,
    }
