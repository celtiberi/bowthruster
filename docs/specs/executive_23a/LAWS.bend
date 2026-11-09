# Site Y — Canonical Spec 23A (`the_executive` Pre-Flight Whiteboard Trial & MCU Inspector) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `Spec 23A` build tag in `ModelCircuits/eng1` must prove these laws via `ModelCircuits/eng1/bend_exec_23a/PROOF.bend`.

import Base
import ./main.bend as EXA

# LAW 1: Rule-conflict dominance — whenever `has_clash == True{}`, `classify_mcu_status(has_unclear, is_question, True{}, has_flawed_idea)`
# is ALWAYS `StatusRuleConflict{}` regardless of `has_unclear`, `is_question`, or `has_flawed_idea`.
law clash_dominates_mcu_status:
  for +has_unclear: Bool
  for +is_question: Bool
  for +has_flawed_idea: Bool
  {EXA.is_rule_conflict_status(EXA.classify_mcu_status(has_unclear, is_question, True{}, has_flawed_idea)) == True{} : Bool}

# LAW 2: Flawed-idea dominance when conflict-free — whenever `has_clash == False{}` and `has_flawed_idea == True{}`,
# `classify_mcu_status(has_unclear, is_question, False{}, True{})` is ALWAYS `StatusFlawedIdea{}`.
law flawed_idea_dominates_when_no_clash:
  for +has_unclear: Bool
  for +is_question: Bool
  {EXA.is_flawed_idea_status(EXA.classify_mcu_status(has_unclear, is_question, False{}, True{})) == True{} : Bool}

# LAW 3: Clarification dominance over open question and clean — whenever `has_clash == False{}`, `has_flawed_idea == False{}`,
# and `has_unclear == True{}`, `classify_mcu_status(True{}, is_question, False{}, False{})` is ALWAYS `StatusNeedsClarification{}`.
law unclear_dominates_when_no_clash:
  for +is_question: Bool
  {EXA.is_needs_clarification_status(EXA.classify_mcu_status(True{}, is_question, False{}, False{})) == True{} : Bool}

# LAW 4: MCU cleanliness bi-implication — `classify_mcu_status(has_unclear, is_question, has_clash, has_flawed_idea)` returns `StatusClean{}`
# iff `has_unclear == False{}`, `is_question == False{}`, `has_clash == False{}`, and `has_flawed_idea == False{}`.
law mcu_clean_iff_all_clear:
  for +has_unclear: Bool
  for +is_question: Bool
  for +has_clash: Bool
  for +has_flawed_idea: Bool
  {EXA.bool_eq(
    EXA.is_clean_status(EXA.classify_mcu_status(has_unclear, is_question, has_clash, has_flawed_idea)),
    Bool.and(
      Bool.not(has_unclear),
      Bool.and(
        Bool.not(is_question),
        Bool.and(Bool.not(has_clash), Bool.not(has_flawed_idea))
      )
    )
  ) == True{} : Bool}

# LAW 5: Turn pre-flight cleanliness bi-implication — `is_preflight_clean(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)`
# returns `True{}` iff all four turn-level blocker flags are `False{}`.
law preflight_clean_iff_no_blockers:
  for +has_ambiguity: Bool
  for +has_open_questions: Bool
  for +has_rule_conflict: Bool
  for +has_flawed_idea: Bool
  {EXA.bool_eq(
    EXA.is_preflight_clean(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea),
    Bool.and(
      Bool.not(has_ambiguity),
      Bool.and(
        Bool.not(has_open_questions),
        Bool.and(Bool.not(has_rule_conflict), Bool.not(has_flawed_idea))
      )
    )
  ) == True{} : Bool}
