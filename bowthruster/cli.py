#!/usr/bin/env python3
"""Multi-Round Executive <-> Whiteboard Dogfooding CLI (`bowthruster.cli`)."""

from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

BOWTHRUSTER_HOME = Path(__file__).resolve().parents[1]
REPO_ROOT = (
    Path(os.environ.get("BOWTHRUSTER_PROJECT_ROOT")).resolve()
    if os.environ.get("BOWTHRUSTER_PROJECT_ROOT")
    else BOWTHRUSTER_HOME
)
if str(BOWTHRUSTER_HOME) not in sys.path:
    sys.path.insert(0, str(BOWTHRUSTER_HOME))

import bowthruster.whiteboard as _wb  # noqa: E402
from bowthruster.cli_commands import (  # noqa: E402
    BUILD_STATUSES, STANDING_TYPES, _assemble_board_summary, _build_cli_parser,
    _cmd_bootstrap, _cmd_packets_or_show, _cmd_prove_or_history, _cmd_status,
    _dispatch_read_cmd, _read_input_text, _remove_item_from_board,
)
from bowthruster.git_manifest import (  # noqa: E402
    AST_EXTRACTORS, DEFAULT_CONFIG_PATHS, _PY_AST_CACHE, _build_git_manifest,
    _collect_ref_map, _extract_ast_local_calls, _extract_py_ast_symbols,
    _print_proof_radar, _py_ast_extractor, _run_bend_proof_check,
    _verify_component_files, format_exception_error, read_flight_logs,
    record_flight_event, register_ast_extractor, run_delta_tests,
)
from bowthruster.graph_core import SQLiteGraphStore  # noqa: E402
from bowthruster.nlp import _extract_generic_source_symbols, verify_python_ast_laws_with_bend  # noqa: E402
from bowthruster.the_table import render_lattice_atlas  # noqa: E402
from bowthruster.whiteboard import (  # noqa: E402
    ContextGraph, annotate_active_tasks, claim_component_scope, create_empty_context_graph,
    expand_node, frustration as _wb_frust, ingest as _wb_ingest, mutations as _wb_mut,
    scan_graph, slice_lattice, sync_to_sqlite,
)

SESSIONS_ROOT = Path("/tmp/whiteboard_sessions")
LIMITS_PATH = BOWTHRUSTER_HOME / "size_limits.json"
FLIGHT_LOG_PATH = REPO_ROOT / ".whiteboard" / "logs" / "bowthruster_events.jsonl"


def _get_evaluate_lattice_proof() -> Any:
    return getattr(_wb, "evaluate_lattice_proof", None) or getattr(_wb_frust, "evaluate_lattice_proof", None)


def _get_compile_work_packets() -> Any:
    return getattr(_wb, "compile_work_packets", None) or getattr(_wb_ingest, "compile_work_packets", None)


def _get_transition_build_status() -> Any:
    return (
        getattr(_wb, "transition_component_build_status", None)
        or getattr(_wb_mut, "transition_component_build_status", None)
    )


def _get_render_lattice_atlas() -> Any:
    surf_mod = sys.modules.get("bowthruster.the_table.surface")
    return getattr(surf_mod, "render_lattice_atlas", render_lattice_atlas)


def apply_slice_stale_and_claim(
    graph: dict[str, Any], view: dict[str, Any], sl_kw: dict[str, Any], *,
    mark_stale: bool = False, claim_by: str | None = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    clean_claim = str(claim_by or "").strip() or None
    if not mark_stale and not clean_claim:
        return view, [], []
    scopes_map, c_arg = graph.get("scopes") or {}, str(sl_kw.get("component") or "").strip()
    sym_sc = {str(m.get("scope")) for m in (view.get("matched_symbols") or []) if m.get("scope") in scopes_map}
    comp_sc = {str(c.get("id")) for c in (view.get("component_specs") or [])
        if c.get("id") in scopes_map and c.get("id") != "global_defaults"}
    targets = [c_arg] if (c_arg and c_arg in scopes_map) else sorted(sym_sc or comp_sc)
    if clean_claim:
        for sid in targets:
            sc_d = scopes_map.get(sid) or {}
            if (cur_c := str(sc_d.get("claimed_by") or "").strip()) and cur_c != clean_claim:
                raise ValueError(f"Component {sid!r} is already claimed by {cur_c!r}")
            if not mark_stale and str(sc_d.get("status") or "").upper() == "VERIFIED":
                raise ValueError(f"Cannot claim VERIFIED component {sid!r}; mark it STALE first")
    stale_sids = list(targets) if mark_stale else []
    for sid in stale_sids:
        _get_transition_build_status()(graph, sid, "STALE")
    now_iso, claimed_sids = datetime.now(timezone.utc).isoformat(), (list(targets) if clean_claim else [])
    for sid in claimed_sids:
        claim_component_scope(graph, sid, clean_claim, timestamp_utc=now_iso)
    return (dict(slice_lattice(graph, **sl_kw)) if (stale_sids or claimed_sids) else view), stale_sids, claimed_sids


def apply_status_and_claim(
    graph: dict[str, Any], component: str, new_status: str | None = None, *, opts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg, new_st = opts or {}, str(new_status or "").strip().upper() or None
    raw_c = cfg.get("claimed_by")
    clean_c = str(raw_c).strip() if isinstance(raw_c, str) and raw_c.strip() else None
    rel = bool(cfg.get("release_claim", False)) or bool(clean_c and clean_c.lower() in ("none", "null"))
    force = bool(cfg.get("force_claim", False))
    if not new_st and not clean_c and not rel:
        raise ValueError("status requires 'new_status', 'claimed_by', or 'release_claim=True'")
    scopes_map = graph.get("scopes") or {}
    sid = component if component in scopes_map else (graph.get("alias_index") or {}).get(component.lower())
    if clean_c and not rel:
        if new_st == "VERIFIED":
            raise ValueError(f"Cannot claim VERIFIED component {component!r}; mark it STALE first")
        cur_c = str((scopes_map.get(sid or component) or {}).get("claimed_by") or "").strip()
        if cur_c and cur_c != clean_c and not force:
            raise ValueError(f"Component {sid or component!r} is already claimed by {cur_c!r}")
    snode: dict[str, Any] = {}
    if new_st:
        if (trans_fn := _get_transition_build_status()) is None:
            raise RuntimeError("transition_component_build_status is not yet promoted to bowthruster.whiteboard")
        snode = trans_fn(graph, component, new_st, build_tag=cfg.get("build_tag"),
            core_tag=cfg.get("core_tag"), failure_diagnostic=cfg.get("reason"))
    if rel or clean_c:
        snode = claim_component_scope(graph, component, None if rel else clean_c,
            release=rel, force=force, timestamp_utc=datetime.now(timezone.utc).isoformat())
    return snode


def _call_compile_packets(
    graph: dict[str, Any], *, force: bool = False, status_filter: str | None = None
) -> dict[str, Any]:
    if (pkt_fn := _get_compile_work_packets()) is None:
        raise RuntimeError("compile_work_packets is not yet promoted to bowthruster.whiteboard")
    if status_filter and "status_filter" in inspect.signature(pkt_fn).parameters:
        return pkt_fn(graph, force=force, status_filter=status_filter)
    res = pkt_fn(graph, force=force)
    if not status_filter:
        return res
    sf = status_filter.upper()
    return {**res, "packets": [p for p in (res.get("packets") or []) if str(p.get("build_status") or "").upper() == sf]}


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
    has_env_root = bool(os.environ.get("BOWTHRUSTER_PROJECT_ROOT"))
    is_named = slug not in ("default", "dogfood") and not slug.endswith("_live")
    if is_named and (wb_local.parent.is_dir() or has_env_root):
        wb_local.parent.mkdir(parents=True, exist_ok=True)
    has_session_wb = (bdir / "whiteboard.json").is_file()
    use_local = wb_local.is_file() or ((not has_session_wb or has_env_root) and wb_local.parent.is_dir() and is_named)
    wb_json = wb_local if use_local else bdir / "whiteboard.json"
    return bdir, wb_json, bdir / "whiteboard.sqlite", bdir / "transcript.jsonl"


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


def _load_atlas_boards(
    explicit_board: str | None, boards_csv: str | None, load_all: bool
) -> dict[str, dict[str, Any]]:
    vault_wb = REPO_ROOT / ".whiteboard"
    if boards_csv:
        names = [x.strip() for x in boards_csv.split(",") if x.strip()]
    elif not load_all and explicit_board:
        names = [explicit_board]
    else:
        roots = (vault_wb,) if vault_wb.is_dir() else (SESSIONS_ROOT,)
        cands = sorted({
            p.stem if p.name != "whiteboard.json" else p.parent.name
            for r in roots if r.is_dir() for p in r.rglob("*.json")
        })
        names = [n for n in cands if n not in ("default", "dogfood") and not n.endswith("_live")] or [
            explicit_board or "default"
        ]
    out: dict[str, dict[str, Any]] = {}
    for n in names:
        cand_paths = (vault_wb / f"{n}.json", vault_wb / n / "whiteboard.json", SESSIONS_ROOT / n / "whiteboard.json")
        if cand := next((c for c in cand_paths if c.is_file()), None):
            d = json.loads(cand.read_text(encoding="utf-8"))
            has_scopes = [k for k in (d.get("scopes") or {}) if k != "global_defaults"]
            if boards_csv or (not load_all and explicit_board) or has_scopes or d.get("flows"):
                out[n] = d
        elif boards_csv or (not load_all and explicit_board):
            out[n] = dict(_load_board(n)[0])
    return out or {explicit_board or "default": dict(_load_board(explicit_board or "default")[0])}


def _read_transcript(p: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.is_file() else []


def _append_transcript(p: Path, record: dict[str, Any]) -> None:
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _execute_turn(
    board: str, speaker: str, raw_text: str, round_num: int | None = None,
    profile: str | None = None, config_path: str | None = None,
) -> int:
    from bowthruster.the_executive import run_executive_turn

    _ = (profile, config_path)
    with _board_lock(board):
        graph, wb_json, wb_sqlite, transcript_path = _load_board(board)
        history = _read_transcript(transcript_path)
        if round_num is None:
            last_turn = next((h for h in reversed(history) if h.get("event", "turn") == "turn"), None)
            is_retry = bool(last_turn and last_turn.get("decision") == "SEND_BACK_TO_USER")
            round_num = int(last_turn.get("round", 1)) + 1 if is_retry and last_turn else 1
        t0 = time.perf_counter()
        res = run_executive_turn(graph, raw_text, db_path=wb_sqlite)
        elapsed_s = round(time.perf_counter() - t0, 3)
        if res["whiteboard_mutated"]:
            _save_board(res["whiteboard_graph"], wb_json, wb_sqlite)
        eval_fn = _get_evaluate_lattice_proof()
        proof = res.get("lattice_proof") or (eval_fn(res["whiteboard_graph"]) if eval_fn else None)
        parsed_mcus = (res.get("intake_packet") or {}).get("parsed_mcus") or []
        _append_transcript(
            transcript_path,
            {
                "event": "turn", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "speaker": speaker, "round": round_num, "turn_index": res["turn_index"],
                "raw_text": raw_text, "decision": res["decision"], "bounce_reason": res["bounce_reason"],
                "whiteboard_mutated": res["whiteboard_mutated"], "user_message": res["user_message"],
                "cited_items": res["cited_items"], "committed_items": res.get("committed_items") or [],
                "conflicts": res.get("conflicts") or [], "flawed_ideas": res.get("flawed_ideas") or [],
                "clarification_questions": res.get("clarification_questions") or [],
                "open_questions": res.get("open_questions") or [], "parsed_mcus": parsed_mcus,
                "lattice_proof_status": (proof or {}).get("status"), "elapsed_s": elapsed_s,
            },
        )
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
        scope_h, prop_v = m.get("scope_hint") or "auto", m.get("target_prop")
        print(f"  - [mcu_{m.get('mcu_id')}] {m.get('type')} scope={scope_h} prop={prop_v!r}: {m.get('text')}")
    diag_keys = (
        ("Conflicts", "conflicts"), ("Flawed Ideas", "flawed_ideas"),
        ("Clarification Questions", "clarification_questions"), ("Open Questions", "open_questions"),
    )
    for label, key in diag_keys:
        if res.get(key):
            items_fmt = "\n".join(
                f"  * {json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item}" for item in res[key]
            )
            print(f"{label} ({len(res[key])}):\n{items_fmt}")
    _print_proof_radar(proof)
    return 0


def execute_git_sync(
    board: str, mode: str, manifest_arg: str | None = None, *,
    mark_verified: bool = False, active_root: Path | None = None,
) -> tuple[dict[str, Any], str, bool]:
    """Shared `git-sync` engine invoked by CLI and MCP (`pre`, `post`, `baseline`, `drift`, `install_hook`)."""
    from bowthruster.ai_memory import sync_git_diff_uris
    from bowthruster.git_manifest import install_git_precommit_hook
    from bowthruster.the_executive import detect_working_tree_drift, sync_post_commit_to_lattice
    from bowthruster.the_executive.gate import evaluate_git_commit_gate

    eff_root = active_root or REPO_ROOT
    if mode == "install_hook":
        hp = install_git_precommit_hook(eff_root, board, overwrite=True)
        res = {"board": board, "hook_installed": hp is not None, "hook_path": str(hp) if hp else None}
        return res, f"[GIT HOOK '{board}'] installed={res['hook_installed']} path={res['hook_path']}", True
    graph, wb_json, wb_sqlite, _ = _load_board(board)
    manifest = _build_git_manifest(mode if mode in ("pre", "post", "baseline") else "drift", manifest_arg, graph)
    if mode == "pre":
        uri_rep = sync_git_diff_uris(copy.deepcopy(graph), manifest)
        gate_res = dict(evaluate_git_commit_gate(graph, manifest, uri_sync_report=uri_rep))
        allow = bool(gate_res.get("allowed", True))
        if allow and mark_verified:
            stale_sids = [
                sid for sid, sc in sorted((graph.get("scopes") or {}).items())
                if sid != "global_defaults"
                and (
                    str(sc.get("status") or "").upper() in ("STALE", "READY")
                    or (
                        bool(sc.get("files") or (sc.get("attributes") or {}).get("files"))
                        and str(sc.get("status") or "DRAFT").upper() != "VERIFIED"
                    )
                )
            ]
            t_ran, t_ok, t_out = run_delta_tests(eff_root, stale_sids, graph)
            gate_res["delta_tests_ran"] = t_ran
            gate_res["delta_tests_passed"] = t_ok
            t_status = "PASSED" if t_ok is True else ("FAILED" if t_ok is False else "NOT_CHECKED")
            gate_res["delta_tests_status"] = t_status
            if t_ok is False:
                allow = False
                gate_res["allowed"] = False
                gate_res["decision"] = "REJECT_COMMIT"
                gate_res.setdefault("violations", []).append({
                    "kind": "TEST_FAILURE", "scope": stale_sids[0] if stale_sids else "global_defaults",
                    "path": "tests/", "description": f"Delta pytest failed:\n{t_out[-600:]}",
                })
            else:
                trans_fn = _get_transition_build_status()
                for sid in stale_sids:
                    _verify_component_files(graph, sid)
                    trans_fn(graph, sid, "VERIFIED")
                    if t_ok == "not_checked":
                        graph["scopes"][sid]["verified_untested"] = True
                if stale_sids:
                    _save_board(graph, wb_json, wb_sqlite)
                gate_res["marked_verified_scopes"] = stale_sids
                if t_ok == "not_checked":
                    gate_res["verified_untested_scopes"] = stale_sids
        unreg = gate_res.get("unregistered_public_symbols") or []
        unreg_str = ", ".join(f"{u.get('scope')}:{u.get('symbol')}" for u in unreg)
        ver_scopes = gate_res.get("marked_verified_scopes")
        ver_sfx = f" marked_verified={ver_scopes}" if ver_scopes else ""
        viol_sfx = f"\nviolations={json.dumps(gate_res.get('violations'))}" if gate_res.get("violations") else ""
        unreg_sfx = f"\nunregistered_public_symbols ({len(unreg)}): {unreg_str}" if unreg else ""
        n_viols = len(gate_res.get("violations") or [])
        pre_txt = (
            f"[GIT PRE-COMMIT GATE '{board}'] allowed={allow} decision={gate_res.get('decision')} "
            f"bounds_ok={gate_res.get('bounds_ok')} contracts_ok={gate_res.get('contracts_ok')} "
            f"bend_proofs_ok={gate_res.get('bend_proofs_ok')} "
            f"no_dangling_deletes={gate_res.get('no_dangling_deletes')} "
            f"flow_schema_checked={gate_res.get('flow_schema_checked')} violations={n_viols}"
            f"{ver_sfx}{viol_sfx}{unreg_sfx}"
        )
        return gate_res, pre_txt, allow
    if mode in ("post", "baseline"):
        sync_res = dict(sync_post_commit_to_lattice(graph, manifest, store=SQLiteGraphStore(wb_sqlite)))
        wb_out = sync_res.get("whiteboard_graph") or graph
        if sync_res.get("commit_sha"):
            wb_out["last_commit_sha"] = sync_res["commit_sha"]
        _save_board(wb_out, wb_json, wb_sqlite)
        out_d = {k: v for k, v in sync_res.items() if k != "whiteboard_graph"}
        post_txt = (
            f"[GIT POST-COMMIT SYNC '{board}'] sha={out_d.get('commit_sha')} "
            f"modified={out_d.get('modified_scopes')} cascaded_stale={out_d.get('cascaded_stale_scopes')}"
        )
        return out_d, post_txt, True
    drift_res = dict(detect_working_tree_drift(graph, manifest))
    drift_txt = (
        f"[GIT DRIFT '{board}'] has_drift={drift_res.get('has_drift')} "
        f"drifted_scopes={drift_res.get('drifted_scopes')} "
        f"uncommitted={len(drift_res.get('uncommitted_files') or [])} dangling_uris={drift_res.get('dangling_uris')}"
    )
    return drift_res, drift_txt, True


def main() -> int:
    args = _build_cli_parser().parse_args()
    explicit_board = getattr(args, "board", None)
    profile, config_path = getattr(args, "profile", None), getattr(args, "config", None)
    board, t0_cmd = explicit_board or "default", time.perf_counter()
    try:
        if args.cmd in ("logs", "atlas", "slice"):
            return _dispatch_read_cmd(args, board, explicit_board, t0_cmd)
        if args.cmd == "git-sync":
            if getattr(args, "install_hook", False):
                mode = "install_hook"
            elif args.pre_commit:
                mode = "pre"
            elif args.baseline:
                mode = "baseline"
            elif args.post_commit:
                mode = "post"
            else:
                mode = "drift"
            payload, txt, allow = execute_git_sync(board, mode, args.manifest, mark_verified=bool(args.mark_verified))
            mod_sc = payload.get("marked_verified_scopes", payload.get("modified_scopes"))
            record_flight_event(
                board, f"git-sync:{mode}", (time.perf_counter() - t0_cmd) * 1000.0, "OK" if allow else "REJECTED",
                decision=payload.get("decision"), violations=payload.get("violations"),
                modified_scopes=mod_sc or payload.get("drifted_scopes"),
                stale_cascaded_scopes=payload.get("cascaded_stale_scopes"),
            )
            print(json.dumps(payload, indent=2, sort_keys=True) if args.as_json else txt)
            return 0 if allow else 1
        if args.cmd == "reset":
            bdir, wb_json, wb_sqlite, transcript_path = _board_paths(board)
            for p in (wb_json, wb_sqlite, transcript_path, bdir / "LATTICE_PROOF.bend"):
                p.unlink(missing_ok=True)
            _save_board(create_empty_context_graph(store=SQLiteGraphStore(wb_sqlite)), wb_json, wb_sqlite)
            record_flight_event(board, "reset", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            print(f"Reset Whiteboard session '{board}' at {bdir}")
            return 0
        if args.cmd == "turn":
            msg = _read_input_text(args.message, args.file)
            return _execute_turn(board, args.speaker, msg, args.round_num, profile, config_path)
        if args.cmd in ("revoke", "replace"):
            graph, wb_json, wb_sqlite, transcript_path = _load_board(board)
            removed = _remove_item_from_board(graph, args.rule_id)
            if not removed:
                raise KeyError(f"Item {args.rule_id!r} not found on Whiteboard '{board}'")
            _save_board(graph, wb_json, wb_sqlite)
            _append_transcript(
                transcript_path,
                {
                    "event": args.cmd, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "speaker": args.speaker, "rule_id": args.rule_id, "removed": removed, "reason": args.message or "",
                },
            )
            record_flight_event(board, args.cmd, (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            print(f"[{args.cmd.upper()}] Removed from '{board}': {'; '.join(removed)}")
            if args.cmd == "replace":
                msg = _read_input_text(args.message, args.file)
                return _execute_turn(board, args.speaker, msg, args.round_num, profile, config_path)
            return 0
        if args.cmd == "bootstrap":
            return _cmd_bootstrap(args, board, t0_cmd)
        if args.cmd == "status":
            return _cmd_status(args, board, t0_cmd)
        if args.cmd in ("packets", "show"):
            return _cmd_packets_or_show(args, board, t0_cmd)
        if args.cmd in ("prove", "history"):
            return _cmd_prove_or_history(args, board, t0_cmd)
    except Exception as exc:
        dur_ms = (time.perf_counter() - t0_cmd) * 1000.0
        record_flight_event(board, args.cmd, dur_ms, "ERROR", error=format_exception_error(exc))
        raise
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
