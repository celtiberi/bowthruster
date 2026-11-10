"""Stage 2 Active Prefrontal Foraging (`forager.py`)."""

from __future__ import annotations

from typing import Any

from bowthruster.graph_core import token_cost
from bowthruster.the_table import (
    _enrich_slot_from_graph_and_links,
    _expand_neighbors_for_concept,
    _find_slot_index,
    _normalize_foraging_directives,
    _record_forage_action,
    _validate_caps,
    _validate_int_bound,
)

from .renderer import (
    LOD_0,
    LOD_1,
    MAX_FORAGING_STEPS,
    MAX_PINNED_SLOTS,
    MAX_SLOT_SUMMARY_WORDS,
    MAX_TABLE_SLOTS,
    MAX_TABLE_TOKENS,
    TableLOD,
    TableSlot,
    TableView,
    _LOD_RANK,
    _extract_raw_slots,
    _norm_lod,
    render_table_view,
    should_execute_foraging_step,
    step_slot_lod_escalation,
)
from .schema import _pick
from .workspace_sync import (
    _build_candidate_slot,
    _evict_until_fits,
    _set_slot_pinned,
    can_evict_table_slot,
    create_empty_table_state,
    should_admit_table_slot,
    sync_memory_to_table,
)


def _escalate_or_admit_foraged_slot(d: dict[str, Any], ctx: dict[str, Any]) -> None:
    working_slots: list[TableSlot] = ctx["working_slots"]
    match_idx: int | None = ctx["match_idx"]
    is_escalate: bool = ctx["is_escalate"]
    raw_cid, raw_sid = ctx["raw_cid"], ctx["raw_sid"]
    wb_graph = ctx["whiteboard_graph"]
    turn, max_words = ctx["current_turn"], ctx["max_summary_words"]
    eff_slots = ctx["eff_max_slots"]
    eff_toks = ctx["eff_max_tokens"]
    eff_pin = ctx["eff_max_pinned"]
    req = ctx["req"]
    steps_under_max = ctx["steps_under_max"]
    explicit_budget_ok = ctx["explicit_budget_ok"]

    can_seed_from_wb = (
        match_idx is None
        and is_escalate
        and bool(raw_cid or raw_sid)
        and wb_graph is not None
        and raw_cid in (wb_graph.get("scopes") or {})
    )
    if can_seed_from_wb:
        seed_spec = {
            "concept_id": raw_cid,
            "slot_id": raw_sid or f"slot_{raw_cid}",
            "lod_tier": LOD_1,
        }
        seed_slot = _build_candidate_slot(seed_spec, raw_cid, turn, max_words)
        sim_slots = _evict_until_fits(
            working_slots, 1, int(seed_slot["token_cost"]), eff_slots, eff_toks, eff_pin
        )
        tok_sum = sum(int(s["token_cost"]) for s in sim_slots) + int(seed_slot["token_cost"])
        if should_admit_table_slot((len(sim_slots) + 1) <= eff_slots, tok_sum <= eff_toks):
            working_slots = sim_slots + [seed_slot]
            match_idx = len(working_slots) - 1

    if match_idx is not None:
        cur_slot = working_slots[match_idx]
        cur_lod = _norm_lod(cur_slot.get("lod_tier") or LOD_0)
        raw_tgt = _pick(d, "target_lod", "new_lod", "lod_tier", "lod")
        if is_escalate:
            default_tgt = TableLOD.TLOD2
        elif cur_lod == TableLOD.TLOD0:
            default_tgt = TableLOD.TLOD1
        else:
            default_tgt = TableLOD.TLOD2
        tgt_lod = _norm_lod(raw_tgt) if raw_tgt is not None else default_tgt
        clamped_sum, enriched_links, words_within_50 = _enrich_slot_from_graph_and_links(
            cur_slot["concept_id"], cur_slot, d, wb_graph, ctx["store"], max_words
        )
        raw_cost = _pick(d, "token_cost", "cost")
        if isinstance(raw_cost, int) and not isinstance(raw_cost, bool) and raw_cost > 0:
            cand_cost = int(raw_cost)
        else:
            cand_cost = max(1, token_cost(clamped_sum or cur_slot["concept_id"]))
        other_tokens = sum(
            int(s["token_cost"]) for i, s in enumerate(working_slots) if i != match_idx
        )
        budget_ok = explicit_budget_ok and ((other_tokens + cand_cost) <= eff_toks)
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
                        1
                        for i, s in enumerate(working_slots)
                        if i != match_idx and bool(s.get("pinned"))
                    )
                    if not bool(d["pinned"]) or other_pinned < eff_pin:
                        cur_slot["pinned"] = bool(d["pinned"])
                cur_slot["updated_turn"] = int(d.get("updated_turn") or turn)
                working_slots[match_idx] = TableSlot(cur_slot, max_summary_words=max_words)
            ctx["steps_executed"] += 1
            t_uri = _pick(d, "target_uri", "uri")
            updated_s = working_slots[match_idx]
            rec: dict[str, Any] = {
                "action": ctx["action"] or "ESCALATE_LOD",
                "slot_id": updated_s["slot_id"],
                "concept_id": updated_s["concept_id"],
                "lod_tier": updated_s["lod_tier"],
                "turn": int(turn),
            }
            if t_uri:
                rec["target_uri"] = str(t_uri)
            _record_forage_action(rec, ctx, updated_s["slot_id"])
        ctx["working_slots"] = working_slots
        return

    cand_slot = _build_candidate_slot(d, f"concept_{ctx['steps_executed'] + 1}", turn, max_words)
    if cand_slot["pinned"] and sum(1 for s in working_slots if bool(s.get("pinned"))) >= eff_pin:
        cand_slot["pinned"] = False
    cand_cost = int(cand_slot["token_cost"])
    sim_slots = _evict_until_fits(working_slots, 1, cand_cost, eff_slots, eff_toks, eff_pin)
    tok_sum = sum(int(s["token_cost"]) for s in sim_slots) + cand_cost
    budget_ok = explicit_budget_ok and should_admit_table_slot(
        (len(sim_slots) + 1) <= eff_slots, tok_sum <= eff_toks
    )
    if should_execute_foraging_step(req, steps_under_max, budget_ok):
        working_slots = sim_slots + [cand_slot]
        ctx["steps_executed"] += 1
        rec = {
            "action": ctx["action"] or "FORAGE_SLOT",
            "slot_id": cand_slot["slot_id"],
            "concept_id": cand_slot["concept_id"],
            "lod_tier": cand_slot["lod_tier"],
            "turn": int(turn),
        }
        _record_forage_action(rec, ctx, cand_slot["slot_id"])
    ctx["working_slots"] = working_slots


def _apply_forage_directive(
    d: dict[str, Any],
    state: dict[str, Any],
    ctx: dict[str, Any],
    max_foraging_steps: int,
    memory_index: Any,
    concept_lookup: Any,
) -> None:
    action = str(_pick(d, "action", "op", "type", "directive", default="")).strip().upper()
    req_flag = bool(d.get("foraging_requested", d.get("requested", d.get("enabled", True))))
    req = req_flag and not (bool(d.get("skip", False)) or action in ("NONE", "NOOP", "SKIP"))
    steps_under_max = ctx["steps_executed"] < max_foraging_steps
    explicit_budget_ok = bool(d.get("budget_ok", state.get("budget_ok", True)))
    raw_sid = str(d.get("slot_id") or "").strip()
    sid_stem = raw_sid[5:] if raw_sid.startswith("slot_") else raw_sid
    raw_cid = str(_pick(d, "concept_id", "target_id", "id", "query", default=sid_stem)).strip()
    if isinstance(concept_lookup, dict) and isinstance(lu := concept_lookup.get(raw_cid), dict):
        d = {**lu, **d}
    elif memory_index is not None and callable(get_c := getattr(memory_index, "get_concept", None)):
        if raw_cid and isinstance(found := get_c(raw_cid), dict):
            d = {**found, **d}
    working_slots = ctx["working_slots"]
    match_idx = _find_slot_index(working_slots, raw_sid or raw_cid)
    current_turn = ctx["current_turn"]
    eff_max_slots = ctx["eff_max_slots"]
    eff_max_tokens = ctx["eff_max_tokens"]
    eff_max_pinned = ctx["eff_max_pinned"]
    max_summary_words = ctx["max_summary_words"]

    if action == "EXPAND_NEIGHBORS":
        neighbors = _expand_neighbors_for_concept(
            raw_cid or raw_sid, ctx["whiteboard_graph"], ctx["store"] or memory_index
        )
        if should_execute_foraging_step(req, steps_under_max, explicit_budget_ok and bool(neighbors)):
            state["slots"] = state["active_slots"] = working_slots
            sync_memory_to_table(
                state,
                neighbors,
                current_turn=current_turn,
                max_table_slots=eff_max_slots,
                max_table_tokens=eff_max_tokens,
                max_pinned_slots=eff_max_pinned,
                max_summary_words=max_summary_words,
            )
            ctx["working_slots"] = list(state.get("slots", []))
            ctx["steps_executed"] += 1
            rec = {
                "action": "EXPAND_NEIGHBORS",
                "concept_id": raw_cid or raw_sid,
                "expanded_count": len(neighbors),
                "turn": int(current_turn),
            }
            _record_forage_action(rec, ctx)
            ctx["foraged_slot_ids"].extend(
                f"slot_{nb['concept_id']}"
                for nb in neighbors
                if isinstance(nb, dict) and nb.get("concept_id")
            )
    elif action in ("PIN", "PIN_SLOT", "UNPIN", "UNPIN_SLOT"):
        want_pin = action in ("PIN", "PIN_SLOT")
        other_pinned = sum(
            1 for i, s in enumerate(working_slots) if i != match_idx and bool(s.get("pinned"))
        )
        pin_ok = (other_pinned < eff_max_pinned) if want_pin else True
        can_pin = explicit_budget_ok and (match_idx is not None) and pin_ok
        if should_execute_foraging_step(req, steps_under_max, can_pin) and match_idx is not None:
            state["slots"] = state["active_slots"] = working_slots
            updated = _set_slot_pinned(
                state,
                working_slots[match_idx]["slot_id"],
                want_pin,
                max_pinned_slots=eff_max_pinned,
            )
            ctx["working_slots"] = list(state["slots"])
            ctx["steps_executed"] += 1
            rec = {
                "action": "PIN_SLOT" if want_pin else "UNPIN_SLOT",
                "slot_id": updated["slot_id"],
                "concept_id": updated["concept_id"],
                "turn": int(current_turn),
            }
            _record_forage_action(rec, ctx, updated["slot_id"])
    elif action in ("EVICT", "EVICT_SLOT", "DROP", "REMOVE"):
        pinned_cnt = sum(1 for s in working_slots if bool(s.get("pinned")))
        evictable = match_idx is not None and can_evict_table_slot(
            bool(working_slots[match_idx].get("pinned")), True, pinned_cnt <= eff_max_pinned
        )
        if (
            should_execute_foraging_step(req, steps_under_max, explicit_budget_ok and evictable)
            and match_idx is not None
        ):
            evicted = working_slots.pop(match_idx)
            ctx["steps_executed"] += 1
            rec = {
                "action": "EVICT_SLOT",
                "slot_id": evicted["slot_id"],
                "concept_id": evicted["concept_id"],
                "turn": int(current_turn),
            }
            _record_forage_action(rec, ctx, evicted["slot_id"])
    else:
        esc_ops = (
            "ESCALATE_LOD", "INSPECT_URI", "UPGRADE_LOD", "EXPAND_LOD", "UPGRADE", "ESCALATE"
        )
        ctx.update(
            action=action,
            is_escalate=action in esc_ops,
            req=req,
            steps_under_max=steps_under_max,
            explicit_budget_ok=explicit_budget_ok,
            raw_cid=raw_cid,
            raw_sid=raw_sid,
            match_idx=match_idx,
        )
        _escalate_or_admit_foraged_slot(d, ctx)


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
    _validate_caps(max_table_slots, max_table_tokens, max_pinned_slots, max_summary_words)
    _validate_int_bound(max_foraging_steps, 0, MAX_FORAGING_STEPS, "max_foraging_steps")
    directives, dir_step_cap = _normalize_foraging_directives(foraging_directives)
    if dir_step_cap is not None:
        capped = _validate_int_bound(dir_step_cap, 0, MAX_FORAGING_STEPS, "max_foraging_steps")
        max_foraging_steps = min(max_foraging_steps, capped)
    if table_state is None:
        state = create_empty_table_state(
            max_table_slots=max_table_slots,
            max_table_tokens=max_table_tokens,
            max_pinned_slots=max_pinned_slots,
            turn_index=current_turn,
        )
        raw_list: list[dict[str, Any]] = []
    else:
        state, raw_list = _extract_raw_slots(table_state)
    if state.get("max_foraging_steps") is not None:
        st_cap = _validate_int_bound(
            state["max_foraging_steps"], 0, MAX_FORAGING_STEPS, "max_foraging_steps"
        )
        max_foraging_steps = min(max_foraging_steps, st_cap)
    eff_max_slots = min(
        int(_pick(state, "max_table_slots", "max_slots", default=max_table_slots)),
        max_table_slots,
    )
    eff_max_tokens = min(
        int(_pick(state, "max_table_tokens", "max_tokens", default=max_table_tokens)),
        max_table_tokens,
    )
    eff_max_pinned = min(int(state.get("max_pinned_slots") or max_pinned_slots), max_pinned_slots)
    working_slots: list[TableSlot] = [
        TableSlot(s, max_summary_words=max_summary_words) for s in raw_list
    ]
    state["slots"] = state["active_slots"] = working_slots
    history_list = (
        state.setdefault("foraging_history", [])
        if isinstance(state.get("foraging_history"), list)
        else []
    )
    state["foraging_history"] = history_list
    ctx: dict[str, Any] = {
        "working_slots": working_slots,
        "whiteboard_graph": whiteboard_graph,
        "store": store,
        "current_turn": int(current_turn),
        "steps_executed": 0,
        "eff_max_slots": eff_max_slots,
        "eff_max_tokens": eff_max_tokens,
        "eff_max_pinned": eff_max_pinned,
        "max_summary_words": max_summary_words,
        "foraging_actions_applied": [],
        "history_list": history_list,
        "foraged_slot_ids": [],
    }
    for d in directives:
        _apply_forage_directive(d, state, ctx, max_foraging_steps, memory_index, concept_lookup)
    working_slots = ctx["working_slots"]
    steps_executed = ctx["steps_executed"]
    state["slots"] = state["active_slots"] = working_slots
    state.update(
        max_table_slots=eff_max_slots,
        max_table_tokens=eff_max_tokens,
        max_pinned_slots=eff_max_pinned,
        foraging_steps=steps_executed,
        turn_index=int(current_turn),
    )
    view = render_table_view(
        state,
        max_summary_words=max_summary_words,
        max_table_slots=eff_max_slots,
        max_table_tokens=eff_max_tokens,
    )
    view["foraging_steps"] = view["steps_executed"] = steps_executed
    view["max_foraging_steps"] = max_foraging_steps
    view["foraged_slot_ids"] = ctx["foraged_slot_ids"]
    view["foraging_actions_applied"] = ctx["foraging_actions_applied"]
    view["table_state"] = state
    return view
