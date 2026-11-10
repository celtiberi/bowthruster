"""Stage 28: End-to-End JSON-RPC 2.0 MCP Protocol Roundtrip Verifier (`lattice/mcp_verifier.py`, <= 80 lines)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bowthruster.mcp_server import KNOWN_TOOL_NAMES, call_lattice_tool, handle_mcp_request


@dataclass(frozen=True)
class McpRoundtripVerdict:
    is_valid: bool
    init_ok: bool
    list_ok: bool
    call_ok: bool
    tools_count: int


def is_mcp_roundtrip_valid(init_ok: bool = True, list_ok: bool = True, call_ok: bool = True) -> bool:
    """Pure roundtrip verdict governed by Bend 2 `mcp_roundtrip_verdict_law` (Stage 28)."""
    return bool(init_ok and (list_ok and call_ok))


def mcp_roundtrip_verdict_law(init_ok: bool = True, list_ok: bool = True, call_ok: bool = True) -> bool:
    """Alias for Bend 2 `mcp_roundtrip_verdict_law` AST call-graph verification."""
    return is_mcp_roundtrip_valid(init_ok, list_ok, call_ok)


def verify_mcp_server_roundtrip(board: Any = None) -> dict[str, Any]:
    """Runs end-to-end MCP initialize, tools/list, and tools/call roundtrip checks (`$ INTERFACE`)."""
    target_board = str(board or "mcp_server_28").strip() or "mcp_server_28"
    init_resp = handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    init_ok = bool(
        isinstance(init_resp, dict)
        and (init_resp.get("result") or {}).get("serverInfo", {}).get("name") == "bowthruster-mcp"
    )

    list_resp = handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = (list_resp or {}).get("result", {}).get("tools") or []
    tool_names = {str(t.get("name")) for t in tools}
    list_ok = bool(tool_names == set(KNOWN_TOOL_NAMES) and len(tools) == len(KNOWN_TOOL_NAMES))

    atlas_resp = handle_mcp_request({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "atlas", "arguments": {"board": target_board}},
    })
    pkts_resp = handle_mcp_request({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "packets", "arguments": {"board": target_board}},
    })
    bad_call = call_lattice_tool("nonexistent_tool", {"board": target_board})
    call_ok = bool(
        not (atlas_resp or {}).get("result", {}).get("isError", True)
        and not (pkts_resp or {}).get("result", {}).get("isError", True)
        and bool(bad_call.get("isError"))
    )

    verdict = is_mcp_roundtrip_valid(init_ok, list_ok, call_ok)
    res = McpRoundtripVerdict(
        is_valid=verdict,
        init_ok=init_ok,
        list_ok=list_ok,
        call_ok=call_ok,
        tools_count=len(tools),
    )
    return {
        "is_valid": res.is_valid,
        "init_ok": res.init_ok,
        "list_ok": res.list_ok,
        "call_ok": res.call_ok,
        "tools_count": res.tools_count,
    }
