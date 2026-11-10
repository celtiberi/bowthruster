"""Stage 25B (`table_renderer`): AI Memory & Whiteboard Workspace Synchronization & Stage 2 Foraging (`workspace_sync.py`)."""

from __future__ import annotations
import copy
import importlib
import importlib.util
from typing import Any
from bowthruster.graph_core import token_cost
from .renderer import (
    LOD_0, LOD_1, LOD_2, MAX_FORAGING_STEPS, MAX_PINNED_SLOTS, MAX_SLOT_SUMMARY_WORDS,
    MAX_TABLE_SLOTS, MAX_TABLE_TOKENS, TableLOD, TableSlot, TableView, _LOD_RANK,
    _clamp_summary_words, _extract_raw_slots, _norm_lod, lod_not_demoted,
    render_table_view, should_execute_foraging_step, step_slot_lod_escalation,
)

def _load_core_submod(submod: str) -> Any | None:
    full_name = f"bowthruster.the_table.{submod}"
    try:
        return importlib.import_module(full_name) if importlib.util.find_spec(full_name) is not None else None
    except Exception:
        return None

def _load_ai_memory() -> Any | None:
    try:
        return importlib.import_module("bowthruster.ai_memory") if importlib.util.find_spec("bowthruster.ai_memory") is not None else None
    except Exception:
        return None

def should_admit_table_slot(within_16_slots: bool, within_1200_tokens: bool) -> bool:
    """Stage 25A Law 1 (`table_token_budget_conservation_law`): returns `True` iff `within_16_slots and within_1200_tokens`."""
    fn = getattr(_load_core_submod("schema"), "should_admit_table_slot", None)
    return bool(fn(within_16_slots, within_1200_tokens)) if callable(fn) else (bool(within_16_slots) and bool(within_1200_tokens))

def can_evict_table_slot(is_pinned: bool, is_over_capacity: bool, within_6_pinned_cap: bool = True) -> bool:
    """Stage 25A Law 2 (`pinned_slot_eviction_immunity_law`): pinned slots within the 6-pinned cap are immune to eviction."""
    fn = getattr(_load_core_submod("schema"), "can_evict_table_slot", None)
    if callable(fn):
        return bool(fn(is_pinned, is_over_capacity, within_6_pinned_cap))
    return False if (bool(is_pinned) and bool(within_6_pinned_cap)) else bool(is_over_capacity)

def should_evict_first_over_second(first_pinned: bool, second_pinned: bool, first_lower_priority: bool) -> bool:
    """Stage 25A Law 3 (`slot_eviction_order_law`): unpinned slots always evict before pinned slots; ties follow priority."""
    fn = getattr(_load_core_submod("surface"), "should_evict_first_over_second", None)
    if callable(fn):
        return bool(fn(first_pinned, second_pinned, first_lower_priority))
    return (not bool(first_pinned)) if (bool(first_pinned) != bool(second_pinned)) else bool(first_lower_priority)

def _validate_int_bound(val: Any, low: int, high: int, label: str) -> int:
    if isinstance(val, bool) or not isinstance(val, int) or not (low <= val <= high):
        raise ValueError(f"{label} must be an int in [{low}, {high}], got {val!r}")
    return int(val)

def _validate_caps(
    max_table_slots: int = MAX_TABLE_SLOTS, max_table_tokens: int = MAX_TABLE_TOKENS,
    max_pinned_slots: int = MAX_PINNED_SLOTS, max_summary_words: int = MAX_SLOT_SUMMARY_WORDS,
) -> None:
    _validate_int_bound(max_table_slots, 1, MAX_TABLE_SLOTS, "max_table_slots")
    _validate_int_bound(max_table_tokens, 1, MAX_TABLE_TOKENS, "max_table_tokens")
    _validate_int_bound(max_pinned_slots, 0, MAX_PINNED_SLOTS, "max_pinned_slots")
    _validate_int_bound(max_summary_words, 1, MAX_SLOT_SUMMARY_WORDS, "slot_summary_words")

def create_empty_table_state(
    *, max_table_slots: int = MAX_TABLE_SLOTS, max_table_tokens: int = MAX_TABLE_TOKENS,
    max_pinned_slots: int = MAX_PINNED_SLOTS, turn_index: int = 1,
) -> dict[str, Any]:
    """Creates a clean bounded `table_state` dictionary (`max_table_slots <= 16`, `max_table_tokens <= 1200`, `max_pinned_slots <= 6`)."""
    _validate_caps(max_table_slots, max_table_tokens, max_pinned_slots)
    for mod_name in ("schema", "surface"):
        mod = _load_core_submod(mod_name)
        for fn_name in ("create_empty_table_state", "create_table_state"):
            if callable(fn := getattr(mod, fn_name, None)):
                for kw in (
                    {
                        "max_table_slots": max_table_slots,
                        "max_table_tokens": max_table_tokens,
                        "max_pinned_slots": max_pinned_slots
                    },
                    {
                        "max_slots": max_table_slots, "max_tokens": max_table_tokens,
                        "max_pinned_slots": max_pinned_slots
                    },
                ):
                    try:
                        if isinstance(state := fn(**kw), dict):
                            for k, v in (
                                ("slots", []), ("active_slots", state.get("slots", [])),
                                ("total_tokens", 0), ("max_table_slots", int(max_table_slots)),
                                ("max_table_tokens", int(max_table_tokens)),
                                ("max_pinned_slots", int(max_pinned_slots)),
                                ("turn_index", int(turn_index)), ("foraging_history", [])
                            ):
                                state.setdefault(k, v)
                            return state
                    except TypeError:
                        continue
    slots_list: list[TableSlot] = []
    return {
        "slots": slots_list, "active_slots": slots_list,
        "total_tokens": 0, "max_table_slots": int(max_table_slots),
        "max_table_tokens": int(max_table_tokens),
        "max_pinned_slots": int(max_pinned_slots),
        "turn_index": int(turn_index), "compact_view": "",
        "foraging_history": []
    }

def _find_slot_index(slots: list[dict[str, Any]], sid_or_cid: str) -> int | None:
    clean = str(sid_or_cid or "").strip()
    if not clean:
        return None
    alt = clean[5:] if clean.startswith("slot_") else f"slot_{clean}"
    for idx, s in enumerate(slots):
        pair = (str(s.get("slot_id") or "").strip(), str(s.get("concept_id") or "").strip())
        if clean in pair or alt in pair:
            return idx
    return None

def _set_slot_pinned(
    table_state: dict[str, Any], slot_id: str,
    pinned: bool, max_pinned_slots: int = MAX_PINNED_SLOTS
) -> TableSlot:
    if not isinstance(table_state, dict):
        raise ValueError(f"table_state must be a dict, got {type(table_state).__name__}")
    if not isinstance(slot_id, str) or not slot_id.strip():
        raise ValueError(f"slot_id must be a non-empty str, got {slot_id!r}")
    _validate_int_bound(max_pinned_slots, 1, MAX_PINNED_SLOTS, "max_pinned_slots")
    _, raw_list = _extract_raw_slots(table_state)
    target_idx = _find_slot_index(raw_list, slot_id)
    if target_idx is None:
        raise KeyError(f"TableSlot {slot_id!r} not found in table_state")
    if pinned:
        eff_pin_cap = min(
            int(table_state.get("max_pinned_slots", max_pinned_slots) or max_pinned_slots),
            max_pinned_slots
        )
        if sum(1 for i, s in enumerate(raw_list) if i != target_idx and bool(s.get("pinned"))) >= eff_pin_cap:
            raise ValueError(f"max_pinned_slots ({eff_pin_cap}) exceeded when pinning {slot_id!r}")
    raw_list[target_idx]["pinned"] = bool(pinned)
    updated = TableSlot(raw_list[target_idx])
    raw_list[target_idx] = updated
    table_state["slots"] = table_state["active_slots"] = raw_list
    updated._table_state = table_state
    return updated

def pin_to_table(table_state: dict[str, Any], slot_id: str, *, max_pinned_slots: int = MAX_PINNED_SLOTS) -> TableSlot:
    """Pins `slot_id` in `table_state`, enforcing `max_pinned_slots <= 6` (`r_table_surface_3`)."""
    return _set_slot_pinned(table_state, slot_id, True, max_pinned_slots=max_pinned_slots)

def unpin_from_table(table_state: dict[str, Any], slot_id: str) -> TableSlot:
    """Unpins `slot_id` in `table_state`."""
    return _set_slot_pinned(table_state, str(slot_id or " "), False)

def _slot_priority_key(slot: dict[str, Any], idx: int) -> tuple[bool, float, int, int]:
    return (
        bool(slot.get("pinned", False)), float(slot.get("activation", 0.0) or 0.0),
        int(slot.get("updated_turn", 0) or 0),
        -idx
    )

def _evict_until_fits(
    slots: list[TableSlot], needed_slots: int,
    needed_tokens: int, max_slots: int,
    max_tokens: int, max_pinned: int
) -> list[TableSlot]:
    """Evicts lowest-priority unpinned slots using `can_evict_table_slot` and `should_evict_first_over_second`."""
    working = list(slots)
    while working:
        total_tok = sum(int(s["token_cost"]) for s in working)
        if not ((len(working) + needed_slots > max_slots) or (total_tok + needed_tokens > max_tokens)):
            break
        within_pin_cap = sum(1 for s in working if bool(s.get("pinned"))) <= max_pinned
        victim_idx: int | None = None
        for idx, cand in enumerate(working):
            if not can_evict_table_slot(bool(cand.get("pinned")), True, within_pin_cap):
                continue
            if victim_idx is None or should_evict_first_over_second(
                bool(cand.get("pinned")), bool(working[victim_idx].get("pinned")),
                _slot_priority_key(cand, idx) < _slot_priority_key(working[victim_idx], victim_idx),
            ):
                victim_idx = idx
        if victim_idx is None:
            break
        working.pop(victim_idx)
    return working

def _build_candidate_slot(
    item: dict[str, Any], default_cid: str,
    current_turn: int, max_summary_words: int,
    *, source: str | None = None
) -> TableSlot:
    cid = str(
        item.get("concept_id") or item.get("target_id")
        or item.get("id") or item.get("uri")
        or item.get("query") or default_cid
    ).strip()
    if cid.startswith("slot_") and not item.get("concept_id"):
        cid = cid[5:] or cid
    sid = str(item.get("slot_id") or (cid if cid.startswith("slot_") else f"slot_{cid}")).strip()
    syn_val = item.get("lod1_synopsis") if item.get("lod1_synopsis") is not None else (
        item.get("summary") if item.get("summary") is not None else (
            item.get("text") if item.get("text") is not None else item.get("canonical_text", "")
        )
    )
    raw_syn = str(syn_val).strip()
    clamped_syn = _clamp_summary_words(raw_syn, max_summary_words)
    lod_val = _norm_lod(item.get("target_lod") or item.get("lod_tier") or item.get("lod") or LOD_1).value
    raw_cost = item.get("token_cost", item.get("cost"))
    return TableSlot(
        slot_id=sid, concept_id=cid, lod_tier=lod_val, pinned=bool(item.get("pinned", False)),
        token_cost_val=raw_cost if isinstance(raw_cost, int) and not isinstance(raw_cost, bool) else None,
        summary=clamped_syn, lod1_synopsis=clamped_syn, external_links=item.get("external_links") if isinstance(item.get("external_links"), list) else [],
        activation=float(item.get("activation", 1.0) or 0.0), hop_distance=int(item.get("hop_distance", item.get("hop", 0)) or 0),
        updated_turn=int(
            item.get("updated_turn", current_turn)
            or current_turn
        ), max_summary_words=max_summary_words, **({"source": source} if source else {}),
    )

def _extract_context_view_candidates(
    context_view: dict[str, Any] | None,
    current_turn: int, max_summary_words: int
) -> list[TableSlot]:
    if not isinstance(context_view, dict):
        return []
    candidates: list[TableSlot] = []
    seen_cids: set[str] = set()
    for raw_slot in (context_view.get("slots") if isinstance(context_view.get("slots"), list) else []):
        if isinstance(raw_slot, dict):
            slot = TableSlot(raw_slot, updated_turn=current_turn, max_summary_words=max_summary_words)
            if slot["concept_id"] not in seen_cids:
                seen_cids.add(slot["concept_id"])
                candidates.append(slot)
    for rule in (context_view.get("active_rules") if isinstance(context_view.get("active_rules"), list) else []):
        if isinstance(rule, dict):
            cid = str(rule.get("scope") or rule.get("id") or "").strip()
            if cid and cid != "global_defaults" and cid not in seen_cids:
                seen_cids.add(cid)
                candidates.append(TableSlot(
                    slot_id=f"slot_{cid}", concept_id=cid, lod_tier=LOD_1, pinned=False,
                    summary=_clamp_summary_words(str(rule.get("text") or cid), max_summary_words),
                    activation=float(rule.get("score", rule.get("activation", 0.5)) or 0.5),
                    updated_turn=int(rule.get("updated_turn", current_turn) or current_turn),
                    max_summary_words=max_summary_words, source="whiteboard",
                ))
    return candidates

def _admit_or_merge_slots(
    working_slots: list[TableSlot], incoming_slots: list[TableSlot],
    eff_max_slots: int, eff_max_tokens: int,
    eff_max_pinned: int, max_summary_words: int
) -> list[TableSlot]:
    for cand in incoming_slots:
        existing_idx = _find_slot_index(working_slots, cand["concept_id"])
        if existing_idx is not None:
            prev_slot = working_slots[existing_idx]
            keep_higher = _LOD_RANK[_norm_lod(prev_slot["lod_tier"])] > _LOD_RANK[_norm_lod(cand["lod_tier"])]
            merged_lod = prev_slot["lod_tier"] if keep_higher else cand["lod_tier"]
            pinned_now = bool(prev_slot.get("pinned")) or bool(cand.get("pinned"))
            other_pinned = sum(1 for i, s in enumerate(working_slots) if i != existing_idx and bool(s.get("pinned")))
            if pinned_now and other_pinned >= eff_max_pinned and not bool(prev_slot.get("pinned")):
                pinned_now = False
            merged_links = prev_slot.get("external_links", []) if (keep_higher or not cand.get("external_links")) else cand["external_links"]
            eff_cand = {
                **cand, "summary": prev_slot["summary"],
                "lod1_synopsis": prev_slot["summary"],
                "token_cost": prev_slot["token_cost"],
                "external_links": merged_links
            } if keep_higher else {**cand, "external_links": merged_links}
            other_tokens = sum(int(s["token_cost"]) for i, s in enumerate(working_slots) if i != existing_idx)
            if other_tokens + int(eff_cand["token_cost"]) > eff_max_tokens:
                others = _evict_until_fits(
                    [s for i, s in enumerate(working_slots) if i != existing_idx],
                    1, int(eff_cand["token_cost"]), eff_max_slots,
                    eff_max_tokens, eff_max_pinned
                )
                if sum(int(s["token_cost"]) for s in others) + int(eff_cand["token_cost"]) <= eff_max_tokens:
                    working_slots, existing_idx = others, None
                else:
                    continue
            updated = TableSlot(
                {
                    **prev_slot, **eff_cand, "slot_id": prev_slot["slot_id"],
                    "concept_id": prev_slot["concept_id"],
                    "lod_tier": merged_lod, "pinned": pinned_now
                },
                max_summary_words=max_summary_words
            )
            if existing_idx is not None:
                working_slots[existing_idx] = updated
            else:
                working_slots.append(updated)
        else:
            if bool(cand.get("pinned")) and sum(1 for s in working_slots if bool(s.get("pinned"))) >= eff_max_pinned:
                cand["pinned"] = False
            working_slots = _evict_until_fits(
                working_slots, 1, int(cand["token_cost"]),
                eff_max_slots, eff_max_tokens, eff_max_pinned
            )
            if should_admit_table_slot(
                (len(working_slots) + 1) <= eff_max_slots,
                (sum(int(s["token_cost"]) for s in working_slots) + int(cand["token_cost"])) <= eff_max_tokens
            ):
                working_slots.append(cand)
    return working_slots

def sync_memory_to_table(
    table_state: dict[str, Any] | None, recalled_concepts: list[dict[str, Any]] | None,
    context_view: dict[str, Any] | None = None, *, current_turn: int = 1,
    max_table_slots: int = MAX_TABLE_SLOTS, max_table_tokens: int = MAX_TABLE_TOKENS,
    max_pinned_slots: int = MAX_PINNED_SLOTS, max_summary_words: int = MAX_SLOT_SUMMARY_WORDS,
) -> TableView:
    """Hydrates recalled `ConceptFrame` records from `ai_memory` (and optional active `ContextView` items from `whiteboard`)
    into bounded `TableSlot` entries (`max_table_slots <= 16`, `max_table_tokens <= 1200`, `slot_summary_words <= 50`)
    and returns the rendered `TableView` dict."""
    if recalled_concepts is not None and not isinstance(recalled_concepts, (list, tuple)):
        raise ValueError(f"recalled_concepts must be a list or None, got {type(recalled_concepts).__name__}")
    if context_view is not None and not isinstance(context_view, dict):
        raise ValueError(f"context_view must be a dict or None, got {type(context_view).__name__}")
    _validate_caps(max_table_slots, max_table_tokens, max_pinned_slots, max_summary_words)
    if table_state is None:
        state = create_empty_table_state(
            max_table_slots=max_table_slots, max_table_tokens=max_table_tokens,
            max_pinned_slots=max_pinned_slots,
            turn_index=current_turn
        )
    elif isinstance(table_state, dict):
        state = table_state
    else:
        raise ValueError(f"table_state must be a dict or None, got {type(table_state).__name__}")
    eff_max_slots = min(
        int(state.get("max_table_slots", state.get("max_slots", max_table_slots)) or max_table_slots),
        max_table_slots
    )
    eff_max_tokens = min(
        int(state.get("max_table_tokens", state.get("max_tokens", max_table_tokens)) or max_table_tokens),
        max_table_tokens
    )
    eff_max_pinned = min(int(state.get("max_pinned_slots", max_pinned_slots) or max_pinned_slots), max_pinned_slots)
    _, existing_raw = _extract_raw_slots(state)
    working_slots: list[TableSlot] = [TableSlot(s, max_summary_words=max_summary_words) for s in existing_raw]
    incoming_slots: list[TableSlot] = []
    for idx, item in enumerate(recalled_concepts or []):
        if not isinstance(item, dict):
            raise ValueError(f"recalled_concepts[{idx}] must be a dict, got {type(item).__name__}")
        incoming_slots.append(_build_candidate_slot(item, f"concept_{idx + 1}", current_turn, max_summary_words, source="ai_memory"))
    incoming_slots.extend(_extract_context_view_candidates(context_view, current_turn, max_summary_words))
    working_slots = _admit_or_merge_slots(
        working_slots, incoming_slots, eff_max_slots,
        eff_max_tokens, eff_max_pinned, max_summary_words
    )
    state["slots"] = state["active_slots"] = working_slots
    state["max_table_slots"], state["max_table_tokens"], state["max_pinned_slots"], state["turn_index"] = eff_max_slots, eff_max_tokens, eff_max_pinned, int(current_turn)
    view = render_table_view(
        state, max_summary_words=max_summary_words,
        max_table_slots=eff_max_slots, max_table_tokens=eff_max_tokens
    )
    view["table_state"] = state
    return view

def _enrich_slot_from_graph_and_links(
    cid: str, cur_slot: dict[str, Any],
    d: dict[str, Any], whiteboard_graph: dict[str, Any] | None,
    store: Any, max_summary_words: int
) -> tuple[str, list[Any], bool]:
    resolve_fn = getattr(_load_ai_memory(), "resolve_external_links", None)
    links: list[Any] = copy.deepcopy(d["external_links"]) if isinstance(d.get("external_links"), list) else copy.deepcopy(cur_slot.get("external_links", []))
    source = whiteboard_graph if isinstance(whiteboard_graph, dict) else (store if store is not None else cur_slot)
    if callable(resolve_fn) and source is not None and cid:
        try:
            if isinstance(resolved := resolve_fn(source, cid, LOD_2), list) and resolved:
                seen = {str(lk.get("target_uri") or lk.get("uri") or lk) for lk in links if lk}
                for rlk in resolved:
                    u = str(rlk.get("target_uri") or rlk.get("uri") or "") if isinstance(rlk, dict) else str(rlk)
                    if u and u not in seen:
                        seen.add(u)
                        links.append(dict(rlk) if isinstance(rlk, dict) else {"target_uri": u, "uri": u, "lod_tier": LOD_2})
        except Exception:
            pass
    target_uri = str(d.get("target_uri") or d.get("uri") or "").strip()
    if target_uri and target_uri not in {str(lk.get("target_uri") or lk.get("uri") or lk) for lk in links if lk}:
        links.append({"link_id": f"link_{cid}_{len(links) + 1}", "target_uri": target_uri, "uri": target_uri, "artifact_kind": "doc", "lod_tier": LOD_2})
    if any(k in d and d[k] is not None for k in ("summary", "lod1_synopsis", "synopsis", "text", "content")):
        raw_sum = str(
            d.get("summary") or d.get("lod1_synopsis")
            or d.get("synopsis") or d.get("text")
            or d.get("content") or ""
        ).strip()
        return _clamp_summary_words(raw_sum, max_summary_words), links, (len(raw_sum.split()) <= max_summary_words)
    parts: list[str] = []
    base_sum = str(cur_slot.get("summary") or cur_slot.get("lod1_synopsis") or "").strip()
    if base_sum and base_sum != cid:
        parts.append(base_sum)
    if isinstance(whiteboard_graph, dict) and isinstance((sc := (whiteboard_graph.get("scopes") or {}).get(cid)), dict):
        if (role := str(sc.get("role") or (sc.get("attributes") or {}).get("lod1_synopsis") or "").strip()) and role not in parts:
            parts.append(role)
        for r in (sc.get("rules") if isinstance(sc.get("rules"), list) else [])[:3]:
            if isinstance(r, dict) and r.get("text") and (rt := str(r["text"]).strip()) not in parts:
                parts.append(rt)
    if target_uri and target_uri not in " ".join(parts):
        parts.append(f"[URI: {target_uri}]")
    return _clamp_summary_words(" | ".join(parts) if parts else (base_sum or cid), max_summary_words), links, True

def _expand_neighbors_for_concept(
    cid: str, whiteboard_graph: dict[str, Any] | None,
    store: Any
) -> list[dict[str, Any]]:
    ai_mem = _load_ai_memory()
    source = whiteboard_graph if isinstance(whiteboard_graph, dict) else store
    if source is None or not cid:
        return []
    for fn_name in ("expand_concept_neighbors", "recall_concepts"):
        if callable(fn := getattr(ai_mem, fn_name, None)):
            for seed_arg in ([cid], cid, [{"concept_id": cid, "cue_text": cid, "salience_score": 1.0}]):
                try:
                    if isinstance(res := fn(source, seed_arg), list) and res:
                        return [r for r in res if isinstance(r, dict)]
                except Exception:
                    continue
    return []

def _normalize_foraging_directives(foraging_directives: list[Any] | dict[str, Any] | str | None) -> tuple[list[dict[str, Any]], int | None]:
    if foraging_directives is None:
        return [], None
    if isinstance(foraging_directives, str):
        return (
            [{"concept_id": foraging_directives.strip(), "slot_id": foraging_directives.strip()}]
            if foraging_directives.strip() else []
        ), None
    if isinstance(foraging_directives, (list, tuple)):
        out: list[dict[str, Any]] = []
        for idx, item in enumerate(foraging_directives):
            if isinstance(item, str) and item.strip():
                out.append({"concept_id": item.strip(), "slot_id": item.strip()})
            elif isinstance(item, dict):
                out.append(dict(item))
            else:
                raise ValueError(f"foraging_directives[{idx}] must be a dict or non-empty str, got {type(item).__name__}")
        return out, None
    if isinstance(foraging_directives, dict):
        dir_cap = foraging_directives.get("max_foraging_steps")
        for key in ("directives", "foraging_directives", "steps", "actions", "items"):
            if key in foraging_directives:
                raw_sub = foraging_directives[key]
                if not isinstance(raw_sub, (list, tuple)):
                    raise ValueError(f"foraging_directives[{key!r}] must be a list, got {type(raw_sub).__name__}")
                return _normalize_foraging_directives(list(raw_sub))[0], dir_cap
        if foraging_directives.get("foraging_requested") is False and not any(k in foraging_directives for k in (
            "slot_id", "concept_id", "id",
            "action", "op", "target_lod",
            "lod_tier"
        )):
            return [], dir_cap
        return [dict(foraging_directives)], dir_cap
    raise ValueError(f"foraging_directives must be a list, dict, str, or None, got {type(foraging_directives).__name__}")

def _record_forage_action(
    rec: dict[str, Any], applied: list[dict[str, Any]],
    history: list[dict[str, Any]], slot_ids: list[str],
    sid: str | None = None
) -> None:
    applied.append(rec)
    history.append(copy.deepcopy(rec))
    if sid:
        slot_ids.append(sid)

def forage_table_context(
    table_state: dict[str, Any] | list[Any] | None = None,
    foraging_directives: list[Any] | dict[str, Any] | str | None = None,
    *, whiteboard_graph: dict[str, Any] | None = None, store: Any = None, current_turn: int = 1,
    max_foraging_steps: int = 4, max_table_slots: int = MAX_TABLE_SLOTS,
    max_table_tokens: int = MAX_TABLE_TOKENS, max_pinned_slots: int = MAX_PINNED_SLOTS,
    max_summary_words: int = MAX_SLOT_SUMMARY_WORDS, memory_index: Any | None = None,
    concept_lookup: dict[str, Any] | None = None,
) -> TableView:
    """Stage 2 Active Prefrontal Foraging (`forage_table_context`, Bend 2 Law 2 `stage2_foraging_budget_law`):
    Executes bounded, budget-gated foraging directives (`ESCALATE_LOD`/`INSPECT_URI`, `EXPAND_NEIGHBORS`,
    `PIN_SLOT`, `UNPIN_SLOT`, `EVICT_SLOT`) via `should_execute_foraging_step` and `step_slot_lod_escalation`,
    returning the updated `TableView` with `foraging_actions_applied` and `table_state`."""
    _validate_caps(max_table_slots, max_table_tokens, max_pinned_slots, max_summary_words)
    _validate_int_bound(max_foraging_steps, 0, MAX_FORAGING_STEPS, "max_foraging_steps")
    directives, dir_step_cap = _normalize_foraging_directives(foraging_directives)
    if dir_step_cap is not None:
        max_foraging_steps = min(max_foraging_steps, _validate_int_bound(dir_step_cap, 0, MAX_FORAGING_STEPS, "max_foraging_steps"))
    if table_state is None:
        state = create_empty_table_state(
            max_table_slots=max_table_slots, max_table_tokens=max_table_tokens,
            max_pinned_slots=max_pinned_slots,
            turn_index=current_turn
        )
        raw_list: list[dict[str, Any]] = []
    else:
        state, raw_list = _extract_raw_slots(table_state)
    if "max_foraging_steps" in state and state["max_foraging_steps"] is not None:
        max_foraging_steps = min(max_foraging_steps, _validate_int_bound(state["max_foraging_steps"], 0, MAX_FORAGING_STEPS, "max_foraging_steps"))
    eff_max_slots = min(
        int(state.get("max_table_slots", state.get("max_slots", max_table_slots)) or max_table_slots),
        max_table_slots
    )
    eff_max_tokens = min(
        int(state.get("max_table_tokens", state.get("max_tokens", max_table_tokens)) or max_table_tokens),
        max_table_tokens
    )
    eff_max_pinned = min(int(state.get("max_pinned_slots", max_pinned_slots) or max_pinned_slots), max_pinned_slots)
    working_slots: list[TableSlot] = [TableSlot(s, max_summary_words=max_summary_words) for s in raw_list]
    state["slots"] = state["active_slots"] = working_slots
    history_list = state.setdefault("foraging_history", []) if isinstance(state.get("foraging_history"), list) else []
    state["foraging_history"] = history_list
    steps_executed = 0
    foraged_slot_ids: list[str] = []
    foraging_actions_applied: list[dict[str, Any]] = []

    for d in directives:
        action = str(d.get("action") or d.get("op") or d.get("type") or d.get("directive") or "").strip().upper()
        req = (
            bool(d.get("foraging_requested", d.get("requested", d.get("enabled", True))))
            and not (bool(d.get("skip", False)) or action in ("NONE", "NOOP", "SKIP"))
        )
        steps_under_max = steps_executed < max_foraging_steps
        explicit_budget_ok = bool(d.get("budget_ok", state.get("budget_ok", True)))
        raw_sid = str(d.get("slot_id") or "").strip()
        raw_cid = str(
            d.get("concept_id") or d.get("target_id")
            or d.get("id") or d.get("query") or (raw_sid[5:] if raw_sid.startswith("slot_") else raw_sid)
        ).strip()
        if isinstance(concept_lookup, dict) and raw_cid in concept_lookup and isinstance(concept_lookup[raw_cid], dict):
            d = {**concept_lookup[raw_cid], **d}
        elif memory_index is not None and callable(getattr(memory_index, "get_concept", None)) and raw_cid:
            if isinstance(found := memory_index.get_concept(raw_cid), dict):
                d = {**found, **d}
        match_idx = _find_slot_index(working_slots, raw_sid or raw_cid)

        if action == "EXPAND_NEIGHBORS":
            neighbors = _expand_neighbors_for_concept(raw_cid or raw_sid, whiteboard_graph, store or memory_index)
            if should_execute_foraging_step(req, steps_under_max, explicit_budget_ok and bool(neighbors)):
                state["slots"] = state["active_slots"] = working_slots
                sync_memory_to_table(
                    state, neighbors, current_turn=current_turn,
                    max_table_slots=eff_max_slots, max_table_tokens=eff_max_tokens,
                    max_pinned_slots=eff_max_pinned, max_summary_words=max_summary_words
                )
                working_slots = list(state.get("slots", []))
                steps_executed += 1
                _record_forage_action(
                    {
                        "action": "EXPAND_NEIGHBORS", "concept_id": raw_cid
                        or raw_sid, "expanded_count": len(neighbors),
                        "turn": int(current_turn)
                    },
                    foraging_actions_applied, history_list,
                    foraged_slot_ids
                )
                for nb in neighbors:
                    if isinstance(nb, dict) and nb.get("concept_id"):
                        foraged_slot_ids.append(f"slot_{nb['concept_id']}")
            continue

        if action in ("PIN", "PIN_SLOT", "UNPIN", "UNPIN_SLOT"):
            want_pin = action in ("PIN", "PIN_SLOT")
            other_pinned = sum(1 for i, s in enumerate(working_slots) if i != match_idx and bool(s.get("pinned")))
            pin_ok = (other_pinned < eff_max_pinned) if want_pin else True
            if should_execute_foraging_step(
                req, steps_under_max, explicit_budget_ok
                and (match_idx is not None) and pin_ok
            ) and match_idx is not None:
                state["slots"] = state["active_slots"] = working_slots
                updated = _set_slot_pinned(
                    state, working_slots[match_idx]["slot_id"],
                    want_pin, max_pinned_slots=eff_max_pinned
                )
                working_slots = list(state["slots"])
                steps_executed += 1
                _record_forage_action(
                    {
                        "action": "PIN_SLOT" if want_pin else "UNPIN_SLOT",
                        "slot_id": updated["slot_id"], "concept_id": updated["concept_id"],
                        "turn": int(current_turn)
                    },
                    foraging_actions_applied, history_list,
                    foraged_slot_ids, updated["slot_id"]
                )
            continue

        if action in ("EVICT", "EVICT_SLOT", "DROP", "REMOVE"):
            pinned_cnt = sum(1 for s in working_slots if bool(s.get("pinned")))
            evictable = match_idx is not None and can_evict_table_slot(
                bool(working_slots[match_idx].get("pinned")),
                True, pinned_cnt <= eff_max_pinned
            )
            if should_execute_foraging_step(
                req, steps_under_max, explicit_budget_ok
                and evictable
            ) and match_idx is not None:
                evicted = working_slots.pop(match_idx)
                steps_executed += 1
                _record_forage_action(
                    {
                        "action": "EVICT_SLOT", "slot_id": evicted["slot_id"],
                        "concept_id": evicted["concept_id"],
                        "turn": int(current_turn)
                    },
                    foraging_actions_applied, history_list,
                    foraged_slot_ids, evicted["slot_id"]
                )
            continue

        is_escalate = action in ("ESCALATE_LOD", "INSPECT_URI", "UPGRADE_LOD", "EXPAND_LOD", "UPGRADE", "ESCALATE")
        if (
            match_idx is None and is_escalate and (raw_cid or raw_sid)
            and isinstance(whiteboard_graph, dict)
            and (raw_cid in (whiteboard_graph.get("scopes") or {}))
        ):
            seed_slot = _build_candidate_slot(
                {"concept_id": raw_cid, "slot_id": raw_sid or f"slot_{raw_cid}", "lod_tier": LOD_1},
                raw_cid, current_turn, max_summary_words
            )
            sim_slots = _evict_until_fits(
                working_slots, 1, int(seed_slot["token_cost"]),
                eff_max_slots, eff_max_tokens, eff_max_pinned
            )
            if should_admit_table_slot(
                (len(sim_slots) + 1) <= eff_max_slots,
                (sum(int(s["token_cost"]) for s in sim_slots) + int(seed_slot["token_cost"])) <= eff_max_tokens
            ):
                working_slots = sim_slots + [seed_slot]
                match_idx = len(working_slots) - 1

        if match_idx is not None:
            cur_slot = working_slots[match_idx]
            cur_lod = _norm_lod(cur_slot.get("lod_tier") or LOD_0)
            raw_tgt = d.get("target_lod") or d.get("new_lod") or d.get("lod_tier") or d.get("lod")
            tgt_lod = (
                _norm_lod(raw_tgt) if raw_tgt is not None
                else (TableLOD.TLOD2 if is_escalate else (TableLOD.TLOD1 if cur_lod == TableLOD.TLOD0 else TableLOD.TLOD2))
            )
            clamped_sum, enriched_links, words_within_50 = _enrich_slot_from_graph_and_links(
                cur_slot["concept_id"], cur_slot, d,
                whiteboard_graph, store, max_summary_words
            )
            raw_cost = d.get("token_cost", d.get("cost"))
            cand_cost = int(raw_cost) if (
                isinstance(raw_cost, int) and not isinstance(raw_cost, bool)
                and raw_cost > 0
            ) else max(1, token_cost(clamped_sum or cur_slot["concept_id"]))
            other_tokens = sum(int(s["token_cost"]) for i, s in enumerate(working_slots) if i != match_idx)
            budget_ok = explicit_budget_ok and ((other_tokens + cand_cost) <= eff_max_tokens)

            if should_execute_foraging_step(req, steps_under_max, budget_ok):
                next_lod = cur_lod
                if _LOD_RANK[tgt_lod] > _LOD_RANK[cur_lod]:
                    while _LOD_RANK[next_lod] < _LOD_RANK[tgt_lod]:
                        stepped = step_slot_lod_escalation(next_lod, True, words_within_50, budget_ok)
                        if stepped == next_lod:
                            break
                        next_lod = stepped
                else:
                    next_lod = step_slot_lod_escalation(cur_lod, False, words_within_50, budget_ok)
                if next_lod != cur_lod or words_within_50:
                    cur_slot["lod_tier"] = next_lod.value
                    if words_within_50:
                        cur_slot["summary"] = cur_slot["lod1_synopsis"] = clamped_sum
                        cur_slot["token_cost"] = cand_cost
                    cur_slot["external_links"] = enriched_links
                    if "pinned" in d:
                        other_pinned = sum(
                            1 for i, s in enumerate(working_slots)
                            if i != match_idx and bool(s.get("pinned"))
                        )
                        if not bool(d["pinned"]) or other_pinned < eff_max_pinned:
                            cur_slot["pinned"] = bool(d["pinned"])
                    cur_slot["updated_turn"] = int(d.get("updated_turn", current_turn) or current_turn)
                    working_slots[match_idx] = TableSlot(cur_slot, max_summary_words=max_summary_words)
                steps_executed += 1
                rec = {
                    "action": action or "ESCALATE_LOD",
                    "slot_id": working_slots[match_idx]["slot_id"],
                    "concept_id": working_slots[match_idx]["concept_id"],
                    "lod_tier": working_slots[match_idx]["lod_tier"],
                    "turn": int(current_turn), **(
                        {"target_uri": str(d.get("target_uri") or d.get("uri"))}
                        if (d.get("target_uri") or d.get("uri")) else {}
                    )
                }
                _record_forage_action(
                    rec, foraging_actions_applied, history_list,
                    foraged_slot_ids, working_slots[match_idx]["slot_id"]
                )
        else:
            cand_slot = _build_candidate_slot(d, f"concept_{steps_executed + 1}", current_turn, max_summary_words)
            if cand_slot["pinned"] and sum(1 for s in working_slots if bool(s.get("pinned"))) >= eff_max_pinned:
                cand_slot["pinned"] = False
            cand_cost = int(cand_slot["token_cost"])
            sim_slots = _evict_until_fits(working_slots, 1, cand_cost, eff_max_slots, eff_max_tokens, eff_max_pinned)
            budget_ok = explicit_budget_ok and should_admit_table_slot(
                (len(sim_slots) + 1) <= eff_max_slots,
                (sum(int(s["token_cost"]) for s in sim_slots) + cand_cost) <= eff_max_tokens
            )
            if should_execute_foraging_step(req, steps_under_max, budget_ok):
                working_slots = sim_slots + [cand_slot]
                steps_executed += 1
                rec = {
                    "action": action or "FORAGE_SLOT", "slot_id": cand_slot["slot_id"],
                    "concept_id": cand_slot["concept_id"],
                    "lod_tier": cand_slot["lod_tier"],
                    "turn": int(current_turn)
                }
                _record_forage_action(
                    rec, foraging_actions_applied, history_list,
                    foraged_slot_ids, cand_slot["slot_id"]
                )

    state["slots"] = state["active_slots"] = working_slots
    state["max_table_slots"], state["max_table_tokens"], state["max_pinned_slots"] = eff_max_slots, eff_max_tokens, eff_max_pinned
    state["foraging_steps"], state["turn_index"] = steps_executed, int(current_turn)
    view = render_table_view(
        state, max_summary_words=max_summary_words,
        max_table_slots=eff_max_slots, max_table_tokens=eff_max_tokens
    )
    view["foraging_steps"] = view["steps_executed"] = steps_executed
    view["max_foraging_steps"] = max_foraging_steps
    view["foraged_slot_ids"] = foraged_slot_ids
    view["foraging_actions_applied"] = foraging_actions_applied
    view["table_state"] = state
    return view
