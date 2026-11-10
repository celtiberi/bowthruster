"""Subcommand execution handlers for the BowThruster CLI (`bowthruster.cli_commands`)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import time
from typing import Any

from bowthruster.cli_parser import (
    BUILD_STATUSES,
    STANDING_TYPES,
    _assemble_board_summary,
    _build_cli_parser,
    _read_input_text,
    _remove_item_from_board,
)
from bowthruster.whiteboard import claim_component_scope, slice_lattice

__all__ = [
    "BUILD_STATUSES",
    "STANDING_TYPES",
    "_assemble_board_summary",
    "_build_cli_parser",
    "_cmd_bootstrap",
    "_cmd_packets_or_show",
    "_cmd_prove_or_history",
    "_cmd_status",
    "_dispatch_read_cmd",
    "_read_input_text",
    "_remove_item_from_board",
    "compile_and_claim_packets",
]


def compile_and_claim_packets(
    graph: dict[str, Any], opts: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    import bowthruster.cli as _cli

    force = bool(opts.get("force", False))
    status_f = str(opts["status"]).strip().upper() if opts.get("status") else None
    wave_f = opts.get("wave")
    comp_f = str(opts.get("component") or "").strip() or None
    unclaimed = bool(opts.get("unclaimed_only", False))
    clean_claim = str(opts.get("claim_by") or "").strip() or None
    res = _cli._call_compile_packets(graph, force=force, status_filter=status_f)
    pkts = [
        p
        for p in (res.get("packets") or [])
        if (wave_f is None or p.get("wave") == int(wave_f))
        and (not comp_f or p.get("component") == comp_f)
        and (not status_f or str(p.get("build_status") or "").upper() == status_f)
        and (not unclaimed or not p.get("claimed_by") or (clean_claim and p.get("claimed_by") == clean_claim))
    ]
    if opts.get("strip_wave0") and any(p.get("wave") in (1, 2) for p in pkts):
        pkts = [p for p in pkts if p.get("wave") != 0]
    claimed_sids: list[str] = []
    if clean_claim and (res.get("ready_to_emit") or force):
        scopes_map = graph.get("scopes") or {}
        targets = [p for p in pkts if p.get("wave") != 0 and str(p.get("component") or "") in scopes_map]
        for p in targets:
            cid = str(p["component"])
            sc_d = scopes_map.get(cid) or {}
            if str(sc_d.get("status") or "").upper() == "VERIFIED":
                raise ValueError(f"Cannot claim VERIFIED component {cid!r}; mark it STALE first")
            if (cur_c := str(sc_d.get("claimed_by") or "").strip()) and cur_c != clean_claim:
                raise ValueError(f"Component {cid!r} is already claimed by {cur_c!r}")
        now_iso = datetime.now(timezone.utc).isoformat()
        for p in targets:
            cid = str(p["component"])
            snode = claim_component_scope(graph, cid, clean_claim, timestamp_utc=now_iso)
            p["claimed_by"], p["claimed_at"] = snode.get("claimed_by"), snode.get("claimed_at")
            claimed_sids.append(cid)
    return res, pkts, claimed_sids


def _cmd_bootstrap(args: argparse.Namespace, board: str, t0_cmd: float) -> int:
    import bowthruster.cli as _cli

    if args.cmd == "migrate":
        from bowthruster.whiteboard.migrations import format_migration_report, migrate_repo_boards

        pay = migrate_repo_boards(
            _cli.REPO_ROOT, board=board, migrate_all=args.migrate_all, dry_run=args.dry_run
        )
        _cli.record_flight_event(board, "migrate", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
        out_m = (
            json.dumps(pay, indent=2, sort_keys=True, ensure_ascii=False)
            if args.as_json
            else format_migration_report(pay)
        )
        print(out_m)
        return 0
    from bowthruster.bootstrap import bootstrap_repository, format_bootstrap_report

    graph, wb_json, wb_sqlite, _ = _cli._load_board(board)
    payload = bootstrap_repository(_cli.REPO_ROOT, board, graph, component=args.component)
    _cli._save_board(graph, wb_json, wb_sqlite)
    _cli.record_flight_event(
        board, "bootstrap", (time.perf_counter() - t0_cmd) * 1000.0, "OK", decision=payload.get("status")
    )
    out_str = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        if args.as_json
        else format_bootstrap_report(payload)
    )
    print(out_str)
    return 0


def _cmd_status(args: argparse.Namespace, board: str, t0_cmd: float) -> int:
    import bowthruster.cli as _cli

    ver_info: dict[str, Any] = {}
    with _cli._board_lock(board):
        graph, wb_json, wb_sqlite, transcript_path = _cli._load_board(board)
        if args.new_status == "VERIFIED":
            ver_info = _cli._verify_component_files(graph, args.component) or {}
        prev_tid = (graph.get("scopes", {}).get(args.component) or {}).get("active_ticket_id")
        has_pkt_fn = bool(_cli._get_compile_work_packets())
        prev_ready = (
            {
                str(p.get("packet_id"))
                for p in (_cli._call_compile_packets(graph, force=True, status_filter="READY").get("packets") or [])
            }
            if has_pkt_fn
            else set()
        )
        snode = _cli.apply_status_and_claim(graph, args.component, args.new_status, opts=vars(args))
        if str(snode.get("status") or "").upper() == "VERIFIED":
            snode["verified_untested"] = bool(ver_info.get("verified_untested", False))
        if prev_tid and not snode.get("active_ticket_id"):
            snode["active_ticket_id"] = prev_tid
        all_pkts = (_cli._call_compile_packets(graph, force=True).get("packets") or []) if has_pkt_fn else []
        ready_pkts = [p for p in all_pkts if str(p.get("build_status") or "").upper() == "READY"]
        unlocked_pkts = [p for p in ready_pkts if str(p.get("packet_id")) not in prev_ready]
        _cli._save_board(graph, wb_json, wb_sqlite)
    raw_st = snode.get("status")
    is_untested = bool(snode.get("verified_untested", False))
    disp_st = "VERIFIED (untested)" if (raw_st == "VERIFIED" and is_untested) else raw_st
    warnings = list(ver_info.get("warnings") or [])
    undeclared_tests = list(ver_info.get("undeclared_matching_tests") or [])
    payload: dict[str, Any] = {
        "component": args.component,
        "status": raw_st,
        "display_status": disp_st,
        "verified_untested": is_untested,
        "claimed_by": snode.get("claimed_by"),
        "build_tag": snode.get("build_tag"),
        "core_tag": snode.get("core_tag"),
        "failure_diagnostic": snode.get("failure_diagnostic"),
        "active_ticket_id": snode.get("active_ticket_id"),
        "ticket_info": {"ticket_id": snode.get("active_ticket_id")},
        "ready_packets": [p.get("packet_id") for p in ready_pkts],
        "newly_unlocked_packets": [p.get("packet_id") for p in unlocked_pkts],
    }
    if undeclared_tests:
        payload["undeclared_matching_tests"] = undeclared_tests
    if warnings:
        payload["warnings"] = warnings
    _cli._append_transcript(
        transcript_path,
        {"event": "status", "timestamp_utc": datetime.now(timezone.utc).isoformat(), **payload},
    )
    dur_ms = (time.perf_counter() - t0_cmd) * 1000.0
    _cli.record_flight_event(
        board,
        f"status:{args.component}",
        dur_ms,
        "OK",
        decision=raw_st,
        modified_scopes=[args.component],
    )
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    unlocked_items = ", ".join(f"{p.get('packet_id')} (owner={p.get('owner')})" for p in unlocked_pkts)
    unlocked_sfx = (
        f"\n[NEWLY UNLOCKED READY PACKETS ({len(unlocked_pkts)})]: {unlocked_items}"
        if unlocked_pkts
        else ""
    )
    warn_sfx = ("\n" + "\n".join(f"[WARNING]: {w}" for w in warnings)) if warnings else ""
    ready_ids = ", ".join(str(p.get("packet_id")) for p in ready_pkts) or "none"
    print(
        f"[STATUS] {args.component} -> status={disp_st} claimed_by={snode.get('claimed_by')} "
        f"build_tag={snode.get('build_tag')} core_tag={snode.get('core_tag')} "
        f"ticket={snode.get('active_ticket_id')} "
        f"failure_diagnostic={snode.get('failure_diagnostic')!r}{warn_sfx}{unlocked_sfx}\n"
        f"[READY PACKETS ({len(ready_pkts)})]: {ready_ids}"
    )
    return 0


def _cmd_packets_or_show(args: argparse.Namespace, board: str, t0_cmd: float) -> int:
    import bowthruster.cli as _cli

    if args.cmd == "packets":
        with _cli._board_lock(board):
            graph, wb_json, wb_sqlite, _ = _cli._load_board(board)
            res, pkts, claimed = compile_and_claim_packets(graph, vars(args))
            if claimed:
                _cli._save_board(graph, wb_json, wb_sqlite)
        st = "OK" if res.get("ready_to_emit") or args.force else "REJECTED"
        dur_ms = (time.perf_counter() - t0_cmd) * 1000.0
        _cli.record_flight_event(
            board, "packets", dur_ms, st, decision=res.get("status"), modified_scopes=claimed or None
        )
        if args.as_json:
            has_f = any((args.wave is not None, args.component, args.status, args.unclaimed_only, claimed))
            extra = {"claimed_scopes": claimed} if claimed else {}
            out_res = {**res, "packets": pkts, **extra} if has_f else res
            print(json.dumps(out_res, indent=2, sort_keys=True, ensure_ascii=False))
            return 0
        if not res.get("ready_to_emit") and not args.force:
            print(
                f"=== WORK PACKETS LOCKED ('{board}') ===\n"
                f"Status: {res.get('status')} (requires PROVEN_COMPLETE; pass --force to preview draft packets)"
            )
            _cli._print_proof_radar(res.get("lattice_proof") or res)
            return 0
        print(
            f"=== WORK PACKETS '{board}' ({len(pkts)} packets | "
            f"status={res.get('status')} | ready_to_emit={res.get('ready_to_emit')}) ==="
        )
        for p in pkts:
            print(
                f"\n[{p.get('packet_id')}] wave={p.get('wave')} kind={p.get('kind')} "
                f"component={p.get('component')} owner={p.get('owner')} stage={p.get('stage')} "
                f"build_status={p.get('build_status')} claimed_by={p.get('claimed_by')} "
                f"build_tag={p.get('build_tag')} core_tag={p.get('core_tag')} "
                f"failure_diagnostic={p.get('failure_diagnostic')!r}\n{p.get('prompt_slice') or ''}"
            )
        return 0
    eval_fn = _cli._get_evaluate_lattice_proof()
    summary = _assemble_board_summary(_cli._load_board(board)[0], args.scope, eval_fn)
    _cli.record_flight_event(board, "show", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
    if args.as_json:
        print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    print(f"=== WHITEBOARD '{board}' (Turn {summary['turn']}) ===")
    _cli._print_proof_radar(summary.get("lattice_proof"))
    for sid, sdata in summary["scopes"].items():
        diag = sdata.get("failure_diagnostic")
        fail_sfx = f"\n  ! Failure Diagnostic: {diag}" if diag else ""
        attrs = sdata["attributes"]
        attr_sfx = f"\n  Attributes: {json.dumps(attrs, sort_keys=True)}" if attrs else ""
        print(
            f"\n[Scope: {sid}] (parent={sdata['parent']}, children={sdata['children']}, "
            f"owner={sdata.get('owner')}, stage={sdata.get('stage')}, status={sdata.get('status')}, "
            f"ticket={sdata.get('active_ticket_id')}, build_tag={sdata.get('build_tag')}, "
            f"core_tag={sdata.get('core_tag')}){fail_sfx}{attr_sfx}"
        )
        for r in sdata["rules"]:
            print(
                f"  - [{r['id']}] ({r['type']}) prop={r.get('target_prop')!r} "
                f"op={r.get('bound_op')!r} val={r.get('bound_val')!r}: {r['text']}"
            )
    if summary["flows"]:
        lines = [
            f"  - [{f.get('id')}] {f.get('source')} -> {f.get('target')} ({f.get('payload')}): {f.get('text')}"
            for f in summary["flows"]
        ]
        print("\n[Flows]:\n" + "\n".join(lines))
    if summary["lattice_artifacts"]:
        lines = [
            f"  - [{a.get('id')}] {a.get('kind')}@{a.get('scope_id')} {a.get('name')}: {a.get('spec')}"
            for a in summary["lattice_artifacts"]
        ]
        print("\n[Lattice Artifacts]:\n" + "\n".join(lines))
    if summary["active_tasks"]:
        lines = [
            f"  - [{t.get('id')}] status={t.get('status')} scope={t.get('scope')}: {t.get('text')}"
            for t in summary["active_tasks"]
        ]
        print("\n[Active Tasks]:\n" + "\n".join(lines))
    ctx_pack = summary["assemble_context_pack"]
    print("\n[Read Engine Context Pack]:\n" + (ctx_pack.get("compact_view") or "[EMPTY_WHITEBOARD]"))
    return 0


def _cmd_prove_or_history(args: argparse.Namespace, board: str, t0_cmd: float) -> int:
    import bowthruster.cli as _cli

    if args.cmd == "prove":
        bdir, _, _, _ = _cli._board_paths(board)
        eval_fn = _cli._get_evaluate_lattice_proof()
        if eval_fn is None:
            raise RuntimeError("evaluate_lattice_proof is not yet promoted to bowthruster.whiteboard")
        proof = dict(eval_fn(_cli._load_board(board)[0]))
        b_ok, b_path, b_out = _cli._run_bend_proof_check(bdir, str(proof.get("bend_proof_source") or ""))
        proof.update({"bend_check_passed": b_ok, "bend_proof_file": str(b_path), "bend_output": b_out})
        st = "OK" if proof.get("is_proven") and b_ok else "REJECTED"
        dur_ms = (time.perf_counter() - t0_cmd) * 1000.0
        _cli.record_flight_event(board, "prove", dur_ms, st, decision=proof.get("status"))
        if args.as_json:
            print(json.dumps(proof, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(
                f"=== LATTICE PROOF '{board}' ===\n"
                f"Status:            {proof.get('status')} (is_proven={proof.get('is_proven')})\n"
                f"Counts:            components={proof.get('component_count', 0)}, "
                f"flows={proof.get('flow_count', 0)}, laws={proof.get('law_count', 0)}, "
                f"contracts={proof.get('contract_count', 0)}\n"
                f"Bend 2 Check:      {'PASS' if b_ok else 'FAIL'} ({b_path})"
            )
            _cli._print_proof_radar(proof)
        return 0
    records = _cli._read_transcript(_cli._board_paths(board)[3])
    _cli.record_flight_event(board, "history", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
    if args.as_json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return 0
    for idx, rec in enumerate(records, 1):
        if rec.get("event") in ("revoke", "replace"):
            ev, spk = rec["event"].upper(), rec.get("speaker")
            print(f"[{idx}] {ev} by {spk}: rule_id={rec.get('rule_id')} removed={rec.get('removed')}")
        elif rec.get("event") == "status":
            tid, btag, ctag = rec.get("active_ticket_id"), rec.get("build_tag"), rec.get("core_tag")
            comp_s = rec.get("component")
            st_s = rec.get("display_status") or rec.get("status")
            print(f"[{idx}] STATUS {comp_s} -> {st_s} (ticket={tid}, build_tag={btag}, core_tag={ctag})")
        else:
            print(
                f"[{idx}] Turn {rec.get('turn_index')} (Round {rec.get('round')}, {rec.get('speaker')}): "
                f"{rec.get('decision')} ({rec.get('bounce_reason')}) mutated={rec.get('whiteboard_mutated')}\n"
                f"    Input: {rec.get('raw_text')}\n    Reply: {rec.get('user_message')}"
            )
    return 0


def _dispatch_read_cmd(
    args: argparse.Namespace, board: str, explicit_board: str | None, t0_cmd: float
) -> int:
    import bowthruster.cli as _cli

    if args.cmd == "logs":
        entries = _cli.read_flight_logs(limit=args.limit, board=explicit_board, status=args.status)
        if args.as_json:
            print(json.dumps(entries, indent=2, ensure_ascii=False))
        else:
            lines = [
                f"[{e.get('timestamp')}] board={e.get('board')} cmd={e.get('tool_or_cmd')} "
                f"status={e.get('status')} decision={e.get('decision')} "
                f"({e.get('duration_ms')}ms, bytes={e.get('response_bytes', 0)}) "
                f"violations={len(e.get('violations') or [])}"
                + (f" error={e['error'].get('type')}: {e['error'].get('message')}" if e.get("error") else "")
                for e in entries
            ]
            print("\n".join(lines) or "(no flight log entries)")
        return 0
    if args.cmd == "atlas":
        boards_dict = _cli._load_atlas_boards(explicit_board, args.boards, args.load_all)
        render_fn = _cli._get_render_lattice_atlas()
        view = render_fn(boards_dict, has_local_seed_cues=False, explicit_atlas_request=True)
        atlas_keys = ("atlas_mounted", "atlas_schematic", "atlas_metrics", "atlas_mermaid", "atlas_spec")
        out_payload = {"boards": list(boards_dict.keys()), **{k: view.get(k) for k in atlas_keys}}
        _cli.record_flight_event(board, "atlas", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
        if args.as_json:
            print(json.dumps(out_payload, indent=2, sort_keys=True, ensure_ascii=False))
        elif args.spec:
            print(str(view.get("atlas_spec") or ""))
        else:
            has_mm = bool(args.mermaid and view.get("atlas_mermaid"))
            mm_sfx = f"\n\n[Atlas Mermaid]:\n{view['atlas_mermaid']}" if has_mm else ""
            metrics_str = json.dumps(view.get("atlas_metrics") or {}, sort_keys=True)
            print(f"{view.get('atlas_schematic') or ''}\n\n[Atlas Metrics]: {metrics_str}{mm_sfx}")
        return 0
    with _cli._board_lock(board):
        graph, wb_json, wb_sqlite, _ = _cli._load_board(board)
        sl_kw = {
            "query": args.query,
            "component": args.component,
            "division": args.division,
            "stage": args.stage,
            "schema": args.schema,
            "kind": args.kind,
            "include_code": args.include_code,
        }
        view = dict(slice_lattice(graph, **sl_kw))
        view, stale_sids, claimed_sids = _cli.apply_slice_stale_and_claim(
            graph, view, sl_kw, mark_stale=args.mark_stale, claim_by=args.claim_by
        )
        if stale_sids or claimed_sids:
            _cli._save_board(graph, wb_json, wb_sqlite)
    if stale_sids:
        view["marked_stale_scopes"] = stale_sids
    if claimed_sids:
        view["claimed_scopes"] = claimed_sids
    mod_sc = sorted(set(stale_sids) | set(claimed_sids)) or None
    _cli.record_flight_event(board, "slice", (time.perf_counter() - t0_cmd) * 1000.0, "OK", modified_scopes=mod_sc)
    if args.as_json:
        print(json.dumps(view, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        stale_sfx = f"\n[MARKED STALE]: {stale_sids}" if stale_sids else ""
        claim_sfx = f"\n[CLAIMED BY {args.claim_by}]: {claimed_sids}" if claimed_sids else ""
        print((view.get("compact_view") or "[EMPTY_SLICE]") + stale_sfx + claim_sfx)
    return 0
