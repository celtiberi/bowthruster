#!/usr/bin/env python3
"""Standalone BowThruster MCP Server (`bowthruster/mcp_server.py`)."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from bowthruster.cli import (
    BOWTHRUSTER_HOME,
    REPO_ROOT,
    _board_lock,
    _load_board,
    _save_board,
    _verify_component_files,
    apply_status_and_claim,
    format_exception_error,
    record_flight_event,
)
from bowthruster.mcp_schemas import (
    KNOWN_TOOL_NAMES,
    MCP_PROTOCOL_VERSION,
    MCP_RESOURCES,
    MCP_TOOLS,
    SERVER_NAME,
    SERVER_VERSION,
)
from bowthruster.mcp_transport import (
    _format_tool_result,
    _hot_reload_if_changed,
    _read_doc_resource,
    _resolve_project_root,
    _tool_atlas,
    _tool_packets_or_turn,
    _tool_readme_or_logs,
    _tool_slice,
)
from bowthruster.whiteboard import slice_lattice


@dataclass(frozen=True)
class McpJsonRpcRequest:
    jsonrpc: str = "2.0"
    id: Any = None
    method: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class McpToolCallResult:
    content: list[dict[str, Any]] = field(default_factory=list)
    isError: bool = False
    structuredContent: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class McpJsonRpcResponse:
    jsonrpc: str = "2.0"
    id: Any = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def can_transition_to_verified(
    is_verified: bool = True, files_exist: bool = True, within_max_lines: bool = True
) -> bool:
    return bool((not is_verified) or (files_exist and within_max_lines))


def _tool_git_sync_or_status(
    name: str, board: str, args: dict[str, Any], active_root: Path, t0: float
) -> dict[str, Any]:
    import bowthruster.cli as _cli

    if name == "git_sync":
        mode = str(args.get("mode") or "drift").strip().lower()
        want_ver = bool(args.get("mark_verified", False))
        payload, txt, allow = _cli.execute_git_sync(
            board, mode, args.get("manifest"), mark_verified=want_ver, active_root=active_root
        )
        resp, nbytes = _format_tool_result(payload, is_error=(mode == "pre" and not allow), text=txt)
        mod_sc = payload.get("marked_verified_scopes", payload.get("modified_scopes"))
        record_flight_event(
            board,
            f"git_sync:{mode}",
            (time.perf_counter() - t0) * 1000.0,
            "OK" if allow else "REJECTED",
            decision=payload.get("decision"),
            violations=payload.get("violations"),
            modified_scopes=mod_sc or payload.get("drifted_scopes"),
            stale_cascaded_scopes=payload.get("cascaded_stale_scopes"),
            response_bytes=nbytes,
        )
        return resp
    comp = str(args.get("component") or "").strip()
    new_st = str(args.get("new_status") or "").strip().upper()
    graph, wb_json, wb_sqlite, _ = _load_board(board)
    ver_info: dict[str, Any] = {}
    if new_st == "VERIFIED" and can_transition_to_verified(
        new_st == "VERIFIED", bool(comp), bool(graph.get("scopes"))
    ):
        ver_info = _verify_component_files(graph, comp) or {}
    snode = apply_status_and_claim(graph, comp, new_st or None, opts=args)
    if str(snode.get("status") or "").upper() == "VERIFIED":
        snode["verified_untested"] = bool(ver_info.get("verified_untested", False))
    _save_board(graph, wb_json, wb_sqlite)
    raw_st = snode.get("status")
    is_untested = bool(snode.get("verified_untested", False))
    disp_st = "VERIFIED (untested)" if (raw_st == "VERIFIED" and is_untested) else raw_st
    warnings = list(ver_info.get("warnings") or [])
    undeclared_tests = list(ver_info.get("undeclared_matching_tests") or [])
    warn_sfx = ("\n" + "\n".join(f"[WARNING]: {w}" for w in warnings)) if warnings else ""
    st_txt = (
        f"[STATUS '{board}'] {comp} -> status={disp_st} claimed_by={snode.get('claimed_by')} "
        f"build_tag={snode.get('build_tag')} core_tag={snode.get('core_tag')} "
        f"failure_diagnostic={snode.get('failure_diagnostic')!r}{warn_sfx}"
    )
    out_p: dict[str, Any] = {
        "board": board,
        "component": comp,
        "status": raw_st,
        "display_status": disp_st,
        "verified_untested": is_untested,
        "claimed_by": snode.get("claimed_by"),
        "build_tag": snode.get("build_tag"),
        "core_tag": snode.get("core_tag"),
        "failure_diagnostic": snode.get("failure_diagnostic"),
    }
    if undeclared_tests:
        out_p["undeclared_matching_tests"] = undeclared_tests
    if warnings:
        out_p["warnings"] = warnings
    resp, nbytes = _format_tool_result(out_p, text=st_txt)
    dur_ms = (time.perf_counter() - t0) * 1000.0
    record_flight_event(
        board, name, dur_ms, "OK", decision=raw_st, modified_scopes=[comp], response_bytes=nbytes
    )
    return resp


def call_lattice_tool(tool_name: Any = None, arguments: Any = None) -> dict[str, Any]:
    """Dispatches a validated MCP tool call to the BowThruster engine (`$ INTERFACE call_lattice_tool`)."""
    _hot_reload_if_changed()
    t0 = time.perf_counter()
    args = dict(arguments) if isinstance(arguments, dict) else {}
    raw_name = str(tool_name or "").strip()
    board = str(args.get("board") or "lattice_core").strip()
    name = raw_name.removeprefix("bowthruster_").removeprefix("lattice_")
    has_name = name in KNOWN_TOOL_NAMES
    if not (has_name and bool(board)):
        err_msg = f"Invalid MCP tool call: tool_name={raw_name!r} (recognized={has_name}), board={board!r}"
        err_obj = {"type": "InvalidToolCall", "file": __file__, "line": 0, "message": err_msg, "traceback": ""}
        err_p = {"error": err_msg, "error_details": err_obj}
        resp, nbytes = _format_tool_result(err_p, is_error=True, text=f"[ERROR] {err_msg}")
        dur_ms = (time.perf_counter() - t0) * 1000.0
        b_id, t_id = board or "default", raw_name or "unknown"
        record_flight_event(b_id, t_id, dur_ms, "REJECTED", response_bytes=nbytes, error=err_obj)
        return resp
    import bowthruster.cli as _cli

    lock_ctx = nullcontext() if name == "git_sync" else _board_lock(board)
    with lock_ctx:
        prev_root, prev_env = _cli.REPO_ROOT, os.environ.get("BOWTHRUSTER_PROJECT_ROOT")
        active_root = _resolve_project_root(board, args.get("project_root"), args.get("message"))
        _cli.REPO_ROOT = active_root
        os.environ["BOWTHRUSTER_PROJECT_ROOT"] = str(active_root)
        try:
            if name == "migrate":
                from bowthruster.whiteboard.migrations import format_migration_report, migrate_repo_boards

                m_pay = migrate_repo_boards(
                    active_root,
                    board=board,
                    migrate_all=bool(args.get("migrate_all")),
                    dry_run=bool(args.get("dry_run")),
                )
                resp, nbytes = _format_tool_result(m_pay, text=format_migration_report(m_pay))
                record_flight_event(
                    board, name, (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes
                )
                return resp
            if name == "bootstrap":
                from bowthruster.bootstrap import bootstrap_repository, format_bootstrap_report

                graph, wb_json, wb_sqlite, _ = _load_board(board)
                b_pay = bootstrap_repository(active_root, board, graph, component=args.get("component"))
                _save_board(graph, wb_json, wb_sqlite)
                resp, nbytes = _format_tool_result(b_pay, text=format_bootstrap_report(b_pay))
                dur_ms = (time.perf_counter() - t0) * 1000.0
                record_flight_event(
                    board, name, dur_ms, "OK", decision=b_pay.get("status"), response_bytes=nbytes
                )
                return resp
            if name in ("readme", "logs"):
                return _tool_readme_or_logs(name, board, args, t0)
            if name == "atlas":
                return _tool_atlas(board, args, t0)
            if name == "slice":
                return _tool_slice(board, args, active_root, t0)
            if name in ("packets", "turn"):
                return _tool_packets_or_turn(name, board, args, t0)
            if name in ("git_sync", "status"):
                return _tool_git_sync_or_status(name, board, args, active_root, t0)
        except Exception as exc:
            err_dict, err_str = format_exception_error(exc), f"{type(exc).__name__}: {exc}"
            err_p = {"error": err_str, "error_details": err_dict}
            resp, nbytes = _format_tool_result(err_p, is_error=True, text=f"[ERROR] {err_str}")
            dur_ms = (time.perf_counter() - t0) * 1000.0
            record_flight_event(board, name, dur_ms, "ERROR", response_bytes=nbytes, error=err_dict)
            return resp
        finally:
            _cli.REPO_ROOT = prev_root
            if prev_env is None:
                os.environ.pop("BOWTHRUSTER_PROJECT_ROOT", None)
            else:
                os.environ["BOWTHRUSTER_PROJECT_ROOT"] = prev_env
    resp, _ = _format_tool_result({"error": f"Unhandled tool {name!r}"}, is_error=True)
    return resp


def handle_mcp_request(request: Any = None) -> dict[str, Any] | None:
    """Handles an incoming JSON-RPC 2.0 MCP request (`$ INTERFACE handle_mcp_request`)."""
    if not isinstance(request, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    raw_params = request.get("params")
    req = McpJsonRpcRequest(
        jsonrpc=str(request.get("jsonrpc") or "2.0"),
        id=request.get("id"),
        method=str(request.get("method") or ""),
        params=raw_params if isinstance(raw_params, dict) else {},
    )
    if not req.method:
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "error": {"code": -32600, "message": "Invalid Request: missing method"},
        }
    if req.method.startswith("notifications/"):
        return None
    if req.method == "ping":
        return {"jsonrpc": "2.0", "id": req.id, "result": {}}
    if req.method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if req.method == "resources/list":
        return {"jsonrpc": "2.0", "id": req.id, "result": {"resources": list(MCP_RESOURCES)}}
    if req.method == "resources/read":
        uri = str(req.params.get("uri") or "bowthruster://readme")
        doc = _read_doc_resource("guide" if "guide" in uri else "readme")
        txt = doc.get("guide") if "guide" in uri else doc.get("readme", "")
        contents = [{"uri": uri, "mimeType": "text/markdown", "text": txt}]
        return {"jsonrpc": "2.0", "id": req.id, "result": {"contents": contents}}
    if req.method == "tools/list":
        return {"jsonrpc": "2.0", "id": req.id, "result": {"tools": list(MCP_TOOLS)}}
    if req.method == "tools/call":
        _hot_reload_if_changed()
        raw_args = req.params.get("arguments")
        res = call_lattice_tool(req.params.get("name"), raw_args if isinstance(raw_args, dict) else {})
        res_out = {"content": res.get("content", []), "isError": bool(res.get("isError", False))}
        return {"jsonrpc": "2.0", "id": req.id, "result": res_out}
    return {
        "jsonrpc": "2.0",
        "id": req.id,
        "error": {"code": -32601, "message": f"Method not found: {req.method}"},
    }


def main() -> int:
    """Runs the stdio JSON-RPC 2.0 MCP server loop."""
    for raw_line in sys.stdin:
        if not (line := raw_line.strip()):
            continue
        try:
            req_obj = json.loads(line)
        except json.JSONDecodeError as exc:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        if (resp := handle_mcp_request(req_obj)) is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
