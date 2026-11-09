# Site Y — Canonical Spec 20 (Context Graph) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every build tag in `ModelCircuits/eng3` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as CG

# LAW 1: Double negation is an involution for every polarity `p`.
law inv_involutive:
  for +p: CG.Pol
  {CG.inv_pol(CG.inv_pol(p)) == p : CG.Pol}

# LAW 2: Positive polarity (`Pos{}`) is the right identity of edge composition.
law comp_pos_right:
  for +p: CG.Pol
  {CG.comp_pol(p, CG.Pos{}) == p : CG.Pol}

# LAW 3: Polarity composition is associative across any 3 hops `(a, b, c)`.
law comp_assoc:
  for a: CG.Pol
  for b: CG.Pol
  for +c: CG.Pol
  {CG.comp_pol(CG.comp_pol(a, b), c) == CG.comp_pol(a, CG.comp_pol(b, c)) : CG.Pol}

# LAW 4: Path composition homomorphism — propagating across concatenated paths
# `cat(xs, ys)` equals propagating across `xs` and then `ys`, for ANY path lengths.
law path_append:
  for xs: List<&2, CG.Pol>
  for ys: List<&2, CG.Pol>
  for p: CG.Pol
  {CG.path_pol(CG.cat(xs, ys), p) == CG.path_pol(ys, CG.path_pol(xs, p)) : CG.Pol}

# LAW 5: A node with definite sign `b` agreeing with itself is never frustrated.
law self_never_frustrated:
  for +b: Bool
  {CG.is_frustrated(b, b) == False{} : Bool}

# LAW 6: A node with definite sign `b` meeting its negation `Bool.not(b)` is always frustrated.
law neg_always_frustrated:
  for +b: Bool
  {CG.is_frustrated(b, Bool.not(b)) == True{} : Bool}

# LAW 7: Zero activation (`0n`) remains at floor `0n` after any number of decay steps `n`.
law decay_zero_floor:
  for n: Nat
  {CG.decay(n, 0n) == 0n : Nat}

# LAW 8: Rule-clash symmetry — swapping rule A `(ta, op_a)` and rule B `(tb, op_b)`
# yields the exact same clash verdict across all 1,620 `(ta, op_a, tb, op_b, vrel)` states.
law rules_clash_symmetric:
  for +ta: CG.RuleType
  for +op_a: CG.EqOp
  for +tb: CG.RuleType
  for +op_b: CG.EqOp
  for +vrel: CG.ValRel
  {CG.rules_clash(ta, op_a, tb, op_b, vrel) == CG.rules_clash(tb, op_b, ta, op_a, vrel) : Bool}

# LAW 9: Numeric-symbolic equality parity — for equality/inequality operators (`OpEq{}`,
# `OpNeq{}`, `OpNone{}`), equal numeric values (`VSameNum{}`) and equal symbolic strings
# (`VSameStr{}`) produce the exact same clash verdict for every rule pair `(ta, op_a, tb, op_b)`.
law rules_clash_same_num_str_parity:
  for +ta: CG.RuleType
  for +op_a: CG.EqOp
  for +tb: CG.RuleType
  for +op_b: CG.EqOp
  {CG.rules_clash(ta, op_a, tb, op_b, CG.VSameNum{}) == CG.rules_clash(ta, op_a, tb, op_b, CG.VSameStr{}) : Bool}

# LAW 10: Numeric-symbolic inequality parity — for equality/inequality operators (`OpEq{}`,
# `OpNeq{}`, `OpNone{}`), distinct numeric values (`VDiffNum{}`) and distinct symbolic strings
# (`VDiffStr{}`) produce the exact same clash verdict for every rule pair `(ta, op_a, tb, op_b)`.
law rules_clash_diff_num_str_parity:
  for +ta: CG.RuleType
  for +op_a: CG.EqOp
  for +tb: CG.RuleType
  for +op_b: CG.EqOp
  {CG.rules_clash(ta, op_a, tb, op_b, CG.VDiffNum{}) == CG.rules_clash(ta, op_a, tb, op_b, CG.VDiffStr{}) : Bool}

# LAW 11: Deontic polarity clash on equal numeric values — a `PROHIBITION` (`RTProhib{}`)
# with `OpEq{}` clashes (`True{}`) with `tb` (`OpEq{}`) on `VSameNum{}` iff `tb` has positive
# deontic polarity (`RTOblig{}`, `RTPolicy{}`, `RTAction{}`, `RTBound{}`).
law prohibition_clashes_with_positive_on_same_num:
  for +tb: CG.RuleType
  {CG.rules_clash(CG.RTProhib{}, CG.OpEq{}, tb, CG.OpEq{}, CG.VSameNum{}) == CG.is_pos_pol(CG.rule_pol(tb)) : Bool}

# LAW 12: Mutual inequality (`OpNeq{}` vs `OpNeq{}`) is always satisfiable and never clashes
# regardless of rule deontic types `(ta, tb)` or value relation `vrel`.
law mutual_neq_never_clashes:
  for +ta: CG.RuleType
  for +tb: CG.RuleType
  for +vrel: CG.ValRel
  {CG.rules_clash(ta, CG.OpNeq{}, tb, CG.OpNeq{}, vrel) == False{} : Bool}
