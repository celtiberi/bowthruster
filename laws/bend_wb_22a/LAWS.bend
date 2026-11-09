# Site Y — Canonical Spec 22A (`whiteboard` Schema, SQLite Persistence & Write Mutations) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `whiteboard_22a` build tag in `ModelCircuits/eng1` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as WBA

# LAW 1: Open blocker resolution exactness — `step_blocker_resolution(BlockerOpen{}, id_matches)`
# transitions to `BlockerResolved{}` iff `id_matches == True{}`.
law blocker_resolve_iff_id_matches:
  for +id_matches: Bool
  {WBA.is_resolved(WBA.step_blocker_resolution(WBA.BlockerOpen{}, id_matches)) == id_matches : Bool}

# LAW 2: Already-resolved blocker idempotence — once a blocker is `BlockerResolved{}`,
# `step_blocker_resolution(BlockerResolved{}, id_matches)` remains `BlockerResolved{}`
# regardless of subsequent resolutions (`id_matches`).
law blocker_resolved_idempotent:
  for +id_matches: Bool
  {WBA.is_resolved(WBA.step_blocker_resolution(WBA.BlockerResolved{}, id_matches)) == True{} : Bool}

# LAW 3: Mitosis rule conservation — splitting `n` rules into `(left_half(n), right_half(n))`
# preserves the exact total rule count `add(left_half(n), right_half(n)) == n` for all `n: Nat`.
law mitosis_conserves_rule_count:
  for n: Nat
  {WBA.add_nat(WBA.left_half(n), WBA.right_half(n)) == n : Nat}

# LAW 4: Lattice kind storage partition — `LKFlow{}` routes to `StoreFlows{}` (`is_flow_store == True{}`),
# `LKComponent{}` routes to `StoreScopes{}`, `LKSchema{}`/`LKInterface{}`/`LKLaw{}` route to `StoreArtifacts{}`,
# and none of those SBF kinds ever route to `StoreRules{}` (`is_rule_store == False{}`).
law lattice_sbf_never_enters_rule_store:
  {Bool.and(
    WBA.is_flow_store(WBA.classify_lattice_store(WBA.LKFlow{})),
    Bool.and(
      Bool.not(WBA.is_rule_store(WBA.classify_lattice_store(WBA.LKFlow{}))),
      Bool.and(
        Bool.not(WBA.is_rule_store(WBA.classify_lattice_store(WBA.LKComponent{}))),
        Bool.and(
          Bool.not(WBA.is_rule_store(WBA.classify_lattice_store(WBA.LKSchema{}))),
          Bool.and(
            Bool.not(WBA.is_rule_store(WBA.classify_lattice_store(WBA.LKInterface{}))),
            Bool.not(WBA.is_rule_store(WBA.classify_lattice_store(WBA.LKLaw{})))
          )
        )
      )
    )
  ) == True{} : Bool}

# LAW 5: Ghost-scope exclusion law — `should_register_as_scope(is_primary_scope, is_flow_endpoint, is_explicit_component, is_bound_or_payload_val)`
# is `False{}` whenever a token is merely a property/bound/payload value (`is_bound_or_payload_val == True{}`)
# and not a primary scope, flow endpoint, or explicit component (`False{}, False{}, False{}, True{}` -> `False{}`),
# while always `True{}` for any primary scope, flow endpoint, or explicit component.
law ghost_scope_exclusion_law:
  for +is_val: Bool
  {Bool.and(
    Bool.not(WBA.should_register_as_scope(False{}, False{}, False{}, is_val)),
    Bool.and(
      WBA.should_register_as_scope(True{}, False{}, False{}, is_val),
      Bool.and(
        WBA.should_register_as_scope(False{}, True{}, False{}, is_val),
        WBA.should_register_as_scope(False{}, False{}, True{}, is_val)
      )
    )
  ) == True{} : Bool}

# LAW 6: Automatic staleness invalidation on spec edit —
# `step_component_lifecycle_on_edit(cur_status, is_spec_mutated)`:
# (1) When `cur_status == BSVerified{}` and `is_spec_mutated == True{}`, transitions to `BSStale{}` (`is_bs_stale == True{}`);
# (2) When `is_spec_mutated == False{}`, preserves `cur_status` unchanged for every `BuildStatus`.
law verified_component_becomes_stale_on_spec_edit:
  for +st: WBA.BuildStatus
  {Bool.and(
    WBA.is_bs_stale(WBA.step_component_lifecycle_on_edit(WBA.BSVerified{}, True{})),
    WBA.bs_eq(WBA.step_component_lifecycle_on_edit(st, False{}), st)
  ) == True{} : Bool}

# LAW 7: Dependency-gated packet readiness (`resolve_packet_lifecycle_status`) —
# For a draft/unstarted component (`BSDraft{}`), `resolve_packet_lifecycle_status(BSDraft{}, is_proven, deps_verified)`
# transitions to `BSReady{}` (`is_bs_ready == True{}`) iff `is_proven == True{}` AND `deps_verified == True{}`.
law resolve_packet_lifecycle_status_law:
  for +is_proven: Bool
  for +deps_ok: Bool
  {WBA.is_bs_ready(WBA.resolve_packet_lifecycle_status(WBA.BSDraft{}, is_proven, deps_ok)) ==
   Bool.and(is_proven, deps_ok) : Bool}
