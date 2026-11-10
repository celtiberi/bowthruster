"""Spec 22C §2.2 & Stage 27: Whiteboard Stage 4C Ingestion, Work Packet & Python Skeleton Compiler (`ingest.py`)."""

from __future__ import annotations

import ast
import copy
import keyword
from pathlib import Path
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore, evict_from_ring
from bowthruster.regiment.intake_packet import compile_intake_packet, compile_markdown_turn
from bowthruster.whiteboard.frustration import classify_lattice_proof_status, detect_frustration, evaluate_lattice_proof
from bowthruster.whiteboard.mutations import (
    ensure_scope_node, transition_component_build_status, two_axis_mitosis, upsert_ambient_note)
from bowthruster.whiteboard.schema import (BUILD_STATUSES, DEFAULT_K_CAP, DEFAULT_MAX_RING, normalize_board_graph,
    resolve_packet_lifecycle_status, step_component_lifecycle_on_edit, sync_to_sqlite, validate_context_graph)
from .linker import (IngestionResult, IngestionStatus, Phase4InvariantError, VALID_INGESTION_STATUSES,
    _resolve_known_component_scope, _resolve_or_register_scope, _slugify_scope_id,
    classify_and_apply_delta, create_empty_context_graph, fuse_table_attributes)
from .read_engine import annotate_active_tasks, expand_node, propagate_graph, scan_graph, slice_lattice

_EMPTY_TOKENS = frozenset({"none", "-", "—", "n/a", "null"})

def resolve_ingestion_status(
    intake_packet: dict[str, Any], open_blockers: list[dict[str, Any]], conflicts: list[dict[str, Any]],
) -> IngestionStatus:
    """Explicit state machine transition for Stage 4C ingestion status (Spec 22C §2.2, Law 6)."""
    has_clarification = intake_packet.get("grounding_status") == IngestionStatus.NEEDS_CLARIFICATION.value or any(
        b.get("type") == "CLARIFICATION" and b.get("status") != "RESOLVED"
        for b in open_blockers)
    if has_clarification:
        return IngestionStatus.NEEDS_CLARIFICATION
    return IngestionStatus.CONFLICT if conflicts else IngestionStatus.COMMITTED

def check_phase4_invariants(result: dict[str, Any], *, k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING,
    ) -> list[str]:
    """Verifies all Spec 22A–22C post-ingestion invariants on an `IngestionResult` or `WhiteboardGraph` dict."""
    if not isinstance(result, dict):
        return [f"result must be a dict, got {type(result).__name__}"]
    is_envelope = "graph" in result and isinstance(result["graph"], dict)
    graph = result["graph"] if is_envelope else result
    errors = validate_context_graph(graph, k_cap=k_cap, max_ring=max_ring)
    if is_envelope:
        status = result.get("status")
        if status not in VALID_INGESTION_STATUSES:
            errors.append(f"invalid status={status!r}, expected one of {sorted(VALID_INGESTION_STATUSES)}")
        if isinstance(result.get("max_rules_per_scope"), int) and result["max_rules_per_scope"] > k_cap:
            errors.append(f"max_rules_per_scope={result['max_rules_per_scope']} exceeds k_cap={k_cap}")
        if isinstance(result.get("ring_size"), int) and result["ring_size"] > max_ring:
            errors.append(f"ring_size={result['ring_size']} exceeds max_ring={max_ring}")
        if result.get("conflicts") and status == IngestionStatus.COMMITTED.value:
            errors.append("result has active conflicts but status is 'COMMITTED'")
        has_open_clarify = any(
            b.get("type") == "CLARIFICATION" and b.get("status") != "RESOLVED"
            for b in graph.get("open_blockers", []))
        if has_open_clarify and status == IngestionStatus.COMMITTED.value:
            errors.append("result has unresolved CLARIFICATION blockers but status is 'COMMITTED'")
    return errors

def ingest_intake_packet(graph: dict[str, Any] | None, intake_packet: dict[str, Any], *, k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING, store: SQLiteGraphStore | None = None, db_path: str | Path | None = None,
    turn_index: int | None = None, source_turn: int | None = None) -> IngestionResult:
    """Executes Stage 4A -> Stage 4B -> Stage 4C Whiteboard Ingestion (Spec 22C §2.2)."""
    if graph is None:
        graph = create_empty_context_graph(k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)
    elif not isinstance(graph, dict):
        raise Phase4InvariantError("phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"])
    elif store is not None or db_path is not None:
        active_store = store if store is not None else SQLiteGraphStore(db_path)
        try:
            object.__setattr__(graph, "_sqlite_store", active_store)
        except (AttributeError, TypeError):
            graph["_sqlite_store"] = active_store
    if not isinstance(intake_packet, dict):
        msg = f"intake_packet must be a dict, got {type(intake_packet).__name__}"
        raise Phase4InvariantError("phase4_invalid_intake_packet", [msg])
    normalize_board_graph(graph)
    prev_turn = max(int(graph.get("turn", 0)), int(graph.get("turn_index", 0)))
    override_turn = turn_index if turn_index is not None else source_turn
    raw_turn = override_turn if override_turn is not None else intake_packet.get("turn_index", max(1, prev_turn + 1))
    if isinstance(raw_turn, bool) or not isinstance(raw_turn, int):
        raise Phase4InvariantError("phase4_invalid_intake_packet", [f"turn_index must be an int, got {raw_turn!r}"])
    eff_turn = int(raw_turn)
    graph["turn_index"] = graph["turn"] = eff_turn
    doc_tables = intake_packet.get("doc_tables", intake_packet.get("tables", []))
    fused_scopes = fuse_table_attributes(graph, doc_tables if isinstance(doc_tables, list) else [], turn_index=eff_turn)
    parsed_mcus = intake_packet.get("parsed_mcus", intake_packet.get("items", intake_packet.get("mcus", [])))
    if not isinstance(parsed_mcus, list):
        raise Phase4InvariantError("phase4_invalid_intake_packet", ["intake_packet['parsed_mcus'] must be a list"])
    for pmcu in parsed_mcus:
        if isinstance(pmcu, dict):
            raw_primary = str(pmcu.get("primary_scope") or "").strip()
            pmcu_text = str(pmcu.get("text", pmcu.get("canonical_text", pmcu.get("raw_text", ""))) or "").strip()
            lp = (pmcu.get("lattice_payload") or {})
            op_kind = str(lp.get("op") or "").removeprefix("UPSERT_")
            lkind = str(
                pmcu.get("lattice_kind") if pmcu.get("lattice_kind") else (lp.get("kind") or op_kind)
            ).strip().upper()
            if raw_primary and lkind not in ("SCHEMA", "INTERFACE", "LAW"):
                _resolve_known_component_scope(graph, raw_primary, pmcu_text, pmcu)
    delta_log: list[dict[str, Any]] = []
    for pmcu in parsed_mcus:
        if not isinstance(pmcu, dict):
            continue
        d_res = classify_and_apply_delta(graph, pmcu, turn_index=eff_turn)
        delta_log.append(d_res)
        lp = (pmcu.get("lattice_payload") or {})
        raw_tests = pmcu.get("tests") if pmcu.get("tests") is not None else lp.get("tests")
        if raw_tests is not None and (target_sid := d_res.get("scope", pmcu.get("primary_scope"))):
            snode = graph.get("scopes", {}).get(str(target_sid))
            if bool(snode):
                snode["tests"] = list(raw_tests) if isinstance(raw_tests, list) else [str(raw_tests)]
        if (pmcu.get("primary_scope") in (None, "", "global_defaults")
            and str(pmcu.get("type") or "").upper() in ("FACT", "GOAL", "PROPOSAL")
            and pmcu.get("target_prop") is None):
            note_payload = {"id": f"ambient_t{eff_turn}_m{pmcu.get('mcu_id', len(delta_log))}",
                "text": str(pmcu.get("text", pmcu.get("canonical_text", "")) or ""), "turn": eff_turn}
            upsert_ambient_note(graph, note_payload, current_turn=eff_turn, max_ring=max_ring)
    for cq in intake_packet.get("clarification_questions", []):
        if isinstance(cq, str) and cq.strip():
            bid = f"clarify_pkt_t{eff_turn}_{len(graph.setdefault('open_blockers', [])) + 1}"
            if not any(b.get("text") == cq.strip() for b in graph["open_blockers"]):
                graph["open_blockers"].append({"id": bid, "type": "CLARIFICATION", "scope": "global_defaults",
                    "text": cq.strip(), "rule_ids": []})
    mitosis_splits = list(two_axis_mitosis(graph, k_cap=k_cap).get("splits", []))
    graph["ambient_notes"] = evict_from_ring(list(graph.get("ambient_notes", [])), eff_turn, max_ring=max_ring)
    conflicts = detect_frustration(graph)
    open_blockers = graph.setdefault("open_blockers", [])
    active_conflict_ids = {c["id"] for c in conflicts}
    open_blockers[:] = [b for b in open_blockers
        if not (b.get("type") == "CONFLICT" and b.get("id") not in active_conflict_ids)]
    for c in reversed(conflicts):
        if not any(b.get("id") == c["id"] for b in open_blockers):
            open_blockers.insert(0, copy.deepcopy(c))
    annotate_active_tasks(graph, conflicts)
    status = resolve_ingestion_status(intake_packet, open_blockers, conflicts).value
    max_rules_per_scope = max(
        (len(s.get("rules", [])) for s in graph.get("scopes", {}).values()), default=0)
    ring_size = len(graph.get("ambient_notes", []))
    lattice_proof = evaluate_lattice_proof(graph)
    graph["lattice_proof"] = lattice_proof
    res = IngestionResult({"graph": graph, "status": status, "delta_log": delta_log, "conflicts": conflicts,
        "mitosis_splits": mitosis_splits, "ring_size": ring_size,
        "max_rules_per_scope": max_rules_per_scope, "fused_scopes": fused_scopes,
        "turn_index": eff_turn, "lattice_proof": lattice_proof})
    if inv_errors := check_phase4_invariants(res, k_cap=k_cap, max_ring=max_ring):
        raise Phase4InvariantError("phase4_invariant_violation", inv_errors)
    sync_to_sqlite(graph)
    return res

from bowthruster.whiteboard import (
    _bend_bool_to_py,
    _emit_interface_skeleton_lines,
    _extract_item_names,
    _extract_law_stub_spec,
    _fmt_spec_doc,
    _parse_bend_law_args,
    _py_ident,
    _split_top_comma,
)

def compile_laws_bend(scope_id: str, law_records: list[dict[str, Any]], *, module_alias: str = "MOD") -> str:
    """Compiles `% LAW` records for `scope_id` into a standalone Bend 2 `LAWS.bend` file (Spec 22C §2.2)."""
    sid = str(scope_id or "global_defaults").strip() or "global_defaults"
    alias = str(module_alias or "MOD").strip() or "MOD"
    lines: list[str] = [
        f"# LAWS.bend — Compiled by Whiteboard from % LAW records for {sid}",
        "", "import Base", f"import ./main.bend as {alias}",
    ]
    seen_names: set[str] = set()
    for law in law_records if isinstance(law_records, list) else []:
        if not isinstance(law, dict):
            continue
        raw_name = str(law.get("name") or "").strip()
        lname = _slugify_scope_id(raw_name).replace("/", "_").replace(".", "_")
        if not lname or lname in seen_names:
            continue
        seen_names.add(lname)
        dims = (law.get("dimensions") or {})
        raw_args = law.get("args") if law.get("args") is not None else dims.get("args")
        raw_lhs = law.get("lhs") if law.get("lhs") is not None else (dims.get("lhs") or "")
        raw_rhs = law.get("rhs") if law.get("rhs") is not None else (dims.get("rhs") or "")
        lhs = str(raw_lhs).strip().strip("\"`'").strip()
        rhs = str(raw_rhs).strip().strip("\"`'").strip()
        arg_specs = _parse_bend_law_args(raw_args)
        lines.extend(("", f"law {lname}:"))
        if arg_specs and lhs and rhs and lhs.lower() not in _EMPTY_TOKENS and rhs.lower() not in _EMPTY_TOKENS:
            lines.extend(f"  for {aspec}" for aspec in arg_specs)
            lines.append(f"  {{{lhs} == {rhs} : Bool}}")
        else:
            lines.extend(("  for +x: Bool", f"  {{{alias}.check_{lname}(x) == x : Bool}}"))
    return "\n".join(lines) + "\n"

def is_python_skeleton_complete(has_schemas: bool, has_interfaces: bool, has_law_stubs: bool) -> bool:
    """Pure completeness guard governed by Bend 2 Law 12 (`python_skeleton_completeness_law`, Stage 27)."""
    return bool(has_schemas) and bool(has_interfaces) and bool(has_law_stubs)

def compile_python_skeleton(scope_id: str, artifacts: list[dict[str, Any]] | None = None, *,
    schemas: list[dict[str, Any]] | None = None, interfaces: list[dict[str, Any]] | None = None,
    laws: list[dict[str, Any]] | None = None, rules: list[dict[str, Any]] | None = None) -> str:
    """Deterministically compiles an AST-parseable Python structural skeleton stub for `scope_id` (Stage 27)."""
    sid = str(scope_id or "global_defaults").strip() or "global_defaults"
    all_arts = [a for a in (artifacts or [])]
    schema_list = [a for a in (schemas if schemas is not None
        else [x for x in all_arts if str(x.get("kind") or "").strip().upper() == "SCHEMA"] )]
    iface_list = [a for a in (interfaces if interfaces is not None
        else [x for x in all_arts if str(x.get("kind") or "").strip().upper() == "INTERFACE"] )
        ]
    law_list = [a for a in (laws if laws is not None
        else [x for x in all_arts if str(x.get("kind") or "").strip().upper() == "LAW"] )]
    lines: list[str] = [
        f"# Python Structural Skeleton — Compiled by Whiteboard for {sid}",
        "from __future__ import annotations", "", "from dataclasses import dataclass", "from typing import Any",
    ]
    seen_consts: set[str] = set()
    skip_props = ("max_file_lines", "import", "depends_on", "value", "rule")
    for r in (rules or []):
        b_val = r.get("bound_val") if r else None
        if str(r.get("type") or "").upper() == "BOUND":
            if isinstance(b_val, (int, float)) and not isinstance(b_val, bool):
                tp = str(r.get("target_prop") or "").strip()
                if tp and tp.isidentifier() and tp.lower() not in skip_props and tp.upper() not in seen_consts:
                    seen_consts.add(tp.upper())
                    lines.append(f"{tp.upper()} = {b_val!r}  # BOUND: {tp} {r.get('bound_op') or '<='} {b_val!r}")
    seen_schemas: set[str] = set()
    for sc_art in schema_list:
        sname = _py_ident(
            sc_art.get("name", sc_art.get("schema", sc_art.get("id"))) or "SchemaStub", default="SchemaStub")
        if sname in seen_schemas:
            continue
        seen_schemas.add(sname)
        dims = (sc_art.get("dimensions") or {})
        sfile = str(next((_v for _v in (sc_art.get("file"), dims.get("file")) if _v), None) or "").strip()
        sc_doc = str(sc_art.get("spec") or "").strip()
        fields = (_extract_item_names(sc_art.get("fields", dims.get("fields"))) or _extract_item_names(
            sc_art.get("inputs", sc_art.get("outputs", dims.get("inputs", dims.get("outputs"))))) or ["value"])
        lines.extend((
            "", *([f"# FILE: {sfile}"] if sfile else []), "@dataclass(frozen=True)",
            f"class {sname}:", *_fmt_spec_doc(sc_doc), *(f"    {f_name}: Any = None" for f_name in fields),
        ))
    iface_names = {
        _py_ident(ia.get("name") or "interface_stub", default="interface_stub")
        for ia in iface_list}
    emitted_laws: list[tuple[str, list[str]]] = []
    seen_laws: set[str] = set()
    for law_art in law_list:
        lfn, lalias, lparams, py_expr = _extract_law_stub_spec(law_art, iface_names | seen_schemas)
        ldims = (law_art.get("dimensions") or {})
        l_doc = str(law_art.get("spec") or "").strip()
        for fn_id in (lfn, lalias):
            if fn_id and fn_id not in seen_laws:
                seen_laws.add(fn_id)
                emitted_laws.append((fn_id, lparams))
                param_sig = ", ".join(f"{p}: bool = True" for p in lparams)
                lines.extend((
                    "", f"def {fn_id}({param_sig}) -> bool:", *_fmt_spec_doc(l_doc), f"    return bool({py_expr})"))
    lines.extend(_emit_interface_skeleton_lines(iface_list, emitted_laws, seen_schemas))
    code = "\n".join(lines) + "\n"
    ast.parse(code)
    return code

def _art_file_path(art: dict[str, Any]) -> str:
    raw = str(art.get("file") or (art.get("dimensions") or {}).get("file") or "")
    return raw.split("#", 1)[0].split(":L", 1)[0].strip()

def _build_slice_packet(graph: dict[str, Any], pkt_id: str, wave: int, kind: str, sid: str,
    sc: dict[str, Any], build_status: str, default_owner: str | None = None) -> dict[str, Any]:
    sl = slice_lattice(graph, component=sid)
    arts = [a for a in sl.get("active_artifacts", [])]
    diag = sc.get("failure_diagnostic")
    diag_clean = str(diag).strip() if isinstance(diag, str) and diag.strip() else None
    prompt_str = (f"{sl['compact_view']}\n! BUILD_FAILURE [{sid}]: {diag_clean}" if diag_clean else sl["compact_view"]
        )
    scope_schemas = [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() == "SCHEMA"]
    scope_ifaces = [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() == "INTERFACE"]
    scope_laws = [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() == "LAW"]
    scope_rules = list(sl.get("active_rules", []))
    compiled_laws = compile_laws_bend(sid, scope_laws)
    py_skeleton = compile_python_skeleton(
        sid, schemas=scope_schemas, interfaces=scope_ifaces, laws=scope_laws, rules=scope_rules)
    art_files = sorted({_art_file_path(a) for a in [*scope_schemas, *scope_ifaces] if _art_file_path(a)})
    by_file = {f: compile_python_skeleton(sid, schemas=[s for s in scope_schemas if _art_file_path(s) in ("", f)],
        interfaces=[i for i in scope_ifaces if _art_file_path(i) == f], laws=scope_laws, rules=scope_rules)
        for f in art_files}
    return {"packet_id": pkt_id, "wave": wave, "kind": kind, "component": sid, "scope": sid,
        "owner": sc.get("owner") or default_owner, "stage": sc.get("stage"),
        "build_status": build_status, "claimed_by": sc.get("claimed_by"), "claimed_at": sc.get("claimed_at"),
        "build_tag": sc.get("build_tag"), "baseline_commit_sha": graph.get("last_commit_sha"),
        "core_tag": sc.get("core_tag"), "failure_diagnostic": diag_clean,
        "depends_on": list(sc.get("depends_on", [])),
        "files": list(sc.get("files", [])), "tests": list(sc.get("tests", [])),
        "schemas": scope_schemas, "laws": scope_laws,
        "compiled_laws_bend": compiled_laws, "laws_skeleton": compiled_laws,
        "python_skeleton": py_skeleton, "python_skeletons_by_file": by_file,
        "is_skeleton_complete": is_python_skeleton_complete(bool(scope_schemas), bool(scope_ifaces), bool(scope_laws)),
        "contracts": [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() in ("SCHEMA", "INTERFACE")],
        "flows": list(sl.get("active_flows", [])), "rules": scope_rules,
        "tasks": list(sl.get("active_tasks", [])), "prompt_slice": prompt_str}

def compile_work_packets(
    graph: dict[str, Any], *, force: bool = False, status_filter: str | None = None, unclaimed_only: bool = False,
) -> dict[str, Any]:
    """Compiles dependency-ordered parallel Work Packets across Wave 0, Wave 1, and Wave 2 (Spec 22C §2.2)."""
    if not isinstance(graph, dict):
        raise Phase4InvariantError("phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"])
    normalize_board_graph(graph)
    proof = evaluate_lattice_proof(graph)
    is_proven = bool(proof.get("is_proven"))
    graph["lattice_proof"] = proof
    scopes = graph["scopes"]
    artifacts = [a for a in graph["lattice_artifacts"]]
    schemas = [copy.deepcopy(a) for a in artifacts if str(a.get("kind") or "").strip().upper() == "SCHEMA"]
    all_ifaces = [copy.deepcopy(a) for a in artifacts if str(a.get("kind") or "").strip().upper() == "INTERFACE"]
    all_laws = [copy.deepcopy(a) for a in artifacts if str(a.get("kind") or "").strip().upper() == "LAW"]
    wave0: list[dict[str, Any]] = []
    if schemas or all_laws:
        w0_slice = slice_lattice(graph, kind="SCHEMA")
        w0_laws_bend = compile_laws_bend("schemas", all_laws)
        w0_py_skel = compile_python_skeleton("schemas", schemas=schemas, interfaces=all_ifaces, laws=all_laws)
        w0_owner = ((schemas[0].get("owner") if schemas else (all_laws[0].get("owner") if all_laws else "architect"))
            or "architect")
        w0_stage = schemas[0].get("stage") if schemas else (all_laws[0].get("stage") if all_laws else None)
        wave0.append({"packet_id": "pkt_wave0_schemas", "wave": 0, "kind": "SCHEMA_CONTRACTS",
            "component": "schemas", "scope": "schemas", "owner": w0_owner, "stage": w0_stage,
            "build_status": resolve_packet_lifecycle_status("DRAFT", is_proven, True),
            "claimed_by": None, "claimed_at": None, "build_tag": None, "core_tag": None, "failure_diagnostic": None,
            "depends_on": [], "schemas": schemas, "contracts": schemas, "laws": all_laws,
            "compiled_laws_bend": w0_laws_bend, "laws_skeleton": w0_laws_bend, "python_skeleton": w0_py_skel,
            "is_skeleton_complete": is_python_skeleton_complete(bool(schemas), bool(all_ifaces), bool(all_laws)),
            "flows": [], "rules": [], "tasks": [], "prompt_slice": w0_slice["compact_view"]})
    sbf_keys = ("role", "owner", "stage", "depends_on", "children", "parent", "files")
    sbf_scopes = [(sid, sc) for sid, sc in scopes.items()
        if sid != "global_defaults" and not str(sid).startswith("global_defaults::")
        and (str(sc.get("kind") or "").strip().upper() == "COMPONENT" or any(sc.get(k) for k in sbf_keys))]
    non_qa_raw, qa_scopes = [], []
    for sid, sc in sbf_scopes:
        owner_low = str(sc.get("owner") or "").strip().lower()
        stage_low = str(sc.get("stage") or "").strip().lower()
        is_qa = sid.lower().startswith(("test_", "tests/", "qa_")) or owner_low in ("qa",
            "q-division") or stage_low == "qa"
        (qa_scopes if is_qa else non_qa_raw).append((sid, sc))
    has_owned_non_qa = any(bool(sc.get("owner")) for _, sc in non_qa_raw)
    w1_candidates = [(sid, sc) for sid, sc in non_qa_raw
        if (bool(sc.get("owner")) if has_owned_non_qa else not bool(sc.get("children"))) ] or list(non_qa_raw)
    w1_candidates.sort(key=lambda item: (len(item[1].get("depends_on") or []), item[0]))
    owned_non_qa_set = {sid for sid, _ in w1_candidates}
    mutable_statuses = (None, "", "DRAFT", "BLOCKED_BY_DEP", "READY")
    wave1: list[dict[str, Any]] = []
    for sid, sc in w1_candidates:
        all_deps_verified = all(scopes.get(d, {}).get("status") == "VERIFIED"
            for d in sc.get("depends_on", []) if d in owned_non_qa_set
            and any(scopes.get(d, {}).get(k) for k in ("rules", "role", "owner"))
            )
        eff_status = resolve_packet_lifecycle_status(sc.get("status"), is_proven, all_deps_verified)
        sc["status"] = sc["build_status"] = eff_status if sc.get("status") in mutable_statuses else (
            sc.get("status") or eff_status)
        wave1.append(_build_slice_packet(graph, f"pkt_wave1_{sid}", 1, "COMPONENT_SLICE", sid, sc, eff_status))
    wave2: list[dict[str, Any]] = []
    for sid, sc in qa_scopes:
        target_deps = [d for d in sc.get("depends_on", []) if d in owned_non_qa_set] or list(owned_non_qa_set)
        deps_ok = all(scopes.get(d, {}).get("status") == "VERIFIED" for d in target_deps)
        eff_status = resolve_packet_lifecycle_status(sc.get("status"), is_proven, deps_ok)
        sc["status"] = sc["build_status"] = eff_status if sc.get("status") in mutable_statuses else (
            sc.get("status") or eff_status)
        wave2.append(
            _build_slice_packet(graph, f"pkt_wave2_{sid}", 2, "QA_VERIFICATION", sid, sc, eff_status,
                default_owner="qa")
        )
    if not all(sc.get("status") == sc.get("build_status") for _, sc in [*w1_candidates, *qa_scopes]):
        raise Phase4InvariantError("phase4_status_sync_violation", ["status != build_status after packet compilation"])
    if isinstance(status_filter, str) and status_filter.strip():
        sf = status_filter.strip().upper()
        wave0, wave1, wave2 = ([p for p in w if p.get("build_status") == sf] for w in (wave0, wave1, wave2))
    if unclaimed_only:
        wave0, wave1, wave2 = ([p for p in w if not p.get("claimed_by")] for w in (wave0, wave1, wave2))
    emit = is_proven or bool(force)
    sync_to_sqlite(graph)
    return {"status": proof["status"], "is_proven": is_proven, "ready_to_emit": is_proven,
        "open_gaps": list(proof.get("open_gaps", [])), "hard_violations": list(proof.get("hard_violations", [])),
        "lattice_proof": proof, "packets": (wave0 + wave1 + wave2) if emit else [],
        "waves": {"wave_0_schemas": wave0 if emit else [], "wave_1_components": wave1 if emit else [],
        "wave_2_qa": wave2 if emit else []}}

ingest_into_context_graph = ingest_into_whiteboard = run_phase4 = phase4 = ingest_intake_packet

def query_context_graph(
    graph: dict[str, Any], query: str = "", *, mode: str = "propagate", node_id: str | None = None,
    temperature: float | None = None, token_budget: int = 400, max_lines: int = 35) -> dict[str, Any]:
    """Queries `graph` in `'propagate'`, `'scan'`, or `'expand'` mode via the Whiteboard read engine."""
    if not isinstance(graph, dict):
        raise Phase4InvariantError("phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"])
    if mode not in ("propagate", "scan", "expand"):
        raise ValueError(f"invalid query mode={mode!r}, expected 'propagate', 'scan', or 'expand'")
    if mode == "expand":
        target = node_id if (isinstance(node_id, str) and node_id.strip()) else str(query or "").strip()
        return expand_node(graph, target, query=str(query or ""), token_budget=token_budget, max_lines=max_lines)
    if mode == "scan" or not isinstance(query, str) or not query.strip():
        q_str = str(query or "") if mode == "scan" else ""
        return scan_graph(graph, q_str, temperature=temperature, token_budget=token_budget, max_lines=max_lines)
    return propagate_graph(graph, query, temperature=temperature, token_budget=token_budget, max_lines=max_lines)

query_whiteboard = query_context_graph

def run_turn(graph: dict[str, Any] | None, raw_text: str = "", gen: Any = None, jev: Any = None, limits: Any = None,
    *, turn_index: int | None = None, query: str | None = None, **options: Any) -> IngestionResult:
    """Executes a full 3-Entity turn (Intake -> Whiteboard Ingestion -> Readout) (Spec 22C §2.2)."""
    _ = (gen, jev, limits)
    k_cap = int(options.get("k_cap", DEFAULT_K_CAP))
    max_ring = int(options.get("max_ring", DEFAULT_MAX_RING))
    token_budget = int(options.get("token_budget", 400))
    max_lines = int(options.get("max_lines", 35))
    phase3_result = options.get("phase3_result")
    intake_packet = options.get("intake_packet")
    store = options.get("store")
    db_path = options.get("db_path")
    if graph is None:
        graph = create_empty_context_graph(k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)
    elif not isinstance(graph, dict):
        raise Phase4InvariantError("phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"])
    prev_turn = max(int(graph.get("turn", 0)), int(graph.get("turn_index", 0)))
    if turn_index is not None and (isinstance(turn_index, bool) or not isinstance(turn_index, int)):
        raise Phase4InvariantError("phase4_invalid_intake_packet", [f"turn_index must be an int, got {turn_index!r}"])
    eff_turn = int(turn_index) if turn_index is not None else max(1, prev_turn + 1)
    if intake_packet is not None:
        if not isinstance(intake_packet, dict):
            msg = f"intake_packet must be a dict, got {type(intake_packet).__name__}"
            raise Phase4InvariantError("phase4_invalid_intake_packet", [msg])
        packet, p3 = dict(intake_packet, turn_index=eff_turn), phase3_result
    elif phase3_result is not None:
        p3, packet = phase3_result, compile_intake_packet(phase3_result, turn_index=eff_turn)
    else:
        if not isinstance(raw_text, str):
            raise Phase4InvariantError("phase4_invalid_raw_text",
                [f"raw_text must be a str, got {type(raw_text).__name__}"])
        packet = compile_markdown_turn(raw_text, turn_index=eff_turn)
        p3 = {"raw_text": raw_text, "doc_tables": packet["doc_tables"],
            "structured_table_rows": packet["structured_table_rows"],
            "parsed_mcus": packet["parsed_mcus"], "intake_packet": packet,
            "grounding_status": "GROUNDED", "is_locked": True, "clarification_questions": [], "open_questions": []}
    res = ingest_intake_packet(graph, packet, k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)
    eff_query = query if query is not None else (raw_text if isinstance(raw_text, str) else "")
    res["intake_packet"], res["phase3_result"] = packet, p3
    res["scan_view"] = query_context_graph(
        res["graph"], eff_query, mode="scan", token_budget=token_budget, max_lines=max_lines)
    res["readout"] = query_context_graph(
        res["graph"], eff_query, mode="propagate", token_budget=token_budget, max_lines=max_lines)
    return res
