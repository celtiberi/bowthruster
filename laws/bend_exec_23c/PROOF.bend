# Spec 23C: Executive Voice Reply Guard Formal Proofs (`PROOF.bend`)

import Base
import ./main.bend as EXC
import ./LAWS.bend as Laws

def Laws.reply_valid_iff_all_guards_hold(
  decision_matches_gate,
  reason_matches_gate,
  message_non_empty,
  all_required_citations_present
):
  match decision_matches_gate reason_matches_gate message_non_empty all_required_citations_present:
    case True{} True{} True{} True{}:
      {==}
    case True{} True{} True{} False{}:
      {==}
    case True{} True{} False{} True{}:
      {==}
    case True{} True{} False{} False{}:
      {==}
    case True{} False{} True{} True{}:
      {==}
    case True{} False{} True{} False{}:
      {==}
    case True{} False{} False{} True{}:
      {==}
    case True{} False{} False{} False{}:
      {==}
    case False{} True{} True{} True{}:
      {==}
    case False{} True{} True{} False{}:
      {==}
    case False{} True{} False{} True{}:
      {==}
    case False{} True{} False{} False{}:
      {==}
    case False{} False{} True{} True{}:
      {==}
    case False{} False{} True{} False{}:
      {==}
    case False{} False{} False{} True{}:
      {==}
    case False{} False{} False{} False{}:
      {==}

def Laws.mismatched_gate_verdict_rejects_reply(
  reason_matches_gate,
  message_non_empty,
  all_required_citations_present
):
  match reason_matches_gate message_non_empty all_required_citations_present:
    case True{} _ _:
      {==}
    case False{} _ _:
      {==}

def Laws.missing_citation_or_empty_message_rejects_reply(
  decision_matches_gate,
  reason_matches_gate
):
  match decision_matches_gate reason_matches_gate:
    case True{} True{}:
      {==}
    case True{} False{}:
      {==}
    case False{} True{}:
      {==}
    case False{} False{}:
      {==}

def Laws.postcommit_stale_cascade_law(
  upstream_changed,
  dep_verified
):
  match upstream_changed dep_verified:
    case True{} True{}:
      {==}
    case True{} False{}:
      {==}
    case False{} True{}:
      {==}
    case False{} False{}:
      {==}
