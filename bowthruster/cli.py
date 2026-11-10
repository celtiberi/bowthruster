#!/usr/bin/env python3
"""Multi-Round Executive <-> Whiteboard Dogfooding CLI (`bowthruster.cli`)."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

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
    BUILD_STATUSES,
    STANDING_TYPES,
    _assemble_board_summary,
    _build_cli_parser,
    _cmd_bootstrap,
    _cmd_packets_or_show,
    _cmd_prove_or_history,
    _cmd_status,
    _dispatch_read_cmd,
    _read_input_text,
    _remove_item_from_board,
)
from bowthruster.cli_session import (  # noqa: E402
    _append_transcript,
    _board_lock,
    _board_paths,
    _execute_turn,
    _load_atlas_boards,
    _load_board,
    _read_transcript,
    _save_board,
)
from bowthruster.git_manifest import (  # noqa: E402
    AST_EXTRACTORS,
    DEFAULT_CONFIG_PATHS,
    _PY_AST_CACHE,
    _build_git_manifest,
    _collect_ref_map,
    _extract_ast_local_calls,
    _extract_py_ast_symbols,
    _load_gate_config,
    _print_proof_radar,
    _py_ast_extractor,
    _run_bend_proof_check,
    _verify_component_files,
    format_exception_error,
    format_undeclared_test_warnings,
    read_flight_logs,
    record_flight_event,
    register_ast_extractor,
    resolve_component_tests,
    run_delta_tests,
)
from bowthruster.graph_core import SQLiteGraphStore  # noqa: E402
from bowthruster.nlp import _extract_generic_source_symbols, verify_python_ast_laws_with_bend  # noqa: E402
from bowthruster.the_table import render_lattice_atlas  # noqa: E402
from bowthruster.whiteboard import (  # noqa: E402
    ContextGraph,
    annotate_active_tasks,
    claim_component_scope,
    create_empty_context_graph,
    expand_node,
    frustration as _wb_frust,
    ingest as _wb_ingest,
    mutations as _wb_mut,
    scan_graph,
    slice_lattice,
    sync_to_sqlite,
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
    graph: dict[str, Any],
    view: dict[str, Any],
    sl_kw: dict[str, Any],
    *,
    mark_stale: bool = False,
    claim_by: str | None = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    clean_claim = str(claim_by or "").strip() or None
    if not mark_stale and not clean_claim:
        return view, [], []
    scopes_map = graph.get("scopes") or {}
    c_arg = str(sl_kw.get("component") or "").strip()
    sym_sc = {
        str(m.get("scope"))
        for m in (view.get("matched_symbols") or [])
        if m.get("scope") in scopes_map
    }
    comp_sc = {
        str(c.get("id"))
        for c in (view.get("component_specs") or [])
        if c.get("id") in scopes_map and c.get("id") != "global_defaults"
    }
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
    now_iso = datetime.now(timezone.utc).isoformat()
    claimed_sids = list(targets) if clean_claim else []
    for sid in claimed_sids:
        claim_component_scope(graph, sid, clean_claim, timestamp_utc=now_iso)
    out_view = dict(slice_lattice(graph, **sl_kw)) if (stale_sids or claimed_sids) else view
    return out_view, stale_sids, claimed_sids


def apply_status_and_claim(
    graph: dict[str, Any],
    component: str,
    new_status: str | None = None,
    *,
    opts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = opts or {}
    new_st = str(new_status or "").strip().upper() or None
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
        snode = trans_fn(
            graph,
            component,
            new_st,
            build_tag=cfg.get("build_tag"),
            core_tag=cfg.get("core_tag"),
            failure_diagnostic=cfg.get("reason"),
        )
    if rel or clean_c:
        snode = claim_component_scope(
            graph,
            component,
            None if rel else clean_c,
            release=rel,
            force=force,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
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
    pkts = [p for p in (res.get("packets") or []) if str(p.get("build_status") or "").upper() == sf]
    return {**res, "packets": pkts}


def execute_git_sync(
    board: str,
    mode: str,
    manifest_arg: str | None = None,
    *,
    mark_verified: bool = False,
    active_root: Path | None = None,
) -> tuple[dict[str, Any], str, bool]:
    """Shared `git-sync` engine invoked by CLI and MCP (`pre`, `post`, `baseline`, `drift`, `install_hook`)."""
    from bowthruster.ai_memory import sync_git_diff_uris
    from bowthruster.git_manifest import install_git_precommit_hook
    from bowthruster.the_executive import detect_working_tree_drift, sync_post_commit_to_lattice
    from bowthruster.the_executive.gate import evaluate_git_commit_gate

    eff_root = (active_root or REPO_ROOT).resolve()
    if mode == "install_hook":
        hp = install_git_precommit_hook(eff_root, board, overwrite=True)
        res = {"board": board, "hook_installed": hp is not None, "hook_path": str(hp) if hp else None}
        return res, f"[GIT HOOK '{board}'] installed={res['hook_installed']} path={res['hook_path']}", True
    if mode == "pre" and manifest_arg is None:
        _, wb_check, _, _ = _board_paths(board, create_dirs=False)
        if not wb_check.is_file():
            viol = {
                "kind": "MISSING_BOARD",
                "scope": "global_defaults",
                "path": str(wb_check),
                "description": f"Named board {board!r} does not exist at {wb_check}.",
            }
            missing_res = {
                "allowed": False,
                "decision": "REJECT_COMMIT",
                "bounce_reason": "MISSING_BOARD",
                "bounds_ok": True,
                "contracts_ok": True,
                "bend_proofs_ok": True,
                "no_dangling_deletes": True,
                "flow_schema_checked": False,
                "violations": [viol],
            }
            msg = (
                f"[GIT PRE-COMMIT GATE '{board}'] allowed=False decision=REJECT_COMMIT "
                f"bounce_reason=MISSING_BOARD violations=1\nviolations={json.dumps([viol])}"
            )
            return missing_res, msg, False
    with _board_lock(board):
        graph, wb_json, wb_sqlite, _ = _load_board(board)
    eff_mode = mode if mode in ("pre", "post", "baseline") else "drift"
    manifest = _build_git_manifest(eff_mode, manifest_arg, graph)
    if mode == "pre":
        req_cg_laws = bool(_load_gate_config(eff_root).get("require_call_graph_laws", True))
        uri_rep = sync_git_diff_uris(copy.deepcopy(graph), manifest)
        gate_res = dict(
            evaluate_git_commit_gate(
                graph, manifest, uri_sync_report=uri_rep, require_call_graph_laws=req_cg_laws
            )
        )
        allow = bool(gate_res.get("allowed", True))
        all_comp_sids = [
            sid for sid in sorted((graph.get("scopes") or {}).keys()) if sid != "global_defaults"
        ]
        _, _, board_untested, undeclared_map = resolve_component_tests(eff_root, all_comp_sids, graph)
        warnings = format_undeclared_test_warnings(undeclared_map)
        if undeclared_map:
            gate_res["undeclared_matching_tests"] = undeclared_map
        if warnings:
            gate_res["warnings"] = warnings
        if allow and mark_verified:
            stale_sids = [
                sid
                for sid, sc in sorted((graph.get("scopes") or {}).items())
                if sid != "global_defaults"
                and (
                    str(sc.get("status") or "").upper() in ("STALE", "READY")
                    or (
                        bool(sc.get("files") or (sc.get("attributes") or {}).get("files"))
                        and str(sc.get("status") or "DRAFT").upper() != "VERIFIED"
                    )
                )
            ]
            _, _, untested_sids, _ = resolve_component_tests(eff_root, stale_sids, graph)
            t_ran, t_ok, t_out = run_delta_tests(eff_root, stale_sids, graph)
            gate_res["delta_tests_ran"] = t_ran
            gate_res["delta_tests_passed"] = t_ok
            if t_ok is False:
                t_status = "FAILED"
            elif t_ok is True and untested_sids:
                t_status = "PARTIAL_UNTESTED"
            elif t_ok is True:
                t_status = "PASSED"
            else:
                t_status = "NOT_CHECKED"
            gate_res["delta_tests_status"] = t_status
            gate_res["untested_scopes"] = untested_sids
            if t_ok is False:
                allow = False
                gate_res["allowed"] = False
                gate_res["decision"] = "REJECT_COMMIT"
                gate_res.setdefault("violations", []).append({
                    "kind": "TEST_FAILURE",
                    "scope": stale_sids[0] if stale_sids else "global_defaults",
                    "path": "tests/",
                    "description": f"Delta pytest failed:\n{t_out[-600:]}",
                })
            else:
                trans_fn = _get_transition_build_status()
                for sid in stale_sids:
                    _verify_component_files(graph, sid)
                    trans_fn(graph, sid, "VERIFIED")
                    graph["scopes"][sid]["verified_untested"] = bool(sid in untested_sids)
                if stale_sids:
                    with _board_lock(board):
                        _save_board(graph, wb_json, wb_sqlite)
                gate_res["marked_verified_scopes"] = stale_sids
                if untested_sids:
                    gate_res["verified_untested_scopes"] = untested_sids
        elif board_untested:
            gate_res["untested_scopes"] = board_untested
        unreg = gate_res.get("unregistered_public_symbols") or []
        unreg_str = ", ".join(f"{u.get('scope')}:{u.get('symbol')}" for u in unreg)
        ver_scopes = gate_res.get("marked_verified_scopes")
        ver_sfx = f" marked_verified={ver_scopes}" if ver_scopes else ""
        dt_st = gate_res.get("delta_tests_status")
        dt_sfx = f" delta_tests={dt_st}" if dt_st else ""
        unt_sc = gate_res.get("untested_scopes") or []
        unt_sfx = f" untested_scopes={unt_sc}" if unt_sc else ""
        viol_sfx = f"\nviolations={json.dumps(gate_res.get('violations'))}" if gate_res.get("violations") else ""
        unreg_sfx = f"\nunregistered_public_symbols ({len(unreg)}): {unreg_str}" if unreg else ""
        warn_sfx = ("\n" + "\n".join(f"[WARNING]: {w}" for w in warnings)) if warnings else ""
        n_viols = len(gate_res.get("violations") or [])
        pre_txt = (
            f"[GIT PRE-COMMIT GATE '{board}'] allowed={allow} decision={gate_res.get('decision')} "
            f"bounds_ok={gate_res.get('bounds_ok')} contracts_ok={gate_res.get('contracts_ok')} "
            f"bend_proofs_ok={gate_res.get('bend_proofs_ok')} "
            f"no_dangling_deletes={gate_res.get('no_dangling_deletes')} "
            f"flow_schema_checked={gate_res.get('flow_schema_checked')} violations={n_viols}"
            f"{ver_sfx}{dt_sfx}{unt_sfx}{viol_sfx}{unreg_sfx}{warn_sfx}"
        )
        return gate_res, pre_txt, allow
    if mode in ("post", "baseline"):
        sync_res = dict(sync_post_commit_to_lattice(graph, manifest, store=SQLiteGraphStore(wb_sqlite)))
        wb_out = sync_res.get("whiteboard_graph") or graph
        if sync_res.get("commit_sha"):
            wb_out["last_commit_sha"] = sync_res["commit_sha"]
        with _board_lock(board):
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
        f"uncommitted={len(drift_res.get('uncommitted_files') or [])} "
        f"dangling_uris={drift_res.get('dangling_uris')}"
    )
    return drift_res, drift_txt, True


def main(argv: list[str] | None = None) -> int:
    global REPO_ROOT
    prev_root = REPO_ROOT
    prev_env = os.environ.get("BOWTHRUSTER_PROJECT_ROOT")
    args = _build_cli_parser().parse_args(argv)
    proj_root_arg = getattr(args, "project_root", None)
    if proj_root_arg:
        REPO_ROOT = Path(str(proj_root_arg)).expanduser().resolve()
        os.environ["BOWTHRUSTER_PROJECT_ROOT"] = str(REPO_ROOT)
    elif not prev_env:
        cwd = Path.cwd().resolve()
        if (cwd / ".whiteboard").is_dir() or (cwd / ".git").exists():
            REPO_ROOT = cwd
    explicit_board = getattr(args, "board", None)
    profile, config_path = getattr(args, "profile", None), getattr(args, "config", None)
    board, t0_cmd = explicit_board or "default", time.perf_counter()
    try:
        if args.cmd in ("logs", "atlas", "slice"):
            return _dispatch_read_cmd(args, board, explicit_board, t0_cmd)
        if args.cmd == "git-sync":
            modes = (
                (getattr(args, "install_hook", False), "install_hook"),
                (args.pre_commit, "pre"),
                (args.baseline, "baseline"),
                (args.post_commit, "post"),
            )
            mode = next((m for flag, m in modes if flag), "drift")
            payload, txt, allow = execute_git_sync(
                board, mode, args.manifest, mark_verified=bool(args.mark_verified)
            )
            mod_sc = payload.get("marked_verified_scopes", payload.get("modified_scopes"))
            record_flight_event(
                board,
                f"git-sync:{mode}",
                (time.perf_counter() - t0_cmd) * 1000.0,
                "OK" if allow else "REJECTED",
                decision=payload.get("decision"),
                violations=payload.get("violations"),
                modified_scopes=mod_sc or payload.get("drifted_scopes"),
                stale_cascaded_scopes=payload.get("cascaded_stale_scopes"),
            )
            print(json.dumps(payload, indent=2, sort_keys=True) if args.as_json else txt)
            return 0 if allow else 1
        if args.cmd == "reset":
            bdir, wb_json, wb_sqlite, transcript_path = _board_paths(board)
            for p in (wb_json, wb_sqlite, Path(f"{wb_sqlite}-wal"), Path(f"{wb_sqlite}-shm"), transcript_path):
                p.unlink(missing_ok=True)
            (bdir / "LATTICE_PROOF.bend").unlink(missing_ok=True)
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
                    "event": args.cmd,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "speaker": args.speaker,
                    "rule_id": args.rule_id,
                    "removed": removed,
                    "reason": args.message or "",
                },
            )
            record_flight_event(board, args.cmd, (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            print(f"[{args.cmd.upper()}] Removed from '{board}': {'; '.join(removed)}")
            if args.cmd == "replace":
                msg = _read_input_text(args.message, args.file)
                return _execute_turn(board, args.speaker, msg, args.round_num, profile, config_path)
            return 0
        if args.cmd in ("bootstrap", "migrate"):
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
    finally:
        if argv is not None:
            REPO_ROOT = prev_root
            if prev_env is None:
                os.environ.pop("BOWTHRUSTER_PROJECT_ROOT", None)
            else:
                os.environ["BOWTHRUSTER_PROJECT_ROOT"] = prev_env
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
