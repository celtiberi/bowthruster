# Site Y — Canonical Spec 17D (Phase 2 AGM & QUD State Machine) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every build tag in `ModelCircuits/eng2` must prove these laws via `PROOF.bend`.
#
# Required `main.bend` (`import ./main.bend as AGM`) datatypes & signatures:
#   type Role is Data: RQuestion{} | RAction{} | RRule{} | RContext{} | RCommit{} | RShelve{}
#   type CommitmentStatus is Data: Active{} | Revised{} | Contracted{} | Resolved{} | Subordinated{} | Shelved{}
#   type QUDOpt is Data: QUDNone{} | QUDOpen{} | QUDResolved{}
#   def is_question_role(role: Role) -> Bool
#   def qud_is_some(q: QUDOpt) -> Bool
#   def compute_qud_state(role: Role, has_resolutions: Bool) -> QUDOpt
#   def transition_root_status(role: Role, has_revoked: Bool, has_revisions: Bool, has_resolutions: Bool) -> CommitmentStatus
#   def transition_dep_status(gov_status: CommitmentStatus) -> CommitmentStatus
#   def transition_shelved_status() -> CommitmentStatus
#   def is_root_commitment(s: CommitmentStatus) -> Bool
#   def is_contracted(s: CommitmentStatus) -> Bool
#   def is_shelved(s: CommitmentStatus) -> Bool
#   def is_open_qud(q: QUDOpt, s: CommitmentStatus) -> Bool
#   def is_resolved_qud(q: QUDOpt, s: CommitmentStatus) -> Bool

import Base
import ./main.bend as AGM

# LAW 1: Revocation Dominates All (Spec 17D §2.3 Step 1) —
# A ROOT EDU with non-empty `revoked_by` (`has_revoked = True{}`) ALWAYS transitions
# to `Contracted{}` regardless of `role`, `has_revisions`, or `has_resolutions`.
law revocation_dominates_all:
  for role: AGM.Role
  for has_revisions: Bool
  for has_resolutions: Bool
  {AGM.transition_root_status(role, True{}, has_revisions, has_resolutions) == AGM.Contracted{} : AGM.CommitmentStatus}

# LAW 2: Contracted Governor Propagates One Hop (Spec 17D §2.3 Step 2) —
# A Dependent EDU transitions to `Contracted{}` iff its governing ROOT EDU is `Contracted{}`,
# and transitions to `Subordinated{}` for every non-contracted governor status.
law contracted_governor_propagates:
  for +gov: AGM.CommitmentStatus
  {(AGM.is_contracted(AGM.transition_dep_status(gov)),
    AGM.transition_dep_status(AGM.Contracted{}),
    AGM.transition_dep_status(AGM.Active{}),
    AGM.transition_dep_status(AGM.Revised{}),
    AGM.transition_dep_status(AGM.Resolved{}))
   == (AGM.is_contracted(gov),
       AGM.Contracted{},
       AGM.Subordinated{},
       AGM.Subordinated{},
       AGM.Subordinated{})
   : Bool & AGM.CommitmentStatus & AGM.CommitmentStatus & AGM.CommitmentStatus & AGM.CommitmentStatus}

# LAW 3: QUD Gameboard Closure Only on Questions (Spec 17D §2.2 & Invariant 5) —
# `compute_qud_state(role, has_resolutions)` yields a non-null QUD status (`qud_is_some == True{}`)
# iff `role` is `RQuestion{}`, returning `QUDOpen{}` when `has_resolutions == False{}`
# and `QUDResolved{}` when `has_resolutions == True{}`.
law qud_only_on_questions:
  for +role: AGM.Role
  for +has_resolutions: Bool
  {(AGM.qud_is_some(AGM.compute_qud_state(role, has_resolutions)),
    AGM.compute_qud_state(AGM.RQuestion{}, False{}),
    AGM.compute_qud_state(AGM.RQuestion{}, True{}))
   == (AGM.is_question_role(role),
       AGM.QUDOpen{},
       AGM.QUDResolved{})
   : Bool & AGM.QUDOpt & AGM.QUDOpt}

# LAW 4: Root Partition Disjoint and Complete (Spec 17D §3 & Invariant 5) —
# For every ROOT EDU, `is_root_commitment` (`Active{} | Revised{} | Resolved{}`) and
# `is_contracted` (`Contracted{}`) are mutually exclusive (`Bool.and == False{}`)
# and exhaustive (`Bool.or == True{}`).
law root_partition_disjoint_and_complete:
  for +role: AGM.Role
  for +has_revoked: Bool
  for +has_revisions: Bool
  for +has_resolutions: Bool
  {(Bool.and(
      AGM.is_root_commitment(AGM.transition_root_status(role, has_revoked, has_revisions, has_resolutions)),
      AGM.is_contracted(AGM.transition_root_status(role, has_revoked, has_revisions, has_resolutions))
    ),
    Bool.or(
      AGM.is_root_commitment(AGM.transition_root_status(role, has_revoked, has_revisions, has_resolutions)),
      AGM.is_contracted(AGM.transition_root_status(role, has_revoked, has_revisions, has_resolutions))
    ))
   == (False{}, True{}) : Bool & Bool}

# LAW 5: Active Chunks Are Never Shelved (Spec 17D §2.3 Step 3 & Invariant 5) —
# Neither a ROOT EDU nor a Dependent EDU in `active_chunks` ever transitions to `Shelved{}`,
# whereas `transition_shelved_status()` always yields `Shelved{}`.
law active_never_shelved:
  for role: AGM.Role
  for has_revoked: Bool
  for has_revisions: Bool
  for has_resolutions: Bool
  for gov: AGM.CommitmentStatus
  {(AGM.is_shelved(AGM.transition_root_status(role, has_revoked, has_revisions, has_resolutions)),
    AGM.is_shelved(AGM.transition_dep_status(gov)),
    AGM.is_shelved(AGM.transition_shelved_status()))
   == (False{}, False{}, True{}) : Bool & Bool & Bool}

# LAW 6: AGM Vacuity, Revision & QUD Resolution Priority (Spec 17D §2.3 Step 1) —
# When `has_revoked == False{}`: an unmodified non-question root transitions to `Active{}` (Vacuity),
# a corrected non-question root transitions to `Revised{}` (Revision), and a non-revoked `RQuestion{}`
# transitions to `Resolved{}` when `has_resolutions == True{}` and `Active{}` when `has_resolutions == False{}`.
law agm_vacuity_and_revision_priority:
  for +has_revisions: Bool
  {(AGM.transition_root_status(AGM.RAction{}, False{}, False{}, False{}),
    AGM.transition_root_status(AGM.RAction{}, False{}, True{}, False{}),
    AGM.transition_root_status(AGM.RQuestion{}, False{}, has_revisions, True{}),
    AGM.transition_root_status(AGM.RQuestion{}, False{}, has_revisions, False{}))
   == (AGM.Active{}, AGM.Revised{}, AGM.Resolved{}, AGM.Active{})
   : AGM.CommitmentStatus & AGM.CommitmentStatus & AGM.CommitmentStatus & AGM.CommitmentStatus}

# LAW 7: Non-Contracted Question QUD Partition (Spec 17D §3 & Invariant 5) —
# For every non-contracted `RQuestion{}` (`has_revoked == False{}`), `is_open_qud` and
# `is_resolved_qud` are mutually exclusive (`Bool.and == False{}`) and exhaustive (`Bool.or == True{}`).
law question_qud_partition:
  for +has_revisions: Bool
  for +has_resolutions: Bool
  {(Bool.and(
      AGM.is_open_qud(
        AGM.compute_qud_state(AGM.RQuestion{}, has_resolutions),
        AGM.transition_root_status(AGM.RQuestion{}, False{}, has_revisions, has_resolutions)
      ),
      AGM.is_resolved_qud(
        AGM.compute_qud_state(AGM.RQuestion{}, has_resolutions),
        AGM.transition_root_status(AGM.RQuestion{}, False{}, has_revisions, has_resolutions)
      )
    ),
    Bool.or(
      AGM.is_open_qud(
        AGM.compute_qud_state(AGM.RQuestion{}, has_resolutions),
        AGM.transition_root_status(AGM.RQuestion{}, False{}, has_revisions, has_resolutions)
      ),
      AGM.is_resolved_qud(
        AGM.compute_qud_state(AGM.RQuestion{}, has_resolutions),
        AGM.transition_root_status(AGM.RQuestion{}, False{}, has_revisions, has_resolutions)
      )
    ))
   == (False{}, True{}) : Bool & Bool}
