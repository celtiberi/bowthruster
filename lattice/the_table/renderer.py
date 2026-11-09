"""Stage 25B (`table_renderer`): SBF Table View Renderer & Monotonic LOD Escalation (`renderer.py`)."""

from __future__ import annotations

import copy
from enum import Enum
import importlib
import importlib.util
from typing import Any

from lattice.graph_core import token_cost
from lattice.nlp import (
    restore_ascii_dots,
)

LOD_0, LOD_1, LOD_2 = "LOD0", "LOD1", "LOD2"
VALID_LOD_TIERS: tuple[str, ...] = (LOD_0, LOD_1, LOD_2)

# Lattice `--board the_table` bounds (`r_the_table_1`, `r_the_table_2`, `r_table_surface_3`, `r_table_renderer_4`, `r_table_renderer_5`)
MAX_TABLE_SLOTS = 16
MAX_TABLE_TOKENS = 1200
MAX_PINNED_SLOTS = 6
MAX_SLOT_SUMMARY_WORDS = 50
MAX_FORAGING_STEPS = 4


class TableLOD(str, Enum):
    """Discrete Table Slot Level-of-Detail tier governed by Bend 2 Law 1 (`lod_escalation_monotonicity_law`)."""

    TLOD0 = "LOD0"
    TLOD1 = "LOD1"
    TLOD2 = "LOD2"
    LOD0 = "LOD0"
    LOD1 = "LOD1"
    LOD2 = "LOD2"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TableLOD):
            return self.value == other.value
        if isinstance(other, dict) and "$" in other:
            tag = str(other["$"]).strip().rstrip("{}").upper()
            return tag in (self.name, self.value)
        if isinstance(other, str):
            s = other.strip().rstrip("{}").upper()
            return s in (self.value, self.name, f"L{self.value[-1]}")
        return False

    def __hash__(self) -> int:
        return hash(self.value)


TLOD0, TLOD1, TLOD2 = TableLOD.TLOD0, TableLOD.TLOD1, TableLOD.TLOD2
_LOD_RANK: dict[TableLOD, int] = {TableLOD.TLOD0: 0, TableLOD.TLOD1: 1, TableLOD.TLOD2: 2}


def _norm_lod(lod: Any) -> TableLOD:
    """Normalizes a `TableLOD`, string (`'LOD0'`/`'TLOD0'`/`'L0'`), Bend tag dict, or int `0..2` into `TableLOD`."""
    if isinstance(lod, TableLOD):
        return lod
    if isinstance(lod, dict) and "$" in lod:
        lod = lod["$"]
    if isinstance(lod, int) and not isinstance(lod, bool):
        if lod in (0, 1, 2):
            return (TableLOD.TLOD0, TableLOD.TLOD1, TableLOD.TLOD2)[lod]
        raise ValueError(f"Invalid TableLOD integer tier: {lod!r}")
    if isinstance(lod, str):
        s = lod.strip().rstrip("{}").upper()
        if s in ("LOD0", "TLOD0", "L0", "0"):
            return TableLOD.TLOD0
        if s in ("LOD1", "TLOD1", "L1", "1"):
            return TableLOD.TLOD1
        if s in ("LOD2", "TLOD2", "L2", "2"):
            return TableLOD.TLOD2
    raise ValueError(f"Invalid TableLOD value: {lod!r}")


def bool_eq(a: bool, b: bool) -> bool:
    """Bend 2 Law 1 helper (`TBLB.bool_eq`): boolean equality."""
    return bool(a) == bool(b)


def lod_eq(a: Any, b: Any) -> bool:
    """Bend 2 Law 1 helper (`TBLB.lod_eq`): structural equality on `TableLOD`."""
    return _norm_lod(a) == _norm_lod(b)


def is_tlod1(lod: Any) -> bool:
    """Bend 2 Law 1 helper (`TBLB.is_tlod1`): returns `True` iff `lod` is `TLOD1` (`'LOD1'`)."""
    return _norm_lod(lod) == TableLOD.TLOD1


def lod_not_demoted(prev: Any, next_lod: Any) -> bool:
    """Bend 2 Law 1 helper (`TBLB.lod_not_demoted`): returns `True` iff `rank(prev) <= rank(next_lod)`."""
    return _LOD_RANK[_norm_lod(prev)] <= _LOD_RANK[_norm_lod(next_lod)]


def step_slot_lod_escalation(
    cur_lod: Any,
    request_upgrade: bool = True,
    words_within_50: bool = True,
    budget_ok: bool = True,
) -> TableLOD:
    """Pure monotonic LOD escalation transition governed by Bend 2 Law 1 (`lod_escalation_monotonicity_law`):
    - When `request_upgrade` is `False`, preserves `cur_lod` unchanged;
    - Never demotes `cur_lod`;
    - Only escalates `TLOD0 -> TLOD1` (and `TLOD1 -> TLOD2`) when `request_upgrade and words_within_50 and budget_ok`."""
    lod = _norm_lod(cur_lod)
    if not bool(request_upgrade) or not bool(words_within_50) or not bool(budget_ok):
        return lod
    if lod == TableLOD.TLOD0:
        return TableLOD.TLOD1
    if lod == TableLOD.TLOD1:
        return TableLOD.TLOD2
    return TableLOD.TLOD2


def should_execute_foraging_step(
    foraging_requested: bool,
    steps_under_max: bool,
    budget_ok: bool,
) -> bool:
    """Bend 2 Law 2 (`stage2_foraging_budget_law`): returns `True` iff
    `foraging_requested == True` AND `steps_under_max == True` AND `budget_ok == True`."""
    return bool(foraging_requested) and bool(steps_under_max) and bool(budget_ok)


def _clamp_summary_words(text: Any, max_words: int = MAX_SLOT_SUMMARY_WORDS) -> str:
    """Clamps a slot summary string to at most `max_words` (`<= 50`) whitespace-delimited words (`r_table_renderer_4`)."""
    if isinstance(max_words, bool) or not isinstance(max_words, int) or not (1 <= max_words <= MAX_SLOT_SUMMARY_WORDS):
        raise ValueError(f"slot_summary_words must be an int in [1, {MAX_SLOT_SUMMARY_WORDS}], got {max_words!r}")
    cleaned = restore_ascii_dots(str(text or "")).strip()
    return " ".join(cleaned.split()[:max_words])


class TableSlot(dict[str, Any]):
    """Dict-compatible `TableSlot` record (`slot_id`, `concept_id`, `lod_tier`, `pinned`, `token_cost`, `summary`)."""

    def __init__(
        self,
        slot_id: str | dict[str, Any] = "",
        concept_id: str = "",
        lod_tier: str | TableLOD = LOD_1,
        pinned: bool = False,
        token_cost_val: int | None = None,
        *,
        summary: str | None = None,
        lod1_synopsis: str | None = None,
        external_links: list[Any] | None = None,
        activation: float | None = None,
        hop_distance: int | None = None,
        updated_turn: int | None = None,
        max_summary_words: int = MAX_SLOT_SUMMARY_WORDS,
        **extra: Any,
    ) -> None:
        super().__init__()
        self._table_state: dict[str, Any] | None = None
        if isinstance(slot_id, dict):
            src = dict(slot_id)
            raw_cid = str(src.get("concept_id") or src.get("scope_id") or src.get("id") or concept_id or "").strip()
            raw_sid = str(src.get("slot_id") or (f"slot_{raw_cid}" if raw_cid else "slot_1")).strip()
            raw_lod = src.get("lod_tier") or src.get("lod") or lod_tier
            raw_pin = bool(src.get("pinned", pinned))
            raw_sum = (
                summary
                if summary is not None
                else (
                    src.get("summary")
                    if src.get("summary") is not None
                    else (src.get("lod1_synopsis") if src.get("lod1_synopsis") is not None else (lod1_synopsis or src.get("text") or raw_cid))
                )
            )
            raw_cost = extra.pop("token_cost", token_cost_val if token_cost_val is not None else src.get("token_cost"))
            raw_links = external_links if external_links is not None else src.get("external_links", [])
            raw_act = activation if activation is not None else src.get("activation")
            raw_hop = hop_distance if hop_distance is not None else src.get("hop_distance", src.get("hop"))
            raw_turn = updated_turn if updated_turn is not None else src.get("updated_turn", src.get("turn"))
            extra_fields = {
                k: v for k, v in src.items()
                if k not in (
                    "slot_id", "concept_id", "lod_tier", "lod", "pinned", "token_cost",
                    "summary", "lod1_synopsis", "external_links", "activation", "hop_distance", "updated_turn",
                )
            } | extra
        else:
            raw_cid = str(concept_id or slot_id or "").strip()
            raw_sid = str(slot_id or (f"slot_{raw_cid}" if raw_cid else "slot_1")).strip()
            raw_lod = lod_tier
            raw_pin = bool(pinned)
            raw_sum = summary if summary is not None else (lod1_synopsis if lod1_synopsis is not None else str(extra.pop("text", raw_cid)))
            raw_cost = extra.pop("token_cost", token_cost_val)
            raw_links = external_links if external_links is not None else []
            raw_act, raw_hop, raw_turn = activation, hop_distance, updated_turn
            extra_fields = dict(extra)

        norm_lod = _norm_lod(raw_lod).value
        clamped_sum = _clamp_summary_words(raw_sum, max_summary_words)
        was_truncated = len(str(raw_sum or "").split()) > max_summary_words
        if not was_truncated and isinstance(raw_cost, int) and not isinstance(raw_cost, bool) and raw_cost > 0:
            eff_cost = int(raw_cost)
        else:
            eff_cost = max(1, token_cost(clamped_sum or raw_cid or raw_sid))

        self.update(
            slot_id=raw_sid,
            concept_id=raw_cid or raw_sid,
            lod_tier=norm_lod,
            pinned=raw_pin,
            token_cost=eff_cost,
            summary=clamped_sum,
            lod1_synopsis=clamped_sum,
            external_links=copy.deepcopy(raw_links) if isinstance(raw_links, list) else [],
        )
        if raw_act is not None:
            self["activation"] = float(raw_act)
        if raw_hop is not None:
            self["hop_distance"] = int(raw_hop)
        if raw_turn is not None:
            self["updated_turn"] = int(raw_turn)
        self.update(extra_fields)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return super().get(key, default)
        if self._table_state is not None and key in ("slots", "active_slots", "total_tokens", "compact_view"):
            return self._table_state.get(key, default)
        return default

    def __getitem__(self, key: Any) -> Any:
        if key in self:
            return super().__getitem__(key)
        if self._table_state is not None and key in ("slots", "active_slots", "total_tokens", "compact_view"):
            return self._table_state[key]
        raise KeyError(key)

    def __getattr__(self, name: str) -> Any:
        if name == "_table_state":
            return object.__getattribute__(self, "_table_state")
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


class TableView(dict[str, Any]):
    """Dict-compatible `TableView` return object (`slots`, `total_tokens`, `compact_view`) for `table_renderer`."""

    def __init__(
        self,
        slots: list[dict[str, Any]] | dict[str, Any] | None = None,
        total_tokens: int = 0,
        compact_view: str = "",
        **extra: Any,
    ) -> None:
        super().__init__()
        if isinstance(slots, dict) and ("slots" in slots or "compact_view" in slots or "total_tokens" in slots):
            src = dict(slots)
            raw_slots = list(src.get("slots", []))
            raw_tokens = int(src.get("total_tokens", sum(int(s.get("token_cost", 0)) for s in raw_slots if isinstance(s, dict))))
            raw_view = str(src.get("compact_view", compact_view))
            extra_fields = {k: v for k, v in src.items() if k not in ("slots", "total_tokens", "compact_view")} | extra
        else:
            raw_slots = list(slots) if isinstance(slots, list) else []
            raw_tokens = int(total_tokens) if total_tokens else sum(int(s.get("token_cost", 0)) for s in raw_slots if isinstance(s, dict))
            raw_view = str(compact_view or "")
            extra_fields = dict(extra)

        self.update(
            slots=raw_slots,
            total_tokens=raw_tokens,
            compact_view=raw_view,
            slot_count=len(raw_slots),
            pinned_count=sum(1 for s in raw_slots if isinstance(s, dict) and bool(s.get("pinned"))),
        )
        self.update(extra_fields)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


def format_table_slot_line(slot: dict[str, Any], *, max_summary_words: int = MAX_SLOT_SUMMARY_WORDS) -> str:
    """Formats a single `TableSlot` into its canonical SBF `@ TABLE_SLOT` compact view line."""
    norm = slot if isinstance(slot, TableSlot) else TableSlot(slot, max_summary_words=max_summary_words)
    summary = _clamp_summary_words(norm.get("summary") or norm.get("lod1_synopsis") or norm.get("concept_id"), max_summary_words)
    norm["summary"] = summary
    norm["lod1_synopsis"] = summary
    return (
        f"@ TABLE_SLOT [{norm['slot_id']}] "
        f"(concept={norm['concept_id']}, lod={norm['lod_tier']}, pinned={bool(norm['pinned'])}, tokens={int(norm['token_cost'])}): "
        f"{summary}"
    )


def _extract_raw_slots(table_state: dict[str, Any] | list[Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if table_state is None:
        state_dict: dict[str, Any] = {"slots": [], "total_tokens": 0}
        return state_dict, []
    if isinstance(table_state, list):
        raw_list = [s for s in table_state if isinstance(s, dict)]
        return {"slots": raw_list, "total_tokens": 0}, raw_list
    if not isinstance(table_state, dict):
        raise ValueError(f"table_state must be a dict, list, or None, got {type(table_state).__name__}")
    raw_slots = table_state.get("slots") if "slots" in table_state else table_state.get("active_slots", [])
    if isinstance(raw_slots, dict):
        raw_list = [v for v in raw_slots.values() if isinstance(v, dict)]
    elif isinstance(raw_slots, list):
        raw_list = [s for s in raw_slots if isinstance(s, dict)]
    else:
        raise ValueError(f"table_state['slots'] must be a list or dict, got {type(raw_slots).__name__}")
    return table_state, raw_list


def render_table_view(
    table_state: dict[str, Any] | list[Any] | None,
    *,
    max_summary_words: int = MAX_SLOT_SUMMARY_WORDS,
    max_table_slots: int = MAX_TABLE_SLOTS,
    max_table_tokens: int = MAX_TABLE_TOKENS,
) -> TableView:
    """Renders `table_state` into a bounded `TableView` dict (`slots`, `total_tokens`, `compact_view`)
    enforcing `slot_summary_words <= 50` (`r_table_renderer_4`), `max_table_slots <= 16` (`r_the_table_1`),
    and `max_table_tokens <= 1200` (`r_the_table_2`)."""
    if isinstance(max_summary_words, bool) or not isinstance(max_summary_words, int) or not (1 <= max_summary_words <= MAX_SLOT_SUMMARY_WORDS):
        raise ValueError(f"slot_summary_words must be an int in [1, {MAX_SLOT_SUMMARY_WORDS}], got {max_summary_words!r}")
    if isinstance(max_table_slots, bool) or not isinstance(max_table_slots, int) or not (1 <= max_table_slots <= MAX_TABLE_SLOTS):
        raise ValueError(f"max_table_slots must be an int in [1, {MAX_TABLE_SLOTS}], got {max_table_slots!r}")
    if isinstance(max_table_tokens, bool) or not isinstance(max_table_tokens, int) or not (1 <= max_table_tokens <= MAX_TABLE_TOKENS):
        raise ValueError(f"max_table_tokens must be an int in [1, {MAX_TABLE_TOKENS}], got {max_table_tokens!r}")

    state_dict, raw_list = _extract_raw_slots(table_state)
    eff_slot_cap = min(int(state_dict.get("max_table_slots", max_table_slots) or max_table_slots), max_table_slots)
    eff_token_cap = min(int(state_dict.get("max_table_tokens", max_table_tokens) or max_table_tokens), max_table_tokens)

    normalized_candidates = [TableSlot(s, max_summary_words=max_summary_words) for s in raw_list]
    if len(normalized_candidates) > eff_slot_cap or sum(int(s["token_cost"]) for s in normalized_candidates) > eff_token_cap:
        ordered_candidates = sorted(
            enumerate(normalized_candidates),
            key=lambda pair: (not bool(pair[1].get("pinned")), pair[0]),
        )
        admitted_indices: set[int] = set()
        running_tokens = 0
        for idx, cand in ordered_candidates:
            cost = int(cand["token_cost"])
            if len(admitted_indices) < eff_slot_cap and (running_tokens + cost) <= eff_token_cap:
                admitted_indices.add(idx)
                running_tokens += cost
        rendered_slots = [cand for idx, cand in enumerate(normalized_candidates) if idx in admitted_indices]
    else:
        rendered_slots = normalized_candidates

    lines = [format_table_slot_line(s, max_summary_words=max_summary_words) for s in rendered_slots]
    total_tokens = sum(int(s["token_cost"]) for s in rendered_slots)
    compact_view = "\n".join(lines)

    state_dict["slots"] = rendered_slots
    state_dict["active_slots"] = rendered_slots
    state_dict["total_tokens"] = total_tokens
    state_dict["compact_view"] = compact_view

    return TableView(
        slots=rendered_slots,
        total_tokens=total_tokens,
        compact_view=compact_view,
        table_state=state_dict,
    )


def upgrade_slot_lod(
    table_state: dict[str, Any],
    slot_id: str,
    target_lod: str | TableLOD = LOD_1,
    *,
    summary: str | None = None,
    external_links: list[Any] | None = None,
    max_table_tokens: int = MAX_TABLE_TOKENS,
    max_summary_words: int = MAX_SLOT_SUMMARY_WORDS,
    current_turn: int | None = None,
) -> TableSlot:
    """Monotonically upgrades `slot_id` in `table_state` toward `target_lod` via `step_slot_lod_escalation`
    when `words_within_50` and `budget_ok` hold (Law 1 `lod_escalation_monotonicity_law`)."""
    if not isinstance(table_state, dict):
        raise ValueError(f"table_state must be a dict, got {type(table_state).__name__}")
    if not isinstance(slot_id, str) or not slot_id.strip():
        raise ValueError(f"slot_id must be a non-empty str, got {slot_id!r}")
    if isinstance(max_summary_words, bool) or not isinstance(max_summary_words, int) or not (1 <= max_summary_words <= MAX_SLOT_SUMMARY_WORDS):
        raise ValueError(f"slot_summary_words must be an int in [1, {MAX_SLOT_SUMMARY_WORDS}], got {max_summary_words!r}")

    tgt_lod = _norm_lod(target_lod)
    _, raw_list = _extract_raw_slots(table_state)
    sid_clean = slot_id.strip()

    target_idx = next(
        (
            i for i, s in enumerate(raw_list)
            if str(s.get("slot_id") or "").strip() == sid_clean or str(s.get("concept_id") or "").strip() == sid_clean
        ),
        None,
    )
    if target_idx is None:
        raise KeyError(f"TableSlot {slot_id!r} not found in table_state")

    raw_slot = raw_list[target_idx]
    cur_lod = _norm_lod(raw_slot.get("lod_tier") or raw_slot.get("lod") or LOD_0)
    raw_cand_summary = (
        str(summary).strip()
        if summary is not None
        else str(raw_slot.get("lod1_synopsis") or raw_slot.get("summary") or raw_slot.get("concept_id") or sid_clean).strip()
    )
    words_within_50 = (
        len(raw_cand_summary.split()) <= max_summary_words
        if summary is not None
        else True
    )
    clamped_summary = _clamp_summary_words(raw_cand_summary, max_summary_words)
    cand_cost = (
        int(raw_slot["token_cost"])
        if (summary is None and isinstance(raw_slot.get("token_cost"), int) and raw_slot["token_cost"] > 0)
        else max(1, token_cost(clamped_summary or str(raw_slot.get("concept_id") or sid_clean)))
    )

    eff_token_cap = min(int(table_state.get("max_table_tokens", max_table_tokens) or max_table_tokens), MAX_TABLE_TOKENS)
    other_tokens = sum(int(s.get("token_cost", 0)) for i, s in enumerate(raw_list) if i != target_idx)
    budget_ok = (other_tokens + cand_cost) <= eff_token_cap

    request_upgrade = _LOD_RANK[tgt_lod] > _LOD_RANK[cur_lod]
    if request_upgrade:
        next_lod = cur_lod
        while _LOD_RANK[next_lod] < _LOD_RANK[tgt_lod]:
            stepped = step_slot_lod_escalation(next_lod, True, words_within_50, budget_ok)
            if stepped == next_lod:
                break
            next_lod = stepped
    else:
        next_lod = step_slot_lod_escalation(cur_lod, False, words_within_50, budget_ok)

    if next_lod != cur_lod or (summary is not None and words_within_50 and budget_ok):
        raw_slot["lod_tier"] = next_lod.value
        raw_slot["summary"] = clamped_summary
        raw_slot["lod1_synopsis"] = clamped_summary
        raw_slot["token_cost"] = cand_cost
        if external_links is not None:
            raw_slot["external_links"] = copy.deepcopy(external_links)
        if current_turn is not None:
            raw_slot["updated_turn"] = int(current_turn)

    updated_slot = TableSlot(raw_slot, max_summary_words=max_summary_words)
    raw_list[target_idx] = updated_slot
    table_state["slots"] = raw_list
    table_state["active_slots"] = raw_list
    table_state["total_tokens"] = sum(int(s.get("token_cost", 0)) for s in raw_list)
    table_state["compact_view"] = "\n".join(
        format_table_slot_line(s, max_summary_words=max_summary_words) for s in raw_list
    )
    updated_slot._table_state = table_state
    return updated_slot


def forage_table_context(
    table_state: dict[str, Any] | list[Any] | None = None,
    foraging_directives: list[Any] | dict[str, Any] | str | None = None,
    **kwargs: Any,
) -> TableView:
    """Stage 2 Active Prefrontal Foraging interface (`forage_table_context`): executes at most
    `MAX_FORAGING_STEPS <= 2` budget-gated foraging steps via `should_execute_foraging_step`
    and returns the rendered `TableView`."""
    from .workspace_sync import forage_table_context as _forage_impl

    return _forage_impl(table_state, foraging_directives, **kwargs)
