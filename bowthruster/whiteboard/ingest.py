"""Spec 22C §2.2 & Stage 27: Whiteboard Stage 4C Ingestion & Python Structural Skeleton Compiler."""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore, evict_from_ring
from bowthruster.whiteboard.frustration import (
    classify_lattice_proof_status,
    detect_frustration,
    evaluate_lattice_proof,
)
from bowthruster.whiteboard.mutations import (
    ensure_scope_node,
    transition_component_build_status,
    two_axis_mitosis,
    upsert_ambient_note,
)
from bowthruster.whiteboard.schema import (
    BUILD_STATUSES,
    DEFAULT_K_CAP,
    DEFAULT_MAX_RING,
    normalize_board_graph,
    resolve_packet_lifecycle_status,
    step_component_lifecycle_on_edit,
    sync_to_sqlite,
    validate_context_graph,
)
from .linker import (
    IngestionResult,
    IngestionStatus,
    Phase4InvariantError,
    VALID_INGESTION_STATUSES,
    _resolve_known_component_scope,
    _resolve_or_register_scope,
    _slugify_scope_id,
    classify_and_apply_delta,
    create_empty_context_graph,
    fuse_table_attributes,
)
from .read_engine import annotate_active_tasks


def resolve_ingestion_status(
    intake_packet: dict[str, Any],
    open_blockers: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> IngestionStatus:
    """Explicit state machine transition for Stage 4C ingestion status (Spec 22C §2.2, Law 6)."""
    pkt_clarify = intake_packet.get("grounding_status") == IngestionStatus.NEEDS_CLARIFICATION.value
    has_clarification = pkt_clarify or any(
        b.get("type") == "CLARIFICATION" and b.get("status") != "RESOLVED"
        for b in open_blockers
    )
    if has_clarification:
        return IngestionStatus.NEEDS_CLARIFICATION
    return IngestionStatus.CONFLICT if conflicts else IngestionStatus.COMMITTED


def check_phase4_invariants(
    result: dict[str, Any],
    *,
    k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING,
) -> list[str]:
    """Verifies Spec 22A–22C post-ingestion invariants on an `IngestionResult` or graph."""
    if not isinstance(result, dict):
        return [f"result must be a dict, got {type(result).__name__}"]
    is_envelope = "graph" in result and isinstance(result["graph"], dict)
    graph = result["graph"] if is_envelope else result
    errors = validate_context_graph(graph, k_cap=k_cap, max_ring=max_ring)
    if is_envelope:
        status = result.get("status")
        if status not in VALID_INGESTION_STATUSES:
            errors.append(
                f"invalid status={status!r}, expected one of {sorted(VALID_INGESTION_STATUSES)}"
            )
        max_r = result.get("max_rules_per_scope")
        if isinstance(max_r, int) and max_r > k_cap:
            errors.append(f"max_rules_per_scope={max_r} exceeds k_cap={k_cap}")
        ring_sz = result.get("ring_size")
        if isinstance(ring_sz, int) and ring_sz > max_ring:
            errors.append(f"ring_size={ring_sz} exceeds max_ring={max_ring}")
        if result.get("conflicts") and status == IngestionStatus.COMMITTED.value:
            errors.append("result has active conflicts but status is 'COMMITTED'")
        has_open_clarify = any(
            b.get("type") == "CLARIFICATION" and b.get("status") != "RESOLVED"
            for b in graph.get("open_blockers", [])
        )
        if has_open_clarify and status == IngestionStatus.COMMITTED.value:
            errors.append("result has unresolved CLARIFICATION blockers but status is 'COMMITTED'")
    return errors


def ingest_intake_packet(
    graph: dict[str, Any] | None,
    intake_packet: dict[str, Any],
    *,
    k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING,
    store: SQLiteGraphStore | None = None,
    db_path: str | Path | None = None,
    turn_index: int | None = None,
    source_turn: int | None = None,
) -> IngestionResult:
    """Executes Stage 4A -> Stage 4B -> Stage 4C Whiteboard Ingestion (Spec 22C §2.2)."""
    if graph is None:
        graph = create_empty_context_graph(k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)
    elif not isinstance(graph, dict):
        raise Phase4InvariantError(
            "phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"]
        )
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
    raw_turn = (
        override_turn
        if override_turn is not None
        else intake_packet.get("turn_index", max(1, prev_turn + 1))
    )
    if isinstance(raw_turn, bool) or not isinstance(raw_turn, int):
        raise Phase4InvariantError(
            "phase4_invalid_intake_packet", [f"turn_index must be an int, got {raw_turn!r}"]
        )
    eff_turn = int(raw_turn)
    graph["turn_index"] = graph["turn"] = eff_turn
    doc_tables = intake_packet.get("doc_tables", intake_packet.get("tables", []))
    fused_scopes = fuse_table_attributes(
        graph, doc_tables if isinstance(doc_tables, list) else [], turn_index=eff_turn
    )
    parsed_mcus = intake_packet.get(
        "parsed_mcus", intake_packet.get("items", intake_packet.get("mcus", []))
    )
    if not isinstance(parsed_mcus, list):
        raise Phase4InvariantError(
            "phase4_invalid_intake_packet", ["intake_packet['parsed_mcus'] must be a list"]
        )
    for pmcu in parsed_mcus:
        if isinstance(pmcu, dict):
            raw_primary = str(pmcu.get("primary_scope") or "").strip()
            raw_pm_txt = pmcu.get(
                "text", pmcu.get("canonical_text", pmcu.get("raw_text", ""))
            )
            pmcu_text = str(raw_pm_txt or "").strip()
            lp = pmcu.get("lattice_payload") or {}
            op_kind = str(lp.get("op") or "").removeprefix("UPSERT_")
            lkind = str(
                pmcu.get("lattice_kind")
                if pmcu.get("lattice_kind")
                else (lp.get("kind") or op_kind)
            ).strip().upper()
            if raw_primary and lkind not in ("SCHEMA", "INTERFACE", "LAW"):
                _resolve_known_component_scope(graph, raw_primary, pmcu_text, pmcu)
    delta_log: list[dict[str, Any]] = []
    for pmcu in parsed_mcus:
        if not isinstance(pmcu, dict):
            continue
        d_res = classify_and_apply_delta(graph, pmcu, turn_index=eff_turn)
        delta_log.append(d_res)
        lp = pmcu.get("lattice_payload") or {}
        raw_tests = pmcu.get("tests") if pmcu.get("tests") is not None else lp.get("tests")
        target_sid = d_res.get("scope", pmcu.get("primary_scope"))
        if raw_tests is not None and target_sid:
            snode = graph.get("scopes", {}).get(str(target_sid))
            if bool(snode):
                snode["tests"] = (
                    list(raw_tests) if isinstance(raw_tests, list) else [str(raw_tests)]
                )
        is_ambient = (
            pmcu.get("primary_scope") in (None, "", "global_defaults")
            and str(pmcu.get("type") or "").upper() in ("FACT", "GOAL", "PROPOSAL")
            and pmcu.get("target_prop") is None
        )
        if is_ambient:
            note_payload = {
                "id": f"ambient_t{eff_turn}_m{pmcu.get('mcu_id', len(delta_log))}",
                "text": str(pmcu.get("text", pmcu.get("canonical_text", "")) or ""),
                "turn": eff_turn,
            }
            upsert_ambient_note(graph, note_payload, current_turn=eff_turn, max_ring=max_ring)
    for cq in intake_packet.get("clarification_questions", []):
        if isinstance(cq, str) and cq.strip():
            bid = f"clarify_pkt_t{eff_turn}_{len(graph.setdefault('open_blockers', [])) + 1}"
            if not any(b.get("text") == cq.strip() for b in graph["open_blockers"]):
                graph["open_blockers"].append({
                    "id": bid,
                    "type": "CLARIFICATION",
                    "scope": "global_defaults",
                    "text": cq.strip(),
                    "rule_ids": [],
                })
    mitosis_splits = list(two_axis_mitosis(graph, k_cap=k_cap).get("splits", []))
    graph["ambient_notes"] = evict_from_ring(
        list(graph.get("ambient_notes", [])), eff_turn, max_ring=max_ring
    )
    conflicts = detect_frustration(graph)
    open_blockers = graph.setdefault("open_blockers", [])
    active_conflict_ids = {c["id"] for c in conflicts}
    open_blockers[:] = [
        b
        for b in open_blockers
        if not (b.get("type") == "CONFLICT" and b.get("id") not in active_conflict_ids)
    ]
    for c in reversed(conflicts):
        if not any(b.get("id") == c["id"] for b in open_blockers):
            open_blockers.insert(0, copy.deepcopy(c))
    annotate_active_tasks(graph, conflicts)
    status = resolve_ingestion_status(intake_packet, open_blockers, conflicts).value
    max_rules_per_scope = max(
        (len(s.get("rules", [])) for s in graph.get("scopes", {}).values()), default=0
    )
    ring_size = len(graph.get("ambient_notes", []))
    lattice_proof = evaluate_lattice_proof(graph)
    graph["lattice_proof"] = lattice_proof
    res = IngestionResult({
        "graph": graph,
        "status": status,
        "delta_log": delta_log,
        "conflicts": conflicts,
        "mitosis_splits": mitosis_splits,
        "ring_size": ring_size,
        "max_rules_per_scope": max_rules_per_scope,
        "fused_scopes": fused_scopes,
        "turn_index": eff_turn,
        "lattice_proof": lattice_proof,
    })
    if inv_errors := check_phase4_invariants(res, k_cap=k_cap, max_ring=max_ring):
        raise Phase4InvariantError("phase4_invariant_violation", inv_errors)
    sync_to_sqlite(graph)
    return res


from bowthruster.whiteboard import (
    _emit_interface_skeleton_lines,
    _extract_item_names,
    _extract_law_stub_spec,
    _fmt_spec_doc,
    _py_ident,
)


def is_python_skeleton_complete(
    has_schemas: bool, has_interfaces: bool, has_law_stubs: bool
) -> bool:
    """Pure completeness guard governed by Law 12 (`python_skeleton_completeness_law`)."""
    return bool(has_schemas) and bool(has_interfaces) and bool(has_law_stubs)


def compile_python_skeleton(
    scope_id: str,
    artifacts: list[dict[str, Any]] | None = None,
    *,
    schemas: list[dict[str, Any]] | None = None,
    interfaces: list[dict[str, Any]] | None = None,
    laws: list[dict[str, Any]] | None = None,
    rules: list[dict[str, Any]] | None = None,
) -> str:
    """Deterministically compiles an AST-parseable Python structural skeleton stub."""
    sid = str(scope_id or "global_defaults").strip() or "global_defaults"
    all_arts = list(artifacts or [])
    schema_list = list(
        schemas
        if schemas is not None
        else [x for x in all_arts if str(x.get("kind") or "").strip().upper() == "SCHEMA"]
    )
    iface_list = list(
        interfaces
        if interfaces is not None
        else [x for x in all_arts if str(x.get("kind") or "").strip().upper() == "INTERFACE"]
    )
    law_list = list(
        laws
        if laws is not None
        else [x for x in all_arts if str(x.get("kind") or "").strip().upper() == "LAW"]
    )
    lines: list[str] = [
        f"# Python Structural Skeleton — Compiled by Whiteboard for {sid}",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from typing import Any",
    ]
    seen_consts: set[str] = set()
    skip_props = ("max_file_lines", "import", "depends_on", "value", "rule")
    for r in (rules or []):
        b_val = r.get("bound_val") if r else None
        if str(r.get("type") or "").upper() == "BOUND":
            if isinstance(b_val, (int, float)) and not isinstance(b_val, bool):
                tp = str(r.get("target_prop") or "").strip()
                if tp and tp.isidentifier() and tp.lower() not in skip_props:
                    if tp.upper() not in seen_consts:
                        seen_consts.add(tp.upper())
                        op = r.get("bound_op") or "<="
                        lines.append(f"{tp.upper()} = {b_val!r}  # BOUND: {tp} {op} {b_val!r}")
    seen_schemas: set[str] = set()
    for sc_art in schema_list:
        raw_sn = sc_art.get("name", sc_art.get("schema", sc_art.get("id"))) or "SchemaStub"
        sname = _py_ident(raw_sn, default="SchemaStub")
        if sname in seen_schemas:
            continue
        seen_schemas.add(sname)
        dims = sc_art.get("dimensions") or {}
        sfile = str(
            next((_v for _v in (sc_art.get("file"), dims.get("file")) if _v), None) or ""
        ).strip()
        sc_doc = str(sc_art.get("spec") or "").strip()
        io_fallback = sc_art.get(
            "inputs", sc_art.get("outputs", dims.get("inputs", dims.get("outputs")))
        )
        fields = (
            _extract_item_names(sc_art.get("fields", dims.get("fields")))
            or _extract_item_names(io_fallback)
            or ["value"]
        )
        lines.extend((
            "",
            *([f"# FILE: {sfile}"] if sfile else []),
            "@dataclass(frozen=True)",
            f"class {sname}:",
            *_fmt_spec_doc(sc_doc),
            *(f"    {f_name}: Any = None" for f_name in fields),
        ))
    iface_names = {
        _py_ident(ia.get("name") or "interface_stub", default="interface_stub")
        for ia in iface_list
    }
    emitted_laws: list[tuple[str, list[str]]] = []
    seen_laws: set[str] = set()
    for law_art in law_list:
        lfn, lalias, lparams, py_expr = _extract_law_stub_spec(
            law_art, iface_names | seen_schemas
        )
        l_doc = str(law_art.get("spec") or "").strip()
        for fn_id in (lfn, lalias):
            if fn_id and fn_id not in seen_laws:
                seen_laws.add(fn_id)
                emitted_laws.append((fn_id, lparams))
                param_sig = ", ".join(f"{p}: bool = True" for p in lparams)
                lines.extend((
                    "",
                    f"def {fn_id}({param_sig}) -> bool:",
                    *_fmt_spec_doc(l_doc),
                    f"    return bool({py_expr})",
                ))
    lines.extend(_emit_interface_skeleton_lines(iface_list, emitted_laws, seen_schemas))
    code = "\n".join(lines) + "\n"
    ast.parse(code)
    return code


ingest_into_context_graph = ingest_into_whiteboard = run_phase4 = phase4 = ingest_intake_packet

from .packet_compiler import (
    _build_slice_packet,
    compile_laws_bend,
    compile_work_packets,
    query_context_graph,
    run_turn,
)

query_whiteboard = query_context_graph
