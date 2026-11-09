# LAWS.bend — Stage 26 Core Deterministic Markdown & Zero-LLM Laws (bend_core_26)

import Base
import ./main.bend as MOD

law markdown_only_turn_law:
  for +has_markdown_tables: Bool
  for +turn_accepted: Bool
  {MOD.is_markdown_only_turn_valid(has_markdown_tables, turn_accepted) == Bool.or(has_markdown_tables, Bool.not(turn_accepted)) : Bool}

law flow_and_rule_table_fusion_law:
  for +has_flow_or_rule_table: Bool
  for +fused_into_graph: Bool
  {MOD.is_flow_or_rule_table_fused(has_flow_or_rule_table, fused_into_graph) == Bool.or(Bool.not(has_flow_or_rule_table), fused_into_graph) : Bool}

law zero_llm_turn_orchestration_law:
  for +llm_invoked_on_turn: Bool
  {MOD.is_turn_zero_llm(llm_invoked_on_turn) == Bool.not(llm_invoked_on_turn) : Bool}

law status_sync_and_spec_docstring_law:
  for +status_synced: Bool
  for +spec_in_docstring: Bool
  {MOD.is_work_packet_self_contained(status_synced, spec_in_docstring) == Bool.and(status_synced, spec_in_docstring) : Bool}

law verified_scope_edit_guard_law:
  for +file_hash_changed: Bool
  for +scope_still_verified: Bool
  {MOD.is_verified_scope_protected(file_hash_changed, scope_still_verified) == Bool.not(Bool.and(file_hash_changed, scope_still_verified)) : Bool}

