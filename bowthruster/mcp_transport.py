"""MCP resource loader, dynamic root discovery, hot-reload, and read/packet/turn handlers (`mcp_transport.py`)."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import time
from typing import Any

from bowthruster.cli import (
    _execute_turn,
    _get_evaluate_lattice_proof,
    _load_atlas_boards,
    _load_board,
    _save_board,
    apply_slice_stale_and_claim,
    read_flight_logs,
    record_flight_event,
)
from bowthruster.cli_commands import compile_and_claim_packets
from bowthruster.mcp_schemas import COMPACT_PACKET_KEYS
from bowthruster.whiteboard import slice_lattice


def _mcp_home_and_root() -> tuple[Path, Path]:
    import bowthruster.cli as _cli
    import bowthruster.mcp_server as _ms

    return _ms.BOWTHRUSTER_HOME, _cli.REPO_ROOT


def _read_doc_resource(section: str = "readme") -> dict[str, Any]:
    home, repo_root = _mcp_home_and_root()
    readme_p = home / "README.md"
    skills_dir = home / "docs" / "skills"
    sec = str(section or "readme").strip().lower()
    out: dict[str, Any] = {"project_root": str(repo_root), "bowthruster_home": str(home)}
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
        "2. Work through one onboarding packet at a time via "
        "`turn(board='<board>', message='| Component | ... |')` to fill in Charter, Role, Why, Spec, "
        "Flows, and optional Laws (or merge/split candidate components).\n"
        "3. Run `git_sync(board='<board>', mode='pre', mark_verified=True)` and "
        "`git_sync(board='<board>', mode='post')`.\n"
        "4. Save the unified skill below to `.agents/skills/bowthruster/SKILL.md` "
        "(substituting `<BOARD_NAME>` and `<TEST_CMD>`). Pass `section='full_readme'` for full docs.\n\n"
        + uni
    )
    skill_keys = ("bowthruster", "lattice", "lattice-change", "lattice-navigate", "lattice-qa")
    if sec in ("skill", "skills", "all") and uni:
        out["skills"] = {k: uni for k in skill_keys}
    elif sec in ("change", "navigate", "qa") and uni:
        out["skills"] = {f"lattice-{sec}": uni, "bowthruster": uni, "lattice": uni}
    return out


def _resolve_project_root(
    board: str, project_root: str | None = None, message: str | None = None
) -> Path:
    if project_root and str(project_root).strip():
        return Path(str(project_root).strip()).expanduser().resolve()
    home, repo_root = _mcp_home_and_root()
    slug = "".join(c for c in (board or "").strip() if c.isalnum() or c in ("_", "-")) or "default"
    sibs = sorted(p for p in home.parents[1].iterdir() if p.is_dir()) if len(home.parents) > 1 else []
    for cand in (repo_root, home, home.parent, *sibs):
        if (cand / ".whiteboard" / f"{slug}.json").is_file():
            return cand.resolve()
    if message:
        rel_files = [
            tok.strip("`'\",:;()")
            for tok in str(message).split()
            if "/" in tok and "." in tok.split("/")[-1]
        ]
        for cand in (repo_root, home.parent, *sibs):
            if any((cand / rf).is_file() for rf in rel_files):
                return cand.resolve()
    return repo_root


def _format_tool_result(
    payload: dict[str, Any],
    *,
    is_error: bool = False,
    text: str | None = None,
    fence: bool = True,
) -> tuple[dict[str, Any], int]:
    import bowthruster.mcp_server as _ms

    raw = str(text) if text is not None else json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    kind = "text" if text is not None else "json"
    out_txt = f"```{kind}\n{raw}\n```" if fence and not raw.lstrip().startswith("```") else raw
    res = _ms.McpToolCallResult([{"type": "text", "text": out_txt}], bool(is_error), payload)
    out_dict = {
        "content": res.content,
        "isError": res.isError,
        "structuredContent": res.structuredContent,
    }
    return out_dict, len(out_txt.encode("utf-8"))


def _max_pkg_mtime() -> float:
    home, _ = _mcp_home_and_root()
    pkg_dir = home / "bowthruster"
    if not pkg_dir.is_dir():
        return 0.0
    return max((p.stat().st_mtime for p in pkg_dir.rglob("*.py") if p.is_file()), default=0.0)


_LAST_MTIME: float = 0.0


def _hot_reload_if_changed() -> None:
    global _LAST_MTIME
    import importlib

    cur = _max_pkg_mtime()
    if _LAST_MTIME and cur > _LAST_MTIME:
        _LAST_MTIME = cur
        pkgs = (
            "bowthruster.nlp",
            "bowthruster.types",
            "bowthruster.graph_core",
            "bowthruster.regiment",
            "bowthruster.ai_memory",
            "bowthruster.whiteboard",
            "bowthruster.the_table",
            "bowthruster.the_executive",
        )
        for pkg in pkgs:
            submods = [
                k
                for k in list(sys.modules)
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


def _tool_readme_or_logs(name: str, board: str, args: dict[str, Any], t0: float) -> dict[str, Any]:
    if name == "readme":
        sec = str(args.get("section") or "readme").strip().lower()
        doc = _read_doc_resource(sec)
        sec_map = {
            "readme": doc.get("quickstart"),
            "full_readme": doc.get("readme"),
            "guide": doc.get("guide"),
        }
        if sec in sec_map:
            doc_txt = sec_map[sec]
        elif sec in ("skill", "skills", "change", "navigate", "qa"):
            doc_txt = (doc.get("skills") or {}).get("bowthruster")
        else:
            doc_txt = None
        resp, nbytes = _format_tool_result(doc, text=doc_txt, fence=(doc_txt is None))
        record_flight_event(board, name, (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes)
        return resp
    entries = read_flight_logs(
        limit=int(args.get("limit") or 20), board=args.get("board"), status=args.get("status")
    )
    log_txt = "\n".join(
        f"[{e.get('timestamp')}] board={e.get('board')} cmd={e.get('tool_or_cmd')} "
        f"status={e.get('status')} decision={e.get('decision')} "
        f"({e.get('duration_ms')}ms, bytes={e.get('response_bytes', 0)}) "
        f"violations={len(e.get('violations') or [])}"
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
    metrics = view.get("atlas_metrics") or {}
    out_p: dict[str, Any] = {
        "boards": list(boards_dict.keys()),
        "atlas_mounted": view.get("atlas_mounted"),
        "atlas_schematic": view.get("atlas_schematic"),
        "atlas_metrics": metrics,
        "verified_components": metrics.get("verified_components", 0),
        "verified_untested_components": metrics.get("verified_untested_components", 0),
    }
    if args.get("mermaid"):
        out_p["atlas_mermaid"] = view.get("atlas_mermaid")
    if args.get("spec"):
        out_p["atlas_spec"] = view.get("atlas_spec")
    resp, nbytes = _format_tool_result(out_p, text=atlas_txt, fence=not bool(args.get("spec")))
    record_flight_event(board, "atlas", (time.perf_counter() - t0) * 1000.0, "OK", response_bytes=nbytes)
    return resp


def _tool_slice(board: str, args: dict[str, Any], active_root: Path, t0: float) -> dict[str, Any]:
    graph, wb_json, wb_sqlite, _ = _load_board(board)
    sl_kw = {
        "query": str(args.get("query") or ""),
        "component": args.get("component"),
        "division": args.get("division"),
        "stage": args.get("stage"),
        "schema": args.get("schema"),
        "kind": args.get("kind"),
        "include_code": bool(args.get("include_code", False)),
    }
    view = slice_lattice(graph, **sl_kw)
    wb_dir = active_root / ".whiteboard"
    has_q = bool(args.get("query", args.get("component")))
    if not view.get("matched_symbols") and not view.get("component_specs") and has_q and wb_dir.is_dir():
        for p in sorted(wb_dir.glob("*.json")):
            if (cand_b := p.stem) != board:
                cg, c_json, c_sql, _ = _load_board(cand_b)
                cv = slice_lattice(cg, **sl_kw)
                if cv.get("matched_symbols") or cv.get("component_specs"):
                    board, graph, wb_json, wb_sqlite, view = cand_b, cg, c_json, c_sql, cv
                    break
    view, stale_sids, claimed_sids = apply_slice_stale_and_claim(
        graph, view, sl_kw, mark_stale=bool(args.get("mark_stale", False)), claim_by=args.get("claim_by")
    )
    if stale_sids or claimed_sids:
        _save_board(graph, wb_json, wb_sqlite)
    if bool(args.get("include_raw", False)):
        payload = dict(view)
    else:
        matched_clean = [
            {k: v for k, v in m.items() if k != "code"} for m in (view.get("matched_symbols") or [])
        ]
        payload = {
            "status": view.get("status"),
            "board": board,
            "token_cost": view.get("token_cost"),
            "line_count": view.get("line_count"),
            "matched_symbols": matched_clean,
            "compact_view": view.get("compact_view"),
        }
    if stale_sids:
        payload["marked_stale_scopes"] = stale_sids
    if claimed_sids:
        payload["claimed_scopes"] = claimed_sids
    stale_sfx = f"\n[MARKED STALE]: {stale_sids}" if stale_sids else ""
    claim_sfx = f"\n[CLAIMED BY {args.get('claim_by')}]: {claimed_sids}" if claimed_sids else ""
    parts = ((view.get("compact_view") or "[EMPTY_SLICE]") + stale_sfx + claim_sfx).split("\n[CODE ")
    hdr_lines = []
    for ln in parts[0].splitlines():
        if ln.startswith("# SCHEMA "):
            hdr_lines.append("@ SCHEMA " + ln[9:])
        elif ln.startswith("> FLOW"):
            hdr_lines.append("@ FLOW" + ln[6:])
        else:
            hdr_lines.append(ln)
    hdr_cv = "\n".join(hdr_lines)
    sl_txt = f"```text\n{hdr_cv}\n```" + "".join(f"\n```python\n# [CODE {p}\n```" for p in parts[1:])
    resp, nbytes = _format_tool_result(payload, text=None if bool(args.get("include_raw", False)) else sl_txt)
    dur_ms = (time.perf_counter() - t0) * 1000.0
    mod_sc = sorted(set(stale_sids) | set(claimed_sids)) or None
    record_flight_event(board, "slice", dur_ms, "OK", modified_scopes=mod_sc, response_bytes=nbytes)
    return resp


def _tool_packets_or_turn(name: str, board: str, args: dict[str, Any], t0: float) -> dict[str, Any]:
    if name == "packets":
        graph, wb_json, wb_sqlite, _ = _load_board(board)
        strip_w0 = (
            args.get("wave") is None
            and not args.get("component")
            and not bool(args.get("include_raw", False))
        )
        res, pkts, claimed = compile_and_claim_packets(graph, {**args, "strip_wave0": strip_w0})
        if claimed:
            _save_board(graph, wb_json, wb_sqlite)
        if bool(args.get("include_raw", False)):
            payload: dict[str, Any] = {**res, "packets": pkts}
        else:
            payload = {
                "status": res.get("status"),
                "is_proven": res.get("is_proven"),
                "ready_to_emit": res.get("ready_to_emit"),
                "packets": [{k: p.get(k) for k in COMPACT_PACKET_KEYS} for p in pkts],
            }
        if claimed:
            payload["claimed_scopes"] = claimed
        resp, nbytes = _format_tool_result(payload)
        st = "OK" if (res.get("ready_to_emit") or args.get("force")) else "REJECTED"
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
        "return_code": rc,
        "board": board,
        "turn": graph.get("turn", 0),
        "lattice_proof": proof,
        "cli_output": cli_out,
    }
    resp, nbytes = _format_tool_result(out_p, is_error=(rc != 0), text=cli_out)
    dur_ms = (time.perf_counter() - t0) * 1000.0
    st_turn = "OK" if rc == 0 else "REJECTED"
    record_flight_event(board, name, dur_ms, st_turn, decision=(proof or {}).get("status"), response_bytes=nbytes)
    return resp
