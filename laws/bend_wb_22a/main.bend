# Spec 22A — `whiteboard` Schema, SQLite Persistence & Write Mutations (`main.bend`)
# Executable Bend 2 kernel for blocker resolution, mitosis rule conservation, SBF lattice store routing, ghost-scope exclusion, and build lifecycle transitions.

import Base

type BlockerStatus is Data:
  BlockerOpen{}
  BlockerResolved{}

type LatticeKind is Data:
  LKComponent{}
  LKFlow{}
  LKSchema{}
  LKInterface{}
  LKLaw{}
  LKConstraint{}
  LKFact{}
  LKTask{}

type LatticeStore is Data:
  StoreScopes{}
  StoreFlows{}
  StoreArtifacts{}
  StoreTasks{}
  StoreRules{}

type BuildStatus is Data:
  BSDraft{}
  BSBlockedByDep{}
  BSReady{}
  BSInProgress{}
  BSInQa{}
  BSFailed{}
  BSVerified{}
  BSStale{}

def step_blocker_resolution(status: BlockerStatus, id_matches: Bool) -> BlockerStatus:
  match status:
    case BlockerOpen{}:
      match id_matches:
        case True{}:
          BlockerResolved{}
        case False{}:
          BlockerOpen{}
    case BlockerResolved{}:
      BlockerResolved{}

def is_resolved(status: BlockerStatus) -> Bool:
  match status:
    case BlockerOpen{}:
      False{}
    case BlockerResolved{}:
      True{}

def add_nat(a: Nat, b: Nat) -> Nat:
  match a:
    case 0n:
      b
    case 1n+p:
      1n+add_nat(p, b)

def left_half(n: Nat) -> Nat:
  match n:
    case 0n:
      0n
    case 1n+p:
      match p:
        case 0n:
          1n
        case 1n+q:
          1n+left_half(q)

def right_half(n: Nat) -> Nat:
  match n:
    case 0n:
      0n
    case 1n+p:
      match p:
        case 0n:
          0n
        case 1n+q:
          1n+right_half(q)

def classify_lattice_store(kind: LatticeKind) -> LatticeStore:
  match kind:
    case LKComponent{}:
      StoreScopes{}
    case LKFlow{}:
      StoreFlows{}
    case LKSchema{}:
      StoreArtifacts{}
    case LKInterface{}:
      StoreArtifacts{}
    case LKLaw{}:
      StoreArtifacts{}
    case LKTask{}:
      StoreTasks{}
    case LKConstraint{}:
      StoreRules{}
    case LKFact{}:
      StoreRules{}

def is_flow_store(store: LatticeStore) -> Bool:
  match store:
    case StoreFlows{}:
      True{}
    case StoreScopes{}:
      False{}
    case StoreArtifacts{}:
      False{}
    case StoreTasks{}:
      False{}
    case StoreRules{}:
      False{}

def is_rule_store(store: LatticeStore) -> Bool:
  match store:
    case StoreRules{}:
      True{}
    case StoreScopes{}:
      False{}
    case StoreFlows{}:
      False{}
    case StoreArtifacts{}:
      False{}
    case StoreTasks{}:
      False{}

def should_register_as_scope(
  is_primary_scope: Bool,
  is_flow_endpoint: Bool,
  is_explicit_component: Bool,
  +is_bound_or_payload_val: Bool
) -> Bool:
  Bool.or(is_primary_scope, Bool.or(is_flow_endpoint, is_explicit_component))

def bs_eq(a: BuildStatus, b: BuildStatus) -> Bool:
  match a:
    case BSDraft{}:
      match b:
        case BSDraft{}:
          True{}
        case BSBlockedByDep{}:
          False{}
        case BSReady{}:
          False{}
        case BSInProgress{}:
          False{}
        case BSInQa{}:
          False{}
        case BSFailed{}:
          False{}
        case BSVerified{}:
          False{}
        case BSStale{}:
          False{}
    case BSBlockedByDep{}:
      match b:
        case BSDraft{}:
          False{}
        case BSBlockedByDep{}:
          True{}
        case BSReady{}:
          False{}
        case BSInProgress{}:
          False{}
        case BSInQa{}:
          False{}
        case BSFailed{}:
          False{}
        case BSVerified{}:
          False{}
        case BSStale{}:
          False{}
    case BSReady{}:
      match b:
        case BSDraft{}:
          False{}
        case BSBlockedByDep{}:
          False{}
        case BSReady{}:
          True{}
        case BSInProgress{}:
          False{}
        case BSInQa{}:
          False{}
        case BSFailed{}:
          False{}
        case BSVerified{}:
          False{}
        case BSStale{}:
          False{}
    case BSInProgress{}:
      match b:
        case BSDraft{}:
          False{}
        case BSBlockedByDep{}:
          False{}
        case BSReady{}:
          False{}
        case BSInProgress{}:
          True{}
        case BSInQa{}:
          False{}
        case BSFailed{}:
          False{}
        case BSVerified{}:
          False{}
        case BSStale{}:
          False{}
    case BSInQa{}:
      match b:
        case BSDraft{}:
          False{}
        case BSBlockedByDep{}:
          False{}
        case BSReady{}:
          False{}
        case BSInProgress{}:
          False{}
        case BSInQa{}:
          True{}
        case BSFailed{}:
          False{}
        case BSVerified{}:
          False{}
        case BSStale{}:
          False{}
    case BSFailed{}:
      match b:
        case BSDraft{}:
          False{}
        case BSBlockedByDep{}:
          False{}
        case BSReady{}:
          False{}
        case BSInProgress{}:
          False{}
        case BSInQa{}:
          False{}
        case BSFailed{}:
          True{}
        case BSVerified{}:
          False{}
        case BSStale{}:
          False{}
    case BSVerified{}:
      match b:
        case BSDraft{}:
          False{}
        case BSBlockedByDep{}:
          False{}
        case BSReady{}:
          False{}
        case BSInProgress{}:
          False{}
        case BSInQa{}:
          False{}
        case BSFailed{}:
          False{}
        case BSVerified{}:
          True{}
        case BSStale{}:
          False{}
    case BSStale{}:
      match b:
        case BSDraft{}:
          False{}
        case BSBlockedByDep{}:
          False{}
        case BSReady{}:
          False{}
        case BSInProgress{}:
          False{}
        case BSInQa{}:
          False{}
        case BSFailed{}:
          False{}
        case BSVerified{}:
          False{}
        case BSStale{}:
          True{}

def is_bs_stale(st: BuildStatus) -> Bool:
  match st:
    case BSStale{}:
      True{}
    case BSDraft{}:
      False{}
    case BSBlockedByDep{}:
      False{}
    case BSReady{}:
      False{}
    case BSInProgress{}:
      False{}
    case BSInQa{}:
      False{}
    case BSFailed{}:
      False{}
    case BSVerified{}:
      False{}

def is_bs_ready(st: BuildStatus) -> Bool:
  match st:
    case BSReady{}:
      True{}
    case BSDraft{}:
      False{}
    case BSBlockedByDep{}:
      False{}
    case BSInProgress{}:
      False{}
    case BSInQa{}:
      False{}
    case BSFailed{}:
      False{}
    case BSVerified{}:
      False{}
    case BSStale{}:
      False{}

def step_component_lifecycle_on_edit(cur_status: BuildStatus, +is_spec_mutated: Bool) -> BuildStatus:
  match cur_status:
    case BSVerified{}:
      match is_spec_mutated:
        case True{}:
          BSStale{}
        case False{}:
          BSVerified{}
    case BSInQa{}:
      match is_spec_mutated:
        case True{}:
          BSStale{}
        case False{}:
          BSInQa{}
    case BSDraft{}:
      BSDraft{}
    case BSBlockedByDep{}:
      BSBlockedByDep{}
    case BSReady{}:
      BSReady{}
    case BSInProgress{}:
      BSInProgress{}
    case BSFailed{}:
      BSFailed{}
    case BSStale{}:
      BSStale{}

def resolve_draft_ready(is_proven: Bool, deps_ok: Bool) -> BuildStatus:
  match is_proven:
    case False{}:
      BSDraft{}
    case True{}:
      match deps_ok:
        case False{}:
          BSBlockedByDep{}
        case True{}:
          BSReady{}

def resolve_packet_lifecycle_status(cur_status: BuildStatus, +is_proven: Bool, +deps_ok: Bool) -> BuildStatus:
  match cur_status:
    case BSDraft{}:
      resolve_draft_ready(is_proven, deps_ok)
    case BSBlockedByDep{}:
      resolve_draft_ready(is_proven, deps_ok)
    case BSReady{}:
      resolve_draft_ready(is_proven, deps_ok)
    case BSInProgress{}:
      BSInProgress{}
    case BSInQa{}:
      BSInQa{}
    case BSFailed{}:
      BSFailed{}
    case BSVerified{}:
      BSVerified{}
    case BSStale{}:
      BSStale{}

def main() -> Nat:
  add_nat(left_half(7n), right_half(7n))
