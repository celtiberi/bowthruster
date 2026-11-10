"""Spec 23 (`23A`–`23C`): `the_executive` v1 — Prefrontal Gate, Pre-Flight Trial, & Executive Voice."""

from __future__ import annotations

from pathlib import Path
from typing import Any as _Any

from bowthruster.ai_memory import (
    extract_concept_cues,
    index_committed_rules,
    recall_concepts,
)


def _first_str(d: dict[str, _Any], *keys: str) -> str:
    return next((val for k in keys if (val := str(d.get(k) or "").strip())), "")


def _first_present(d: dict[str, _Any], *keys: str) -> _Any:
    return next((d[k] for k in keys if d.get(k) is not None), None)


def _first_list(d: dict[str, _Any], *keys: str) -> list[_Any]:
    return next((list(val) for k in keys if isinstance(val := d.get(k), (list, tuple, set))), [])


def _normalize_file_entry(f: dict[str, _Any]) -> dict[str, _Any]:
    f["path"] = _first_str(f, "path", "file")
    f["status"] = _first_str(f, "status").upper() or "M"
    f["scope"] = _first_str(f, "scope", "component")
    f["source"] = _first_present(f, "source", "content", "code")
    return f


def _clean_path(raw: str) -> str:
    s = str(raw or "").strip()
    for pfx in ("uri://file/", "uri://", "file://", "./"):
        s = s.removeprefix(pfx)
    return s.split("#", 1)[0].split(":", 1)[0].strip()


def _check_numeric_bound(actual: float, op: str, limit: float) -> bool:
    ops = {
        "<=": actual <= limit,
        "le": actual <= limit,
        "at_most": actual <= limit,
        "max": actual <= limit,
        "<": actual < limit,
        "lt": actual < limit,
        ">=": actual >= limit,
        "ge": actual >= limit,
        "at_least": actual >= limit,
        "min": actual >= limit,
        ">": actual > limit,
        "gt": actual > limit,
        "==": actual == limit,
        "=": actual == limit,
        "eq": actual == limit,
        "!=": actual != limit,
        "ne": actual != limit,
    }
    return ops.get(op, True)


def _extract_prohibited_tokens(r: dict[str, _Any]) -> set[str]:
    out: set[str] = set()
    for piece in _first_str(r, "target_prop").replace(",", " ").split():
        if (tok := piece.strip("`'\".").lower()) and tok.isidentifier():
            out.add(tok)
    for item in (*_first_list(r, "prohibited_imports"), *_first_list(r, "banned_imports")):
        if (tok := str(item).strip().strip("`'\".").lower()) and tok.isidentifier():
            out.add(tok)
    return out


def _extract_scope_files(sc: dict[str, _Any]) -> list[str]:
    attrs = (sc.get("attributes") or {})
    return [
        str(x).strip()
        for k, src in (("files", sc), ("files", attrs), ("_table_files", attrs))
        for x in _first_list(src, k)
        if str(x).strip()
    ]


def _normalize_symbol_map(raw_syms: _Any) -> tuple[set[str], dict[str, set[str]]]:
    names, params_by_name = set(), {}
    if isinstance(raw_syms, dict):
        items = [(str(k).strip(), v) for k, v in raw_syms.items()]
    elif isinstance(raw_syms, (list, tuple, set)):
        items = [
            (item.strip(), None) if isinstance(item, str) else (_first_str(item, "name", "id"), item)
            for item in raw_syms
            if isinstance(item, (str, dict))
        ]
    else:
        items = []
    for nm, val in items:
        if nm:
            names.add(nm)
            raw_p = (
                val
                if isinstance(val, (list, tuple, set))
                else (_first_list(val, "args", "inputs", "params", "fields") if isinstance(val, dict) else None)
            )
            if isinstance(raw_p, (list, tuple, set)):
                params_by_name[nm] = {str(p).strip().lstrip("*") for p in raw_p if str(p).strip()}
    return names, params_by_name


from bowthruster.the_executive.gate import (
    BOUNCE_FLAWED_IDEA,
    BOUNCE_NEEDS_CLARIFICATION,
    BOUNCE_NONE,
    BOUNCE_RULE_CONFLICT,
    BounceReason,
    DECISION_COMMIT_TO_WHITEBOARD,
    DECISION_SEND_BACK_TO_USER,
    ExecutiveDecision,
    ExecutiveGateError,
    GateDecisionResult,
    REQUIRED_PREFLIGHT_KEYS,
    VALID_BOUNCE_REASONS,
    VALID_EXECUTIVE_DECISIONS,
    evaluate_git_commit_gate,
    execute_gate_decision,
    is_call_graph_skeleton_valid,
    is_verified_scope_protected,
    resolve_bounce_reason,
    route_executive_decision,
    should_allow_git_commit,
    should_commit_whiteboard,
    verified_scope_edit_guard_law,
    verify_ast_call_graph_skeleton,
    verify_preflight_report,
)
from bowthruster.the_executive.preflight import (
    ExecutivePreflightError,
    FLAW_KIND_CRITIQUE_FLAGGED,
    FLAW_KIND_FACT_CONTRADICTION,
    FLAW_KIND_GOAL_SABOTAGE,
    MCU_STATUS_CLEAN,
    MCU_STATUS_FLAWED_IDEA,
    MCU_STATUS_HAS_OPEN_QUESTION,
    MCU_STATUS_NEEDS_CLARIFICATION,
    MCU_STATUS_RULE_CONFLICT,
    McuPreflightStatus,
    VALID_MCU_PREFLIGHT_STATUSES,
    classify_mcu_status,
    clone_whiteboard_for_trial,
    inspect_turn_preflight,
    is_preflight_clean,
    normalize_intake_packet,
)
import copy
from typing import Any
from bowthruster.whiteboard.schema import ArtifactEntry


def _norm_git_path(val: Any) -> str:
    raw = str(val or "").strip().split("#", 1)[0].removeprefix("file://")
    while raw.startswith("./"):
        raw = raw[2:]
    return raw.strip()


def _paths_match(p1: Any, p2: Any) -> bool:
    n1, n2 = _norm_git_path(p1), _norm_git_path(p2)
    same_stem = "/" not in n1 or "/" not in n2 or n1.startswith("/") != n2.startswith("/")
    return bool(
        n1
        and n2
        and (
            n1 == n2
            or n1.lstrip("/") == n2.lstrip("/")
            or (same_stem and (n1.endswith("/" + n2.lstrip("/")) or n2.endswith("/" + n1.lstrip("/"))))
        )
    )


def _scope_file_refs(graph: dict[str, Any], sid: str, sc: dict[str, Any]) -> list[str]:
    attrs = (sc.get("attributes") or {})
    refs: list[str] = []
    file_sources = (
        sc.get("files"),
        attrs.get("files"),
        attrs.get("_table_files"),
        sc.get("source_links"),
        sc.get("external_uris"),
    )
    for raw_list in file_sources:
        if isinstance(raw_list, list):
            refs.extend(str(x) for x in raw_list if x)
        elif isinstance(raw_list, str):
            refs.extend(p.strip() for p in raw_list.split(",") if p.strip())
    ext_top = (graph.get("external_links") or {}).get(sid) if isinstance(graph.get("external_links"), dict) else None
    rule_lks = [r.get("external_links") for r in (sc.get("rules") or [])]
    for lk_list in (sc.get("external_links"), ext_top, *rule_lks):
        if isinstance(lk_list, list):
            refs.extend(
                str(lk.get("target_uri", lk.get("uri", "")) or "").strip() for lk in lk_list if isinstance(lk, dict)
            )
    for art in graph.get("lattice_artifacts") or ():
        if str(art.get("scope_id", art.get("scope", "")) or "").strip() == sid:
            dim_f = (art.get("dimensions") or {}).get("file") if isinstance(art.get("dimensions"), dict) else None
            cands = (art.get("file"), dim_f, art.get("target_uri"), art.get("uri"))
            refs.extend(c.strip() for c in cands if isinstance(c, str) and c.strip())
    return refs


def _find_owning_scopes(graph: dict[str, Any], path: Any, old_path: Any = None,
    explicit_scope: Any = None) -> list[str]:
    scopes = (graph.get("scopes") or {})
    owners = [str(explicit_scope).strip()] if str(explicit_scope or "").strip() in scopes else []
    return owners + [
        sid
        for sid, sc in scopes.items()
        if sid not in owners
        and any(
            _paths_match(r, path) or (old_path and _paths_match(r, old_path))
            for r in _scope_file_refs(graph, sid, sc)
        )
    ]


def _scope_direct_deps(sc: dict[str, Any]) -> list[str]:
    attrs = (sc.get("attributes") or {})
    deps: list[str] = []
    for raw in (sc.get("depends_on"), attrs.get("depends_on"), attrs.get("_table_depends_on")):
        items = (
            raw
            if isinstance(raw, list)
            else ([p.strip() for p in str(raw).split(",")] if isinstance(raw, str) and raw.strip() else [])
        )
        for d in items:
            if (ds := str(d).strip()) and ds not in deps:
                deps.append(ds)
    return deps


def _upsert_contract_artifact(
    graph: dict[str, Any], sid: str, kind: str, item: Any, file_path: str, eff_turn: int
) -> str:
    name_keys = ("name", "interface", "schema", "id", "function", "class")
    name = (
        str(next((item[k] for k in name_keys if item.get(k)), "")).strip()
        if isinstance(item, dict)
        else str(item).strip()
    )
    if not name:
        return ""
    k_clean = str(item.get("kind", kind) if isinstance(item, dict) else kind).strip().upper()
    k_clean = k_clean if k_clean in ("INTERFACE", "SCHEMA", "LAW") else "INTERFACE"
    artifacts = graph.setdefault("lattice_artifacts", [])
    entry = next(
        (
            a
            for a in artifacts
            if a.get("kind") == k_clean
            and a.get("scope_id", a.get("scope")) == sid
            and str(a.get("name", "")).lower() == name.lower()
        ),
        None,
    )
    if entry is None:
        entry = ArtifactEntry(
            {
                "id": f"{k_clean.lower()}_{sid}_{name.lower()}",
                "kind": k_clean,
                "name": name,
                "scope_id": sid,
                "scope": sid,
                "created_turn": eff_turn,
            }
        )
        artifacts.append(entry)
    if isinstance(item, dict):
        for k_src, k_dst in (("inputs", "inputs"), ("args", "inputs"), ("outputs", "outputs"), ("returns", "outputs")):
            if isinstance(item.get(k_src), list):
                entry[k_dst] = [str(x).strip() for x in item[k_src] if str(x).strip()]
        if spec_str := str(item.get("spec", item.get("signature", item.get("text", ""))) or "").strip():
            entry["spec"] = entry["text"] = spec_str
    if file_path:
        entry["file"] = entry.setdefault("dimensions", {})["file"] = file_path
    entry["turn"] = entry["updated_turn"] = eff_turn
    return name


def _normalize_report_entry(fe: dict[str, Any], top_hashes: dict[str, str]) -> dict[str, Any]:
    path = str(fe.get("path", fe.get("file", fe.get("new_path", fe.get("uri", "")))) or "").strip()
    fe["path"] = path
    fe["old_path"] = str(fe.get("old_path", fe.get("from_path", fe.get("old_uri", ""))) or "").strip()
    fe["status"] = str(fe.get("status", fe.get("change_type", fe.get("action", ""))) or "").strip().upper()
    raw_h = fe.get("file_hash", fe.get("sha256", fe.get("hash", fe.get("blob_sha"))))
    fe["file_hash"] = str(raw_h if raw_h else top_hashes.get(path, "")).strip()
    fe["scope"] = str(fe.get("scope", fe.get("scope_id", fe.get("component", ""))) or "").strip()
    return fe


def _parse_git_report_entries(
    report: dict[str, Any] | list[Any],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int], dict[str, str]]:
    if isinstance(report, list):
        raw_list = [copy.deepcopy(x) if isinstance(x, dict) else {"path": str(x)} for x in report]
        return [_normalize_report_entry(x, {}) for x in raw_list], {}, {}, {}
    rename_map: dict[str, str] = {}
    raw_ren = report.get("renamed_files", report.get("renames"))
    ren_iter = (
        [{"old_path": k, "new_path": v} for k, v in raw_ren.items()]
        if isinstance(raw_ren, dict)
        else (raw_ren if isinstance(raw_ren, list) else ())
    )
    for rn in ren_iter:
        if isinstance(rn, dict):
            op = str(rn.get("old_path", rn.get("from_path", rn.get("old", rn.get("old_uri", "")))) or "").strip()
            np_raw = rn.get("new_path", rn.get("to_path", rn.get("path", rn.get("new", rn.get("new_uri", "")))))
            np = str(np_raw or "").strip()
            if op and np:
                rename_map[op] = np
        elif isinstance(rn, (tuple, list)) and len(rn) >= 2:
            rename_map[str(rn[0]).strip()] = str(rn[1]).strip()
    raw_shifts = report.get("line_shifts", report.get("line_deltas", {}))
    shift_items = raw_shifts.items() if isinstance(raw_shifts, dict) else ()
    line_shifts = {str(k): int(v) for k, v in shift_items if isinstance(v, int)}
    raw_hashes = report.get("file_hashes", report.get("sha256", report.get("hashes", {})))
    hash_items = raw_hashes.items() if isinstance(raw_hashes, dict) else ()
    top_hashes = {str(k): str(v) for k, v in hash_items if v}
    entries: list[dict[str, Any]] = []
    f_keys = ("files", "changed_files", "file_changes", "changes", "diff_entries", "entries")
    raw_f = next((report[k] for k in f_keys if report.get(k) is not None), None)
    if isinstance(raw_f, dict):
        entries.extend(
            {"path": str(fp), **(info if isinstance(info, dict) else {"file_hash": str(info)})}
            for fp, info in raw_f.items()
        )
    elif isinstance(raw_f, list):
        entries.extend(copy.deepcopy(x) if isinstance(x, dict) else {"path": str(x)} for x in raw_f)
    st_pairs = (
        ("modified_files", "M"),
        ("added_files", "A"),
        ("untracked_files", "??"),
        ("deleted_files", "D"),
        ("removed_files", "D"),
    )
    for key, st_code in st_pairs:
        if isinstance(k_list := report.get(key), list):
            entries.extend(
                {"status": st_code, **(copy.deepcopy(x) if isinstance(x, dict) else {"path": str(x)})}
                for x in k_list
            )
    for op, np in list(rename_map.items()):
        if not any(_paths_match(fe.get("path", fe.get("new_path", "")), np) for fe in entries):
            entries.append({"old_path": op, "path": np, "status": "R"})
    for fe in entries:
        _normalize_report_entry(fe, top_hashes)
        if fe["old_path"] and fe["path"]:
            rename_map.setdefault(fe["old_path"], fe["path"])
        if isinstance(shift_val := fe.get("line_shift", fe.get("line_delta")), int) and fe["path"]:
            line_shifts[fe["path"]] = int(shift_val)
    return entries, rename_map, line_shifts, top_hashes


from bowthruster.the_executive.orchestrator import (
    ExecutiveTurnResult,
    PostCommitSyncResult,
    WorkingTreeDriftReport,
    detect_working_tree_drift,
    run_executive_turn,
    should_mark_dependent_stale_on_commit,
    sync_post_commit_to_lattice,
)
from bowthruster.the_executive.voice import (
    EXECUTIVE_MAX_TOKENS,
    EXECUTIVE_PROMPT_KEY,
    ExecutiveVoiceError,
    format_executive_prompt,
    is_reply_valid,
    load_executive_prompt,
    render_deterministic_executive_reply,
    synthesize_executive_voice,
    verify_executive_reply,
)

__all__ = [
    "BOUNCE_FLAWED_IDEA",
    "BOUNCE_NEEDS_CLARIFICATION",
    "BOUNCE_NONE",
    "BOUNCE_RULE_CONFLICT",
    "BounceReason",
    "DECISION_COMMIT_TO_WHITEBOARD",
    "DECISION_SEND_BACK_TO_USER",
    "EXECUTIVE_MAX_TOKENS",
    "EXECUTIVE_PROMPT_KEY",
    "ExecutiveDecision",
    "ExecutiveGateError",
    "ExecutivePreflightError",
    "ExecutiveTurnResult",
    "ExecutiveVoiceError",
    "FLAW_KIND_CRITIQUE_FLAGGED",
    "FLAW_KIND_FACT_CONTRADICTION",
    "FLAW_KIND_GOAL_SABOTAGE",
    "GateDecisionResult",
    "MCU_STATUS_CLEAN",
    "MCU_STATUS_FLAWED_IDEA",
    "MCU_STATUS_HAS_OPEN_QUESTION",
    "MCU_STATUS_NEEDS_CLARIFICATION",
    "MCU_STATUS_RULE_CONFLICT",
    "McuPreflightStatus",
    "PostCommitSyncResult",
    "REQUIRED_PREFLIGHT_KEYS",
    "VALID_BOUNCE_REASONS",
    "VALID_EXECUTIVE_DECISIONS",
    "VALID_MCU_PREFLIGHT_STATUSES",
    "WorkingTreeDriftReport",
    "classify_mcu_status",
    "clone_whiteboard_for_trial",
    "detect_working_tree_drift",
    "evaluate_git_commit_gate",
    "execute_gate_decision",
    "extract_concept_cues",
    "format_executive_prompt",
    "index_committed_rules",
    "inspect_turn_preflight",
    "is_call_graph_skeleton_valid",
    "is_preflight_clean",
    "is_reply_valid",
    "is_verified_scope_protected",
    "load_executive_prompt",
    "normalize_intake_packet",
    "recall_concepts",
    "render_deterministic_executive_reply",
    "resolve_bounce_reason",
    "route_executive_decision",
    "run_executive_turn",
    "should_allow_git_commit",
    "should_commit_whiteboard",
    "should_mark_dependent_stale_on_commit",
    "sync_post_commit_to_lattice",
    "synthesize_executive_voice",
    "verified_scope_edit_guard_law",
    "verify_ast_call_graph_skeleton",
    "verify_executive_reply",
    "verify_preflight_report",
]
