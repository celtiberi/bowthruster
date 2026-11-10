#!/usr/bin/env python3
"""Standalone BowThruster MCP Server (`bowthruster/mcp_server.py`)."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from bowthruster.cli import (
    BOWTHRUSTER_HOME, REPO_ROOT, _board_lock, _execute_turn, _get_evaluate_lattice_proof,
    _load_atlas_boards, _load_board, _save_board, _verify_component_files,
    apply_slice_stale_and_claim, apply_status_and_claim, format_exception_error,
    read_flight_logs, record_flight_event,
)
from bowthruster.cli_commands import compile_and_claim_packets
from bowthruster.mcp_schemas import (
    COMPACT_PACKET_KEYS, KNOWN_TOOL_NAMES, MCP_PROTOCOL_VERSION,
    MCP_RESOURCES, MCP_TOOLS, SERVER_NAME, SERVER_VERSION,
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


def _read_doc_resource(section: str = "readme") -> dict[str, Any]:
    home = BOWTHRUSTER_HOME
    readme_p, skills_dir = home / "README.md", home / "docs" / "skills"
    sec = str(section or "readme").strip().lower()
    out: dict[str, Any] = {"project_root": str(REPO_ROOT), "bowthruster_home": str(home)}
    cands = (skills_dir / "bowthruster" / "SKILL.md", skills_dir / "lattice" / "SKILL.md")
    sk_p = next((p for p in cands if p.is_file()), None)
    uni = sk_p.read_text(encoding="utf-8") if sk_p else ""
    if sec in ("readme", "full_readme", "all") and readme_p.is_file():
        out["readme"] = readme_p.read_text(encoding="utf-8")
    if sec in ("guide", "all") and readme_p.is_file():
        out["guide"] = readme_p.read_text(encoding="utf-8")
    out["quickstart"] = (
        "# BowThruster Agent Quickstart & Brownfield Onboarding\n"
        "1. Deterministically extract candidate components, public symbols, imports, and tests via "
        "`bootstrap(board='<board>', project_root='/abs/path')`.\n"
        "2. Work through one onboarding packet at a time via `turn(board='<board>', message='| Component | ... |')` "
        "to fill in Charter, Role, Why, Spec, Flows, and optional Laws (or merge/split candidate components).\n"
        "3. Run `git_sync(board='<board>', mode='pre', mark_verified=True)` and "
        "`git_sync(board='<board>', mode='post')`.\n"
        "4. Save the unified skill below to `.agents/skills/bowthruster/SKILL.md` "
        "(substituting `<BOARD_NAME>` and `<TEST_CMD>`). Pass `section='full_readme'` for full docs.\n\n"
        + uni
    )
    if sec in ("skill", "skills", "all") and uni:
        out["skills"] = {k: uni for k in ("bowthruster", "lattice", "lattice-change", "lattice-navigate", "lattice-qa")}
    elif sec in ("change", "navigate", "qa") and uni:
        out["skills"] = {f"lattice-{sec}": uni, "bowthruster": uni, "lattice": uni}
    return out


def _resolve_project_root(board: str, project_root: str | None = None, message: str | None = None) -> Path:
    if project_root and str(project_root).strip():
        return Path(str(project_root).strip()).expanduser().resolve()
    home = BOWTHRUSTER_HOME
    slug = "".join(c for c in (board or "").strip() if c.isalnum() or c in ("_", "-")) or "default"
    sibs = sorted(p for p in home.parents[1].iterdir() if p.is_dir()) if len(home.parents) > 1 else []
    for cand in (REPO_ROOT, home, home.parent, *sibs):
        if (cand / ".whiteboard" / f"{slug}.json").is_file():
            return cand.resolve()
    if message:
        rel_files = [tok.strip("`'\",:;()") for tok in str(message).split() if "/" in tok and "." in tok.split("/")[-1]]
        for cand in (REPO_ROOT, home.parent, *sibs):
            if any((cand / rf).is_file() for rf in rel_files):
                return cand.resolve()
    return REPO_ROOT


def _format_tool_result(
    payload: dict[str, Any], *, is_error: bool = False, text: str | None = None, fence: bool = True
) -> tuple[dict[str, Any], int]:
    raw = str(text) if text is not None else json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    kind = "text" if text is not None else "json"
    out_txt = f"```{kind}\n{raw}\n```" if fence and not raw.lstrip().startswith("```") else raw
    res = McpToolCallResult([{"type": "text", "text": out_txt}], bool(is_error), payload)
    out_dict = {"content": res.content, "isError": res.isError, "structuredContent": res.structuredContent}
    return out_dict, len(out_txt.encode("utf-8"))


def _tool_readme_or_logs(name: str, board: str, args: dict[str, Any], t0: float) -> dict[str, Any]:
    if name == "readme":
        sec = str(args.get("section") or "readme").strip().lower()
        doc = _read_doc_resource(sec)
        sec_map = {"readme": doc.get("quickstart"), "full_readme": doc.get("readme"), "guide": doc.get("guide")}
        if sec in sec_map:
            doc_txt = sec_map[sec]
        elif sec in ("skill", "skills", "change", "navigate", "qa"):
            doc_txt = (doc.get("skills") or {}).get("bowthruster")
        else:
            doc_txt = None
        resp, nbytes = _format_tool_result(doc, text=doc_txt, fence=(doc_txt is None))
        record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes)
        return resp
    entries = read_flight_logs(limit=int(args.get("limit") or 20), board=args.get("board"), status=args.get("status"))
    log_txt = "\n".join(
        f"[{e.get('timestamp')}] board={e.get('board')} cmd={e.get('tool_or_cmd')} "
        f"status={e.get('status')} decision={e.get('decision')} "
        f"({e.get('duration_ms')}ms, bytes={e.get('response_bytes', 0)}) violations={len(e.get('violations') or [])}"
        for e in entries
    ) or "(no flight log entries)"
    resp, nbytes = _format_tool_result({"entries": entries, "count": len(entries)}, text=log_txt)
    record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes)
    return resp


def _tool_atlas(board: str, args: dict[str, Any], t0: float) -> dict[str, Any]:
    from bowthruster.the_table.surface import render_lattice_atlas as _render_atlas

    boards_dict = _load_atlas_boards(board, args.get("boards"), bool(args.get("load_all", False)))
    view = _render_atlas(boards_dict, has_local_seed_cues=False, explicit_atlas_request=True)
    if args.get("spec"):
        atlas_txt = str(view.get("atlas_spec") or "")
    else:
        metrics_json = json.dumps(view.get("atlas_metrics") or {}, sort_keys=True)
        atlas_txt = f"```text\n{view.get('atlas_schematic') or ''}\n[Atlas Metrics]: {metrics_json}\n```"
        if args.get("mermaid") and view.get("atlas_mermaid"):
            atlas_txt += f"\n\n```mermaid\n{view['atlas_mermaid']}\n```"
    out_p = {
        "boards": list(boards_dict.keys()),
        "atlas_mounted": view.get("atlas_mounted"),
        "atlas_schematic": view.get("atlas_schematic"),
        "atlas_metrics": view.get("atlas_metrics"),
        **({"atlas_mermaid": view.get("atlas_mermaid")} if args.get("mermaid") else {}),
        **({"atlas_spec": view.get("atlas_spec")} if args.get("spec") else {}),
    }
    resp, nbytes = _format_tool_result(out_p, text=atlas_txt, fence=not bool(args.get("spec")))
    record_flight_event(board, "atlas", (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes)
    return resp


def _tool_slice(board: str, args: dict[str, Any], active_root: Path, t0: float) -> dict[str, Any]:
    graph, wb_json, wb_sqlite, _ = _load_board(board)
    sl_kw = {
        "query": str(args.get("query") or ""), "component": args.get("component"),
        "division": args.get("division"), "stage": args.get("stage"),
        "schema": args.get("schema"), "kind": args.get("kind"),
        "include_code": bool(args.get("include_code", False)),
    }
    view = slice_lattice(graph, **sl_kw)
    wb_dir, has_q = active_root / ".whiteboard", bool(args.get("query", args.get("component")))
    if not view.get("matched_symbols") and not view.get("component_specs") and has_q and wb_dir.is_dir():
        for p in sorted(wb_dir.glob("*.json")):
            if (cand_b := p.stem) != board:
                cg, c_json, c_sql, _ = _load_board(cand_b)
                cv = slice_lattice(cg, **sl_kw)
                if next((_v for _v in (cv.get("matched_symbols"), cv.get("component_specs")) if _v), None):
                    board, graph, wb_json, wb_sqlite, view = cand_b, cg, c_json, c_sql, cv
                    break
    view, stale_sids, claimed_sids = apply_slice_stale_and_claim(
        graph, view, sl_kw, mark_stale=bool(args.get("mark_stale", False)), claim_by=args.get("claim_by")
    )
    if stale_sids or claimed_sids:
        _save_board(graph, wb_json, wb_sqlite)
    if bool(args.get("include_raw", False)):
        payload = {
            **view,
            **({"marked_stale_scopes": stale_sids} if stale_sids else {}),
            **({"claimed_scopes": claimed_sids} if claimed_sids else {}),
        }
    else:
        matched_clean = [{k: v for k, v in m.items() if k != "code"} for m in (view.get("matched_symbols") or [])]
        payload = {
            "status": view.get("status"), "board": board, "token_cost": view.get("token_cost"),
            "line_count": view.get("line_count"), "matched_symbols": matched_clean,
            "compact_view": view.get("compact_view"),
            **({"marked_stale_scopes": stale_sids} if stale_sids else {}),
            **({"claimed_scopes": claimed_sids} if claimed_sids else {}),
        }
    stale_sfx = f"\n[MARKED STALE]: {stale_sids}" if stale_sids else ""
    claim_sfx = f"\n[CLAIMED BY {args.get('claim_by')}]: {claimed_sids}" if claimed_sids else ""
    parts = ((view.get("compact_view") or "[EMPTY_SLICE]") + stale_sfx + claim_sfx).split("\n[CODE ")
    hdr_cv = "\n".join(
        ("@ SCHEMA " + ln[9:] if ln.startswith("# SCHEMA ") else ("@ FLOW" + ln[6:] if ln.startswith("> FLOW") else ln))
        for ln in parts[0].splitlines()
    )
    sl_txt = f"```text\n{hdr_cv}\n```" + "".join(f"\n```python\n# [CODE {p}\n```" for p in parts[1:])
    resp, nbytes = _format_tool_result(payload, text=None if bool(args.get("include_raw", False)) else sl_txt)
    dur_ms = (time.perf_counter() - t0) * 1000.0
    mod_sc = sorted(set(stale_sids) | set(claimed_sids)) or None
    record_flight_event(board, "slice", dur_ms, "OK", modified_scopes=mod_sc, response_bytes=nbytes)
    return resp


def _tool_packets_or_turn(name: str, board: str, args: dict[str, Any], t0: float) -> dict[str, Any]:
    if name == "packets":
        graph, wb_json, wb_sqlite, _ = _load_board(board)
        strip_w0 = args.get("wave") is None and not args.get("component") and not bool(args.get("include_raw", False))
        res, pkts, claimed = compile_and_claim_packets(graph, {**args, "strip_wave0": strip_w0})
        if claimed:
            _save_board(graph, wb_json, wb_sqlite)
        if bool(args.get("include_raw", False)):
            payload = {**res, "packets": pkts, **({"claimed_scopes": claimed} if claimed else {})}
        else:
            payload = {
                "status": res.get("status"), "is_proven": res.get("is_proven"),
                "ready_to_emit": res.get("ready_to_emit"),
                "packets": [{k: p.get(k) for k in COMPACT_PACKET_KEYS} for p in pkts],
                **({"claimed_scopes": claimed} if claimed else {}),
            }
        resp, nbytes = _format_tool_result(payload)
        st = "OK" if next((_v for _v in (res.get("ready_to_emit"), args.get("force")) if _v), None) else "REJECTED"
        dur_ms = (time.perf_counter() - t0) * 1000.0
        record_flight_event(
            board, name, dur_ms, st, decision=res.get("status"), modified_scopes=claimed or None, response_bytes=nbytes
        )
        return resp
    msg = str(args.get("message") or "").strip()
    if not msg:
        err_msg = "turn requires a non-empty 'message'"
        err_obj = {"type": "ValueError", "file": __file__, "line": 0, "message": err_msg, "traceback": ""}
        err_p = {"error": err_msg, "error_details": err_obj}
        resp, nbytes = _format_tool_result(err_p, is_error=True, text=f"[ERROR] {err_msg}")
        dur_ms = (time.perf_counter() - t0) * 1000.0
        record_flight_event(board, name, dur_ms, "REJECTED", response_bytes=nbytes, error=err_obj)
        return resp
    speaker = str(args.get("speaker") or "agent").strip().lower()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _execute_turn(board, speaker, msg, args.get("round_num"), args.get("profile"), args.get("config"))
    graph, _, _, _ = _load_board(board)
    eval_fn = _get_evaluate_lattice_proof()
    proof = eval_fn(graph) if eval_fn else graph.get("lattice_proof")
    cli_out = buf.getvalue().strip()
    out_p = {
        "return_code": rc, "board": board, "turn": graph.get("turn", 0),
        "lattice_proof": proof, "cli_output": cli_out,
    }
    resp, nbytes = _format_tool_result(out_p, is_error=(rc != 0), text=cli_out)
    dur_ms = (time.perf_counter() - t0) * 1000.0
    st_turn = "OK" if rc == 0 else "REJECTED"
    record_flight_event(board, name, dur_ms, st_turn, decision=(proof or {}).get("status"), response_bytes=nbytes)
    return resp


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
            board, f"git_sync:{mode}", (time.perf_counter() - t0) * 1000.0, "OK" if allow else "REJECTED",
            decision=payload.get("decision"), violations=payload.get("violations"),
            modified_scopes=mod_sc or payload.get("drifted_scopes"),
            stale_cascaded_scopes=payload.get("cascaded_stale_scopes"), response_bytes=nbytes,
        )
        return resp
    comp, new_st = str(args.get("component") or "").strip(), str(args.get("new_status") or "").strip().upper()
    graph, wb_json, wb_sqlite, _ = _load_board(board)
    if new_st == "VERIFIED" and can_transition_to_verified(new_st == "VERIFIED", bool(comp), bool(graph.get("scopes"))):
        _verify_component_files(graph, comp)
    snode = apply_status_and_claim(graph, comp, new_st or None, opts=args)
    _save_board(graph, wb_json, wb_sqlite)
    st_txt = (
        f"[STATUS '{board}'] {comp} -> status={snode.get('status')} claimed_by={snode.get('claimed_by')} "
        f"build_tag={snode.get('build_tag')} core_tag={snode.get('core_tag')} "
        f"failure_diagnostic={snode.get('failure_diagnostic')!r}"
    )
    out_p = {
        "board": board, "component": comp, "status": snode.get("status"), "claimed_by": snode.get("claimed_by"),
        "build_tag": snode.get("build_tag"), "core_tag": snode.get("core_tag"),
        "failure_diagnostic": snode.get("failure_diagnostic"),
    }
    resp, nbytes = _format_tool_result(out_p, text=st_txt)
    dur_ms = (time.perf_counter() - t0) * 1000.0
    record_flight_event(
        board, name, dur_ms, "OK", decision=snode.get("status"), modified_scopes=[comp], response_bytes=nbytes
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

    with _board_lock(board):
        prev_root, prev_env = _cli.REPO_ROOT, os.environ.get("BOWTHRUSTER_PROJECT_ROOT")
        active_root = _resolve_project_root(board, args.get("project_root"), args.get("message"))
        _cli.REPO_ROOT = active_root
        os.environ["BOWTHRUSTER_PROJECT_ROOT"] = str(active_root)
        try:
            if name == "migrate":
                from bowthruster.whiteboard.migrations import format_migration_report, migrate_repo_boards

                m_pay = migrate_repo_boards(
                    active_root, board=board, migrate_all=bool(args.get("migrate_all")), dry_run=bool(args.get("dry_run"))
                )
                resp, nbytes = _format_tool_result(m_pay, text=format_migration_report(m_pay))
                record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes)
                return resp
            if name == "bootstrap":
                from bowthruster.bootstrap import bootstrap_repository, format_bootstrap_report

                graph, wb_json, wb_sqlite, _ = _load_board(board)
                b_pay = bootstrap_repository(active_root, board, graph, component=args.get("component"))
                _save_board(graph, wb_json, wb_sqlite)
                resp, nbytes = _format_tool_result(b_pay, text=format_bootstrap_report(b_pay))
                dur_ms = (time.perf_counter() - t0) * 1000.0
                record_flight_event(board, name, dur_ms, "OK", decision=b_pay.get("status"), response_bytes=nbytes)
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


def _max_pkg_mtime() -> float:
    py_files = (BOWTHRUSTER_HOME / "bowthruster").rglob("*.py")
    return max((p.stat().st_mtime for p in py_files if p.is_file()), default=0.0)


_LAST_MTIME: float = _max_pkg_mtime()


def _hot_reload_if_changed() -> None:
    global _LAST_MTIME
    import importlib

    cur = _max_pkg_mtime()
    if _LAST_MTIME and cur > _LAST_MTIME:
        _LAST_MTIME = cur
        pkgs = (
            "bowthruster.nlp", "bowthruster.types", "bowthruster.graph_core", "bowthruster.regiment",
            "bowthruster.ai_memory", "bowthruster.whiteboard", "bowthruster.the_table", "bowthruster.the_executive",
        )
        for pkg in pkgs:
            submods = [
                k for k in list(sys.modules)
                if k.startswith(f"{pkg}.") and k != "bowthruster.graph_core.sqlite_store"
            ]
            for mname in sorted(submods):
                mod = sys.modules.get(mname)
                if mod is not None and getattr(mod, "__file__", None):
                    try:
                        importlib.reload(mod)
                    except Exception:
                        pass
            if (pmod := sys.modules.get(pkg)) is not None:
                try:
                    importlib.reload(pmod)
                except Exception:
                    pass
        if (cmod := sys.modules.get("bowthruster.cli")) is not None:
            try:
                importlib.reload(cmod)
            except Exception:
                pass
    _LAST_MTIME = cur


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
        return {"jsonrpc": "2.0", "id": req.id, "error": {"code": -32600, "message": "Invalid Request: missing method"}}
    if req.method.startswith("notifications/"):
        return None
    if req.method == "ping":
        return {"jsonrpc": "2.0", "id": req.id, "result": {}}
    if req.method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req.id,
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
    return {"jsonrpc": "2.0", "id": req.id, "error": {"code": -32601, "message": f"Method not found: {req.method}"}}


def main() -> int:
    """Runs the stdio JSON-RPC 2.0 MCP server loop."""
    for raw_line in sys.stdin:
        if not (line := raw_line.strip()):
            continue
        try:
            req_obj = json.loads(line)
        except json.JSONDecodeError as exc:
            err_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        if (resp := handle_mcp_request(req_obj)) is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
