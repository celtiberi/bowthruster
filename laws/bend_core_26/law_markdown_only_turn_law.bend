# Standalone Proof (`law_markdown_only_turn_law.bend`)

import Base
import ./main.bend as MOD
import ./LAWS.bend as Laws

def Laws.markdown_only_turn_law(has_markdown_tables, turn_accepted):
  match has_markdown_tables turn_accepted:
    case False{} False{}:
      {==}
    case False{} True{}:
      {==}
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}
