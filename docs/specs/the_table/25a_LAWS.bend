# Site Y — Stage 25A (`the_table` & `table_surface` — `schema.py`, `surface.py`) Formal Laws (`LAWS.bend`)
# Generated directly from Lattice `--board the_table` (`pkt_wave1_the_table`, `pkt_wave1_table_surface`).
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `the_table` 25A build tag in `ModelCircuits/eng1` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as TBLA

# LAW 1: Table Token & Slot Budget Conservation Law (`table_token_budget_conservation_law`) —
# `should_admit_table_slot(within_16_slots, within_1200_tokens)` is `True{}`
# iff `within_16_slots == True{}` AND `within_1200_tokens == True{}`.
law table_token_budget_conservation_law:
  for +slots_ok: Bool
  for +tokens_ok: Bool
  {TBLA.should_admit_table_slot(slots_ok, tokens_ok) ==
   Bool.and(slots_ok, tokens_ok) : Bool}

# LAW 2: Pinned Slot Eviction Immunity Law (`pinned_slot_eviction_immunity_law`) —
# `can_evict_table_slot(is_pinned, is_over_capacity, within_6_pinned_cap)`:
# (1) When `is_pinned == True{}` and `within_6_pinned_cap == True{}`, returns `False{}` (pinned slots are immune);
# (2) When `is_pinned == False{}`, returns `is_over_capacity`.
law pinned_slot_eviction_immunity_law:
  for +over_cap: Bool
  for +pinned_cap_ok: Bool
  {Bool.and(
    Bool.not(TBLA.can_evict_table_slot(True{}, over_cap, True{})),
    TBLA.bool_eq(TBLA.can_evict_table_slot(False{}, over_cap, pinned_cap_ok), over_cap)
  ) == True{} : Bool}

# LAW 3: Slot Eviction Order Law (`slot_eviction_order_law`) —
# `should_evict_first_over_second(first_pinned, second_pinned, first_lower_priority)`:
# (1) Never evicts a pinned first slot when the second slot is unpinned (`True{}, False{}` -> `False{}`);
# (2) Always evicts an unpinned first slot when the second slot is pinned (`False{}, True{}` -> `True{}`);
# (3) When both are unpinned (`False{}, False{}`), follows `first_lower_priority`.
law slot_eviction_order_law:
  for +first_lower: Bool
  {Bool.and(
    Bool.not(TBLA.should_evict_first_over_second(True{}, False{}, first_lower)),
    Bool.and(
      TBLA.should_evict_first_over_second(False{}, True{}, first_lower),
      TBLA.bool_eq(TBLA.should_evict_first_over_second(False{}, False{}, first_lower), first_lower)
    )
  ) == True{} : Bool}

# LAW 4: Global Atlas vs. Local Slice Mode Isolation Law (`atlas_mode_isolation_law`) —
# `should_mount_global_atlas(has_local_seed_cues, explicit_atlas_request, budget_ok)`:
# (1) Anti-Rabbit-Hole Isolation: When `has_local_seed_cues == True{}` and `explicit_atlas_request == False{}`,
#     always returns `False{}` (never mounts the `LOD_0` Global Schematic during local slice turns);
# (2) Mounts the `LOD_0` Global Atlas iff `budget_ok == True{}` AND (`explicit_atlas_request == True{}` OR `has_local_seed_cues == False{}`).
law atlas_mode_isolation_law:
  for +has_cues: Bool
  for +explicit_req: Bool
  for +budget_ok: Bool
  {Bool.and(
    Bool.not(TBLA.should_mount_global_atlas(True{}, False{}, budget_ok)),
    TBLA.bool_eq(
      TBLA.should_mount_global_atlas(has_cues, explicit_req, budget_ok),
      Bool.and(budget_ok, Bool.or(explicit_req, Bool.not(has_cues)))
    )
  ) == True{} : Bool}
