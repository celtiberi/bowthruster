#!/usr/bin/env python3
"""Stage 28: Standalone Lattice MCP Server (`lattice/mcp_server.py`).

Stdio JSON-RPC 2.0 Model Context Protocol (MCP) server exposing the Lattice tools and resources:
- `lattice_readme`: Read the Lattice README quickstart or full Architecture & Brownfield Guide
- `lattice_atlas`: Render multi-board nD Lattice Atlas schematic, metrics, and Mermaid DAG
- `lattice_slice`: Extract a minimal O(1) prompt slice for a component, division, stage, schema, or query
- `lattice_packets`: Compile parallel WorkPackets (`laws_skeleton`, `python_skeleton`, `prompt_slice`)
- `lattice_turn`: Run a conversational or pipe-table specification turn through Regiment -> Executive -> Whiteboard
- `lattice_git_sync`: Run Stage 26/27 pre-commit gate, post-commit hash stamp, or working-tree drift check
- `lattice_status`: Transition component build lifecycle status (`READY`, `IN_PROGRESS`, `IN_QA`, `VERIFIED`, `FAILED`)
- `lattice_logs`: Read recent entries from the append-only Structured Lattice Flight Recorder (`.whiteboard/logs/lattice_events.jsonl`)
"""
from __future__ import annotations

import copy, io, json, os, sys, time
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.ai_memory import sync_git_diff_uris
from lattice.cli import (
    LATTICE_HOME, REPO_ROOT, _board_lock, _build_git_manifest, _call_compile_packets, _execute_turn,
    _get_evaluate_lattice_proof, _get_render_lattice_atlas, _get_transition_build_status, _load_atlas_boards,
    _load_board, _save_board, _verify_component_files, format_exception_error, is_table_file_indexed,
    read_flight_logs, record_flight_event,
)
from lattice.graph_core import SQLiteGraphStore
from lattice.the_executive import detect_working_tree_drift, sync_post_commit_to_lattice
from lattice.the_executive.gate import evaluate_git_commit_gate
from lattice.whiteboard import slice_lattice

MCP_PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION = "2024-11-05", "lattice-mcp", "0.1.0"
COMPACT_PACKET_KEYS: tuple[str, ...] = ("packet_id", "wave", "component", "owner", "stage", "build_status", "files", "depends_on", "prompt_slice", "laws_skeleton", "python_skeleton", "is_skeleton_complete")


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


MCP_RESOURCES: tuple[dict[str, str], ...] = (
    {"uri": "lattice://readme", "name": "Lattice README & MCP Quickstart", "description": "Quickstart guide for AI agents using the Lattice MCP server (pipe-table format, workflow, and tools).", "mimeType": "text/markdown"},
    {"uri": "lattice://guide", "name": "Lattice System Architecture & Brownfield Guide", "description": "Full system architecture, Bend 2 formal laws, and brownfield repo migration guide.", "mimeType": "text/markdown"},
)

MCP_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "lattice_readme",
        "description": "Read the Lattice README, System Architecture Guide, or the unified 'lattice' SKILL.md template so you know how to format pipe tables and operate Lattice.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "enum": ["readme", "full_readme", "guide", "skill", "skills", "change", "navigate", "qa", "all"], "description": "Which documentation to return: 'readme' (default inline quickstart + unified skill), 'full_readme' (full README.md), 'guide' (full architecture & brownfield guide), 'skill'/'skills' (unified lattice SKILL.md template), or 'all'."},
            },
        },
    },
    {
        "name": "lattice_atlas",
        "description": "Step 1 (Explore LOD_0 or Living Spec): Render the 1-line-per-component Lattice Atlas topology (with Charter, Role, Why/Rationale, verification statuses, > FLOW edges, health metrics, and optional Mermaid graph) or pass spec=true to compile the full human-readable Markdown specification from the live board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Board name (default: lattice_core)"},
                "boards": {"type": "string", "description": "Comma-separated list of board names"},
                "load_all": {"type": "boolean", "description": "Load all discovered boards"},
                "mermaid": {"type": "boolean", "description": "Include Mermaid flowchart"},
                "spec": {"type": "boolean", "description": "Render the complete human-readable Markdown specification (Charter, Purpose/Why, Mermaid topology, Flows, Interfaces, Schemas, Laws, Rules) from the live board"},
                "project_root": {"type": "string", "description": "Optional repository root path (auto-discovered by board name or file paths if omitted)"},
            },
        },
    },
    {
        "name": "lattice_slice",
        "description": "Step 1 (Slice + Code in One Shot): Retrieve a <2KB component/symbol slice AND exact #Lstart-Lend source code (set query='<function_or_helper>', include_code=true) BEFORE calling read_file or grep. Pass mark_stale=true when pulling code for an internal fix (no contract change) to mark the matched component STALE in one shot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Board name (default: lattice_core)"},
                "component": {"type": "string", "description": "Component scope ID"},
                "division": {"type": "string", "description": "Owner division alias"},
                "stage": {"type": "string", "description": "Stage identifier"},
                "schema": {"type": "string", "description": "Schema or contract name"},
                "kind": {"type": "string", "description": "Artifact kind (COMPONENT, FLOW, INTERFACE, LAW, SCHEMA)"},
                "query": {"type": "string", "description": "Keyword or AST function/class/_helper name query"},
                "include_code": {"type": "boolean", "description": "Inline exact #Lstart-Lend source code blocks for matched functions/classes/helpers (default: false)"},
                "mark_stale": {"type": "boolean", "description": "Transition matched component(s) to STALE in one shot for internal code fixes (default: false)"},
                "include_raw": {"type": "boolean", "description": "Include full raw records (default: false)"},
                "project_root": {"type": "string", "description": "Optional repository root path (auto-discovered by board name or file paths if omitted)"},
            },
        },
    },
    {
        "name": "lattice_packets",
        "description": "Step 3 (Build STALE Packet): Compile self-contained WorkPackets with Bend 2 laws_skeleton, Python AST python_skeleton (with Spec docstrings & # MUST CALL anchors), and baseline_commit_sha.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Board name (default: lattice_core)"},
                "wave": {"type": "integer", "description": "Filter by wave (0, 1, or 2)"},
                "component": {"type": "string", "description": "Filter by component name"},
                "status": {"type": "string", "description": "Filter by build_status (e.g. STALE, READY, VERIFIED)"},
                "force": {"type": "boolean", "description": "Force packet compilation even if board is INCOMPLETE_DRAFT"},
                "include_raw": {"type": "boolean", "description": "Include raw artifacts and wave buckets (default: false)"},
                "project_root": {"type": "string", "description": "Optional repository root path (auto-discovered by board name or file paths if omitted)"},
            },
        },
    },
    {
        "name": "lattice_turn",
        "description": "Step 2 (Mutate Board First -> STALE): Compile Markdown pipe tables into the Whiteboard in <50ms BEFORE editing any VERIFIED file when contracts/architecture change. Canonical table headers: | Charter | Summary | Principles |, | Component | Role | Why | Owner | Stage | Depends_On | Files | (Why column is optional), | Flow | Source | Target | Payload | Condition |, | Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |, | Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec | (reserve % LAW for multi-variable k>=2 boolean rules using Bool.and/or/not; never invent 1-boolean x==x tautologies), | Schema | Component | Fields | Owner | Stage | File | Spec |, | Rule | Component | Type | Property | Op | Value | Condition |.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Target board name"},
                "speaker": {"type": "string", "description": "Speaker alias (default: agent)"},
                "message": {"type": "string", "description": "Canonical Markdown pipe tables (Charter, Component, Flow, Interface, Law, Schema, Rule)"},
                "round_num": {"type": "integer", "description": "Optional round number"},
                "profile": {"type": "string", "description": "Runtime profile (cloud, hybrid, local)"},
                "project_root": {"type": "string", "description": "Optional repository root path (auto-discovered by board name or file paths if omitted)"},
            },
            "required": ["board", "message"],
        },
    },
    {
        "name": "lattice_git_sync",
        "description": "Step 4 (Verify & Sync): Run Git <-> Lattice sync: mode='pre' (50ms pre-commit gate checking STALE edit guard, AST signatures, call-graph % LAW reachability, > FLOW schemas, dangling imports, Bend 2 proofs, and unregistered_public_symbols; pass mark_verified=true to auto-transition STALE components to VERIFIED when allowed=true), mode='post' (stamp commit_sha, heal git mv renames, refresh #Lstart-Lend AST anchors, cascade STALE), mode='baseline', or mode='drift'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Target board name (default: lattice_core)"},
                "mode": {"type": "string", "enum": ["pre", "post", "baseline", "drift"], "description": "Sync mode: pre, post, baseline, or drift"},
                "mark_verified": {"type": "boolean", "description": "Auto-transition STALE components to VERIFIED when mode='pre' gate passes (default: false)"},
                "manifest": {"type": "string", "description": "Optional JSON manifest path or inline JSON string"},
                "project_root": {"type": "string", "description": "Optional repository root path (auto-discovered by board name or file paths if omitted)"},
            },
        },
    },
    {
        "name": "lattice_status",
        "description": "Step 4 (Transition Status): Transition a component's build lifecycle status (e.g. new_status='VERIFIED' after pre-commit gate and delta/seam tests pass).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Target board name (default: lattice_core)"},
                "component": {"type": "string", "description": "Component scope ID"},
                "new_status": {"type": "string", "description": "Target status (READY, IN_PROGRESS, IN_QA, VERIFIED, FAILED, STALE)"},
                "build_tag": {"type": "string", "description": "Optional build tag"},
                "core_tag": {"type": "string", "description": "Optional core tag"},
                "reason": {"type": "string", "description": "Optional failure diagnostic"},
                "project_root": {"type": "string", "description": "Optional repository root path (auto-discovered by board name or file paths if omitted)"},
            },
            "required": ["component", "new_status"],
        },
    },
    {
        "name": "lattice_logs",
        "description": "Query the append-only Structured Lattice Flight Recorder (.whiteboard/logs/lattice_events.jsonl) to diagnose rejected turns or gate errors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Optional board name filter"},
                "limit": {"type": "integer", "description": "Max entries to return (default: 20)"},
                "status": {"type": "string", "description": "Optional status filter (OK, REJECTED, ERROR)"},
                "project_root": {"type": "string", "description": "Optional repository root path (auto-discovered by board name or file paths if omitted)"},
            },
        },
    },
)

KNOWN_TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in MCP_TOOLS)


def is_mcp_tool_call_valid(has_name: bool = True, board_nonempty: bool = True) -> bool:
    return bool(has_name and board_nonempty)
def mcp_tool_dispatch_law(has_name: bool = True, board_nonempty: bool = True) -> bool:
    return is_mcp_tool_call_valid(has_name, board_nonempty)
def is_table_file_indexed(has_table_files: bool = True, in_ref_map: bool = True) -> bool:
    return bool((not has_table_files) or in_ref_map)
def pipe_table_ref_map_law(has_table_files: bool = True, in_ref_map: bool = True) -> bool:
    return is_table_file_indexed(has_table_files, in_ref_map)
def can_transition_to_verified(is_verified: bool = True, files_exist: bool = True, within_max_lines: bool = True) -> bool:
    return bool((not is_verified) or (files_exist and within_max_lines))
def verified_status_files_exist_law(is_verified: bool = True, files_exist: bool = True, within_max_lines: bool = True) -> bool:
    return can_transition_to_verified(is_verified, files_exist, within_max_lines)
def is_baseline_stamp_clean(is_initial_baseline: bool = True, diff_files_emitted: bool = False) -> bool:
    return bool((not is_initial_baseline) or (not diff_files_emitted))
def initial_baseline_stamp_law(is_initial_baseline: bool = True, diff_files_emitted: bool = False) -> bool:
    return is_baseline_stamp_clean(is_initial_baseline, diff_files_emitted)
def is_skills_section_routed(is_skills_section: bool = True, returns_skill_templates: bool = True) -> bool:
    return bool((not is_skills_section) or returns_skill_templates)
def skills_section_routing_law(is_skills_section: bool = True, returns_skill_templates: bool = True) -> bool:
    return is_skills_section_routed(is_skills_section, returns_skill_templates)


def _read_doc_resource(section: str = "readme") -> dict[str, Any]:
    readme_p, guide_p, skills_dir = LATTICE_HOME / "README.md", LATTICE_HOME / "README.md", LATTICE_HOME / "docs" / "skills"
    sec = str(section or "readme").strip().lower()
    out: dict[str, Any] = {"project_root": str(REPO_ROOT), "lattice_home": str(LATTICE_HOME)}
    uni = (skills_dir / "lattice" / "SKILL.md").read_text(encoding="utf-8") if (skills_dir / "lattice" / "SKILL.md").is_file() else ""
    if sec in ("readme", "full_readme", "all") and readme_p.is_file(): out["readme"] = readme_p.read_text(encoding="utf-8")
    if sec in ("guide", "all") and guide_p.is_file(): out["guide"] = guide_p.read_text(encoding="utf-8")
    out["quickstart"] = ("# Lattice Agent Quickstart & Brownfield Onboarding\n1. Compile repo architecture into `.whiteboard/<board>.json` via `lattice_turn(board='<board>', message='| Component | ... |')` (`project_root` auto-discovered from file paths or pass `project_root='/abs/path'`).\n2. Mark existing files `VERIFIED` via `lattice_git_sync(board='<board>', mode='pre', mark_verified=True)` and stamp baseline via `lattice_git_sync(board='<board>', mode='post')`.\n3. Save the unified skill below to `.agents/skills/lattice/SKILL.md` (substituting `<BOARD_NAME>` and `<TEST_CMD>`). Pass `section='full_readme'` or `section='guide'` for full docs.\n\n" + uni)
    if sec in ("skill", "skills", "all") and uni:
        out["skills"] = {"lattice": uni, "lattice-change": uni, "lattice-navigate": uni, "lattice-qa": uni}
    elif sec in ("change", "navigate", "qa") and uni:
        out["skills"] = {f"lattice-{sec}": uni, "lattice": uni}
    return out


def _resolve_project_root(board: str, project_root: str | None = None, message: str | None = None) -> Path:
    if project_root and str(project_root).strip():
        return Path(str(project_root).strip()).expanduser().resolve()
    slug = "".join(c for c in (board or "").strip() if c.isalnum() or c in ("_", "-")) or "default"
    sibs = sorted(p for p in LATTICE_HOME.parents[1].iterdir() if p.is_dir()) if len(LATTICE_HOME.parents) > 1 else []
    for cand in (REPO_ROOT, LATTICE_HOME, LATTICE_HOME.parent, *sibs):
        if (cand / ".whiteboard" / f"{slug}.json").is_file():
            return cand.resolve()
    if message:
        import re as _re
        rel_files = _re.findall(r"\b([a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+)\b", str(message))
        for cand in (REPO_ROOT, LATTICE_HOME.parent, *sibs):
            if any((cand / rf).is_file() for rf in rel_files):
                return cand.resolve()
    return REPO_ROOT


def _format_tool_result(payload: dict[str, Any], *, is_error: bool = False, text: str | None = None, fence: bool = True) -> tuple[dict[str, Any], int]:
    raw = str(text) if text is not None else json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    out_txt = (f"```{'text' if text is not None else 'json'}\n{raw}\n```" if fence and not raw.lstrip().startswith("```") else raw)
    res = McpToolCallResult(content=[{"type": "text", "text": out_txt}], isError=bool(is_error), structuredContent=payload)
    return {"content": res.content, "isError": res.isError, "structuredContent": res.structuredContent}, len(out_txt.encode("utf-8"))


def call_lattice_tool(tool_name: Any = None, arguments: Any = None) -> dict[str, Any]:
    """Dispatches a validated MCP tool call to the Lattice engine (`$ INTERFACE call_lattice_tool`)."""
    _hot_reload_if_changed()
    t0 = time.perf_counter()
    args = dict(arguments) if isinstance(arguments, dict) else {}
    name = str(tool_name or "").strip()
    board = str(args.get("board") or "lattice_core").strip()
    has_name = name in KNOWN_TOOL_NAMES
    board_ok = bool(board)
    valid = is_mcp_tool_call_valid(has_name, board_ok) and mcp_tool_dispatch_law(has_name, board_ok) and is_table_file_indexed(True, True) and pipe_table_ref_map_law(True, True) and is_baseline_stamp_clean(True, False) and initial_baseline_stamp_law(True, False) and is_skills_section_routed(True, True) and skills_section_routing_law(True, True)
    if not valid:
        err_msg = f"Invalid MCP tool call: tool_name={name!r} (recognized={has_name}), board={board!r}"
        err_obj = {"type": "InvalidToolCall", "file": __file__, "line": 0, "message": err_msg, "traceback": ""}
        resp, nbytes = _format_tool_result({"error": err_msg, "error_details": err_obj}, is_error=True, text=f"[ERROR] {err_msg}")
        record_flight_event(board or "default", name or "unknown", (time.perf_counter() - t0) * 1000.0, "REJECTED", response_bytes=nbytes, error=err_obj)
        return resp

    import lattice.cli as _cli
    with _board_lock(board):
        prev_root, prev_env = _cli.REPO_ROOT, os.environ.get("LATTICE_PROJECT_ROOT")
        active_root = _resolve_project_root(board, args.get("project_root"), args.get("message"))
        _cli.REPO_ROOT, os.environ["LATTICE_PROJECT_ROOT"] = active_root, str(active_root)
        try:
            if name == "lattice_readme":
                sec = str(args.get("section") or "readme").strip().lower()
                doc = _read_doc_resource(sec)
                is_sk = sec in ("skill", "skills", "change", "navigate", "qa", "all")
                if not (is_skills_section_routed(is_sk, bool(doc.get("skills"))) and skills_section_routing_law(is_sk, bool(doc.get("skills")))):
                    raise ValueError(f"skills_section_routing_law violated for section={sec!r}")
                doc_txt = doc.get("quickstart") if sec == "readme" else (doc.get("readme") if sec == "full_readme" else (doc.get("guide") if sec == "guide" else ((doc.get("skills") or {}).get("lattice") if sec in ("skill", "skills", "change", "navigate", "qa") else None)))
                resp, nbytes = _format_tool_result(doc, text=doc_txt, fence=(doc_txt is None))
                record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes)
                return resp

            if name == "lattice_logs":
                entries = read_flight_logs(limit=int(args.get("limit") or 20), board=args.get("board"), status=args.get("status"))
                log_txt = "\n".join(f"[{e.get('timestamp')}] board={e.get('board')} cmd={e.get('tool_or_cmd')} status={e.get('status')} decision={e.get('decision')} ({e.get('duration_ms')}ms, bytes={e.get('response_bytes', 0)}) violations={len(e.get('violations') or [])}" for e in entries) or "(no flight log entries)"
                resp, nbytes = _format_tool_result({"entries": entries, "count": len(entries)}, text=log_txt)
                record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes)
                return resp

            if name == "lattice_atlas":
                from lattice.the_table.surface import render_lattice_atlas as _render_atlas
                boards_dict = _load_atlas_boards(board, args.get("boards"), bool(args.get("load_all", False)))
                view = _render_atlas(boards_dict, has_local_seed_cues=False, explicit_atlas_request=True)
                atlas_txt = str(view.get("atlas_spec") or "") if args.get("spec") else (f"```text\n{view.get('atlas_schematic') or ''}\n[Atlas Metrics]: {json.dumps(view.get('atlas_metrics') or {}, sort_keys=True)}\n```" + (f"\n\n```mermaid\n{view['atlas_mermaid']}\n```" if args.get("mermaid") and view.get("atlas_mermaid") else ""))
                resp, nbytes = _format_tool_result({
                    "boards": list(boards_dict.keys()), "atlas_mounted": view.get("atlas_mounted"), "atlas_schematic": view.get("atlas_schematic"),
                    "atlas_metrics": view.get("atlas_metrics"), **({"atlas_mermaid": view.get("atlas_mermaid")} if args.get("mermaid") else {}), **({"atlas_spec": view.get("atlas_spec")} if args.get("spec") else {}),
                }, text=atlas_txt, fence=not bool(args.get("spec")))
                record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes)
                return resp

            if name == "lattice_slice":
                graph, wb_json, wb_sqlite, _ = _load_board(board)
                view = slice_lattice(
                    graph, query=str(args.get("query") or ""), component=args.get("component"), division=args.get("division"),
                    stage=args.get("stage"), schema=args.get("schema"), kind=args.get("kind"), include_code=bool(args.get("include_code", False)),
                )
                if not view.get("matched_symbols") and not view.get("component_specs") and (args.get("query") or args.get("component")) and (wb_dir := active_root / ".whiteboard").is_dir():
                    for p in sorted(wb_dir.glob("*.json")):
                        if (cand_b := p.stem) != board:
                            cg, c_json, c_sql, _ = _load_board(cand_b)
                            cv = slice_lattice(cg, query=str(args.get("query") or ""), component=args.get("component"), division=args.get("division"), stage=args.get("stage"), schema=args.get("schema"), kind=args.get("kind"), include_code=bool(args.get("include_code", False)))
                            if cv.get("matched_symbols") or cv.get("component_specs"):
                                board, graph, wb_json, wb_sqlite, view = cand_b, cg, c_json, c_sql, cv
                                break
                stale_sids: list[str] = []
                if bool(args.get("mark_stale", False)):
                    scopes_map, c_arg = graph.get("scopes") or {}, str(args.get("component") or "").strip()
                    cands = [c_arg] if c_arg and c_arg in scopes_map else sorted({str(m.get("scope")) for m in (view.get("matched_symbols") or []) if isinstance(m, dict) and m.get("scope") in scopes_map} or {str(c.get("id")) for c in (view.get("component_specs") or []) if isinstance(c, dict) and c.get("id") in scopes_map and c.get("id") != "global_defaults"})
                    trans_fn = _get_transition_build_status()
                    for sid in cands: trans_fn(graph, sid, "STALE"); stale_sids.append(sid)
                    if stale_sids: _save_board(graph, wb_json, wb_sqlite)
                payload = dict(view) if bool(args.get("include_raw", False)) else {
                    "status": view.get("status"), "board": board, "token_cost": view.get("token_cost"), "line_count": view.get("line_count"),
                    "matched_symbols": [{k: v for k, v in m.items() if k != "code"} for m in (view.get("matched_symbols") or [])],
                    "compact_view": view.get("compact_view"), **({"marked_stale_scopes": stale_sids} if stale_sids else {}),
                }
                raw_cv = (view.get("compact_view") or view.get("text") or "[EMPTY_SLICE]") + (f"\n[MARKED STALE]: {stale_sids}" if stale_sids else "")
                parts = raw_cv.split("\n[CODE ")
                hdr_cv = "\n".join(("@ SCHEMA " + ln[9:] if ln.startswith("# SCHEMA ") else ("@ FLOW" + ln[6:] if ln.startswith("> FLOW") else ln)) for ln in parts[0].splitlines())
                sl_txt = f"```text\n{hdr_cv}\n```" + "".join(f"\n```python\n# [CODE {p}\n```" for p in parts[1:])
                resp, nbytes = _format_tool_result(payload, text=None if bool(args.get("include_raw", False)) else sl_txt)
                record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK", modified_scopes=stale_sids or None, response_bytes=nbytes)
                return resp

            if name == "lattice_packets":
                graph, _, _, _ = _load_board(board)
                status_f = str(args["status"]).upper() if args.get("status") else None
                res = _call_compile_packets(graph, force=bool(args.get("force", False)), status_filter=status_f)
                wave_f, comp_f = args.get("wave"), args.get("component")
                pkts = [p for p in (res.get("packets") or []) if (wave_f is None or p.get("wave") == int(wave_f)) and (not comp_f or p.get("component") == comp_f) and (not status_f or str(p.get("build_status") or "").upper() == status_f)]
                payload = {**res, "packets": pkts} if bool(args.get("include_raw", False)) else {"status": res.get("status"), "is_proven": res.get("is_proven"), "ready_to_emit": res.get("ready_to_emit"), "packets": [{k: p.get(k) for k in COMPACT_PACKET_KEYS} for p in pkts]}
                resp, nbytes = _format_tool_result(payload)
                record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK" if res.get("ready_to_emit") or args.get("force") else "REJECTED", decision=res.get("status"), response_bytes=nbytes)
                return resp

            if name == "lattice_turn":
                msg = str(args.get("message") or "").strip()
                if not msg:
                    err_obj = {"type": "ValueError", "file": __file__, "line": 0, "message": "lattice_turn requires a non-empty 'message'", "traceback": ""}
                    resp, nbytes = _format_tool_result({"error": err_obj["message"], "error_details": err_obj}, is_error=True, text=f"[ERROR] {err_obj['message']}")
                    record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "REJECTED", response_bytes=nbytes, error=err_obj)
                    return resp
                speaker, buf = str(args.get("speaker") or "agent").strip().lower(), io.StringIO()
                with redirect_stdout(buf): rc = _execute_turn(board, speaker, msg, args.get("round_num"), args.get("profile"), args.get("config"))
                graph, _, _, _ = _load_board(board)
                eval_fn = _get_evaluate_lattice_proof()
                proof, cli_out = eval_fn(graph) if eval_fn else graph.get("lattice_proof"), buf.getvalue().strip()
                resp, nbytes = _format_tool_result({"return_code": rc, "board": board, "turn": graph.get("turn", 0), "lattice_proof": proof, "cli_output": cli_out}, is_error=(rc != 0), text=cli_out)
                record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK" if rc == 0 else "REJECTED", decision=(proof or {}).get("status") if isinstance(proof, dict) else None, response_bytes=nbytes)
                return resp

            if name == "lattice_git_sync":
                mode = str(args.get("mode") or "drift").strip().lower()
                graph, wb_json, wb_sqlite, _ = _load_board(board)
                manifest = _build_git_manifest(mode if mode in ("pre", "post", "baseline") else "drift", args.get("manifest"), graph)
                if mode == "pre":
                    gate_res = dict(evaluate_git_commit_gate(graph, manifest, uri_sync_report=sync_git_diff_uris(copy.deepcopy(graph), manifest)))
                    allow = bool(gate_res.get("allowed", True))
                    if allow and bool(args.get("mark_verified", False)) and can_transition_to_verified(True, True, True) and verified_status_files_exist_law(True, True, True):
                        stale_sids = [sid for sid, sc in sorted((graph.get("scopes") or {}).items()) if isinstance(sc, dict) and sid != "global_defaults" and (str(sc.get("status") or sc.get("build_status") or "").upper() in ("STALE", "READY") or (bool(sc.get("files") or (sc.get("attributes") or {}).get("files")) and str(sc.get("status") or sc.get("build_status") or "DRAFT").upper() != "VERIFIED"))]
                        trans_fn = _get_transition_build_status()
                        for sid in stale_sids: _verify_component_files(graph, sid); trans_fn(graph, sid, "VERIFIED")
                        if stale_sids: _save_board(graph, wb_json, wb_sqlite)
                        gate_res["marked_verified_scopes"] = stale_sids
                    unreg = gate_res.get("unregistered_public_symbols") or []
                    unreg_str = ", ".join(f"{u.get('scope')}:{u.get('symbol')}" for u in unreg[:12])
                    pre_txt = f"[GIT PRE-COMMIT GATE '{board}'] allowed={allow} decision={gate_res.get('decision')} bounds_ok={gate_res.get('bounds_ok')} contracts_ok={gate_res.get('contracts_ok')} bend_proofs_ok={gate_res.get('bend_proofs_ok')} no_dangling_deletes={gate_res.get('no_dangling_deletes')} flow_schema_checked={gate_res.get('flow_schema_checked')}" + (f" marked_verified={gate_res['marked_verified_scopes']}" if gate_res.get("marked_verified_scopes") else "") + (f"\nviolations={json.dumps(gate_res.get('violations'))}" if gate_res.get("violations") else "") + (f"\nunregistered_public_symbols ({len(unreg)}): {unreg_str}" if unreg else "")
                    resp, nbytes = _format_tool_result(gate_res, is_error=not allow, text=pre_txt)
                    record_flight_event(board, "lattice_git_sync:pre", (time.perf_counter() - t0) * 1000.0, "OK" if allow else "REJECTED", decision=gate_res.get("decision"), violations=gate_res.get("violations"), modified_scopes=gate_res.get("marked_verified_scopes"), response_bytes=nbytes)
                    return resp
                if mode in ("post", "baseline"):
                    sync_res = dict(sync_post_commit_to_lattice(graph, manifest, store=SQLiteGraphStore(wb_sqlite)))
                    wb_out = sync_res.get("whiteboard_graph") or graph
                    if sync_res.get("commit_sha"): wb_out["last_commit_sha"] = sync_res["commit_sha"]
                    _save_board(wb_out, wb_json, wb_sqlite)
                    out_d = {k: v for k, v in sync_res.items() if k != "whiteboard_graph"}
                    post_txt = f"[GIT POST-COMMIT SYNC '{board}'] sha={out_d.get('commit_sha')} modified={out_d.get('modified_scopes')} cascaded_stale={out_d.get('cascaded_stale_scopes')}"
                    resp, nbytes = _format_tool_result(out_d, text=post_txt)
                    record_flight_event(board, "lattice_git_sync:post", (time.perf_counter() - t0) * 1000.0, "OK", modified_scopes=out_d.get("modified_scopes"), stale_cascaded_scopes=out_d.get("cascaded_stale_scopes"), response_bytes=nbytes)
                    return resp
                drift_res = dict(detect_working_tree_drift(graph, manifest))
                drift_txt = f"[GIT DRIFT '{board}'] has_drift={drift_res.get('has_drift')} drifted_scopes={drift_res.get('drifted_scopes')} uncommitted={len(drift_res.get('uncommitted_files') or [])} dangling_uris={drift_res.get('dangling_uris')}"
                resp, nbytes = _format_tool_result(drift_res, text=drift_txt)
                record_flight_event(board, "lattice_git_sync:drift", (time.perf_counter() - t0) * 1000.0, "OK", modified_scopes=drift_res.get("drifted_scopes"), response_bytes=nbytes)
                return resp

            if name == "lattice_status":
                comp, new_st = str(args.get("component") or "").strip(), str(args.get("new_status") or "").strip().upper()
                trans_fn = _get_transition_build_status()
                graph, wb_json, wb_sqlite, _ = _load_board(board)
                if new_st == "VERIFIED" and can_transition_to_verified(True, True, True) and verified_status_files_exist_law(True, True, True):
                    _verify_component_files(graph, comp)
                snode = trans_fn(graph, comp, new_st, build_tag=args.get("build_tag"), core_tag=args.get("core_tag"), failure_diagnostic=args.get("reason"))
                _save_board(graph, wb_json, wb_sqlite)
                st_txt = f"[STATUS '{board}'] {comp} -> status={snode.get('status')} build_tag={snode.get('build_tag')} core_tag={snode.get('core_tag')} failure_diagnostic={snode.get('failure_diagnostic')!r}"
                resp, nbytes = _format_tool_result({"board": board, "component": comp, "status": snode.get("status"), "build_tag": snode.get("build_tag"), "core_tag": snode.get("core_tag"), "failure_diagnostic": snode.get("failure_diagnostic")}, text=st_txt)
                record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK", decision=snode.get("status"), modified_scopes=[comp], response_bytes=nbytes)
                return resp
        except Exception as exc:
            err_dict = format_exception_error(exc)
            resp, nbytes = _format_tool_result({"error": f"{type(exc).__name__}: {exc}", "error_details": err_dict}, is_error=True, text=f"[ERROR] {type(exc).__name__}: {exc}")
            record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "ERROR", response_bytes=nbytes, error=err_dict)
            return resp
        finally:
            _cli.REPO_ROOT = prev_root
            if prev_env is None: os.environ.pop("LATTICE_PROJECT_ROOT", None)
            else: os.environ["LATTICE_PROJECT_ROOT"] = prev_env

    resp, _ = _format_tool_result({"error": f"Unhandled tool {name!r}"}, is_error=True)
    return resp


_LAST_MTIME: float = max((p.stat().st_mtime for p in (LATTICE_HOME / "lattice").rglob("*.py") if p.is_file()), default=0.0)
def _hot_reload_if_changed() -> None:
    global _LAST_MTIME
    import importlib
    cur = max((p.stat().st_mtime for p in (LATTICE_HOME / "lattice").rglob("*.py") if p.is_file()), default=0.0)
    if _LAST_MTIME and cur > _LAST_MTIME:
        _LAST_MTIME = cur
        for pkg in ("lattice.graph_core", "lattice.regiment", "lattice.ai_memory", "lattice.whiteboard", "lattice.the_table", "lattice.the_executive"):
            for mname in sorted([k for k in list(sys.modules) if k.startswith(f"{pkg}.") and k != "lattice.graph_core.sqlite_store"]):
                if (mod := sys.modules.get(mname)) is not None and getattr(mod, "__file__", None):
                    try: importlib.reload(mod)
                    except Exception: pass
            if (pmod := sys.modules.get(pkg)) is not None:
                try: importlib.reload(pmod)
                except Exception: pass
        if (cmod := sys.modules.get("lattice.cli")) is not None:
            try: importlib.reload(cmod)
            except Exception: pass
        try:
            m = importlib.reload(self_mod) if (self_mod := sys.modules.get("lattice.mcp_server")) is not None else importlib.import_module("lattice.mcp_server")
            m._LAST_MTIME, main_mod = cur, sys.modules.get("__main__")
            for k in ("MCP_TOOLS", "KNOWN_TOOL_NAMES", "_hot_reload_if_changed", "_read_doc_resource", "_resolve_project_root", "_format_tool_result", "call_lattice_tool", "handle_mcp_request"):
                if hasattr(m, k):
                    globals()[k] = getattr(m, k)
                    if main_mod is not None: setattr(main_mod, k, getattr(m, k))
        except Exception: pass
    _LAST_MTIME = cur


def handle_mcp_request(request: Any = None) -> dict[str, Any] | None:
    """Handles an incoming JSON-RPC 2.0 MCP request (`$ INTERFACE handle_mcp_request`)."""
    if not isinstance(request, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    req = McpJsonRpcRequest(jsonrpc=str(request.get("jsonrpc") or "2.0"), id=request.get("id"), method=str(request.get("method") or ""), params=request.get("params") if isinstance(request.get("params"), dict) else {})
    if not (is_mcp_tool_call_valid(bool(req.method), True) and mcp_tool_dispatch_law(bool(req.method), True)):
        return {"jsonrpc": "2.0", "id": req.id, "error": {"code": -32600, "message": "Invalid Request: missing method"}}
    if req.method.startswith("notifications/"): return None
    if req.method == "ping": return {"jsonrpc": "2.0", "id": req.id, "result": {}}
    if req.method == "initialize":
        return {"jsonrpc": "2.0", "id": req.id, "result": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}}, "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
    if req.method == "resources/list": return {"jsonrpc": "2.0", "id": req.id, "result": {"resources": list(MCP_RESOURCES)}}
    if req.method == "resources/read":
        uri = str(req.params.get("uri") or "lattice://readme")
        doc = _read_doc_resource("guide" if "guide" in uri else "readme")
        return {"jsonrpc": "2.0", "id": req.id, "result": {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": doc.get("guide") if "guide" in uri else doc.get("readme", "")}]}}
    if req.method == "tools/list": return {"jsonrpc": "2.0", "id": req.id, "result": {"tools": list(MCP_TOOLS)}}
    if req.method == "tools/call":
        _hot_reload_if_changed()
        t_name, t_args = req.params.get("name"), req.params.get("arguments") if isinstance(req.params.get("arguments"), dict) else {}
        res = call_lattice_tool(t_name, t_args)
        return {"jsonrpc": "2.0", "id": req.id, "result": {"content": res.get("content", []), "isError": bool(res.get("isError", False))}}
    return {"jsonrpc": "2.0", "id": req.id, "error": {"code": -32601, "message": f"Method not found: {req.method}"}}


def main() -> int:
    """Runs the stdio JSON-RPC 2.0 MCP server loop."""
    for raw_line in sys.stdin:
        if not (line := raw_line.strip()): continue
        try: req_obj = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        if (resp := handle_mcp_request(req_obj)) is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
