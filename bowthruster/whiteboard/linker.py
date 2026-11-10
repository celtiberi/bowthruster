"""Spec 22C §2.2: Whiteboard Stage 4B Explicit Delta Classification State Machine, Markovian Overwrite, and Bidirectional Edge Wiring (`linker.py`)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bowthruster.nlp import (
    restore_ascii_dots,
)
from bowthruster.whiteboard.frustration import _intervals_satisfiable
from bowthruster.whiteboard.mutations import (
    graduate_task_to_fact, markovian_overwrite_rule, prune_dysfluency_and_upsert_rule,
    upsert_ambient_note, upsert_component_spec, upsert_flow_edge, upsert_lattice_artifact, wire_lex_specialis_override,
)
from bowthruster.whiteboard.schema import _invalidate_scope_on_edit, sync_to_sqlite, tokenize_content_words
from .read_engine import annotate_active_tasks, classify_task_status
from .rewriter import (
    IngestionResult, IngestionStatus, Phase4InvariantError, VALID_INGESTION_STATUSES,
    _extract_canonical_and_aliases, _filter_non_dep_tokens, _parse_list_cell, _parse_scalar_cell,
    _register_scope_aliases, _resolve_known_component_scope, _resolve_or_register_scope,
    _slugify_scope_id, create_empty_context_graph, fuse_table_attributes,
    resolve_mcu_scopes_and_refs,
)


class DeltaOperation(str, Enum):
    """Explicit state machine operations for Stage 4B delta classification (Spec 22C §2.2)."""

    BLOCKER_CLARIFICATION = "BLOCKER_CLARIFICATION"
    BLOCKER_QUESTION = "BLOCKER_QUESTION"
    COMMIT_NOTE = "COMMIT_NOTE"
    GRADUATE_TASK_TO_FACT = "GRADUATE_TASK_TO_FACT"
    UPSERT_TASK = "UPSERT_TASK"
    ATTACH_RATIONALE = "ATTACH_RATIONALE"
    MARKOVIAN_OVERWRITE = "MARKOVIAN_OVERWRITE"
    LEX_SPECIALIS_EXCEPTION = "LEX_SPECIALIS_EXCEPTION"
    ORTHOGONAL_INSERT = "ORTHOGONAL_INSERT"


@dataclass(frozen=True)
class DeltaTransition:
    """Deterministic Stage 4B transition decision produced before mutating the WhiteboardGraph (Spec 22C §2.2)."""

    operation: DeltaOperation
    scope_id: str
    ref_scopes: list[str]
    mcu_id: Any
    text: str
    node_type: str
    facet: Any
    unclear_spans: list[str]
    target_prop: Any
    bound_op: Any
    bound_val: Any
    conditions: list[str]
    is_exception: bool
    meta: dict[str, Any]
    matched_rule_scope: str | None = None
    matched_rule: dict[str, Any] | None = None
    broader_rules: list[dict[str, Any]] = field(default_factory=list)


def _collect_root_scope_rules(graph: dict[str, Any], root_scope_id: str) -> list[tuple[str, dict[str, Any]]]:
    """Returns `[(scope_id, rule), ...]` for `root_scope_id` and any mitosis child scopes `root_scope_id::*`."""
    base = root_scope_id.split("::")[0]
    out: list[tuple[str, dict[str, Any]]] = []
    for sid, snode in graph.get("scopes", {}).items():
        if isinstance(snode, dict) and (sid == base or sid.startswith(f"{base}::")):
            for r in snode.get("rules", []):
                if isinstance(r, dict) and r.get("id"):
                    out.append((sid, r))
    return out


def _is_prohibited_by_active_rule(
    existing_rules: list[tuple[str, dict[str, Any]]],
    target_prop: str | None,
    bound_op: str | None,
    bound_val: Any,
    cond_set: set[str],
    incoming_type: str,
    is_exception: bool,
) -> bool:
    """Returns `True` if an active rule in `existing_rules` and the incoming rule have opposing prohibition polarity
    and clash via identical value or disjoint numeric intervals (Spec 22C §2.2, Laws 3-5)."""
    if target_prop is None or is_exception:
        return False
    inc_is_prohib = incoming_type == "PROHIBITION" or bound_op == "!="
    inc_is_num = isinstance(bound_val, (int, float)) and not isinstance(bound_val, bool)
    for _, r in existing_rules:
        if r.get("target_prop") != target_prop:
            continue
        r_conds = set(r.get("conditions", []))
        if (r_conds and cond_set and not (r_conds & cond_set)) or (len(cond_set) > len(r_conds) and r_conds.issubset(cond_set)):
            continue
        r_type, r_op, r_val = str(r.get("type", "")), r.get("bound_op"), r.get("bound_val")
        r_is_prohib = r_type == "PROHIBITION" or r_op == "!="
        if r_is_prohib == inc_is_prohib:
            continue
        r_is_num = isinstance(r_val, (int, float)) and not isinstance(r_val, bool)
        same_val = (
            r_val is None
            or bound_val is None
            or (
                float(r_val) == float(bound_val)
                if (r_is_num and inc_is_num)
                else str(r_val).strip().lower() == str(bound_val).strip().lower()
            )
        )
        if same_val:
            return True
        if r_is_num and inc_is_num and not _intervals_satisfiable(r_op, float(r_val), bound_op, float(bound_val)):
            return True
    return False


_DESCRIPTIVE_NODE_TYPES = frozenset({"FACT", "GOAL", "PROPOSAL"})
_NON_NORMATIVE_NODE_TYPES = frozenset({"FACT", "GOAL", "PROPOSAL", "QUESTION", "COMMIT", "ACTION"})


def can_overwrite_node_kind(existing_type: str, incoming_type: str) -> bool:
    """Returns True iff `existing_type` and `incoming_type` belong to the same overwritable epistemic partition
    (Spec 22C §2.2, Bend 2 Law 8: `epistemic_partition_blocks_cross_kind_overwrite`)."""
    ex_t = str(existing_type or "")
    in_t = str(incoming_type or "")
    if not ex_t or not in_t:
        return False
    if in_t in _DESCRIPTIVE_NODE_TYPES or ex_t in _DESCRIPTIVE_NODE_TYPES:
        return ex_t == in_t and in_t in _DESCRIPTIVE_NODE_TYPES
    return ex_t not in _NON_NORMATIVE_NODE_TYPES and in_t not in _NON_NORMATIVE_NODE_TYPES


def classify_delta_transition(
    graph: dict[str, Any], parsed_mcu: dict[str, Any], *, scope_id: str,
    ref_scopes: list[str], existing_rules: list[tuple[str, dict[str, Any]]], turn_index: int = 1,
) -> DeltaTransition:
    """Deterministically maps `(parsed_mcu, existing_rules)` to a `DeltaTransition` before mutating `graph` (Spec 22C §2.2, Laws 1-3)."""
    t_turn = int(turn_index)
    mcu_id = parsed_mcu.get("mcu_id", 1)
    text = restore_ascii_dots(str(parsed_mcu.get("text") or parsed_mcu.get("canonical_text") or "")).strip()
    raw_ntype = str(parsed_mcu.get("type") or "OBLIGATION").strip().upper()
    node_type = "FACT" if raw_ntype == "CLAIM" else raw_ntype
    facet = parsed_mcu.get("facet")
    unclear_spans = list(parsed_mcu.get("unclear_spans", []))
    grounding = str(parsed_mcu.get("grounding_status") or "GROUNDED")
    target_prop, bound_op, bound_val = parsed_mcu.get("target_prop"), parsed_mcu.get("bound_op"), parsed_mcu.get("bound_val")
    conditions = [
        restore_ascii_dots(str(c)).strip()
        for c in parsed_mcu.get("conditions", [])
        if str(c).strip() and restore_ascii_dots(str(c)).strip().lower() not in ("none", "-", "—", "n/a", "null")
    ]
    cond_set = set(conditions)
    is_exception = bool(parsed_mcu.get("is_exception", False))
    meta = copy.deepcopy(parsed_mcu.get("meta", {})) if isinstance(parsed_mcu.get("meta"), dict) else {}
    base_kw: dict[str, Any] = {
        "scope_id": scope_id, "ref_scopes": list(ref_scopes), "mcu_id": mcu_id, "text": text,
        "node_type": node_type, "facet": facet, "unclear_spans": unclear_spans, "target_prop": target_prop,
        "bound_op": bound_op, "bound_val": bound_val, "conditions": conditions, "is_exception": is_exception, "meta": meta,
    }

    if grounding == "NEEDS_CLARIFICATION" or unclear_spans:
        return DeltaTransition(operation=DeltaOperation.BLOCKER_CLARIFICATION, **base_kw)
    if node_type == "QUESTION" or grounding == "HAS_OPEN_QUESTIONS":
        return DeltaTransition(operation=DeltaOperation.BLOCKER_QUESTION, **base_kw)
    if node_type == "COMMIT":
        return DeltaTransition(operation=DeltaOperation.COMMIT_NOTE, **base_kw)
    if node_type == "ACTION":
        op = DeltaOperation.GRADUATE_TASK_TO_FACT if parsed_mcu.get("completed") is True else DeltaOperation.UPSERT_TASK
        return DeltaTransition(operation=op, **base_kw)

    if node_type == "FACT" and target_prop is None:
        inc_toks = set(tokenize_content_words(text))
        if inc_toks:
            for r_sid, r_prev in existing_rules:
                if int(r_prev.get("turn", 0)) != t_turn:
                    continue
                rmeta = graph.get("scopes", {}).get(r_sid, {}).get("_meta", {}).get(r_prev.get("id", ""), {})
                if isinstance(rmeta, dict):
                    rat_str = " ".join(
                        str(rmeta.get(k, "")) for k in ("rationale", "transition_rationale")
                        if rmeta.get(k)
                    )
                    if rat_str and inc_toks.issubset(set(tokenize_content_words(rat_str))):
                        return DeltaTransition(
                            operation=DeltaOperation.ATTACH_RATIONALE,
                            matched_rule_scope=r_sid, matched_rule=r_prev,
                            **base_kw
                        )

    prohibited = _is_prohibited_by_active_rule(
        existing_rules, target_prop, bound_op,
        bound_val, cond_set, node_type, is_exception
    )
    incoming_is_prohib = node_type == "PROHIBITION" or bound_op == "!="
    if target_prop is not None and not prohibited:
        same_prop_cond_match = next(
            (
                (r_sid, r) for r_sid, r in existing_rules
                if r.get("target_prop") == target_prop and set(r.get("conditions", [])) == cond_set
                and (r.get("type") == "PROHIBITION" or r.get("bound_op") == "!=") == incoming_is_prohib
                and can_overwrite_node_kind(str(r.get("type") or ""), node_type)
            ),
            None,
        )
        if same_prop_cond_match is not None:
            r_sid, r_old = same_prop_cond_match
            r_old_val = r_old.get("bound_val")
            same_turn_disjoint_bounds = (
                int(r_old.get("turn", 0)) == t_turn and not parsed_mcu.get("superseded_literals")
                and isinstance(r_old_val, (int, float)) and not isinstance(r_old_val, bool)
                and isinstance(bound_val, (int, float)) and not isinstance(bound_val, bool)
                and r_old.get("bound_op") != bound_op
                and not _intervals_satisfiable(r_old.get("bound_op"), float(r_old_val), bound_op, float(bound_val))
            )
            if not same_turn_disjoint_bounds:
                return DeltaTransition(
                    operation=DeltaOperation.MARKOVIAN_OVERWRITE,
                    matched_rule_scope=r_sid, matched_rule=r_old,
                    **base_kw
                )

    broader_rules: list[dict[str, Any]] = []
    if conditions or is_exception:
        for _, r_cand in existing_rules:
            r_conds = set(r_cand.get("conditions", []))
            if target_prop is not None and r_cand.get("target_prop") == target_prop:
                if r_conds < cond_set or (is_exception and not r_conds):
                    broader_rules.append(r_cand)
            elif target_prop is None and is_exception and not r_conds:
                broader_rules.append(r_cand)
    for ov_target_id in parsed_mcu.get("overrides", []):
        for _, r_cand in existing_rules:
            if r_cand.get("id") == ov_target_id and r_cand not in broader_rules:
                broader_rules.append(r_cand)

    if broader_rules:
        return DeltaTransition(operation=DeltaOperation.LEX_SPECIALIS_EXCEPTION, broader_rules=broader_rules, **base_kw)
    return DeltaTransition(operation=DeltaOperation.ORTHOGONAL_INSERT, **base_kw)


_SBF_KIND_TO_OP: dict[str, str] = {
    "COMPONENT": "UPSERT_COMPONENT",
    "FLOW": "UPSERT_FLOW",
    "SCHEMA": "UPSERT_SCHEMA",
    "INTERFACE": "UPSERT_INTERFACE",
    "LAW": "UPSERT_LAW",
}


def classify_lattice_delta(
    lattice_kind: str,
    grounding_status: str,
    has_unclear: bool,
) -> str:
    """Deterministically routes an nD Lattice / SBF MCU per Spec 22C §2.2 and Bend 2 Law 11 (`lattice_delta_routes_sbf_kinds`)."""
    if str(grounding_status or "").strip().upper() == "NEEDS_CLARIFICATION" or has_unclear:
        return "BLOCKER_INSERT_CLARIFICATION"
    k = str(lattice_kind or "").strip().upper()
    if k in _SBF_KIND_TO_OP:
        return _SBF_KIND_TO_OP[k]
    return "DELEGATE_RULE_OR_TASK"


def classify_and_apply_delta(
    graph: dict[str, Any], parsed_mcu: dict[str, Any], turn_index: int = 1, *, source_turn: int | None = None,
) -> dict[str, Any]:
    """Stage 4B: Classifies `parsed_mcu` via `classify_lattice_delta` and `classify_delta_transition` and applies the mutation (Spec 22C §2.2)."""
    if not isinstance(graph, dict) or not isinstance(parsed_mcu, dict):
        raise ValueError("graph and parsed_mcu must be dicts")

    t_turn = int(source_turn if source_turn is not None else turn_index)
    graph["turn_index"] = max(int(graph.get("turn_index", 0)), t_turn)
    graph["turn"] = max(int(graph.get("turn", 0)), graph["turn_index"])
    scope_id, ref_scopes = resolve_mcu_scopes_and_refs(graph, parsed_mcu)

    lpayload = parsed_mcu.get("lattice_payload") if isinstance(parsed_mcu.get("lattice_payload"), dict) else {}
    lkind = str(
        parsed_mcu.get("lattice_kind") or lpayload.get("kind")
        or str(lpayload.get("op") or "").removeprefix("UPSERT_")
        or ""
    ).strip().upper()
    unclear_spans = list(parsed_mcu.get("unclear_spans", []))
    grounding = str(parsed_mcu.get("grounding_status") or "GROUNDED")
    mcu_id = parsed_mcu.get("mcu_id", 1)
    text_clean = restore_ascii_dots(str(parsed_mcu.get("text") or parsed_mcu.get("canonical_text") or parsed_mcu.get("raw_text") or "")).strip()

    lop = classify_lattice_delta(lkind, grounding, bool(unclear_spans))
    if lop == "UPSERT_COMPONENT":
        comp_raw = lpayload.get("scope_id") or lpayload.get("id") or lpayload.get("component") or lpayload.get("scope") or scope_id
        comp_sid = _resolve_known_component_scope(graph, str(comp_raw), text_clean, parsed_mcu) if comp_raw else scope_id
        existing_sc = graph.get("scopes", {}).get(comp_sid, {}) if isinstance(graph.get("scopes"), dict) else {}
        sc_attrs = existing_sc.get("attributes", {}) if isinstance(existing_sc, dict) and isinstance(existing_sc.get("attributes"), dict) else {}
        from_table_this_turn = int(sc_attrs.get("updated_turn", 0)) == t_turn
        raw_parent = lpayload.get("parent") if "parent" in lpayload else parsed_mcu.get("parent")
        parent_sid = _resolve_or_register_scope(graph, str(raw_parent), create=True) if raw_parent else None
        raw_children = lpayload.get("children") if "children" in lpayload else parsed_mcu.get("children")
        children_sids = (
            [_resolve_or_register_scope(graph, str(c), create=True) for c in raw_children if str(c).strip()]
            if isinstance(raw_children, list) and (raw_children or not existing_sc.get("children"))
            else None
        )
        raw_deps = lpayload.get("depends_on") if "depends_on" in lpayload else parsed_mcu.get("depends_on")
        if "_table_depends_on" in sc_attrs and from_table_this_turn:
            dep_sids: list[str] | None = list(sc_attrs["_table_depends_on"])
        elif isinstance(raw_deps, list):
            clean_deps, _ = _filter_non_dep_tokens(graph, comp_sid, raw_deps, text_clean, lpayload, parsed_mcu)
            dep_sids = [_resolve_or_register_scope(graph, str(d), create=True) for d in clean_deps if str(d).strip()]
        else:
            dep_sids = None
        role_val = (
            sc_attrs["_table_role"] if ("_table_role" in sc_attrs and from_table_this_turn)
            else (lpayload.get("role") if "role" in lpayload else parsed_mcu.get("role"))
        )
        raw_files = lpayload.get("files") if "files" in lpayload else parsed_mcu.get("files")
        comp_scope = upsert_component_spec(
            graph, comp_sid, role=role_val,
            inputs=lpayload.get("inputs") if "inputs" in lpayload else parsed_mcu.get("inputs"),
            outputs=lpayload.get("outputs") if "outputs" in lpayload else parsed_mcu.get("outputs"),
            depends_on=dep_sids, children=children_sids, parent=parent_sid,
            owner=lpayload.get("owner") if "owner" in lpayload else parsed_mcu.get("owner"),
            stage=lpayload.get("stage") if "stage" in lpayload else parsed_mcu.get("stage"),
            kind=lpayload.get("kind") or "COMPONENT",
            status=lpayload.get("status") if "status" in lpayload else parsed_mcu.get("status"),
            current_turn=t_turn,
        )
        if raw_files is not None:
            flist = _parse_list_cell(raw_files) if isinstance(raw_files, str) else [str(f).strip() for f in raw_files if str(f).strip()]
            comp_scope["files"] = flist
            comp_scope.setdefault("attributes", {})["files"] = flist
        annotate_active_tasks(graph)
        sync_to_sqlite(graph)
        return {"operation": lop, "scope": comp_scope["id"], "scope_id": comp_scope["id"], "mcu_id": mcu_id}

    if lop == "UPSERT_FLOW":
        raw_src = str(lpayload.get("source") or parsed_mcu.get("source") or scope_id).strip()
        raw_tgt = str(
            lpayload.get("target") or parsed_mcu.get("target")
            or (ref_scopes[0] if ref_scopes else scope_id)
        ).strip()
        src_sid = _resolve_or_register_scope(graph, raw_src, create=True)
        tgt_sid = _resolve_or_register_scope(graph, raw_tgt, create=True)
        conds = [restore_ascii_dots(str(c)).strip() for c in parsed_mcu.get("conditions", []) if str(c).strip()]
        cond_val = lpayload.get("condition") or ("; ".join(conds) if conds else None)
        dims = dict(lpayload.get("dimensions") or {}) if isinstance(lpayload.get("dimensions"), dict) else {}
        for d_key in ("owner", "stage", "flow_name"):
            d_val = lpayload.get(d_key) or parsed_mcu.get(d_key)
            if d_val is not None:
                dims.setdefault(d_key, str(d_val).strip())
        flow_entry = upsert_flow_edge(
            graph, src_sid, tgt_sid,
            payload=lpayload.get("payload") if "payload" in lpayload else parsed_mcu.get("payload"),
            action=lpayload.get("action") if "action" in lpayload else parsed_mcu.get("action"),
            condition=cond_val, step=lpayload.get("step") if "step" in lpayload else parsed_mcu.get("step"),
            text=str(lpayload.get("text") or text_clean), dimensions=dims or None, current_turn=t_turn,
        )
        annotate_active_tasks(graph)
        sync_to_sqlite(graph)
        return {
            "operation": lop, "flow_id": flow_entry["id"],
            "source": src_sid, "target": tgt_sid,
            "scope": src_sid, "mcu_id": mcu_id
        }

    if lop in ("UPSERT_SCHEMA", "UPSERT_INTERFACE", "UPSERT_LAW"):
        raw_ids = lpayload.get("ids") if isinstance(lpayload.get("ids"), list) else (parsed_mcu.get("ids") if isinstance(parsed_mcu.get("ids"), list) else [])
        fallback_id = str(
            lpayload.get("id") or lpayload.get("name")
            or parsed_mcu.get("id") or parsed_mcu.get("name")
            or parsed_mcu.get("target_prop") or scope_id
        ).strip()
        art_ids = [str(x).strip() for x in raw_ids if str(x).strip()] or [fallback_id]
        art_sid_raw = lpayload.get("scope_id") or lpayload.get("scope")
        art_sid = _resolve_known_component_scope(
            graph, str(art_sid_raw), text_clean,
            parsed_mcu
        ) if (art_sid_raw and str(art_sid_raw).strip() != "global_defaults") else scope_id
        if art_sid == "global_defaults" and scope_id != "global_defaults":
            art_sid = scope_id
        raw_ins = lpayload.get("inputs") if "inputs" in lpayload else parsed_mcu.get("inputs")
        raw_outs = (
            lpayload.get("outputs") if "outputs" in lpayload
            else (lpayload.get("output") if "output" in lpayload else (parsed_mcu.get("outputs") if "outputs" in parsed_mcu else parsed_mcu.get("output")))
        )
        ins_list = _parse_list_cell(raw_ins) if isinstance(raw_ins, str) else raw_ins
        outs_list = _parse_list_cell(raw_outs) if isinstance(raw_outs, str) else raw_outs
        extra_art_fields = {
            k: str(v).strip()
            for k in ("file", "bend_dir", "args", "lhs", "rhs")
            if (v := (lpayload.get(k) if k in lpayload else parsed_mcu.get(k))) is not None and str(v).strip()
        }
        dims_in = dict(lpayload.get("dimensions") or {}) if isinstance(lpayload.get("dimensions"), dict) else {}
        dims_in.update(extra_art_fields)
        upserted_entries: list[dict[str, Any]] = []
        for art_name in art_ids:
            eff_sid = art_sid
            if eff_sid == "global_defaults":
                existing_art = next(
                    (
                        a for a in graph.get("lattice_artifacts", [])
                        if isinstance(a, dict) and a.get("kind") == lkind
                        and str(a.get("name", "")).lower() == art_name.lower()
                        and a.get("scope") not in (None, "", "global_defaults")
                    ),
                    None
                )
                if existing_art is not None:
                    eff_sid = str(existing_art["scope"])
            entry = upsert_lattice_artifact(
                graph, lkind, art_name, scope_id=eff_sid, spec=str(lpayload.get("spec") or text_clean),
                inputs=ins_list, outputs=outs_list,
                owner=lpayload.get("owner") if "owner" in lpayload else parsed_mcu.get("owner"),
                stage=lpayload.get("stage") if "stage" in lpayload else parsed_mcu.get("stage"),
                dimensions=dims_in or None, text=str(lpayload.get("text") or text_clean), current_turn=t_turn,
            )
            for fk, fv in extra_art_fields.items():
                entry[fk] = fv
            upserted_entries.append(entry)
        annotate_active_tasks(graph)
        sync_to_sqlite(graph)
        first_entry = upserted_entries[0]
        return {
            "operation": lop, "artifact_id": first_entry["id"], "artifact_ids": [e["id"] for e in upserted_entries],
            "kind": lkind, "name": first_entry["name"], "scope": first_entry["scope"], "mcu_id": mcu_id,
        }

    existing_rules = _collect_root_scope_rules(graph, scope_id)
    tr = classify_delta_transition(
        graph, parsed_mcu, scope_id=scope_id,
        ref_scopes=ref_scopes, existing_rules=existing_rules,
        turn_index=t_turn
    )
    op = tr.operation

    if op == DeltaOperation.BLOCKER_CLARIFICATION:
        blocker_id = f"clarify_t{t_turn}_m{tr.mcu_id}"
        blocker = {
            "id": blocker_id, "type": "CLARIFICATION",
            "scope": scope_id, "mcu_id": tr.mcu_id,
            "text": "; ".join(tr.unclear_spans)
            if tr.unclear_spans else tr.text, "rule_ids": []
        }
        blockers = graph.setdefault("open_blockers", [])
        if not any(b.get("id") == blocker_id for b in blockers if isinstance(b, dict)):
            blockers.append(blocker)
        annotate_active_tasks(graph)
        sync_to_sqlite(graph)
        return {"operation": op.value, "blocker_id": blocker_id, "scope": scope_id, "mcu_id": tr.mcu_id}

    if op == DeltaOperation.BLOCKER_QUESTION:
        blocker_id = f"question_t{t_turn}_m{tr.mcu_id}"
        blocker = {
            "id": blocker_id, "type": "QUESTION",
            "scope": scope_id, "mcu_id": tr.mcu_id,
            "text": tr.text, "rule_ids": []
        }
        blockers = graph.setdefault("open_blockers", [])
        if not any(b.get("id") == blocker_id for b in blockers if isinstance(b, dict)):
            blockers.append(blocker)
        annotate_active_tasks(graph)
        sync_to_sqlite(graph)
        return {"operation": op.value, "blocker_id": blocker_id, "scope": scope_id, "mcu_id": tr.mcu_id}

    if op == DeltaOperation.COMMIT_NOTE:
        note = upsert_ambient_note(
            graph, {"id": f"commit_t{t_turn}_m{tr.mcu_id}", "text": tr.text, "turn": t_turn},
            current_turn=t_turn
        )
        return {"operation": op.value, "note_id": note["id"], "scope": scope_id, "mcu_id": tr.mcu_id}

    if op in (DeltaOperation.UPSERT_TASK, DeltaOperation.GRADUATE_TASK_TO_FACT):
        tasks = graph.setdefault("active_tasks", [])
        if parsed_mcu.get("completed") is True and parsed_mcu.get("task_id"):
            explicit_tid = str(parsed_mcu["task_id"])
            if any(isinstance(t, dict) and t.get("id") == explicit_tid for t in tasks):
                fact_r = graduate_task_to_fact(graph, explicit_tid, current_turn=t_turn)
                annotate_active_tasks(graph)
                sync_to_sqlite(graph)
                return {
                    "operation": DeltaOperation.GRADUATE_TASK_TO_FACT.value,
                    "rule_id": fact_r["id"], "scope": scope_id,
                    "mcu_id": tr.mcu_id
                }
        task_id = str(parsed_mcu.get("task_id") or parsed_mcu.get("id") or f"task_t{t_turn}_m{tr.mcu_id}")
        raw_deps = parsed_mcu.get("depends_on") if parsed_mcu.get("depends_on") is not None else lpayload.get("depends_on")
        deps_list = [str(d) for d in raw_deps if d] if isinstance(raw_deps, list) else list(ref_scopes)
        scope_obj = graph.get("scopes", {}).get(scope_id, {})
        owner_val = lpayload.get("owner") or parsed_mcu.get("owner") or (scope_obj.get("owner") if isinstance(scope_obj, dict) else None)
        stage_val = lpayload.get("stage") or parsed_mcu.get("stage") or (scope_obj.get("stage") if isinstance(scope_obj, dict) else None)
        task_entry: dict[str, Any] = {
            "id": task_id, "text": tr.text, "scope": scope_id,
            "condition": "; ".join(tr.conditions) if tr.conditions else None,
            "depends_on": deps_list, "governed_by": [r["id"] for _, r in existing_rules],
            "completed": bool(parsed_mcu.get("completed", False)), "turn": t_turn,
            "target_prop": tr.target_prop, "bound_op": tr.bound_op, "bound_val": tr.bound_val,
            "owner": str(owner_val).strip() if owner_val else None,
            "stage": str(stage_val).strip() if stage_val else None,
        }
        existing_t = next((t for t in tasks if isinstance(t, dict) and t.get("id") == task_id), None)
        if existing_t is not None:
            existing_t.update(task_entry)
        else:
            tasks.append(task_entry)
        _invalidate_scope_on_edit(graph, scope_id)
        if op == DeltaOperation.GRADUATE_TASK_TO_FACT:
            fact_r = graduate_task_to_fact(graph, task_id, current_turn=t_turn)
            annotate_active_tasks(graph)
            sync_to_sqlite(graph)
            return {"operation": op.value, "rule_id": fact_r["id"], "scope": scope_id, "mcu_id": tr.mcu_id}
        annotate_active_tasks(graph)
        sync_to_sqlite(graph)
        return {"operation": op.value, "task_id": task_id, "scope": scope_id, "mcu_id": tr.mcu_id}

    if op == DeltaOperation.ATTACH_RATIONALE and tr.matched_rule is not None and tr.matched_rule_scope is not None:
        return {
            "operation": op.value, "rule_id": str(tr.matched_rule["id"]),
            "scope": tr.matched_rule_scope, "mcu_id": tr.mcu_id
        }

    rule_payload: dict[str, Any] = {
        "type": tr.node_type, "facet": tr.facet, "text": tr.text, "target_prop": tr.target_prop,
        "bound_op": tr.bound_op, "bound_val": tr.bound_val, "conditions": tr.conditions,
        "is_exception": tr.is_exception, "refs": ref_scopes, "meta": tr.meta, "turn": t_turn,
    }
    if op == DeltaOperation.MARKOVIAN_OVERWRITE and tr.matched_rule is not None and tr.matched_rule_scope is not None:
        updated = markovian_overwrite_rule(graph, tr.matched_rule_scope, str(tr.matched_rule["id"]), rule_payload)
        annotate_active_tasks(graph)
        sync_to_sqlite(graph)
        return {
            "operation": op.value, "rule_id": updated["id"],
            "scope": tr.matched_rule_scope, "target_prop": tr.target_prop,
            "mcu_id": tr.mcu_id
        }

    existing_rids = {
        str(r["id"])
        for s in graph.get("scopes", {}).values() if isinstance(s, dict)
        for r in s.get("rules", []) if isinstance(r, dict) and r.get("id")
    }
    top_rules = graph.get("rules")
    if isinstance(top_rules, dict):
        existing_rids.update(str(k) for k in top_rules.keys() if k)
    elif isinstance(top_rules, list):
        existing_rids.update(str(r["id"]) for r in top_rules if isinstance(r, dict) and r.get("id"))
    next_num = 1 + max(
        (int(rid.rsplit("_", 1)[-1]) for rid in existing_rids if rid.rsplit("_", 1)[-1].isdigit()),
        default=len(existing_rids),
    )
    while f"r_{scope_id}_{next_num}" in existing_rids:
        next_num += 1
    rule_payload["id"] = str(parsed_mcu.get("id") or f"r_{scope_id}_{next_num}")
    new_rule = prune_dysfluency_and_upsert_rule(graph, scope_id, rule_payload)

    if op == DeltaOperation.LEX_SPECIALIS_EXCEPTION:
        note_str = "; ".join(tr.conditions) if tr.conditions else "exception override"
        for gen_r in tr.broader_rules:
            wire_lex_specialis_override(graph, new_rule, gen_r, note=note_str)
        annotate_active_tasks(graph)
        sync_to_sqlite(graph)
        return {
            "operation": op.value, "rule_id": new_rule["id"],
            "overrides": list(new_rule.get("overrides", [])), "scope": scope_id, "target_prop": tr.target_prop, "mcu_id": tr.mcu_id,
        }

    annotate_active_tasks(graph)
    sync_to_sqlite(graph)
    return {
        "operation": op.value, "rule_id": new_rule["id"],
        "scope": scope_id, "target_prop": tr.target_prop,
        "mcu_id": tr.mcu_id
    }
