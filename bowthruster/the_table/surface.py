"""Stage 25A (`the_table.surface`): Slot Allocation, Pinning Immunity & Atlas Surface (`surface.py`)."""

from __future__ import annotations

import copy
from typing import Any

from .atlas_renderer import (
    _collect_atlas_topology,
    _extract_board_charter,
    _normalize_atlas_boards,
    _render_board_spec,
)
from .schema import (
    LOD_0,
    LOD_1,
    LOD_2,
    MAX_PINNED_SLOTS,
    MAX_SLOT_SUMMARY_WORDS,
    MAX_TABLE_SLOTS,
    MAX_TABLE_TOKENS,
    TableSlot,
    TableState,
    TableView,
    _pick,
    bool_eq,
    can_evict_table_slot,
    create_empty_table_state,
    should_admit_table_slot,
    should_evict_first_over_second,
    should_mount_global_atlas,
    sync_table_to_sqlite,
    validate_table_slot,
    validate_table_state,
)


def _ensure_table_state_dict(
    table_state: dict[str, Any],
) -> tuple[list[TableSlot], int, int, int]:
    if not isinstance(table_state, dict):
        msg = f"table_state must be a dict or TableState, got {type(table_state).__name__}"
        raise ValueError(msg)
    max_slots = table_state.get("max_slots", MAX_TABLE_SLOTS)
    max_tokens = table_state.get("max_tokens", MAX_TABLE_TOKENS)
    max_pinned = table_state.get("max_pinned_slots", MAX_PINNED_SLOTS)
    if isinstance(max_slots, bool) or not isinstance(max_slots, int) or not (
        1 <= max_slots <= MAX_TABLE_SLOTS
    ):
        raise ValueError(f"max_slots must be an int in [1, {MAX_TABLE_SLOTS}], got {max_slots!r}")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not (
        1 <= max_tokens <= MAX_TABLE_TOKENS
    ):
        raise ValueError(
            f"max_tokens must be an int in [1, {MAX_TABLE_TOKENS}], got {max_tokens!r}"
        )
    if isinstance(max_pinned, bool) or not isinstance(max_pinned, int) or not (
        0 <= max_pinned <= MAX_PINNED_SLOTS
    ):
        raise ValueError(
            f"max_pinned_slots must be an int in [0, {MAX_PINNED_SLOTS}], got {max_pinned!r}"
        )
    raw_slots = table_state.get("slots", [])
    if isinstance(raw_slots, dict):
        raw_slots = list(raw_slots.values())
    if not isinstance(raw_slots, list):
        raise ValueError(f"table_state['slots'] must be a list, got {type(raw_slots).__name__}")
    slots = [validate_table_slot(s, max_tokens=max_tokens) for s in raw_slots]
    table_state["slots"] = slots
    table_state["total_tokens"] = sum(int(s["token_cost"]) for s in slots)
    table_state.setdefault("max_slots", max_slots)
    table_state.setdefault("max_tokens", max_tokens)
    table_state.setdefault("max_pinned_slots", max_pinned)
    return slots, max_slots, max_tokens, max_pinned


def _slot_priority_tuple(slot: dict[str, Any], index: int) -> tuple[float, int, int]:
    """Computes `(priority_or_activation, updated_turn, insertion_index)` for LRU eviction."""
    raw_prio = _pick(slot, "priority", "activation", default=0.0)
    if isinstance(raw_prio, (int, float)) and not isinstance(raw_prio, bool):
        prio = float(raw_prio)
    else:
        prio = 0.0
    return (prio, int(slot.get("updated_turn", 1)), index)


def _select_eviction_victim_index(
    slots: list[TableSlot],
    *,
    protected_index: int,
    within_6_pinned_cap: bool,
) -> int | None:
    """Selects the lowest-priority evictable slot index using Bend 2 Laws 2 & 3."""
    best_idx: int | None = None
    for idx, cand in enumerate(slots):
        is_pinned = bool(cand.get("pinned", False))
        if idx == protected_index or not can_evict_table_slot(is_pinned, True, within_6_pinned_cap):
            continue
        if best_idx is None:
            best_idx = idx
            continue
        best_slot = slots[best_idx]
        first_lower = _slot_priority_tuple(cand, idx) < _slot_priority_tuple(best_slot, best_idx)
        if should_evict_first_over_second(
            is_pinned, bool(best_slot.get("pinned", False)), first_lower
        ):
            best_idx = idx
    return best_idx


def admit_or_evict_slot(
    table_state: dict[str, Any],
    slot: dict[str, Any] | TableSlot,
    *,
    current_turn: int = 1,
) -> TableSlot:
    """Admits or updates `slot` in `table_state`, enforcing slot and token caps."""
    if isinstance(current_turn, bool) or not isinstance(current_turn, int) or current_turn < 0:
        raise ValueError(f"current_turn must be a non-negative int, got {current_turn!r}")
    if not isinstance(slot, dict):
        raise ValueError(f"slot must be a dict or TableSlot, got {type(slot).__name__}")
    slots, max_slots, max_tokens, max_pinned = _ensure_table_state_dict(table_state)
    cand_dict = copy.deepcopy(dict(slot))
    if "updated_turn" not in slot and "turn" not in slot:
        cand_dict["updated_turn"] = current_turn
    elif current_turn > int(cand_dict.get("updated_turn", 0)):
        cand_dict["updated_turn"] = current_turn
    cand_sid = str(_pick(cand_dict, "slot_id", "id", "concept_id", default="")).strip()
    cand_cid = str(cand_dict.get("concept_id") or "").strip()
    existing_idx = next(
        (
            i
            for i, s in enumerate(slots)
            if s["slot_id"] == cand_sid or (cand_cid and s["concept_id"] == cand_cid)
        ),
        None,
    )
    if existing_idx is not None and "pinned" not in slot:
        cand_dict["pinned"] = slots[existing_idx]["pinned"]
    new_slot = validate_table_slot(cand_dict, max_tokens=max_tokens)
    work_slots = list(slots)
    if existing_idx is not None:
        work_slots[existing_idx] = new_slot
        target_idx = existing_idx
    else:
        work_slots.append(new_slot)
        target_idx = len(work_slots) - 1
    if sum(1 for s in work_slots if s["pinned"]) > max_pinned:
        msg = f"max_pinned_slots ({max_pinned}) exceeded: cannot admit pinned slot {new_slot['slot_id']!r}"
        raise ValueError(msg)
    evicted_ids: list[str] = []
    while not should_admit_table_slot(
        len(work_slots) <= max_slots,
        sum(int(s["token_cost"]) for s in work_slots) <= max_tokens,
    ):
        pinned_now = sum(1 for s in work_slots if s["pinned"])
        victim_idx = _select_eviction_victim_index(
            work_slots,
            protected_index=target_idx,
            within_6_pinned_cap=(pinned_now <= max_pinned),
        )
        if victim_idx is None:
            msg = (
                f"Cannot admit slot {new_slot['slot_id']!r} (token_cost={new_slot['token_cost']}): "
                "insufficient evictable unpinned capacity on table"
            )
            raise ValueError(msg)
        evicted_slot = work_slots.pop(victim_idx)
        evicted_ids.append(evicted_slot["slot_id"])
        if victim_idx < target_idx:
            target_idx -= 1
    table_state["slots"] = work_slots
    table_state["total_tokens"] = sum(int(s["token_cost"]) for s in work_slots)
    table_state["evicted_slots"] = list(evicted_ids)
    validate_table_state(table_state)
    sync_table_to_sqlite(table_state)
    admitted = work_slots[target_idx]
    object.__setattr__(admitted, "_table_state", table_state)
    object.__setattr__(admitted, "_evicted_ids", list(evicted_ids))
    return admitted


def pin_to_table(
    table_state: dict[str, Any],
    slot_id: str,
    *,
    pinned: bool = True,
    current_turn: int | None = None,
) -> TableSlot:
    """Pins or unpins `slot_id` in `table_state` (`max_pinned_slots <= 6`)."""
    if not isinstance(slot_id, str) or not slot_id.strip():
        raise ValueError(f"slot_id must be a non-empty str, got {slot_id!r}")
    if not isinstance(pinned, bool):
        raise ValueError(f"pinned must be a bool, got {pinned!r}")
    slots, _, _, max_pinned = _ensure_table_state_dict(table_state)
    target = slot_id.strip()
    idx = next(
        (i for i, s in enumerate(slots) if s["slot_id"] == target or s["concept_id"] == target),
        None,
    )
    if idx is None:
        raise KeyError(f"Slot {slot_id!r} not found in table_state['slots']")
    slot = slots[idx]
    if pinned and not slot["pinned"] and sum(1 for s in slots if s["pinned"]) + 1 > max_pinned:
        raise ValueError(f"max_pinned_slots ({max_pinned}) exceeded: cannot pin {slot_id!r}")
    slot["pinned"] = bool(pinned)
    if current_turn is not None:
        if isinstance(current_turn, bool) or not isinstance(current_turn, int) or current_turn < 0:
            raise ValueError(f"current_turn must be a non-negative int, got {current_turn!r}")
        slot["updated_turn"] = int(current_turn)
    sync_table_to_sqlite(table_state)
    object.__setattr__(slot, "_table_state", table_state)
    return slot


def unpin_from_table(
    table_state: dict[str, Any],
    slot_id: str,
    *,
    current_turn: int | None = None,
) -> TableSlot:
    """Unpins `slot_id` in `table_state` and returns the updated `TableSlot`."""
    return pin_to_table(table_state, slot_id, pinned=False, current_turn=current_turn)


def evict_from_table(
    table_state: dict[str, Any],
    slot_id: str,
    *,
    force: bool = False,
) -> bool:
    """Evicts an unpinned `slot_id` from `table_state` (enforcing Law 2 unless `force=True`)."""
    if not isinstance(slot_id, str) or not slot_id.strip():
        return False
    slots, _, _, max_pinned = _ensure_table_state_dict(table_state)
    target = slot_id.strip()
    idx = next(
        (i for i, s in enumerate(slots) if s["slot_id"] == target or s["concept_id"] == target),
        None,
    )
    if idx is None:
        return False
    slot = slots[idx]
    within_pin = sum(1 for s in slots if s["pinned"]) <= max_pinned
    if not force and not can_evict_table_slot(bool(slot["pinned"]), True, within_pin):
        raise ValueError(f"Cannot evict pinned slot {slot_id!r}: pinned_slot_eviction_immunity_law")
    slots.pop(idx)
    table_state["slots"] = slots
    table_state["total_tokens"] = sum(int(s["token_cost"]) for s in slots)
    sync_table_to_sqlite(table_state)
    return True


def get_active_slots(table_state: dict[str, Any]) -> list[TableSlot]:
    """Returns the ordered list of active `TableSlot` entries on `table_state`."""
    return list(_ensure_table_state_dict(table_state)[0])


def render_table_view(table_state: dict[str, Any]) -> TableView:
    """Renders `table_state` into a bounded `TableView` (`slots`, `total_tokens`, `compact_view`)."""
    slots, max_slots, max_tokens, _ = _ensure_table_state_dict(table_state)
    total_tokens = sum(int(s["token_cost"]) for s in slots)
    lines = [
        f"[TABLE_VIEW slots={len(slots)}/{max_slots} tokens={total_tokens}/{max_tokens}]",
        *[
            f"- [{s['slot_id']}] ({s['concept_id']}, {s['lod_tier']}"
            f"{' [PINNED]' if s['pinned'] else ''}, cost={s['token_cost']}): {s['summary']}"
            for s in slots
        ],
    ]
    compact = "\n".join(lines)
    return TableView({
        "slots": copy.deepcopy(slots),
        "total_tokens": total_tokens,
        "compact_view": compact,
        "text": compact,
    })


def sync_memory_to_table(
    table_state: dict[str, Any] | None,
    recalled_concepts: list[dict[str, Any]],
    *,
    current_turn: int = 1,
) -> TableView:
    """Hydrates `recalled_concepts` onto `table_state` and returns `TableView`."""
    state = create_empty_table_state() if table_state is None else table_state
    if not isinstance(recalled_concepts, list):
        msg = f"recalled_concepts must be a list, got {type(recalled_concepts).__name__}"
        raise ValueError(msg)
    for item in recalled_concepts:
        if not isinstance(item, dict):
            msg = f"recalled_concepts entry must be a dict, got {type(item).__name__}"
            raise ValueError(msg)
        raw_sum = str(_pick(item, "summary", "lod1_synopsis", "text", default="")).strip()
        cid = str(_pick(item, "concept_id", "id", "slot_id", default="")).strip()
        admit_or_evict_slot(
            state,
            {
                **item,
                "slot_id": str(item.get("slot_id") or cid).strip(),
                "concept_id": cid,
                "summary": " ".join(raw_sum.split()[:MAX_SLOT_SUMMARY_WORDS]),
                "lod_tier": item.get("lod_tier", LOD_1),
                "pinned": bool(item.get("pinned", False)),
            },
            current_turn=current_turn,
        )
    view = render_table_view(state)
    view["table_state"] = state
    return view


def render_lattice_atlas(
    whiteboard_boards: dict[str, dict[str, Any]] | list[dict[str, Any]] | dict[str, Any],
    *,
    table_state: dict[str, Any] | None = None,
    has_local_seed_cues: bool = False,
    explicit_atlas_request: bool = True,
    current_turn: int = 1,
) -> TableView:
    """Computes a multi-board `LOD_0` topological Lattice Atlas and mounts `slot_lattice_atlas`."""
    boards = _normalize_atlas_boards(whiteboard_boards)
    components, flows, atlas_metrics = _collect_atlas_topology(boards)
    total_components = atlas_metrics["total_components"]
    verified_components = atlas_metrics["verified_components"]
    untested_verified = int(atlas_metrics.get("verified_untested_components") or 0)
    total_flows = atlas_metrics["total_flows"]
    open_gaps = atlas_metrics["open_gaps"]
    hub_bottlenecks = atlas_metrics["hub_bottlenecks"]
    untested_hdr = f" untested_verified={untested_verified}" if untested_verified else ""
    schem_lines = [
        f"[BOWTHRUSTER_ATLAS LOD0 boards={len(boards)} components={total_components} "
        f"verified={verified_components}/{total_components}{untested_hdr} "
        f"flows={total_flows} open_gaps={open_gaps}]"
    ]
    for bname, board in boards:
        ch = _extract_board_charter(bname, board)
        if ch:
            prin_sfx = f" (principles: {ch['principles']})" if ch["principles"] else ""
            bv = (atlas_metrics.get("board_schema_versions") or {}).get(bname) or {}
            act_v = bv.get("active", board.get("schema_version", 2))
            if bv.get("needs_migration"):
                mig_tag = f" [schema=v{bv.get('on_disk', 1)}->v{act_v} NEEDS_MIGRATION]"
            else:
                mig_tag = f" [schema=v{act_v}]"
            schem_lines.append(f"- @ CHARTER [{ch['name']}]: {ch['summary']}{prin_sfx}{mig_tag}")
        raw_scopes = board.get("scopes")
        for k, sc in (raw_scopes.items() if isinstance(raw_scopes, dict) else []):
            if k == "global_defaults" or str(k).startswith("global_defaults::"):
                for r in sc.get("rules") or []:
                    if r.get("text") and not str(r.get("text") or "").startswith("Charter ["):
                        schem_lines.append(
                            f"- * GLOBAL_RULE [{r.get('type', 'RULE')}]: {r.get('text')}"
                        )
    mermaid_lines = ["flowchart LR"]
    for sid, c in components.items():
        deps_str = ", ".join(c["depends_on"])
        owner_str = c["owner"] or "unassigned"
        role_tail = f": {c['role']}" if c.get("role") else ""
        if c.get("why"):
            role_tail += f" [why: {c['why']}]"
        if c["build_status"] == "VERIFIED" and bool(c.get("verified_untested")):
            disp_status = "VERIFIED (untested)"
        else:
            disp_status = c["build_status"]
        claim_sfx = f", claimed_by={c['claimed_by']}" if c.get("claimed_by") else ""
        m_claim = f", claimed:{c['claimed_by']}" if c.get("claimed_by") else ""
        schem_lines.append(
            f"- @ COMPONENT [{sid}] (board={c['board']}, status={disp_status}{claim_sfx}, "
            f"owner={owner_str}, depends_on=[{deps_str}]){role_tail}"
        )
        mermaid_lines.append(f'  {sid}["{sid} ({disp_status}, {owner_str}{m_claim})"]')
    for fl in flows:
        pay_tag = f" [{fl['payload']}]" if fl["payload"] else ""
        schem_lines.append(f"- @ FLOW {fl['source']} -> {fl['target']}{pay_tag}")
        if fl["payload"]:
            mermaid_lines.append(f"  {fl['source']} -->|{fl['payload']}| {fl['target']}")
        else:
            mermaid_lines.append(f"  {fl['source']} --> {fl['target']}")
    untested_met = (
        f" verified_untested_components={untested_verified}" if untested_verified else ""
    )
    schem_lines.append(
        f"- @ METRICS total_components={total_components} "
        f"verified_components={verified_components}{untested_met} "
        f"total_flows={total_flows} open_gaps={open_gaps} "
        f"hub_bottlenecks=[{', '.join(hub_bottlenecks)}]"
    )
    atlas_schematic = "\n".join(schem_lines)
    atlas_mermaid = "\n".join(mermaid_lines)
    atlas_spec = _render_board_spec(boards, components, flows, atlas_metrics, atlas_mermaid)
    hw_edges = ", ".join(f"{fl['source']}->{fl['target']}" for fl in flows) or "none"
    raw_sum = (
        f"LOD0 Lattice Atlas: {len(boards)} boards, {total_components} components "
        f"({verified_components} VERIFIED), {total_flows} flows, {open_gaps} gaps. "
        f"Highway: {hw_edges}"
    )
    clamped_sum = " ".join(raw_sum.split()[:MAX_SLOT_SUMMARY_WORDS])
    state = create_empty_table_state() if table_state is None else table_state
    _, max_slots, max_tokens, _ = _ensure_table_state_dict(state)
    slot_cand = validate_table_slot(
        {
            "slot_id": "slot_lattice_atlas",
            "concept_id": "lattice_atlas",
            "lod_tier": LOD_0,
            "summary": clamped_sum,
            "updated_turn": current_turn,
        },
        max_tokens=max_tokens,
    )
    pinned_other = [
        s for s in state.get("slots", []) if s.get("pinned") and s.get("slot_id") != "slot_lattice_atlas"
    ]
    budget_ok = should_admit_table_slot(
        len(pinned_other) + 1 <= max_slots,
        sum(int(s["token_cost"]) for s in pinned_other) + int(slot_cand["token_cost"]) <= max_tokens,
    )
    mounted = should_mount_global_atlas(
        bool(has_local_seed_cues), bool(explicit_atlas_request), bool(budget_ok)
    )
    if mounted:
        try:
            admit_or_evict_slot(state, slot_cand, current_turn=current_turn)
        except ValueError:
            mounted = False
    view = render_table_view(state)
    view.update({
        "atlas_mounted": mounted,
        "atlas_schematic": atlas_schematic,
        "atlas_mermaid": atlas_mermaid,
        "atlas_metrics": atlas_metrics,
        "atlas_spec": atlas_spec,
        "table_state": state,
    })
    return view
