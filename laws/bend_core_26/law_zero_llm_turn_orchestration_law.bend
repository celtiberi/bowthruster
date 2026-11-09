# Standalone Proof (`law_zero_llm_turn_orchestration_law.bend`)

import Base
import ./main.bend as MOD
import ./LAWS.bend as Laws

def Laws.zero_llm_turn_orchestration_law(llm_invoked_on_turn):
  match llm_invoked_on_turn:
    case False{}:
      {==}
    case True{}:
      {==}
