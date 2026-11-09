# Site Y — Canonical Spec 23B (`the_executive` Routing Gate & Transactional Turn Policy) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `Spec 23B` build tag in `ModelCircuits/eng3` must prove these laws via `ModelCircuits/eng3/bend_exec_23b/PROOF.bend`.

import Base
import ./main.bend as EXB

# LAW 1: Transactional commit bi-implication — `should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)`
# is `True{}` iff `has_ambiguity == False{}`, `has_open_questions == False{}`, `has_rule_conflict == False{}`, and `has_flawed_idea == False{}`.
law commit_iff_all_clear:
  for +has_ambiguity: Bool
  for +has_open_questions: Bool
  for +has_rule_conflict: Bool
  for +has_flawed_idea: Bool
  {EXB.bool_eq(
    EXB.should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea),
    Bool.and(
      Bool.not(has_ambiguity),
      Bool.and(
        Bool.not(has_open_questions),
        Bool.and(Bool.not(has_rule_conflict), Bool.not(has_flawed_idea))
      )
    )
  ) == True{} : Bool}

# LAW 2: Decision bijection with commit flag — `route_executive_decision(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)`
# is `DecisionCommit{}` iff `should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea) == True{}`.
law decision_matches_commit_flag:
  for +has_ambiguity: Bool
  for +has_open_questions: Bool
  for +has_rule_conflict: Bool
  for +has_flawed_idea: Bool
  {EXB.bool_eq(
    EXB.is_commit_decision(EXB.route_executive_decision(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)),
    EXB.should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)
  ) == True{} : Bool}

# LAW 3: Rule-conflict bounce dominance — whenever `has_rule_conflict == True{}`, `resolve_bounce_reason`
# ALWAYS returns `BounceRuleConflict{}` and `route_executive_decision` is `DecisionSendBack{}`.
law conflict_dominates_bounce_reason:
  for +has_ambiguity: Bool
  for +has_open_questions: Bool
  for +has_flawed_idea: Bool
  {Bool.and(
    EXB.is_rule_conflict_bounce(EXB.resolve_bounce_reason(has_ambiguity, has_open_questions, True{}, has_flawed_idea)),
    EXB.is_send_back_decision(EXB.route_executive_decision(has_ambiguity, has_open_questions, True{}, has_flawed_idea))
  ) == True{} : Bool}

# LAW 4: Flawed-idea bounce dominance when conflict-free — whenever `has_rule_conflict == False{}` and `has_flawed_idea == True{}`,
# `resolve_bounce_reason` ALWAYS returns `BounceFlawedIdea{}` and `route_executive_decision` is `DecisionSendBack{}`.
law flawed_idea_dominates_when_no_conflict:
  for +has_ambiguity: Bool
  for +has_open_questions: Bool
  {Bool.and(
    EXB.is_flawed_idea_bounce(EXB.resolve_bounce_reason(has_ambiguity, has_open_questions, False{}, True{})),
    EXB.is_send_back_decision(EXB.route_executive_decision(has_ambiguity, has_open_questions, False{}, True{}))
  ) == True{} : Bool}

# LAW 5: Clarification bounce when conflict-free and flaw-free — whenever `has_rule_conflict == False{}`,
# `has_flawed_idea == False{}`, and `Bool.or(has_ambiguity, has_open_questions) == True{}`,
# `resolve_bounce_reason` returns `BounceNeedsClarification{}`.
law ambiguity_or_question_bounces_for_clarification:
  for +has_ambiguity: Bool
  for +has_open_questions: Bool
  {EXB.bool_imp(
    Bool.or(has_ambiguity, has_open_questions),
    EXB.is_needs_clarification_bounce(EXB.resolve_bounce_reason(has_ambiguity, has_open_questions, False{}, False{}))
  ) == True{} : Bool}

# LAW 6: `BounceNone{}` bi-implication — `resolve_bounce_reason(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)`
# returns `BounceNone{}` iff `should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea) == True{}`.
law bounce_none_iff_commit:
  for +has_ambiguity: Bool
  for +has_open_questions: Bool
  for +has_rule_conflict: Bool
  for +has_flawed_idea: Bool
  {EXB.bool_eq(
    EXB.is_none_bounce(EXB.resolve_bounce_reason(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)),
    EXB.should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)
  ) == True{} : Bool}

# LAW 7: Git Pre-Commit Lattice Gate Law (`git_precommit_gate_law`, Stage 26 `precommit_lattice_gate`) —
# `should_allow_git_commit(bounds_ok, contracts_ok, bend_proofs_ok, no_dangling_deletes)` returns `True{}`
# iff all four pre-commit Lattice invariants hold (`True{}`).
law git_precommit_gate_law:
  for +bounds_ok: Bool
  for +contracts_ok: Bool
  for +bend_proofs_ok: Bool
  for +no_dangling_deletes: Bool
  {EXB.bool_eq(
    EXB.should_allow_git_commit(bounds_ok, contracts_ok, bend_proofs_ok, no_dangling_deletes),
    Bool.and(
      bounds_ok,
      Bool.and(
        contracts_ok,
        Bool.and(bend_proofs_ok, no_dangling_deletes)
      )
    )
  ) == True{} : Bool}

# LAW 8: AST Call-Graph Skeleton Law (`skeleton_call_graph_law`, Stage 27 `ast_call_graph_gate`, arXiv:2610.38214) —
# `is_call_graph_skeleton_valid(interface_defined, law_fn_defined, interface_calls_law)` returns `True{}`
# iff the `$ INTERFACE` entrypoint is defined, the `% LAW` helper function is defined, and the `$ INTERFACE`
# entrypoint invokes the `% LAW` helper function in the module's AST call graph (`True{}`).
law skeleton_call_graph_law:
  for +interface_defined: Bool
  for +law_fn_defined: Bool
  for +interface_calls_law: Bool
  {EXB.bool_eq(
    EXB.is_call_graph_skeleton_valid(interface_defined, law_fn_defined, interface_calls_law),
    Bool.and(interface_defined, Bool.and(law_fn_defined, interface_calls_law))
  ) == True{} : Bool}
