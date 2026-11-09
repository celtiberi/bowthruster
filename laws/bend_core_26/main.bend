import Base

def is_markdown_only_turn_valid(has_markdown_tables: Bool, turn_accepted: Bool) -> Bool:
  match has_markdown_tables:
    case True{}:
      True{}
    case False{}:
      match turn_accepted:
        case True{}:
          False{}
        case False{}:
          True{}

def is_flow_or_rule_table_fused(has_flow_or_rule_table: Bool, fused_into_graph: Bool) -> Bool:
  match has_flow_or_rule_table:
    case False{}:
      True{}
    case True{}:
      fused_into_graph

def is_turn_zero_llm(llm_invoked_on_turn: Bool) -> Bool:
  match llm_invoked_on_turn:
    case False{}:
      True{}
    case True{}:
      False{}

def is_work_packet_self_contained(status_synced: Bool, spec_in_docstring: Bool) -> Bool:
  match status_synced:
    case False{}:
      False{}
    case True{}:
      spec_in_docstring

def is_verified_scope_protected(file_hash_changed: Bool, scope_still_verified: Bool) -> Bool:
  match file_hash_changed:
    case False{}:
      True{}
    case True{}:
      match scope_still_verified:
        case False{}:
          True{}
        case True{}:
          False{}

