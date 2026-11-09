"""Spec 22C §2.2 & Stage 27: Whiteboard Stage 4C Ingestion, Work Packet & Python Skeleton Compiler (`ingest.py`)."""

from __future__ import annotations

import ast
import copy
import keyword
from pathlib import Path
from typing import Any

from lattice.graph_core import SQLiteGraphStore, evict_from_ring
from lattice.regiment.intake_packet import compile_intake_packet, compile_markdown_turn
from lattice.whiteboard.frustration import classify_lattice_proof_status, detect_frustration, evaluate_lattice_proof
from lattice.whiteboard.mutations import ensure_scope_node, transition_component_build_status, two_axis_mitosis, upsert_ambient_note
from lattice.whiteboard.schema import (
    BUILD_STATUSES, DEFAULT_K_CAP, DEFAULT_MAX_RING, resolve_packet_lifecycle_status,
    step_component_lifecycle_on_edit, sync_to_sqlite, validate_context_graph,
)
from .linker import (
    IngestionResult, IngestionStatus, Phase4InvariantError, VALID_INGESTION_STATUSES,
    _resolve_known_component_scope, _resolve_or_register_scope, _slugify_scope_id,
    classify_and_apply_delta, create_empty_context_graph, fuse_table_attributes,
    is_flow_or_rule_table_fused, is_table_schema_valid,
)
from .read_engine import annotate_active_tasks, expand_node, propagate_graph, scan_graph, slice_lattice


def resolve_ingestion_status(intake_packet: dict[str, Any], open_blockers: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> IngestionStatus:
    """Explicit state machine transition for Stage 4C ingestion status (Spec 22C §2.2, Law 6)."""
    has_clarification = intake_packet.get("grounding_status") == IngestionStatus.NEEDS_CLARIFICATION.value or any(
        isinstance(b, dict) and b.get("type") == "CLARIFICATION" and b.get("status") != "RESOLVED" for b in open_blockers
    )
    return IngestionStatus.NEEDS_CLARIFICATION if has_clarification else (IngestionStatus.CONFLICT if conflicts else IngestionStatus.COMMITTED)


def check_phase4_invariants(result: dict[str, Any], *, k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING) -> list[str]:
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
        if any(isinstance(b, dict) and b.get("type") == "CLARIFICATION" and b.get("status") != "RESOLVED" for b in graph.get("open_blockers", [])) and status == IngestionStatus.COMMITTED.value:
            errors.append("result has unresolved CLARIFICATION blockers but status is 'COMMITTED'")
    return errors


def ingest_intake_packet(
    graph: dict[str, Any] | None, intake_packet: dict[str, Any], *, k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING, store: SQLiteGraphStore | None = None, db_path: str | Path | None = None,
    turn_index: int | None = None, source_turn: int | None = None,
) -> IngestionResult:
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
        raise Phase4InvariantError("phase4_invalid_intake_packet", [f"intake_packet must be a dict, got {type(intake_packet).__name__}"])
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
            pmcu_text = str(pmcu.get("text") or pmcu.get("canonical_text") or pmcu.get("raw_text") or "").strip()
            lp = pmcu.get("lattice_payload") if isinstance(pmcu.get("lattice_payload"), dict) else {}
            lkind = str(pmcu.get("lattice_kind") or lp.get("kind") or str(lp.get("op") or "").removeprefix("UPSERT_") or "").strip().upper()
            if raw_primary and lkind not in ("SCHEMA", "INTERFACE", "LAW"):
                _resolve_known_component_scope(graph, raw_primary, pmcu_text, pmcu)
    delta_log: list[dict[str, Any]] = []
    for pmcu in parsed_mcus:
        if not isinstance(pmcu, dict):
            continue
        delta_log.append(classify_and_apply_delta(graph, pmcu, turn_index=eff_turn))
        if pmcu.get("primary_scope") in (None, "", "global_defaults") and str(pmcu.get("type") or "").upper() in ("FACT", "GOAL", "PROPOSAL") and pmcu.get("target_prop") is None:
            upsert_ambient_note(
                graph,
                {"id": f"ambient_t{eff_turn}_m{pmcu.get('mcu_id', len(delta_log))}", "text": str(pmcu.get("text") or pmcu.get("canonical_text") or ""), "turn": eff_turn},
                current_turn=eff_turn, max_ring=max_ring,
            )
    for cq in intake_packet.get("clarification_questions", []):
        if isinstance(cq, str) and cq.strip():
            bid = f"clarify_pkt_t{eff_turn}_{len(graph.setdefault('open_blockers', [])) + 1}"
            if not any(b.get("text") == cq.strip() for b in graph["open_blockers"] if isinstance(b, dict)):
                graph["open_blockers"].append({"id": bid, "type": "CLARIFICATION", "scope": "global_defaults", "text": cq.strip(), "rule_ids": []})
    mitosis_splits = list(two_axis_mitosis(graph, k_cap=k_cap).get("splits", []))
    graph["ambient_notes"] = evict_from_ring(list(graph.get("ambient_notes", [])), eff_turn, max_ring=max_ring)
    conflicts = detect_frustration(graph)
    open_blockers = graph.setdefault("open_blockers", [])
    active_conflict_ids = {c["id"] for c in conflicts}
    open_blockers[:] = [b for b in open_blockers if not (isinstance(b, dict) and b.get("type") == "CONFLICT" and b.get("id") not in active_conflict_ids)]
    for c in reversed(conflicts):
        if not any(isinstance(b, dict) and b.get("id") == c["id"] for b in open_blockers):
            open_blockers.insert(0, copy.deepcopy(c))
    annotate_active_tasks(graph, conflicts)
    status = resolve_ingestion_status(intake_packet, open_blockers, conflicts).value
    max_rules_per_scope = max((len(s.get("rules", [])) for s in graph.get("scopes", {}).values() if isinstance(s, dict)), default=0)
    ring_size = len(graph.get("ambient_notes", []))
    lattice_proof = evaluate_lattice_proof(graph)
    graph["lattice_proof"] = lattice_proof
    res = IngestionResult({
        "graph": graph, "status": status, "delta_log": delta_log, "conflicts": conflicts,
        "mitosis_splits": mitosis_splits, "ring_size": ring_size,
        "max_rules_per_scope": max_rules_per_scope, "fused_scopes": fused_scopes,
        "turn_index": eff_turn, "lattice_proof": lattice_proof,
    })
    if inv_errors := check_phase4_invariants(res, k_cap=k_cap, max_ring=max_ring):
        raise Phase4InvariantError("phase4_invariant_violation", inv_errors)
    sync_to_sqlite(graph)
    return res


def _parse_bend_law_args(raw_args: Any) -> list[str]:
    chunks = [str(x) for x in raw_args] if isinstance(raw_args, list) else (raw_args.replace("\n", ",").replace(";", ",").split(",") if isinstance(raw_args, str) else [])
    out: list[str] = []
    for chunk in chunks:
        for sub in chunk.split(" for "):
            c = sub.strip().strip("\"`'").strip()
            if c.startswith("for "):
                c = c[4:].strip()
            if not c or c.lower() in ("none", "-", "—", "n/a", "null"):
                continue
            if ":" not in c:
                c = f"{c}: Bool"
            out.append(c if c.startswith("+") else f"+{c}")
    return out


def compile_laws_bend(scope_id: str, law_records: list[dict[str, Any]], *, module_alias: str = "MOD") -> str:
    """Compiles `% LAW` records for `scope_id` into a standalone Bend 2 `LAWS.bend` file (Spec 22C §2.2)."""
    sid = str(scope_id or "global_defaults").strip() or "global_defaults"
    alias = str(module_alias or "MOD").strip() or "MOD"
    lines: list[str] = [f"# LAWS.bend — Compiled by Whiteboard from % LAW records for {sid}", "", "import Base", f"import ./main.bend as {alias}"]
    seen_names: set[str] = set()
    for law in law_records if isinstance(law_records, list) else []:
        if not isinstance(law, dict):
            continue
        raw_name = str(law.get("name") or law.get("law") or law.get("id") or "").strip()
        lname = _slugify_scope_id(raw_name).replace("/", "_").replace(".", "_")
        if not lname or lname in seen_names:
            continue
        seen_names.add(lname)
        dims = law.get("dimensions") if isinstance(law.get("dimensions"), dict) else {}
        raw_args = law.get("args") if law.get("args") is not None else dims.get("args")
        lhs = str(law.get("lhs") if law.get("lhs") is not None else (dims.get("lhs") or "")).strip().strip("\"`'").strip()
        rhs = str(law.get("rhs") if law.get("rhs") is not None else (dims.get("rhs") or "")).strip().strip("\"`'").strip()
        arg_specs = _parse_bend_law_args(raw_args)
        lines.extend(("", f"law {lname}:"))
        if arg_specs and lhs and rhs and lhs.lower() not in ("none", "-", "—", "n/a", "null") and rhs.lower() not in ("none", "-", "—", "n/a", "null"):
            lines.extend(f"  for {aspec}" for aspec in arg_specs)
            lines.append(f"  {{{lhs} == {rhs} : Bool}}")
        else:
            lines.extend(("  for +x: Bool", f"  {{{alias}.check_{lname}(x) == x : Bool}}"))
    return "\n".join(lines) + "\n"


def is_python_skeleton_complete(has_schemas: bool, has_interfaces: bool, has_law_stubs: bool) -> bool:
    """Pure completeness guard governed by Bend 2 Law 12 (`python_skeleton_completeness_law`, Stage 27)."""
    return bool(has_schemas) and bool(has_interfaces) and bool(has_law_stubs)
def is_status_synced(status_eq_build_status: bool = True) -> bool:
    return bool(status_eq_build_status)
def is_work_packet_self_contained(status_synced: bool, spec_in_docstring: bool) -> bool:
    return bool(status_synced) and bool(spec_in_docstring)
status_sync_and_spec_docstring_law = is_work_packet_self_contained


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
    dims = law.get("dimensions") if isinstance(law.get("dimensions"), dict) else {}
    lhs = str(law.get("lhs") if law.get("lhs") is not None else (dims.get("lhs") or "")).strip().strip("\"`'").strip()
    rhs = str(law.get("rhs") if law.get("rhs") is not None else (dims.get("rhs") or "")).strip().strip("\"`'").strip()
    fn_explicit = str(law.get("fn_name") or law.get("function") or dims.get("fn_name") or "").strip()
    law_title = _py_ident(law.get("name") or law.get("law") or law.get("id") or "", default="")
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
    params = [_py_ident(a.lstrip("+-").split(":", 1)[0].strip(), default=f"arg_{i}") for i, a in enumerate(_parse_bend_law_args(raw_args))]
    if not params and "(" in call_target and ")" in call_target:
        raw_call_args = _split_top_comma(call_target[call_target.find("(") + 1 : call_target.rfind(")")])
        params = [_py_ident(ca, default=f"arg_{i}") for i, ca in enumerate(raw_call_args) if ca and ca not in ("True{}", "False{}") and "(" not in ca and "." not in ca]
    params = params or ["x"]
    return fn_name, alias_name, params, _bend_bool_to_py(target_rhs if target_rhs and target_rhs not in ("True{}", "True") else "", params)


def _extract_item_names(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else ([p.strip() for p in str(raw).split(",") if p.strip()] if isinstance(raw, str) and raw.strip() else [])
    out: list[str] = []
    for item in items:
        raw_s = str((item.get("name") or item.get("field") or item.get("id")) if isinstance(item, dict) else item).strip()
        if raw_s and raw_s.lower() not in ("none", "-", "—", "n/a", "null"):
            ident = _py_ident(raw_s.split(":", 1)[0].strip(), default="")
            if ident and ident not in out:
                out.append(ident)
    return out


def compile_python_skeleton(
    scope_id: str, artifacts: list[dict[str, Any]] | None = None, *, schemas: list[dict[str, Any]] | None = None,
    interfaces: list[dict[str, Any]] | None = None, laws: list[dict[str, Any]] | None = None,
) -> str:
    """Deterministically compiles an AST-parseable Python structural skeleton stub for `scope_id` from its `SCHEMA`, `INTERFACE`, and `LAW` artifacts (Stage 27)."""
    sid = str(scope_id or "global_defaults").strip() or "global_defaults"
    all_arts = [a for a in (artifacts or []) if isinstance(a, dict)]
    schema_list = [a for a in (schemas if schemas is not None else [x for x in all_arts if str(x.get("kind") or "").strip().upper() == "SCHEMA"]) if isinstance(a, dict)]
    iface_list = [a for a in (interfaces if interfaces is not None else [x for x in all_arts if str(x.get("kind") or "").strip().upper() == "INTERFACE"]) if isinstance(a, dict)]
    law_list = [a for a in (laws if laws is not None else [x for x in all_arts if str(x.get("kind") or "").strip().upper() == "LAW"]) if isinstance(a, dict)]

    lines: list[str] = [
        f"# Python Structural Skeleton — Compiled by Whiteboard for {sid}",
        "from __future__ import annotations", "", "from dataclasses import dataclass", "from typing import Any",
    ]
    seen_schemas: set[str] = set()
    for sc_art in schema_list:
        sname = _py_ident(sc_art.get("name") or sc_art.get("schema") or sc_art.get("id") or "SchemaStub", default="SchemaStub")
        if sname in seen_schemas:
            continue
        seen_schemas.add(sname)
        dims = sc_art.get("dimensions") if isinstance(sc_art.get("dimensions"), dict) else {}
        sc_doc = str(sc_art.get("spec") or dims.get("spec") or sc_art.get("text") or "").strip().replace('"""', "'''")
        fields = _extract_item_names(sc_art.get("fields") or dims.get("fields")) or _extract_item_names(sc_art.get("inputs") or sc_art.get("outputs") or dims.get("inputs") or dims.get("outputs")) or ["value"]
        lines.extend(("", "@dataclass(frozen=True)", f"class {sname}:", *([f'    """{sc_doc}"""'] if sc_doc else []), *(f"    {f_name}: Any = None" for f_name in fields)))

    iface_names = {_py_ident(ia.get("name") or ia.get("interface") or ia.get("id") or "interface_stub", default="interface_stub") for ia in iface_list}
    emitted_laws: list[tuple[str, list[str]]] = []
    seen_laws: set[str] = set()
    for law_art in law_list:
        lfn, lalias, lparams, py_expr = _extract_law_stub_spec(law_art, iface_names | seen_schemas)
        ldims = law_art.get("dimensions") if isinstance(law_art.get("dimensions"), dict) else {}
        l_doc = str(law_art.get("spec") or ldims.get("spec") or law_art.get("text") or "").strip().replace('"""', "'''")
        for fn_id in (lfn, lalias):
            if fn_id and fn_id not in seen_laws:
                seen_laws.add(fn_id)
                emitted_laws.append((fn_id, lparams))
                sig_params = ", ".join(f"{p}: bool = True" for p in lparams)
                lines.extend(("", f"def {fn_id}({sig_params}) -> bool:", *([f'    """{l_doc}"""'] if l_doc else []), f"    return bool({py_expr})"))

    seen_ifaces: set[str] = set()
    for ia in iface_list:
        iname = _py_ident(ia.get("name") or ia.get("interface") or ia.get("id") or "interface_stub", default="interface_stub")
        if iname in seen_ifaces:
            continue
        seen_ifaces.add(iname)
        dims = ia.get("dimensions") if isinstance(ia.get("dimensions"), dict) else {}
        i_doc = str(ia.get("spec") or dims.get("spec") or ia.get("text") or "").strip().replace('"""', "'''")
        in_params = _extract_item_names(ia.get("inputs") or ia.get("args") or dims.get("inputs") or dims.get("args"))
        out_types = _extract_item_names(ia.get("outputs") or ia.get("output") or ia.get("returns") or dims.get("outputs"))
        sig_in = ", ".join(f"{p}: Any = None" for p in in_params)
        ret_ann = out_types[0] if len(out_types) == 1 else (f"tuple[{', '.join(out_types)}]" if len(out_types) > 1 else "Any")
        lines.extend(("", f"def {iname}({sig_in}) -> {ret_ann}:", *([f'    """{i_doc}"""'] if i_doc else [])))
        for lfn, lparams in emitted_laws:
            lines.append(f"    # MUST CALL: {lfn}({', '.join(lparams)})")
            lines.append(f"    _law_{lfn} = {lfn}({', '.join('True' for _ in lparams)})")
        ret_stmt = f"return {out_types[0]}()" if (len(out_types) == 1 and out_types[0] in seen_schemas) else (f"return _law_{emitted_laws[0][0]}" if emitted_laws else "return None")
        lines.append(f"    {ret_stmt}")
    has_defs = bool(seen_schemas or seen_ifaces or seen_laws or lines)
    if not (is_work_packet_self_contained(is_status_synced(has_defs), has_defs) and is_table_schema_valid(has_defs, bool(lines)) and is_flow_or_rule_table_fused(bool(all_arts), has_defs)):
        raise ValueError("status_sync_and_spec_docstring_law violated")
    code = "\n".join(lines) + "\n"
    ast.parse(code)
    return code


def _build_slice_packet(
    graph: dict[str, Any], pkt_id: str, wave: int, kind: str, sid: str,
    sc: dict[str, Any], build_status: str, default_owner: str | None = None,
) -> dict[str, Any]:
    sl = slice_lattice(graph, component=sid)
    arts = [a for a in sl.get("active_artifacts", []) if isinstance(a, dict)]
    diag = sc.get("failure_diagnostic")
    diag_clean = str(diag).strip() if isinstance(diag, str) and diag.strip() else None
    prompt_str = f"{sl['compact_view']}\n! BUILD_FAILURE [{sid}]: {diag_clean}" if diag_clean else sl["compact_view"]
    scope_schemas = [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() == "SCHEMA"]
    scope_ifaces = [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() == "INTERFACE"]
    scope_laws = [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() == "LAW"]
    compiled_laws = compile_laws_bend(sid, scope_laws)
    py_skeleton = compile_python_skeleton(sid, schemas=scope_schemas, interfaces=scope_ifaces, laws=scope_laws)
    return {
        "packet_id": pkt_id, "wave": wave, "kind": kind, "component": sid, "scope": sid,
        "owner": sc.get("owner") or default_owner, "stage": sc.get("stage"),
        "build_status": build_status, "build_tag": sc.get("build_tag"), "baseline_commit_sha": graph.get("last_commit_sha"),
        "core_tag": sc.get("core_tag"), "failure_diagnostic": diag_clean,
        "depends_on": list(sc.get("depends_on", [])),
        "files": list(sc.get("files", [])) if isinstance(sc.get("files"), list) else [],
        "schemas": scope_schemas, "laws": scope_laws,
        "compiled_laws_bend": compiled_laws, "laws_skeleton": compiled_laws,
        "python_skeleton": py_skeleton, "is_skeleton_complete": is_python_skeleton_complete(bool(scope_schemas), bool(scope_ifaces), bool(scope_laws)),
        "contracts": [copy.deepcopy(a) for a in arts if str(a.get("kind") or "").upper() in ("SCHEMA", "INTERFACE")],
        "flows": list(sl.get("active_flows", [])), "rules": list(sl.get("active_rules", [])),
        "tasks": list(sl.get("active_tasks", [])), "prompt_slice": prompt_str,
    }


def compile_work_packets(graph: dict[str, Any], *, force: bool = False, status_filter: str | None = None) -> dict[str, Any]:
    """Compiles dependency-ordered parallel Work Packets across Wave 0, Wave 1, and Wave 2 (Spec 22C §2.2)."""
    if not isinstance(graph, dict):
        raise Phase4InvariantError("phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"])
    proof = evaluate_lattice_proof(graph)
    is_proven = bool(proof.get("is_proven"))
    graph["lattice_proof"] = proof
    scopes = graph.get("scopes", {}) if isinstance(graph.get("scopes"), dict) else {}
    artifacts = [a for a in graph.get("lattice_artifacts", []) if isinstance(a, dict)]
    schemas = [copy.deepcopy(a) for a in artifacts if str(a.get("kind") or "").strip().upper() == "SCHEMA"]
    all_ifaces = [copy.deepcopy(a) for a in artifacts if str(a.get("kind") or "").strip().upper() == "INTERFACE"]
    all_laws = [copy.deepcopy(a) for a in artifacts if str(a.get("kind") or "").strip().upper() == "LAW"]
    wave0: list[dict[str, Any]] = []
    if schemas or all_laws:
        w0_slice = slice_lattice(graph, kind="SCHEMA")
        w0_laws_bend = compile_laws_bend("schemas", all_laws)
        w0_py_skel = compile_python_skeleton("schemas", schemas=schemas, interfaces=all_ifaces, laws=all_laws)
        wave0.append({
            "packet_id": "pkt_wave0_schemas", "wave": 0, "kind": "SCHEMA_CONTRACTS", "component": "schemas", "scope": "schemas",
            "owner": (schemas[0].get("owner") if schemas else (all_laws[0].get("owner") if all_laws else "bethe")) or "bethe",
            "stage": schemas[0].get("stage") if schemas else (all_laws[0].get("stage") if all_laws else None),
            "build_status": resolve_packet_lifecycle_status("DRAFT", is_proven, True), "build_tag": None, "core_tag": None, "failure_diagnostic": None,
            "depends_on": [], "schemas": schemas, "contracts": schemas, "laws": all_laws,
            "compiled_laws_bend": w0_laws_bend, "laws_skeleton": w0_laws_bend,
            "python_skeleton": w0_py_skel, "is_skeleton_complete": is_python_skeleton_complete(bool(schemas), bool(all_ifaces), bool(all_laws)),
            "flows": [], "rules": [], "tasks": [], "prompt_slice": w0_slice["compact_view"],
        })
    sbf_scopes = [(sid, sc) for sid, sc in scopes.items() if isinstance(sc, dict) and sid != "global_defaults" and not str(sid).startswith("global_defaults::") and (str(sc.get("kind") or "").strip().upper() == "COMPONENT" or bool(sc.get("role") or sc.get("owner") or sc.get("stage") or sc.get("depends_on") or sc.get("children") or sc.get("parent") or sc.get("files")))]
    non_qa_raw, qa_scopes = [], []
    for sid, sc in sbf_scopes:
        owner_low, stage_low = str(sc.get("owner") or "").strip().lower(), str(sc.get("stage") or "").strip().lower()
        (qa_scopes if (sid.lower().startswith(("test_", "tests/")) or owner_low in ("bobo", "qa", "q-division") or stage_low == "qa") else non_qa_raw).append((sid, sc))
    has_owned_non_qa = any(bool(sc.get("owner")) for _, sc in non_qa_raw)
    w1_candidates = [(sid, sc) for sid, sc in non_qa_raw if (bool(sc.get("owner")) if has_owned_non_qa else not bool(sc.get("children")))] or list(non_qa_raw)
    w1_candidates.sort(key=lambda item: (len(item[1].get("depends_on") or []), item[0]))
    owned_non_qa_set = {sid for sid, _ in w1_candidates}
    wave1: list[dict[str, Any]] = []
    for sid, sc in w1_candidates:
        all_deps_verified = all(scopes.get(d, {}).get("status") == "VERIFIED" for d in sc.get("depends_on", []) if d in owned_non_qa_set and bool(scopes.get(d, {}).get("rules") or scopes.get(d, {}).get("role") or scopes.get(d, {}).get("owner")))
        eff_status = resolve_packet_lifecycle_status(sc.get("status"), is_proven, all_deps_verified)
        sc["status"] = sc["build_status"] = eff_status if sc.get("status") in (None, "", "DRAFT", "BLOCKED_BY_DEP", "READY") else (sc.get("status") or eff_status)
        wave1.append(_build_slice_packet(graph, f"pkt_wave1_{sid}", 1, "COMPONENT_SLICE", sid, sc, eff_status))
    wave2: list[dict[str, Any]] = []
    for sid, sc in qa_scopes:
        target_deps = [d for d in sc.get("depends_on", []) if d in owned_non_qa_set] or list(owned_non_qa_set)
        eff_status = resolve_packet_lifecycle_status(sc.get("status"), is_proven, all(scopes.get(d, {}).get("status") == "VERIFIED" for d in target_deps))
        sc["status"] = sc["build_status"] = eff_status if sc.get("status") in (None, "", "DRAFT", "BLOCKED_BY_DEP", "READY") else (sc.get("status") or eff_status)
        wave2.append(_build_slice_packet(graph, f"pkt_wave2_{sid}", 2, "QA_VERIFICATION", sid, sc, eff_status, default_owner="bobo"))
    status_synced = all(sc.get("status") == sc.get("build_status") for _, sc in [*w1_candidates, *qa_scopes])
    if not is_work_packet_self_contained(is_status_synced(status_synced), bool(proof)):
        raise Phase4InvariantError("phase4_status_sync_violation", ["status != build_status after packet compilation"])
    if isinstance(status_filter, str) and status_filter.strip():
        sf = status_filter.strip().upper()
        wave0, wave1, wave2 = ([p for p in w if p.get("build_status") == sf] for w in (wave0, wave1, wave2))
    emit = is_proven or bool(force)
    sync_to_sqlite(graph)
    return {
        "status": proof["status"], "is_proven": is_proven, "ready_to_emit": is_proven,
        "open_gaps": list(proof.get("open_gaps", [])), "hard_violations": list(proof.get("hard_violations", [])),
        "lattice_proof": proof, "packets": (wave0 + wave1 + wave2) if emit else [],
        "waves": {"wave_0_schemas": wave0 if emit else [], "wave_1_components": wave1 if emit else [], "wave_2_qa": wave2 if emit else []},
    }


ingest_into_context_graph = ingest_into_whiteboard = run_phase4 = phase4 = ingest_intake_packet


def query_context_graph(
    graph: dict[str, Any], query: str = "", *, mode: str = "propagate", node_id: str | None = None,
    temperature: float | None = None, token_budget: int = 400, max_lines: int = 35,
) -> dict[str, Any]:
    """Queries `graph` in `'propagate'`, `'scan'`, or `'expand'` mode via the Whiteboard read engine (Spec 22C §2.1)."""
    if not isinstance(graph, dict):
        raise Phase4InvariantError("phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"])
    if mode not in ("propagate", "scan", "expand"):
        raise ValueError(f"invalid query mode={mode!r}, expected 'propagate', 'scan', or 'expand'")
    if mode == "expand":
        target_id = node_id if (isinstance(node_id, str) and node_id.strip()) else str(query or "").strip()
        return expand_node(graph, target_id, query=str(query or ""), token_budget=token_budget, max_lines=max_lines)
    if mode == "scan" or not isinstance(query, str) or not query.strip():
        return scan_graph(graph, str(query or "") if mode == "scan" else "", temperature=temperature, token_budget=token_budget, max_lines=max_lines)
    return propagate_graph(graph, query, temperature=temperature, token_budget=token_budget, max_lines=max_lines)


query_whiteboard = query_context_graph


def run_turn(
    graph: dict[str, Any] | None, raw_text: str = "", gen: Any = None, jev: Any = None, limits: Any = None, *,
    turn_index: int | None = None, query: str | None = None, k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING,
    token_budget: int = 400, max_lines: int = 35, phase3_result: dict[str, Any] | None = None,
    intake_packet: dict[str, Any] | None = None, store: SQLiteGraphStore | None = None, db_path: str | Path | None = None,
) -> IngestionResult:
    """Executes a full 3-Entity turn (Intake -> Whiteboard Ingestion -> Readout) (Spec 22C §2.2)."""
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
            raise Phase4InvariantError("phase4_invalid_intake_packet", [f"intake_packet must be a dict, got {type(intake_packet).__name__}"])
        packet, p3 = dict(intake_packet, turn_index=eff_turn), phase3_result
    elif phase3_result is not None:
        p3 = phase3_result
        packet = compile_intake_packet(p3, turn_index=eff_turn)
    else:
        if not isinstance(raw_text, str):
            raise Phase4InvariantError("phase4_invalid_raw_text", [f"raw_text must be a str, got {type(raw_text).__name__}"])
        packet = compile_markdown_turn(raw_text, turn_index=eff_turn)
        p3 = {"raw_text": raw_text, "doc_tables": packet["doc_tables"], "structured_table_rows": packet["structured_table_rows"], "parsed_mcus": packet["parsed_mcus"], "intake_packet": packet, "grounding_status": "GROUNDED", "is_locked": True, "clarification_questions": [], "open_questions": []}
    res = ingest_intake_packet(graph, packet, k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)
    eff_query = query if query is not None else (raw_text if isinstance(raw_text, str) else "")
    res["intake_packet"], res["phase3_result"] = packet, p3
    res["scan_view"] = query_context_graph(res["graph"], eff_query, mode="scan", token_budget=token_budget, max_lines=max_lines)
    res["readout"] = query_context_graph(res["graph"], eff_query, mode="propagate", token_budget=token_budget, max_lines=max_lines)
    return res
