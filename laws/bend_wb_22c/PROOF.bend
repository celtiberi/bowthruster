# Spec 22C: Whiteboard Intake Linker, Task Readiness & Read Engine Formal Proofs (`PROOF.bend`)

import Base
import ./main.bend as WBC
import ./LAWS.bend as Laws

def Laws.clarification_dominates_all_deltas(
  nt,
  completed,
  has_target_prop,
  is_prohibited,
  has_overwrite_match,
  has_broader_rules,
  has_rationale_match
):
  match nt completed has_target_prop is_prohibited has_overwrite_match has_broader_rules has_rationale_match:
    case WBC.NKQuestion{} _ _ _ _ _ _:
      {==}
    case WBC.NKCommit{} _ _ _ _ _ _:
      {==}
    case WBC.NKAction{} _ _ _ _ _ _:
      {==}
    case WBC.NKFact{} _ _ _ _ _ _:
      {==}
    case WBC.NKRule{} _ _ _ _ _ _:
      {==}

def Laws.grounded_action_never_enters_rule_table(
  completed,
  has_target_prop,
  is_prohibited,
  has_overwrite_match,
  has_broader_rules,
  has_rationale_match
):
  match completed has_target_prop is_prohibited has_overwrite_match has_broader_rules has_rationale_match:
    case True{} _ _ _ _ _:
      {==}
    case False{} _ _ _ _ _:
      {==}

def Laws.prohibition_guard_blocks_overwrite(
  nt,
  grounding,
  has_unclear,
  completed,
  has_target_prop,
  has_overwrite_match,
  has_broader_rules,
  has_rationale_match
):
  match nt grounding has_unclear completed has_target_prop has_overwrite_match has_broader_rules has_rationale_match:
    case _ WBC.GNeedsClarification{} _ _ _ _ _ _:
      {==}
    case _ WBC.GHasOpenQuestions{} True{} _ _ _ _ _:
      {==}
    case _ WBC.GHasOpenQuestions{} False{} _ _ _ _ _:
      {==}
    case _ WBC.GGrounded{} True{} _ _ _ _ _:
      {==}
    case WBC.NKQuestion{} WBC.GGrounded{} False{} _ _ _ _ _:
      {==}
    case WBC.NKCommit{} WBC.GGrounded{} False{} _ _ _ _ _:
      {==}
    case WBC.NKAction{} WBC.GGrounded{} False{} True{} _ _ _ _:
      {==}
    case WBC.NKAction{} WBC.GGrounded{} False{} False{} _ _ _ _:
      {==}
    case WBC.NKFact{} WBC.GGrounded{} False{} _ False{} _ True{} True{}:
      {==}
    case WBC.NKFact{} WBC.GGrounded{} False{} _ False{} _ False{} True{}:
      {==}
    case WBC.NKFact{} WBC.GGrounded{} False{} _ False{} _ True{} False{}:
      {==}
    case WBC.NKFact{} WBC.GGrounded{} False{} _ False{} _ False{} False{}:
      {==}
    case WBC.NKFact{} WBC.GGrounded{} False{} _ True{} _ True{} _:
      {==}
    case WBC.NKFact{} WBC.GGrounded{} False{} _ True{} _ False{} _:
      {==}
    case WBC.NKRule{} WBC.GGrounded{} False{} _ False{} _ True{} _:
      {==}
    case WBC.NKRule{} WBC.GGrounded{} False{} _ False{} _ False{} _:
      {==}
    case WBC.NKRule{} WBC.GGrounded{} False{} _ True{} _ True{} _:
      {==}
    case WBC.NKRule{} WBC.GGrounded{} False{} _ True{} _ False{} _:
      {==}

def Laws.prohibition_guard_symmetric(a_is_prohib, b_is_prohib, same_val, intervals_disjoint):
  match a_is_prohib b_is_prohib same_val intervals_disjoint:
    case True{} True{} _ _:
      {==}
    case False{} False{} _ _:
      {==}
    case True{} False{} True{} _:
      {==}
    case True{} False{} False{} True{}:
      {==}
    case True{} False{} False{} False{}:
      {==}
    case False{} True{} True{} _:
      {==}
    case False{} True{} False{} True{}:
      {==}
    case False{} True{} False{} False{}:
      {==}

def Laws.prohibition_guard_disjoint_intervals(same_val):
  match same_val:
    case True{}:
      {==}
    case False{}:
      {==}

def Laws.ingestion_status_commitment_iff_clean(has_clarification, has_conflicts):
  match has_clarification has_conflicts:
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}
    case False{} True{}:
      {==}
    case False{} False{}:
      {==}

def Laws.task_ready_iff_all_clear(has_conflict, has_prohibition, has_unmet_dep):
  match has_conflict has_prohibition has_unmet_dep:
    case True{} False{} False{}:
      {==}
    case True{} False{} True{}:
      {==}
    case True{} True{} False{}:
      {==}
    case True{} True{} True{}:
      {==}
    case False{} True{} False{}:
      {==}
    case False{} True{} True{}:
      {==}
    case False{} False{} True{}:
      {==}
    case False{} False{} False{}:
      {==}

def Laws.epistemic_partition_blocks_cross_kind_overwrite():
  {==}

def Laws.property_specific_prohibition_spares_untargeted_task(task_has_prop):
  match task_has_prop:
    case True{}:
      {==}
    case False{}:
      {==}

def Laws.scope_wide_or_matching_prohibition_blocks_task(
  rule_has_prop,
  task_has_prop,
  props_match,
  mentions_prohibited_val
):
  match rule_has_prop task_has_prop props_match mentions_prohibited_val:
    case False{} _ _ True{}:
      {==}
    case False{} _ _ False{}:
      {==}
    case True{} True{} True{} True{}:
      {==}
    case True{} True{} True{} False{}:
      {==}
    case True{} True{} False{} True{}:
      {==}
    case True{} True{} False{} False{}:
      {==}
    case True{} False{} _ True{}:
      {==}
    case True{} False{} _ False{}:
      {==}

def Laws.lattice_delta_routes_sbf_kinds(lkind):
  match lkind:
    case WBC.LKComponent{}:
      {==}
    case WBC.LKFlow{}:
      {==}
    case WBC.LKSchema{}:
      {==}
    case WBC.LKInterface{}:
      {==}
    case WBC.LKLaw{}:
      {==}
    case WBC.LKConstraint{}:
      {==}
    case WBC.LKFact{}:
      {==}
    case WBC.LKTask{}:
      {==}

def Laws.python_skeleton_completeness_law(
  has_schemas,
  has_interfaces,
  has_law_stubs
):
  match has_schemas has_interfaces has_law_stubs:
    case True{} True{} True{}:
      {==}
    case True{} True{} False{}:
      {==}
    case True{} False{} True{}:
      {==}
    case True{} False{} False{}:
      {==}
    case False{} True{} True{}:
      {==}
    case False{} True{} False{}:
      {==}
    case False{} False{} True{}:
      {==}
    case False{} False{} False{}:
      {==}
