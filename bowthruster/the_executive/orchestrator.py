"""Spec 23C §2.2 & Stage 26: Executive Pipeline Orchestrator & Post-Commit Lattice Updater (`orchestrator.py`)."""

from __future__ import annotations

import copy
import importlib
import importlib.util
import inspect
from pathlib import Path
from typing import Any, Callable

from bowthruster.ai_memory import extract_concept_cues, index_committed_rules, recall_concepts
from bowthruster.graph_core import DEFAULT_K_CAP, DEFAULT_MAX_RING, SQLiteGraphStore
from bowthruster.regiment.intake_packet import compile_intake_packet, compile_markdown_turn
from bowthruster.the_executive.gate import execute_gate_decision
from bowthruster.the_executive.preflight import (
    ExecutivePreflightError, clone_whiteboard_for_trial, inspect_turn_preflight)
from bowthruster.whiteboard import propagate_graph, scan_graph, sync_to_sqlite
from bowthruster.whiteboard.frustration import evaluate_lattice_proof as _eval_lattice_proof
from bowthruster.whiteboard.schema import ArtifactEntry
from .voice import synthesize_executive_voice

_DEL_STATUSES = frozenset({"D", "DELETED", "REMOVED", "REMOVE"})
_CONTRACT_KEYS = (("INTERFACE", "updated_interfaces"), ("INTERFACE", "interfaces"), ("INTERFACE", "ast_functions"),
    ("SCHEMA", "ast_classes"), ("SCHEMA", "updated_schemas"), ("SCHEMA", "schemas"))

def _resolve_table_fns() -> tuple[Callable[..., dict[str, Any]] | None, Callable[..., dict[str, Any]] | None]:
    fallback_sync: Callable[..., dict[str, Any]] | None = None
    for mod_name in ("the_table", "bowthruster.the_table"):
        try:
            if importlib.util.find_spec(mod_name) is not None and (mod := importlib.import_module(mod_name)):
                s_fn, f_fn = getattr(mod, "sync_memory_to_table", None), getattr(mod, "forage_table_context", None)
                if callable(s_fn) and callable(f_fn):
                    return s_fn, f_fn
                fallback_sync = s_fn if (callable(s_fn) and fallback_sync is None) else fallback_sync
        except (ImportError, ModuleNotFoundError):
            pass
    local_init = Path(__file__).resolve().parents[1] / "the_table" / "__init__.py"
    spec = importlib.util.spec_from_file_location("the_table", local_init) if local_init.is_file() else None
    if spec and spec.loader:
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if callable(s_fn := getattr(mod, "sync_memory_to_table", None)):
                f_fn = getattr(mod, "forage_table_context", None)
                return s_fn, (f_fn if callable(f_fn) else None)
        except (ImportError, ModuleNotFoundError):
            pass
    return fallback_sync, None

def _build_conflict_foraging_directives(gate_result: dict[str, Any], active_table_state: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
    raw_slots = active_table_state.get("slots") if isinstance(active_table_state, dict) else None
    slots = [s for s in raw_slots] if isinstance(raw_slots, list) else []
    on_cids = {str(s.get("concept_id") or "").strip() for s in slots} | {
        str(s.get("slot_id") or "").removeprefix("slot_").strip() for s in slots}
    target_scopes: list[str] = []
    for item in [*(gate_result.get("conflicts") or []), *(gate_result.get("flawed_ideas") or [])]:
        if isinstance(item, dict):
            raw_sc = str(item.get("scope", item.get("primary_scope", item.get("concept_id", ""))) or "").strip()
            if not raw_sc and "." in (rid := str(item.get("existing_rule_id", item.get("rule_id", "")) or "")):
                raw_sc = rid.split(".", 1)[0]
            if raw_sc and raw_sc != "global_defaults" and raw_sc not in target_scopes:
                target_scopes.append(raw_sc)
    directives: list[dict[str, Any]] = []
    for sc_id in target_scopes:
        if sc_id not in on_cids:
            directives.append({"action": "EXPAND_NEIGHBORS", "concept_id": sc_id})
        directives.append({"action": "ESCALATE_LOD", "concept_id": sc_id, "target_lod": "LOD2"})
        directives.append({"action": "PIN_SLOT", "concept_id": sc_id})
    return directives

def _collect_and_index_turn_rules(wb_graph: dict[str, Any], cur_turn: int, target_sids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
    committed_rules, saved_scope_rules = [], {}
    if not isinstance(wb_graph.get("external_links"), dict):
        wb_graph["external_links"] = {}
    saved_ext = copy.deepcopy(wb_graph["external_links"])
    scopes = (wb_graph.get("scopes") or {})
    for sid in (target_sids if target_sids is not None else list(scopes.keys())):
        if isinstance(sc := scopes.get(sid), dict) and bool(sc.get("rules")):
            turn_rules = [
                copy.deepcopy(r) for r in sc["rules"]
                if (target_sids is not None or int(r.get("updated_turn", r.get("turn", 0)) or 0) == cur_turn)
            ]
            if turn_rules:
                saved_scope_rules[sid] = copy.deepcopy(sc["rules"])
                for tr in turn_rules:
                    tr.setdefault("scope", sid)
                    tr.setdefault("concept_id", sid)
                committed_rules.extend(turn_rules)
    if committed_rules or target_sids is None:
        index_committed_rules(wb_graph, committed_rules, current_turn=cur_turn)
    for k, v in saved_ext.items():
        cur_lks = wb_graph["external_links"].get(k) if isinstance(wb_graph["external_links"].get(k), list) else []
        seen_u = {str(x.get("target_uri", x.get("uri", "")) or "").strip() for x in cur_lks if isinstance(x, dict)}
        wb_graph["external_links"][k] = cur_lks + [x for x in (v or [])
            if not (isinstance(x, dict) and str(x.get("target_uri", x.get("uri", "")) or "").strip() in seen_u)]
    for sid, orig_rules in saved_scope_rules.items():
        if isinstance(sc := scopes.get(sid), dict):
            idx_map = {str(r["id"]): r for r in ((sc.get("rules") or []))
                if r.get("id")}
            for orig_r in orig_rules:
                if (idx_r := idx_map.get(str(orig_r.get("id") or ""))) is not None:
                    if "access_turns" in idx_r:
                        orig_r["access_turns"] = list(idx_r["access_turns"])
                    if "external_links" in idx_r and "external_links" not in orig_r:
                        orig_r["external_links"] = copy.deepcopy(idx_r["external_links"])
            sc["rules"] = orig_rules
    sync_to_sqlite(wb_graph)
    return committed_rules

class ExecutiveTurnResult(dict[str, Any]):
    """Dict-compatible end-to-end return object supporting both dict and attribute access."""
    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name) or name not in self:
            raise AttributeError(name)
        return self[name]

PostCommitSyncResult = WorkingTreeDriftReport = ExecutiveTurnResult

def _prepare_turn_intake(raw_text: str, phase3_result: dict[str, Any] | None, intake_packet: dict[str, Any] | None,
    eff_turn: int, turn_index: int | None, prev_turn: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    had_precompiled = intake_packet is not None or phase3_result is not None
    if intake_packet is not None:
        return intake_packet, phase3_result
    if phase3_result is None:
        if not isinstance(raw_text, str):
            raise ExecutivePreflightError("preflight_invalid_raw_text",
                [f"raw_text must be a str, got {type(raw_text).__name__}"])
        intake_packet = compile_markdown_turn(raw_text, turn_index=eff_turn)
        phase3_result = {"raw_text": raw_text, "doc_tables": intake_packet["doc_tables"],
            "structured_table_rows": intake_packet["structured_table_rows"],
            "parsed_mcus": intake_packet["parsed_mcus"], "intake_packet": intake_packet,
            "grounding_status": "GROUNDED", "is_locked": True, "clarification_questions": [], "open_questions": []}
        return intake_packet, phase3_result
    if not isinstance(phase3_result, dict):
        raise ExecutivePreflightError("preflight_invalid_packet",
            [f"phase3_result must be a dict, got {type(phase3_result).__name__}"])
    if isinstance(phase3_result.get("intake_packet"), dict):
        intake_packet = copy.deepcopy(phase3_result["intake_packet"])
        if turn_index is not None or not had_precompiled or prev_turn > 0:
            intake_packet["turn_index"] = eff_turn
        return intake_packet, phase3_result
    return compile_intake_packet(phase3_result, turn_index=eff_turn), phase3_result

def _sync_and_forage_post_gate(gate_result: dict[str, Any], cue_text: str, seed_cues: list[str],
    recalled_concepts: list[dict[str, Any]], active_table_state: dict[str, Any] | None,
    table_view: dict[str, Any] | None, foraging_directives: list[dict[str, Any]] | None,
    store: SQLiteGraphStore | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    sync_fn, forage_fn = _resolve_table_fns()
    wb_graph, cur_turn = gate_result["whiteboard_graph"], int(gate_result["turn_index"])
    did_commit = gate_result["decision"] == "COMMIT_TO_WHITEBOARD" and bool(gate_result.get("whiteboard_mutated"))
    if did_commit and (wb_graph is not None):
        _collect_and_index_turn_rules(wb_graph, cur_turn)
        if seed_cues:
            seen_cids = {str(c.get("concept_id")) for c in recalled_concepts}
            for pc in recall_concepts(wb_graph, seed_cues):
                if isinstance(pc, dict) and str(pc.get("concept_id")) not in seen_cids:
                    seen_cids.add(str(pc.get("concept_id")))
                    recalled_concepts.append(pc)
        if callable(sync_fn):
            post_view = propagate_graph(wb_graph, cue_text) if cue_text.strip() else scan_graph(wb_graph, "")
            table_view = sync_fn(active_table_state, recalled_concepts, context_view=post_view, current_turn=cur_turn)
            if (table_view is not None) and isinstance(table_view.get("table_state"), dict):
                active_table_state = table_view["table_state"]
    stage2_directives = [copy.deepcopy(d) for d in (foraging_directives or []) if isinstance(d, dict)]
    stage2_directives.extend(_build_conflict_foraging_directives(gate_result, active_table_state))
    foraging_actions: list[dict[str, Any]] = []
    if stage2_directives and callable(forage_fn):
        table_view = forage_fn(active_table_state, stage2_directives,
            whiteboard_graph=wb_graph if (wb_graph is not None) else None, store=store, current_turn=cur_turn)
        if (table_view is not None):
            foraging_actions = list(table_view.get("foraging_actions_applied", []))
    elif (table_view is not None):
        table_view.setdefault("foraging_actions_applied", [])
        table_view.setdefault("foraging_steps", 0)
    return table_view, foraging_actions

def run_executive_turn(
    whiteboard_graph: dict[str, Any] | None = None,
    raw_text: str = "",
    *,
    turn_index: int | None = None,
    phase3_result: dict[str, Any] | None = None,
    intake_packet: dict[str, Any] | None = None,
    store: SQLiteGraphStore | None = None,
    db_path: str | Path | None = None,
    table_state: dict[str, Any] | None = None,
    flawed_idea_critiques: list[Any] | None = None,
    foraging_directives: Any = None,
    k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING,
) -> ExecutiveTurnResult:
    """Executes a turn across `regiment` -> Hippocampal Ignition -> Pre-Flight -> Gate -> `the_table` -> Voice."""
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        raise ExecutivePreflightError("preflight_invalid_whiteboard",
            [f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}"])
    if turn_index is not None and (isinstance(turn_index, bool) or not isinstance(turn_index, int) or turn_index < 1):
        raise ExecutivePreflightError("preflight_invalid_packet",
            [f"turn_index must be a positive int, got {turn_index!r}"])
    wb_dict = whiteboard_graph or {}
    prev_turn = max(int(wb_dict.get("turn", 0) or 0), int(wb_dict.get("turn_index", 0) or 0))
    eff_turn = int(turn_index) if turn_index is not None else max(1, prev_turn + 1)
    intake_packet, phase3_result = _prepare_turn_intake(raw_text, phase3_result, intake_packet, eff_turn, turn_index,
        prev_turn)
    cue_text = raw_text if isinstance(raw_text, str) and raw_text.strip() else " ".join(
        str(m.get("text", m.get("canonical_text", "")) or "").strip()
        for m in ((intake_packet or {}).get("parsed_mcus", []) or [])
        if isinstance(m, dict)
    )
    seed_cues = extract_concept_cues(cue_text)
    recall_src = whiteboard_graph if isinstance(whiteboard_graph, dict) else (store if store is not None
        else {"scopes": {}})
    recalled_concepts = recall_concepts(recall_src, seed_cues)
    sync_fn, _ = _resolve_table_fns()
    active_table_state, table_view = table_state, None
    if callable(sync_fn):
        pre_graph = clone_whiteboard_for_trial(whiteboard_graph) if isinstance(whiteboard_graph, dict) else None
        pre_view = (propagate_graph(pre_graph, cue_text) if cue_text.strip() else scan_graph(pre_graph,
            "")) if isinstance(pre_graph, dict) else None
        table_view = sync_fn(active_table_state, recalled_concepts, context_view=pre_view, current_turn=eff_turn)
        if (table_view is not None) and isinstance(table_view.get("table_state"), dict):
            active_table_state = table_view["table_state"]
    preflight_report = inspect_turn_preflight(
        intake_packet, whiteboard_graph, turn_index=turn_index,
        flawed_idea_critiques=flawed_idea_critiques,
    )
    gate_result = execute_gate_decision(
        preflight_report, whiteboard_graph,
        k_cap=int(k_cap), max_ring=int(max_ring), store=store, db_path=db_path,
    )
    wb_graph, cur_turn = gate_result["whiteboard_graph"], int(gate_result["turn_index"])
    table_view, foraging_actions = _sync_and_forage_post_gate(
        gate_result, cue_text, seed_cues, recalled_concepts, active_table_state,
        table_view, foraging_directives, store,
    )
    voice_reply = synthesize_executive_voice(gate_result)
    raw_lp = gate_result.get("lattice_proof")
    lattice_proof = raw_lp if isinstance(raw_lp, dict) else _eval_lattice_proof(wb_graph)
    if bool(gate_result.get("whiteboard_mutated")) and (wb_graph is not None):
        wb_graph["lattice_proof"] = lattice_proof
    user_msg = str(voice_reply.get("user_message") or "")
    if (
        gate_result["decision"] == "COMMIT_TO_WHITEBOARD"
        and isinstance(lattice_proof, dict)
        and "[Lattice Proof:" not in user_msg
    ):
        if lattice_proof.get("is_proven") is True:
            msg_sfx = "[Lattice Proof: PROVEN_COMPLETE (0 gaps — Work Packets ready to emit)]"
            voice_reply["user_message"] = f"{user_msg}\n{msg_sfx}"
        elif lattice_proof.get("status") == "INCOMPLETE_DRAFT" and (gaps := list(lattice_proof.get("open_gaps") or [])):
            gap_ids = ", ".join(g.get("scope", "?") + ":" + g.get("kind", "?") for g in gaps)
            msg_sfx = (
                f"[Lattice Proof: INCOMPLETE_DRAFT ({len(gaps)} gap(s) "
                f"remaining before Work Packets unlock: {gap_ids})]"
            )
            voice_reply["user_message"] = f"{user_msg}\n{msg_sfx}"
    return ExecutiveTurnResult({
        "turn_index": cur_turn, "decision": gate_result["decision"], "bounce_reason": gate_result["bounce_reason"],
        "should_commit": gate_result["should_commit"], "whiteboard_mutated": gate_result["whiteboard_mutated"],
        "user_message": voice_reply["user_message"], "cited_items": list(voice_reply["cited_items"]),
        "whiteboard_graph": wb_graph, "committed_items": list(gate_result["committed_items"]),
        "blocked_items": list(gate_result["blocked_items"]), "conflicts": list(gate_result["conflicts"]),
        "flawed_ideas": list(gate_result["flawed_ideas"]),
        "clarification_questions": list(gate_result["clarification_questions"]),
        "open_questions": list(gate_result["open_questions"]), "intake_packet": preflight_report["intake_packet"],
        "phase3_result": phase3_result, "preflight_report": preflight_report, "gate_result": gate_result,
        "voice_reply": voice_reply, "lattice_proof": lattice_proof, "seed_cues": seed_cues,
        "recalled_concepts": recalled_concepts, "table_view": table_view, "foraging_actions": foraging_actions})

def should_mark_dependent_stale_on_commit(upstream_contract_changed: bool, dependent_was_verified: bool) -> bool:
    """Pure downstream stale-cascade guard governed by Bend 2 Law 4 (`postcommit_stale_cascade_law`, Stage 26)."""
    return bool(upstream_contract_changed) and bool(dependent_was_verified)

from bowthruster.the_executive import (
    _find_owning_scopes,
    _norm_git_path,
    _normalize_report_entry,
    _parse_git_report_entries,
    _paths_match,
    _scope_direct_deps,
    _scope_file_refs,
    _upsert_contract_artifact,
)

def _apply_commit_file_updates(
    whiteboard_graph: dict[str, Any], commit_report: dict[str, Any], file_entries: list[dict[str, Any]],
    top_hashes: dict[str, str], commit_sha: str | None, eff_turn: int,
    modified_scopes: list[str], contract_changed_scopes: set[str]) -> list[str]:
    scopes = whiteboard_graph.setdefault("scopes", {})
    updated_artifacts: list[str] = []
    for fe in file_entries:
        is_del, path, old_path, f_hash = (fe["status"] in _DEL_STATUSES), fe["path"], fe["old_path"], fe["file_hash"]
        for sid in _find_owning_scopes(whiteboard_graph, path, old_path, fe["scope"]):
            if sid not in modified_scopes:
                modified_scopes.append(sid)
            sc = scopes[sid]
            if commit_sha is not None:
                sc["last_commit_sha"] = commit_sha
            if is_del and bool(sc.get("file_hashes")):
                sc["file_hashes"].pop(path, None)
            elif not is_del and f_hash and path:
                fh_map = sc.setdefault("file_hashes", {})
                if old_path:
                    fh_map.pop(old_path, None)
                fh_map[path] = f_hash
            has_ckey = any(fe.get(k) for _, k in _CONTRACT_KEYS)
            if bool(fe.get("contract_changed", commit_report.get("contract_changed")) or has_ckey):
                contract_changed_scopes.add(sid)
                for kind, key in _CONTRACT_KEYS:
                    for item in (fe.get(key) or []):
                        if (aname := _upsert_contract_artifact(whiteboard_graph, sid, kind, item, path,
                            eff_turn)) and aname not in updated_artifacts:
                            updated_artifacts.append(aname)
                if not has_ckey:
                    for art in (whiteboard_graph.get("lattice_artifacts") or []):
                        if art.get("scope_id",
                            art.get("scope")) == sid and art.get("kind") in ("INTERFACE", "SCHEMA"):
                            art["updated_turn"] = eff_turn
                            aname = str(art.get("name", art.get("id", "")) or "").strip()
                            if aname and aname not in updated_artifacts:
                                updated_artifacts.append(aname)
    for path, f_hash in top_hashes.items():
        for sid in _find_owning_scopes(whiteboard_graph, path):
            if sid not in modified_scopes:
                modified_scopes.append(sid)
            scopes[sid].setdefault("file_hashes", {})[path] = f_hash
            if commit_sha is not None:
                scopes[sid]["last_commit_sha"] = commit_sha
    if commit_sha is not None and not file_entries and not top_hashes:
        for sid in modified_scopes:
            scopes[sid]["last_commit_sha"] = commit_sha
            if bool(commit_report.get("contract_changed")):
                contract_changed_scopes.add(sid)
    for kind, key in _CONTRACT_KEYS:
        for item in (commit_report.get(key) if isinstance(commit_report.get(key), list) else ()):
            raw_sc_val = (
                item.get("scope", item.get("scope_id", item.get("component", ""))) if isinstance(item, dict) else ""
            )
            raw_scope = str(raw_sc_val or "").strip()
            for sid in ([raw_scope] if raw_scope else modified_scopes):
                if sid in scopes:
                    contract_changed_scopes.add(sid)
                    if (aname := _upsert_contract_artifact(whiteboard_graph, sid, kind, item, "",
                        eff_turn)) and aname not in updated_artifacts:
                        updated_artifacts.append(aname)
    return updated_artifacts

def sync_post_commit_to_lattice(whiteboard_graph: dict[str, Any], commit_report: dict[str, Any],
    *, store: Any = None, current_turn: int | None = None) -> PostCommitSyncResult:
    """Synchronizes a completed git commit (`commit_report`) into `whiteboard_graph` and AI Memory (Stage 26)."""
    if not isinstance(whiteboard_graph, dict):
        raise ExecutivePreflightError("postcommit_invalid_whiteboard",
            [f"whiteboard_graph must be a dict, got {type(whiteboard_graph).__name__}"])
    if not isinstance(commit_report, dict):
        raise ExecutivePreflightError("postcommit_invalid_commit_report",
            [f"commit_report must be a dict, got {type(commit_report).__name__}"])
    if current_turn is not None and (isinstance(current_turn, bool) or not isinstance(current_turn, int)
        or current_turn < 1):
        raise ExecutivePreflightError("postcommit_invalid_turn",
            [f"current_turn must be a positive int, got {current_turn!r}"])
    eff_turn = (int(current_turn) if current_turn is not None
        else max(1, int(whiteboard_graph.get("turn", 0) or 0), int(whiteboard_graph.get("turn_index", 0) or 0)))
    if isinstance(store, SQLiteGraphStore):
        whiteboard_graph["_sqlite_store"] = store
    raw_sha = commit_report.get(
        "commit_sha", commit_report.get("sha", commit_report.get("commit_id", commit_report.get("head_sha")))
    )
    commit_sha = str(raw_sha).strip() if raw_sha else None
    if commit_sha is not None:
        whiteboard_graph["last_commit_sha"] = commit_sha
    file_entries, rename_map, line_shifts, top_hashes = _parse_git_report_entries(commit_report)
    scopes = whiteboard_graph.setdefault("scopes", {})
    modified_scopes = [s for s in (commit_report.get("modified_scopes") or []) if s in scopes]
    raw_cc = commit_report.get("contract_changed_scopes", commit_report.get("changed_contracts", []))
    contract_changed_scopes = {s for s in (raw_cc or []) if s in scopes}
    updated_artifacts = _apply_commit_file_updates(whiteboard_graph, commit_report, file_entries, top_hashes,
        commit_sha, eff_turn, modified_scopes, contract_changed_scopes)
    raw_usr = commit_report.get("uri_sync_report")
    uri_sync_report: dict[str, Any] = copy.deepcopy(raw_usr) if isinstance(raw_usr, dict) else {}
    if callable(sync_uris_fn := getattr(importlib.import_module("bowthruster.ai_memory"), "sync_git_diff_uris", None)):
        eff_manifest = {**commit_report, **({"renames": rename_map} if rename_map else {}),
            **({"line_shifts": line_shifts} if line_shifts else {})}
        sig_params = inspect.signature(sync_uris_fn).parameters
        accepts_turn = "current_turn" in sig_params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p
            in sig_params.values())
        u_res = (sync_uris_fn(whiteboard_graph, eff_manifest, current_turn=eff_turn)
            if accepts_turn else sync_uris_fn(whiteboard_graph, eff_manifest))
        if isinstance(u_res, dict):
            uri_sync_report.update(u_res)
    del_paths = [fe["path"] for fe in file_entries if fe["status"] in _DEL_STATUSES and fe["path"]]
    dangling = list(uri_sync_report.get("dangling_uris") or [])
    for sid, sc in (scopes.items() if del_paths else ()):
        for ref in (_scope_file_refs(whiteboard_graph, sid, sc) if sc else ()):
            if any(_paths_match(ref, dp) for dp in del_paths) and ref not in dangling:
                dangling.append(ref)
    for k, v in (("updated_uris", []), ("renamed_count", len(rename_map)),
        ("dangling_uri_pointers", len(dangling)), ("dangling_uris", dangling), ("is_valid", len(dangling) == 0)):
        uri_sync_report.setdefault(k, v)
    cascaded_stale_scopes: list[str] = []
    for dep_sid, dep_sc in list(scopes.items()):
        if bool(dep_sc):
            visited_anc, queue = set(), [d for d in _scope_direct_deps(dep_sc) if d != dep_sid]
            while queue:
                if (cur := queue.pop(0)) not in visited_anc:
                    visited_anc.add(cur)
                    if isinstance(cur_sc := scopes.get(cur), dict):
                        queue.extend(nxt for nxt in _scope_direct_deps(cur_sc) if nxt not in visited_anc and nxt
                            != dep_sid)
            attrs = (dep_sc.get("attributes") or {})
            raw_dep_st = dep_sc.get(
                "status", dep_sc.get("build_status", attrs.get("status", attrs.get("_table_status", "")))
            )
            dep_status = str(raw_dep_st or "").strip().upper()
            if should_mark_dependent_stale_on_commit(any(u in contract_changed_scopes for u in visited_anc), dep_status
                == "VERIFIED"):
                cascaded_stale_scopes.append(dep_sid)
    for dep_sid in cascaded_stale_scopes:
        dep_sc = scopes[dep_sid]
        dep_sc["status"] = dep_sc["build_status"] = "STALE"
        dep_sc["updated_turn"] = eff_turn
        if isinstance(attrs := dep_sc.get("attributes"), dict):
            attrs.update({ak: "STALE" for ak in ("status", "_table_status") if ak in attrs})
    stale_unflagged = sum(1 for sid in cascaded_stale_scopes if str(scopes[sid].get("status") or "").upper() != "STALE")
    _collect_and_index_turn_rules(whiteboard_graph, eff_turn, target_sids=modified_scopes)
    return PostCommitSyncResult({
        "commit_sha": commit_sha, "modified_scopes": modified_scopes, "cascaded_stale_scopes": cascaded_stale_scopes,
        "stale_unflagged_dependents": stale_unflagged, "uri_sync_report": uri_sync_report,
        "updated_artifacts": updated_artifacts, "whiteboard_graph": whiteboard_graph})

def detect_working_tree_drift(
    whiteboard_graph: dict[str, Any], working_tree_status: dict[str, Any] | list[dict[str, Any]],
) -> WorkingTreeDriftReport:
    """Inspects uncommitted working-tree changes against `whiteboard_graph` without mutating it (Stage 26)."""
    if not isinstance(whiteboard_graph, dict):
        raise ExecutivePreflightError("drift_invalid_whiteboard",
            [f"whiteboard_graph must be a dict, got {type(whiteboard_graph).__name__}"])
    if not isinstance(working_tree_status, (dict, list)):
        raise ExecutivePreflightError("drift_invalid_status",
            [f"working_tree_status must be a dict or list, got {type(working_tree_status).__name__}"])
    scopes = (whiteboard_graph.get("scopes") or {})
    entries, _, _, top_hashes = _parse_git_report_entries(working_tree_status)
    uncommitted_files, deleted_or_moved, drifted_scopes, dangling_uris = [], [], [], []
    def _mark(path_str: str, is_del: bool = False, old_p: str = "", exp_scope: Any = None) -> None:
        if p := str(path_str or "").strip():
            if p not in uncommitted_files:
                uncommitted_files.append(p)
            for dp in ((p,) if is_del else ()) + ((old_p,) if old_p else ()):
                if dp not in deleted_or_moved:
                    deleted_or_moved.append(dp)
            for sid in _find_owning_scopes(whiteboard_graph, p, old_p, exp_scope):
                if sid not in drifted_scopes:
                    drifted_scopes.append(sid)
    for item in entries:
        path, old_p, st, f_hash = item["path"], item["old_path"], item["status"], item["file_hash"]
        if st in ("CLEAN", "UNCHANGED", "OK"):
            if f_hash and any(bool(sc.get("file_hashes"))
                and any(_paths_match(k, path) and str(v) != f_hash for k, v in sc["file_hashes"].items())
                for sc in scopes.values()):
                _mark(path, exp_scope=item["scope"])
        else:
            _mark(path or old_p, is_del=st in _DEL_STATUSES, old_p=old_p, exp_scope=item["scope"])
    for fp, cur_hash in top_hashes.items():
        for sid, sc in scopes.items():
            if bool(sc.get("file_hashes")) and any(
                _paths_match(k, fp) and str(v) != str(cur_hash) for k, v in sc["file_hashes"].items()):
                _mark(str(fp), exp_scope=sid)
    for sid, sc in (scopes.items() if deleted_or_moved else ()):
        for ref in (_scope_file_refs(whiteboard_graph, sid, sc) if sc else ()):
            if any(_paths_match(ref, dp) for dp in deleted_or_moved):
                if ref not in dangling_uris:
                    dangling_uris.append(ref)
                if sid not in drifted_scopes:
                    drifted_scopes.append(sid)
    return WorkingTreeDriftReport({"has_drift": bool(drifted_scopes or uncommitted_files or dangling_uris),
        "drifted_scopes": drifted_scopes, "uncommitted_files": uncommitted_files, "dangling_uris": dangling_uris})
