import Base

def bool_eq(a: Bool, b: Bool) -> Bool:
  match a b:
    case True{}  True{}:  True{}
    case False{} False{}: True{}
    case _       _:       False{}

# --- LAW 1: Table Token & Slot Budget Conservation (`should_admit_table_slot`) ---

def should_admit_table_slot(within_16_slots: Bool, within_1200_tokens: Bool) -> Bool:
  match within_16_slots within_1200_tokens:
    case True{} True{}: True{}
    case _      _:      False{}

# --- LAW 2: Pinned Slot Eviction Immunity (`can_evict_table_slot`) ---

def can_evict_table_slot(is_pinned: Bool, is_over_capacity: Bool, within_6_pinned_cap: Bool) -> Bool:
  match is_pinned is_over_capacity within_6_pinned_cap:
    case True{}  _        True{}:  False{}
    case True{}  over_cap False{}: over_cap
    case False{} over_cap _:       over_cap

# --- LAW 3: Slot Eviction Order (`should_evict_first_over_second`) ---

def should_evict_first_over_second(first_pinned: Bool, second_pinned: Bool, first_lower_priority: Bool) -> Bool:
  match first_pinned second_pinned first_lower_priority:
    case True{}  False{} _:           False{}
    case False{} True{}  _:           True{}
    case False{} False{} first_lower: first_lower
    case True{}  True{}  _:           False{}

# --- LAW 4: Atlas Mode Isolation (`should_mount_global_atlas`) ---

def should_mount_global_atlas(has_local_seed_cues: Bool, explicit_atlas_request: Bool, budget_ok: Bool) -> Bool:
  match has_local_seed_cues explicit_atlas_request budget_ok:
    case _       _       False{}: False{}
    case _       True{}  True{}:  True{}
    case False{} False{} True{}:  True{}
    case True{}  False{} True{}:  False{}

def main() -> Bool:
  Bool.and(
    should_admit_table_slot(True{}, True{}),
    Bool.and(
      Bool.not(can_evict_table_slot(True{}, True{}, True{})),
      Bool.and(
        should_evict_first_over_second(False{}, True{}, False{}),
        Bool.not(should_mount_global_atlas(True{}, False{}, True{}))
      )
    )
  )
