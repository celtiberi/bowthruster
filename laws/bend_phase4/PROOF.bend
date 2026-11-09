# Spec 19: Phase 4 Graph Ingestion & Linker Formal Proofs (`PROOF.bend`)

import Base
import ./main.bend as P4
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
    case P4.NKQuestion{} _ _ _ _ _ _:
      {==}
    case P4.NKCommit{} _ _ _ _ _ _:
      {==}
    case P4.NKAction{} _ _ _ _ _ _:
      {==}
    case P4.NKFact{} _ _ _ _ _ _:
      {==}
    case P4.NKRule{} _ _ _ _ _ _:
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
    case _ P4.GNeedsClarification{} _ _ _ _ _ _:
      {==}
    case _ P4.GHasOpenQuestions{} True{} _ _ _ _ _:
      {==}
    case _ P4.GHasOpenQuestions{} False{} _ _ _ _ _:
      {==}
    case _ P4.GGrounded{} True{} _ _ _ _ _:
      {==}
    case P4.NKQuestion{} P4.GGrounded{} False{} _ _ _ _ _:
      {==}
    case P4.NKCommit{} P4.GGrounded{} False{} _ _ _ _ _:
      {==}
    case P4.NKAction{} P4.GGrounded{} False{} True{} _ _ _ _:
      {==}
    case P4.NKAction{} P4.GGrounded{} False{} False{} _ _ _ _:
      {==}
    case P4.NKFact{} P4.GGrounded{} False{} _ False{} _ True{} True{}:
      {==}
    case P4.NKFact{} P4.GGrounded{} False{} _ False{} _ False{} True{}:
      {==}
    case P4.NKFact{} P4.GGrounded{} False{} _ False{} _ True{} False{}:
      {==}
    case P4.NKFact{} P4.GGrounded{} False{} _ False{} _ False{} False{}:
      {==}
    case P4.NKFact{} P4.GGrounded{} False{} _ True{} _ True{} _:
      {==}
    case P4.NKFact{} P4.GGrounded{} False{} _ True{} _ False{} _:
      {==}
    case P4.NKRule{} P4.GGrounded{} False{} _ False{} _ True{} _:
      {==}
    case P4.NKRule{} P4.GGrounded{} False{} _ False{} _ False{} _:
      {==}
    case P4.NKRule{} P4.GGrounded{} False{} _ True{} _ True{} _:
      {==}
    case P4.NKRule{} P4.GGrounded{} False{} _ True{} _ False{} _:
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
