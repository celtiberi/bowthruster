# Site Y — Engineer 3 Formal Proofs (`PROOF.bend`) for Spec 20 Context Graph Laws

import Base
import ./main.bend as CG
import ./LAWS.bend as Laws

# Proof of LAW 1: Double negation involution
def Laws.inv_involutive(p):
  match p:
    case CG.Pos{}:
      {==}
    case CG.Neg{}:
      {==}
    case CG.Neut{}:
      {==}

# Proof of LAW 2: Right identity of Pos{}
def Laws.comp_pos_right(p):
  match p:
    case CG.Pos{}:
      {==}
    case CG.Neg{}:
      {==}
    case CG.Neut{}:
      {==}

# Helper lemma: inv_pol distributes over comp_pol on the right
def inv_comp(b: CG.Pol, c: CG.Pol) -> {CG.comp_pol(CG.inv_pol(b), c) == CG.inv_pol(CG.comp_pol(b, c)) : CG.Pol}:
  match b c:
    case CG.Pos{} c:
      {==}
    case CG.Neg{} CG.Pos{}:
      {==}
    case CG.Neg{} CG.Neg{}:
      {==}
    case CG.Neg{} CG.Neut{}:
      {==}
    case CG.Neut{} c:
      {==}

# Proof of LAW 3: Associativity of polarity composition
def Laws.comp_assoc(a, b, c):
  match a:
    case CG.Pos{}:
      {==}
    case CG.Neg{}:
      inv_comp(b, c)
    case CG.Neut{}:
      {==}

# Proof of LAW 4: Structural induction over path `xs`
def Laws.path_append(xs, ys, p):
  match xs:
    case Nil{}:
      {==}
    case Con{h, t}:
      Laws.path_append(t, ys, CG.comp_pol(p, h))

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

# Helper lemma: deontic opposition is symmetric
def opposing_sym(pa: CG.Pol, pb: CG.Pol) -> {CG.is_deontic_opposing(pa, pb) == CG.is_deontic_opposing(pb, pa) : Bool}:
  match pa pb:
    case CG.Pos{} CG.Pos{}:
      {==}
    case CG.Pos{} CG.Neg{}:
      {==}
    case CG.Pos{} CG.Neut{}:
      {==}
    case CG.Neg{} CG.Pos{}:
      {==}
    case CG.Neg{} CG.Neg{}:
      {==}
    case CG.Neg{} CG.Neut{}:
      {==}
    case CG.Neut{} CG.Pos{}:
      {==}
    case CG.Neut{} CG.Neg{}:
      {==}
    case CG.Neut{} CG.Neut{}:
      {==}

# Helper lemma: clash_core is symmetric in `(pa, op_a)` and `(pb, op_b)`
def clash_core_sym(pa: CG.Pol, op_a: CG.EqOp, pb: CG.Pol, op_b: CG.EqOp, vr: CG.VRelNorm) -> {CG.clash_core(pa, op_a, pb, op_b, vr) == CG.clash_core(pb, op_b, pa, op_a, vr) : Bool}:
  match op_a op_b vr:
    case CG.OpNeq{} CG.OpNeq{} vr:
      {==}
    case CG.OpEq{} CG.OpEq{} CG.VRDiff{}:
      {==}
    case CG.OpEq{} CG.OpNeq{} CG.VRDiff{}:
      {==}
    case CG.OpEq{} CG.OpNone{} CG.VRDiff{}:
      {==}
    case CG.OpNeq{} CG.OpEq{} CG.VRDiff{}:
      {==}
    case CG.OpNeq{} CG.OpNone{} CG.VRDiff{}:
      {==}
    case CG.OpNone{} CG.OpEq{} CG.VRDiff{}:
      {==}
    case CG.OpNone{} CG.OpNeq{} CG.VRDiff{}:
      {==}
    case CG.OpNone{} CG.OpNone{} CG.VRDiff{}:
      {==}
    case CG.OpEq{} CG.OpNeq{} CG.VRSame{}:
      {==}
    case CG.OpNeq{} CG.OpEq{} CG.VRSame{}:
      {==}
    case CG.OpEq{} CG.OpEq{} CG.VRSame{}:
      opposing_sym(pa, pb)
    case CG.OpEq{} CG.OpNone{} CG.VRSame{}:
      opposing_sym(pa, pb)
    case CG.OpNone{} CG.OpEq{} CG.VRSame{}:
      opposing_sym(pa, pb)
    case CG.OpNeq{} CG.OpNone{} CG.VRSame{}:
      opposing_sym(pa, pb)
    case CG.OpNone{} CG.OpNeq{} CG.VRSame{}:
      opposing_sym(pa, pb)
    case CG.OpNone{} CG.OpNone{} CG.VRSame{}:
      opposing_sym(pa, pb)
    case CG.OpEq{} CG.OpEq{} CG.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case CG.OpEq{} CG.OpNeq{} CG.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case CG.OpNeq{} CG.OpEq{} CG.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case CG.OpEq{} CG.OpNone{} CG.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case CG.OpNone{} CG.OpEq{} CG.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case CG.OpNeq{} CG.OpNone{} CG.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case CG.OpNone{} CG.OpNeq{} CG.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case CG.OpNone{} CG.OpNone{} CG.VRUnconstrained{}:
      opposing_sym(pa, pb)

# Proof of LAW 8: Rule-clash symmetry across all 1,620 `(ta, op_a, tb, op_b, vrel)` states
def Laws.rules_clash_symmetric(ta, op_a, tb, op_b, vrel):
  clash_core_sym(CG.rule_pol(ta), op_a, CG.rule_pol(tb), op_b, CG.norm_vrel(vrel))

# Proof of LAW 9: Numeric-symbolic equality parity (`VSameNum{}` == `VSameStr{}`)
def Laws.rules_clash_same_num_str_parity(ta, op_a, tb, op_b):
  {==}

# Proof of LAW 10: Numeric-symbolic inequality parity (`VDiffNum{}` == `VDiffStr{}`)
def Laws.rules_clash_diff_num_str_parity(ta, op_a, tb, op_b):
  {==}

# Proof of LAW 11: Deontic polarity clash on equal numeric values
def Laws.prohibition_clashes_with_positive_on_same_num(tb):
  match tb:
    case CG.RTProhib{}:
      {==}
    case CG.RTOblig{}:
      {==}
    case CG.RTPolicy{}:
      {==}
    case CG.RTAction{}:
      {==}
    case CG.RTBound{}:
      {==}
    case CG.RTOther{}:
      {==}

# Proof of LAW 12: Mutual inequality (`OpNeq{}` vs `OpNeq{}`) never clashes
def Laws.mutual_neq_never_clashes(ta, tb, vrel):
  {==}
