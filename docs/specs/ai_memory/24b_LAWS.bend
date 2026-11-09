# Site Y — Stage 24B (`ai_memory` — `hippocampal_index.py` & `__init__.py`) Formal Laws (`LAWS.bend`)
# Generated directly from Lattice `--board ai_memory` (`pkt_wave1_hippocampal_index`).
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `ai_memory` 24B build tag in `ModelCircuits/eng3` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as MEMB

# LAW 1: Recall Hop & Token Budget Bound Law (`recall_hop_bound_law`) —
# `should_include_recalled_concept(hop_bucket, budget_within_400, words_within_50)`:
# (1) Returns `True{}` for `Hop0Seed{}`, `Hop1Neighbor{}`, and `Hop2Second{}` iff `budget_within_400 == True{}` AND `words_within_50 == True{}`;
# (2) Always returns `False{}` for `HopExceeds2{}` (`max_recall_hops <= 6` for flow bridges; `HopExceeds2{}` represents out-of-bound hops).
law recall_hop_bound_law:
  for +budget_ok: Bool
  for +words_ok: Bool
  {Bool.and(
    Bool.not(MEMB.should_include_recalled_concept(MEMB.HopExceeds2{}, budget_ok, words_ok)),
    Bool.and(
      MEMB.bool_eq(MEMB.should_include_recalled_concept(MEMB.Hop0Seed{}, budget_ok, words_ok), Bool.and(budget_ok, words_ok)),
      Bool.and(
        MEMB.bool_eq(MEMB.should_include_recalled_concept(MEMB.Hop1Neighbor{}, budget_ok, words_ok), Bool.and(budget_ok, words_ok)),
        MEMB.bool_eq(MEMB.should_include_recalled_concept(MEMB.Hop2Second{}, budget_ok, words_ok), Bool.and(budget_ok, words_ok))
      )
    )
  ) == True{} : Bool}

# LAW 2: Steiner Bridge & Contract Sink Isolation Law (`steiner_bridge_and_sink_isolation_law`) —
# `can_propagate_from_node(reached_via_depends_on_sink, step_energy_above_epsilon, within_max_hops)`:
# (1) When `reached_via_depends_on_sink == True{}` (a utility callee reached via 1-hop forward `depends_on`),
#     always returns `False{}` (strictly forbids shared-utility V-bounce out of `CONTRACT_SINK`);
# (2) When `reached_via_depends_on_sink == False{}`, returns `Bool.and(step_energy_above_epsilon, within_max_hops)`.
law steiner_bridge_and_sink_isolation_law:
  for +energy_ok: Bool
  for +hops_ok: Bool
  {Bool.and(
    Bool.not(MEMB.can_propagate_from_node(True{}, energy_ok, hops_ok)),
    MEMB.bool_eq(
      MEMB.can_propagate_from_node(False{}, energy_ok, hops_ok),
      Bool.and(energy_ok, hops_ok)
    )
  ) == True{} : Bool}
