# Stage 25B (`table_renderer`): LOD Escalation Monotonicity & Summary Bound Proofs (`PROOF.bend`)

import Base
import ./main.bend as TBLB
import ./LAWS.bend as Laws

def Laws.lod_escalation_monotonicity_law(
  lod,
  req,
  words_ok,
  budget_ok
):
  match lod req words_ok budget_ok:
    case TBLB.TLOD0{} False{} False{} False{}:
      {==}
    case TBLB.TLOD0{} False{} False{} True{}:
      {==}
    case TBLB.TLOD0{} False{} True{} False{}:
      {==}
    case TBLB.TLOD0{} False{} True{} True{}:
      {==}
    case TBLB.TLOD0{} True{} False{} False{}:
      {==}
    case TBLB.TLOD0{} True{} False{} True{}:
      {==}
    case TBLB.TLOD0{} True{} True{} False{}:
      {==}
    case TBLB.TLOD0{} True{} True{} True{}:
      {==}
    case TBLB.TLOD1{} False{} False{} False{}:
      {==}
    case TBLB.TLOD1{} False{} False{} True{}:
      {==}
    case TBLB.TLOD1{} False{} True{} False{}:
      {==}
    case TBLB.TLOD1{} False{} True{} True{}:
      {==}
    case TBLB.TLOD1{} True{} False{} False{}:
      {==}
    case TBLB.TLOD1{} True{} False{} True{}:
      {==}
    case TBLB.TLOD1{} True{} True{} False{}:
      {==}
    case TBLB.TLOD1{} True{} True{} True{}:
      {==}
    case TBLB.TLOD2{} False{} False{} False{}:
      {==}
    case TBLB.TLOD2{} False{} False{} True{}:
      {==}
    case TBLB.TLOD2{} False{} True{} False{}:
      {==}
    case TBLB.TLOD2{} False{} True{} True{}:
      {==}
    case TBLB.TLOD2{} True{} False{} False{}:
      {==}
    case TBLB.TLOD2{} True{} False{} True{}:
      {==}
    case TBLB.TLOD2{} True{} True{} False{}:
      {==}
    case TBLB.TLOD2{} True{} True{} True{}:
      {==}

def Laws.stage2_foraging_budget_law(
  req,
  steps_ok,
  budget_ok
):
  match req steps_ok budget_ok:
    case False{} False{} False{}:
      {==}
    case False{} False{} True{}:
      {==}
    case False{} True{} False{}:
      {==}
    case False{} True{} True{}:
      {==}
    case True{} False{} False{}:
      {==}
    case True{} False{} True{}:
      {==}
    case True{} True{} False{}:
      {==}
    case True{} True{} True{}:
      {==}
