"""Spec 22A–22C: Unified Persistent Bounded Whiteboard Package (`whiteboard`)."""

from __future__ import annotations

from bowthruster.graph_core import (
    ConflictDetector, DEFAULT_K_CAP, DEFAULT_MAX_RING, MAX_META_KEY_WORDS, MAX_META_SLOTS,
    MAX_META_SLOT_WORDS, MAX_META_TOTAL_WORDS, PropagationPhase, PropagationStateMachine,
    SQLiteGraphStore, _TYPE_TO_FACET, _build_five_family_edges, _build_rule_index,
    _build_scope_headers, _finalize_view, _lexical_token_similarity, _pack_active_rules_and_lod2,
    _rule_matches_target_or_refs, _scope_cross_degree, _score_single_rule,
    _tau_min_for_temperature, _unresolved_energy, actr_base_level_activation, clamp_rule_meta,
    compute_seed_activations, compute_tau, dense_text_similarities, evict_from_ring,
    expand_graph_node, format_compact_view, infer_query_temperature, init_graph_db,
    lexical_token_similarity, matches_nd_slice, max_lexical_similarity, propagate_nodes,
    scan_nodes, soft_token_overlap_count, token_cost, tokenize_content_words,
)
from bowthruster.whiteboard.frustration import (
    MAX_ANCESTOR_HOPS, _ancestor_scopes, _inherited_rules_clash, _intervals_satisfiable,
    _numeric_interval, _rules_clash, detect_frustration,
)
from bowthruster.whiteboard.migrations import (
    BOARD_SCHEMA_VERSION, BoardMigrationReport, BoardSchemaVersionError,
    detect_board_schema_version, format_migration_report, migrate_board_data,
    migrate_board_file, migrate_repo_boards,
)
from bowthruster.whiteboard.mutations import (
    _partition_rules_for_mitosis, claim_component_scope, ensure_scope_node, graduate_task_to_fact,
    markovian_overwrite_rule, prune_dysfluency_and_upsert_rule, resolve_open_blocker,
    transition_component_build_status, two_axis_mitosis, upsert_ambient_note, upsert_component_spec,
    upsert_flow_edge, upsert_lattice_artifact, wire_bidirectional_refs, wire_lex_specialis_override,
)
from bowthruster.whiteboard.schema import (
    ArtifactEntry,
    BUILD_STATUSES,
    ContextGraph,
    ContextGraphInvariantError,
    FlowEntry,
    LATTICE_KINDS,
    LatticeGraph,
    RuleEntry,
    ScopeNode,
    WhiteboardGraph,
    classify_lattice_store,
    create_empty_graph,
    create_empty_lattice,
    normalize_board_graph,
    resolve_packet_lifecycle_status,
    should_register_as_scope,
    step_component_lifecycle_on_edit,
    sync_to_sqlite,
    validate_context_graph,
    verify_graph_invariants,
)
import keyword
from typing import Any

_EMPTY_TOKENS = frozenset({"none", "-", "—", "n/a", "null"})


def _parse_bend_law_args(raw_args: Any) -> list[str]:
    chunks = (
        [str(x) for x in raw_args]
        if isinstance(raw_args, list)
        else (raw_args.replace("\n", ",").replace(";", ",").split(",") if isinstance(raw_args, str) else [])
    )
    out: list[str] = []
    for chunk in chunks:
        for sub in chunk.split(" for "):
            c = sub.strip().strip("\"`'").strip()
            if c.startswith("for "):
                c = c[4:].strip()
            if not c or c.lower() in _EMPTY_TOKENS:
                continue
            if ":" not in c:
                c = f"{c}: Bool"
            out.append(c if c.startswith("+") else f"+{c}")
    return out


def _py_ident(raw: Any, default: str = "stub") -> str:
    s = str(raw or "").strip().strip("\"`'").strip()
    if s and s.isidentifier() and not keyword.iskeyword(s):
        return s
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in s).strip("_")
    if not cleaned:
        return default
    cleaned = f"_{cleaned}" if cleaned[0].isdigit() else cleaned
    return f"{cleaned}_" if keyword.iskeyword(cleaned) else cleaned


def _split_top_comma(expr: str) -> list[str]:
    parts, cur, depth = [], [], 0
    for ch in expr:
        depth = depth + 1 if ch in "([{" else (max(0, depth - 1) if ch in ")]}" else depth)
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur or expr.endswith(","):
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _bend_bool_to_py(expr: str, params: list[str]) -> str:
    s = str(expr or "").strip().strip("\"`'").strip()
    if s in ("True{}", "True", "true"):
        return "True"
    if s in ("False{}", "False", "false"):
        return "False"
    if s.startswith("Bool.not(") and s.endswith(")"):
        return f"(not {_bend_bool_to_py(s[9:-1], params)})"
    if "(" in s and s.endswith(")"):
        callee = s.split("(", 1)[0].strip().split(".")[-1].strip()
        args = _split_top_comma(s[s.find("(") + 1 : -1])
        if callee in ("and", "or", "bool_eq", "eq") and len(args) == 2:
            op = "==" if callee in ("bool_eq", "eq") else callee
            return f"({_bend_bool_to_py(args[0], params)} {op} {_bend_bool_to_py(args[1], params)})"
    ident = _py_ident(s, default="")
    return ident if (ident and ident in params) else (" and ".join(params) if params else "True")


def _extract_law_stub_spec(law: dict[str, Any], conflict_names: set[str]) -> tuple[str, str | None, list[str], str]:
    dims = (law.get("dimensions") or {})
    raw_lhs = law.get("lhs") if law.get("lhs") is not None else (dims.get("lhs") or "")
    raw_rhs = law.get("rhs") if law.get("rhs") is not None else (dims.get("rhs") or "")
    lhs, rhs = str(raw_lhs).strip().strip("\"`'").strip(), str(raw_rhs).strip().strip("\"`'").strip()
    fn_explicit = str(law.get("fn_name", law.get("function", dims.get("fn_name", ""))) or "").strip()
    law_title = _py_ident(law.get("name", law.get("law", law.get("id", ""))) or "", default="")
    call_target, target_rhs = lhs, rhs
    if not fn_explicit and "(" in call_target:
        first_callee = call_target.split("(", 1)[0].strip().split(".")[-1].strip()
        if first_callee in ("bool_eq", "and", "or", "not", "eq") and "(" in call_target.split("(", 1)[1]:
            inner_args = _split_top_comma(call_target[call_target.find("(") + 1 : -1])
            call_target = inner_args[0] if inner_args else call_target.split("(", 1)[1]
            if len(inner_args) >= 2 and target_rhs in ("True{}", "True", ""):
                target_rhs = inner_args[1]
        fn_raw = call_target.split("(", 1)[0].strip().split(".")[-1].strip()
    else:
        fn_raw = fn_explicit or law_title or "check_law"
    fn_name = _py_ident(fn_raw, default="check_law")
    if fn_name in conflict_names:
        fn_name = f"check_{fn_name}"
    alias_name = law_title if (law_title and law_title != fn_name and law_title not in conflict_names) else None
    raw_args = law.get("args") if law.get("args") is not None else dims.get("args")
    params = [
        _py_ident(a.lstrip("+-").split(":", 1)[0].strip(), default=f"arg_{i}")
        for i, a in enumerate(_parse_bend_law_args(raw_args))
    ]
    if not params and "(" in call_target and ")" in call_target:
        raw_call_args = _split_top_comma(call_target[call_target.find("(") + 1 : call_target.rfind(")")])
        params = [
            _py_ident(ca, default=f"arg_{i}")
            for i, ca in enumerate(raw_call_args)
            if ca and ca not in ("True{}", "False{}") and "(" not in ca and "." not in ca
        ]
    params = params or ["x"]
    rhs_clean = target_rhs if target_rhs and target_rhs not in ("True{}", "True") else ""
    return fn_name, alias_name, params, _bend_bool_to_py(rhs_clean, params)


def _extract_item_names(raw: Any) -> list[str]:
    items = (
        raw
        if isinstance(raw, list)
        else ([p.strip() for p in str(raw).split(",") if p.strip()] if isinstance(raw, str) and raw.strip() else [])
    )
    out: list[str] = []
    for item in items:
        raw_s = str(item.get("name", item.get("field", item.get("id", ""))) if isinstance(item, dict) else item)
        if raw_s.strip() and raw_s.strip().lower() not in _EMPTY_TOKENS:
            ident = _py_ident(raw_s.split(":", 1)[0].strip(), default="")
            if ident and ident not in out:
                out.append(ident)
    return out


def _fmt_spec_doc(raw_doc: str) -> list[str]:
    clean = str(raw_doc or "").strip().replace('"""', "'''")
    if not clean:
        return []
    parts = [p.strip() for p in clean.split(";") if p.strip()]
    if len(parts) <= 1:
        return [f'    """{clean}"""']
    return [f'    """{parts[0]}', *(f"    - {p}" for p in parts[1:]), '    """']


def _emit_interface_skeleton_lines(
    iface_list: list[dict[str, Any]], emitted_laws: list[tuple[str, list[str]]], seen_schemas: set[str]
) -> list[str]:
    lines: list[str] = []
    seen_ifaces: set[str] = set()
    for ia in iface_list:
        iname = _py_ident(
            ia.get("name", ia.get("interface", ia.get("id"))) or "interface_stub", default="interface_stub"
        )
        if iname in seen_ifaces:
            continue
        seen_ifaces.add(iname)
        dims = (ia.get("dimensions") or {})
        ifile = str(next((_v for _v in (ia.get("file"), dims.get("file")) if _v), None) or "").strip()
        i_doc = str(next((_v for _v in (ia.get("spec"), dims.get("spec")) if _v), None) or ia.get("text") or "").strip()
        in_params = _extract_item_names(ia.get("inputs", ia.get("args", dims.get("inputs", dims.get("args")))))
        out_types = _extract_item_names(
            ia.get("outputs", ia.get("output", ia.get("returns", dims.get("outputs"))))
        )
        ret_ann = (
            out_types[0]
            if len(out_types) == 1
            else (f"tuple[{', '.join(out_types)}]" if len(out_types) > 1 else "Any")
        )
        param_sig = ", ".join(f"{p}: Any = None" for p in in_params)
        lines.extend(("", *([f"# FILE: {ifile}"] if ifile else []), f"def {iname}({param_sig}) -> {ret_ann}:"))
        lines.extend(_fmt_spec_doc(i_doc))
        for lfn, lparams in emitted_laws:
            arg_exprs = [
                (
                    f"{in_params[i % len(in_params)]} is not None"
                    if i % 3 == 0
                    else (
                        f"bool({in_params[i % len(in_params)]})"
                        if i % 3 == 1
                        else f"str({in_params[i % len(in_params)]}) != ''"
                    )
                )
                if in_params
                else p
                for i, p in enumerate(lparams)
            ]
            lines.extend((
                f"    # MUST CALL: {lfn}({', '.join(lparams)})",
                f"    _law_{lfn} = {lfn}({', '.join(arg_exprs)})",
            ))
        law_guard = " and ".join(f"_law_{lfn}" for lfn, _ in emitted_laws)
        is_schema_out = len(out_types) == 1 and out_types[0] in seen_schemas
        ret_expr = f"{out_types[0]}()" if is_schema_out else (law_guard if emitted_laws else "None")
        if emitted_laws and is_schema_out:
            lines.append(f"    return {ret_expr} if ({law_guard}) else {ret_expr}")
        else:
            lines.append(f"    return {ret_expr}")
    return lines


from .ingest import (
    check_phase4_invariants,
    classify_lattice_proof_status,
    compile_laws_bend,
    compile_python_skeleton,
    compile_work_packets,
    evaluate_lattice_proof,
    ingest_intake_packet,
    ingest_into_context_graph,
    ingest_into_whiteboard,
    is_python_skeleton_complete,
    phase4,
    query_context_graph,
    query_whiteboard,
    resolve_ingestion_status,
    run_phase4,
    run_turn,
)
from .linker import (
    DeltaOperation,
    DeltaTransition,
    _collect_root_scope_rules,
    _is_prohibited_by_active_rule,
    can_overwrite_node_kind,
    classify_and_apply_delta,
    classify_delta_transition,
    classify_lattice_delta,
)
from .read_engine import (
    annotate_active_tasks,
    classify_task_status,
    expand_node,
    prohibition_blocks_task,
    propagate_graph,
    scan_graph,
    slice_lattice,
)
from .rewriter import (
    IngestionResult,
    IngestionStatus,
    Phase4InvariantError,
    VALID_INGESTION_STATUSES,
    _extract_canonical_and_aliases,
    _parse_scalar_cell,
    _register_scope_aliases,
    _resolve_or_register_scope,
    _slugify_scope_id,
    create_empty_context_graph,
    fuse_table_attributes,
    resolve_mcu_scopes_and_refs,
)

try:
    from the_executive.orchestrator import (
        PostCommitSyncResult,
        WorkingTreeDriftReport,
        detect_working_tree_drift,
        should_mark_dependent_stale_on_commit,
        sync_post_commit_to_lattice,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from bowthruster.the_executive.orchestrator import (  # type: ignore[no-redef]
            PostCommitSyncResult,
            WorkingTreeDriftReport,
            detect_working_tree_drift,
            should_mark_dependent_stale_on_commit,
            sync_post_commit_to_lattice,
        )
    except (ImportError, ModuleNotFoundError):
        pass


def __getattr__(name: str):
    if name in (
        "PostCommitSyncResult", "WorkingTreeDriftReport",
        "detect_working_tree_drift", "should_mark_dependent_stale_on_commit",
        "sync_post_commit_to_lattice"
    ):
        from bowthruster.the_executive import orchestrator as _orch
        return getattr(_orch, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ArtifactEntry",
    "BUILD_STATUSES",
    "ConflictDetector",
    "ContextGraph",
    "ContextGraphInvariantError",
    "DEFAULT_K_CAP",
    "DEFAULT_MAX_RING",
    "DeltaOperation",
    "DeltaTransition",
    "FlowEntry",
    "IngestionResult",
    "IngestionStatus",
    "LATTICE_KINDS",
    "LatticeGraph",
    "MAX_ANCESTOR_HOPS",
    "MAX_META_KEY_WORDS",
    "MAX_META_SLOTS",
    "MAX_META_SLOT_WORDS",
    "MAX_META_TOTAL_WORDS",
    "Phase4InvariantError",
    "PostCommitSyncResult",
    "PropagationPhase",
    "PropagationStateMachine",
    "RuleEntry",
    "SQLiteGraphStore",
    "ScopeNode",
    "VALID_INGESTION_STATUSES",
    "WhiteboardGraph",
    "WorkingTreeDriftReport",
    "_TYPE_TO_FACET",
    "_ancestor_scopes",
    "_build_five_family_edges",
    "_build_rule_index",
    "_build_scope_headers",
    "_collect_root_scope_rules",
    "_extract_canonical_and_aliases",
    "_finalize_view",
    "_inherited_rules_clash",
    "_intervals_satisfiable",
    "_is_prohibited_by_active_rule",
    "_lexical_token_similarity",
    "_numeric_interval",
    "_pack_active_rules_and_lod2",
    "_parse_scalar_cell",
    "_partition_rules_for_mitosis",
    "_register_scope_aliases",
    "_resolve_or_register_scope",
    "_rule_matches_target_or_refs",
    "_rules_clash",
    "_scope_cross_degree",
    "_score_single_rule",
    "_slugify_scope_id",
    "_tau_min_for_temperature",
    "_unresolved_energy",
    "actr_base_level_activation",
    "annotate_active_tasks",
    "can_overwrite_node_kind",
    "check_phase4_invariants",
    "clamp_rule_meta",
    "claim_component_scope",
    "classify_and_apply_delta",
    "classify_delta_transition",
    "classify_lattice_delta",
    "classify_lattice_proof_status",
    "classify_lattice_store",
    "classify_task_status",
    "compile_laws_bend",
    "compile_python_skeleton",
    "compile_work_packets",
    "compute_seed_activations",
    "compute_tau",
    "create_empty_context_graph",
    "create_empty_graph",
    "create_empty_lattice",
    "dense_text_similarities",
    "detect_frustration",
    "detect_working_tree_drift",
    "ensure_scope_node",
    "evaluate_lattice_proof",
    "evict_from_ring",
    "expand_graph_node",
    "expand_node",
    "format_compact_view",
    "fuse_table_attributes",
    "graduate_task_to_fact",
    "infer_query_temperature",
    "ingest_intake_packet",
    "ingest_into_context_graph",
    "ingest_into_whiteboard",
    "init_graph_db",
    "is_python_skeleton_complete",
    "lexical_token_similarity",
    "markovian_overwrite_rule",
    "matches_nd_slice",
    "max_lexical_similarity",
    "normalize_board_graph",
    "phase4",
    "prohibition_blocks_task",
    "propagate_graph",
    "propagate_nodes",
    "prune_dysfluency_and_upsert_rule",
    "query_context_graph",
    "query_whiteboard",
    "resolve_ingestion_status",
    "resolve_mcu_scopes_and_refs",
    "resolve_open_blocker",
    "resolve_packet_lifecycle_status",
    "run_phase4",
    "run_turn",
    "scan_graph",
    "scan_nodes",
    "should_mark_dependent_stale_on_commit",
    "should_register_as_scope",
    "slice_lattice",
    "soft_token_overlap_count",
    "step_component_lifecycle_on_edit",
    "sync_post_commit_to_lattice",
    "sync_to_sqlite",
    "token_cost",
    "tokenize_content_words",
    "transition_component_build_status",
    "two_axis_mitosis",
    "upsert_ambient_note",
    "upsert_component_spec",
    "upsert_flow_edge",
    "upsert_lattice_artifact",
    "validate_context_graph",
    "verify_graph_invariants",
    "wire_bidirectional_refs",
    "wire_lex_specialis_override",
    "BOARD_SCHEMA_VERSION",
    "BoardMigrationReport",
    "BoardSchemaVersionError",
    "detect_board_schema_version",
    "format_migration_report",
    "migrate_board_data",
    "migrate_board_file",
    "migrate_repo_boards",
]
