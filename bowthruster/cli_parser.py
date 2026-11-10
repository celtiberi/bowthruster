"""Argument parser, input reader, item removal, and board summary builder (`bowthruster.cli_parser`)."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
from typing import Any

from bowthruster.whiteboard import annotate_active_tasks, expand_node, scan_graph

STANDING_TYPES = ("BOUND", "PROHIBITION", "OBLIGATION", "POLICY")
BUILD_STATUSES = (
    "DRAFT", "BLOCKED_BY_DEP", "READY", "IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE"
)


def _read_input_text(message: str | None, file_path: str | None) -> str:
    if message is None and file_path is None:
        raise ValueError("Provide either -m/--message or -F/--file")
    if message is not None:
        raw = message
    elif file_path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(file_path or "").read_text(encoding="utf-8")
    return raw.strip()


def _remove_item_from_board(graph: dict[str, Any], target_id: str) -> list[str]:
    removed: list[str] = []
    scopes = graph.get("scopes") or {}
    if target_id.startswith("scope:"):
        del_sid = target_id.removeprefix("scope:").strip()
        if del_sid in scopes:
            scopes.pop(del_sid, None)
            removed.append(f"scope:{del_sid}")
            for snode in scopes.values():
                if isinstance(snode.get("depends_on"), list):
                    snode["depends_on"] = [d for d in snode["depends_on"] if d != del_sid]
    if graph.get("scopes"):
        kept_arts = [a for a in graph["lattice_artifacts"] if str(a.get("id") or "") != target_id]
        if len(kept_arts) < len(graph["lattice_artifacts"]):
            removed.append(f"artifact:{target_id}")
            graph["lattice_artifacts"] = kept_arts
    has_rule_id = any(
        any(r.get("id") == target_id for r in (s.get("rules") or [])) for s in scopes.values()
    )
    is_attr_id = target_id.startswith("attr:") or ("." in target_id and not has_rule_id)
    if is_attr_id and "." in target_id.removeprefix("attr:"):
        sid, prop = target_id.removeprefix("attr:").split(".", 1)
        snode = scopes.get(sid) or {}
        if prop in (snode.get("attributes") or {}):
            removed.append(f"attr:{sid}.{prop}={snode['attributes'].pop(prop)!r}")
    for sid, snode in scopes.items():
        kept = []
        for r in (snode.get("rules") or []):
            if r.get("id") == target_id:
                removed.append(f"{r.get('type', 'RULE')}:{target_id}@{sid} ({r.get('text', '')})")
                (snode.get("_meta") or {}).pop(target_id, None)
            else:
                kept.append(r)
        snode["rules"] = kept
    if removed:
        for snode in scopes.values():
            if target_id in (snode.get("in_edges") or []):
                snode["in_edges"].remove(target_id)
            for r in (snode.get("rules") or []):
                if target_id in (r.get("overrides") or []):
                    r["overrides"].remove(target_id)
                r["exceptions"] = [
                    ex for ex in (r.get("exceptions") or []) if ex.get("rule_id") != target_id
                ]
        graph["open_blockers"] = [
            b
            for b in (graph.get("open_blockers") or [])
            if b.get("id") != target_id and target_id not in (b.get("rule_ids") or [])
        ]
    return removed


def _assemble_board_summary(
    graph: dict[str, Any], scope_filter: str | None = None, eval_fn: Any = None
) -> dict[str, Any]:
    annotate_active_tasks(graph)
    scopes_out, standing, facts, goals, actions = {}, [], [], [], []
    scope_keys = (
        "parent", "role", "owner", "stage", "status", "claimed_by",
        "build_tag", "core_tag", "failure_diagnostic", "active_ticket_id",
    )
    for sid, snode in sorted((graph.get("scopes") or {}).items()):
        if scope_filter and sid != scope_filter and not sid.startswith(f"{scope_filter}::"):
            continue
        attrs = {
            k: v
            for k, v in (snode.get("attributes") or {}).items()
            if k not in ("updated_turn", "heading_path")
        }
        rules = [dict(r) for r in (snode.get("rules") or [])]
        scopes_out[sid] = {
            **{k: snode.get(k) for k in scope_keys},
            "children": list(snode.get("children") or []),
            "attributes": attrs,
            "rules": rules,
        }
        for r in rules:
            rtype = str(r.get("type", "OBLIGATION"))
            if rtype in STANDING_TYPES:
                standing.append(r)
            elif rtype == "FACT":
                facts.append(r)
            elif rtype == "GOAL":
                goals.append(r)
            else:
                actions.append(r)
    raw_arts = graph.get("lattice_artifacts") or []
    g_copy = copy.deepcopy(graph)
    ctx_pack = expand_node(g_copy, scope_filter) if scope_filter else scan_graph(g_copy)
    return {
        "turn": graph.get("turn", 0),
        "scopes": scopes_out,
        "standing_rules": standing,
        "facts": facts,
        "goals": goals,
        "actions": actions,
        "flows": list(graph.get("flows") or []),
        "lattice_artifacts": list(raw_arts.values()) if isinstance(raw_arts, dict) else list(raw_arts),
        "active_tasks": list(graph.get("active_tasks") or []),
        "open_blockers": list(graph.get("open_blockers") or []),
        "lattice_proof": eval_fn(graph) if eval_fn else graph.get("lattice_proof"),
        "assemble_context_pack": ctx_pack,
    }


def _build_cli_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    parser = argparse.ArgumentParser(description="Multi-Round Executive <-> Whiteboard Dogfooding CLI")
    for flg, dst in (
        ("--board", "board"),
        ("--profile", "profile"),
        ("--config", "config"),
        ("--project-root", "project_root"),
    ):
        common.add_argument(flg, dest=dst, default=argparse.SUPPRESS)
        parser.add_argument(flg, dest=dst, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_turn = sub.add_parser("turn", parents=[common])
    p_rep = sub.add_parser("replace", parents=[common])
    p_rev = sub.add_parser("revoke", parents=[common])
    for p in (p_rep, p_rev):
        p.add_argument("rule_id")
    for p in (p_turn, p_rep, p_rev):
        p.add_argument("--speaker", default="agent")
        p.add_argument("-m", "--message", default="Explicitly revoked" if p is p_rev else None)
        if p is not p_rev:
            p.add_argument("--round", type=int, default=None, dest="round_num")
            p.add_argument("-F", "--file", default=None)
    p_show = sub.add_parser("show", parents=[common])
    p_slice = sub.add_parser("slice", parents=[common])
    p_show.add_argument("--scope", default=None)
    for flag in ("--component", "--division", "--stage", "--schema", "--kind"):
        p_slice.add_argument(flag, default=None)
    p_slice.add_argument("-q", "--query", default="")
    p_slice.add_argument("--code", "--include-code", action="store_true", dest="include_code")
    p_slice.add_argument("--mark-stale", action="store_true", dest="mark_stale")
    p_slice.add_argument("--claim", default=None, dest="claim_by")
    p_pkts = sub.add_parser("packets", parents=[common])
    p_stat = sub.add_parser("status", parents=[common])
    p_atlas = sub.add_parser("atlas", parents=[common])
    p_gsync = sub.add_parser("git-sync", parents=[common])
    p_logs = sub.add_parser("logs", parents=[common])
    p_pkts.add_argument("--wave", type=int, choices=(0, 1, 2), default=None)
    p_pkts.add_argument("--component", default=None)
    p_pkts.add_argument("--status", choices=BUILD_STATUSES, type=str.upper, default=None)
    p_pkts.add_argument("--unclaimed", action="store_true", dest="unclaimed_only")
    p_pkts.add_argument("--claim", default=None, dest="claim_by")
    p_pkts.add_argument("--force", action="store_true")
    p_stat.add_argument("component")
    p_stat.add_argument("new_status", nargs="?", choices=BUILD_STATUSES, type=str.upper, default=None)
    stat_opts = (
        ("--build-tag", "build_tag"),
        ("--core-tag", "core_tag"),
        ("--reason", "reason"),
        ("--for", "for_div"),
        ("--claim", "claimed_by"),
    )
    for flg, dst in stat_opts:
        p_stat.add_argument(flg, default=None, dest=dst)
    for flg, dst in (
        ("--dispatch", "dispatch"),
        ("--no-ticket", "no_ticket"),
        ("--release", "release_claim"),
        ("--force-claim", "force_claim"),
    ):
        p_stat.add_argument(flg, action="store_true", dest=dst)
    p_atlas.add_argument("--boards", default=None)
    for flg, dst in (("--all", "load_all"), ("--mermaid", "mermaid"), ("--spec", "spec")):
        p_atlas.add_argument(flg, action="store_true", dest=dst)
    gsync_flags = (
        ("--pre-commit", "pre_commit"),
        ("--post-commit", "post_commit"),
        ("--baseline", "baseline"),
        ("--drift", "drift"),
        ("--mark-verified", "mark_verified"),
        ("--install-hook", "install_hook"),
    )
    for flg, dst in gsync_flags:
        p_gsync.add_argument(flg, action="store_true", dest=dst)
    p_gsync.add_argument("--manifest", default=None)
    p_logs.add_argument("--limit", type=int, default=20)
    p_logs.add_argument("--status", default=None)
    p_prove = sub.add_parser("prove", parents=[common])
    p_hist = sub.add_parser("history", parents=[common])
    p_boot = sub.add_parser("bootstrap", parents=[common])
    p_mig = sub.add_parser("migrate", parents=[common])
    p_boot.add_argument("--component", default=None)
    p_mig.add_argument("--all", action="store_true", dest="migrate_all")
    p_mig.add_argument("--dry-run", action="store_true", dest="dry_run")
    json_parsers = (
        p_show, p_slice, p_prove, p_pkts, p_stat, p_atlas, p_gsync, p_logs, p_hist, p_boot, p_mig
    )
    for p in json_parsers:
        p.add_argument("--json", action="store_true", dest="as_json")
    sub.add_parser("reset", parents=[common])
    return parser
