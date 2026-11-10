"""Stage 25B (`table_renderer`): AI Memory & Whiteboard Workspace Synchronization & Stage 2 Foraging."""

from __future__ import annotations

import copy
import importlib
import importlib.util
from typing import Any

from bowthruster.graph_core import token_cost

from .renderer import (LOD_0, LOD_1, LOD_2, MAX_FORAGING_STEPS, MAX_PINNED_SLOTS, MAX_SLOT_SUMMARY_WORDS,
    MAX_TABLE_SLOTS, MAX_TABLE_TOKENS, TableLOD, TableSlot, TableView, _LOD_RANK, _clamp_summary_words,
    _extract_raw_slots, _norm_lod, lod_not_demoted, render_table_view, should_execute_foraging_step,
    step_slot_lod_escalation)
from .schema import _pick

def _load_core_submod(submod: str) -> Any | None:
    full_name = f"bowthruster.the_table.{submod}"
    try:
        return importlib.import_module(full_name) if importlib.util.find_spec(full_name) is not None else None
    except Exception:
        return None

def _load_ai_memory() -> Any | None:
    try:
        if importlib.util.find_spec("bowthruster.ai_memory") is not None:
            return importlib.import_module("bowthruster.ai_memory")
        return None
    except Exception:
        return None

def should_admit_table_slot(within_16_slots: bool, within_1200_tokens: bool) -> bool:
    """Stage 25A Law 1 (`table_token_budget_conservation_law`): True iff `within_16_slots and within_1200_tokens`."""
    fn = getattr(_load_core_submod("schema"), "should_admit_table_slot", None)
    if callable(fn):
        return bool(fn(within_16_slots, within_1200_tokens))
    return bool(within_16_slots) and bool(within_1200_tokens)

def can_evict_table_slot(is_pinned: bool, is_over_capacity: bool, within_6_pinned_cap: bool = True) -> bool:
    """Stage 25A Law 2 (`pinned_slot_eviction_immunity_law`): pinned slots within 6-pinned cap are immune."""
    fn = getattr(_load_core_submod("schema"), "can_evict_table_slot", None)
    if callable(fn):
        return bool(fn(is_pinned, is_over_capacity, within_6_pinned_cap))
    return False if (bool(is_pinned) and bool(within_6_pinned_cap)) else bool(is_over_capacity)

def should_evict_first_over_second(first_pinned: bool, second_pinned: bool, first_lower_priority: bool) -> bool:
    """Stage 25A Law 3 (`slot_eviction_order_law`): unpinned slots evict before pinned slots; ties follow priority."""
    fn = getattr(_load_core_submod("surface"), "should_evict_first_over_second", None)
    if callable(fn):
        return bool(fn(first_pinned, second_pinned, first_lower_priority))
    return (not bool(first_pinned)) if (bool(first_pinned) != bool(second_pinned)) else bool(first_lower_priority)

from bowthruster.the_table import (
    _enrich_slot_from_graph_and_links,
    _expand_neighbors_for_concept,
    _find_slot_index,
    _link_uri,
    _load_ai_memory,
    _normalize_foraging_directives,
    _record_forage_action,
    _slot_priority_key,
    _validate_caps,
    _validate_int_bound,
)

def create_empty_table_state(*, max_table_slots: int = MAX_TABLE_SLOTS, max_table_tokens: int = MAX_TABLE_TOKENS,
    max_pinned_slots: int = MAX_PINNED_SLOTS, turn_index: int = 1) -> dict[str, Any]:
    """Creates a clean bounded `table_state` dictionary (`max_table_slots <= 16`, `max_table_tokens <= 1200`)."""
    _validate_caps(max_table_slots, max_table_tokens, max_pinned_slots)
    kw_variants = (
        {"max_table_slots": max_table_slots, "max_table_tokens": max_table_tokens,
            "max_pinned_slots": max_pinned_slots},
        {"max_slots": max_table_slots, "max_tokens": max_table_tokens, "max_pinned_slots": max_pinned_slots})
    for mod_name in ("schema", "surface"):
        mod = _load_core_submod(mod_name)
        for fn_name in ("create_empty_table_state", "create_table_state"):
            if callable(fn := getattr(mod, fn_name, None)):
                for kw in kw_variants:
                    try:
                        if isinstance(state := fn(**kw), dict):
                            defaults = (("slots", []), ("active_slots", state.get("slots", [])), ("total_tokens", 0),
                                ("max_table_slots", int(max_table_slots)), ("max_table_tokens", int(max_table_tokens)),
                                ("max_pinned_slots", int(max_pinned_slots)), ("turn_index", int(turn_index)),
                                ("foraging_history", []))
                            for k, v in defaults:
                                state.setdefault(k, v)
                            return state
                    except TypeError:
                        continue
    slots_list: list[TableSlot] = []
    return {"slots": slots_list, "active_slots": slots_list, "total_tokens": 0,
        "max_table_slots": int(max_table_slots), "max_table_tokens": int(max_table_tokens),
        "max_pinned_slots": int(max_pinned_slots), "turn_index": int(turn_index),
        "compact_view": "", "foraging_history": []}

def _set_slot_pinned(
    table_state: dict[str, Any], slot_id: str, pinned: bool, max_pinned_slots: int = MAX_PINNED_SLOTS) -> TableSlot:
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
            int(table_state.get("max_pinned_slots", max_pinned_slots) or max_pinned_slots), max_pinned_slots)
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

def _evict_until_fits(slots: list[TableSlot], needed_slots: int, needed_tokens: int,
    max_slots: int, max_tokens: int, max_pinned: int) -> list[TableSlot]:
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
            is_better = victim_idx is None or should_evict_first_over_second(
                bool(cand.get("pinned")), bool(working[victim_idx].get("pinned")),
                _slot_priority_key(cand, idx) < _slot_priority_key(working[victim_idx], victim_idx))
            if is_better:
                victim_idx = idx
        if victim_idx is None:
            break
        working.pop(victim_idx)
    return working

def _build_candidate_slot(
    item: dict[str, Any], default_cid: str, current_turn: int, max_summary_words: int, *, source: str | None = None,
) -> TableSlot:
    cid = str(_pick(item, "concept_id", "target_id", "id", "uri", "query", default=default_cid)).strip()
    if cid.startswith("slot_") and not item.get("concept_id"):
        cid = cid[5:] or cid
    sid = str(item.get("slot_id") or (cid if cid.startswith("slot_") else f"slot_{cid}")).strip()
    raw_syn = str(_pick(item, "lod1_synopsis", "summary", "text", "canonical_text", default="")).strip()
    clamped_syn = _clamp_summary_words(raw_syn, max_summary_words)
    lod_val = _norm_lod(_pick(item, "target_lod", "lod_tier", "lod", default=LOD_1)).value
    raw_cost = _pick(item, "token_cost", "cost")
    ext_links = item["external_links"] if isinstance(item.get("external_links"), list) else []
    hop_val = int(_pick(item, "hop_distance", "hop", default=0) or 0)
    turn_val = int(item.get("updated_turn") or current_turn)
    return TableSlot(slot_id=sid, concept_id=cid, lod_tier=lod_val, pinned=bool(item.get("pinned", False)),
        token_cost_val=raw_cost if isinstance(raw_cost, int) and not isinstance(raw_cost, bool) else None,
        summary=clamped_syn, lod1_synopsis=clamped_syn, external_links=ext_links,
        activation=float(item.get("activation", 1.0) or 0.0), hop_distance=hop_val,
        updated_turn=turn_val, max_summary_words=max_summary_words, **({"source": source} if source else {}))

def _extract_context_view_candidates(context_view: dict[str, Any] | None, current_turn: int, max_summary_words: int,
    ) -> list[TableSlot]:
    if not isinstance(context_view, dict):
        return []
    candidates: list[TableSlot] = []
    seen_cids: set[str] = set()
    for raw_slot in ((context_view.get("slots") or [])):
        if isinstance(raw_slot, dict):
            slot = TableSlot(raw_slot, updated_turn=current_turn, max_summary_words=max_summary_words)
            if slot["concept_id"] not in seen_cids:
                seen_cids.add(slot["concept_id"])
                candidates.append(slot)
    for rule in (context_view.get("active_rules") if isinstance(context_view.get("active_rules"), list) else []):
        if isinstance(rule, dict):
            cid = str(_pick(rule, "scope", "id", default="")).strip()
            if cid and cid != "global_defaults" and cid not in seen_cids:
                seen_cids.add(cid)
                candidates.append(TableSlot(slot_id=f"slot_{cid}", concept_id=cid, lod_tier=LOD_1, pinned=False,
                    summary=_clamp_summary_words(str(rule.get("text") or cid), max_summary_words),
                    activation=float(_pick(rule, "score", "activation", default=0.5) or 0.5),
                    updated_turn=int(rule.get("updated_turn") or current_turn),
                    max_summary_words=max_summary_words, source="whiteboard"))
    return candidates

def _admit_or_merge_slots(working_slots: list[TableSlot], incoming_slots: list[TableSlot],
    eff_max_slots: int, eff_max_tokens: int, eff_max_pinned: int, max_summary_words: int) -> list[TableSlot]:
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
            merged_links = (prev_slot.get("external_links", [])
                if (keep_higher or not cand.get("external_links")) else cand["external_links"])
            eff_cand = ({**cand, "summary": prev_slot["summary"], "lod1_synopsis": prev_slot["summary"],
                "token_cost": prev_slot["token_cost"], "external_links": merged_links}
                if keep_higher else {**cand, "external_links": merged_links})
            other_tokens = sum(int(s["token_cost"]) for i, s in enumerate(working_slots) if i != existing_idx)
            if other_tokens + int(eff_cand["token_cost"]) > eff_max_tokens:
                others = _evict_until_fits([s for i, s in enumerate(working_slots) if i != existing_idx],
                    1, int(eff_cand["token_cost"]), eff_max_slots, eff_max_tokens, eff_max_pinned)
                if sum(int(s["token_cost"]) for s in others) + int(eff_cand["token_cost"]) <= eff_max_tokens:
                    working_slots, existing_idx = others, None
                else:
                    continue
            updated = TableSlot({**prev_slot, **eff_cand, "slot_id": prev_slot["slot_id"],
                "concept_id": prev_slot["concept_id"], "lod_tier": merged_lod, "pinned": pinned_now},
                max_summary_words=max_summary_words)
            if existing_idx is not None:
                working_slots[existing_idx] = updated
            else:
                working_slots.append(updated)
        else:
            if bool(cand.get("pinned")) and sum(1 for s in working_slots if bool(s.get("pinned"))) >= eff_max_pinned:
                cand["pinned"] = False
            working_slots = _evict_until_fits(
                working_slots, 1, int(cand["token_cost"]), eff_max_slots, eff_max_tokens, eff_max_pinned)
            slots_ok = (len(working_slots) + 1) <= eff_max_slots
            toks_ok = (sum(int(s["token_cost"]) for s in working_slots) + int(cand["token_cost"])) <= eff_max_tokens
            if should_admit_table_slot(slots_ok, toks_ok):
                working_slots.append(cand)
    return working_slots

def sync_memory_to_table(table_state: dict[str, Any] | None, recalled_concepts: list[dict[str, Any]] | None,
    context_view: dict[str, Any] | None = None, *, current_turn: int = 1, max_table_slots: int = MAX_TABLE_SLOTS,
    max_table_tokens: int = MAX_TABLE_TOKENS, max_pinned_slots: int = MAX_PINNED_SLOTS,
    max_summary_words: int = MAX_SLOT_SUMMARY_WORDS) -> TableView:
    """Hydrates recalled `ConceptFrame` records and optional `ContextView` items onto `table_state`."""
    if recalled_concepts is not None and not isinstance(recalled_concepts, (list, tuple)):
        raise ValueError(f"recalled_concepts must be a list or None, got {type(recalled_concepts).__name__}")
    if context_view is not None and not isinstance(context_view, dict):
        raise ValueError(f"context_view must be a dict or None, got {type(context_view).__name__}")
    _validate_caps(max_table_slots, max_table_tokens, max_pinned_slots, max_summary_words)
    if table_state is None:
        state = create_empty_table_state(max_table_slots=max_table_slots, max_table_tokens=max_table_tokens,
            max_pinned_slots=max_pinned_slots, turn_index=current_turn)
    elif isinstance(table_state, dict):
        state = table_state
    else:
        raise ValueError(f"table_state must be a dict or None, got {type(table_state).__name__}")
    eff_max_slots = min(int(_pick(state, "max_table_slots", "max_slots", default=max_table_slots)), max_table_slots)
    eff_max_tokens = min(int(_pick(state, "max_table_tokens", "max_tokens", default=max_table_tokens)),
        max_table_tokens)
    eff_max_pinned = min(int(state.get("max_pinned_slots") or max_pinned_slots), max_pinned_slots)
    _, existing_raw = _extract_raw_slots(state)
    working_slots: list[TableSlot] = [TableSlot(s, max_summary_words=max_summary_words) for s in existing_raw]
    incoming_slots: list[TableSlot] = []
    for idx, item in enumerate(recalled_concepts or []):
        if not isinstance(item, dict):
            raise ValueError(f"recalled_concepts[{idx}] must be a dict, got {type(item).__name__}")
        incoming_slots.append(
            _build_candidate_slot(item, f"concept_{idx + 1}", current_turn, max_summary_words, source="ai_memory"))
    incoming_slots.extend(_extract_context_view_candidates(context_view, current_turn, max_summary_words))
    working_slots = _admit_or_merge_slots(
        working_slots, incoming_slots, eff_max_slots, eff_max_tokens, eff_max_pinned, max_summary_words)
    state["slots"] = state["active_slots"] = working_slots
    state.update(max_table_slots=eff_max_slots, max_table_tokens=eff_max_tokens,
        max_pinned_slots=eff_max_pinned, turn_index=int(current_turn))
    view = render_table_view(
        state, max_summary_words=max_summary_words, max_table_slots=eff_max_slots, max_table_tokens=eff_max_tokens)
    view["table_state"] = state
    return view


def forage_table_context(
    table_state: dict[str, Any] | list[Any] | None = None,
    foraging_directives: list[Any] | dict[str, Any] | str | None = None,
    *,
    whiteboard_graph: dict[str, Any] | None = None,
    store: Any = None,
    current_turn: int = 1,
    max_foraging_steps: int = 4,
    max_table_slots: int = MAX_TABLE_SLOTS,
    max_table_tokens: int = MAX_TABLE_TOKENS,
    max_pinned_slots: int = MAX_PINNED_SLOTS,
    max_summary_words: int = MAX_SLOT_SUMMARY_WORDS,
    memory_index: Any = None,
    concept_lookup: dict[str, Any] | None = None,
) -> TableView:
    """Stage 2 Active Prefrontal Foraging (`stage2_foraging_budget_law`)."""
    from .forager import forage_table_context as _forage

    return _forage(
        table_state,
        foraging_directives,
        whiteboard_graph=whiteboard_graph,
        store=store,
        current_turn=current_turn,
        max_foraging_steps=max_foraging_steps,
        max_table_slots=max_table_slots,
        max_table_tokens=max_table_tokens,
        max_pinned_slots=max_pinned_slots,
        max_summary_words=max_summary_words,
        memory_index=memory_index,
        concept_lookup=concept_lookup,
    )
