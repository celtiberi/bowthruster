# Spec 23A: Formal Proofs of Laws 1-5 (`PROOF.bend`)

import Base
import ./main.bend as EXA
import ./LAWS.bend as Laws

def Laws.clash_dominates_mcu_status(has_unclear, is_question, has_flawed_idea):
  {==}

def Laws.flawed_idea_dominates_when_no_clash(has_unclear, is_question):
  {==}

def Laws.unclear_dominates_when_no_clash(is_question):
  {==}

def Laws.mcu_clean_iff_all_clear(has_unclear, is_question, has_clash, has_flawed_idea):
  match has_unclear:
    case True{}:
      match is_question:
        case True{}:
          match has_clash:
            case True{}:
              match has_flawed_idea:
                case True{}:
                  {==}
                case False{}:
                  {==}
            case False{}:
              match has_flawed_idea:
                case True{}:
                  {==}
                case False{}:
                  {==}
        case False{}:
          match has_clash:
            case True{}:
              match has_flawed_idea:
                case True{}:
                  {==}
                case False{}:
                  {==}
            case False{}:
              match has_flawed_idea:
                case True{}:
                  {==}
                case False{}:
                  {==}
    case False{}:
      match is_question:
        case True{}:
          match has_clash:
            case True{}:
              match has_flawed_idea:
                case True{}:
                  {==}
                case False{}:
                  {==}
            case False{}:
              match has_flawed_idea:
                case True{}:
                  {==}
                case False{}:
                  {==}
        case False{}:
          match has_clash:
            case True{}:
              match has_flawed_idea:
                case True{}:
                  {==}
                case False{}:
                  {==}
            case False{}:
              match has_flawed_idea:
                case True{}:
                  {==}
                case False{}:
                  {==}

def Laws.preflight_clean_iff_no_blockers(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea):
  match has_ambiguity:
    case True{}:
      {==}
    case False{}:
      match has_open_questions:
        case True{}:
          {==}
        case False{}:
          match has_rule_conflict:
            case True{}:
              {==}
            case False{}:
              match has_flawed_idea:
                case True{}:
                  {==}
                case False{}:
                  {==}
