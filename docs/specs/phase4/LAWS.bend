# Site Y — Canonical Spec 19 (Phase 4 Graph Ingestion & Linker) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every Phase 4 build tag must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as P4

# LAW 1: Clarification priority — any MCU with `GNeedsClarification{}` or `has_unclear == True{}`
# transitions unconditionally to `OpBlockerClarification{}` regardless of node type or rule matches.
law clarification_dominates_all_deltas:
  for +nt: P4.NodeKind
  for +completed: Bool
  for +has_target_prop: Bool
  for +is_prohibited: Bool
  for +has_overwrite_match: Bool
  for +has_broader_rules: Bool
  for +has_rationale_match: Bool
  {Bool.and(
    P4.is_clarification_op(P4.classify_delta(nt, P4.GNeedsClarification{}, False{}, completed, has_target_prop, is_prohibited, has_overwrite_match, has_broader_rules, has_rationale_match)),
    P4.is_clarification_op(P4.classify_delta(nt, P4.GGrounded{}, True{}, completed, has_target_prop, is_prohibited, has_overwrite_match, has_broader_rules, has_rationale_match))
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
  {P4.is_rule_table_op(
    P4.classify_delta(P4.NKAction{}, P4.GGrounded{}, False{}, completed, has_target_prop, is_prohibited, has_overwrite_match, has_broader_rules, has_rationale_match)
  ) == False{} : Bool}

# LAW 3: Prohibition guard blocks Markovian overwrite — whenever `is_prohibited == True{}`,
# `classify_delta` never returns `OpMarkovianOverwrite{}` (`is_markovian_overwrite == False{}`),
# ensuring prohibited rules are inserted and caught by Stage 4C Spin-Glass Frustration.
law prohibition_guard_blocks_overwrite:
  for +nt: P4.NodeKind
  for +grounding: P4.GroundingKind
  for +has_unclear: Bool
  for +completed: Bool
  for +has_target_prop: Bool
  for +has_overwrite_match: Bool
  for +has_broader_rules: Bool
  for +has_rationale_match: Bool
  {P4.is_markovian_overwrite(
    P4.classify_delta(nt, grounding, has_unclear, completed, has_target_prop, True{}, has_overwrite_match, has_broader_rules, has_rationale_match)
  ) == False{} : Bool}

# LAW 4: Prohibition guard symmetry — `prohibition_clashes` is symmetric when swapping
# the existing rule (`a_is_prohib`) and incoming rule (`b_is_prohib`) across both
# `same_val` and `intervals_disjoint`.
law prohibition_guard_symmetric:
  for +a_is_prohib: Bool
  for +b_is_prohib: Bool
  for +same_val: Bool
  for +intervals_disjoint: Bool
  {P4.prohibition_clashes(a_is_prohib, b_is_prohib, same_val, intervals_disjoint) ==
   P4.prohibition_clashes(b_is_prohib, a_is_prohib, same_val, intervals_disjoint) : Bool}

# LAW 5: Prohibition guard disjoint-interval completeness — whenever one rule is a prohibition
# (`True{}`) and the other is positive (`False{}`), disjoint numeric intervals (`intervals_disjoint == True{}`)
# always trigger `prohibition_clashes == True{}` regardless of which rule arrived first.
law prohibition_guard_disjoint_intervals:
  for +same_val: Bool
  {Bool.and(
    P4.prohibition_clashes(True{}, False{}, same_val, True{}),
    P4.prohibition_clashes(False{}, True{}, same_val, True{})
  ) == True{} : Bool}

# LAW 6: Stage 4C ingestion status priority and commitment lock — `resolve_ingestion_status`
# returns `StatusCommitted{}` iff `has_clarification == False{}` and `has_conflicts == False{}`,
# and never returns `StatusCommitted{}` when `has_conflicts == True{}`.
law ingestion_status_commitment_iff_clean:
  for +has_clarification: Bool
  for +has_conflicts: Bool
  {P4.is_committed_status(P4.resolve_ingestion_status(has_clarification, has_conflicts)) ==
   Bool.and(Bool.not(has_clarification), Bool.not(has_conflicts)) : Bool}
