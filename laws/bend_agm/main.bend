# Spec 17D: Phase 2 AGM & QUD State Machine (`main.bend`)

import Base

type Role is Data:
  RQuestion{}
  RAction{}
  RRule{}
  RContext{}
  RCommit{}
  RShelve{}

type CommitmentStatus is Data:
  Active{}
  Revised{}
  Contracted{}
  Resolved{}
  Subordinated{}
  Shelved{}

type QUDOpt is Data:
  QUDNone{}
  QUDOpen{}
  QUDResolved{}

def is_question_role(role: Role) -> Bool:
  match role:
    case RQuestion{}:
      True{}
    case RAction{}:
      False{}
    case RRule{}:
      False{}
    case RContext{}:
      False{}
    case RCommit{}:
      False{}
    case RShelve{}:
      False{}

def qud_is_some(q: QUDOpt) -> Bool:
  match q:
    case QUDNone{}:
      False{}
    case QUDOpen{}:
      True{}
    case QUDResolved{}:
      True{}

def compute_qud_state(role: Role, has_resolutions: Bool) -> QUDOpt:
  match role has_resolutions:
    case RQuestion{} False{}:
      QUDOpen{}
    case RQuestion{} True{}:
      QUDResolved{}
    case RAction{} _:
      QUDNone{}
    case RRule{} _:
      QUDNone{}
    case RContext{} _:
      QUDNone{}
    case RCommit{} _:
      QUDNone{}
    case RShelve{} _:
      QUDNone{}

def transition_root_status(role: Role, has_revoked: Bool, has_revisions: Bool, has_resolutions: Bool) -> CommitmentStatus:
  match role has_revoked has_revisions has_resolutions:
    case _ True{} _ _:
      Contracted{}
    case RQuestion{} False{} _ True{}:
      Resolved{}
    case RQuestion{} False{} _ False{}:
      Active{}
    case RAction{} False{} True{} _:
      Revised{}
    case RAction{} False{} False{} _:
      Active{}
    case RRule{} False{} True{} _:
      Revised{}
    case RRule{} False{} False{} _:
      Active{}
    case RContext{} False{} True{} _:
      Revised{}
    case RContext{} False{} False{} _:
      Active{}
    case RCommit{} False{} True{} _:
      Revised{}
    case RCommit{} False{} False{} _:
      Active{}
    case RShelve{} False{} True{} _:
      Revised{}
    case RShelve{} False{} False{} _:
      Active{}

def transition_dep_status(gov_status: CommitmentStatus) -> CommitmentStatus:
  match gov_status:
    case Contracted{}:
      Contracted{}
    case Active{}:
      Subordinated{}
    case Revised{}:
      Subordinated{}
    case Resolved{}:
      Subordinated{}
    case Subordinated{}:
      Subordinated{}
    case Shelved{}:
      Subordinated{}

def transition_shelved_status() -> CommitmentStatus:
  Shelved{}

def is_root_commitment(s: CommitmentStatus) -> Bool:
  match s:
    case Active{}:
      True{}
    case Revised{}:
      True{}
    case Resolved{}:
      True{}
    case Contracted{}:
      False{}
    case Subordinated{}:
      False{}
    case Shelved{}:
      False{}

def is_contracted(s: CommitmentStatus) -> Bool:
  match s:
    case Contracted{}:
      True{}
    case Active{}:
      False{}
    case Revised{}:
      False{}
    case Resolved{}:
      False{}
    case Subordinated{}:
      False{}
    case Shelved{}:
      False{}

def is_shelved(s: CommitmentStatus) -> Bool:
  match s:
    case Shelved{}:
      True{}
    case Active{}:
      False{}
    case Revised{}:
      False{}
    case Contracted{}:
      False{}
    case Resolved{}:
      False{}
    case Subordinated{}:
      False{}

def is_open_qud(q: QUDOpt, s: CommitmentStatus) -> Bool:
  match q s:
    case QUDOpen{} Active{}:
      True{}
    case _ _:
      False{}

def is_resolved_qud(q: QUDOpt, s: CommitmentStatus) -> Bool:
  match q s:
    case QUDResolved{} Resolved{}:
      True{}
    case _ _:
      False{}
