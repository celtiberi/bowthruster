# Spec 23A: Pre-Flight Whiteboard Trial & MCU Status Classification (`main.bend`)

import Base

type McuStatus is Data:
  StatusClean{}
  StatusNeedsClarification{}
  StatusHasOpenQuestion{}
  StatusFlawedIdea{}
  StatusRuleConflict{}

def bool_eq(a: Bool, b: Bool) -> Bool:
  match a:
    case True{}:
      match b:
        case True{}:
          True{}
        case False{}:
          False{}
    case False{}:
      match b:
        case True{}:
          False{}
        case False{}:
          True{}

def is_rule_conflict_status(s: McuStatus) -> Bool:
  match s:
    case StatusRuleConflict{}:
      True{}
    case StatusFlawedIdea{}:
      False{}
    case StatusNeedsClarification{}:
      False{}
    case StatusHasOpenQuestion{}:
      False{}
    case StatusClean{}:
      False{}

def is_flawed_idea_status(s: McuStatus) -> Bool:
  match s:
    case StatusFlawedIdea{}:
      True{}
    case StatusRuleConflict{}:
      False{}
    case StatusNeedsClarification{}:
      False{}
    case StatusHasOpenQuestion{}:
      False{}
    case StatusClean{}:
      False{}

def is_needs_clarification_status(s: McuStatus) -> Bool:
  match s:
    case StatusNeedsClarification{}:
      True{}
    case StatusRuleConflict{}:
      False{}
    case StatusFlawedIdea{}:
      False{}
    case StatusHasOpenQuestion{}:
      False{}
    case StatusClean{}:
      False{}

def is_clean_status(s: McuStatus) -> Bool:
  match s:
    case StatusClean{}:
      True{}
    case StatusRuleConflict{}:
      False{}
    case StatusFlawedIdea{}:
      False{}
    case StatusNeedsClarification{}:
      False{}
    case StatusHasOpenQuestion{}:
      False{}

def classify_by_priority(
  has_clash: Bool,
  has_flawed_idea: Bool,
  has_unclear: Bool,
  is_question: Bool
) -> McuStatus:
  match has_clash:
    case True{}:
      StatusRuleConflict{}
    case False{}:
      match has_flawed_idea:
        case True{}:
          StatusFlawedIdea{}
        case False{}:
          match has_unclear:
            case True{}:
              StatusNeedsClarification{}
            case False{}:
              match is_question:
                case True{}:
                  StatusHasOpenQuestion{}
                case False{}:
                  StatusClean{}

def classify_mcu_status(
  has_unclear: Bool,
  is_question: Bool,
  has_clash: Bool,
  has_flawed_idea: Bool
) -> McuStatus:
  classify_by_priority(has_clash, has_flawed_idea, has_unclear, is_question)

def is_preflight_clean(
  has_ambiguity: Bool,
  has_open_questions: Bool,
  has_rule_conflict: Bool,
  has_flawed_idea: Bool
) -> Bool:
  Bool.and(
    Bool.not(has_ambiguity),
    Bool.and(
      Bool.not(has_open_questions),
      Bool.and(Bool.not(has_rule_conflict), Bool.not(has_flawed_idea))
    )
  )

def main() -> Bool:
  Bool.and(
    is_rule_conflict_status(classify_mcu_status(True{}, True{}, True{}, True{})),
    Bool.and(
      is_flawed_idea_status(classify_mcu_status(True{}, True{}, False{}, True{})),
      Bool.and(
        is_needs_clarification_status(classify_mcu_status(True{}, True{}, False{}, False{})),
        Bool.and(
          is_clean_status(classify_mcu_status(False{}, False{}, False{}, False{})),
          is_preflight_clean(False{}, False{}, False{}, False{})
        )
      )
    )
  )
