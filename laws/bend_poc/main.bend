# Site Y — Context Graph & Phase 3 Polarity / Frustration Kernel (Bend 2 POC)
#
# Implements three-valued signed polarity propagation (`Pos{}`, `Neg{}`, `Neut{}`)
# over arbitrary multi-hop Context Graph paths (`List<&2, Pol>`), definite sign
# frustration detection (`is_frustrated`), and bounded decay (`decay`).

import Base

type Pol is Data:
  Pos{}
  Neg{}
  Neut{}

# Invert a polarity (`Pos <-> Neg`, `Neut -> Neut`).
def inv_pol(p: Pol) -> Pol:
  match p:
    case Pos{}:
      Neg{}
    case Neg{}:
      Pos{}
    case Neut{}:
      Neut{}

# Compose two polarities along a directed edge (`a -> b`).
def comp_pol(a: Pol, b: Pol) -> Pol:
  match a:
    case Pos{}:
      b
    case Neg{}:
      inv_pol(b)
    case Neut{}:
      Neut{}

# Concatenate two polarity edge lists (`xs ++ ys`).
def cat(xs: List<&2, Pol>, ys: List<&2, Pol>) -> List<&2, Pol>:
  match xs:
    case Nil{}:
      ys
    case Con{h, t}:
      Con{h, cat(t, ys)}

# Propagate initial polarity `p` along a multi-hop edge path `edges`.
def path_pol(edges: List<&2, Pol>, p: Pol) -> Pol:
  match edges:
    case Nil{}:
      p
    case Con{h, t}:
      path_pol(t, comp_pol(p, h))

# Definite-sign frustration check: returns True{} iff two definite signs (`True{}`=Pos, `False{}`=Neg) conflict.
def is_frustrated(a: Bool, b: Bool) -> Bool:
  match a b:
    case True{} True{}:
      False{}
    case False{} False{}:
      False{}
    case True{} False{}:
      True{}
    case False{} True{}:
      True{}

# Bounded natural decay: decrements activation by 1 step without underflowing below 0n.
def dec_nat(x: Nat) -> Nat:
  match x:
    case 0n:
      0n
    case 1n+p:
      p

def decay(steps: Nat, act: Nat) -> Nat:
  match steps:
    case 0n:
      act
    case 1n+rest:
      decay(rest, dec_nat(act))

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

def main() -> Pol:
  path_pol([Neg{}, Neg{}, Pos{}], Pos{})
