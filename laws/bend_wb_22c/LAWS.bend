# Site Y — Canonical Spec 22C (`whiteboard` Intake Linker, Task Readiness & Read Engine) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `whiteboard_22c` build tag in `ModelCircuits/eng2` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as WBC

# LAW 1: Clarification priority — any MCU with `GNeedsClarification{}` or `has_unclear == True{}`
# transitions unconditionally to `OpBlockerClarification{}` regardless of node type or rule matches.
law clarification_dominates_all_deltas:
  for +nt: WBC.NodeKind
  for +completed: Bool
  for +has_target_prop: Bool
  for +is_prohibited: Bool
  for +has_overwrite_match: Bool
  for +has_broader_rules: Bool
  for +has_rationale_match: Bool
  {Bool.and(
    WBC.is_clarification_op(WBC.classify_delta(nt, WBC.GNeedsClarification{}, False{}, completed, has_target_prop, is_prohibited, has_overwrite_match, has_broader_rules, has_rationale_match)),
    WBC.is_clarification_op(WBC.classify_delta(nt, WBC.GGrounded{}, True{}, completed, has_target_prop, is_prohibited, has_overwrite_match, has_broader_rules, has_rationale_match))
  ) == True{} : Bool}

# LAW 2: Grounded `NKAction{}` isolation — a grounded `NKAction{}` with no unclear spans
# routes exclusively to task operations (`OpUpsertTask{}` or `OpGraduateTaskToFact{}`)
# and never mutates standing rule tables (`is_rule_table_op == False{}`).
law grounded_action_never_enters_rule_table:
  for +completed: Bool
  for +has_target_prop: Bool
  for +is_prohibited: Bool
  for +has_overwrite_match: Bool
  for +has_broader_rules: Bool
  for +has_rationale_match: Bool
  {WBC.is_rule_table_op(
    WBC.classify_delta(WBC.NKAction{}, WBC.GGrounded{}, False{}, completed, has_target_prop, is_prohibited, has_overwrite_match, has_broader_rules, has_rationale_match)
  ) == False{} : Bool}

# LAW 3: Prohibition guard blocks Markovian overwrite — whenever `is_prohibited == True{}`,
# `classify_delta` never returns `OpMarkovianOverwrite{}` (`is_markovian_overwrite == False{}`).
law prohibition_guard_blocks_overwrite:
  for +nt: WBC.NodeKind
  for +grounding: WBC.GroundingKind
  for +has_unclear: Bool
  for +completed: Bool
  for +has_target_prop: Bool
  for +has_overwrite_match: Bool
  for +has_broader_rules: Bool
  for +has_rationale_match: Bool
  {WBC.is_markovian_overwrite(
    WBC.classify_delta(nt, grounding, has_unclear, completed, has_target_prop, True{}, has_overwrite_match, has_broader_rules, has_rationale_match)
  ) == False{} : Bool}

# LAW 4: Prohibition guard symmetry — `prohibition_clashes` is symmetric when swapping
# the existing rule (`a_is_prohib`) and incoming rule (`b_is_prohib`) across both
# `same_val` and `intervals_disjoint`.
law prohibition_guard_symmetric:
  for +a_is_prohib: Bool
  for +b_is_prohib: Bool
  for +same_val: Bool
  for +intervals_disjoint: Bool
  {WBC.prohibition_clashes(a_is_prohib, b_is_prohib, same_val, intervals_disjoint) ==
   WBC.prohibition_clashes(b_is_prohib, a_is_prohib, same_val, intervals_disjoint) : Bool}

# LAW 5: Prohibition guard disjoint-interval completeness — whenever one rule is a prohibition
# (`True{}`) and the other is positive (`False{}`), disjoint numeric intervals (`intervals_disjoint == True{}`)
# always trigger `prohibition_clashes == True{}` regardless of which rule arrived first.
law prohibition_guard_disjoint_intervals:
  for +same_val: Bool
  {Bool.and(
    WBC.prohibition_clashes(True{}, False{}, same_val, True{}),
    WBC.prohibition_clashes(False{}, True{}, same_val, True{})
  ) == True{} : Bool}

# LAW 6: Ingestion status priority and commitment lock — `resolve_ingestion_status`
# returns `StatusCommitted{}` iff `has_clarification == False{}` and `has_conflicts == False{}`.
law ingestion_status_commitment_iff_clean:
  for +has_clarification: Bool
  for +has_conflicts: Bool
  {WBC.is_committed_status(WBC.resolve_ingestion_status(has_clarification, has_conflicts)) ==
   Bool.and(Bool.not(has_clarification), Bool.not(has_conflicts)) : Bool}

# LAW 7: Task readiness iff all clear — `classify_task_readiness(has_conflict, has_prohibition, has_unmet_dep)`
# returns `TaskReady{}` iff `has_conflict == False{}`, `has_prohibition == False{}`, and `has_unmet_dep == False{}`.
law task_ready_iff_all_clear:
  for +has_conflict: Bool
  for +has_prohibition: Bool
  for +has_unmet_dep: Bool
  {WBC.is_task_ready(WBC.classify_task_readiness(has_conflict, has_prohibition, has_unmet_dep)) ==
   Bool.and(Bool.not(has_conflict), Bool.and(Bool.not(has_prohibition), Bool.not(has_unmet_dep))) : Bool}

# LAW 8: Epistemic partition on Markovian overwrite — descriptive nodes (`NKFact{}`) and
# normative rules (`NKRule{}`) never overwrite each other (`can_overwrite_node_kind == False{}`).
law epistemic_partition_blocks_cross_kind_overwrite:
  {Bool.or(
    WBC.can_overwrite_node_kind(WBC.NKRule{}, WBC.NKFact{}),
    WBC.can_overwrite_node_kind(WBC.NKFact{}, WBC.NKRule{})
  ) == False{} : Bool}

# LAW 9: Property-specific prohibition scoping — when a `PROHIBITION` rule targets a specific
# property (`rule_has_prop == True{}`), a task that neither targets that same property
# (`props_match == False{}`) nor mentions the prohibited value (`mentions_prohibited_val == False{}`)
# is never blocked (`prohibition_blocks_task == False{}`), even when `task_has_prop == False{}`.
law property_specific_prohibition_spares_untargeted_task:
  for +task_has_prop: Bool
  {WBC.prohibition_blocks_task(True{}, task_has_prop, False{}, False{}) == False{} : Bool}

# LAW 10: Scope-wide or matching prohibition completeness — a `PROHIBITION` rule always blocks
# a task when the rule is scope-wide (`rule_has_prop == False{}`), when both target the same
# property (`task_has_prop == True{}` and `props_match == True{}`), or when the task explicitly
# mentions the prohibited value (`mentions_prohibited_val == True{}`).
law scope_wide_or_matching_prohibition_blocks_task:
  for +rule_has_prop: Bool
  for +task_has_prop: Bool
  for +props_match: Bool
  for +mentions_prohibited_val: Bool
  {Bool.and(
    WBC.prohibition_blocks_task(False{}, task_has_prop, props_match, mentions_prohibited_val),
    Bool.and(
      WBC.prohibition_blocks_task(True{}, True{}, True{}, mentions_prohibited_val),
      WBC.prohibition_blocks_task(rule_has_prop, task_has_prop, props_match, True{})
    )
  ) == True{} : Bool}

# LAW 11: Lattice SBF delta routing and clarification priority — `classify_lattice_delta(lkind, grounding, has_unclear)`
# routes any `GNeedsClarification{}` or `has_unclear == True{}` to `LOpBlockerClarification{}`
# (`is_lattice_clarification == True{}`), and routes grounded, clear SBF kinds (`LKComponent{}`,
# `LKFlow{}`, `LKSchema{}`, `LKInterface{}`, `LKLaw{}`) exclusively to their dedicated lattice
# stores (`is_sbf_store_op == True{}`) without mutating the rule or task stores.
law lattice_delta_routes_sbf_kinds:
  for +lkind: WBC.LatticeKind
  {Bool.and(
    WBC.is_lattice_clarification(WBC.classify_lattice_delta(lkind, WBC.GNeedsClarification{}, False{})),
    Bool.and(
      WBC.is_lattice_clarification(WBC.classify_lattice_delta(lkind, WBC.GGrounded{}, True{})),
      Bool.and(
        WBC.is_sbf_store_op(WBC.classify_lattice_delta(WBC.LKComponent{}, WBC.GGrounded{}, False{})),
        Bool.and(
          WBC.is_sbf_store_op(WBC.classify_lattice_delta(WBC.LKFlow{}, WBC.GGrounded{}, False{})),
          Bool.and(
            WBC.is_sbf_store_op(WBC.classify_lattice_delta(WBC.LKSchema{}, WBC.GGrounded{}, False{})),
            Bool.and(
              WBC.is_sbf_store_op(WBC.classify_lattice_delta(WBC.LKInterface{}, WBC.GGrounded{}, False{})),
              WBC.is_sbf_store_op(WBC.classify_lattice_delta(WBC.LKLaw{}, WBC.GGrounded{}, False{}))
            )
          )
        )
      )
    )
  ) == True{} : Bool}

# LAW 12: Python Skeleton Completeness Law (`python_skeleton_completeness_law`, Stage 27 `python_skeleton_compiler`, arXiv:2610.38214) —
# `is_python_skeleton_complete(has_schemas, has_interfaces, has_law_stubs)` returns `True{}`
# iff all declared `$ SCHEMA` classes, `$ INTERFACE` function signatures, and `% LAW` helper stubs are present (`True{}`).
law python_skeleton_completeness_law:
  for +has_schemas: Bool
  for +has_interfaces: Bool
  for +has_law_stubs: Bool
  {WBC.is_python_skeleton_complete(has_schemas, has_interfaces, has_law_stubs) ==
   Bool.and(has_schemas, Bool.and(has_interfaces, has_law_stubs)) : Bool}
