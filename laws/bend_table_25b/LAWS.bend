# Site Y — Stage 25B (`table_renderer` — `renderer.py`, `workspace_sync.py`, `__init__.py`) Formal Laws (`LAWS.bend`)
# Generated directly from Lattice `--board the_table` (`pkt_wave1_table_renderer`).
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `the_table` 25B build tag in `ModelCircuits/eng2` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as TBLB

# LAW 1: LOD Escalation Monotonicity & Summary Bound Law (`lod_escalation_monotonicity_law`) —
# `step_slot_lod_escalation(cur_lod, request_upgrade, words_within_50, budget_ok)`:
# (1) When `request_upgrade == False{}`, preserves `cur_lod` unchanged (`lod_eq(..., cur_lod) == True{}`);
# (2) For every transition, never demotes LOD (`lod_not_demoted(cur_lod, step_slot_lod_escalation(cur_lod, req, words_ok, budget_ok)) == True{}`);
# (3) Only escalates `TLOD0{}` to `TLOD1{}` (`is_tlod1 == True{}`) when `request_upgrade == True{}` AND `words_within_50 == True{}` AND `budget_ok == True{}`.
law lod_escalation_monotonicity_law:
  for +lod: TBLB.TableLOD
  for +req: Bool
  for +words_ok: Bool
  for +budget_ok: Bool
  {Bool.and(
    TBLB.lod_eq(TBLB.step_slot_lod_escalation(lod, False{}, words_ok, budget_ok), lod),
    Bool.and(
      TBLB.lod_not_demoted(lod, TBLB.step_slot_lod_escalation(lod, req, words_ok, budget_ok)),
      TBLB.bool_eq(
        TBLB.is_tlod1(TBLB.step_slot_lod_escalation(TBLB.TLOD0{}, req, words_ok, budget_ok)),
        Bool.and(req, Bool.and(words_ok, budget_ok))
      )
    )
  ) == True{} : Bool}

# LAW 2: Stage 2 Active Prefrontal Foraging Budget Law (`stage2_foraging_budget_law`) —
# `should_execute_foraging_step(foraging_requested, steps_under_max, budget_ok)`:
# Returns `True{}` iff `foraging_requested == True{}` AND `steps_under_max == True{}` AND `budget_ok == True{}`.
law stage2_foraging_budget_law:
  for +req: Bool
  for +steps_ok: Bool
  for +budget_ok: Bool
  {TBLB.should_execute_foraging_step(req, steps_ok, budget_ok) ==
   Bool.and(req, Bool.and(steps_ok, budget_ok)) : Bool}
