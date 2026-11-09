# Spec 23C: Executive Voice Reply Guard (`main.bend`)

import Base

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

def is_reply_valid(
  decision_matches_gate: Bool,
  reason_matches_gate: Bool,
  message_non_empty: Bool,
  all_required_citations_present: Bool
) -> Bool:
  match decision_matches_gate reason_matches_gate message_non_empty all_required_citations_present:
    case True{} True{} True{} True{}:
      True{}
    case True{} True{} True{} False{}:
      False{}
    case True{} True{} False{} _:
      False{}
    case True{} False{} _ _:
      False{}
    case False{} _ _ _:
      False{}

def should_mark_dependent_stale_on_commit(
  upstream_contract_changed: Bool,
  dependent_was_verified: Bool
) -> Bool:
  match upstream_contract_changed dependent_was_verified:
    case True{} True{}:
      True{}
    case True{} False{}:
      False{}
    case False{} _:
      False{}
