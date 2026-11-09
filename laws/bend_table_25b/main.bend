# Stage 25B (`table_renderer`): LOD Escalation & Summary Bound (`main.bend`)

import Base

type TableLOD is Data:
  TLOD0{}
  TLOD1{}
  TLOD2{}

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

def lod_eq(a: TableLOD, b: TableLOD) -> Bool:
  match a b:
    case TLOD0{} TLOD0{}:
      True{}
    case TLOD1{} TLOD1{}:
      True{}
    case TLOD2{} TLOD2{}:
      True{}
    case TLOD0{} TLOD1{}:
      False{}
    case TLOD0{} TLOD2{}:
      False{}
    case TLOD1{} TLOD0{}:
      False{}
    case TLOD1{} TLOD2{}:
      False{}
    case TLOD2{} TLOD0{}:
      False{}
    case TLOD2{} TLOD1{}:
      False{}

def is_tlod1(lod: TableLOD) -> Bool:
  match lod:
    case TLOD0{}:
      False{}
    case TLOD1{}:
      True{}
    case TLOD2{}:
      False{}

def lod_not_demoted(prev: TableLOD, next: TableLOD) -> Bool:
  match prev next:
    case TLOD0{} TLOD0{}:
      True{}
    case TLOD0{} TLOD1{}:
      True{}
    case TLOD0{} TLOD2{}:
      True{}
    case TLOD1{} TLOD0{}:
      False{}
    case TLOD1{} TLOD1{}:
      True{}
    case TLOD1{} TLOD2{}:
      True{}
    case TLOD2{} TLOD0{}:
      False{}
    case TLOD2{} TLOD1{}:
      False{}
    case TLOD2{} TLOD2{}:
      True{}

def step_slot_lod_escalation(
  cur_lod: TableLOD,
  request_upgrade: Bool,
  words_within_50: Bool,
  budget_ok: Bool
) -> TableLOD:
  match cur_lod request_upgrade words_within_50 budget_ok:
    case TLOD0{} False{} _ _:
      TLOD0{}
    case TLOD1{} False{} _ _:
      TLOD1{}
    case TLOD2{} False{} _ _:
      TLOD2{}
    case TLOD0{} True{} True{} True{}:
      TLOD1{}
    case TLOD0{} True{} True{} False{}:
      TLOD0{}
    case TLOD0{} True{} False{} _:
      TLOD0{}
    case TLOD1{} True{} True{} True{}:
      TLOD2{}
    case TLOD1{} True{} True{} False{}:
      TLOD1{}
    case TLOD1{} True{} False{} _:
      TLOD1{}
    case TLOD2{} True{} _ _:
      TLOD2{}

def should_execute_foraging_step(
  foraging_requested: Bool,
  steps_under_max: Bool,
  budget_ok: Bool
) -> Bool:
  match foraging_requested steps_under_max budget_ok:
    case True{} True{} True{}:
      True{}
    case True{} True{} False{}:
      False{}
    case True{} False{} _:
      False{}
    case False{} _ _:
      False{}
