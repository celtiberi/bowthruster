# Site Y — Engineer 3 Formal Proofs (`PROOF.bend`) for Spec 22B (`whiteboard` Frustration & Hierarchy) Laws 1-6

import Base
import ./main.bend as WBB
import ./LAWS.bend as Laws

# Helper lemma: deontic opposition is symmetric
def opposing_sym(pa: WBB.Pol, pb: WBB.Pol) -> {WBB.is_deontic_opposing(pa, pb) == WBB.is_deontic_opposing(pb, pa) : Bool}:
  match pa pb:
    case WBB.Pos{} WBB.Pos{}:
      {==}
    case WBB.Pos{} WBB.Neg{}:
      {==}
    case WBB.Pos{} WBB.Neut{}:
      {==}
    case WBB.Neg{} WBB.Pos{}:
      {==}
    case WBB.Neg{} WBB.Neg{}:
      {==}
    case WBB.Neg{} WBB.Neut{}:
      {==}
    case WBB.Neut{} WBB.Pos{}:
      {==}
    case WBB.Neut{} WBB.Neg{}:
      {==}
    case WBB.Neut{} WBB.Neut{}:
      {==}

# Helper lemma: clash_core is symmetric in `(pa, op_a)` and `(pb, op_b)`
def clash_core_sym(
  pa: WBB.Pol,
  op_a: WBB.EqOp,
  pb: WBB.Pol,
  op_b: WBB.EqOp,
  vr: WBB.VRelNorm,
) -> {WBB.clash_core(pa, op_a, pb, op_b, vr) == WBB.clash_core(pb, op_b, pa, op_a, vr) : Bool}:
  match op_a op_b vr:
    case WBB.OpNeq{} WBB.OpNeq{} vr:
      {==}
    case WBB.OpEq{} WBB.OpEq{} WBB.VRDiff{}:
      {==}
    case WBB.OpEq{} WBB.OpNeq{} WBB.VRDiff{}:
      {==}
    case WBB.OpEq{} WBB.OpNone{} WBB.VRDiff{}:
      {==}
    case WBB.OpNeq{} WBB.OpEq{} WBB.VRDiff{}:
      {==}
    case WBB.OpNeq{} WBB.OpNone{} WBB.VRDiff{}:
      {==}
    case WBB.OpNone{} WBB.OpEq{} WBB.VRDiff{}:
      {==}
    case WBB.OpNone{} WBB.OpNeq{} WBB.VRDiff{}:
      {==}
    case WBB.OpNone{} WBB.OpNone{} WBB.VRDiff{}:
      {==}
    case WBB.OpEq{} WBB.OpNeq{} WBB.VRSame{}:
      {==}
    case WBB.OpNeq{} WBB.OpEq{} WBB.VRSame{}:
      {==}
    case WBB.OpEq{} WBB.OpEq{} WBB.VRSame{}:
      opposing_sym(pa, pb)
    case WBB.OpEq{} WBB.OpNone{} WBB.VRSame{}:
      opposing_sym(pa, pb)
    case WBB.OpNone{} WBB.OpEq{} WBB.VRSame{}:
      opposing_sym(pa, pb)
    case WBB.OpNeq{} WBB.OpNone{} WBB.VRSame{}:
      opposing_sym(pa, pb)
    case WBB.OpNone{} WBB.OpNeq{} WBB.VRSame{}:
      opposing_sym(pa, pb)
    case WBB.OpNone{} WBB.OpNone{} WBB.VRSame{}:
      opposing_sym(pa, pb)
    case WBB.OpEq{} WBB.OpEq{} WBB.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case WBB.OpEq{} WBB.OpNeq{} WBB.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case WBB.OpNeq{} WBB.OpEq{} WBB.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case WBB.OpEq{} WBB.OpNone{} WBB.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case WBB.OpNone{} WBB.OpEq{} WBB.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case WBB.OpNeq{} WBB.OpNone{} WBB.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case WBB.OpNone{} WBB.OpNeq{} WBB.VRUnconstrained{}:
      opposing_sym(pa, pb)
    case WBB.OpNone{} WBB.OpNone{} WBB.VRUnconstrained{}:
      opposing_sym(pa, pb)

# Helper lemma: WBB.bool_eq is reflexive
def bool_eq_refl(b: Bool) -> {WBB.bool_eq(b, b) == True{} : Bool}:
  match b:
    case True{}:
      {==}
    case False{}:
      {==}

# Proof of LAW 1: Rule-clash symmetry across all 1,620 `(ta, op_a, tb, op_b, vrel)` states
def Laws.rules_clash_symmetric(ta, op_a, tb, op_b, vrel):
  clash_core_sym(WBB.rule_pol(ta), op_a, WBB.rule_pol(tb), op_b, WBB.norm_vrel(vrel))

# Proof of LAW 2: Numeric-symbolic equality parity (`VSameNum{}` == `VSameStr{}`)
def Laws.rules_clash_same_num_str_parity(ta, op_a, tb, op_b):
  {==}

# Proof of LAW 3: Numeric-symbolic inequality parity (`VDiffNum{}` == `VDiffStr{}`)
def Laws.rules_clash_diff_num_str_parity(ta, op_a, tb, op_b):
  {==}

# Proof of LAW 4: Deontic polarity clash on equal numeric values
def Laws.prohibition_clashes_with_positive_on_same_num(tb):
  match tb:
    case WBB.RTProhib{}:
      {==}
    case WBB.RTOblig{}:
      {==}
    case WBB.RTPolicy{}:
      {==}
    case WBB.RTAction{}:
      {==}
    case WBB.RTBound{}:
      {==}
    case WBB.RTOther{}:
      {==}

# Proof of LAW 5: Mutual inequality (`OpNeq{}` vs `OpNeq{}`) never clashes
def Laws.mutual_neq_never_clashes(ta, tb, vrel):
  {==}

# Proof of LAW 6: Lex specialis shields inherited parent-scope clash
def Laws.lex_specialis_shields_clash(ta, op_a, tb, op_b, vrel):
  bool_eq_refl(WBB.rules_clash(ta, op_a, tb, op_b, vrel))

# Proof of LAW 7: Structural dependency or signal-flow prohibition clash
def Laws.prohibited_dependency_or_flow_clashes(has_prohib, is_upward, is_cycle):
  bool_eq_refl(Bool.or(has_prohib, Bool.or(is_upward, is_cycle)))

# Proof of LAW 8: Signal-flow interface payload compatibility
def Laws.flow_interface_payload_compatibility(src_decl, in_src, tgt_decl, in_tgt):
  {==}

# Proof of LAW 9: Continuous Lattice Proof Readiness (`classify_lattice_proof_status`)
def Laws.lattice_proof_proven_iff_complete_and_conflict_free(has_viol, has_comp, comp_ok, flow_ok):
  match has_viol has_comp comp_ok flow_ok:
    case True{} has_comp comp_ok flow_ok:
      {==}
    case False{} True{} True{} True{}:
      {==}
    case False{} True{} True{} False{}:
      {==}
    case False{} True{} False{} flow_ok:
      {==}
    case False{} False{} comp_ok flow_ok:
      {==}
