# Site Y — Engineer 3 Formal Proofs (`PROOF.bend`) for Spec 21 (`graph_core`) Laws 1-9

import Base
import ./main.bend as GC
import ./LAWS.bend as Laws

# Proof of LAW 1: Double negation involution
def Laws.inv_involutive(p):
  match p:
    case GC.Pos{}:
      {==}
    case GC.Neg{}:
      {==}
    case GC.Neut{}:
      {==}

# Proof of LAW 2: Right identity of Pos{}
def Laws.comp_pos_right(p):
  match p:
    case GC.Pos{}:
      {==}
    case GC.Neg{}:
      {==}
    case GC.Neut{}:
      {==}

# Helper lemma: inv_pol distributes over comp_pol on the right
def inv_comp(b: GC.Pol, c: GC.Pol) -> {GC.comp_pol(GC.inv_pol(b), c) == GC.inv_pol(GC.comp_pol(b, c)) : GC.Pol}:
  match b c:
    case GC.Pos{} c:
      {==}
    case GC.Neg{} GC.Pos{}:
      {==}
    case GC.Neg{} GC.Neg{}:
      {==}
    case GC.Neg{} GC.Neut{}:
      {==}
    case GC.Neut{} c:
      {==}

# Proof of LAW 3: Associativity of polarity composition
def Laws.comp_assoc(a, b, c):
  match a:
    case GC.Pos{}:
      {==}
    case GC.Neg{}:
      inv_comp(b, c)
    case GC.Neut{}:
      {==}

# Proof of LAW 4: Structural induction over path `xs`
def Laws.path_append(xs, ys, p):
  match xs:
    case Nil{}:
      {==}
    case Con{h, t}:
      Laws.path_append(t, ys, GC.comp_pol(p, h))

# Proof of LAW 5: Self-agreement is never frustrated
def Laws.self_never_frustrated(b):
  match b:
    case True{}:
      {==}
    case False{}:
      {==}

# Proof of LAW 6: Opposing definite signs are always frustrated
def Laws.neg_always_frustrated(b):
  match b:
    case True{}:
      {==}
    case False{}:
      {==}

# Proof of LAW 7: Structural induction over decay steps `n` at floor `0n`
def Laws.decay_zero_floor(n):
  match n:
    case 0n:
      {==}
    case 1n+rest:
      Laws.decay_zero_floor(rest)

# Proof of LAW 8: Two-pass LOD 1 before LOD 2 budget admission invariance
def Laws.lod1_never_starved_by_lod2(cands, lod2_a, lod2_b, cap):
  {==}

# Proof of LAW 9: Topological temperature barrier monotonicity
def Laws.temperature_barrier_monotonic(sb, has_cross, has_task):
  match sb has_cross has_task:
    case GC.SBZero{} False{} False{}:
      {==}
    case GC.SBZero{} False{} True{}:
      {==}
    case GC.SBZero{} True{} False{}:
      {==}
    case GC.SBZero{} True{} True{}:
      {==}
    case GC.SBOne{} False{} False{}:
      {==}
    case GC.SBOne{} False{} True{}:
      {==}
    case GC.SBOne{} True{} False{}:
      {==}
    case GC.SBOne{} True{} True{}:
      {==}
    case GC.SBTwo{} False{} False{}:
      {==}
    case GC.SBTwo{} False{} True{}:
      {==}
    case GC.SBTwo{} True{} False{}:
      {==}
    case GC.SBTwo{} True{} True{}:
      {==}
    case GC.SBMany{} False{} False{}:
      {==}
    case GC.SBMany{} False{} True{}:
      {==}
    case GC.SBMany{} True{} False{}:
      {==}
    case GC.SBMany{} True{} True{}:
      {==}

# Proof of LAW 10: nD Lattice hyperplane slice admission
def Laws.nd_coordinate_slice_exact(has_dim_overlap):
  match has_dim_overlap:
    case True{}:
      {==}
    case False{}:
      {==}

# Proof of LAW 11: Directed signal-flow edge polarity preservation
def Laws.flow_edge_preserves_polarity(p):
  match p:
    case GC.Pos{}:
      {==}
    case GC.Neg{}:
      {==}
    case GC.Neut{}:
      {==}
