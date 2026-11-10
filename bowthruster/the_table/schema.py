"""Stage 25A (`the_table.schema`): TableSlot & TableState Schemas, Budget Conservation Laws, and SQLite Persistence (`schema.py`)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore, token_cost as _compute_token_cost
from bowthruster.nlp import restore_ascii_dots

# Lattice `the_table` bounds (`r_the_table_1`, `r_the_table_2`, `r_table_surface_3`, `r_table_renderer_4`)
MAX_TABLE_SLOTS = 16
MAX_TABLE_TOKENS = 1200
MAX_PINNED_SLOTS = 6
MAX_SLOT_SUMMARY_WORDS = 50

LOD_0, LOD_1, LOD_2 = "LOD0", "LOD1", "LOD2"
VALID_LOD_TIERS = (LOD_0, LOD_1, LOD_2)
_LOD_ALIAS_MAP = {
    "LOD0": LOD_0, "L0": LOD_0, "0": LOD_0,
    "LOD1": LOD_1, "L1": LOD_1, "1": LOD_1,
    "LOD2": LOD_2, "L2": LOD_2, "2": LOD_2,
}
_BASE_SLOT_KEYS = frozenset({"slot_id", "concept_id", "lod_tier", "pinned", "token_cost", "summary", "external_links", "updated_turn"})
_TABLE_SLOTS_DDL = """
CREATE TABLE IF NOT EXISTS table_slots (
    slot_id TEXT PRIMARY KEY, concept_id TEXT NOT NULL, lod_tier TEXT NOT NULL,
    pinned INTEGER NOT NULL, token_cost INTEGER NOT NULL, summary TEXT NOT NULL,
    external_links_json TEXT NOT NULL, updated_turn INTEGER NOT NULL, extra_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_table_slots_concept_id ON table_slots(concept_id);
"""


def bool_eq(a: bool, b: bool) -> bool:
    """Pure boolean equality helper mirroring `TBLA.bool_eq` in `bend_table_25a/main.bend`."""
    return bool(a) == bool(b)


def should_admit_table_slot(within_16_slots: bool, within_1200_tokens: bool) -> bool:
    """Bend 2 Law 1 (`table_token_budget_conservation_law`): returns `True` iff both slot count and token budget are within bounds."""
    return bool(within_16_slots) and bool(within_1200_tokens)


def can_evict_table_slot(is_pinned: bool, is_over_capacity: bool, within_6_pinned_cap: bool = True) -> bool:
    """Bend 2 Law 2 (`pinned_slot_eviction_immunity_law`): pinned slots within the 6-pinned cap are immune to eviction."""
    return (False if bool(within_6_pinned_cap) else bool(is_over_capacity)) if bool(is_pinned) else bool(is_over_capacity)


def should_evict_first_over_second(first_pinned: bool, second_pinned: bool, first_lower_priority: bool) -> bool:
    """Bend 2 Law 3 (`slot_eviction_order_law`): never evicts pinned over unpinned, always evicts unpinned over pinned, else uses priority."""
    if bool(first_pinned):
        return False
    if bool(second_pinned):
        return True
    return bool(first_lower_priority)


def should_mount_global_atlas(has_local_seed_cues: bool, explicit_atlas_request: bool, budget_ok: bool = True) -> bool:
    """Bend 2 Law 4 (`atlas_mode_isolation_law`): mounts the global LOD_0 Atlas iff budget is OK and (explicit request or no local seed cues)."""
    return bool(budget_ok) and (bool(explicit_atlas_request) or not bool(has_local_seed_cues))


def normalize_lod_tier(lod_tier: Any) -> str:
    """Normalizes an LOD tier token (`'LOD0'`, `'LOD1'`, `'LOD2'`, `'L0'`..`'L2'`, or `0`..`2`) into canonical `'LOD0'|'LOD1'|'LOD2'`."""
    if isinstance(lod_tier, bool):
        raise ValueError(f"Invalid lod_tier={lod_tier!r}")
    key = str(lod_tier if lod_tier is not None else LOD_1).strip().upper() or LOD_1
    if key not in _LOD_ALIAS_MAP:
        raise ValueError(f"Invalid lod_tier={lod_tier!r}, expected one of {VALID_LOD_TIERS}")
    return _LOD_ALIAS_MAP[key]


def _normalize_slot_links(raw_links: Any, concept_id: str) -> list[dict[str, Any]]:
    if raw_links is None:
        return []
    if not isinstance(raw_links, list):
        raise ValueError(f"external_links must be a list, got {type(raw_links).__name__}")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_links):
        if isinstance(item, str) and item.strip():
            uri = item.strip()
            if uri not in seen:
                seen.add(uri)
                out.append({"link_id": f"link_{concept_id}_{idx + 1}", "target_uri": uri, "uri": uri, "artifact_kind": "doc", "lod_tier": LOD_1})
        elif isinstance(item, dict):
            uri = str(item.get("target_uri") or item.get("uri") or "").strip()
            if not uri:
                raise ValueError(f"external_links[{idx}] missing non-empty 'target_uri'/'uri'")
            if uri not in seen:
                seen.add(uri)
                entry = copy.deepcopy(dict(item))
                entry.setdefault("target_uri", uri)
                entry.setdefault("uri", uri)
                entry.setdefault("link_id", f"link_{concept_id}_{idx + 1}")
                entry.setdefault("artifact_kind", str(entry.get("kind") or "doc"))
                entry.setdefault("lod_tier", LOD_1)
                out.append(entry)
        else:
            raise ValueError(f"Invalid external link entry: {item!r}")
    return out


class _TableBaseDict(dict[str, Any]):
    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


class TableSlot(_TableBaseDict):
    """Canonical `the_table` TableSlot schema (`slot_id`, `concept_id`, `lod_tier`, `pinned`, `token_cost`, `summary`, `external_links`, `updated_turn`)."""

    def __init__(
        self, slot_id: str | dict[str, Any] = "", concept_id: str = "", lod_tier: str = LOD_1, pinned: bool = False,
        token_cost: int | None = None, summary: str = "", external_links: list[Any] | None = None, updated_turn: int = 1, **extra: Any,
    ) -> None:
        super().__init__()
        object.__setattr__(self, "_table_state", None)
        object.__setattr__(self, "_evicted_ids", [])
        if isinstance(slot_id, dict):
            src = dict(slot_id) | extra
            raw_cid = str(src.get("concept_id") or src.get("id") or src.get("slot_id") or "").strip()
            raw_sid = str(src.get("slot_id") or src.get("id") or raw_cid).strip()
            raw_tier, raw_pinned = src.get("lod_tier", lod_tier), src.get("pinned", pinned)
            raw_sum = src.get("summary") if "summary" in src else (src.get("lod1_synopsis") if "lod1_synopsis" in src else src.get("text", summary))
            raw_cost = src.get("token_cost", token_cost)
            raw_links = src.get("external_links") if "external_links" in src else src.get("source_links", external_links)
            raw_turn = src.get("updated_turn", src.get("turn", updated_turn))
            rem = {k: copy.deepcopy(v) for k, v in src.items() if k not in (*_BASE_SLOT_KEYS, "lod1_synopsis", "source_links")}
        else:
            raw_sid = str(slot_id or concept_id or "").strip()
            raw_cid = str(concept_id or raw_sid).strip()
            raw_tier, raw_pinned = lod_tier, pinned
            raw_sum = summary if summary or "lod1_synopsis" not in extra else extra.pop("lod1_synopsis", "")
            raw_cost, raw_links, raw_turn, rem = token_cost, external_links, updated_turn, copy.deepcopy(extra)

        clean_sum = " ".join(restore_ascii_dots(str(raw_sum or "")).strip().split())
        comp_cost = int(raw_cost) if (raw_cost is not None and not isinstance(raw_cost, bool)) else max(1, _compute_token_cost(clean_sum or raw_cid or raw_sid or "slot"))
        self.update(
            slot_id=raw_sid, concept_id=raw_cid, lod_tier=normalize_lod_tier(raw_tier), pinned=bool(raw_pinned),
            token_cost=comp_cost, summary=clean_sum, external_links=_normalize_slot_links(raw_links, raw_cid or raw_sid),
            updated_turn=int(raw_turn if raw_turn is not None and not isinstance(raw_turn, bool) else 1), **rem,
        )

    def get(self, key: Any, default: Any = None) -> Any:
        if super().__contains__(key):
            return super().get(key, default)
        if key == "lod1_synopsis":
            return super().get("summary", default)
        if key == "source_links":
            return copy.deepcopy(super().get("external_links", default))
        if key == "admitted":
            return True
        if key in ("slot", "admitted_slot"):
            return self
        if key in ("evicted_slots", "evicted_slot_ids"):
            return list(object.__getattribute__(self, "_evicted_ids"))
        tstate = object.__getattribute__(self, "_table_state")
        if isinstance(tstate, dict):
            if key == "table_state":
                return tstate
            if key in ("slots", "active_slots", "total_tokens", "max_slots", "max_tokens", "max_pinned_slots"):
                return tstate.get(key, default)
        return default

    def __getitem__(self, key: Any) -> Any:
        if super().__contains__(key) or key in ("lod1_synopsis", "source_links", "admitted", "slot", "admitted_slot", "evicted_slots", "evicted_slot_ids"):
            return self.get(key)
        tstate = object.__getattribute__(self, "_table_state")
        if isinstance(tstate, dict) and key in ("table_state", "slots", "active_slots", "total_tokens", "max_slots", "max_tokens", "max_pinned_slots"):
            return self.get(key)
        raise KeyError(key)


class TableView(_TableBaseDict):
    """Canonical `TableView` schema (`slots`, `total_tokens`, `compact_view`)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        slots = [s if isinstance(s, TableSlot) else TableSlot(s) for s in self.get("slots", []) if isinstance(s, dict)]
        self["slots"] = slots
        self["total_tokens"] = int(self.get("total_tokens", sum(int(s.get("token_cost", 0)) for s in slots)))
        cview = str(self.get("compact_view") if "compact_view" in self else self.get("text", ""))
        self["compact_view"] = cview
        self.setdefault("text", cview)


class TableState(_TableBaseDict):
    """Bounded Conference Room working surface state (`slots`, `total_tokens`, `max_slots`, `max_tokens`, `max_pinned_slots`)."""

    def __init__(self, data: dict[str, Any] | None = None, *, store: SQLiteGraphStore | None = None, **kwargs: Any) -> None:
        inherited_store = store if store is not None else getattr(data, "_sqlite_store", None)
        super().__init__(data or {}, **kwargs)
        object.__setattr__(self, "_sqlite_store", inherited_store)
        raw_slots = list(self["slots"].values()) if isinstance(self.get("slots"), dict) else self.get("slots", [])
        slots = [s if isinstance(s, TableSlot) else TableSlot(s) for s in (raw_slots if isinstance(raw_slots, list) else []) if isinstance(s, dict)]
        self["slots"] = slots
        self["total_tokens"] = sum(int(s.get("token_cost", 0)) for s in slots)
        self.setdefault("max_slots", MAX_TABLE_SLOTS)
        self.setdefault("max_tokens", MAX_TABLE_TOKENS)
        self.setdefault("max_pinned_slots", MAX_PINNED_SLOTS)

    @property
    def store(self) -> SQLiteGraphStore | None:
        return object.__getattribute__(self, "_sqlite_store")

    @property
    def active_slots(self) -> list[TableSlot]:
        return self["slots"]

    @property
    def pinned_count(self) -> int:
        return sum(1 for s in self.get("slots", []) if isinstance(s, dict) and bool(s.get("pinned")))

    def get(self, key: Any, default: Any = None) -> Any:
        if key == "active_slots" and not super().__contains__("active_slots"):
            return super().get("slots", default)
        if key == "pinned_count" and not super().__contains__("pinned_count"):
            return self.pinned_count
        return super().get(key, default)

    def __getitem__(self, key: Any) -> Any:
        if key == "active_slots" and not super().__contains__("active_slots"):
            return super().__getitem__("slots")
        if key == "pinned_count" and not super().__contains__("pinned_count"):
            return self.pinned_count
        return super().__getitem__(key)

    def __deepcopy__(self, memo: dict[int, Any]) -> TableState:
        copied = TableState(copy.deepcopy(dict(self), memo), store=self.store)
        memo[id(self)] = copied
        return copied

    def __getattr__(self, name: str) -> Any:
        return object.__getattribute__(self, "_sqlite_store") if name == "_sqlite_store" else super().__getattr__(name)


def validate_table_slot(
    slot: dict[str, Any] | TableSlot, *, max_summary_words: int = MAX_SLOT_SUMMARY_WORDS, max_tokens: int = MAX_TABLE_TOKENS,
) -> TableSlot:
    """Validates `slot` against `TableSlot` schema and bounds (`slot_summary_words <= 50`, `token_cost <= 1200`)."""
    if not isinstance(slot, dict):
        raise ValueError(f"slot must be a dict or TableSlot, got {type(slot).__name__}")
    if isinstance(max_summary_words, bool) or not isinstance(max_summary_words, int) or not (1 <= max_summary_words <= MAX_SLOT_SUMMARY_WORDS):
        raise ValueError(f"max_summary_words must be an int in [1, {MAX_SLOT_SUMMARY_WORDS}], got {max_summary_words!r}")
    if not isinstance(slot.get("pinned", False), bool):
        raise ValueError(f"TableSlot.pinned must be a bool, got {slot.get('pinned')!r}")
    raw_cost = slot.get("token_cost")
    if raw_cost is not None and (isinstance(raw_cost, bool) or not isinstance(raw_cost, int) or raw_cost < 1):
        raise ValueError(f"TableSlot.token_cost must be a positive int, got {raw_cost!r}")
    raw_turn = slot.get("updated_turn", slot.get("turn", 1))
    if isinstance(raw_turn, bool) or not isinstance(raw_turn, int) or raw_turn < 0:
        raise ValueError(f"TableSlot.updated_turn must be a non-negative int, got {raw_turn!r}")

    raw_sum = slot.get("summary") if "summary" in slot else (slot.get("lod1_synopsis") if "lod1_synopsis" in slot else slot.get("text", ""))
    if not isinstance(raw_sum, str):
        raise ValueError(f"TableSlot.summary must be a str, got {type(raw_sum).__name__}")
    if len(restore_ascii_dots(raw_sum).strip().split()) > max_summary_words:
        raise ValueError(f"TableSlot.summary exceeds MAX_SLOT_SUMMARY_WORDS={max_summary_words}")

    ts = TableSlot(slot)
    if not ts["slot_id"] or not ts["concept_id"]:
        raise ValueError("TableSlot requires non-empty 'slot_id' and 'concept_id' strings")
    if ts["token_cost"] > max_tokens:
        raise ValueError(f"TableSlot.token_cost={ts['token_cost']} exceeds max_tokens={max_tokens}")
    return ts


def validate_table_state(table_state: dict[str, Any] | TableState) -> TableState:
    """Validates `table_state` against Law 1 (`table_token_budget_conservation_law`) and `max_pinned_slots <= 6`."""
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
    if not isinstance(raw_slots := table_state.get("slots", []), list):
        raise ValueError(f"table_state['slots'] must be a list, got {type(raw_slots).__name__}")

    validated_slots: list[TableSlot] = []
    seen_ids: set[str] = set()
    for s in raw_slots:
        vs = validate_table_slot(s, max_tokens=max_tokens)
        if vs["slot_id"] in seen_ids:
            raise ValueError(f"Duplicate slot_id {vs['slot_id']!r} in table_state['slots']")
        seen_ids.add(vs["slot_id"])
        validated_slots.append(vs)

    computed_tokens = sum(int(s["token_cost"]) for s in validated_slots)
    if "total_tokens" in table_state and (decl_tokens := table_state["total_tokens"]) is not None:
        if isinstance(decl_tokens, bool) or not isinstance(decl_tokens, int) or decl_tokens != computed_tokens:
            raise ValueError(f"table_state['total_tokens']={decl_tokens!r} != computed {computed_tokens}")
    if sum(1 for s in validated_slots if s["pinned"]) > max_pinned:
        raise ValueError(f"pinned slot count exceeds max_pinned_slots={max_pinned}")
    if not should_admit_table_slot(len(validated_slots) <= max_slots, computed_tokens <= max_tokens):
        raise ValueError(f"table_token_budget_conservation_law violated: slots={len(validated_slots)}/{max_slots}, tokens={computed_tokens}/{max_tokens}")

    store = getattr(table_state, "_sqlite_store", None) or table_state.get("_sqlite_store")
    return TableState(
        {**table_state, "slots": validated_slots, "total_tokens": computed_tokens, "max_slots": max_slots, "max_tokens": max_tokens, "max_pinned_slots": max_pinned},
        store=store if isinstance(store, SQLiteGraphStore) else None,
    )


def _ensure_table_slots_ddl(store: SQLiteGraphStore) -> None:
    with store.conn:
        store.conn.executescript(_TABLE_SLOTS_DDL)


def _insert_slot_row(store: SQLiteGraphStore, ts: TableSlot) -> None:
    extra = {k: v for k, v in ts.items() if k not in _BASE_SLOT_KEYS}
    store.conn.execute(
        "INSERT OR REPLACE INTO table_slots(slot_id, concept_id, lod_tier, pinned, token_cost, summary, external_links_json, updated_turn, extra_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts["slot_id"], ts["concept_id"], ts["lod_tier"], 1 if ts["pinned"] else 0,
            int(ts["token_cost"]), ts["summary"], json.dumps(ts["external_links"], sort_keys=True),
            int(ts["updated_turn"]), json.dumps(extra, sort_keys=True),
        ),
    )


def sync_table_to_sqlite(table_state: dict[str, Any]) -> None:
    """Atomically syncs `table_state['slots']` into the attached `SQLiteGraphStore` if present."""
    if not isinstance(table_state, dict):
        return
    store = getattr(table_state, "_sqlite_store", None) or table_state.get("_sqlite_store")
    if not isinstance(store, SQLiteGraphStore):
        return
    _ensure_table_slots_ddl(store)
    with store.conn:
        store.conn.execute("DELETE FROM table_slots")
        for s in table_state.get("slots", []):
            if isinstance(s, dict):
                _insert_slot_row(store, validate_table_slot(s))


def create_empty_table_state(
    *, max_slots: int = MAX_TABLE_SLOTS, max_tokens: int = MAX_TABLE_TOKENS, max_pinned_slots: int = MAX_PINNED_SLOTS,
    store: SQLiteGraphStore | None = None, db_path: str | Path | None = None,
) -> TableState:
    """Initializes a clean `TableState` with bounded slot/token/pin caps and optional `SQLiteGraphStore` persistence."""
    active_store = store if store is not None else (SQLiteGraphStore(db_path) if db_path is not None else None)
    state = validate_table_state({"slots": [], "total_tokens": 0, "max_slots": max_slots, "max_tokens": max_tokens, "max_pinned_slots": max_pinned_slots})
    if active_store is not None:
        object.__setattr__(state, "_sqlite_store", active_store)
        if existing := list_table_slots(active_store):
            state["slots"] = existing
            state["total_tokens"] = sum(int(s["token_cost"]) for s in existing)
        else:
            sync_table_to_sqlite(state)
    return state


def list_table_slots(store_or_state: SQLiteGraphStore | dict[str, Any], *, pinned_only: bool = False) -> list[TableSlot]:
    """Lists all `TableSlot` records from `SQLiteGraphStore` or an in-memory `TableState` dict."""
    if isinstance(store_or_state, SQLiteGraphStore):
        _ensure_table_slots_ddl(store_or_state)
        rows = store_or_state.conn.execute("SELECT * FROM table_slots ORDER BY rowid ASC").fetchall()
        slots: list[TableSlot] = []
        for r in rows:
            extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
            ts = TableSlot(
                slot_id=str(r["slot_id"]), concept_id=str(r["concept_id"]), lod_tier=str(r["lod_tier"]),
                pinned=bool(r["pinned"]), token_cost=int(r["token_cost"]), summary=str(r["summary"]),
                external_links=json.loads(r["external_links_json"]), updated_turn=int(r["updated_turn"]),
                **(extra if isinstance(extra, dict) else {}),
            )
            if not pinned_only or ts["pinned"]:
                slots.append(ts)
        return slots
    if isinstance(store_or_state, dict):
        raw_slots = store_or_state.get("slots", [])
        out = [s if isinstance(s, TableSlot) else TableSlot(s) for s in (raw_slots if isinstance(raw_slots, list) else []) if isinstance(s, dict)]
        return [s for s in out if not pinned_only or s["pinned"]]
    raise ValueError(f"Expected SQLiteGraphStore or TableState dict, got {type(store_or_state).__name__}")


def get_table_slot(store_or_state: SQLiteGraphStore | dict[str, Any], slot_id: str) -> TableSlot | None:
    """Retrieves a single `TableSlot` by `slot_id` (or `concept_id`) from `SQLiteGraphStore` or `TableState`."""
    if not isinstance(slot_id, str) or not slot_id.strip():
        return None
    target = slot_id.strip()
    return next((s for s in list_table_slots(store_or_state) if s["slot_id"] == target or s["concept_id"] == target), None)


def upsert_table_slot(
    store_or_state: SQLiteGraphStore | dict[str, Any], slot_or_id: dict[str, Any] | TableSlot | str, concept_id: str | None = None,
    *, lod_tier: str = LOD_1, pinned: bool = False, token_cost: int | None = None, summary: str = "",
    external_links: list[Any] | None = None, updated_turn: int = 1, **extra: Any,
) -> TableSlot:
    """Validates and persists a `TableSlot` into `SQLiteGraphStore` or `TableState`, enforcing capacity and pin bounds."""
    cand = dict(slot_or_id) | extra if isinstance(slot_or_id, dict) else {
        "slot_id": str(slot_or_id), "concept_id": str(concept_id or slot_or_id), "lod_tier": lod_tier,
        "pinned": pinned, "token_cost": token_cost, "summary": summary, "external_links": external_links, "updated_turn": updated_turn, **extra,
    }
    ts = validate_table_slot(cand)
    if isinstance(store_or_state, SQLiteGraphStore):
        _ensure_table_slots_ddl(store_or_state)
        new_slots = [*(s for s in list_table_slots(store_or_state) if s["slot_id"] != ts["slot_id"]), ts]
        validate_table_state({"slots": new_slots, "total_tokens": sum(int(s["token_cost"]) for s in new_slots)})
        with store_or_state.conn:
            _insert_slot_row(store_or_state, ts)
        return ts
    if isinstance(store_or_state, dict):
        slots = [s if isinstance(s, TableSlot) else TableSlot(s) for s in store_or_state.get("slots", []) if isinstance(s, dict)]
        if (idx := next((i for i, s in enumerate(slots) if s["slot_id"] == ts["slot_id"]), None)) is not None:
            slots[idx] = ts
        else:
            slots.append(ts)
        validated = validate_table_state({**store_or_state, "slots": slots, "total_tokens": sum(int(s["token_cost"]) for s in slots)})
        store_or_state["slots"], store_or_state["total_tokens"] = validated["slots"], validated["total_tokens"]
        sync_table_to_sqlite(store_or_state)
        return ts
    raise ValueError(f"Expected SQLiteGraphStore or TableState dict, got {type(store_or_state).__name__}")


def delete_table_slot(store_or_state: SQLiteGraphStore | dict[str, Any], slot_id: str) -> bool:
    """Deletes `slot_id` from `SQLiteGraphStore` or `TableState`, returning `True` if a slot was removed."""
    if not isinstance(slot_id, str) or not slot_id.strip():
        return False
    target = slot_id.strip()
    if isinstance(store_or_state, SQLiteGraphStore):
        _ensure_table_slots_ddl(store_or_state)
        with store_or_state.conn:
            cur = store_or_state.conn.execute("DELETE FROM table_slots WHERE slot_id = ? OR concept_id = ?", (target, target))
        return cur.rowcount > 0
    if isinstance(store_or_state, dict):
        if not isinstance(slots := store_or_state.get("slots", []), list):
            return False
        kept = [s for s in slots if not (isinstance(s, dict) and (s.get("slot_id") == target or s.get("concept_id") == target))]
        if len(kept) == len(slots):
            return False
        store_or_state["slots"] = kept
        store_or_state["total_tokens"] = sum(int(s.get("token_cost", 0)) for s in kept if isinstance(s, dict))
        sync_table_to_sqlite(store_or_state)
        return True
    raise ValueError(f"Expected SQLiteGraphStore or TableState dict, got {type(store_or_state).__name__}")


for _m, _f in (("upsert_table_slot", upsert_table_slot), ("list_table_slots", list_table_slots), ("delete_table_slot", delete_table_slot), ("get_table_slot", get_table_slot)):
    if not hasattr(SQLiteGraphStore, _m):
        setattr(SQLiteGraphStore, _m, _f)
