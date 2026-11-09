import Base

def is_mcp_tool_call_valid(has_name: Bool, board_nonempty: Bool) -> Bool:
  match has_name:
    case False{}:
      False{}
    case True{}:
      board_nonempty

def is_mcp_roundtrip_valid(init_ok: Bool, list_ok: Bool, call_ok: Bool) -> Bool:
  match init_ok:
    case False{}:
      False{}
    case True{}:
      match list_ok:
        case False{}:
          False{}
        case True{}:
          call_ok
