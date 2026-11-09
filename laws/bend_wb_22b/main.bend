# Site Y — Spec 22B Whiteboard Spin-Glass Conflict & Parent-Hierarchy Kernel (`main.bend`)
#
# Formalizes rule deontic polarity (`RuleType`, `Pol`), equality/inequality operators (`EqOp`),
# value relations (`ValRel`, `VRelNorm`), intra-scope and inherited rule clash (`rules_clash`,
# `inherited_rules_clash`), and lex specialis exception shielding (Laws 1-6).

import Base

type Pol is Data:
  Pos{}
  Neg{}
  Neut{}

type RuleType is Data:
  RTProhib{}
  RTOblig{}
  RTPolicy{}
  RTAction{}
  RTBound{}
  RTOther{}

type EqOp is Data:
  OpEq{}
  OpNeq{}
  OpNone{}

type ValRel is Data:
  VSameNum{}
  VDiffNum{}
  VSameStr{}
  VDiffStr{}
  VUnconstrained{}

type VRelNorm is Data:
  VRSame{}
  VRDiff{}
  VRUnconstrained{}

def rule_pol(t: RuleType) -> Pol:
  match t:
    case RTProhib{}:
      Neg{}
    case RTOblig{}:
      Pos{}
    case RTPolicy{}:
      Pos{}
    case RTAction{}:
      Pos{}
    case RTBound{}:
      Pos{}
    case RTOther{}:
      Neut{}

def is_pos_pol(p: Pol) -> Bool:
  match p:
    case Pos{}:
      True{}
    case Neg{}:
      False{}
    case Neut{}:
      False{}

def bool_eq(a: Bool, b: Bool) -> Bool:
  match a b:
    case True{} True{}:
      True{}
    case False{} False{}:
      True{}
    case True{} False{}:
      False{}
    case False{} True{}:
      False{}

def norm_vrel(vrel: ValRel) -> VRelNorm:
  match vrel:
    case VSameNum{}:
      VRSame{}
    case VDiffNum{}:
      VRDiff{}
    case VSameStr{}:
      VRSame{}
    case VDiffStr{}:
      VRDiff{}
    case VUnconstrained{}:
      VRUnconstrained{}

def is_deontic_opposing(pa: Pol, pb: Pol) -> Bool:
  match pa pb:
    case Neg{} Pos{}:
      True{}
    case Pos{} Neg{}:
      True{}
    case Pos{} Pos{}:
      False{}
    case Pos{} Neut{}:
      False{}
    case Neg{} Neg{}:
      False{}
    case Neg{} Neut{}:
      False{}
    case Neut{} Pos{}:
      False{}
    case Neut{} Neg{}:
      False{}
    case Neut{} Neut{}:
      False{}

def clash_core(pa: Pol, op_a: EqOp, pb: Pol, op_b: EqOp, vr: VRelNorm) -> Bool:
  match op_a op_b vr:
    case OpNeq{} OpNeq{} vr:
      False{}
    case OpEq{} OpEq{} VRDiff{}:
      True{}
    case OpEq{} OpNeq{} VRDiff{}:
      False{}
    case OpEq{} OpNone{} VRDiff{}:
      False{}
    case OpNeq{} OpEq{} VRDiff{}:
      False{}
    case OpNeq{} OpNone{} VRDiff{}:
      False{}
    case OpNone{} OpEq{} VRDiff{}:
      False{}
    case OpNone{} OpNeq{} VRDiff{}:
      False{}
    case OpNone{} OpNone{} VRDiff{}:
      False{}
    case OpEq{} OpNeq{} VRSame{}:
      True{}
    case OpNeq{} OpEq{} VRSame{}:
      True{}
    case OpEq{} OpEq{} VRSame{}:
      is_deontic_opposing(pa, pb)
    case OpEq{} OpNone{} VRSame{}:
      is_deontic_opposing(pa, pb)
    case OpNone{} OpEq{} VRSame{}:
      is_deontic_opposing(pa, pb)
    case OpNeq{} OpNone{} VRSame{}:
      is_deontic_opposing(pa, pb)
    case OpNone{} OpNeq{} VRSame{}:
      is_deontic_opposing(pa, pb)
    case OpNone{} OpNone{} VRSame{}:
      is_deontic_opposing(pa, pb)
    case OpEq{} OpEq{} VRUnconstrained{}:
      is_deontic_opposing(pa, pb)
    case OpEq{} OpNeq{} VRUnconstrained{}:
      is_deontic_opposing(pa, pb)
    case OpNeq{} OpEq{} VRUnconstrained{}:
      is_deontic_opposing(pa, pb)
    case OpEq{} OpNone{} VRUnconstrained{}:
      is_deontic_opposing(pa, pb)
    case OpNone{} OpEq{} VRUnconstrained{}:
      is_deontic_opposing(pa, pb)
    case OpNeq{} OpNone{} VRUnconstrained{}:
      is_deontic_opposing(pa, pb)
    case OpNone{} OpNeq{} VRUnconstrained{}:
      is_deontic_opposing(pa, pb)
    case OpNone{} OpNone{} VRUnconstrained{}:
      is_deontic_opposing(pa, pb)

def rules_clash(ta: RuleType, op_a: EqOp, tb: RuleType, op_b: EqOp, vrel: ValRel) -> Bool:
  clash_core(rule_pol(ta), op_a, rule_pol(tb), op_b, norm_vrel(vrel))

def inherited_rules_clash(
  ta: RuleType,
  op_a: EqOp,
  tb: RuleType,
  op_b: EqOp,
  vrel: ValRel,
  is_exception: Bool,
) -> Bool:
  match is_exception:
    case True{}:
      False{}
    case False{}:
      rules_clash(ta, op_a, tb, op_b, vrel)

def dependency_or_flow_clashes(
  has_prohib: Bool,
  is_upward: Bool,
  is_cycle: Bool,
  is_exception: Bool,
) -> Bool:
  match is_exception:
    case True{}:
      False{}
    case False{}:
      Bool.or(has_prohib, Bool.or(is_upward, is_cycle))

def flow_payload_clashes(
  src_decl: Bool,
  in_src: Bool,
  tgt_decl: Bool,
  in_tgt: Bool,
) -> Bool:
  Bool.or(
    Bool.and(src_decl, Bool.not(in_src)),
    Bool.and(tgt_decl, Bool.not(in_tgt))
  )

type LPStatus is Data:
  LPBroken{}
  LPProven{}
  LPDraft{}

def is_lp_broken(s: LPStatus) -> Bool:
  match s:
    case LPBroken{}:
      True{}
    case LPProven{}:
      False{}
    case LPDraft{}:
      False{}

def is_lp_proven(s: LPStatus) -> Bool:
  match s:
    case LPBroken{}:
      False{}
    case LPProven{}:
      True{}
    case LPDraft{}:
      False{}

def classify_lattice_proof_status(
  has_viol: Bool,
  has_comp: Bool,
  comp_ok: Bool,
  flow_ok: Bool,
) -> LPStatus:
  match has_viol has_comp comp_ok flow_ok:
    case True{} has_comp comp_ok flow_ok:
      LPBroken{}
    case False{} True{} True{} True{}:
      LPProven{}
    case False{} True{} True{} False{}:
      LPDraft{}
    case False{} True{} False{} flow_ok:
      LPDraft{}
    case False{} False{} comp_ok flow_ok:
      LPDraft{}

def main() -> Bool:
  inherited_rules_clash(RTProhib{}, OpEq{}, RTOblig{}, OpEq{}, VSameNum{}, False{})
