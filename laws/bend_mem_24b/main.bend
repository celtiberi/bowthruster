# Site Y — Stage 24B (`ai_memory` — `hippocampal_index.py` & `__init__.py`) Kernel (`main.bend`)

import Base

type HopBucket is Data:
  Hop0Seed{}
  Hop1Neighbor{}
  Hop2Second{}
  HopExceeds2{}

def bool_eq(a: Bool, b: Bool) -> Bool:
  match a b:
    case True{} True{}:
      True{}
    case False{} False{}:
      True{}
    case True{} False{}:
      False{}
    case False{} True{}:
      False{}

def should_include_recalled_concept(
  hop_bucket: HopBucket,
  budget_within_400: Bool,
  words_within_50: Bool,
) -> Bool:
  match hop_bucket:
    case Hop0Seed{}:
      Bool.and(budget_within_400, words_within_50)
    case Hop1Neighbor{}:
      Bool.and(budget_within_400, words_within_50)
    case Hop2Second{}:
      Bool.and(budget_within_400, words_within_50)
    case HopExceeds2{}:
      False{}

def can_propagate_from_node(
  reached_via_depends_on_sink: Bool,
  step_energy_above_epsilon: Bool,
  within_max_hops: Bool,
) -> Bool:
  match reached_via_depends_on_sink:
    case True{}:
      False{}
    case False{}:
      Bool.and(step_energy_above_epsilon, within_max_hops)

def main() -> Bool:
  Bool.and(
    should_include_recalled_concept(Hop0Seed{}, True{}, True{}),
    can_propagate_from_node(False{}, True{}, True{})
  )
