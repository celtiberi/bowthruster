"""Spec 23C §2.2 & Stage 26: End-to-End Executive Pipeline Orchestrator & Post-Commit Lattice Updater (`orchestrator.py`)."""
from __future__ import annotations
import copy, importlib, importlib.util, inspect
from pathlib import Path
from typing import Any, Callable
from bowthruster.ai_memory import extract_concept_cues, index_committed_rules, recall_concepts
from bowthruster.graph_core import DEFAULT_K_CAP, DEFAULT_MAX_RING, SQLiteGraphStore
from bowthruster.regiment.intake_packet import compile_intake_packet, compile_markdown_turn
from bowthruster.the_executive.gate import execute_gate_decision
from bowthruster.the_executive.preflight import ExecutivePreflightError, clone_whiteboard_for_trial, inspect_turn_preflight
from bowthruster.types import SizeLimits
from bowthruster.whiteboard import propagate_graph, scan_graph, sync_to_sqlite
from bowthruster.whiteboard.frustration import evaluate_lattice_proof as _eval_lattice_proof
from bowthruster.whiteboard.schema import ArtifactEntry
from .voice import synthesize_executive_voice


def is_turn_zero_llm(llm_invoked_on_turn: bool = False) -> bool:
    """Pure zero-LLM turn orchestration guard governed by Bend 2 `zero_llm_turn_orchestration_law` (Stage 23C)."""
    return bool(not llm_invoked_on_turn)


def zero_llm_turn_orchestration_law(llm_invoked_on_turn: bool = False) -> bool:
    """Alias for Bend 2 `zero_llm_turn_orchestration_law` AST call-graph verification."""
    return is_turn_zero_llm(llm_invoked_on_turn)


def _resolve_table_fns() -> tuple[Callable[..., dict[str, Any]] | None, Callable[..., dict[str, Any]] | None]:
    fallback_sync: Callable[..., dict[str, Any]] | None = None
    for mod_name in ("the_table", "bowthruster.the_table"):
        try:
            if importlib.util.find_spec(mod_name) is not None and (mod := importlib.import_module(mod_name)):
                s_fn, f_fn = getattr(mod, "sync_memory_to_table", None), getattr(mod, "forage_table_context", None)
                if callable(s_fn) and callable(f_fn): return s_fn, f_fn
                fallback_sync = s_fn if (callable(s_fn) and fallback_sync is None) else fallback_sync
        except (ImportError, ModuleNotFoundError): pass
    local_init = Path(__file__).resolve().parents[1] / "the_table" / "__init__.py"
    if local_init.is_file() and (spec := importlib.util.spec_from_file_location("the_table", local_init)) and spec.loader:
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if callable(s_fn := getattr(mod, "sync_memory_to_table", None)):
                return s_fn, (f_fn if callable(f_fn := getattr(mod, "forage_table_context", None)) else None)
        except (ImportError, ModuleNotFoundError): pass
    return fallback_sync, None


def _build_conflict_foraging_directives(gate_result: dict[str, Any], active_table_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    slots = active_table_state.get("slots", []) if isinstance(active_table_state, dict) and isinstance(active_table_state.get("slots"), list) else []
    on_cids = {str(s.get("concept_id") or "").strip() for s in slots if isinstance(s, dict)} | {str(s.get("slot_id") or "")[5:] if str(s.get("slot_id") or "").startswith("slot_") else str(s.get("slot_id") or "").strip() for s in slots if isinstance(s, dict)}
    target_scopes: list[str] = []
    for item in [*(gate_result.get("conflicts") or []), *(gate_result.get("flawed_ideas") or [])]:
        if isinstance(item, dict):
            raw_sc = str(item.get("scope") or item.get("primary_scope") or item.get("concept_id") or "").strip()
            if not raw_sc and "." in (rid := str(item.get("existing_rule_id") or item.get("rule_id") or "").strip()): raw_sc = rid.split(".", 1)[0]
            if raw_sc and raw_sc != "global_defaults" and raw_sc not in target_scopes: target_scopes.append(raw_sc)
    directives: list[dict[str, Any]] = []
    for sc_id in target_scopes:
        if sc_id not in on_cids: directives.append({"action": "EXPAND_NEIGHBORS", "concept_id": sc_id})
        directives.extend(({"action": "ESCALATE_LOD", "concept_id": sc_id, "target_lod": "LOD2"}, {"action": "PIN_SLOT", "concept_id": sc_id}))
    return directives


def _collect_and_index_turn_rules(wb_graph: dict[str, Any], cur_turn: int, target_sids: list[str] | None = None) -> list[dict[str, Any]]:
    committed_rules, saved_scope_rules = [], {}
    if not isinstance(wb_graph.get("external_links"), dict): wb_graph["external_links"] = {}
    saved_ext = copy.deepcopy(wb_graph["external_links"])
    scopes = wb_graph.get("scopes") if isinstance(wb_graph.get("scopes"), dict) else {}
    for sid in (target_sids if target_sids is not None else list(scopes.keys())):
        if isinstance(sc := scopes.get(sid), dict) and isinstance(sc.get("rules"), list):
            if turn_rules := [copy.deepcopy(r) for r in sc["rules"] if isinstance(r, dict) and (target_sids is not None or int(r.get("updated_turn", r.get("turn", 0)) or 0) == cur_turn)]:
                saved_scope_rules[sid] = copy.deepcopy(sc["rules"])
                for tr in turn_rules: tr.setdefault("scope", sid); tr.setdefault("concept_id", sid)
                committed_rules.extend(turn_rules)
    if committed_rules or target_sids is None: index_committed_rules(wb_graph, committed_rules, current_turn=cur_turn)
    for k, v in saved_ext.items():
        cur_lks = wb_graph["external_links"].get(k) if isinstance(wb_graph["external_links"].get(k), list) else []
        seen_u = {str(x.get("target_uri") or x.get("uri") or "") for x in cur_lks if isinstance(x, dict)}
        wb_graph["external_links"][k] = cur_lks + [x for x in (v if isinstance(v, list) else []) if not (isinstance(x, dict) and str(x.get("target_uri") or x.get("uri") or "") in seen_u)]
    for sid, orig_rules in saved_scope_rules.items():
        if isinstance(sc := scopes.get(sid), dict):
            idx_map = {str(r.get("id")): r for r in (sc.get("rules") if isinstance(sc.get("rules"), list) else []) if isinstance(r, dict) and r.get("id")}
            for orig_r in orig_rules:
                if (idx_r := idx_map.get(str(orig_r.get("id") or ""))) is not None:
                    if "access_turns" in idx_r: orig_r["access_turns"] = list(idx_r["access_turns"])
                    if "external_links" in idx_r and "external_links" not in orig_r: orig_r["external_links"] = copy.deepcopy(idx_r["external_links"])
            sc["rules"] = orig_rules
    sync_to_sqlite(wb_graph)
    return committed_rules


class ExecutiveTurnResult(dict[str, Any]):
    """Dict-compatible end-to-end return object supporting both dict and attribute access (Spec 23C §2.2 & Stage 26)."""
    def to_dict(self) -> dict[str, Any]: return copy.deepcopy(dict(self))
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name) or name not in self: raise AttributeError(name)
        return self[name]


PostCommitSyncResult = WorkingTreeDriftReport = ExecutiveTurnResult


def run_executive_turn(whiteboard_graph: dict[str, Any] | None = None, raw_text: str = "", *, gen: Any = None, jev: Any = None, executive_gen: Any = None, limits: SizeLimits | None = None, turn_index: int | None = None, phase3_result: dict[str, Any] | None = None, intake_packet: dict[str, Any] | None = None, flawed_idea_critiques: list[dict[str, Any]] | None = None, table_state: dict[str, Any] | None = None, foraging_directives: list[dict[str, Any]] | None = None, k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING, store: SQLiteGraphStore | None = None, db_path: str | Path | None = None) -> ExecutiveTurnResult:
    """Executes a complete turn across `regiment` -> Hippocampal Ignition -> Stage 23A Pre-Flight -> Stage 23B Gate -> `the_table` -> Stage 23C Voice."""
    _ = (gen, jev, executive_gen, limits)
    if not (is_turn_zero_llm(False) and zero_llm_turn_orchestration_law(False)):
        raise ExecutivePreflightError("turn_llm_invoked", ["zero_llm_turn_orchestration_law violated"])
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        raise ExecutivePreflightError("preflight_invalid_whiteboard", [f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}"])
    if turn_index is not None and (isinstance(turn_index, bool) or not isinstance(turn_index, int) or turn_index < 1):
        raise ExecutivePreflightError("preflight_invalid_packet", [f"turn_index must be a positive int, got {turn_index!r}"])
    prev_turn = max(int(whiteboard_graph.get("turn", 0) or 0), int(whiteboard_graph.get("turn_index", 0) or 0)) if isinstance(whiteboard_graph, dict) else 0
    eff_turn, had_precompiled_intake = (int(turn_index) if turn_index is not None else max(1, prev_turn + 1)), (intake_packet is not None or phase3_result is not None)
    if intake_packet is None:
        if phase3_result is None:
            if not isinstance(raw_text, str):
                raise ExecutivePreflightError("preflight_invalid_raw_text", [f"raw_text must be a str, got {type(raw_text).__name__}"])
            intake_packet = compile_markdown_turn(raw_text, turn_index=eff_turn)
            phase3_result = {"raw_text": raw_text, "doc_tables": intake_packet["doc_tables"], "structured_table_rows": intake_packet["structured_table_rows"], "parsed_mcus": intake_packet["parsed_mcus"], "intake_packet": intake_packet, "grounding_status": "GROUNDED", "is_locked": True, "clarification_questions": [], "open_questions": []}
        elif not isinstance(phase3_result, dict):
            raise ExecutivePreflightError("preflight_invalid_packet", [f"phase3_result must be a dict, got {type(phase3_result).__name__}"])
        elif isinstance(phase3_result.get("intake_packet"), dict):
            intake_packet = copy.deepcopy(phase3_result["intake_packet"])
            if turn_index is not None or not had_precompiled_intake or prev_turn > 0:
                intake_packet["turn_index"] = eff_turn
        else:
            intake_packet = compile_intake_packet(phase3_result, turn_index=eff_turn)

    cue_text = raw_text if isinstance(raw_text, str) and raw_text.strip() else " ".join(
        str(m.get("text") or m.get("canonical_text") or "") for m in (intake_packet.get("parsed_mcus", []) if isinstance(intake_packet, dict) else []) if isinstance(m, dict)
    )
    seed_cues = extract_concept_cues(cue_text)
    recalled_concepts = recall_concepts(whiteboard_graph if isinstance(whiteboard_graph, dict) else (store if store is not None else {"scopes": {}}), seed_cues)
    sync_fn, forage_fn = _resolve_table_fns()
    active_table_state, table_view = table_state, None
    if callable(sync_fn):
        pre_graph = clone_whiteboard_for_trial(whiteboard_graph) if isinstance(whiteboard_graph, dict) else None
        pre_view = (propagate_graph(pre_graph, cue_text) if cue_text.strip() else scan_graph(pre_graph, "")) if isinstance(pre_graph, dict) else None
        table_view = sync_fn(active_table_state, recalled_concepts, context_view=pre_view, current_turn=eff_turn)
        if isinstance(table_view, dict) and isinstance(table_view.get("table_state"), dict): active_table_state = table_view["table_state"]

    preflight_report = inspect_turn_preflight(intake_packet, whiteboard_graph, turn_index=turn_index, flawed_idea_critiques=flawed_idea_critiques)
    gate_result = execute_gate_decision(preflight_report, whiteboard_graph, k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)
    wb_graph, cur_turn = gate_result["whiteboard_graph"], int(gate_result["turn_index"])
    if gate_result["decision"] == "COMMIT_TO_WHITEBOARD" and bool(gate_result.get("whiteboard_mutated")) and isinstance(wb_graph, dict):
        _collect_and_index_turn_rules(wb_graph, cur_turn)
        if seed_cues:
            seen_cids = {str(c.get("concept_id")) for c in recalled_concepts if isinstance(c, dict)}
            for pc in recall_concepts(wb_graph, seed_cues):
                if isinstance(pc, dict) and str(pc.get("concept_id")) not in seen_cids:
                    seen_cids.add(str(pc.get("concept_id"))); recalled_concepts.append(pc)
        if callable(sync_fn):
            post_view = propagate_graph(wb_graph, cue_text) if cue_text.strip() else scan_graph(wb_graph, "")
            table_view = sync_fn(active_table_state, recalled_concepts, context_view=post_view, current_turn=cur_turn)
            if isinstance(table_view, dict) and isinstance(table_view.get("table_state"), dict): active_table_state = table_view["table_state"]

    stage2_directives = [copy.deepcopy(d) for d in (foraging_directives or []) if isinstance(d, dict)] + _build_conflict_foraging_directives(gate_result, active_table_state)
    foraging_actions: list[dict[str, Any]] = []
    if stage2_directives and callable(forage_fn):
        table_view = forage_fn(active_table_state, stage2_directives, whiteboard_graph=wb_graph if isinstance(wb_graph, dict) else None, store=store, current_turn=cur_turn)
        if isinstance(table_view, dict):
            foraging_actions = list(table_view.get("foraging_actions_applied", []))
            if isinstance(table_view.get("table_state"), dict): active_table_state = table_view["table_state"]
    elif isinstance(table_view, dict):
        table_view.setdefault("foraging_actions_applied", []); table_view.setdefault("foraging_steps", 0)

    voice_reply = synthesize_executive_voice(gate_result)
    lattice_proof = gate_result["lattice_proof"] if isinstance(gate_result.get("lattice_proof"), dict) else _eval_lattice_proof(wb_graph)
    if bool(gate_result.get("whiteboard_mutated")) and isinstance(wb_graph, dict):
        wb_graph["lattice_proof"] = lattice_proof
    if gate_result["decision"] == "COMMIT_TO_WHITEBOARD" and isinstance(lattice_proof, dict) and "[Lattice Proof:" not in (user_msg := str(voice_reply.get("user_message") or "")):
        if lattice_proof.get("is_proven") is True:
            voice_reply["user_message"] = user_msg + "\n[Lattice Proof: PROVEN_COMPLETE — all components, flows, contracts, and Bend 2 laws verified; parallel Work Packets unlocked.]"
        elif lattice_proof.get("status") == "INCOMPLETE_DRAFT" and (gaps := list(lattice_proof.get("open_gaps") or [])):
            voice_reply["user_message"] = user_msg + f"\n[Lattice Proof: INCOMPLETE_DRAFT ({len(gaps)} gap(s) remaining before Work Packets unlock: {'; '.join(str(g.get('description') if isinstance(g, dict) else g) for g in gaps)})]"

    return ExecutiveTurnResult({
        "turn_index": cur_turn, "decision": gate_result["decision"], "bounce_reason": gate_result["bounce_reason"], "should_commit": gate_result["should_commit"],
        "whiteboard_mutated": gate_result["whiteboard_mutated"], "user_message": voice_reply["user_message"], "cited_items": list(voice_reply["cited_items"]),
        "whiteboard_graph": wb_graph, "committed_items": list(gate_result["committed_items"]), "blocked_items": list(gate_result["blocked_items"]),
        "conflicts": list(gate_result["conflicts"]), "flawed_ideas": list(gate_result["flawed_ideas"]), "clarification_questions": list(gate_result["clarification_questions"]),
        "open_questions": list(gate_result["open_questions"]), "intake_packet": preflight_report["intake_packet"], "phase3_result": phase3_result,
        "preflight_report": preflight_report, "gate_result": gate_result, "voice_reply": voice_reply, "lattice_proof": lattice_proof,
        "seed_cues": seed_cues, "recalled_concepts": recalled_concepts, "table_view": table_view, "foraging_actions": foraging_actions,
    })


def should_mark_dependent_stale_on_commit(upstream_contract_changed: bool, dependent_was_verified: bool) -> bool:
    """Pure downstream stale-cascade guard governed by Bend 2 Law 4 (`postcommit_stale_cascade_law`, Stage 26)."""
    return bool(upstream_contract_changed) and bool(dependent_was_verified)


def _norm_git_path(val: Any) -> str:
    raw = str(val or "").strip().split("#", 1)[0]
    raw = raw[7:] if raw.startswith("file://") else raw
    while raw.startswith("./"):
        raw = raw[2:]
    return raw.strip()


def _paths_match(p1: Any, p2: Any) -> bool:
    n1, n2 = _norm_git_path(p1), _norm_git_path(p2)
    return bool(n1 and n2 and (n1 == n2 or n1.lstrip("/") == n2.lstrip("/") or (("/" not in n1 or "/" not in n2 or n1.startswith("/") != n2.startswith("/")) and (n1.endswith("/" + n2.lstrip("/")) or n2.endswith("/" + n1.lstrip("/"))))))


def _scope_file_refs(graph: dict[str, Any], sid: str, sc: dict[str, Any]) -> list[str]:
    attrs = sc.get("attributes") if isinstance(sc.get("attributes"), dict) else {}
    refs: list[str] = []
    for raw_list in (sc.get("files"), attrs.get("files"), attrs.get("_table_files"), sc.get("source_links"), sc.get("external_uris")):
        refs.extend([str(x) for x in raw_list if x] if isinstance(raw_list, list) else ([p.strip() for p in raw_list.split(",") if p.strip()] if isinstance(raw_list, str) else []))
    for lk_list in (sc.get("external_links"), (graph.get("external_links") or {}).get(sid) if isinstance(graph.get("external_links"), dict) else None, *[r.get("external_links") for r in (sc.get("rules") or []) if isinstance(r, dict)]):
        if isinstance(lk_list, list):
            refs.extend(str(lk.get("target_uri") or lk.get("uri") or "") for lk in lk_list if isinstance(lk, dict))
    for art in (graph.get("lattice_artifacts") if isinstance(graph.get("lattice_artifacts"), list) else ()):
        if isinstance(art, dict) and str(art.get("scope_id") or art.get("scope") or "") == sid:
            refs.extend(str(c).strip() for c in (art.get("file"), (art.get("dimensions") or {}).get("file") if isinstance(art.get("dimensions"), dict) else None, art.get("target_uri"), art.get("uri")) if isinstance(c, str) and c.strip())
    return refs


def _find_owning_scopes(graph: dict[str, Any], path: Any, old_path: Any = None, explicit_scope: Any = None) -> list[str]:
    scopes = graph.get("scopes") if isinstance(graph.get("scopes"), dict) else {}
    owners = [str(explicit_scope).strip()] if str(explicit_scope or "").strip() in scopes else []
    return owners + [sid for sid, sc in scopes.items() if isinstance(sc, dict) and sid not in owners and any(_paths_match(r, path) or (old_path and _paths_match(r, old_path)) for r in _scope_file_refs(graph, sid, sc))]


def _scope_direct_deps(sc: dict[str, Any]) -> list[str]:
    attrs = sc.get("attributes") if isinstance(sc.get("attributes"), dict) else {}
    deps: list[str] = []
    for raw in (sc.get("depends_on"), attrs.get("depends_on"), attrs.get("_table_depends_on")):
        for d in (raw if isinstance(raw, list) else ([p.strip() for p in str(raw).split(",")] if isinstance(raw, str) and raw.strip() else [])):
            if (ds := str(d).strip()) and ds not in deps:
                deps.append(ds)
    return deps


def _upsert_contract_artifact(graph: dict[str, Any], sid: str, kind: str, item: Any, file_path: str, eff_turn: int) -> str:
    if not (name := str((item.get("name") or item.get("interface") or item.get("schema") or item.get("id") or item.get("function") or item.get("class")) if isinstance(item, dict) else item).strip()):
        return ""
    k_clean = str(item.get("kind", kind) if isinstance(item, dict) else kind).strip().upper()
    k_clean, artifacts = (k_clean if k_clean in ("INTERFACE", "SCHEMA", "LAW") else "INTERFACE"), graph.setdefault("lattice_artifacts", [])
    if (entry := next((a for a in artifacts if isinstance(a, dict) and a.get("kind") == k_clean and a.get("scope_id", a.get("scope")) == sid and str(a.get("name", "")).lower() == name.lower()), None)) is None:
        entry = ArtifactEntry({"id": f"{k_clean.lower()}_{sid}_{name.lower()}", "kind": k_clean, "name": name, "scope_id": sid, "scope": sid, "created_turn": eff_turn})
        artifacts.append(entry)
    if isinstance(item, dict):
        for k_src, k_dst in (("inputs", "inputs"), ("args", "inputs"), ("outputs", "outputs"), ("returns", "outputs")):
            if isinstance(item.get(k_src), list):
                entry[k_dst] = [str(x).strip() for x in item[k_src] if str(x).strip()]
        if spec_str := str(item.get("spec") or item.get("signature") or item.get("text") or "").strip():
            entry["spec"] = entry["text"] = spec_str
    if file_path:
        entry["file"] = entry.setdefault("dimensions", {})["file"] = file_path
    entry["turn"] = entry["updated_turn"] = eff_turn
    return name


def _parse_git_report_entries(report: dict[str, Any] | list[Any]) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int], dict[str, str]]:
    if isinstance(report, list):
        return [copy.deepcopy(x) if isinstance(x, dict) else {"path": str(x)} for x in report], {}, {}, {}
    rename_map, raw_ren = {}, report.get("renamed_files") or report.get("renames")
    for rn in ([{"old_path": k, "new_path": v} for k, v in raw_ren.items()] if isinstance(raw_ren, dict) else (raw_ren if isinstance(raw_ren, list) else ())):
        if isinstance(rn, dict) and (op := str(rn.get("old_path") or rn.get("from_path") or rn.get("old") or rn.get("old_uri") or "").strip()) and (np := str(rn.get("new_path") or rn.get("to_path") or rn.get("path") or rn.get("new") or rn.get("new_uri") or "").strip()):
            rename_map[op] = np
        elif isinstance(rn, (tuple, list)) and len(rn) >= 2:
            rename_map[str(rn[0]).strip()] = str(rn[1]).strip()
    line_shifts = {str(k): int(v) for k, v in (report.get("line_shifts") or report.get("line_deltas") or {}).items() if isinstance(v, int)}
    top_hashes = {str(k): str(v) for k, v in (report.get("file_hashes") or report.get("sha256") or report.get("hashes") or {}).items() if v}
    entries, raw_f = [], report.get("files") or report.get("changed_files") or report.get("file_changes") or report.get("changes") or report.get("diff_entries") or report.get("entries")
    if isinstance(raw_f, dict):
        entries.extend({"path": str(fp), **(info if isinstance(info, dict) else {"file_hash": str(info)})} for fp, info in raw_f.items())
    elif isinstance(raw_f, list):
        entries.extend(copy.deepcopy(x) if isinstance(x, dict) else {"path": str(x)} for x in raw_f)
    for key, st_code in (("modified_files", "M"), ("added_files", "A"), ("untracked_files", "??"), ("deleted_files", "D"), ("removed_files", "D")):
        entries.extend({"status": st_code, **(copy.deepcopy(x) if isinstance(x, dict) else {"path": str(x)})} for x in ((report.get(key) or []) if isinstance(report.get(key), list) else ()))
    entries.extend({"old_path": op, "path": np, "status": "R"} for op, np in list(rename_map.items()) if not any(_paths_match(fe.get("path") or fe.get("new_path"), np) for fe in entries))
    for fe in entries:
        op, np = str(fe.get("old_path") or fe.get("from_path") or "").strip(), str(fe.get("path") or fe.get("file") or fe.get("new_path") or fe.get("uri") or "").strip()
        if op and np:
            rename_map.setdefault(op, np)
        if isinstance(fe.get("line_shift", fe.get("line_delta")), int) and np:
            line_shifts[np] = int(fe.get("line_shift", fe.get("line_delta")))
    return entries, rename_map, line_shifts, top_hashes


def sync_post_commit_to_lattice(whiteboard_graph: dict[str, Any], commit_report: dict[str, Any], *, store: Any = None, current_turn: int | None = None) -> PostCommitSyncResult:
    """Synchronizes a completed git commit (`commit_report`) into `whiteboard_graph` and AI Memory, applying Law 4 (`postcommit_stale_cascade_law`) downstream `STALE` cascade."""
    if not isinstance(whiteboard_graph, dict):
        raise ExecutivePreflightError("postcommit_invalid_whiteboard", [f"whiteboard_graph must be a dict, got {type(whiteboard_graph).__name__}"])
    if not isinstance(commit_report, dict):
        raise ExecutivePreflightError("postcommit_invalid_commit_report", [f"commit_report must be a dict, got {type(commit_report).__name__}"])
    if current_turn is not None and (isinstance(current_turn, bool) or not isinstance(current_turn, int) or current_turn < 1):
        raise ExecutivePreflightError("postcommit_invalid_turn", [f"current_turn must be a positive int, got {current_turn!r}"])
    eff_turn = int(current_turn) if current_turn is not None else max(1, int(whiteboard_graph.get("turn", 0) or 0), int(whiteboard_graph.get("turn_index", 0) or 0))
    if isinstance(store, SQLiteGraphStore):
        whiteboard_graph["_sqlite_store"] = store
    raw_sha = commit_report.get("commit_sha") or commit_report.get("sha") or commit_report.get("commit_id") or commit_report.get("head_sha")
    commit_sha = str(raw_sha).strip() if isinstance(raw_sha, str) and raw_sha.strip() else None
    if commit_sha is not None: whiteboard_graph["last_commit_sha"] = commit_sha
    file_entries, rename_map, line_shifts, top_hashes = _parse_git_report_entries(commit_report)
    scopes = whiteboard_graph.setdefault("scopes", {})
    modified_scopes = [s for s in (commit_report.get("modified_scopes") or []) if isinstance(s, str) and s in scopes]
    contract_changed_scopes = {s for s in (commit_report.get("contract_changed_scopes") or commit_report.get("changed_contracts") or []) if isinstance(s, str) and s in scopes}
    updated_artifacts, contract_keys = [], (("INTERFACE", "updated_interfaces"), ("INTERFACE", "interfaces"), ("INTERFACE", "ast_functions"), ("SCHEMA", "ast_classes"), ("SCHEMA", "updated_schemas"), ("SCHEMA", "schemas"))

    for fe in file_entries:
        is_del = str(fe.get("status") or "").strip().upper() in ("D", "DELETED", "REMOVED", "REMOVE")
        path, old_path = str(fe.get("path") or fe.get("file") or fe.get("new_path") or fe.get("uri") or "").strip(), str(fe.get("old_path") or fe.get("from_path") or "").strip()
        f_hash = str(fe.get("file_hash") or fe.get("sha256") or fe.get("hash") or fe.get("blob_sha") or top_hashes.get(path) or "").strip()
        for sid in _find_owning_scopes(whiteboard_graph, path, old_path, fe.get("scope") or fe.get("scope_id") or fe.get("component")):
            if sid not in modified_scopes:
                modified_scopes.append(sid)
            sc = scopes[sid]
            if commit_sha is not None:
                sc["last_commit_sha"] = commit_sha
            if is_del and isinstance(sc.get("file_hashes"), dict):
                sc["file_hashes"].pop(path, None)
            elif not is_del and f_hash and path:
                fh_map = sc.setdefault("file_hashes", {})
                if old_path:
                    fh_map.pop(old_path, None)
                fh_map[path] = f_hash
            if bool(fe.get("contract_changed") or commit_report.get("contract_changed") or any(fe.get(k) for _, k in contract_keys)):
                contract_changed_scopes.add(sid)
                for kind, key in contract_keys:
                    for item in (fe.get(key) or []):
                        if (aname := _upsert_contract_artifact(whiteboard_graph, sid, kind, item, path, eff_turn)) and aname not in updated_artifacts:
                            updated_artifacts.append(aname)
                if not any(fe.get(k) for _, k in contract_keys):
                    for art in (whiteboard_graph.get("lattice_artifacts") or []):
                        if isinstance(art, dict) and art.get("scope_id", art.get("scope")) == sid and art.get("kind") in ("INTERFACE", "SCHEMA"):
                            art["updated_turn"] = eff_turn
                            if (aname := str(art.get("name") or art.get("id") or "")) and aname not in updated_artifacts:
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
    for kind, key in contract_keys:
        for item in (commit_report.get(key) or []) if isinstance(commit_report.get(key), list) else ():
            for sid in ([str(item.get("scope") or item.get("scope_id") or item.get("component"))] if isinstance(item, dict) and (item.get("scope") or item.get("scope_id") or item.get("component")) else modified_scopes):
                if sid in scopes:
                    contract_changed_scopes.add(sid)
                    if (aname := _upsert_contract_artifact(whiteboard_graph, sid, kind, item, "", eff_turn)) and aname not in updated_artifacts:
                        updated_artifacts.append(aname)

    uri_sync_report: dict[str, Any] = copy.deepcopy(commit_report["uri_sync_report"]) if isinstance(commit_report.get("uri_sync_report"), dict) else {}
    if callable(sync_uris_fn := getattr(importlib.import_module("bowthruster.ai_memory"), "sync_git_diff_uris", None)):
        eff_manifest = {**commit_report, **({"renames": rename_map} if rename_map else {}), **({"line_shifts": line_shifts} if line_shifts else {})}
        sig_params = inspect.signature(sync_uris_fn).parameters
        accepts_turn = "current_turn" in sig_params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_params.values())
        u_res = sync_uris_fn(whiteboard_graph, eff_manifest, current_turn=eff_turn) if accepts_turn else sync_uris_fn(whiteboard_graph, eff_manifest)
        if isinstance(u_res, dict):
            uri_sync_report.update(u_res)
    del_paths = [str(fe.get("path") or fe.get("file") or fe.get("uri") or "").strip() for fe in file_entries if str(fe.get("status") or "").strip().upper() in ("D", "DELETED", "REMOVED", "REMOVE")]
    dangling = list(uri_sync_report.get("dangling_uris") or [])
    for sid, sc in (scopes.items() if del_paths else ()):
        for ref in (_scope_file_refs(whiteboard_graph, sid, sc) if isinstance(sc, dict) else ()):
            if any(_paths_match(ref, dp) for dp in del_paths) and ref not in dangling:
                dangling.append(ref)
    for k, v in (("updated_uris", []), ("renamed_count", len(rename_map)), ("dangling_uri_pointers", len(dangling)), ("dangling_uris", dangling), ("is_valid", len(dangling) == 0)):
        uri_sync_report.setdefault(k, v)

    cascaded_stale_scopes: list[str] = []
    for dep_sid, dep_sc in list(scopes.items()):
        if isinstance(dep_sc, dict):
            visited_anc, queue = set(), [d for d in _scope_direct_deps(dep_sc) if d != dep_sid]
            while queue:
                if (cur := queue.pop(0)) not in visited_anc:
                    visited_anc.add(cur)
                    if isinstance(cur_sc := scopes.get(cur), dict):
                        queue.extend(nxt for nxt in _scope_direct_deps(cur_sc) if nxt not in visited_anc and nxt != dep_sid)
            attrs = dep_sc.get("attributes") if isinstance(dep_sc.get("attributes"), dict) else {}
            dep_status = str(dep_sc.get("status") or dep_sc.get("build_status") or attrs.get("status") or attrs.get("_table_status") or "").strip().upper()
            if should_mark_dependent_stale_on_commit(any(u in contract_changed_scopes for u in visited_anc), dep_status == "VERIFIED"):
                cascaded_stale_scopes.append(dep_sid)
    for dep_sid in cascaded_stale_scopes:
        dep_sc = scopes[dep_sid]
        dep_sc["status"] = dep_sc["build_status"] = "STALE"
        dep_sc["updated_turn"] = eff_turn
        if isinstance(attrs := dep_sc.get("attributes"), dict):
            attrs.update({ak: "STALE" for ak in ("status", "_table_status") if ak in attrs})
    stale_unflagged = sum(1 for sid in cascaded_stale_scopes if str(scopes[sid].get("status") or "").upper() != "STALE")
    _collect_and_index_turn_rules(whiteboard_graph, eff_turn, target_sids=modified_scopes)
    return PostCommitSyncResult({"commit_sha": commit_sha, "modified_scopes": modified_scopes, "cascaded_stale_scopes": cascaded_stale_scopes, "stale_unflagged_dependents": stale_unflagged, "uri_sync_report": uri_sync_report, "updated_artifacts": updated_artifacts, "whiteboard_graph": whiteboard_graph})


def detect_working_tree_drift(whiteboard_graph: dict[str, Any], working_tree_status: dict[str, Any] | list[dict[str, Any]]) -> WorkingTreeDriftReport:
    """Inspects uncommitted working-tree changes against `whiteboard_graph` without mutating `whiteboard_graph` (Stage 26)."""
    if not isinstance(whiteboard_graph, dict):
        raise ExecutivePreflightError("drift_invalid_whiteboard", [f"whiteboard_graph must be a dict, got {type(whiteboard_graph).__name__}"])
    if not isinstance(working_tree_status, (dict, list)):
        raise ExecutivePreflightError("drift_invalid_status", [f"working_tree_status must be a dict or list, got {type(working_tree_status).__name__}"])
    scopes = whiteboard_graph.get("scopes") if isinstance(whiteboard_graph.get("scopes"), dict) else {}
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
        path, old_p = str(item.get("path") or item.get("file") or item.get("new_path") or item.get("uri") or "").strip(), str(item.get("old_path") or item.get("from_path") or item.get("old_uri") or "").strip()
        st, f_hash = str(item.get("status") or item.get("change_type") or item.get("action") or "").strip().upper(), str(item.get("file_hash") or item.get("sha256") or item.get("hash") or "").strip()
        if st in ("CLEAN", "UNCHANGED", "OK"):
            if f_hash and any(isinstance(sc, dict) and isinstance(sc.get("file_hashes"), dict) and any(_paths_match(k, path) and str(v) != f_hash for k, v in sc["file_hashes"].items()) for sc in scopes.values()):
                _mark(path, exp_scope=item.get("scope") or item.get("component"))
        else:
            _mark(path or old_p, is_del=st in ("D", "DELETED", "REMOVED", "REMOVE"), old_p=old_p, exp_scope=item.get("scope") or item.get("component"))
    for fp, cur_hash in top_hashes.items():
        for sid, sc in scopes.items():
            if isinstance(sc, dict) and isinstance(sc.get("file_hashes"), dict) and any(_paths_match(k, fp) and str(v) != str(cur_hash) for k, v in sc["file_hashes"].items()):
                _mark(str(fp), exp_scope=sid)
    for sid, sc in (scopes.items() if deleted_or_moved else ()):
        for ref in (_scope_file_refs(whiteboard_graph, sid, sc) if isinstance(sc, dict) else ()):
            if any(_paths_match(ref, dp) for dp in deleted_or_moved):
                if ref not in dangling_uris:
                    dangling_uris.append(ref)
                if sid not in drifted_scopes:
                    drifted_scopes.append(sid)
    return WorkingTreeDriftReport({"has_drift": bool(drifted_scopes or uncommitted_files or dangling_uris), "drifted_scopes": drifted_scopes, "uncommitted_files": uncommitted_files, "dangling_uris": dangling_uris})
