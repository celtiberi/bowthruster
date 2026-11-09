# Site Y — Spec 23B (`the_executive` Routing Gate & Transactional Turn Policy) Formal Kernel (`main.bend`)
#
# Formalizes the Executive Routing Gate (`ExecDecision`, `BounceReason`, `should_commit_whiteboard`,
# `route_executive_decision`, `resolve_bounce_reason`) and proves Laws 1-6 via `PROOF.bend`.

import Base

type ExecDecision is Data:
  DecisionCommit{}
  DecisionSendBack{}

type BounceReason is Data:
  BounceNone{}
  BounceNeedsClarification{}
  BounceFlawedIdea{}
  BounceRuleConflict{}

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

def bool_imp(a: Bool, b: Bool) -> Bool:
  match a:
    case False{}:
      True{}
    case True{}:
      b

def should_commit_whiteboard(
  has_ambiguity: Bool,
  has_open_questions: Bool,
  has_rule_conflict: Bool,
  has_flawed_idea: Bool,
) -> Bool:
  match has_ambiguity has_open_questions has_rule_conflict has_flawed_idea:
    case False{} False{} False{} False{}:
      True{}
    case False{} False{} False{} True{}:
      False{}
    case False{} False{} True{} False{}:
      False{}
    case False{} False{} True{} True{}:
      False{}
    case False{} True{} False{} False{}:
      False{}
    case False{} True{} False{} True{}:
      False{}
    case False{} True{} True{} False{}:
      False{}
    case False{} True{} True{} True{}:
      False{}
    case True{} False{} False{} False{}:
      False{}
    case True{} False{} False{} True{}:
      False{}
    case True{} False{} True{} False{}:
      False{}
    case True{} False{} True{} True{}:
      False{}
    case True{} True{} False{} False{}:
      False{}
    case True{} True{} False{} True{}:
      False{}
    case True{} True{} True{} False{}:
      False{}
    case True{} True{} True{} True{}:
      False{}

def decision_from_commit(commit: Bool) -> ExecDecision:
  match commit:
    case True{}:
      DecisionCommit{}
    case False{}:
      DecisionSendBack{}

def route_executive_decision(
  has_ambiguity: Bool,
  has_open_questions: Bool,
  has_rule_conflict: Bool,
  has_flawed_idea: Bool,
) -> ExecDecision:
  decision_from_commit(should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea))

def is_commit_decision(d: ExecDecision) -> Bool:
  match d:
    case DecisionCommit{}:
      True{}
    case DecisionSendBack{}:
      False{}

def is_send_back_decision(d: ExecDecision) -> Bool:
  match d:
    case DecisionCommit{}:
      False{}
    case DecisionSendBack{}:
      True{}

def resolve_bounce_reason(
  has_ambiguity: Bool,
  has_open_questions: Bool,
  has_rule_conflict: Bool,
  has_flawed_idea: Bool,
) -> BounceReason:
  match has_ambiguity has_open_questions has_rule_conflict has_flawed_idea:
    case amb oq True{} fi:
      BounceRuleConflict{}
    case amb oq False{} True{}:
      BounceFlawedIdea{}
    case True{} oq False{} False{}:
      BounceNeedsClarification{}
    case False{} True{} False{} False{}:
      BounceNeedsClarification{}
    case False{} False{} False{} False{}:
      BounceNone{}

def is_rule_conflict_bounce(b: BounceReason) -> Bool:
  match b:
    case BounceRuleConflict{}:
      True{}
    case BounceFlawedIdea{}:
      False{}
    case BounceNeedsClarification{}:
      False{}
    case BounceNone{}:
      False{}

def is_flawed_idea_bounce(b: BounceReason) -> Bool:
  match b:
    case BounceFlawedIdea{}:
      True{}
    case BounceRuleConflict{}:
      False{}
    case BounceNeedsClarification{}:
      False{}
    case BounceNone{}:
      False{}

def is_needs_clarification_bounce(b: BounceReason) -> Bool:
  match b:
    case BounceNeedsClarification{}:
      True{}
    case BounceRuleConflict{}:
      False{}
    case BounceFlawedIdea{}:
      False{}
    case BounceNone{}:
      False{}

def is_none_bounce(b: BounceReason) -> Bool:
  match b:
    case BounceNone{}:
      True{}
    case BounceRuleConflict{}:
      False{}
    case BounceFlawedIdea{}:
      False{}
    case BounceNeedsClarification{}:
      False{}

def should_allow_git_commit(
  bounds_ok: Bool,
  contracts_ok: Bool,
  bend_proofs_ok: Bool,
  no_dangling_deletes: Bool,
) -> Bool:
  Bool.and(
    bounds_ok,
    Bool.and(
      contracts_ok,
      Bool.and(bend_proofs_ok, no_dangling_deletes)
    )
  )

def is_call_graph_skeleton_valid(
  interface_defined: Bool,
  law_fn_defined: Bool,
  interface_calls_law: Bool,
) -> Bool:
  Bool.and(interface_defined, Bool.and(law_fn_defined, interface_calls_law))

def main() -> ExecDecision:
  route_executive_decision(False{}, False{}, False{}, False{})
