# Spec 22C: Whiteboard Intake Linker, Task Readiness & Read Engine (`main.bend`)

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

type TaskReadiness is Data:
  TaskBlockedByConflict{}
  TaskBlockedByRule{}
  TaskWaitingOnDep{}
  TaskReady{}

type LatticeKind is Data:
  LKComponent{}
  LKFlow{}
  LKSchema{}
  LKInterface{}
  LKLaw{}
  LKConstraint{}
  LKFact{}
  LKTask{}

type LatticeDeltaOp is Data:
  LOpBlockerClarification{}
  LOpUpsertComponent{}
  LOpUpsertFlow{}
  LOpUpsertSchema{}
  LOpUpsertInterface{}
  LOpUpsertLaw{}
  LOpDelegateRuleOrTask{}

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

def classify_task_readiness(has_conflict: Bool, has_prohibition: Bool, has_unmet_dep: Bool) -> TaskReadiness:
  match has_conflict has_prohibition has_unmet_dep:
    case True{} _ _:
      TaskBlockedByConflict{}
    case False{} True{} _:
      TaskBlockedByRule{}
    case False{} False{} True{}:
      TaskWaitingOnDep{}
    case False{} False{} False{}:
      TaskReady{}

def is_task_ready(r: TaskReadiness) -> Bool:
  match r:
    case TaskBlockedByConflict{}:
      False{}
    case TaskBlockedByRule{}:
      False{}
    case TaskWaitingOnDep{}:
      False{}
    case TaskReady{}:
      True{}

def can_overwrite_node_kind(existing_kind: NodeKind, incoming_kind: NodeKind) -> Bool:
  match existing_kind incoming_kind:
    case NKFact{} NKFact{}:
      True{}
    case NKRule{} NKRule{}:
      True{}
    case _ _:
      False{}

def prohibition_blocks_task(
  rule_has_prop: Bool,
  task_has_prop: Bool,
  props_match: Bool,
  mentions_prohibited_val: Bool
) -> Bool:
  match rule_has_prop task_has_prop props_match mentions_prohibited_val:
    case False{} _ _ _:
      True{}
    case True{} _ _ True{}:
      True{}
    case True{} True{} True{} False{}:
      True{}
    case True{} True{} False{} False{}:
      False{}
    case True{} False{} _ False{}:
      False{}

def classify_lattice_delta(
  lkind: LatticeKind,
  grounding: GroundingKind,
  has_unclear: Bool
) -> LatticeDeltaOp:
  match lkind grounding has_unclear:
    case _ GNeedsClarification{} _:
      LOpBlockerClarification{}
    case _ GHasOpenQuestions{} True{}:
      LOpBlockerClarification{}
    case _ GGrounded{} True{}:
      LOpBlockerClarification{}
    case LKComponent{} GHasOpenQuestions{} False{}:
      LOpUpsertComponent{}
    case LKComponent{} GGrounded{} False{}:
      LOpUpsertComponent{}
    case LKFlow{} GHasOpenQuestions{} False{}:
      LOpUpsertFlow{}
    case LKFlow{} GGrounded{} False{}:
      LOpUpsertFlow{}
    case LKSchema{} GHasOpenQuestions{} False{}:
      LOpUpsertSchema{}
    case LKSchema{} GGrounded{} False{}:
      LOpUpsertSchema{}
    case LKInterface{} GHasOpenQuestions{} False{}:
      LOpUpsertInterface{}
    case LKInterface{} GGrounded{} False{}:
      LOpUpsertInterface{}
    case LKLaw{} GHasOpenQuestions{} False{}:
      LOpUpsertLaw{}
    case LKLaw{} GGrounded{} False{}:
      LOpUpsertLaw{}
    case LKConstraint{} GHasOpenQuestions{} False{}:
      LOpDelegateRuleOrTask{}
    case LKConstraint{} GGrounded{} False{}:
      LOpDelegateRuleOrTask{}
    case LKFact{} GHasOpenQuestions{} False{}:
      LOpDelegateRuleOrTask{}
    case LKFact{} GGrounded{} False{}:
      LOpDelegateRuleOrTask{}
    case LKTask{} GHasOpenQuestions{} False{}:
      LOpDelegateRuleOrTask{}
    case LKTask{} GGrounded{} False{}:
      LOpDelegateRuleOrTask{}

def is_lattice_clarification(op: LatticeDeltaOp) -> Bool:
  match op:
    case LOpBlockerClarification{}:
      True{}
    case LOpUpsertComponent{}:
      False{}
    case LOpUpsertFlow{}:
      False{}
    case LOpUpsertSchema{}:
      False{}
    case LOpUpsertInterface{}:
      False{}
    case LOpUpsertLaw{}:
      False{}
    case LOpDelegateRuleOrTask{}:
      False{}

def is_sbf_store_op(op: LatticeDeltaOp) -> Bool:
  match op:
    case LOpBlockerClarification{}:
      False{}
    case LOpUpsertComponent{}:
      True{}
    case LOpUpsertFlow{}:
      True{}
    case LOpUpsertSchema{}:
      True{}
    case LOpUpsertInterface{}:
      True{}
    case LOpUpsertLaw{}:
      True{}
    case LOpDelegateRuleOrTask{}:
      False{}

def is_python_skeleton_complete(
  has_schemas: Bool,
  has_interfaces: Bool,
  has_law_stubs: Bool
) -> Bool:
  match has_schemas has_interfaces has_law_stubs:
    case True{} True{} True{}:
      True{}
    case _ _ _:
      False{}
