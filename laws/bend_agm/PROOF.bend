# Spec 17D: Phase 2 AGM & QUD State Machine Formal Proofs (`PROOF.bend`)

import Base
import ./main.bend as AGM
import ./LAWS.bend as Laws

def Laws.revocation_dominates_all(role, has_revisions, has_resolutions):
  match role has_revisions has_resolutions:
    case AGM.RQuestion{} _ _:
      {==}
    case AGM.RAction{} _ _:
      {==}
    case AGM.RRule{} _ _:
      {==}
    case AGM.RContext{} _ _:
      {==}
    case AGM.RCommit{} _ _:
      {==}
    case AGM.RShelve{} _ _:
      {==}

def Laws.contracted_governor_propagates(gov):
  match gov:
    case AGM.Active{}:
      {==}
    case AGM.Revised{}:
      {==}
    case AGM.Contracted{}:
      {==}
    case AGM.Resolved{}:
      {==}
    case AGM.Subordinated{}:
      {==}
    case AGM.Shelved{}:
      {==}

def Laws.qud_only_on_questions(role, has_resolutions):
  match role has_resolutions:
    case AGM.RQuestion{} False{}:
      {==}
    case AGM.RQuestion{} True{}:
      {==}
    case AGM.RAction{} _:
      {==}
    case AGM.RRule{} _:
      {==}
    case AGM.RContext{} _:
      {==}
    case AGM.RCommit{} _:
      {==}
    case AGM.RShelve{} _:
      {==}

def Laws.root_partition_disjoint_and_complete(role, has_revoked, has_revisions, has_resolutions):
  match role has_revoked has_revisions has_resolutions:
    case _ True{} _ _:
      {==}
    case AGM.RQuestion{} False{} _ True{}:
      {==}
    case AGM.RQuestion{} False{} _ False{}:
      {==}
    case AGM.RAction{} False{} True{} _:
      {==}
    case AGM.RAction{} False{} False{} _:
      {==}
    case AGM.RRule{} False{} True{} _:
      {==}
    case AGM.RRule{} False{} False{} _:
      {==}
    case AGM.RContext{} False{} True{} _:
      {==}
    case AGM.RContext{} False{} False{} _:
      {==}
    case AGM.RCommit{} False{} True{} _:
      {==}
    case AGM.RCommit{} False{} False{} _:
      {==}
    case AGM.RShelve{} False{} True{} _:
      {==}
    case AGM.RShelve{} False{} False{} _:
      {==}

def Laws.active_never_shelved(role, has_revoked, has_revisions, has_resolutions, gov):
  match role has_revoked has_revisions has_resolutions gov:
    case _ True{} _ _ AGM.Contracted{}:
      {==}
    case _ True{} _ _ AGM.Active{}:
      {==}
    case _ True{} _ _ AGM.Revised{}:
      {==}
    case _ True{} _ _ AGM.Resolved{}:
      {==}
    case _ True{} _ _ AGM.Subordinated{}:
      {==}
    case _ True{} _ _ AGM.Shelved{}:
      {==}
    case AGM.RQuestion{} False{} _ True{} AGM.Contracted{}:
      {==}
    case AGM.RQuestion{} False{} _ True{} AGM.Active{}:
      {==}
    case AGM.RQuestion{} False{} _ True{} AGM.Revised{}:
      {==}
    case AGM.RQuestion{} False{} _ True{} AGM.Resolved{}:
      {==}
    case AGM.RQuestion{} False{} _ True{} AGM.Subordinated{}:
      {==}
    case AGM.RQuestion{} False{} _ True{} AGM.Shelved{}:
      {==}
    case AGM.RQuestion{} False{} _ False{} AGM.Contracted{}:
      {==}
    case AGM.RQuestion{} False{} _ False{} AGM.Active{}:
      {==}
    case AGM.RQuestion{} False{} _ False{} AGM.Revised{}:
      {==}
    case AGM.RQuestion{} False{} _ False{} AGM.Resolved{}:
      {==}
    case AGM.RQuestion{} False{} _ False{} AGM.Subordinated{}:
      {==}
    case AGM.RQuestion{} False{} _ False{} AGM.Shelved{}:
      {==}
    case AGM.RAction{} False{} True{} _ AGM.Contracted{}:
      {==}
    case AGM.RAction{} False{} True{} _ AGM.Active{}:
      {==}
    case AGM.RAction{} False{} True{} _ AGM.Revised{}:
      {==}
    case AGM.RAction{} False{} True{} _ AGM.Resolved{}:
      {==}
    case AGM.RAction{} False{} True{} _ AGM.Subordinated{}:
      {==}
    case AGM.RAction{} False{} True{} _ AGM.Shelved{}:
      {==}
    case AGM.RAction{} False{} False{} _ AGM.Contracted{}:
      {==}
    case AGM.RAction{} False{} False{} _ AGM.Active{}:
      {==}
    case AGM.RAction{} False{} False{} _ AGM.Revised{}:
      {==}
    case AGM.RAction{} False{} False{} _ AGM.Resolved{}:
      {==}
    case AGM.RAction{} False{} False{} _ AGM.Subordinated{}:
      {==}
    case AGM.RAction{} False{} False{} _ AGM.Shelved{}:
      {==}
    case AGM.RRule{} False{} True{} _ AGM.Contracted{}:
      {==}
    case AGM.RRule{} False{} True{} _ AGM.Active{}:
      {==}
    case AGM.RRule{} False{} True{} _ AGM.Revised{}:
      {==}
    case AGM.RRule{} False{} True{} _ AGM.Resolved{}:
      {==}
    case AGM.RRule{} False{} True{} _ AGM.Subordinated{}:
      {==}
    case AGM.RRule{} False{} True{} _ AGM.Shelved{}:
      {==}
    case AGM.RRule{} False{} False{} _ AGM.Contracted{}:
      {==}
    case AGM.RRule{} False{} False{} _ AGM.Active{}:
      {==}
    case AGM.RRule{} False{} False{} _ AGM.Revised{}:
      {==}
    case AGM.RRule{} False{} False{} _ AGM.Resolved{}:
      {==}
    case AGM.RRule{} False{} False{} _ AGM.Subordinated{}:
      {==}
    case AGM.RRule{} False{} False{} _ AGM.Shelved{}:
      {==}
    case AGM.RContext{} False{} True{} _ AGM.Contracted{}:
      {==}
    case AGM.RContext{} False{} True{} _ AGM.Active{}:
      {==}
    case AGM.RContext{} False{} True{} _ AGM.Revised{}:
      {==}
    case AGM.RContext{} False{} True{} _ AGM.Resolved{}:
      {==}
    case AGM.RContext{} False{} True{} _ AGM.Subordinated{}:
      {==}
    case AGM.RContext{} False{} True{} _ AGM.Shelved{}:
      {==}
    case AGM.RContext{} False{} False{} _ AGM.Contracted{}:
      {==}
    case AGM.RContext{} False{} False{} _ AGM.Active{}:
      {==}
    case AGM.RContext{} False{} False{} _ AGM.Revised{}:
      {==}
    case AGM.RContext{} False{} False{} _ AGM.Resolved{}:
      {==}
    case AGM.RContext{} False{} False{} _ AGM.Subordinated{}:
      {==}
    case AGM.RContext{} False{} False{} _ AGM.Shelved{}:
      {==}
    case AGM.RCommit{} False{} True{} _ AGM.Contracted{}:
      {==}
    case AGM.RCommit{} False{} True{} _ AGM.Active{}:
      {==}
    case AGM.RCommit{} False{} True{} _ AGM.Revised{}:
      {==}
    case AGM.RCommit{} False{} True{} _ AGM.Resolved{}:
      {==}
    case AGM.RCommit{} False{} True{} _ AGM.Subordinated{}:
      {==}
    case AGM.RCommit{} False{} True{} _ AGM.Shelved{}:
      {==}
    case AGM.RCommit{} False{} False{} _ AGM.Contracted{}:
      {==}
    case AGM.RCommit{} False{} False{} _ AGM.Active{}:
      {==}
    case AGM.RCommit{} False{} False{} _ AGM.Revised{}:
      {==}
    case AGM.RCommit{} False{} False{} _ AGM.Resolved{}:
      {==}
    case AGM.RCommit{} False{} False{} _ AGM.Subordinated{}:
      {==}
    case AGM.RCommit{} False{} False{} _ AGM.Shelved{}:
      {==}
    case AGM.RShelve{} False{} True{} _ AGM.Contracted{}:
      {==}
    case AGM.RShelve{} False{} True{} _ AGM.Active{}:
      {==}
    case AGM.RShelve{} False{} True{} _ AGM.Revised{}:
      {==}
    case AGM.RShelve{} False{} True{} _ AGM.Resolved{}:
      {==}
    case AGM.RShelve{} False{} True{} _ AGM.Subordinated{}:
      {==}
    case AGM.RShelve{} False{} True{} _ AGM.Shelved{}:
      {==}
    case AGM.RShelve{} False{} False{} _ AGM.Contracted{}:
      {==}
    case AGM.RShelve{} False{} False{} _ AGM.Active{}:
      {==}
    case AGM.RShelve{} False{} False{} _ AGM.Revised{}:
      {==}
    case AGM.RShelve{} False{} False{} _ AGM.Resolved{}:
      {==}
    case AGM.RShelve{} False{} False{} _ AGM.Subordinated{}:
      {==}
    case AGM.RShelve{} False{} False{} _ AGM.Shelved{}:
      {==}

def Laws.agm_vacuity_and_revision_priority(has_revisions):
  match has_revisions:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.question_qud_partition(has_revisions, has_resolutions):
  match has_revisions has_resolutions:
    case _ False{}:
      {==}
    case _ True{}:
      {==}
