# LAWS.bend — Compiled by Whiteboard from % LAW records for mcp_server_28

import Base
import ./main.bend as MOD

law mcp_tool_dispatch_law:
  for +has_name: Bool
  for +board_nonempty: Bool
  {MOD.is_mcp_tool_call_valid(has_name, board_nonempty) == Bool.and(has_name, board_nonempty) : Bool}

law mcp_roundtrip_verdict_law:
  for +init_ok: Bool
  for +list_ok: Bool
  for +call_ok: Bool
  {MOD.is_mcp_roundtrip_valid(init_ok, list_ok, call_ok) == Bool.and(init_ok, Bool.and(list_ok, call_ok)) : Bool}
