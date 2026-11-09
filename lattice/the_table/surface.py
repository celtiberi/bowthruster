"""Stage 25A (`the_table.surface`): Slot Allocation, Pinning Immunity, and Priority/LRU Eviction (`surface.py`)."""

from __future__ import annotations

import copy
from typing import Any

from .schema import (
    LOD_0, LOD_1, LOD_2, MAX_PINNED_SLOTS, MAX_SLOT_SUMMARY_WORDS, MAX_TABLE_SLOTS, MAX_TABLE_TOKENS,
    TableSlot, TableState, TableView, bool_eq, can_evict_table_slot, create_empty_table_state,
    should_admit_table_slot, should_evict_first_over_second, should_mount_global_atlas,
    sync_table_to_sqlite, validate_table_slot, validate_table_state,
)

_BOARD_TOP_KEYS = frozenset({
    "scopes", "flows", "lattice_artifacts", "rules", "facts", "open_blockers", "lattice_proof", "ambient_notes",
})


def _ensure_table_state_dict(table_state: dict[str, Any]) -> tuple[list[TableSlot], int, int, int]:
    if not isinstance(table_state, dict):
        raise ValueError(f"table_state must be a dict or TableState, got {type(table_state).__name__}")
    max_slots = table_state.get("max_slots", MAX_TABLE_SLOTS)
    max_tokens = table_state.get("max_tokens", MAX_TABLE_TOKENS)
    max_pinned = table_state.get("max_pinned_slots", MAX_PINNED_SLOTS)
    if isinstance(max_slots, bool) or not isinstance(max_slots, int) or not (1 <= max_slots <= MAX_TABLE_SLOTS):
        raise ValueError(f"max_slots must be an int in [1, {MAX_TABLE_SLOTS}], got {max_slots!r}")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not (1 <= max_tokens <= MAX_TABLE_TOKENS):
        raise ValueError(f"max_tokens must be an int in [1, {MAX_TABLE_TOKENS}], got {max_tokens!r}")
    if isinstance(max_pinned, bool) or not isinstance(max_pinned, int) or not (0 <= max_pinned <= MAX_PINNED_SLOTS):
        raise ValueError(f"max_pinned_slots must be an int in [0, {MAX_PINNED_SLOTS}], got {max_pinned!r}")
    raw_slots = table_state.get("slots", [])
    if isinstance(raw_slots, dict):
        raw_slots = list(raw_slots.values())
    if not isinstance(raw_slots, list):
        raise ValueError(f"table_state['slots'] must be a list, got {type(raw_slots).__name__}")
    slots = [validate_table_slot(s, max_tokens=max_tokens) for s in raw_slots if isinstance(s, dict)]
    table_state["slots"], table_state["total_tokens"] = slots, sum(int(s["token_cost"]) for s in slots)
    table_state.setdefault("max_slots", max_slots)
    table_state.setdefault("max_tokens", max_tokens)
    table_state.setdefault("max_pinned_slots", max_pinned)
    return slots, max_slots, max_tokens, max_pinned


def _slot_priority_tuple(slot: dict[str, Any], index: int) -> tuple[float, int, int]:
    """Computes `(priority_or_activation, updated_turn, insertion_index)` so lower values are evicted first."""
    raw_prio = slot.get("priority") if slot.get("priority") is not None else slot.get("activation", 0.0)
    prio = float(raw_prio) if isinstance(raw_prio, (int, float)) and not isinstance(raw_prio, bool) else 0.0
    return (prio, int(slot.get("updated_turn", 1)), index)


def _select_eviction_victim_index(
    slots: list[TableSlot], *, protected_index: int, within_6_pinned_cap: bool
) -> int | None:
    """Selects the index of the lowest-priority evictable slot using Bend 2 Laws 2 & 3 (`can_evict_table_slot`, `should_evict_first_over_second`)."""
    best_idx: int | None = None
    for idx, cand in enumerate(slots):
        if idx == protected_index or not can_evict_table_slot(bool(cand.get("pinned", False)), True, within_6_pinned_cap):
            continue
        if best_idx is None:
            best_idx = idx
            continue
        best_slot = slots[best_idx]
        first_lower = _slot_priority_tuple(cand, idx) < _slot_priority_tuple(best_slot, best_idx)
        if should_evict_first_over_second(bool(cand.get("pinned", False)), bool(best_slot.get("pinned", False)), first_lower):
            best_idx = idx
    return best_idx


def admit_or_evict_slot(
    table_state: dict[str, Any], slot: dict[str, Any] | TableSlot, *, current_turn: int = 1
) -> TableSlot:
    """Admits or updates `slot` in `table_state`, enforcing `MAX_TABLE_SLOTS <= 16` and `MAX_TABLE_TOKENS <= 1200`
    via `should_admit_table_slot` and evicting lowest-priority unpinned slots via `can_evict_table_slot` and
    `should_evict_first_over_second` while keeping pinned slots (`<= 6`) immune to eviction."""
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

    existing_idx = next(
        (
            i for i, s in enumerate(slots)
            if s["slot_id"] == str(cand_dict.get("slot_id") or cand_dict.get("id") or cand_dict.get("concept_id") or "").strip()
            or (cand_dict.get("concept_id") and s["concept_id"] == str(cand_dict["concept_id"]).strip())
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

    pinned_cnt = sum(1 for s in work_slots if s["pinned"])
    if pinned_cnt > max_pinned:
        raise ValueError(f"max_pinned_slots ({max_pinned}) exceeded: cannot admit pinned slot {new_slot['slot_id']!r}")

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
            raise ValueError(
                f"Cannot admit slot {new_slot['slot_id']!r} (token_cost={new_slot['token_cost']}): "
                "insufficient evictable unpinned capacity on table"
            )
        evicted_slot = work_slots.pop(victim_idx)
        evicted_ids.append(evicted_slot["slot_id"])
        if victim_idx < target_idx:
            target_idx -= 1

    total_tokens = sum(int(s["token_cost"]) for s in work_slots)
    table_state["slots"] = work_slots
    table_state["total_tokens"] = total_tokens
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
    """Pins or unpins `slot_id` in `table_state` (enforcing `max_pinned_slots <= 6`) and returns the updated `TableSlot`."""
    if not isinstance(slot_id, str) or not slot_id.strip():
        raise ValueError(f"slot_id must be a non-empty str, got {slot_id!r}")
    if not isinstance(pinned, bool):
        raise ValueError(f"pinned must be a bool, got {pinned!r}")
    slots, _, _, max_pinned = _ensure_table_state_dict(table_state)
    target = slot_id.strip()
    idx = next((i for i, s in enumerate(slots) if s["slot_id"] == target or s["concept_id"] == target), None)
    if idx is None:
        raise KeyError(f"Slot {slot_id!r} not found in table_state['slots']")

    slot = slots[idx]
    if pinned and not slot["pinned"]:
        pinned_cnt = sum(1 for s in slots if s["pinned"])
        if pinned_cnt + 1 > max_pinned:
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
    """Evicts an unpinned `slot_id` from `table_state` (enforcing Law 2 `pinned_slot_eviction_immunity_law` unless `force=True`)."""
    if not isinstance(slot_id, str) or not slot_id.strip():
        return False
    slots, _, _, max_pinned = _ensure_table_state_dict(table_state)
    target = slot_id.strip()
    idx = next((i for i, s in enumerate(slots) if s["slot_id"] == target or s["concept_id"] == target), None)
    if idx is None:
        return False
    slot = slots[idx]
    pinned_cnt = sum(1 for s in slots if s["pinned"])
    if not force and not can_evict_table_slot(bool(slot["pinned"]), True, pinned_cnt <= max_pinned):
        raise ValueError(f"Cannot evict pinned slot {slot_id!r}: pinned_slot_eviction_immunity_law")
    slots.pop(idx)
    table_state["slots"] = slots
    table_state["total_tokens"] = sum(int(s["token_cost"]) for s in slots)
    sync_table_to_sqlite(table_state)
    return True


def get_active_slots(table_state: dict[str, Any]) -> list[TableSlot]:
    """Returns the ordered list of active `TableSlot` entries on `table_state` (`table_surface --[active_slots]--> table_renderer`)."""
    slots, _, _, _ = _ensure_table_state_dict(table_state)
    return list(slots)


def render_table_view(table_state: dict[str, Any]) -> TableView:
    """Renders `table_state` into a bounded `TableView` (`slots`, `total_tokens`, `compact_view`)."""
    slots, max_slots, max_tokens, _ = _ensure_table_state_dict(table_state)
    total_tokens = sum(int(s["token_cost"]) for s in slots)
    header = f"[TABLE_VIEW slots={len(slots)}/{max_slots} tokens={total_tokens}/{max_tokens}]"
    lines = [header]
    for s in slots:
        pin_tag = " [PINNED]" if s["pinned"] else ""
        lines.append(f"- [{s['slot_id']}] ({s['concept_id']}, {s['lod_tier']}{pin_tag}, cost={s['token_cost']}): {s['summary']}")
    compact = "\n".join(lines)
    return TableView({"slots": copy.deepcopy(slots), "total_tokens": total_tokens, "compact_view": compact, "text": compact})


def sync_memory_to_table(
    table_state: dict[str, Any] | None,
    recalled_concepts: list[dict[str, Any]],
    *,
    current_turn: int = 1,
) -> TableView:
    """Hydrates `recalled_concepts` (`ConceptFrames` from `ai_memory`) onto `table_state` and returns `TableView`."""
    state = create_empty_table_state() if table_state is None else table_state
    if not isinstance(recalled_concepts, list):
        raise ValueError(f"recalled_concepts must be a list, got {type(recalled_concepts).__name__}")
    for item in recalled_concepts:
        if not isinstance(item, dict):
            raise ValueError(f"recalled_concepts entry must be a dict, got {type(item).__name__}")
        raw_sum = str(item.get("summary") if "summary" in item else (item.get("lod1_synopsis") if "lod1_synopsis" in item else item.get("text", ""))).strip()
        clamped_sum = " ".join(raw_sum.split()[:MAX_SLOT_SUMMARY_WORDS])
        cid = str(item.get("concept_id") or item.get("id") or item.get("slot_id") or "").strip()
        sid = str(item.get("slot_id") or cid).strip()
        slot_cand = {
            **item,
            "slot_id": sid,
            "concept_id": cid,
            "summary": clamped_sum,
            "lod_tier": item.get("lod_tier", LOD_1),
            "pinned": bool(item.get("pinned", False)),
        }
        admit_or_evict_slot(state, slot_cand, current_turn=current_turn)
    view = render_table_view(state)
    view["table_state"] = state
    return view


def is_atlas_component_scope(has_comp_attrs_or_refs: bool = True, board_has_sbf_comps: bool = True) -> bool:
    """Pure atlas component scope filter governed by Bend 2 `atlas_component_filter_law` (Stage 25A)."""
    return bool(has_comp_attrs_or_refs or not board_has_sbf_comps)


def atlas_component_filter_law(has_comp_attrs_or_refs: bool = True, board_has_sbf_comps: bool = True) -> bool:
    """Alias for Bend 2 `atlas_component_filter_law` AST call-graph verification."""
    return is_atlas_component_scope(has_comp_attrs_or_refs, board_has_sbf_comps)


def _normalize_atlas_boards(
    whiteboard_boards: dict[str, dict[str, Any]] | list[dict[str, Any]] | dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Normalizes a single board dict, a list of board dicts, or `{board_name: board_dict}` into `[(board_name, board_dict)]`."""
    if isinstance(whiteboard_boards, dict):
        dict_vals = [v for v in whiteboard_boards.values() if isinstance(v, dict)]
        is_multi = bool(dict_vals) and not bool(_BOARD_TOP_KEYS & set(whiteboard_boards.keys()))
        if is_multi:
            return [(str(k).strip() or f"board_{i + 1}", v) for i, (k, v) in enumerate(whiteboard_boards.items()) if isinstance(v, dict)]
        bname = str(whiteboard_boards.get("board_name") or whiteboard_boards.get("board") or whiteboard_boards.get("name") or "default").strip() or "default"
        return [(bname, whiteboard_boards)]
    if isinstance(whiteboard_boards, list):
        return [
            (str(b.get("board_name") or b.get("board") or b.get("name") or f"board_{i + 1}").strip() or f"board_{i + 1}", b)
            for i, b in enumerate(whiteboard_boards)
            if isinstance(b, dict)
        ]
    raise ValueError(f"whiteboard_boards must be a dict or list of board dicts, got {type(whiteboard_boards).__name__}")


def _collect_atlas_topology(
    boards: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Two-pass topology collector: Pass 1 gathers flows, artifact refs, and explicit proof metrics across all boards;
    Pass 2 merges component scopes across all boards and computes `atlas_metrics` (`s25a-spec-laws-2`)."""
    components: dict[str, dict[str, Any]] = {}
    flows: list[dict[str, Any]] = []
    seen_flows: set[tuple[str, str, str]] = set()
    ref_sids: dict[str, str] = {}
    explicit_gaps = 0
    saw_explicit_gaps = False
    explicit_hubs: set[str] = set()

    for bname, board in boards:
        for fl in (board.get("flows") if isinstance(board.get("flows"), list) else []):
            if not isinstance(fl, dict):
                continue
            src, tgt = str(fl.get("source") or "").strip(), str(fl.get("target") or "").strip()
            payload = str(fl.get("payload") or "").strip() or None
            if src and tgt:
                if src != "global_defaults":
                    ref_sids.setdefault(src, bname)
                if tgt != "global_defaults":
                    ref_sids.setdefault(tgt, bname)
                fkey = (src, tgt, payload or "")
                if fkey not in seen_flows:
                    seen_flows.add(fkey)
                    flows.append({"source": src, "target": tgt, "payload": payload, "board": bname})
        for art in (board.get("lattice_artifacts") if isinstance(board.get("lattice_artifacts"), list) else []):
            if isinstance(art, dict):
                asid = str(art.get("scope_id") or art.get("scope") or "").strip()
                if asid and asid != "global_defaults":
                    ref_sids.setdefault(asid, bname)

        lp = board.get("lattice_proof") if isinstance(board.get("lattice_proof"), dict) else {}
        for g_src in (board.get("open_gaps"), lp.get("open_gaps")):
            if g_src is not None and not isinstance(g_src, bool):
                if isinstance(g_src, int):
                    saw_explicit_gaps = True
                    explicit_gaps += max(0, g_src)
                elif isinstance(g_src, list):
                    saw_explicit_gaps = True
                    explicit_gaps += len(g_src)
                elif isinstance(g_src, str) and g_src.strip().isdigit():
                    saw_explicit_gaps = True
                    explicit_gaps += int(g_src.strip())
        for h_src in (board.get("hub_bottlenecks"), lp.get("hub_bottlenecks")):
            if isinstance(h_src, list):
                explicit_hubs.update(str(h).strip() for h in h_src if str(h).strip())
        for blk in (board.get("open_blockers") if isinstance(board.get("open_blockers"), list) else []):
            if isinstance(blk, dict) and str(blk.get("status") or "").upper() != "RESOLVED":
                saw_explicit_gaps = True
                explicit_gaps += 1

    _has_comp = lambda s_id, s_d: bool(
        str(s_d.get("role") or "").strip() or str(s_d.get("owner") or "").strip() or str(s_d.get("stage") or "").strip()
        or [d for d in (s_d.get("depends_on") or []) if str(d).strip() and str(d).strip() != s_id]
        or s_id in ref_sids or str(s_d.get("kind") or "").upper() == "COMPONENT" or s_d.get("files")
        or (s_d.get("attributes") if isinstance(s_d.get("attributes"), dict) else {}).get("files")
        or (str(s_d.get("build_status") or s_d.get("status") or "DRAFT").strip().upper() or "DRAFT") != "DRAFT"
    )
    for bname, board in boards:
        scopes = board.get("scopes") if isinstance(board.get("scopes"), dict) else {}
        board_has_sbf_comps = bool(ref_sids) or any(_has_comp(str(k).strip(), v) for k, v in scopes.items() if str(k).strip() and str(k).strip() != "global_defaults" and isinstance(v, dict))
        for raw_sid, sc in scopes.items():
            sid = str(raw_sid).strip()
            if not sid or sid == "global_defaults" or not isinstance(sc, dict):
                continue
            st = "STALE" if "STALE" in (str(sc.get("status") or "").strip().upper(), str(sc.get("build_status") or "").strip().upper()) else (str(sc.get("status") if sc.get("status") not in (None, "", "DRAFT") else (sc.get("build_status") or "DRAFT")).strip().upper() or "DRAFT")
            owner, role, stage = str(sc.get("owner") or "").strip() or None, str(sc.get("role") or "").strip() or None, str(sc.get("stage") or "").strip() or None
            deps = [str(d).strip() for d in (sc.get("depends_on") or []) if str(d).strip() and str(d).strip() != sid]
            has_comp_attrs_or_refs = _has_comp(sid, sc)
            if not (is_atlas_component_scope(has_comp_attrs_or_refs, board_has_sbf_comps) and atlas_component_filter_law(has_comp_attrs_or_refs, board_has_sbf_comps)):
                continue
            if not (has_comp_attrs_or_refs or sc.get("build_status") or sc.get("status")):
                continue
            if sid not in components:
                components[sid] = {
                    "id": sid, "board": bname, "build_status": st, "status": st,
                    "owner": owner, "role": role, "stage": stage, "depends_on": list(deps),
                }
            else:
                cur = components[sid]
                if st != "DRAFT" and cur["build_status"] == "DRAFT":
                    cur["build_status"] = cur["status"] = st
                if owner and not cur["owner"]:
                    cur["owner"] = owner
                    cur["board"] = bname
                if role and not cur["role"]:
                    cur["role"] = role
                if stage and not cur["stage"]:
                    cur["stage"] = stage
                for d in deps:
                    if d not in cur["depends_on"]:
                        cur["depends_on"].append(d)

    for rsid, bname in ref_sids.items():
        if rsid not in components:
            components[rsid] = {
                "id": rsid, "board": bname, "build_status": "DRAFT", "status": "DRAFT",
                "owner": None, "role": None, "stage": None, "depends_on": [],
            }

    dependents_map: dict[str, set[str]] = {sid: set() for sid in components}
    for sid, c in components.items():
        for dep in c["depends_on"]:
            dependents_map.setdefault(dep, set()).add(sid)

    # Spec 25A (`s25a-spec-laws-2`): `hub_bottlenecks` = scopes imported by >= 3 dependents
    hub_bottlenecks = sorted(
        explicit_hubs | {sid for sid, imps in dependents_map.items() if len(imps) >= 3}
    )
    inferred_gaps = sum(
        1 for c in components.values()
        if not c["owner"] or c["build_status"] in ("FAILED", "BLOCKED_BY_DEP")
    )
    open_gaps = explicit_gaps if saw_explicit_gaps else inferred_gaps
    total_components = len(components)
    verified_components = sum(1 for c in components.values() if c["build_status"] == "VERIFIED")
    total_flows = len(flows)
    metrics: dict[str, Any] = {
        "total_components": total_components,
        "verified_components": verified_components,
        "total_flows": total_flows,
        "open_gaps": open_gaps,
        "hub_bottlenecks": hub_bottlenecks,
    }
    return components, flows, metrics


def render_lattice_atlas(
    whiteboard_boards: dict[str, dict[str, Any]] | list[dict[str, Any]] | dict[str, Any],
    *,
    table_state: dict[str, Any] | None = None,
    has_local_seed_cues: bool = False,
    explicit_atlas_request: bool = True,
    current_turn: int = 1,
) -> TableView:
    """Computes a multi-board `LOD_0` topological Lattice Atlas (`atlas_schematic`, `atlas_mermaid`, `atlas_metrics`)
    and conditionally mounts `slot_lattice_atlas` onto `table_state` per Bend 2 Law 4 (`should_mount_global_atlas`)."""
    boards = _normalize_atlas_boards(whiteboard_boards)
    components, flows, atlas_metrics = _collect_atlas_topology(boards)
    total_components, verified_components, total_flows, open_gaps, hub_bottlenecks = (
        atlas_metrics["total_components"], atlas_metrics["verified_components"], atlas_metrics["total_flows"], atlas_metrics["open_gaps"], atlas_metrics["hub_bottlenecks"]
    )
    schem_lines = [f"[LATTICE_ATLAS LOD0 boards={len(boards)} components={total_components} verified={verified_components}/{total_components} flows={total_flows} open_gaps={open_gaps}]"]
    mermaid_lines = ["flowchart LR"]
    for sid, c in components.items():
        deps_str, owner_str = ", ".join(c["depends_on"]), c["owner"] or "unassigned"
        schem_lines.append(f"- @ COMPONENT [{sid}] (board={c['board']}, status={c['build_status']}, owner={owner_str}, depends_on=[{deps_str}])")
        mermaid_lines.append(f'  {sid}["{sid} ({c["build_status"]}, {owner_str})"]')
    for fl in flows:
        pay_tag = f" [{fl['payload']}]" if fl["payload"] else ""
        schem_lines.append(f"- > FLOW {fl['source']} -> {fl['target']}{pay_tag}")
        mermaid_lines.append(f"  {fl['source']} -->|{fl['payload']}| {fl['target']}" if fl["payload"] else f"  {fl['source']} --> {fl['target']}")
    schem_lines.append(f"- # METRICS total_components={total_components} verified_components={verified_components} total_flows={total_flows} open_gaps={open_gaps} hub_bottlenecks=[{', '.join(hub_bottlenecks)}]")
    atlas_schematic, atlas_mermaid = "\n".join(schem_lines), "\n".join(mermaid_lines)

    hw_edges = ", ".join(f"{fl['source']}->{fl['target']}" for fl in flows) or "none"
    raw_sum = f"LOD0 Lattice Atlas: {len(boards)} boards, {total_components} components ({verified_components} VERIFIED), {total_flows} flows, {open_gaps} gaps. Highway: {hw_edges}"
    clamped_sum = " ".join(raw_sum.split()[:MAX_SLOT_SUMMARY_WORDS])

    state = create_empty_table_state() if table_state is None else table_state
    _, max_slots, max_tokens, _ = _ensure_table_state_dict(state)
    slot_cand = validate_table_slot({"slot_id": "slot_lattice_atlas", "concept_id": "lattice_atlas", "lod_tier": LOD_0, "summary": clamped_sum, "updated_turn": current_turn}, max_tokens=max_tokens)
    pinned_other = [s for s in state.get("slots", []) if isinstance(s, dict) and s.get("pinned") and s.get("slot_id") != "slot_lattice_atlas"]
    budget_ok = should_admit_table_slot(len(pinned_other) + 1 <= max_slots, sum(int(s["token_cost"]) for s in pinned_other) + int(slot_cand["token_cost"]) <= max_tokens)

    mounted = should_mount_global_atlas(bool(has_local_seed_cues), bool(explicit_atlas_request), bool(budget_ok))
    if mounted:
        try: admit_or_evict_slot(state, slot_cand, current_turn=current_turn)
        except ValueError: mounted = False

    view = render_table_view(state)
    view.update({"atlas_mounted": mounted, "atlas_schematic": atlas_schematic, "atlas_mermaid": atlas_mermaid, "atlas_metrics": atlas_metrics, "table_state": state})
    return view
