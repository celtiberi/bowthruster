# Site Y — Canonical Spec 21 (`graph_core` Shared Graph & SQLite Engine) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `graph_core` build tag in `ModelCircuits/eng3` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as GC

# LAW 1: Double negation is an involution for every polarity `p`.
law inv_involutive:
  for +p: GC.Pol
  {GC.inv_pol(GC.inv_pol(p)) == p : GC.Pol}

# LAW 2: Positive polarity (`Pos{}`) is the right identity of edge composition.
law comp_pos_right:
  for +p: GC.Pol
  {GC.comp_pol(p, GC.Pos{}) == p : GC.Pol}

# LAW 3: Polarity composition is associative across any 3 hops `(a, b, c)`.
law comp_assoc:
  for a: GC.Pol
  for b: GC.Pol
  for +c: GC.Pol
  {GC.comp_pol(GC.comp_pol(a, b), c) == GC.comp_pol(a, GC.comp_pol(b, c)) : GC.Pol}

# LAW 4: Path composition homomorphism — propagating across concatenated paths
# `cat(xs, ys)` equals propagating across `xs` and then `ys`, for ANY path lengths.
law path_append:
  for xs: List<&2, GC.Pol>
  for ys: List<&2, GC.Pol>
  for p: GC.Pol
  {GC.path_pol(GC.cat(xs, ys), p) == GC.path_pol(ys, GC.path_pol(xs, p)) : GC.Pol}

# LAW 5: A node with definite sign `b` agreeing with itself is never frustrated.
law self_never_frustrated:
  for +b: Bool
  {GC.is_frustrated(b, b) == False{} : Bool}

# LAW 6: A node with definite sign `b` meeting its negation `Bool.not(b)` is always frustrated.
law neg_always_frustrated:
  for +b: Bool
  {GC.is_frustrated(b, Bool.not(b)) == True{} : Bool}

# LAW 7: Zero activation (`0n`) remains at floor `0n` after any number of decay steps `n`.
law decay_zero_floor:
  for n: Nat
  {GC.decay(n, 0n) == 0n : Nat}

# LAW 8: Two-pass LOD 1 before LOD 2 budget admission invariance — the number of
# admitted LOD 1 primary nodes (`admit_lod1_count(cands, lod2_req, cap)`) is completely
# invariant to how many LOD 2 metadata/external-link lines are requested (`lod2_a` vs `lod2_b`),
# proving that LOD 2 expansions never starve any LOD 1 primary node.
law lod1_never_starved_by_lod2:
  for cands: Nat
  for lod2_a: Nat
  for lod2_b: Nat
  for cap: Nat
  {GC.admit_lod1_count(cands, lod2_a, cap) == GC.admit_lod1_count(cands, lod2_b, cap) : Nat}

# LAW 9: Topological temperature barrier monotonicity — across all discrete seed topologies
# `(sb: SeedBucket, has_cross: Bool, has_task: Bool)`, `Warm{}` has the lowest activation
# barrier and `Cold{}` has the highest (`tau_min(Warm) <= tau_min(regime) <= tau_min(Cold)`).
law temperature_barrier_monotonic:
  for +sb: GC.SeedBucket
  for +has_cross: Bool
  for +has_task: Bool
  {Bool.and(
    GC.nat_lte(GC.tau_min_scaled(GC.Warm{}), GC.tau_min_scaled(GC.classify_temp_regime(sb, has_cross, has_task))),
    GC.nat_lte(GC.tau_min_scaled(GC.classify_temp_regime(sb, has_cross, has_task)), GC.tau_min_scaled(GC.Cold{}))
  ) == True{} : Bool}

# LAW 10: nD Lattice hyperplane slice admission — when `is_unconstrained == True{}`, every node is
# admitted regardless of dimension overlap (`has_dim_overlap`); when `is_unconstrained == False{}`,
# a node is admitted iff `has_dim_overlap == True{}`.
law nd_coordinate_slice_exact:
  for +has_dim_overlap: Bool
  {Bool.and(
    GC.matches_nd_slice(True{}, has_dim_overlap),
    GC.bool_eq(GC.matches_nd_slice(False{}, has_dim_overlap), has_dim_overlap)
  ) == True{} : Bool}

# LAW 11: Directed signal-flow edge polarity preservation — propagating any polarity `p`
# across a `FlowEdge` (`GC.flow_edge_pol()`) preserves `p` identically (`comp_pol(p, flow_edge_pol()) == p`).
law flow_edge_preserves_polarity:
  for +p: GC.Pol
  {GC.comp_pol(p, GC.flow_edge_pol()) == p : GC.Pol}
