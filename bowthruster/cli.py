#!/usr/bin/env python3
"""Multi-Round Executive <-> Whiteboard Dogfooding CLI (`bowthruster.cli`)."""

from __future__ import annotations

import argparse
import contextlib
import copy
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

LATTICE_HOME = Path(__file__).resolve().parents[1]
REPO_ROOT = (
    Path(os.environ.get("BOWTHRUSTER_PROJECT_ROOT") or os.environ["LATTICE_PROJECT_ROOT"]).resolve()
    if (os.environ.get("BOWTHRUSTER_PROJECT_ROOT") or os.environ.get("LATTICE_PROJECT_ROOT"))
    else LATTICE_HOME
)
if str(LATTICE_HOME) not in sys.path:
    sys.path.insert(0, str(LATTICE_HOME))

import bowthruster.whiteboard as _wb  # noqa: E402
from bowthruster.git_manifest import (  # noqa: E402
    AST_EXTRACTORS, DEFAULT_CONFIG_PATHS, _PY_AST_CACHE, _assemble_board_summary, _build_git_manifest,
    _collect_ref_map, _extract_ast_local_calls, _extract_py_ast_symbols, _print_proof_radar, _py_ast_extractor,
    _remove_item_from_board, _run_bend_proof_check, _verify_component_files, format_exception_error,
    is_baseline_stamp_clean, is_table_file_indexed, read_flight_logs, record_flight_event, register_ast_extractor, run_delta_tests,
)
from bowthruster.graph_core import SQLiteGraphStore  # noqa: E402
from bowthruster.nlp import _extract_generic_source_symbols, verify_python_ast_laws_with_bend  # noqa: E402
from bowthruster.the_table import render_lattice_atlas  # noqa: E402
from bowthruster.whiteboard import (  # noqa: E402
    ContextGraph, create_empty_context_graph, frustration as _wb_frust, ingest as _wb_ingest,
    mutations as _wb_mut, slice_lattice, sync_to_sqlite,
)

SESSIONS_ROOT = Path("/tmp/whiteboard_sessions")
LIMITS_PATH = LATTICE_HOME / "size_limits.json"
FLIGHT_LOG_PATH = REPO_ROOT / ".whiteboard" / "logs" / "lattice_events.jsonl"
STANDING_TYPES = ("BOUND", "PROHIBITION", "OBLIGATION", "POLICY")
BUILD_STATUSES = ("DRAFT", "BLOCKED_BY_DEP", "READY", "IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE")


def _get_evaluate_lattice_proof() -> Any:
    return getattr(_wb, "evaluate_lattice_proof", None) or getattr(_wb_frust, "evaluate_lattice_proof", None)


def _get_compile_work_packets() -> Any:
    return getattr(_wb, "compile_work_packets", None) or getattr(_wb_ingest, "compile_work_packets", None)


def _get_transition_build_status() -> Any:
    return getattr(_wb, "transition_component_build_status", None) or getattr(_wb_mut, "transition_component_build_status", None)


def _get_render_lattice_atlas() -> Any:
    return getattr(sys.modules.get("bowthruster.the_table.surface"), "render_lattice_atlas", render_lattice_atlas)


def _call_compile_packets(graph: dict[str, Any], *, force: bool = False, status_filter: str | None = None) -> dict[str, Any]:
    pkt_fn = _get_compile_work_packets()
    if pkt_fn is None:
        raise RuntimeError("compile_work_packets is not yet promoted to bowthruster.whiteboard")
    if status_filter and "status_filter" in inspect.signature(pkt_fn).parameters:
        return pkt_fn(graph, force=force, status_filter=status_filter)
    res = pkt_fn(graph, force=force)
    if status_filter:
        return {**res, "packets": [p for p in (res.get("packets") or []) if str(p.get("build_status") or "").upper() == status_filter.upper()]}
    return res


_META_LOCK = threading.Lock()
_RLOCKS: dict[str, threading.RLock] = {}
_LOCK_DEPTH: dict[str, tuple[int, Any]] = {}


@contextlib.contextmanager
def _board_lock(board: str):
    slug = "".join(c for c in board.strip() if c.isalnum() or c in ("_", "-")) or "default"
    bdir = SESSIONS_ROOT / slug
    bdir.mkdir(parents=True, exist_ok=True)
    key = str(bdir)
    with _META_LOCK:
        rlock = _RLOCKS.setdefault(key, threading.RLock())
    with rlock:
        depth, fh = _LOCK_DEPTH.get(key) or (0, None)
        if depth == 0:
            fh = (bdir / ".board.lock").open("a+", encoding="utf-8")
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _LOCK_DEPTH[key] = (depth + 1, fh)
        try:
            yield
        finally:
            if depth == 0:
                _LOCK_DEPTH.pop(key, None)
                if fcntl is not None and fh is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                if fh is not None:
                    fh.close()
            else:
                _LOCK_DEPTH[key] = (depth, fh)


def _board_paths(board: str) -> tuple[Path, Path, Path, Path]:
    slug = "".join(c for c in board.strip() if c.isalnum() or c in ("_", "-")) or "default"
    wb_local = REPO_ROOT / ".whiteboard" / f"{board}.json"
    bdir = SESSIONS_ROOT / slug
    bdir.mkdir(parents=True, exist_ok=True)
    has_env_root = bool(os.environ.get("BOWTHRUSTER_PROJECT_ROOT") or os.environ.get("LATTICE_PROJECT_ROOT"))
    if slug not in ("default", "dogfood") and not slug.endswith("_live") and (wb_local.parent.is_dir() or has_env_root):
        wb_local.parent.mkdir(parents=True, exist_ok=True)
    use_local = wb_local.is_file() or (
        (not (bdir / "whiteboard.json").is_file() or has_env_root)
        and wb_local.parent.is_dir()
        and slug not in ("default", "dogfood")
        and not slug.endswith("_live")
    )
    return bdir, (wb_local if use_local else bdir / "whiteboard.json"), bdir / "whiteboard.sqlite", bdir / "transcript.jsonl"


def _save_board(graph: dict[str, Any], wb_json: Path, wb_sqlite: Path) -> None:
    if isinstance(graph, ContextGraph):
        object.__setattr__(graph, "_sqlite_store", SQLiteGraphStore(wb_sqlite))
    sync_to_sqlite(graph)
    tmp_p = wb_json.with_name(f".{wb_json.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    clean_graph = copy.deepcopy({k: v for k, v in dict(graph).items() if k != "_sqlite_store"})
    tmp_p.write_text(json.dumps(clean_graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_p, wb_json)


def _load_board(board: str) -> tuple[ContextGraph, Path, Path, Path]:
    with _board_lock(board):
        _, wb_json, wb_sqlite, transcript_path = _board_paths(board)
        store = SQLiteGraphStore(wb_sqlite)
        if wb_json.is_file():
            raw_data = json.loads(wb_json.read_text(encoding="utf-8"))
            graph = ContextGraph(raw_data, store=store)
            for sid, raw_sc in (raw_data.get("scopes") or {}).items():
                if isinstance(raw_sc, dict) and raw_sc.get("active_ticket_id") and sid in graph.get("scopes", {}):
                    graph["scopes"][sid]["active_ticket_id"] = raw_sc["active_ticket_id"]
            sync_to_sqlite(graph)
        else:
            graph = create_empty_context_graph(store=store)
        return graph, wb_json, wb_sqlite, transcript_path


def _load_atlas_boards(explicit_board: str | None, boards_csv: str | None, load_all: bool) -> dict[str, dict[str, Any]]:
    vault_wb = REPO_ROOT / ".whiteboard"
    if boards_csv:
        names = [x.strip() for x in boards_csv.split(",") if x.strip()]
    elif not load_all and explicit_board:
        names = [explicit_board]
    else:
        roots = (vault_wb,) if vault_wb.is_dir() else (SESSIONS_ROOT,)
        cands = sorted({p.stem if p.name != "whiteboard.json" else p.parent.name for r in roots if r.is_dir() for p in r.rglob("*.json")})
        names = [n for n in cands if n not in ("default", "dogfood") and not n.endswith("_live")] or [explicit_board or "default"]
    out: dict[str, dict[str, Any]] = {}
    for n in names:
        cand = next((c for c in (vault_wb / f"{n}.json", vault_wb / n / "whiteboard.json", SESSIONS_ROOT / n / "whiteboard.json") if c.is_file()), None)
        if cand:
            d = json.loads(cand.read_text(encoding="utf-8"))
            if boards_csv or (not load_all and explicit_board) or [k for k in (d.get("scopes") or {}) if k != "global_defaults"] or d.get("flows"):
                out[n] = d
        elif boards_csv or (not load_all and explicit_board):
            out[n] = dict(_load_board(n)[0])
    return out or {explicit_board or "default": dict(_load_board(explicit_board or "default")[0])}


def _read_transcript(p: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.is_file() else []


def _append_transcript(p: Path, record: dict[str, Any]) -> None:
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_input_text(message: str | None, file_path: str | None) -> str:
    if message is not None or file_path is not None:
        raw = message if message is not None else (sys.stdin.read() if file_path == "-" else Path(file_path).read_text(encoding="utf-8"))
        return raw.strip()
    raise ValueError("Provide either -m/--message or -F/--file")


def _execute_turn(board: str, speaker: str, raw_text: str, round_num: int | None, profile: str | None = None, config_path: str | None = None) -> int:
    from bowthruster.the_executive import run_executive_turn

    with _board_lock(board):
        graph, wb_json, wb_sqlite, transcript_path = _load_board(board)
        history = _read_transcript(transcript_path)
        if round_num is None:
            last_turn = next((h for h in reversed(history) if h.get("event", "turn") == "turn"), None)
            round_num = int(last_turn.get("round", 1)) + 1 if (last_turn and last_turn.get("decision") == "SEND_BACK_TO_USER") else 1
        t0 = time.perf_counter()
        res = run_executive_turn(graph, raw_text, db_path=wb_sqlite)
        elapsed_s = round(time.perf_counter() - t0, 3)
        if res["whiteboard_mutated"]:
            _save_board(res["whiteboard_graph"], wb_json, wb_sqlite)
        eval_fn = _get_evaluate_lattice_proof()
        proof = res.get("lattice_proof") or (eval_fn(res["whiteboard_graph"]) if eval_fn else None)
        parsed_mcus = res.get("intake_packet", {}).get("parsed_mcus", [])
        _append_transcript(transcript_path, {
            "event": "turn", "timestamp_utc": datetime.now(timezone.utc).isoformat(), "speaker": speaker, "round": round_num,
            "turn_index": res["turn_index"], "raw_text": raw_text, "decision": res["decision"], "bounce_reason": res["bounce_reason"],
            "whiteboard_mutated": res["whiteboard_mutated"], "user_message": res["user_message"], "cited_items": res["cited_items"],
            "committed_items": res.get("committed_items", []), "conflicts": res.get("conflicts", []), "flawed_ideas": res.get("flawed_ideas", []),
            "clarification_questions": res.get("clarification_questions", []), "open_questions": res.get("open_questions", []),
            "parsed_mcus": parsed_mcus, "lattice_proof_status": proof.get("status") if isinstance(proof, dict) else None, "elapsed_s": elapsed_s,
        })
    record_flight_event(
        board, "turn", elapsed_s * 1000.0, "OK" if res["decision"] == "COMMIT_TO_WHITEBOARD" else "REJECTED",
        decision=res["decision"], violations=list(res.get("conflicts") or []) + list(res.get("flawed_ideas") or []),
    )
    cited_str = f"\nCited Items:       {', '.join(str(x) for x in res['cited_items'])}" if res["cited_items"] else ""
    print(
        f"=== EXECUTIVE TURN {res['turn_index']} (Round {round_num} | Speaker: {speaker}) ===\n"
        f"Decision:          {res['decision']}\nBounce Reason:     {res['bounce_reason']}\n"
        f"Whiteboard Mutated:{res['whiteboard_mutated']} (elapsed {elapsed_s:.2f}s)\n"
        f"Executive Reply:   {res['user_message']}{cited_str}\nParsed MCUs ({len(parsed_mcus)}):"
    )
    for m in parsed_mcus:
        print(f"  - [mcu_{m.get('mcu_id')}] {m.get('type')} scope={m.get('scope_hint') or 'auto'} prop={m.get('target_prop')!r}: {m.get('text')}")
    for label, key in (("Conflicts", "conflicts"), ("Flawed Ideas", "flawed_ideas"), ("Clarification Questions", "clarification_questions"), ("Open Questions", "open_questions")):
        if res.get(key):
            print(f"{label} ({len(res[key])}):\n" + "\n".join(f"  * {json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item}" for item in res[key]))
    _print_proof_radar(proof)
    return 0


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    parser = argparse.ArgumentParser(description="Multi-Round Executive <-> Whiteboard Dogfooding CLI")
    for flg in ("--board", "--profile", "--config"):
        common.add_argument(flg, default=argparse.SUPPRESS)
        parser.add_argument(flg, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_turn, p_rep, p_rev = sub.add_parser("turn", parents=[common]), sub.add_parser("replace", parents=[common]), sub.add_parser("revoke", parents=[common])
    for p in (p_rep, p_rev):
        p.add_argument("rule_id")
    for p in (p_turn, p_rep, p_rev):
        p.add_argument("--speaker", default="agent")
        p.add_argument("-m", "--message", default="Explicitly revoked" if p is p_rev else None)
        if p is not p_rev:
            p.add_argument("--round", type=int, default=None, dest="round_num")
            p.add_argument("-F", "--file", default=None)
    p_show, p_slice = sub.add_parser("show", parents=[common]), sub.add_parser("slice", parents=[common])
    p_show.add_argument("--scope", default=None)
    for flag in ("--component", "--division", "--stage", "--schema", "--kind"):
        p_slice.add_argument(flag, default=None)
    p_slice.add_argument("-q", "--query", default="")
    p_slice.add_argument("--code", action="store_true", dest="include_code")
    p_slice.add_argument("--mark-stale", action="store_true", dest="mark_stale")
    p_pkts = sub.add_parser("packets", parents=[common])
    p_stat = sub.add_parser("status", parents=[common])
    p_atlas = sub.add_parser("atlas", parents=[common])
    p_gsync = sub.add_parser("git-sync", parents=[common])
    p_logs = sub.add_parser("logs", parents=[common])
    p_pkts.add_argument("--wave", type=int, choices=(0, 1, 2), default=None)
    p_pkts.add_argument("--component", default=None)
    p_pkts.add_argument("--status", choices=BUILD_STATUSES, type=str.upper, default=None)
    p_pkts.add_argument("--force", action="store_true")
    p_stat.add_argument("component")
    p_stat.add_argument("new_status", choices=BUILD_STATUSES, type=str.upper)
    for flg, dst in (("--build-tag", "build_tag"), ("--core-tag", "core_tag"), ("--reason", "reason"), ("--for", "for_div")):
        p_stat.add_argument(flg, default=None, dest=dst)
    for flg, dst in (("--dispatch", "dispatch"), ("--no-ticket", "no_ticket")):
        p_stat.add_argument(flg, action="store_true", dest=dst)
    p_atlas.add_argument("--boards", default=None)
    for flg, dst in (("--all", "load_all"), ("--mermaid", "mermaid"), ("--spec", "spec")):
        p_atlas.add_argument(flg, action="store_true", dest=dst)
    for flg, dst in (("--pre-commit", "pre_commit"), ("--post-commit", "post_commit"), ("--baseline", "baseline"), ("--drift", "drift"), ("--mark-verified", "mark_verified")):
        p_gsync.add_argument(flg, action="store_true", dest=dst)
    p_gsync.add_argument("--manifest", default=None)
    p_logs.add_argument("--limit", type=int, default=20)
    p_logs.add_argument("--status", default=None)
    for p in (p_show, p_slice, sub.add_parser("prove", parents=[common]), p_pkts, p_stat, p_atlas, p_gsync, p_logs, sub.add_parser("history", parents=[common])):
        p.add_argument("--json", action="store_true", dest="as_json")
    sub.add_parser("reset", parents=[common])
    args = parser.parse_args()
    explicit_board, profile, config_path = getattr(args, "board", None), getattr(args, "profile", None), getattr(args, "config", None)
    board, t0_cmd = explicit_board or "default", time.perf_counter()
    try:
        if args.cmd == "logs":
            entries = read_flight_logs(limit=args.limit, board=explicit_board, status=args.status)
            if args.as_json:
                print(json.dumps(entries, indent=2, ensure_ascii=False))
            else:
                lines = [
                    f"[{e.get('timestamp')}] board={e.get('board')} cmd={e.get('tool_or_cmd')} status={e.get('status')} decision={e.get('decision')} ({e.get('duration_ms')}ms, bytes={e.get('response_bytes', 0)}) violations={len(e.get('violations') or [])}"
                    + (f" error={e['error'].get('type')}: {e['error'].get('message')}" if e.get("error") else "")
                    for e in entries
                ]
                print("\n".join(lines) or "(no flight log entries)")
            return 0
        if args.cmd == "git-sync":
            from bowthruster.ai_memory import sync_git_diff_uris
            from bowthruster.the_executive import detect_working_tree_drift, sync_post_commit_to_lattice
            from bowthruster.the_executive.gate import evaluate_git_commit_gate

            graph, wb_json, wb_sqlite, _ = _load_board(board)
            if args.pre_commit:
                manifest = _build_git_manifest("pre", args.manifest, graph)
                gate_res = dict(evaluate_git_commit_gate(graph, manifest, uri_sync_report=sync_git_diff_uris(copy.deepcopy(graph), manifest)))
                allow = bool(gate_res.get("allowed", gate_res.get("allow_commit", True)))
                if allow and args.mark_verified:
                    stale_sids = [
                        sid
                        for sid, sc in sorted((graph.get("scopes") or {}).items())
                        if isinstance(sc, dict)
                        and sid != "global_defaults"
                        and (
                            str(sc.get("status") or sc.get("build_status") or "").upper() in ("STALE", "READY")
                            or (bool(sc.get("files") or (sc.get("attributes") or {}).get("files")) and str(sc.get("status") or sc.get("build_status") or "DRAFT").upper() != "VERIFIED")
                        )
                    ]
                    t_ran, t_ok, t_out = run_delta_tests(REPO_ROOT, stale_sids)
                    gate_res["delta_tests_ran"] = t_ran
                    gate_res["delta_tests_passed"] = t_ok
                    if not t_ok:
                        allow = False
                        gate_res["allowed"] = False
                        gate_res["decision"] = "REJECT_COMMIT"
                        gate_res.setdefault("violations", []).append({"kind": "TEST_FAILURE", "scope": stale_sids[0] if stale_sids else "global_defaults", "path": "tests/", "description": f"Delta pytest failed:\n{t_out[-600:]}"})
                    else:
                        for sid in stale_sids:
                            _verify_component_files(graph, sid)
                            _get_transition_build_status()(graph, sid, "VERIFIED")
                        if stale_sids:
                            _save_board(graph, wb_json, wb_sqlite)
                        gate_res["marked_verified_scopes"] = stale_sids
                record_flight_event(board, "git-sync:pre-commit", (time.perf_counter() - t0_cmd) * 1000.0, "OK" if allow else "REJECTED", decision=gate_res.get("decision"), violations=gate_res.get("violations"), modified_scopes=gate_res.get("marked_verified_scopes"))
                print(json.dumps(gate_res, indent=2, sort_keys=True) if args.as_json else f"[GIT PRE-COMMIT GATE '{board}'] allowed={allow} decision={gate_res.get('decision')} bounds_ok={gate_res.get('bounds_ok')} contracts_ok={gate_res.get('contracts_ok')} bend_proofs_ok={gate_res.get('bend_proofs_ok')} no_dangling_deletes={gate_res.get('no_dangling_deletes')} violations={len(gate_res.get('violations') or [])}" + (f" marked_verified={gate_res['marked_verified_scopes']}" if gate_res.get("marked_verified_scopes") else ""))
                return 0 if allow else 1
            if args.post_commit or args.baseline:
                sync_res = dict(sync_post_commit_to_lattice(graph, _build_git_manifest("baseline" if args.baseline else "post", args.manifest, graph), store=SQLiteGraphStore(wb_sqlite)))
                wb_out = sync_res.get("whiteboard_graph") or graph
                if sync_res.get("commit_sha"):
                    wb_out["last_commit_sha"] = sync_res["commit_sha"]
                _save_board(wb_out, wb_json, wb_sqlite)
                out_d = {k: v for k, v in sync_res.items() if k != "whiteboard_graph"}
                record_flight_event(board, "git-sync:post-commit", (time.perf_counter() - t0_cmd) * 1000.0, "OK", modified_scopes=out_d.get("modified_scopes"), stale_cascaded_scopes=out_d.get("cascaded_stale_scopes"))
                print(json.dumps(out_d, indent=2, sort_keys=True) if args.as_json else f"[GIT POST-COMMIT SYNC '{board}'] sha={out_d.get('commit_sha')} modified={out_d.get('modified_scopes')} cascaded_stale={out_d.get('cascaded_stale_scopes')}")
                return 0
            drift_res = dict(detect_working_tree_drift(graph, _build_git_manifest("drift", args.manifest, graph)))
            record_flight_event(board, "git-sync:drift", (time.perf_counter() - t0_cmd) * 1000.0, "OK", modified_scopes=drift_res.get("drifted_scopes"))
            print(json.dumps(drift_res, indent=2, sort_keys=True) if args.as_json else f"[GIT DRIFT '{board}'] has_drift={drift_res.get('has_drift')} drifted_scopes={drift_res.get('drifted_scopes')} uncommitted={len(drift_res.get('uncommitted_files') or [])} dangling_uris={drift_res.get('dangling_uris')}")
            return 0
        if args.cmd == "atlas":
            boards_dict = _load_atlas_boards(explicit_board, args.boards, args.load_all)
            view = _get_render_lattice_atlas()(boards_dict, has_local_seed_cues=False, explicit_atlas_request=True)
            out_payload = {"boards": list(boards_dict.keys()), **{k: view.get(k) for k in ("atlas_mounted", "atlas_schematic", "atlas_metrics", "atlas_mermaid", "atlas_spec")}}
            record_flight_event(board, "atlas", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            print(json.dumps(out_payload, indent=2, sort_keys=True, ensure_ascii=False) if args.as_json else (str(view.get("atlas_spec") or "") if args.spec else f"{view.get('atlas_schematic') or ''}\n\n[Atlas Metrics]: {json.dumps(view.get('atlas_metrics') or {}, sort_keys=True)}" + (f"\n\n[Atlas Mermaid]:\n{view['atlas_mermaid']}" if args.mermaid and view.get("atlas_mermaid") else "")))
            return 0
        if args.cmd == "reset":
            bdir, wb_json, wb_sqlite, transcript_path = _board_paths(board)
            for p in (wb_json, wb_sqlite, transcript_path, bdir / "LATTICE_PROOF.bend"):
                p.unlink(missing_ok=True)
            _save_board(create_empty_context_graph(store=SQLiteGraphStore(wb_sqlite)), wb_json, wb_sqlite)
            record_flight_event(board, "reset", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            print(f"Reset Whiteboard session '{board}' at {bdir}")
            return 0
        if args.cmd == "turn":
            return _execute_turn(board, args.speaker, _read_input_text(args.message, args.file), args.round_num, profile, config_path)
        if args.cmd in ("revoke", "replace"):
            graph, wb_json, wb_sqlite, transcript_path = _load_board(board)
            removed = _remove_item_from_board(graph, args.rule_id)
            if not removed:
                raise KeyError(f"Item {args.rule_id!r} not found on Whiteboard '{board}'")
            _save_board(graph, wb_json, wb_sqlite)
            _append_transcript(transcript_path, {"event": args.cmd, "timestamp_utc": datetime.now(timezone.utc).isoformat(), "speaker": args.speaker, "rule_id": args.rule_id, "removed": removed, "reason": args.message or ""})
            record_flight_event(board, args.cmd, (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            print(f"[{args.cmd.upper()}] Removed from '{board}': {'; '.join(removed)}")
            return _execute_turn(board, args.speaker, _read_input_text(args.message, args.file), args.round_num, profile, config_path) if args.cmd == "replace" else 0
        if args.cmd == "slice":
            graph, wb_json, wb_sqlite, _ = _load_board(board)
            view = dict(slice_lattice(graph, query=args.query, component=args.component, division=args.division, stage=args.stage, schema=args.schema, kind=args.kind, include_code=args.include_code))
            if args.mark_stale:
                scopes_map, c_arg = graph.get("scopes") or {}, str(args.component or "").strip()
                stale_sids = [c_arg] if c_arg and c_arg in scopes_map else sorted({str(m.get("scope")) for m in (view.get("matched_symbols") or []) if isinstance(m, dict) and m.get("scope") in scopes_map} or {str(c.get("id")) for c in (view.get("component_specs") or []) if isinstance(c, dict) and c.get("id") in scopes_map and c.get("id") != "global_defaults"})
                for sid in stale_sids:
                    _get_transition_build_status()(graph, sid, "STALE")
                if stale_sids:
                    _save_board(graph, wb_json, wb_sqlite)
                view["marked_stale_scopes"] = stale_sids
            record_flight_event(board, "slice", (time.perf_counter() - t0_cmd) * 1000.0, "OK", modified_scopes=view.get("marked_stale_scopes"))
            print(json.dumps(view, indent=2, sort_keys=True, ensure_ascii=False) if args.as_json else (view.get("compact_view") or view.get("text") or "[EMPTY_SLICE]") + (f"\n[MARKED STALE]: {view['marked_stale_scopes']}" if view.get("marked_stale_scopes") else ""))
            return 0
        if args.cmd == "prove":
            bdir, _, _, _ = _board_paths(board)
            eval_fn = _get_evaluate_lattice_proof()
            if eval_fn is None:
                raise RuntimeError("evaluate_lattice_proof is not yet promoted to bowthruster.whiteboard")
            proof = dict(eval_fn(_load_board(board)[0]))
            b_ok, b_path, b_out = _run_bend_proof_check(bdir, str(proof.get("bend_proof_source") or ""))
            proof.update({"bend_check_passed": b_ok, "bend_proof_file": str(b_path), "bend_output": b_out})
            record_flight_event(board, "prove", (time.perf_counter() - t0_cmd) * 1000.0, "OK" if proof.get("is_proven") and b_ok else "REJECTED", decision=proof.get("status"))
            print(json.dumps(proof, indent=2, sort_keys=True, ensure_ascii=False) if args.as_json else f"=== LATTICE PROOF '{board}' ===\nStatus:            {proof.get('status')} (is_proven={proof.get('is_proven')})\nCounts:            components={proof.get('component_count', 0)}, flows={proof.get('flow_count', 0)}, laws={proof.get('law_count', 0)}, contracts={proof.get('contract_count', 0)}\nBend 2 Check:      {'PASS' if b_ok else 'FAIL'} ({b_path})")
            if not args.as_json:
                _print_proof_radar(proof)
            return 0
        if args.cmd == "status":
            trans_fn = _get_transition_build_status()
            if trans_fn is None:
                raise RuntimeError("transition_component_build_status is not yet promoted to bowthruster.whiteboard")
            graph, wb_json, wb_sqlite, transcript_path = _load_board(board)
            if args.new_status == "VERIFIED":
                _verify_component_files(graph, args.component)
            prev_tid = (graph.get("scopes", {}).get(args.component) or {}).get("active_ticket_id")
            prev_ready = {str(p.get("packet_id")) for p in (_call_compile_packets(graph, force=True, status_filter="READY").get("packets") or [])} if _get_compile_work_packets() else set()
            snode = trans_fn(graph, args.component, args.new_status, build_tag=args.build_tag, core_tag=args.core_tag, failure_diagnostic=args.reason)
            if prev_tid and not snode.get("active_ticket_id"):
                snode["active_ticket_id"] = prev_tid
            all_pkts = (_call_compile_packets(graph, force=True).get("packets") or []) if _get_compile_work_packets() else []
            ready_pkts = [p for p in all_pkts if str(p.get("build_status") or "").upper() == "READY"]
            unlocked_pkts = [p for p in ready_pkts if str(p.get("packet_id")) not in prev_ready]
            t_info = {"ticket_id": snode.get("active_ticket_id")}
            _save_board(graph, wb_json, wb_sqlite)
            payload = {"component": args.component, "status": snode.get("status"), "build_tag": snode.get("build_tag"), "core_tag": snode.get("core_tag"), "failure_diagnostic": snode.get("failure_diagnostic"), "active_ticket_id": snode.get("active_ticket_id"), "ticket_info": t_info, "ready_packets": [p.get("packet_id") for p in ready_pkts], "newly_unlocked_packets": [p.get("packet_id") for p in unlocked_pkts]}
            _append_transcript(transcript_path, {"event": "status", "timestamp_utc": datetime.now(timezone.utc).isoformat(), **payload})
            record_flight_event(board, f"status:{args.component}", (time.perf_counter() - t0_cmd) * 1000.0, "OK", decision=snode.get("status"), modified_scopes=[args.component])
            if args.as_json:
                print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
                return 0
            print(f"[STATUS] {args.component} -> status={snode.get('status')} build_tag={snode.get('build_tag')} core_tag={snode.get('core_tag')} ticket={snode.get('active_ticket_id')} failure_diagnostic={snode.get('failure_diagnostic')!r}" + (f"\n[NEWLY UNLOCKED READY PACKETS ({len(unlocked_pkts)})]: " + ", ".join(f"{p.get('packet_id')} (owner={p.get('owner')})" for p in unlocked_pkts) if unlocked_pkts else "") + f"\n[READY PACKETS ({len(ready_pkts)})]: {', '.join(str(p.get('packet_id')) for p in ready_pkts) or 'none'}")
            return 0
        if args.cmd == "packets":
            res = _call_compile_packets(_load_board(board)[0], force=args.force, status_filter=args.status)
            pkts = [p for p in (res.get("packets") or []) if (args.wave is None or p.get("wave") == args.wave) and (not args.component or p.get("component") == args.component) and (not args.status or str(p.get("build_status") or "").upper() == args.status)]
            record_flight_event(board, "packets", (time.perf_counter() - t0_cmd) * 1000.0, "OK" if res.get("ready_to_emit") or args.force else "REJECTED", decision=res.get("status"))
            if args.as_json:
                print(json.dumps({**res, "packets": pkts} if (args.wave is not None or args.component or args.status) else res, indent=2, sort_keys=True, ensure_ascii=False))
                return 0
            if not res.get("ready_to_emit") and not args.force:
                print(f"=== WORK PACKETS LOCKED ('{board}') ===\nStatus: {res.get('status')} (requires PROVEN_COMPLETE; pass --force to preview draft packets)")
                _print_proof_radar(res.get("lattice_proof") or res)
                return 0
            print(f"=== WORK PACKETS '{board}' ({len(pkts)} packets | status={res.get('status')} | ready_to_emit={res.get('ready_to_emit')}) ===")
            for p in pkts:
                print(f"\n[{p.get('packet_id')}] wave={p.get('wave')} kind={p.get('kind')} component={p.get('component')} owner={p.get('owner')} stage={p.get('stage')} build_status={p.get('build_status')} build_tag={p.get('build_tag')} core_tag={p.get('core_tag')} failure_diagnostic={p.get('failure_diagnostic')!r}\n{p.get('prompt_slice') or ''}")
            return 0
        if args.cmd == "show":
            summary = _assemble_board_summary(_load_board(board)[0], args.scope, _get_evaluate_lattice_proof())
            record_flight_event(board, "show", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            if args.as_json:
                print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
                return 0
            print(f"=== WHITEBOARD '{board}' (Turn {summary['turn']}) ===")
            _print_proof_radar(summary.get("lattice_proof"))
            for sid, sdata in summary["scopes"].items():
                print(f"\n[Scope: {sid}] (parent={sdata['parent']}, children={sdata['children']}, owner={sdata.get('owner')}, stage={sdata.get('stage')}, status={sdata.get('status')}, ticket={sdata.get('active_ticket_id')}, build_tag={sdata.get('build_tag')}, core_tag={sdata.get('core_tag')})" + (f"\n  ! Failure Diagnostic: {sdata['failure_diagnostic']}" if sdata.get("failure_diagnostic") else "") + (f"\n  Attributes: {json.dumps(sdata['attributes'], sort_keys=True)}" if sdata["attributes"] else ""))
                for r in sdata["rules"]:
                    print(f"  - [{r['id']}] ({r['type']}) prop={r.get('target_prop')!r} op={r.get('bound_op')!r} val={r.get('bound_val')!r}: {r['text']}")
            for label, key, fmt in (("Flows", "flows", lambda f: f"  - [{f.get('id')}] {f.get('source')} -> {f.get('target')} ({f.get('payload')}): {f.get('text')}"), ("Lattice Artifacts", "lattice_artifacts", lambda a: f"  - [{a.get('id')}] {a.get('kind')}@{a.get('scope_id')} {a.get('name')}: {a.get('spec') or a.get('text')}"), ("Active Tasks", "active_tasks", lambda t: f"  - [{t.get('id')}] status={t.get('status')} scope={t.get('scope')}: {t.get('text')}")):
                if summary[key]:
                    print(f"\n[{label}]:\n" + "\n".join(fmt(x) for x in summary[key]))
            print("\n[Read Engine Context Pack]:\n" + (summary["assemble_context_pack"].get("compact_view") or summary["assemble_context_pack"].get("text") or "[EMPTY_WHITEBOARD]"))
            return 0
        if args.cmd == "history":
            records = _read_transcript(_board_paths(board)[3])
            record_flight_event(board, "history", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            if args.as_json:
                print(json.dumps(records, indent=2, ensure_ascii=False))
                return 0
            for idx, rec in enumerate(records, 1):
                print(f"[{idx}] {rec['event'].upper()} by {rec.get('speaker')}: rule_id={rec.get('rule_id')} removed={rec.get('removed')}" if rec.get("event") in ("revoke", "replace") else f"[{idx}] STATUS {rec.get('component')} -> {rec.get('status')} (ticket={rec.get('active_ticket_id')}, build_tag={rec.get('build_tag')}, core_tag={rec.get('core_tag')})" if rec.get("event") == "status" else f"[{idx}] Turn {rec.get('turn_index')} (Round {rec.get('round')}, {rec.get('speaker')}): {rec.get('decision')} ({rec.get('bounce_reason')}) mutated={rec.get('whiteboard_mutated')}\n    Input: {rec.get('raw_text')}\n    Reply: {rec.get('user_message')}")
            return 0
    except Exception as exc:
        record_flight_event(board, args.cmd, (time.perf_counter() - t0_cmd) * 1000.0, "ERROR", error=format_exception_error(exc))
        raise
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
