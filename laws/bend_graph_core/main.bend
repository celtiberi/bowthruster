# Site Y — Spec 21 (`graph_core` Shared Graph & SQLite Engine) Kernel (Bend 2)
#
# Formalizes:
# 1. Three-valued signed polarity propagation (`Pol`, `inv_pol`, `comp_pol`, `path_pol`),
#    definite-sign frustration (`is_frustrated`), and bounded decay (`decay`).
# 2. Two-pass LOD 1 before LOD 2 budget admission (`admit_lod1_count`).
# 3. Topological temperature regime classification (`classify_temp_regime`) and
#    scaled activation barrier (`tau_min_scaled`).

import Base

type Pol is Data:
  Pos{}
  Neg{}
  Neut{}

def inv_pol(p: Pol) -> Pol:
  match p:
    case Pos{}:
      Neg{}
    case Neg{}:
      Pos{}
    case Neut{}:
      Neut{}

def comp_pol(a: Pol, b: Pol) -> Pol:
  match a:
    case Pos{}:
      b
    case Neg{}:
      inv_pol(b)
    case Neut{}:
      Neut{}

def cat(xs: List<&2, Pol>, ys: List<&2, Pol>) -> List<&2, Pol>:
  match xs:
    case Nil{}:
      ys
    case Con{h, t}:
      Con{h, cat(t, ys)}

def path_pol(edges: List<&2, Pol>, p: Pol) -> Pol:
  match edges:
    case Nil{}:
      p
    case Con{h, t}:
      path_pol(t, comp_pol(p, h))

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

def nat_min(a: Nat, b: Nat) -> Nat:
  match a b:
    case 0n b:
      0n
    case 1n+ap 0n:
      0n
    case 1n+ap 1n+bp:
      1n+nat_min(ap, bp)

# Pass 1 admits `min(cands, cap)` LOD 1 primary nodes before Pass 2 ever considers `lod2_req`.
def admit_lod1_count(cands: Nat, lod2_req: Nat, cap: Nat) -> Nat:
  nat_min(cands, cap)

type SeedBucket is Data:
  SBZero{}
  SBOne{}
  SBTwo{}
  SBMany{}

type TempRegime is Data:
  Cold{}
  Standard{}
  Warm{}

def classify_temp_regime(sb: SeedBucket, has_cross: Bool, has_task: Bool) -> TempRegime:
  match sb has_cross has_task:
    case SBZero{} hc ht:
      Standard{}
    case SBMany{} hc ht:
      Warm{}
    case SBTwo{} hc ht:
      Standard{}
    case SBOne{} False{} False{}:
      Cold{}
    case SBOne{} False{} True{}:
      Standard{}
    case SBOne{} True{} False{}:
      Standard{}
    case SBOne{} True{} True{}:
      Standard{}

# Scaled activation barrier `20 * tau_min(T)`: Warm (0.10 -> 2n), Standard (0.20 -> 4n), Cold (0.25 -> 5n).
def tau_min_scaled(r: TempRegime) -> Nat:
  match r:
    case Warm{}:
      2n
    case Standard{}:
      4n
    case Cold{}:
      5n

def nat_lte(a: Nat, b: Nat) -> Bool:
  match a b:
    case 0n b:
      True{}
    case 1n+ap 0n:
      False{}
    case 1n+ap 1n+bp:
      nat_lte(ap, bp)

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

def matches_nd_slice(is_unconstrained: Bool, has_dim_overlap: Bool) -> Bool:
  match is_unconstrained has_dim_overlap:
    case True{} hdo:
      True{}
    case False{} True{}:
      True{}
    case False{} False{}:
      False{}

def flow_edge_pol() -> Pol:
  Pos{}

def main() -> Pol:
  path_pol([Neg{}, Neg{}, Pos{}], Pos{})
