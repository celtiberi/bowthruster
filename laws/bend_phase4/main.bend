# Spec 19: Phase 4 Graph Ingestion, Delta Classification & Prohibition Guard (`main.bend`)

import Base

type NodeKind is Data:
  NKQuestion{}
  NKCommit{}
  NKAction{}
  NKFact{}
  NKRule{}

type GroundingKind is Data:
  GGrounded{}
  GHasOpenQuestions{}
  GNeedsClarification{}

type DeltaOp is Data:
  OpBlockerClarification{}
  OpBlockerQuestion{}
  OpCommitNote{}
  OpGraduateTaskToFact{}
  OpUpsertTask{}
  OpAttachRationale{}
  OpMarkovianOverwrite{}
  OpLexSpecialisException{}
  OpOrthogonalInsert{}

type IngestionStatus is Data:
  StatusNeedsClarification{}
  StatusConflict{}
  StatusCommitted{}

def is_clarification_op(op: DeltaOp) -> Bool:
  match op:
    case OpBlockerClarification{}:
      True{}
    case OpBlockerQuestion{}:
      False{}
    case OpCommitNote{}:
      False{}
    case OpGraduateTaskToFact{}:
      False{}
    case OpUpsertTask{}:
      False{}
    case OpAttachRationale{}:
      False{}
    case OpMarkovianOverwrite{}:
      False{}
    case OpLexSpecialisException{}:
      False{}
    case OpOrthogonalInsert{}:
      False{}

def is_rule_table_op(op: DeltaOp) -> Bool:
  match op:
    case OpBlockerClarification{}:
      False{}
    case OpBlockerQuestion{}:
      False{}
    case OpCommitNote{}:
      False{}
    case OpGraduateTaskToFact{}:
      False{}
    case OpUpsertTask{}:
      False{}
    case OpAttachRationale{}:
      True{}
    case OpMarkovianOverwrite{}:
      True{}
    case OpLexSpecialisException{}:
      True{}
    case OpOrthogonalInsert{}:
      True{}

def is_markovian_overwrite(op: DeltaOp) -> Bool:
  match op:
    case OpBlockerClarification{}:
      False{}
    case OpBlockerQuestion{}:
      False{}
    case OpCommitNote{}:
      False{}
    case OpGraduateTaskToFact{}:
      False{}
    case OpUpsertTask{}:
      False{}
    case OpAttachRationale{}:
      False{}
    case OpMarkovianOverwrite{}:
      True{}
    case OpLexSpecialisException{}:
      False{}
    case OpOrthogonalInsert{}:
      False{}

def classify_delta(
  nt: NodeKind,
  grounding: GroundingKind,
  has_unclear: Bool,
  completed: Bool,
  has_target_prop: Bool,
  is_prohibited: Bool,
  has_overwrite_match: Bool,
  has_broader_rules: Bool,
  has_rationale_match: Bool
) -> DeltaOp:
  match nt grounding has_unclear completed has_target_prop is_prohibited has_overwrite_match has_broader_rules has_rationale_match:
    case _ GNeedsClarification{} _ _ _ _ _ _ _:
      OpBlockerClarification{}
    case _ GHasOpenQuestions{} True{} _ _ _ _ _ _:
      OpBlockerClarification{}
    case _ GHasOpenQuestions{} False{} _ _ _ _ _ _:
      OpBlockerQuestion{}
    case _ GGrounded{} True{} _ _ _ _ _ _:
      OpBlockerClarification{}
    case NKQuestion{} GGrounded{} False{} _ _ _ _ _ _:
      OpBlockerQuestion{}
    case NKCommit{} GGrounded{} False{} _ _ _ _ _ _:
      OpCommitNote{}
    case NKAction{} GGrounded{} False{} True{} _ _ _ _ _:
      OpGraduateTaskToFact{}
    case NKAction{} GGrounded{} False{} False{} _ _ _ _ _:
      OpUpsertTask{}
    case NKFact{} GGrounded{} False{} _ False{} _ _ True{} True{}:
      OpAttachRationale{}
    case NKFact{} GGrounded{} False{} _ False{} _ _ False{} True{}:
      OpAttachRationale{}
    case NKFact{} GGrounded{} False{} _ False{} _ _ True{} False{}:
      OpLexSpecialisException{}
    case NKFact{} GGrounded{} False{} _ False{} _ _ False{} False{}:
      OpOrthogonalInsert{}
    case NKFact{} GGrounded{} False{} _ True{} False{} True{} _ _:
      OpMarkovianOverwrite{}
    case NKFact{} GGrounded{} False{} _ True{} False{} False{} True{} _:
      OpLexSpecialisException{}
    case NKFact{} GGrounded{} False{} _ True{} False{} False{} False{} _:
      OpOrthogonalInsert{}
    case NKFact{} GGrounded{} False{} _ True{} True{} _ True{} _:
      OpLexSpecialisException{}
    case NKFact{} GGrounded{} False{} _ True{} True{} _ False{} _:
      OpOrthogonalInsert{}
    case NKRule{} GGrounded{} False{} _ False{} _ _ True{} _:
      OpLexSpecialisException{}
    case NKRule{} GGrounded{} False{} _ False{} _ _ False{} _:
      OpOrthogonalInsert{}
    case NKRule{} GGrounded{} False{} _ True{} False{} True{} _ _:
      OpMarkovianOverwrite{}
    case NKRule{} GGrounded{} False{} _ True{} False{} False{} True{} _:
      OpLexSpecialisException{}
    case NKRule{} GGrounded{} False{} _ True{} False{} False{} False{} _:
      OpOrthogonalInsert{}
    case NKRule{} GGrounded{} False{} _ True{} True{} _ True{} _:
      OpLexSpecialisException{}
    case NKRule{} GGrounded{} False{} _ True{} True{} _ False{} _:
      OpOrthogonalInsert{}

def prohibition_clashes(a_is_prohib: Bool, b_is_prohib: Bool, same_val: Bool, intervals_disjoint: Bool) -> Bool:
  match a_is_prohib b_is_prohib same_val intervals_disjoint:
    case True{} True{} _ _:
      False{}
    case False{} False{} _ _:
      False{}
    case True{} False{} True{} _:
      True{}
    case True{} False{} False{} True{}:
      True{}
    case True{} False{} False{} False{}:
      False{}
    case False{} True{} True{} _:
      True{}
    case False{} True{} False{} True{}:
      True{}
    case False{} True{} False{} False{}:
      False{}

def resolve_ingestion_status(has_clarification: Bool, has_conflicts: Bool) -> IngestionStatus:
  match has_clarification has_conflicts:
    case True{} _:
      StatusNeedsClarification{}
    case False{} True{}:
      StatusConflict{}
    case False{} False{}:
      StatusCommitted{}

def is_committed_status(s: IngestionStatus) -> Bool:
  match s:
    case StatusNeedsClarification{}:
      False{}
    case StatusConflict{}:
      False{}
    case StatusCommitted{}:
      True{}
