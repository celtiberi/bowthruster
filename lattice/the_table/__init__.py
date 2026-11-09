"""Spec 25 (`the_table`): Bounded Conference Room Working Surface, Renderer, & AI Memory Sync Package."""

from __future__ import annotations

import importlib
import importlib.util

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

for _pkg_prefix in (__name__, "lattice.the_table"):
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
