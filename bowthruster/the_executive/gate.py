"""Spec 23B, Stage 26 & Stage 27: `the_executive` Routing Gate & Pre-Commit Lattice Gate (`gate.py`)."""

from __future__ import annotations

import copy
from enum import Enum
import importlib
from pathlib import Path
from typing import Any

from bowthruster.graph_core import DEFAULT_K_CAP, DEFAULT_MAX_RING, SQLiteGraphStore
from bowthruster.the_executive.call_graph import (
    ExecutiveGateError,
    _BEND_WRAPPER_FNS,
    _cached_ast_nodes,
    _check_numeric_bound,
    _collect_dangling_imports,
    _collect_fn_calls,
    _extract_law_fn_name,
    _extract_prohibited_tokens,
    _extract_scope_files,
    _normalize_symbol_map,
    _parse_ast_imports,
    _parse_file_call_graph,
    _paths_match,
    _populate_file_ast_metadata,
    _resolve_file_scopes,
    _unpack_lattice_and_files,
    is_call_graph_skeleton_valid,
    verify_ast_call_graph_skeleton,
)

DECISION_COMMIT_TO_WHITEBOARD = "COMMIT_TO_WHITEBOARD"
DECISION_SEND_BACK_TO_USER = "SEND_BACK_TO_USER"
DECISION_ALLOW_COMMIT = "ALLOW_COMMIT"
DECISION_REJECT_COMMIT = "REJECT_COMMIT"
VALID_EXECUTIVE_DECISIONS = frozenset({DECISION_COMMIT_TO_WHITEBOARD, DECISION_SEND_BACK_TO_USER})

BOUNCE_NONE = "NONE"
BOUNCE_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
BOUNCE_FLAWED_IDEA = "FLAWED_IDEA"
BOUNCE_RULE_CONFLICT = "RULE_CONFLICT"
BOUNCE_BEND_PROOF_FAILURE = "BEND_PROOF_FAILURE"
BOUNCE_DANGLING_DELETED_URI = "DANGLING_DELETED_URI"
DEFAULT_MAX_FILE_LINES = 500
VALID_BOUNCE_REASONS = frozenset({
    BOUNCE_NONE,
    BOUNCE_NEEDS_CLARIFICATION,
    BOUNCE_FLAWED_IDEA,
    BOUNCE_RULE_CONFLICT,
    BOUNCE_BEND_PROOF_FAILURE,
    BOUNCE_DANGLING_DELETED_URI,
})
REQUIRED_PREFLIGHT_KEYS: tuple[str, ...] = (
    "turn_index",
    "intake_packet",
    "is_clean",
    "has_ambiguity",
    "has_open_questions",
    "has_rule_conflict",
    "has_flawed_idea",
    "mcu_findings",
    "conflicts",
    "flawed_ideas",
    "clarification_questions",
    "open_questions",
)


class ExecutiveDecision(str, Enum):
    COMMIT_TO_WHITEBOARD = DECISION_COMMIT_TO_WHITEBOARD
    SEND_BACK_TO_USER = DECISION_SEND_BACK_TO_USER


class BounceReason(str, Enum):
    NONE = BOUNCE_NONE
    NEEDS_CLARIFICATION = BOUNCE_NEEDS_CLARIFICATION
    FLAWED_IDEA = BOUNCE_FLAWED_IDEA
    RULE_CONFLICT = BOUNCE_RULE_CONFLICT


class GateDecisionResult(dict[str, Any]):
    """Dictionary result wrapper supporting attribute-style read access."""

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name) or name not in self:
            raise AttributeError(name)
        return self[name]


def should_commit_whiteboard(
    has_ambiguity: bool,
    has_open_questions: bool,
    has_rule_conflict: bool,
    has_flawed_idea: bool = False,
) -> bool:
    return not (bool(has_ambiguity) or bool(has_open_questions) or bool(has_rule_conflict) or bool(has_flawed_idea))


def route_executive_decision(
    has_ambiguity: bool,
    has_open_questions: bool,
    has_rule_conflict: bool,
    has_flawed_idea: bool = False,
) -> str:
    if should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea):
        return ExecutiveDecision.COMMIT_TO_WHITEBOARD.value
    return ExecutiveDecision.SEND_BACK_TO_USER.value


def resolve_bounce_reason(
    has_ambiguity: bool,
    has_open_questions: bool,
    has_rule_conflict: bool,
    has_flawed_idea: bool = False,
) -> str:
    if bool(has_rule_conflict):
        return BounceReason.RULE_CONFLICT.value
    if bool(has_flawed_idea):
        return BounceReason.FLAWED_IDEA.value
    if bool(has_ambiguity) or bool(has_open_questions):
        return BounceReason.NEEDS_CLARIFICATION.value
    return BounceReason.NONE.value


def verify_preflight_report(preflight_report: dict[str, Any]) -> list[str]:
    if not isinstance(preflight_report, dict):
        return [f"preflight_report must be a dict, got {type(preflight_report).__name__}"]
    errors = [f"preflight_report missing required key {k!r}" for k in REQUIRED_PREFLIGHT_KEYS if k not in preflight_report]
    if errors:
        return errors
    turn_idx = preflight_report.get("turn_index")
    intake_pkt = preflight_report.get("intake_packet")
    if isinstance(turn_idx, bool) or not isinstance(turn_idx, int) or turn_idx < 1:
        errors.append(f"preflight_report['turn_index'] must be a positive int, got {turn_idx!r}")
    if not isinstance(intake_pkt, dict):
        errors.append(f"preflight_report['intake_packet'] must be a dict, got {type(intake_pkt).__name__}")
    elif not isinstance(intake_pkt.get("parsed_mcus"), list):
        errors.append("preflight_report['intake_packet']['parsed_mcus'] must be a list")
    for lk in ("mcu_findings", "conflicts", "flawed_ideas", "clarification_questions", "open_questions"):
        if not isinstance(preflight_report.get(lk), list):
            errors.append(f"preflight_report[{lk!r}] must be a list, got {type(preflight_report.get(lk)).__name__}")
    for fk in ("has_ambiguity", "has_open_questions", "has_rule_conflict", "has_flawed_idea", "is_clean"):
        if not isinstance(preflight_report.get(fk), bool):
            errors.append(f"preflight_report[{fk!r}] must be a bool, got {preflight_report.get(fk)!r}")
    if errors:
        return errors
    has_amb, has_oq = preflight_report["has_ambiguity"], preflight_report["has_open_questions"]
    has_rc, has_fi = preflight_report["has_rule_conflict"], preflight_report["has_flawed_idea"]
    is_clean = preflight_report["is_clean"]
    if is_clean != (expected_clean := should_commit_whiteboard(has_amb, has_oq, has_rc, has_fi)):
        errors.append(f"preflight_report['is_clean']={is_clean!r} != should_commit_whiteboard(...)={expected_clean!r}")
    if has_rc != (len(preflight_report["conflicts"]) > 0):
        errors.append(f"preflight_report['has_rule_conflict']={has_rc!r} != (len(conflicts) > 0)")
    if has_fi != (len(preflight_report["flawed_ideas"]) > 0):
        errors.append(f"preflight_report['has_flawed_idea']={has_fi!r} != (len(flawed_ideas) > 0)")
    return errors


def execute_gate_decision(
    preflight_report: dict[str, Any],
    whiteboard_graph: dict[str, Any] | None = None,
    *,
    k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING,
    store: SQLiteGraphStore | None = None,
    db_path: str | Path | None = None,
) -> GateDecisionResult:
    """Executes the transactional Whiteboard gate decision, committing via `ingest_intake_packet` iff `should_commit_whiteboard(...)` is `True`."""
    if rep_errors := verify_preflight_report(preflight_report):
        raise ExecutiveGateError("gate_invalid_preflight_report", rep_errors)
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        raise ExecutiveGateError("gate_invalid_whiteboard", [f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}"])
    has_amb, has_oq = preflight_report["has_ambiguity"], preflight_report["has_open_questions"]
    has_rc, has_fi = preflight_report["has_rule_conflict"], preflight_report["has_flawed_idea"]
    should_commit = should_commit_whiteboard(has_amb, has_oq, has_rc, has_fi)
    decision = route_executive_decision(has_amb, has_oq, has_rc, has_fi)
    bounce_reason = resolve_bounce_reason(has_amb, has_oq, has_rc, has_fi)
    parsed_mcus = copy.deepcopy(preflight_report["intake_packet"].get("parsed_mcus", []))
    from bowthruster.whiteboard import create_empty_context_graph, ingest_intake_packet
    from bowthruster.whiteboard.frustration import evaluate_lattice_proof

    if should_commit:
        ingestion_res = ingest_intake_packet(whiteboard_graph, preflight_report["intake_packet"], k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)
        active_graph, whiteboard_mutated, committed_items, blocked_items = ingestion_res["graph"], True, parsed_mcus, []
    else:
        active_graph = create_empty_context_graph(k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path) if whiteboard_graph is None else whiteboard_graph
        ingestion_res, whiteboard_mutated, committed_items, blocked_items = None, False, [], parsed_mcus
    lattice_proof = evaluate_lattice_proof(active_graph)
    if should_commit and isinstance(active_graph, dict):
        active_graph["lattice_proof"] = lattice_proof
    return GateDecisionResult({
        "turn_index": int(preflight_report["turn_index"]),
        "decision": decision,
        "bounce_reason": bounce_reason,
        "should_commit": should_commit,
        "whiteboard_mutated": whiteboard_mutated,
        "whiteboard_graph": active_graph,
        "ingestion_result": ingestion_res,
        "committed_items": committed_items,
        "blocked_items": blocked_items,
        "conflicts": copy.deepcopy(preflight_report["conflicts"]),
        "flawed_ideas": copy.deepcopy(preflight_report["flawed_ideas"]),
        "clarification_questions": list(preflight_report["clarification_questions"]),
        "open_questions": list(preflight_report["open_questions"]),
        "preflight_report": preflight_report,
        "lattice_proof": lattice_proof,
    })


def should_allow_git_commit(
    bounds_ok: bool,
    contracts_ok: bool,
    bend_proofs_ok: bool,
    no_dangling_deletes: bool,
) -> bool:
    """Pure pre-commit gate classifier governed by Bend 2 Law 7 (`git_precommit_gate_law`, Stage 26)."""
    return bool(bounds_ok and contracts_ok and bend_proofs_ok and no_dangling_deletes)


def is_verified_scope_protected(file_hash_changed: bool = False, scope_still_verified: bool = False) -> bool:
    """Reject commits that modify a baseline-hashed file belonging to a VERIFIED component unless the component was marked STALE."""
    return not (bool(file_hash_changed) and bool(scope_still_verified))


verified_scope_edit_guard_law = is_verified_scope_protected


def evaluate_git_commit_gate(
    whiteboard_graph: dict[str, Any] | None,
    staged_diff_report: dict[str, Any],
    *,
    uri_sync_report: dict[str, Any] | None = None,
) -> GateDecisionResult:
    """Evaluates a staged git commit against the active Whiteboard Lattice (`whiteboard_graph`) without mutating it."""
    wb, scopes, artifacts, staged_files = _unpack_lattice_and_files(whiteboard_graph, staged_diff_report)
    eff_uri_sync = uri_sync_report if isinstance(uri_sync_report, dict) else (staged_diff_report.get("uri_sync_report") if isinstance(staged_diff_report.get("uri_sync_report"), dict) else None)
    violations: list[dict[str, Any]] = []
    bounds_ok, contracts_ok, bend_proofs_ok, no_dangling_deletes = True, True, True, True
    raw_mlb = wb.get("max_file_lines") if wb.get("max_file_lines") is not None else staged_diff_report.get("max_file_lines")
    max_lines_bound: int | None = int(raw_mlb) if isinstance(raw_mlb, (int, float)) and not isinstance(raw_mlb, bool) and raw_mlb > 0 else None
    scope_bounds: dict[str, list[tuple[str, str, float, str]]] = {}
    prohibited_by_scope: dict[str, set[str]] = {}
    for sid, sc in scopes.items():
        base_sid = sid.split("::", 1)[0]
        for r in (sc.get("rules") if isinstance(sc, dict) and isinstance(sc.get("rules"), list) else []):
            if not isinstance(r, dict):
                continue
            rtype, rtext, prop = str(r.get("type") or "").upper(), str(r.get("text") or ""), str(r.get("target_prop") or "").strip()
            if (rtype == "BOUND" or r.get("bound_op") is not None) and r.get("bound_val") is not None and not isinstance(r.get("bound_val"), bool):
                try:
                    bval, bop = float(r["bound_val"]), str(r.get("bound_op") or "<=").strip()
                    if prop.lower() in ("max_file_lines", "file_lines", "line_count", "lines", "max_lines") or "max_file_lines" in rtext.lower():
                        max_lines_bound = int(bval)
                    elif prop:
                        scope_bounds.setdefault(base_sid, []).append((prop, bop, bval, rtext or f"{prop} {bop} {bval}"))
                except (TypeError, ValueError):
                    pass
            if rtype == "PROHIBITION":
                prohibited_by_scope.setdefault(base_sid, set()).update(_extract_prohibited_tokens(r))

    modified_scope_files: dict[str, list[dict[str, Any]]] = {}
    deleted_paths: set[str] = {str(p).strip() for p in (staged_diff_report.get("deleted_files") or []) if str(p).strip()}
    for f in staged_files:
        path, status = str(f.get("path") or f.get("file") or "").strip(), str(f.get("status") or "M").strip().upper()
        if status.startswith("D") or bool(f.get("deleted")):
            if path:
                deleted_paths.add(path)
            continue
        src = f.get("source") if f.get("source") is not None else (f.get("content") if f.get("content") is not None else f.get("code"))
        eff_imps = _populate_file_ast_metadata(f, src) if isinstance(src, str) and src.strip() else list(f.get("imports") or [])
        f_scopes = _resolve_file_scopes(path, str(f.get("scope") or f.get("component") or "").strip(), scopes, artifacts)
        cur_hash, top_hashes = str(f.get("file_hash") or "").strip(), (wb.get("file_hashes") if isinstance(wb.get("file_hashes"), dict) else {})
        for s in f_scopes:
            modified_scope_files.setdefault(s, []).append(f)
            sc = scopes.get(s) if isinstance(scopes.get(s), dict) else {}
            sc_hashes = sc.get("file_hashes") if isinstance(sc.get("file_hashes"), dict) else top_hashes
            base_h = next((str(v).strip() for k, v in sc_hashes.items() if _paths_match(k, path) and str(v).strip()), "")
            still_ver = str(sc.get("status") or sc.get("build_status") or "").strip().upper() == "VERIFIED"
            hash_changed = bool(cur_hash and base_h and cur_hash != base_h)
            if not (is_verified_scope_protected(hash_changed, still_ver) and verified_scope_edit_guard_law(hash_changed, still_ver)):
                contracts_ok = False
                violations.append({"kind": "CONTRACT_VIOLATION", "scope": s, "path": path, "description": f"{path} modified (file_hash {cur_hash} != baseline {base_h}) while component '{s}' is still VERIFIED; run turn first to mark '{s}' STALE"})
        raw_lc = f.get("line_count") if f.get("line_count") is not None else f.get("lines")
        line_cnt = len(raw_lc) if isinstance(raw_lc, list) else raw_lc
        f_raw_mlb = f.get("max_file_lines") if f.get("max_file_lines") is not None else max_lines_bound
        f_max_lines = int(f_raw_mlb) if isinstance(f_raw_mlb, (int, float)) and not isinstance(f_raw_mlb, bool) and f_raw_mlb > 0 else None
        if f_max_lines is not None and isinstance(line_cnt, (int, float)) and not isinstance(line_cnt, bool) and line_cnt > f_max_lines:
            bounds_ok = False
            violations.append({"kind": "BOUND_VIOLATION", "scope": f_scopes[0] if f_scopes else "global_defaults", "path": path, "description": f"{path} line_count ({int(line_cnt)}) exceeds max_file_lines bound ({f_max_lines})"})
        raw_consts = f.get("ast_constants")
        consts_map = {str(k).strip().lower(): v for k, v in raw_consts.items()} if isinstance(raw_consts, dict) else ({str(c.get("name") or "").strip().lower(): c.get("value") for c in raw_consts if isinstance(c, dict) and c.get("name")} if isinstance(raw_consts, list) else {})
        active_prohibited: set[str] = set()
        for check_sid in ("global_defaults", *f_scopes):
            active_prohibited.update(prohibited_by_scope.get(check_sid, set()))
            for b_prop, b_op, b_val, b_desc in scope_bounds.get(check_sid, []):
                cand_val = consts_map.get(b_prop.lower(), f.get(b_prop))
                if isinstance(cand_val, (int, float)) and not isinstance(cand_val, bool) and not _check_numeric_bound(float(cand_val), b_op, b_val):
                    bounds_ok = False
                    violations.append({"kind": "BOUND_VIOLATION", "scope": check_sid, "path": path, "description": f"{path} constant {b_prop}={cand_val} violates bound ({b_desc})"})
        bad_imps = [str(x).strip() for x in (f.get("prohibited_imports") or []) if str(x).strip()] + [
            str(imp).strip() for imp in eff_imps
            if str(imp).strip().replace("import ", "").split(".")[0].strip().lower() in active_prohibited or str(imp).strip().lower() in active_prohibited
        ]
        if bad_imps:
            bounds_ok = False
            violations.append({"kind": "PROHIBITION_VIOLATION", "scope": f_scopes[0] if f_scopes else "global_defaults", "path": path, "description": f"{path} contains prohibited imports: {', '.join(dict.fromkeys(bad_imps))}"})

    for s, s_files in modified_scope_files.items():
        sc = scopes.get(s) if isinstance(scopes.get(s), dict) else {}
        if not any(f.get("file_hash") for f in s_files) or not sc.get("depends_on"):
            continue
        s_srcs = "\n".join(str(f.get("source") or f.get("content") or f.get("code") or "") for f in s_files)
        if not s_srcs.strip():
            continue
        for dep in (str(d).strip() for d in (sc.get("depends_on") or []) if str(d).strip()):
            if dep == "global_defaults" or dep == s or not isinstance(dsc := scopes.get(dep), dict):
                continue
            dep_files = [fp for fp in _extract_scope_files(dsc) if fp.endswith(".py")]
            if not dep_files:
                continue
            dep_toks = {
                dep, dep.split("::")[0],
                *(Path(fp).stem for fp in dep_files if Path(fp).stem != "__init__"),
                *(Path(fp).parent.name for fp in dep_files if Path(fp).parent.name not in ("", ".", Path(dep_files[0]).parts[0])),
                *(str(a.get("name") or a.get("id") or "").strip() for a in artifacts if str(a.get("scope") or "").strip() == dep and str(a.get("name") or a.get("id") or "").strip()),
            }
            if not any(tok and tok in s_srcs for tok in dep_toks):
                contracts_ok = False
                violations.append({"kind": "CONTRACT_VIOLATION", "scope": s, "path": str(s_files[0].get("path") or ""), "description": f"Component '{s}' declares depends_on '{dep}' on the Lattice, but none of {s}'s files import or reference {sorted(dep_toks)}"})

    if staged_diff_report.get("bounds_ok") is False:
        bounds_ok = False
        if not any(v["kind"] in ("BOUND_VIOLATION", "PROHIBITION_VIOLATION") for v in violations):
            violations.append({"kind": "BOUND_VIOLATION", "scope": "global_defaults", "path": "", "description": "Staged diff report flagged bounds_ok=False"})

    decl_syms = (
        {str(a.get("name") or a.get("id") or "").strip() for a in artifacts if str(a.get("kind") or "").upper() in ("INTERFACE", "SCHEMA")}
        | {_extract_law_fn_name(a) for a in artifacts if str(a.get("kind") or "").upper() == "LAW"}
        | {str(a.get("name") or a.get("id") or "").strip() for a in artifacts if str(a.get("kind") or "").upper() == "LAW"}
    )
    unreg_syms = [
        {"scope": s, "path": str(f.get("path") or ""), "symbol": sym}
        for s, s_files in modified_scope_files.items()
        if str((scopes.get(s) or {}).get("status") or (scopes.get(s) or {}).get("build_status") or "").strip().upper() != "VERIFIED" or any(not f.get("file_hash") for f in s_files)
        for f in s_files
        for sym in sorted(_normalize_symbol_map(f.get("ast_functions"))[0] | _normalize_symbol_map(f.get("ast_classes") if "ast_classes" in f else f.get("ast_schemas"))[0])
        if sym and not sym.startswith("_") and sym != "main" and sym not in decl_syms
    ]
    flow_schemas = [
        str(fl.get("payload") or "").strip() for fl in (wb.get("flows") or [])
        if isinstance(fl, dict) and any(str(a.get("kind") or "").upper() == "SCHEMA" and str(a.get("name") or a.get("id") or "").lower() == str(fl.get("payload") or "").strip().lower() for a in artifacts)
    ]

    for art in (a for a in artifacts if str(a.get("kind") or "").upper() in ("INTERFACE", "SCHEMA")):
        akind, asid = str(art.get("kind") or "").upper(), str(art.get("scope") or "").strip()
        aname, afile = str(art.get("name") or art.get("id") or "").strip(), str(art.get("file") or "").strip()
        req_inputs = [str(x).split(":", 1)[0].split("=", 1)[0].strip().lstrip("*") for x in (art.get("inputs") or art.get("in") or art.get("fields") or []) if str(x).strip()]
        sc_files = _extract_scope_files(scopes.get(asid) or {})
        cand_cfiles = [f for f in staged_files if (afile and _paths_match(str(f.get("path") or ""), afile))] or ([] if (afile and len(sc_files) > 1) else modified_scope_files.get(asid, []))
        for cf in cand_cfiles:
            cpath = str(cf.get("path") or afile or "").strip()
            removed = {str(x).strip() for x in (cf.get("removed_symbols") or []) if str(x).strip()}
            if aname and aname in removed:
                contracts_ok = False
                violations.append({"kind": "CONTRACT_VIOLATION", "scope": asid, "path": cpath, "description": f"Required {akind} '{aname}' was removed from {cpath}"})
            has_fn_ast = "ast_functions" in cf and cf["ast_functions"] is not None
            has_cls_ast = ("ast_classes" in cf and cf["ast_classes"] is not None) or ("ast_schemas" in cf and cf["ast_schemas"] is not None)
            (fn_names, fn_params), (cls_names, cls_fields) = _normalize_symbol_map(cf.get("ast_functions")), _normalize_symbol_map(cf.get("ast_classes") if "ast_classes" in cf else cf.get("ast_schemas"))
            all_names = fn_names | cls_names
            if (akind == "INTERFACE" and has_fn_ast) or (akind == "SCHEMA" and (has_cls_ast or has_fn_ast)):
                if aname not in all_names and ((afile and (_paths_match(cpath, afile) or len(sc_files) <= 1)) or (len(cand_cfiles) == 1 and len(sc_files) <= 1)):
                    contracts_ok = False
                    violations.append({"kind": "CONTRACT_VIOLATION", "scope": asid, "path": cpath, "description": f"Required {akind} '{aname}' missing from {cpath}"})
                elif akind == "INTERFACE" and req_inputs and aname in fn_params and not any(k in fn_params[aname] for k in ("kwargs", "args")) and (missing_in := [inp for inp in req_inputs if inp not in fn_params[aname]]):
                    contracts_ok = False
                    violations.append({"kind": "CONTRACT_VIOLATION", "scope": asid, "path": cpath, "description": f"INTERFACE '{aname}' in {cpath} missing required inputs: {missing_in}"})
                elif akind == "SCHEMA" and req_inputs and aname in cls_fields and (missing_f := [fld for fld in req_inputs if fld not in cls_fields[aname]]):
                    contracts_ok = False
                    violations.append({"kind": "CONTRACT_VIOLATION", "scope": asid, "path": cpath, "description": f"SCHEMA '{aname}' in {cpath} missing required fields: {missing_f}"})

    if not (is_verified_scope_protected(False, False) and verified_scope_edit_guard_law(False, False)):
        contracts_ok = False
    has_direct_contract_err = not contracts_ok
    cg_report = verify_ast_call_graph_skeleton(whiteboard_graph, staged_diff_report)
    if not cg_report["is_valid"]:
        contracts_ok = False
        violations.extend(cg_report["violations"])
    if staged_diff_report.get("contracts_ok") is False:
        contracts_ok = False
        if not any(v["kind"] == "CONTRACT_VIOLATION" for v in violations):
            violations.append({"kind": "CONTRACT_VIOLATION", "scope": "global_defaults", "path": "", "description": "Staged diff report flagged contracts_ok=False"})

    import bowthruster.nlp as _nlp_mod
    nlp_mod = importlib.reload(_nlp_mod) if not hasattr(_nlp_mod, "verify_python_ast_laws_with_bend") else _nlp_mod
    ast_law_rep = nlp_mod.verify_python_ast_laws_with_bend(artifacts, staged_files, scopes)
    failed_laws = list(dict.fromkeys([*(staged_diff_report.get("failed_laws") or staged_diff_report.get("bend_failures") or []), *ast_law_rep["failed_laws"]]))
    if not bool(staged_diff_report.get("bend_proofs_ok", True)) or not ast_law_rep["bend_proofs_ok"] or failed_laws:
        bend_proofs_ok = False
        violations.append({"kind": "BEND_PROOF_FAILURE", "scope": "global_defaults", "path": "", "description": f"Bend 2 proof verification failed: {failed_laws or 'bend_proofs_ok=False'}"})

    healed_old_paths: set[str] = {str(ren["old_uri"]).strip() for ren in ((eff_uri_sync.get("renamed_uris") or eff_uri_sync.get("healed_renames") or []) if isinstance(eff_uri_sync, dict) else []) if isinstance(ren, dict) and ren.get("old_uri")}
    if isinstance(eff_uri_sync, dict) and (bool(eff_uri_sync.get("has_dangling_deleted_uri")) or bool(eff_uri_sync.get("has_dangling_delete")) or eff_uri_sync.get("is_valid") is False or eff_uri_sync.get("dangling_uris") or eff_uri_sync.get("dangling_deleted_uris")):
        no_dangling_deletes = False
        for duri in (eff_uri_sync.get("dangling_deleted_uris") or eff_uri_sync.get("dangling_uris") or [""]):
            d_str = str(duri.get("uri") if isinstance(duri, dict) else duri).strip()
            violations.append({"kind": "DANGLING_DELETED_URI", "scope": "global_defaults", "path": d_str, "description": f"URI sync report detected dangling deleted URI: {d_str or 'has_dangling_deleted_uri=True'}"})
    for dpath in (dp for dp in sorted(deleted_paths) if not any(_paths_match(dp, hp) for hp in healed_old_paths)):
        for sid, sc in scopes.items():
            if isinstance(sc, dict) and any(_paths_match(dpath, fp) for fp in _extract_scope_files(sc)):
                no_dangling_deletes = False
                violations.append({"kind": "DANGLING_DELETED_URI", "scope": sid, "path": dpath, "description": f"Deleted file '{dpath}' is still referenced by active scope '{sid}'"})
        for art in artifacts:
            if art.get("file") and _paths_match(dpath, str(art["file"])):
                no_dangling_deletes = False
                violations.append({"kind": "DANGLING_DELETED_URI", "scope": str(art.get("scope") or "global_defaults"), "path": dpath, "description": f"Deleted file '{dpath}' is still referenced by lattice artifact '{art.get('name') or art.get('id')}'"})
    if staged_diff_report.get("no_dangling_deletes") is False:
        no_dangling_deletes = False
        if not any(v["kind"] == "DANGLING_DELETED_URI" for v in violations):
            violations.append({"kind": "DANGLING_DELETED_URI", "scope": "global_defaults", "path": "", "description": "Staged diff report flagged no_dangling_deletes=False"})

    allowed = should_allow_git_commit(bounds_ok, contracts_ok, bend_proofs_ok, no_dangling_deletes)
    non_del_contract_err = has_direct_contract_err or bool(cg_report["disconnected_laws"]) or staged_diff_report.get("contracts_ok") is False or (bool(cg_report["dangling_imports"]) and no_dangling_deletes)
    bounce_reason = BOUNCE_NONE if allowed else (BOUNCE_RULE_CONFLICT if (not bounds_ok or non_del_contract_err) else (BOUNCE_BEND_PROOF_FAILURE if not bend_proofs_ok else BOUNCE_DANGLING_DELETED_URI))
    return GateDecisionResult({
        "allowed": allowed,
        "decision": DECISION_ALLOW_COMMIT if allowed else DECISION_REJECT_COMMIT,
        "bounce_reason": bounce_reason,
        "bounds_ok": bounds_ok,
        "contracts_ok": contracts_ok,
        "bend_proofs_ok": bend_proofs_ok,
        "bend_proof_status": str(ast_law_rep.get("bend_proof_status") or "NO_LAWS"),
        "bend_binary_checked": bool(ast_law_rep.get("bend_binary_checked", False)),
        "truth_table_checked": bool(ast_law_rep.get("truth_table_checked", True)),
        "no_dangling_deletes": no_dangling_deletes,
        "failed_laws": failed_laws,
        "checked_laws": ast_law_rep["checked_laws"],
        "call_graph_report": cg_report,
        "dangling_imports": cg_report["dangling_imports"],
        "disconnected_law_functions": cg_report["disconnected_law_functions"],
        "unregistered_public_symbols": unreg_syms,
        "flow_schema_checked": flow_schemas,
        "unverified_bound_violations": 0 if allowed else len(violations),
        "violations": violations,
    })
