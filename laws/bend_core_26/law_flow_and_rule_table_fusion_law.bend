# Standalone Proof (`law_flow_and_rule_table_fusion_law.bend`)

import Base
import ./main.bend as MOD
import ./LAWS.bend as Laws

def Laws.flow_and_rule_table_fusion_law(has_flow_or_rule_table, fused_into_graph):
  match has_flow_or_rule_table fused_into_graph:
    case False{} False{}:
      {==}
    case False{} True{}:
      {==}
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}
