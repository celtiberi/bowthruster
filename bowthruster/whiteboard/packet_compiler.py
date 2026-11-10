"""Spec 22C §2.2: Whiteboard WorkPacket Compiler, Bend Law Emitter, & Turn Runner (`packet_compiler.py`)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore
from bowthruster.regiment.intake_packet import compile_intake_packet, compile_markdown_turn
from bowthruster.whiteboard.frustration import evaluate_lattice_proof
from bowthruster.whiteboard.schema import (
    DEFAULT_K_CAP,
    DEFAULT_MAX_RING,
    normalize_board_graph,
    resolve_packet_lifecycle_status,
    sync_to_sqlite,
)
from .ast_slice import _art_file_path
from .linker import (
    IngestionResult,
    Phase4InvariantError,
    _slugify_scope_id,
    create_empty_context_graph,
)
from .read_engine import expand_node, propagate_graph, scan_graph, slice_lattice

_EMPTY_TOKENS = frozenset({"none", "-", "—", "n/a", "null"})


def compile_laws_bend(
    scope_id: str, law_records: list[dict[str, Any]], *, module_alias: str = "MOD"
) -> str:
    """Compiles `% LAW` records for `scope_id` into a standalone Bend 2 `LAWS.bend` file."""
    from bowthruster.whiteboard import _parse_bend_law_args

    sid = str(scope_id or "global_defaults").strip() or "global_defaults"
    alias = str(module_alias or "MOD").strip() or "MOD"
    lines: list[str] = [
        f"# LAWS.bend — Compiled by Whiteboard from % LAW records for {sid}",
        "",
        "import Base",
        f"import ./main.bend as {alias}",
    ]
    seen_names: set[str] = set()
    for law in (law_records if isinstance(law_records, list) else []):
        if not isinstance(law, dict):
            continue
        raw_name = str(law.get("name") or "").strip()
        lname = _slugify_scope_id(raw_name).replace("/", "_").replace(".", "_")
        if not lname or lname in seen_names:
            continue
        seen_names.add(lname)
        dims = law.get("dimensions") or {}
        raw_args = law.get("args") if law.get("args") is not None else dims.get("args")
        raw_lhs = law.get("lhs") if law.get("lhs") is not None else (dims.get("lhs") or "")
        raw_rhs = law.get("rhs") if law.get("rhs") is not None else (dims.get("rhs") or "")
        lhs = str(raw_lhs).strip().strip("\"`'").strip()
        rhs = str(raw_rhs).strip().strip("\"`'").strip()
        arg_specs = _parse_bend_law_args(raw_args)
        lines.extend(("", f"law {lname}:"))
        valid_eq = (
            arg_specs
            and lhs
            and rhs
            and lhs.lower() not in _EMPTY_TOKENS
            and rhs.lower() not in _EMPTY_TOKENS
        )
        if valid_eq:
            lines.extend(f"  for {aspec}" for aspec in arg_specs)
            lines.append(f"  {{{lhs} == {rhs} : Bool}}")
        else:
            lines.extend(("  for +x: Bool", f"  {{{alias}.check_{lname}(x) == x : Bool}}"))
    return "\n".join(lines) + "\n"


def _build_slice_packet(
    graph: dict[str, Any],
    pkt_id: str,
    wave: int,
    kind: str,
    sid: str,
    sc: dict[str, Any],
    build_status: str,
    default_owner: str | None = None,
) -> dict[str, Any]:
    from .ingest import compile_python_skeleton, is_python_skeleton_complete

    sl = slice_lattice(graph, component=sid)
    arts = list(sl.get("active_artifacts", []))
    diag = sc.get("failure_diagnostic")
    diag_clean = str(diag).strip() if isinstance(diag, str) and diag.strip() else None
    prompt_str = (
        f"{sl['compact_view']}\n! BUILD_FAILURE [{sid}]: {diag_clean}"
        if diag_clean
        else sl["compact_view"]
    )
    scope_schemas = [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() == "SCHEMA"]
    scope_ifaces = [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() == "INTERFACE"]
    scope_laws = [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() == "LAW"]
    scope_rules = list(sl.get("active_rules", []))
    compiled_laws = compile_laws_bend(sid, scope_laws)
    py_skeleton = compile_python_skeleton(
        sid, schemas=scope_schemas, interfaces=scope_ifaces, laws=scope_laws, rules=scope_rules
    )
    art_files = sorted(
        {_art_file_path(a) for a in [*scope_schemas, *scope_ifaces] if _art_file_path(a)}
    )
    by_file = {
        f: compile_python_skeleton(
            sid,
            schemas=[s for s in scope_schemas if _art_file_path(s) in ("", f)],
            interfaces=[i for i in scope_ifaces if _art_file_path(i) == f],
            laws=scope_laws,
            rules=scope_rules,
        )
        for f in art_files
    }
    contracts = [
        copy.deepcopy(a)
        for a in arts
        if str(a.get("kind") or "").upper() in ("SCHEMA", "INTERFACE")
    ]
    return {
        "packet_id": pkt_id,
        "wave": wave,
        "kind": kind,
        "component": sid,
        "scope": sid,
        "owner": sc.get("owner") or default_owner,
        "stage": sc.get("stage"),
        "build_status": build_status,
        "claimed_by": sc.get("claimed_by"),
        "claimed_at": sc.get("claimed_at"),
        "build_tag": sc.get("build_tag"),
        "baseline_commit_sha": graph.get("last_commit_sha"),
        "core_tag": sc.get("core_tag"),
        "failure_diagnostic": diag_clean,
        "depends_on": list(sc.get("depends_on", [])),
        "files": list(sc.get("files", [])),
        "tests": list(sc.get("tests", [])),
        "schemas": scope_schemas,
        "laws": scope_laws,
        "compiled_laws_bend": compiled_laws,
        "laws_skeleton": compiled_laws,
        "python_skeleton": py_skeleton,
        "python_skeletons_by_file": by_file,
        "is_skeleton_complete": is_python_skeleton_complete(
            bool(scope_schemas), bool(scope_ifaces), bool(scope_laws)
        ),
        "contracts": contracts,
        "flows": list(sl.get("active_flows", [])),
        "rules": scope_rules,
        "tasks": list(sl.get("active_tasks", [])),
        "prompt_slice": prompt_str,
    }


def compile_work_packets(
    graph: dict[str, Any],
    *,
    force: bool = False,
    status_filter: str | None = None,
    unclaimed_only: bool = False,
) -> dict[str, Any]:
    """Compiles dependency-ordered Work Packets across Wave 0, Wave 1, and Wave 2."""
    from .ingest import compile_python_skeleton, is_python_skeleton_complete

    if not isinstance(graph, dict):
        raise Phase4InvariantError(
            "phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"]
        )
    normalize_board_graph(graph)
    proof = evaluate_lattice_proof(graph)
    is_proven = bool(proof.get("is_proven"))
    graph["lattice_proof"] = proof
    scopes = graph["scopes"]
    artifacts = list(graph["lattice_artifacts"])
    schemas = [
        copy.deepcopy(a)
        for a in artifacts
        if str(a.get("kind") or "").strip().upper() == "SCHEMA"
    ]
    all_ifaces = [
        copy.deepcopy(a)
        for a in artifacts
        if str(a.get("kind") or "").strip().upper() == "INTERFACE"
    ]
    all_laws = [
        copy.deepcopy(a)
        for a in artifacts
        if str(a.get("kind") or "").strip().upper() == "LAW"
    ]
    wave0: list[dict[str, Any]] = []
    if schemas or all_laws:
        w0_slice = slice_lattice(graph, kind="SCHEMA")
        w0_laws_bend = compile_laws_bend("schemas", all_laws)
        w0_py_skel = compile_python_skeleton(
            "schemas", schemas=schemas, interfaces=all_ifaces, laws=all_laws
        )
        first_art = schemas[0] if schemas else (all_laws[0] if all_laws else {})
        w0_owner = first_art.get("owner") or "architect"
        wave0.append({
            "packet_id": "pkt_wave0_schemas",
            "wave": 0,
            "kind": "SCHEMA_CONTRACTS",
            "component": "schemas",
            "scope": "schemas",
            "owner": w0_owner,
            "stage": first_art.get("stage"),
            "build_status": resolve_packet_lifecycle_status("DRAFT", is_proven, True),
            "claimed_by": None,
            "claimed_at": None,
            "build_tag": None,
            "core_tag": None,
            "failure_diagnostic": None,
            "depends_on": [],
            "schemas": schemas,
            "contracts": schemas,
            "laws": all_laws,
            "compiled_laws_bend": w0_laws_bend,
            "laws_skeleton": w0_laws_bend,
            "python_skeleton": w0_py_skel,
            "is_skeleton_complete": is_python_skeleton_complete(
                bool(schemas), bool(all_ifaces), bool(all_laws)
            ),
            "flows": [],
            "rules": [],
            "tasks": [],
            "prompt_slice": w0_slice["compact_view"],
        })
    sbf_keys = ("role", "owner", "stage", "depends_on", "children", "parent", "files")
    sbf_scopes = [
        (sid, sc)
        for sid, sc in scopes.items()
        if sid != "global_defaults"
        and not str(sid).startswith("global_defaults::")
        and (
            str(sc.get("kind") or "").strip().upper() == "COMPONENT"
            or any(sc.get(k) for k in sbf_keys)
        )
    ]
    non_qa_raw, qa_scopes = [], []
    for sid, sc in sbf_scopes:
        owner_low = str(sc.get("owner") or "").strip().lower()
        stage_low = str(sc.get("stage") or "").strip().lower()
        is_qa = (
            sid.lower().startswith(("test_", "tests/", "qa_"))
            or owner_low in ("qa", "q-division")
            or stage_low == "qa"
        )
        (qa_scopes if is_qa else non_qa_raw).append((sid, sc))
    has_owned_non_qa = any(bool(sc.get("owner")) for _, sc in non_qa_raw)
    w1_candidates = [
        (sid, sc)
        for sid, sc in non_qa_raw
        if (bool(sc.get("owner")) if has_owned_non_qa else not bool(sc.get("children")))
    ] or list(non_qa_raw)
    w1_candidates.sort(key=lambda item: (len(item[1].get("depends_on") or []), item[0]))
    owned_non_qa_set = {sid for sid, _ in w1_candidates}
    mutable_statuses = (None, "", "DRAFT", "BLOCKED_BY_DEP", "READY")
    wave1: list[dict[str, Any]] = []
    for sid, sc in w1_candidates:
        all_deps_verified = all(
            scopes.get(d, {}).get("status") == "VERIFIED"
            for d in sc.get("depends_on", [])
            if d in owned_non_qa_set
            and any(scopes.get(d, {}).get(k) for k in ("rules", "role", "owner"))
        )
        eff_status = resolve_packet_lifecycle_status(sc.get("status"), is_proven, all_deps_verified)
        sc["status"] = sc["build_status"] = (
            eff_status if sc.get("status") in mutable_statuses else (sc.get("status") or eff_status)
        )
        wave1.append(
            _build_slice_packet(graph, f"pkt_wave1_{sid}", 1, "COMPONENT_SLICE", sid, sc, eff_status)
        )
    wave2: list[dict[str, Any]] = []
    for sid, sc in qa_scopes:
        target_deps = [
            d for d in sc.get("depends_on", []) if d in owned_non_qa_set
        ] or list(owned_non_qa_set)
        deps_ok = all(scopes.get(d, {}).get("status") == "VERIFIED" for d in target_deps)
        eff_status = resolve_packet_lifecycle_status(sc.get("status"), is_proven, deps_ok)
        sc["status"] = sc["build_status"] = (
            eff_status if sc.get("status") in mutable_statuses else (sc.get("status") or eff_status)
        )
        wave2.append(
            _build_slice_packet(
                graph, f"pkt_wave2_{sid}", 2, "QA_VERIFICATION", sid, sc, eff_status, default_owner="qa"
            )
        )
    if not all(sc.get("status") == sc.get("build_status") for _, sc in [*w1_candidates, *qa_scopes]):
        raise Phase4InvariantError(
            "phase4_status_sync_violation", ["status != build_status after packet compilation"]
        )
    if isinstance(status_filter, str) and status_filter.strip():
        sf = status_filter.strip().upper()
        wave0 = [p for p in wave0 if p.get("build_status") == sf]
        wave1 = [p for p in wave1 if p.get("build_status") == sf]
        wave2 = [p for p in wave2 if p.get("build_status") == sf]
    if unclaimed_only:
        wave0 = [p for p in wave0 if not p.get("claimed_by")]
        wave1 = [p for p in wave1 if not p.get("claimed_by")]
        wave2 = [p for p in wave2 if not p.get("claimed_by")]
    emit = is_proven or bool(force)
    sync_to_sqlite(graph)
    return {
        "status": proof["status"],
        "is_proven": is_proven,
        "ready_to_emit": is_proven,
        "open_gaps": list(proof.get("open_gaps", [])),
        "hard_violations": list(proof.get("hard_violations", [])),
        "lattice_proof": proof,
        "packets": (wave0 + wave1 + wave2) if emit else [],
        "waves": {
            "wave_0_schemas": wave0 if emit else [],
            "wave_1_components": wave1 if emit else [],
            "wave_2_qa": wave2 if emit else [],
        },
    }


def query_context_graph(
    graph: dict[str, Any],
    query: str = "",
    *,
    mode: str = "propagate",
    node_id: str | None = None,
    temperature: float | None = None,
    token_budget: int = 400,
    max_lines: int = 35,
) -> dict[str, Any]:
    """Queries `graph` in `'propagate'`, `'scan'`, or `'expand'` mode."""
    if not isinstance(graph, dict):
        raise Phase4InvariantError(
            "phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"]
        )
    if mode not in ("propagate", "scan", "expand"):
        raise ValueError(f"invalid query mode={mode!r}, expected 'propagate', 'scan', or 'expand'")
    if mode == "expand":
        target = (
            node_id if (isinstance(node_id, str) and node_id.strip()) else str(query or "").strip()
        )
        return expand_node(
            graph, target, query=str(query or ""), token_budget=token_budget, max_lines=max_lines
        )
    if mode == "scan" or not isinstance(query, str) or not query.strip():
        q_str = str(query or "") if mode == "scan" else ""
        return scan_graph(
            graph, q_str, temperature=temperature, token_budget=token_budget, max_lines=max_lines
        )
    return propagate_graph(
        graph, query, temperature=temperature, token_budget=token_budget, max_lines=max_lines
    )


def run_turn(
    graph: dict[str, Any] | None,
    raw_text: str = "",
    gen: Any = None,
    jev: Any = None,
    limits: Any = None,
    *,
    turn_index: int | None = None,
    query: str | None = None,
    k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING,
    token_budget: int = 400,
    max_lines: int = 35,
    phase3_result: dict[str, Any] | None = None,
    intake_packet: dict[str, Any] | None = None,
    store: SQLiteGraphStore | None = None,
    db_path: str | Path | None = None,
) -> IngestionResult:
    """Executes a full 3-Entity turn (Intake -> Whiteboard Ingestion -> Readout)."""
    from .ingest import ingest_intake_packet

    _ = (gen, jev, limits)
    eff_k_cap = int(k_cap)
    eff_max_ring = int(max_ring)
    if graph is None:
        graph = create_empty_context_graph(
            k_cap=eff_k_cap, max_ring=eff_max_ring, store=store, db_path=db_path
        )
    elif not isinstance(graph, dict):
        raise Phase4InvariantError(
            "phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"]
        )
    prev_turn = max(int(graph.get("turn", 0)), int(graph.get("turn_index", 0)))
    if turn_index is not None and (isinstance(turn_index, bool) or not isinstance(turn_index, int)):
        raise Phase4InvariantError(
            "phase4_invalid_intake_packet", [f"turn_index must be an int, got {turn_index!r}"]
        )
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
            raise Phase4InvariantError(
                "phase4_invalid_raw_text", [f"raw_text must be a str, got {type(raw_text).__name__}"]
            )
        packet = compile_markdown_turn(raw_text, turn_index=eff_turn)
        p3 = {
            "raw_text": raw_text,
            "doc_tables": packet["doc_tables"],
            "structured_table_rows": packet["structured_table_rows"],
            "parsed_mcus": packet["parsed_mcus"],
            "intake_packet": packet,
            "grounding_status": "GROUNDED",
            "is_locked": True,
            "clarification_questions": [],
            "open_questions": [],
        }
    res = ingest_intake_packet(
        graph, packet, k_cap=eff_k_cap, max_ring=eff_max_ring, store=store, db_path=db_path
    )
    eff_query = query if query is not None else (raw_text if isinstance(raw_text, str) else "")
    res["intake_packet"], res["phase3_result"] = packet, p3
    res["scan_view"] = query_context_graph(
        res["graph"], eff_query, mode="scan", token_budget=int(token_budget), max_lines=int(max_lines)
    )
    res["readout"] = query_context_graph(
        res["graph"],
        eff_query,
        mode="propagate",
        token_budget=int(token_budget),
        max_lines=int(max_lines),
    )
    return res
