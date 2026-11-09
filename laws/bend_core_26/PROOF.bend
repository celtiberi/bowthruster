# Formal Proofs (`PROOF.bend`) for Stage 26 Core Laws (`bend_core_26`)

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

def Laws.zero_llm_turn_orchestration_law(llm_invoked_on_turn):
  match llm_invoked_on_turn:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.status_sync_and_spec_docstring_law(status_synced, spec_in_docstring):
  match status_synced spec_in_docstring:
    case False{} False{}:
      {==}
    case False{} True{}:
      {==}
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}

def Laws.verified_scope_edit_guard_law(file_hash_changed, scope_still_verified):
  match file_hash_changed scope_still_verified:
    case False{} False{}:
      {==}
    case False{} True{}:
      {==}
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}

