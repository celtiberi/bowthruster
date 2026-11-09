# Site Y — Canonical Spec 23C (`the_executive` Voice Reply Guard & Pipeline Orchestrator) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `Spec 23C` build tag in `ModelCircuits/eng2` must prove these laws via `ModelCircuits/eng2/bend_exec_23c/PROOF.bend`.

import Base
import ./main.bend as EXC

# LAW 1: Reply validity bi-implication — `is_reply_valid(decision_matches_gate, reason_matches_gate, message_non_empty, all_required_citations_present)`
# returns `True{}` iff all four guard conditions hold (`True{}`).
law reply_valid_iff_all_guards_hold:
  for +decision_matches_gate: Bool
  for +reason_matches_gate: Bool
  for +message_non_empty: Bool
  for +all_required_citations_present: Bool
  {EXC.bool_eq(
    EXC.is_reply_valid(
      decision_matches_gate,
      reason_matches_gate,
      message_non_empty,
      all_required_citations_present
    ),
    Bool.and(
      decision_matches_gate,
      Bool.and(
        reason_matches_gate,
        Bool.and(message_non_empty, all_required_citations_present)
      )
    )
  ) == True{} : Bool}

# LAW 2: Anti-override gate fidelity — whenever `decision_matches_gate == False{}` or `reason_matches_gate == False{}`,
# `is_reply_valid` ALWAYS returns `False{}` (the Executive Voice LLM can never override the Bend 2 Routing Gate).
law mismatched_gate_verdict_rejects_reply:
  for +reason_matches_gate: Bool
  for +message_non_empty: Bool
  for +all_required_citations_present: Bool
  {Bool.and(
    Bool.not(EXC.is_reply_valid(False{}, reason_matches_gate, message_non_empty, all_required_citations_present)),
    Bool.not(EXC.is_reply_valid(True{}, False{}, message_non_empty, all_required_citations_present))
  ) == True{} : Bool}

# LAW 3: Mandatory conflict/clarification citation — whenever `all_required_citations_present == False{}` or
# `message_non_empty == False{}`, `is_reply_valid` ALWAYS returns `False{}`.
law missing_citation_or_empty_message_rejects_reply:
  for +decision_matches_gate: Bool
  for +reason_matches_gate: Bool
  {Bool.and(
    Bool.not(EXC.is_reply_valid(decision_matches_gate, reason_matches_gate, False{}, True{})),
    Bool.not(EXC.is_reply_valid(decision_matches_gate, reason_matches_gate, True{}, False{}))
  ) == True{} : Bool}

# LAW 4: Post-Commit Downstream Stale Cascade Law (`postcommit_stale_cascade_law`, Stage 26 `postcommit_lattice_updater`) —
# `should_mark_dependent_stale_on_commit(upstream_contract_changed, dependent_was_verified)` returns `True{}`
# iff `upstream_contract_changed == True{}` AND `dependent_was_verified == True{}`.
law postcommit_stale_cascade_law:
  for +upstream_changed: Bool
  for +dep_verified: Bool
  {EXC.bool_eq(
    EXC.should_mark_dependent_stale_on_commit(upstream_changed, dep_verified),
    Bool.and(upstream_changed, dep_verified)
  ) == True{} : Bool}
