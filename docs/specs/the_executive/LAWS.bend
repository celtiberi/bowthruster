# Site Y — Canonical Spec 23 (`the_executive` & `the_table` Prefrontal Gate & Working Discussion) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `the_executive` / `the_table` build tag in `ModelCircuits/eng1` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as EX

# LAW 1: Anti-sycophancy mandatory challenge — whenever `Falsification` is `FactClash{}`,
# `WhiteboardClash{}`, or `XYConfusion{}` (`is_flawed_premise(f) == True{}`), `route_turn(m, f, p, has_unclear)`
# ALWAYS emits `DirChallenge{}` and `WBNoOp{}` regardless of the user's `IntentMode` `m`
# (even if `m == IntentDecide{}`).
law clash_forces_challenge:
  for +m: EX.IntentMode
  for +f: EX.Falsification
  for +p: EX.ProposalState
  for +has_unclear: Bool
  {Bool.imp(
    EX.is_flawed_premise(f),
    Bool.and(
      EX.is_challenge_directive(EX.route_directive(m, f, p, has_unclear)),
      EX.is_wb_noop(EX.route_whiteboard(m, f, p, has_unclear))
    )
  ) == True{} : Bool}

# LAW 2: Clark's Grounding Gate — `route_whiteboard(m, f, p, has_unclear)` returns `WBCommit{}`
# iff `f == FalsValid{}`, `has_unclear == False{}`, `p == PropAccepted{}`, and
# `m` is `IntentDecide{}` or `IntentResolve{}`.
law no_unaccepted_whiteboard_write:
  for +m: EX.IntentMode
  for +f: EX.Falsification
  for +p: EX.ProposalState
  for +has_unclear: Bool
  {EX.is_wb_commit(EX.route_whiteboard(m, f, p, has_unclear)) ==
   Bool.and(
     EX.is_valid_falsification(f),
     Bool.and(
       Bool.not(has_unclear),
       Bool.and(
         EX.is_accepted_proposal(p),
         EX.is_commit_intent(m)
       )
     )
   ) : Bool}

# LAW 3: Exploratory table conservation — whenever `m` is `IntentBrainstorm{}` or `IntentAsk{}`,
# `route_whiteboard(m, f, p, has_unclear)` is always `WBNoOp{}` (the Whiteboard is bit-for-bit invariant).
law brainstorm_preserves_whiteboard:
  for +f: EX.Falsification
  for +p: EX.ProposalState
  for +has_unclear: Bool
  {Bool.and(
    EX.is_wb_noop(EX.route_whiteboard(EX.IntentBrainstorm{}, f, p, has_unclear)),
    EX.is_wb_noop(EX.route_whiteboard(EX.IntentAsk{}, f, p, has_unclear))
  ) == True{} : Bool}

# LAW 4: Blocker resolution monotonicity — an `OpenBlocker{}` transitions to `BlockerResolved{}`
# iff `m == IntentResolve{}`, `f == FalsValid{}`, and `has_unclear == False{}`.
law blocker_resolution_monotonic:
  for +m: EX.IntentMode
  for +f: EX.Falsification
  for +has_unclear: Bool
  {EX.is_blocker_resolved(EX.step_blocker(EX.BlockerOpen{}, m, f, has_unclear)) ==
   Bool.and(
     EX.is_resolve_intent(m),
     Bool.and(EX.is_valid_falsification(f), Bool.not(has_unclear))
   ) : Bool}
