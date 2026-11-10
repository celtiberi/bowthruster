"""Spec 25 (`the_table`): Bounded Conference Room Working Surface, Renderer, & AI Memory Sync Package."""

from __future__ import annotations

import copy
import importlib
import importlib.util
from typing import Any

from .renderer import (
    LOD_0,
    LOD_1,
    LOD_2,
    MAX_FORAGING_STEPS,
    MAX_PINNED_SLOTS,
    MAX_SLOT_SUMMARY_WORDS,
    MAX_TABLE_SLOTS,
    MAX_TABLE_TOKENS,
    TLOD0,
    TLOD1,
    TLOD2,
    TableLOD,
    TableSlot,
    TableView,
    VALID_LOD_TIERS,
    _clamp_summary_words,
    bool_eq,
    format_table_slot_line,
    is_tlod1,
    lod_eq,
    lod_not_demoted,
    render_table_view,
    should_execute_foraging_step,
    step_slot_lod_escalation,
    upgrade_slot_lod,
)
from .schema import _pick


def _load_ai_memory() -> Any | None:
    try:
        if importlib.util.find_spec("bowthruster.ai_memory") is not None:
            return importlib.import_module("bowthruster.ai_memory")
        return None
    except Exception:
        return None


def _link_uri(lk: Any) -> str:
    return str(_pick(lk, "target_uri", "uri", default="")) if isinstance(lk, dict) else str(lk or "")


def _enrich_slot_from_graph_and_links(
    cid: str,
    cur_slot: dict[str, Any],
    d: dict[str, Any],
    whiteboard_graph: dict[str, Any] | None,
    store: Any,
    max_summary_words: int,
) -> tuple[str, list[Any], bool]:
    resolve_fn = getattr(_load_ai_memory(), "resolve_external_links", None)
    raw_d_links = d.get("external_links")
    links: list[Any] = copy.deepcopy(
        raw_d_links if isinstance(raw_d_links, list) else cur_slot.get("external_links", [])
    )
    source = whiteboard_graph if isinstance(whiteboard_graph, dict) else (store if store is not None else cur_slot)
    if callable(resolve_fn) and source is not None and cid:
        try:
            if isinstance(resolved := resolve_fn(source, cid, LOD_2), list) and resolved:
                seen = {_link_uri(lk) for lk in links if lk}
                for rlk in resolved:
                    if (u := _link_uri(rlk)) and u not in seen:
                        seen.add(u)
                        item = dict(rlk) if isinstance(rlk, dict) else {"target_uri": u, "uri": u, "lod_tier": LOD_2}
                        links.append(item)
        except Exception:
            pass
    target_uri = str(_pick(d, "target_uri", "uri", default="")).strip()
    if target_uri and target_uri not in {_link_uri(lk) for lk in links if lk}:
        links.append(
            {
                "link_id": f"link_{cid}_{len(links) + 1}",
                "target_uri": target_uri,
                "uri": target_uri,
                "artifact_kind": "doc",
                "lod_tier": LOD_2,
            }
        )
    sum_keys = ("summary", "lod1_synopsis", "synopsis", "text", "content")
    if any(k in d and d[k] is not None for k in sum_keys):
        raw_sum = str(_pick(d, *sum_keys, default="")).strip()
        return _clamp_summary_words(raw_sum, max_summary_words), links, (len(raw_sum.split()) <= max_summary_words)
    parts: list[str] = []
    base_sum = str(_pick(cur_slot, "summary", "lod1_synopsis", default="")).strip()
    if base_sum and base_sum != cid:
        parts.append(base_sum)
    if isinstance(whiteboard_graph, dict) and isinstance(sc := (whiteboard_graph.get("scopes") or {}).get(cid), dict):
        sc_attrs = (sc.get("attributes") or {})
        role = str(sc.get("role") if sc.get("role") else sc_attrs.get("lod1_synopsis", "")).strip()
        if role and role not in parts:
            parts.append(role)
        for r in ((sc.get("rules") or []))[:3]:
            if r.get("text") and (rt := str(r["text"]).strip()) not in parts:
                parts.append(rt)
    if target_uri and target_uri not in " ".join(parts):
        parts.append(f"[URI: {target_uri}]")
    return _clamp_summary_words(" | ".join(parts) if parts else (base_sum or cid), max_summary_words), links, True


def _expand_neighbors_for_concept(
    cid: str, whiteboard_graph: dict[str, Any] | None, store: Any
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
                        return [r for r in res]
                except Exception:
                    continue
    return []


def _normalize_foraging_directives(
    foraging_directives: list[Any] | dict[str, Any] | str | None,
) -> tuple[list[dict[str, Any]], int | None]:
    if foraging_directives is None:
        return [], None
    if isinstance(foraging_directives, str):
        s = foraging_directives.strip()
        return ([{"concept_id": s, "slot_id": s}] if s else []), None
    if isinstance(foraging_directives, (list, tuple)):
        out: list[dict[str, Any]] = []
        for idx, item in enumerate(foraging_directives):
            if isinstance(item, str) and item.strip():
                out.append({"concept_id": item.strip(), "slot_id": item.strip()})
            elif isinstance(item, dict):
                out.append(dict(item))
            else:
                msg = f"foraging_directives[{idx}] must be a dict or non-empty str, got {type(item).__name__}"
                raise ValueError(msg)
        return out, None
    if isinstance(foraging_directives, dict):
        dir_cap = foraging_directives.get("max_foraging_steps")
        for key in ("directives", "foraging_directives", "steps", "actions", "items"):
            if key in foraging_directives:
                raw_sub = foraging_directives[key]
                if not isinstance(raw_sub, (list, tuple)):
                    raise ValueError(f"foraging_directives[{key!r}] must be a list, got {type(raw_sub).__name__}")
                return _normalize_foraging_directives(list(raw_sub))[0], dir_cap
        action_keys = ("slot_id", "concept_id", "id", "action", "op", "target_lod", "lod_tier")
        if foraging_directives.get("foraging_requested") is False and not any(
            k in foraging_directives for k in action_keys
        ):
            return [], dir_cap
        return [dict(foraging_directives)], dir_cap
    tname = type(foraging_directives).__name__
    raise ValueError(f"foraging_directives must be a list, dict, str, or None, got {tname}")


def _record_forage_action(rec: dict[str, Any], ctx: dict[str, Any], sid: str | None = None) -> None:
    ctx["foraging_actions_applied"].append(rec)
    ctx["history_list"].append(copy.deepcopy(rec))
    if sid:
        ctx["foraged_slot_ids"].append(sid)


def _validate_int_bound(val: Any, low: int, high: int, label: str) -> int:
    if isinstance(val, bool) or not isinstance(val, int) or not (low <= val <= high):
        raise ValueError(f"{label} must be an int in [{low}, {high}], got {val!r}")
    return int(val)


def _validate_caps(
    max_table_slots: int = MAX_TABLE_SLOTS,
    max_table_tokens: int = MAX_TABLE_TOKENS,
    max_pinned_slots: int = MAX_PINNED_SLOTS,
    max_summary_words: int = MAX_SLOT_SUMMARY_WORDS,
) -> None:
    _validate_int_bound(max_table_slots, 1, MAX_TABLE_SLOTS, "max_table_slots")
    _validate_int_bound(max_table_tokens, 1, MAX_TABLE_TOKENS, "max_table_tokens")
    _validate_int_bound(max_pinned_slots, 0, MAX_PINNED_SLOTS, "max_pinned_slots")
    _validate_int_bound(max_summary_words, 1, MAX_SLOT_SUMMARY_WORDS, "slot_summary_words")


def _find_slot_index(slots: list[dict[str, Any]], sid_or_cid: str) -> int | None:
    if not (clean := str(sid_or_cid or "").strip()):
        return None
    alt = clean[5:] if clean.startswith("slot_") else f"slot_{clean}"
    for idx, s in enumerate(slots):
        pair = (str(s.get("slot_id") or "").strip(), str(s.get("concept_id") or "").strip())
        if clean in pair or alt in pair:
            return idx
    return None


def _slot_priority_key(slot: dict[str, Any], idx: int) -> tuple[bool, float, int, int]:
    return (
        bool(slot.get("pinned", False)),
        float(slot.get("activation", 0.0) or 0.0),
        int(slot.get("updated_turn", 0) or 0),
        -idx,
    )


from .workspace_sync import (
    can_evict_table_slot,
    create_empty_table_state,
    forage_table_context,
    pin_to_table,
    should_admit_table_slot,
    should_evict_first_over_second,
    sync_memory_to_table,
    unpin_from_table,
)

__all__ = [
    "LOD_0",
    "LOD_1",
    "LOD_2",
    "MAX_FORAGING_STEPS",
    "MAX_PINNED_SLOTS",
    "MAX_SLOT_SUMMARY_WORDS",
    "MAX_TABLE_SLOTS",
    "MAX_TABLE_TOKENS",
    "TLOD0",
    "TLOD1",
    "TLOD2",
    "TableLOD",
    "TableSlot",
    "TableView",
    "VALID_LOD_TIERS",
    "bool_eq",
    "can_evict_table_slot",
    "create_empty_table_state",
    "forage_table_context",
    "format_table_slot_line",
    "is_tlod1",
    "lod_eq",
    "lod_not_demoted",
    "pin_to_table",
    "render_table_view",
    "should_admit_table_slot",
    "should_evict_first_over_second",
    "should_execute_foraging_step",
    "step_slot_lod_escalation",
    "sync_memory_to_table",
    "unpin_from_table",
    "upgrade_slot_lod",
]

for _pkg_prefix in (__name__, "bowthruster.the_table"):
    for _submod in ("schema", "surface"):
        _full_name = f"{_pkg_prefix}.{_submod}"
        try:
            if importlib.util.find_spec(_full_name) is not None:
                _mod = importlib.import_module(_full_name)
                for _attr in getattr(_mod, "__all__", [k for k in dir(_mod) if not k.startswith("_")]):
                    if _attr not in globals():
                        globals()[_attr] = getattr(_mod, _attr)
                        if _attr not in __all__:
                            __all__.append(_attr)
        except Exception:
            pass
