"""Spec 23A: Pre-Flight Whiteboard Trial & MCU Collision Inspector (`preflight.py`)."""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any

from bowthruster.nlp import restore_ascii_dots
from bowthruster.regiment.intake_packet import compile_intake_packet
from bowthruster.whiteboard import (
    _ancestor_scopes, _inherited_rules_clash, _rules_clash, create_empty_context_graph,
    detect_frustration, ingest_intake_packet, resolve_mcu_scopes_and_refs,
)

MCU_STATUS_CLEAN, MCU_STATUS_NEEDS_CLARIFICATION = "CLEAN", "NEEDS_CLARIFICATION"
MCU_STATUS_HAS_OPEN_QUESTION, MCU_STATUS_FLAWED_IDEA, MCU_STATUS_RULE_CONFLICT = "HAS_OPEN_QUESTION", "FLAWED_IDEA", "RULE_CONFLICT"
VALID_MCU_PREFLIGHT_STATUSES = frozenset({
    MCU_STATUS_CLEAN, MCU_STATUS_NEEDS_CLARIFICATION, MCU_STATUS_HAS_OPEN_QUESTION, MCU_STATUS_FLAWED_IDEA, MCU_STATUS_RULE_CONFLICT,
})
FLAW_KIND_FACT_CONTRADICTION, FLAW_KIND_GOAL_SABOTAGE, FLAW_KIND_CRITIQUE_FLAGGED = "FACT_CONTRADICTION", "GOAL_SABOTAGE", "CRITIQUE_FLAGGED"
_STANDING_RULE_TYPES = frozenset({"PROHIBITION", "BOUND", "OBLIGATION", "POLICY"})
_META_ATTR_KEYS = frozenset({"updated_turn", "heading_path"})
_SBF_CONFLICT_PREFIXES = ("conflict_dep_", "conflict_flow_", "conflict_payload_")


class McuPreflightStatus(str, Enum):
    """Explicit discrete state enum for per-MCU pre-flight classification (Spec 23A §2.1–2.2)."""

    CLEAN = MCU_STATUS_CLEAN
    NEEDS_CLARIFICATION = MCU_STATUS_NEEDS_CLARIFICATION
    HAS_OPEN_QUESTION = MCU_STATUS_HAS_OPEN_QUESTION
    FLAWED_IDEA = MCU_STATUS_FLAWED_IDEA
    RULE_CONFLICT = MCU_STATUS_RULE_CONFLICT


class ExecutivePreflightError(ValueError):
    """Fail-closed error raised when pre-flight inputs violate Spec 23A contracts (§2.1)."""

    def __init__(self, code: str, errors: list[str]) -> None:
        self.code = code
        self.errors = list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


def _err(code: str, msg: str) -> ExecutivePreflightError:
    return ExecutivePreflightError(code, [msg])


def classify_mcu_status(
    has_unclear: bool, is_question: bool, has_clash: bool, has_flawed_idea: bool = False
) -> str:
    """Pure per-MCU status classifier governed by Bend 2 Laws 1–4 (`laws/bend_exec_23a/LAWS.bend`)."""
    if has_clash:
        return McuPreflightStatus.RULE_CONFLICT.value
    if has_flawed_idea:
        return McuPreflightStatus.FLAWED_IDEA.value
    if has_unclear:
        return McuPreflightStatus.NEEDS_CLARIFICATION.value
    if is_question:
        return McuPreflightStatus.HAS_OPEN_QUESTION.value
    return McuPreflightStatus.CLEAN.value


def is_preflight_clean(
    has_ambiguity: bool, has_open_questions: bool, has_rule_conflict: bool, has_flawed_idea: bool = False
) -> bool:
    """Pure turn-level cleanliness gate governed by Bend 2 Law 5 (`preflight_clean_iff_no_blockers`)."""
    return not (bool(has_ambiguity) or bool(has_open_questions) or bool(has_rule_conflict) or bool(has_flawed_idea))


def normalize_intake_packet(packet_or_phase3: dict[str, Any], *, turn_index: int = 1) -> dict[str, Any]:
    """Normalizes an `IntakePacket` or `Phase3Result` dict into a canonical deep-copied `IntakePacket` (Spec 23A §2.3)."""
    if not isinstance(packet_or_phase3, dict):
        raise _err("preflight_invalid_packet", f"packet_or_phase3 must be a dict, got {type(packet_or_phase3).__name__}")
    if isinstance(turn_index, bool) or not isinstance(turn_index, int) or turn_index < 1:
        raise _err("preflight_invalid_packet", f"turn_index must be a positive int, got {turn_index!r}")

    if "parsed_mcus" in packet_or_phase3:
        raw_pkt = packet_or_phase3
    elif isinstance(packet_or_phase3.get("intake_packet"), dict):
        raw_pkt = packet_or_phase3["intake_packet"]
    elif "mcus" in packet_or_phase3 and "propositions" in packet_or_phase3:
        try:
            raw_pkt = compile_intake_packet(packet_or_phase3, turn_index=turn_index)
        except Exception as exc:
            raise _err("preflight_invalid_packet", str(exc)) from exc
    else:
        raise _err("preflight_invalid_packet", "packet_or_phase3 must contain 'parsed_mcus', 'intake_packet', or ('mcus' and 'propositions')")

    parsed_mcus = raw_pkt.get("parsed_mcus")
    if not isinstance(parsed_mcus, list) or not all(isinstance(m, dict) for m in parsed_mcus):
        raise _err("preflight_invalid_packet", "IntakePacket['parsed_mcus'] must be a list of dicts")

    raw_turn = raw_pkt.get("turn_index", turn_index)
    if isinstance(raw_turn, bool) or not isinstance(raw_turn, int) or raw_turn < 1:
        raise _err("preflight_invalid_packet", f"packet turn_index must be a positive int, got {raw_turn!r}")

    doc_tables = copy.deepcopy(raw_pkt["doc_tables"]) if isinstance(raw_pkt.get("doc_tables"), list) else []
    mcus_copy = copy.deepcopy(parsed_mcus)
    raw_cqs, raw_oqs = raw_pkt.get("clarification_questions"), raw_pkt.get("open_questions")
    cqs = [str(q).strip() for q in raw_cqs if str(q).strip()] if isinstance(raw_cqs, list) else []
    oqs = [str(q).strip() for q in raw_oqs if str(q).strip()] if isinstance(raw_oqs, list) else []

    for pm in mcus_copy:
        for span in pm.get("unclear_spans", []):
            if (s_clean := str(span).strip()) and s_clean not in cqs:
                cqs.append(s_clean)
        if pm.get("type") == "QUESTION":
            if (q_txt := str(pm.get("text") or pm.get("canonical_text") or "").strip()) and q_txt not in oqs:
                oqs.append(q_txt)

    if isinstance(raw_pkt.get("grounding_status"), str) and raw_pkt["grounding_status"].strip():
        grounding_status = raw_pkt["grounding_status"].strip()
    elif cqs or any(
        bool(pm.get("unclear_spans")) or pm.get("grounding_status") == "NEEDS_CLARIFICATION"
        for pm in mcus_copy
    ):
        grounding_status = "NEEDS_CLARIFICATION"
    elif oqs or any(
        pm.get("type") == "QUESTION" or pm.get("grounding_status") == "HAS_OPEN_QUESTIONS"
        for pm in mcus_copy
    ):
        grounding_status = "HAS_OPEN_QUESTIONS"
    else:
        grounding_status = "GROUNDED"

    return {
        "turn_index": int(raw_turn), "doc_tables": doc_tables, "parsed_mcus": mcus_copy,
        "grounding_status": grounding_status, "is_locked": bool(raw_pkt.get("is_locked", grounding_status == "GROUNDED")),
        "clarification_questions": cqs, "open_questions": oqs,
    }


def _detach_sqlite_store(graph: dict[str, Any]) -> None:
    try:
        object.__setattr__(graph, "_sqlite_store", None)
    except (AttributeError, TypeError):
        pass
    graph.pop("_sqlite_store", None)


def clone_whiteboard_for_trial(whiteboard_graph: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-copies `whiteboard_graph` with `_sqlite_store` detached so trial runs never touch live state or SQLite (Spec 23A §2.4)."""
    if whiteboard_graph is None:
        trial = create_empty_context_graph()
        _detach_sqlite_store(trial)
        return trial
    if not isinstance(whiteboard_graph, dict):
        raise _err("preflight_invalid_whiteboard", f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}")

    had_attr = "_sqlite_store" in getattr(whiteboard_graph, "__dict__", {})
    saved_attr = getattr(whiteboard_graph, "_sqlite_store", None) if had_attr else None
    had_key, saved_key = "_sqlite_store" in whiteboard_graph, whiteboard_graph.get("_sqlite_store")
    try:
        _detach_sqlite_store(whiteboard_graph)
        trial_graph = copy.deepcopy(whiteboard_graph)
    finally:
        if had_attr:
            try:
                object.__setattr__(whiteboard_graph, "_sqlite_store", saved_attr)
            except (AttributeError, TypeError):
                pass
        if had_key:
            whiteboard_graph["_sqlite_store"] = saved_key
    _detach_sqlite_store(trial_graph)
    return trial_graph


def _is_in_scope_family(cand_sid: str, scope_id: str, root_sid: str, ancestors: set[str]) -> bool:
    return cand_sid in (scope_id, "global_defaults") or cand_sid.split("::")[0] in (root_sid, "global_defaults") or cand_sid in ancestors


def _conditions_compatible(pmcu: dict[str, Any], r_old: dict[str, Any]) -> bool:
    conds_a = {str(c).strip() for c in pmcu.get("conditions", []) if str(c).strip()}
    conds_b = {str(c).strip() for c in r_old.get("conditions", []) if str(c).strip()}
    return not (conds_a and conds_b and not (conds_a & conds_b))


def _normalized_rule_view(entry: dict[str, Any], *, default_type: str | None = None) -> dict[str, Any]:
    rtype = default_type or str(entry.get("type") or "OBLIGATION")
    b_val, b_op = entry.get("bound_val"), entry.get("bound_op")
    if b_op is None and b_val is not None:
        b_op = "!=" if rtype == "PROHIBITION" else "=="
    return {
        "type": rtype, "target_prop": entry.get("target_prop"),
        "bound_op": b_op, "bound_val": b_val,
        "conditions": list(entry.get("conditions", []))
    }


def _check_standing_rule_clash(pmcu: dict[str, Any], r_old: dict[str, Any]) -> bool:
    r_type = str(r_old.get("type") or "")
    if r_type not in _STANDING_RULE_TYPES:
        return False
    if bool(pmcu.get("is_exception")) or (r_old.get("id") in (pmcu.get("overrides") or [])) or not _conditions_compatible(pmcu, r_old):
        return False

    pmcu_type = str(pmcu.get("type") or "OBLIGATION")
    tp_new, tp_old = pmcu.get("target_prop"), r_old.get("target_prop")
    pmcu_view, old_view = _normalized_rule_view(pmcu), _normalized_rule_view(r_old)
    same_prop_clash = tp_new is not None and tp_old == tp_new and (
        _inherited_rules_clash(pmcu_view, old_view, False)
        or (
            r_type == "BOUND" and pmcu.get("bound_val") is not None
            and _rules_clash(_normalized_rule_view(pmcu, default_type="BOUND"), old_view)
        )
    )
    if pmcu_type == "ACTION":
        if r_type == "PROHIBITION" and (tp_old is None or tp_old == tp_new):
            if tp_old is None or pmcu.get("bound_val") is None or r_old.get("bound_val") is None or _inherited_rules_clash(pmcu_view, old_view, False):
                return True
        return bool(same_prop_clash)
    return bool(pmcu_type in _STANDING_RULE_TYPES and not pmcu.get("superseded_literals") and same_prop_clash)


def _check_fact_or_goal_clash(pmcu: dict[str, Any], r_ref: dict[str, Any], *, ref_kind: str) -> bool:
    tp_new = pmcu.get("target_prop")
    if tp_new is None or r_ref.get("target_prop") != tp_new or bool(pmcu.get("is_exception")) or not _conditions_compatible(pmcu, r_ref):
        return False
    pmcu_type = str(pmcu.get("type") or "OBLIGATION")
    if ref_kind == "FACT" and pmcu_type not in ("PROPOSAL", "ACTION", "OBLIGATION", "BOUND", "PROHIBITION", "POLICY"):
        return False
    if ref_kind == "GOAL" and pmcu_type not in (
        "PROPOSAL", "ACTION", "OBLIGATION",
        "BOUND", "PROHIBITION", "POLICY", "GOAL"
    ):
        return False

    pmcu_view, ref_view = _normalized_rule_view(pmcu), _normalized_rule_view(r_ref, default_type="OBLIGATION")
    if _inherited_rules_clash(pmcu_view, ref_view, False):
        return True
    if pmcu.get("bound_val") is not None and r_ref.get("bound_val") is not None:
        if _rules_clash(
            _normalized_rule_view(pmcu, default_type="BOUND"),
            _normalized_rule_view(r_ref, default_type="BOUND")
        ):
            return True
    if ref_kind == "GOAL" and (pmcu_type == "PROHIBITION" or pmcu_view.get("bound_op") == "!=") != (ref_view.get("bound_op") == "!="):
        if pmcu.get("bound_val") is None or r_ref.get("bound_val") is None:
            return True
    return False


def _collect_rules_by_id(graph: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        str(r["id"]): (sid, r)
        for sid, snode in graph.get("scopes", {}).items() if isinstance(snode, dict)
        for r in snode.get("rules", []) if isinstance(r, dict) and r.get("id")
    }


def _make_flaw(
    flaw_id: str, mcu_id: int, flaw_kind: str, scope: str, target_prop: Any,
    incoming_text: str, basis_id: str, basis_text: str, description: str,
) -> dict[str, Any]:
    return {
        "flaw_id": flaw_id, "mcu_id": mcu_id, "flaw_kind": flaw_kind, "scope": scope, "target_prop": target_prop,
        "incoming_text": incoming_text, "basis_id": basis_id, "basis_text": basis_text, "description": description,
    }


def _make_conflict(
    cid: str, mcu_id: int, scope: str, target_prop: Any, rule_ids: list[str],
    old_rid: str, old_text: str, incoming_text: str, desc: str, base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = copy.deepcopy(base) if base else {
        "id": cid, "type": "CONFLICT", "scope": scope,
        "target_prop": target_prop, "rule_ids": rule_ids,
        "text": desc
    }
    out.update({
        "conflict_id": cid, "mcu_id": mcu_id,
        "existing_rule_id": old_rid,
        "existing_rule_text": old_text,
        "incoming_text": incoming_text,
        "description": desc
    })
    return out


def inspect_turn_preflight(
    packet_or_phase3: dict[str, Any], whiteboard_graph: dict[str, Any] | None = None, *,
    turn_index: int | None = None, flawed_idea_critiques: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Executes the non-destructive Spec 23A pre-flight inspection on a detached trial Whiteboard (§2.5)."""
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        raise _err("preflight_invalid_whiteboard", f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}")
    if not isinstance(packet_or_phase3, dict):
        raise _err("preflight_invalid_packet", f"packet_or_phase3 must be a dict, got {type(packet_or_phase3).__name__}")
    if flawed_idea_critiques is not None and not isinstance(flawed_idea_critiques, list):
        raise _err("preflight_invalid_critiques", f"flawed_idea_critiques must be a list or None, got {type(flawed_idea_critiques).__name__}")

    prev_turn = max(
        int(whiteboard_graph.get("turn", 0) or 0),
        int(whiteboard_graph.get("turn_index", 0) or 0)
    ) if isinstance(whiteboard_graph, dict) else 0
    raw_t = turn_index if turn_index is not None else packet_or_phase3.get("turn_index")
    if raw_t is None and isinstance(packet_or_phase3.get("intake_packet"), dict):
        raw_t = packet_or_phase3["intake_packet"].get("turn_index")
    if raw_t is not None and (isinstance(raw_t, bool) or not isinstance(raw_t, int) or raw_t < 1):
        raise _err("preflight_invalid_packet", f"turn_index must be a positive int, got {raw_t!r}")
    eff_turn = int(raw_t) if raw_t is not None else max(1, prev_turn + 1)

    packet = normalize_intake_packet(packet_or_phase3, turn_index=eff_turn)
    packet["turn_index"] = eff_turn

    base_graph = clone_whiteboard_for_trial(whiteboard_graph)
    trial_graph = clone_whiteboard_for_trial(base_graph)
    pre_conflict_ids = {str(c["id"]) for c in detect_frustration(base_graph) if isinstance(c, dict) and c.get("id")}
    base_rules_by_id = _collect_rules_by_id(base_graph)

    trial_res = ingest_intake_packet(trial_graph, packet)
    new_frustrations = [
        copy.deepcopy(c) for c in trial_res.get("conflicts", [])
        if isinstance(c, dict) and str(c.get("id", "")) not in pre_conflict_ids
    ]
    trial_rules_by_id = _collect_rules_by_id(trial_graph)
    all_rules_by_id = {**base_rules_by_id, **trial_rules_by_id}
    delta_log = [d for d in trial_res.get("delta_log", []) if isinstance(d, dict)]
    delta_by_mcu = {d["mcu_id"]: d for d in reversed(delta_log) if "mcu_id" in d}

    mcu_findings: list[dict[str, Any]] = []
    clean_mcus, ambiguous_mcus, question_mcus, conflicting_mcus, flawed_mcus = [], [], [], [], []
    conflicts, flawed_ideas = [], []
    seen_conflict_keys: set[tuple[int, str]] = set()
    seen_flaw_keys: set[tuple[int, str, str]] = set()
    prior_turn_rules: list[tuple[str, dict[str, Any]]] = []

    for idx, pmcu in enumerate(packet["parsed_mcus"]):
        mcu_id = int(pmcu.get("mcu_id", idx + 1))
        mcu_text = restore_ascii_dots(str(pmcu.get("text") or pmcu.get("canonical_text") or "")).strip()
        delta_entry = delta_by_mcu.get(pmcu.get("mcu_id")) or delta_by_mcu.get(mcu_id)
        scope_id = str(delta_entry["scope"]) if (isinstance(delta_entry, dict) and delta_entry.get("scope")) else resolve_mcu_scopes_and_refs(
            clone_whiteboard_for_trial(base_graph),
            pmcu
        )[0]

        root_sid = scope_id.split("::")[0]
        ancestors = _ancestor_scopes(base_graph.get("scopes", {}), scope_id) | _ancestor_scopes(trial_graph.get("scopes", {}), scope_id)
        cand_base_rules = [
            (sid, r) for sid, snode in base_graph.get("scopes", {}).items()
            if isinstance(snode, dict) and _is_in_scope_family(sid, scope_id, root_sid, ancestors)
            for r in snode.get("rules", []) if isinstance(r, dict) and r.get("id")
        ]
        cand_all_rules = cand_base_rules + [
            (sid, r) for sid, r in prior_turn_rules
            if _is_in_scope_family(sid, scope_id, root_sid, ancestors)
        ]

        mcu_conflicts, mcu_flaws = [], []
        delta_rule_id = str(delta_entry["rule_id"]) if isinstance(delta_entry, dict) and delta_entry.get("rule_id") else None
        delta_op = str(delta_entry.get("operation") or "") if isinstance(delta_entry, dict) else ""
        delta_fid = str(delta_entry.get("flow_id") or "") if isinstance(delta_entry, dict) else ""
        lkind = str(pmcu.get("lattice_kind") or "").strip().upper()
        is_flow_mcu = delta_op == "UPSERT_FLOW" or lkind == "FLOW"
        is_comp_mcu = delta_op == "UPSERT_COMPONENT" or lkind == "COMPONENT"
        pmcu_type, target_prop = str(pmcu.get("type") or "OBLIGATION"), pmcu.get("target_prop")

        for frust in new_frustrations:
            cid = str(frust.get("id", ""))
            rids = [str(x) for x in frust.get("rule_ids", [])]
            if delta_rule_id and pmcu_type not in ("FACT", "GOAL") and delta_rule_id in rids:
                other_rid = next((x for x in rids if x != delta_rule_id), delta_rule_id)
                other_rule = all_rules_by_id.get(other_rid, ("", {}))[1]
                if str(other_rule.get("type") or "") in _STANDING_RULE_TYPES and (mcu_id, other_rid) not in seen_conflict_keys:
                    seen_conflict_keys.add((mcu_id, other_rid))
                    c_entry = _make_conflict(
                        cid or f"conflict_m{mcu_id}_{other_rid}",
                        mcu_id, scope_id, target_prop, rids,
                        other_rid, str(other_rule.get("text", "")),
                        mcu_text, str(frust.get("text", "")),
                        frust
                    )
                    mcu_conflicts.append(c_entry)
                    conflicts.append(c_entry)
            elif (is_flow_mcu or is_comp_mcu) and cid.startswith(_SBF_CONFLICT_PREFIXES):
                matches_sbf = (
                    (delta_fid in rids if delta_fid else frust.get("scope") == root_sid)
                    if is_flow_mcu
                    else (frust.get("scope") == root_sid or cid.endswith(f"_{scope_id}"))
                )
                if matches_sbf and (mcu_id, cid) not in seen_conflict_keys:
                    seen_conflict_keys.add((mcu_id, cid))
                    ref_rid = rids[0] if rids else cid
                    ref_rule = all_rules_by_id.get(ref_rid, ("", {}))[1]
                    c_entry = _make_conflict(
                        cid, mcu_id, scope_id, target_prop
                        or frust.get("target_prop"), rids or [cid],
                        ref_rid, str(ref_rule.get("text") or frust.get("text") or ""),
                        mcu_text, str(frust.get("text", "")),
                        frust
                    )
                    mcu_conflicts.append(c_entry)
                    conflicts.append(c_entry)

        for _, r_old in cand_base_rules:
            old_rid = str(r_old.get("id", ""))
            if old_rid and (mcu_id, old_rid) not in seen_conflict_keys and _check_standing_rule_clash(pmcu, r_old):
                seen_conflict_keys.add((mcu_id, old_rid))
                cid = f"conflict_t{eff_turn}_m{mcu_id}_{old_rid}"
                desc = f"MCU {mcu_id} ({pmcu_type} on target_prop={target_prop!r}) conflicts with standing {r_old.get('type')} rule {old_rid!r}: {r_old.get('text', '')}"
                rids = [old_rid] if not delta_rule_id else sorted({old_rid, delta_rule_id})
                c_entry = _make_conflict(
                    cid, mcu_id, scope_id, target_prop
                    if target_prop is not None else r_old.get("target_prop"),
                    rids, old_rid, str(r_old.get("text", "")),
                    mcu_text, desc
                )
                mcu_conflicts.append(c_entry)
                conflicts.append(c_entry)

        for _, r_ref in cand_all_rules:
            ref_id, ref_type = str(r_ref.get("id", "")), str(r_ref.get("type") or "")
            if ref_type in ("FACT", "GOAL") and _check_fact_or_goal_clash(pmcu, r_ref, ref_kind=ref_type):
                f_kind = FLAW_KIND_FACT_CONTRADICTION if ref_type == "FACT" else FLAW_KIND_GOAL_SABOTAGE
                verb = "contradicts established FACT" if ref_type == "FACT" else "sabotages active GOAL"
                if (mcu_id, f_kind, ref_id) not in seen_flaw_keys:
                    seen_flaw_keys.add((mcu_id, f_kind, ref_id))
                    desc = f"MCU {mcu_id} {verb} {ref_id!r} on {target_prop!r}: {r_ref.get('text', '')}"
                    f_entry = _make_flaw(
                        f"flaw_t{eff_turn}_m{mcu_id}_{ref_id}",
                        mcu_id, f_kind, scope_id, target_prop,
                        mcu_text, ref_id, str(r_ref.get("text", "")),
                        desc
                    )
                    mcu_flaws.append(f_entry)
                    flawed_ideas.append(f_entry)

        if target_prop is not None and pmcu_type in ("PROPOSAL", "ACTION", "OBLIGATION", "BOUND", "PROHIBITION"):
            for g_src in (base_graph, trial_graph):
                for sid, snode in g_src.get("scopes", {}).items():
                    if not isinstance(snode, dict) or not _is_in_scope_family(sid, scope_id, root_sid, ancestors):
                        continue
                    attrs = snode.get("attributes", {})
                    if isinstance(attrs, dict) and target_prop in attrs and target_prop not in _META_ATTR_KEYS:
                        attr_val = attrs[target_prop]
                        synth_fact = {
                            "type": "FACT", "target_prop": target_prop,
                            "bound_op": "==", "bound_val": attr_val
                        }
                        basis_id = f"attr:{sid}.{target_prop}"
                        if (mcu_id, FLAW_KIND_FACT_CONTRADICTION, basis_id) not in seen_flaw_keys and _rules_clash(
                            _normalized_rule_view(pmcu, default_type="BOUND"),
                            synth_fact
                        ):
                            seen_flaw_keys.add((mcu_id, FLAW_KIND_FACT_CONTRADICTION, basis_id))
                            basis_txt = f"{sid}.{target_prop} == {attr_val!r}"
                            f_entry = _make_flaw(
                                f"flaw_t{eff_turn}_m{mcu_id}_attr_{target_prop}",
                                mcu_id, FLAW_KIND_FACT_CONTRADICTION,
                                scope_id, target_prop, mcu_text, basis_id,
                                basis_txt, f"MCU {mcu_id} contradicts table attribute {basis_txt}"
                            )
                            mcu_flaws.append(f_entry)
                            flawed_ideas.append(f_entry)

        flawed_reason = pmcu.get("flawed_reason")
        if isinstance(flawed_reason, str) and flawed_reason.strip():
            reason_clean = restore_ascii_dots(flawed_reason).strip()
            basis_id = str(pmcu.get("flawed_basis_id") or f"mcu_{mcu_id}_flawed_reason")
            if (mcu_id, FLAW_KIND_CRITIQUE_FLAGGED, basis_id) not in seen_flaw_keys:
                seen_flaw_keys.add((mcu_id, FLAW_KIND_CRITIQUE_FLAGGED, basis_id))
                f_entry = _make_flaw(
                    f"flaw_t{eff_turn}_m{mcu_id}_critique",
                    mcu_id, FLAW_KIND_CRITIQUE_FLAGGED,
                    scope_id, target_prop, mcu_text, basis_id,
                    str(pmcu.get("flawed_basis_text") or reason_clean),
                    reason_clean
                )
                mcu_flaws.append(f_entry)
                flawed_ideas.append(f_entry)

        for c_idx, crit in enumerate(flawed_idea_critiques or []):
            if isinstance(crit, dict) and crit.get("mcu_id") == mcu_id:
                f_kind = str(crit.get("flaw_kind") or FLAW_KIND_CRITIQUE_FLAGGED)
                basis_id = str(crit.get("basis_id") or f"critique_{c_idx + 1}")
                if (mcu_id, f_kind, basis_id) not in seen_flaw_keys:
                    seen_flaw_keys.add((mcu_id, f_kind, basis_id))
                    desc = restore_ascii_dots(str(crit.get("description") or crit.get("reason") or crit.get("basis_text") or "Critique flagged flawed premise")).strip()
                    f_entry = _make_flaw(
                        str(crit.get("flaw_id") or f"flaw_t{eff_turn}_m{mcu_id}_crit_{c_idx + 1}"),
                        mcu_id, f_kind, str(crit.get("scope") or scope_id),
                        crit.get("target_prop", target_prop),
                        str(crit.get("incoming_text") or mcu_text),
                        basis_id, str(crit.get("basis_text") or desc),
                        desc
                    )
                    mcu_flaws.append(f_entry)
                    flawed_ideas.append(f_entry)

        has_unclear = bool(pmcu.get("unclear_spans")) or pmcu.get("grounding_status") == "NEEDS_CLARIFICATION"
        is_question = pmcu_type == "QUESTION" or pmcu.get("grounding_status") == "HAS_OPEN_QUESTIONS"
        status = classify_mcu_status(has_unclear, is_question, bool(mcu_conflicts), bool(mcu_flaws))

        finding = {
            "mcu_id": mcu_id, "status": status, "type": pmcu_type, "text": mcu_text, "scope": scope_id,
            "target_prop": target_prop, "unclear_spans": list(pmcu.get("unclear_spans", [])),
            "conflicts": mcu_conflicts, "flawed_ideas": mcu_flaws,
        }
        mcu_findings.append(finding)
        buckets = {
            MCU_STATUS_CLEAN: clean_mcus, MCU_STATUS_NEEDS_CLARIFICATION: ambiguous_mcus,
            MCU_STATUS_HAS_OPEN_QUESTION: question_mcus, MCU_STATUS_RULE_CONFLICT: conflicting_mcus,
            MCU_STATUS_FLAWED_IDEA: flawed_mcus,
        }
        buckets[status].append(finding)

        if delta_rule_id and delta_rule_id in trial_rules_by_id:
            prior_turn_rules.append(trial_rules_by_id[delta_rule_id])
        elif pmcu_type in ("FACT", "GOAL") and target_prop is not None:
            prior_turn_rules.append((scope_id, {
                "id": delta_rule_id or f"turn{eff_turn}_m{mcu_id}", "type": pmcu_type, "text": mcu_text,
                "target_prop": target_prop, "bound_op": pmcu.get("bound_op"),
                "bound_val": pmcu.get("bound_val"), "conditions": list(pmcu.get("conditions", [])),
            }))

    has_ambiguity = bool(ambiguous_mcus) or bool(packet.get("clarification_questions")) or packet.get("grounding_status") == "NEEDS_CLARIFICATION"
    has_open_questions = bool(question_mcus) or bool(packet.get("open_questions")) or packet.get("grounding_status") == "HAS_OPEN_QUESTIONS"
    has_rule_conflict, has_flawed_idea = bool(conflicts), bool(flawed_ideas)

    return {
        "turn_index": eff_turn, "intake_packet": packet,
        "is_clean": is_preflight_clean(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea),
        "has_ambiguity": has_ambiguity, "has_open_questions": has_open_questions,
        "has_rule_conflict": has_rule_conflict, "has_flawed_idea": has_flawed_idea,
        "mcu_findings": mcu_findings, "clean_mcus": clean_mcus, "ambiguous_mcus": ambiguous_mcus,
        "question_mcus": question_mcus, "conflicting_mcus": conflicting_mcus, "flawed_mcus": flawed_mcus,
        "conflicts": conflicts, "flawed_ideas": flawed_ideas, "new_frustrations": new_frustrations,
        "clarification_questions": list(packet.get("clarification_questions", [])),
        "open_questions": list(packet.get("open_questions", [])), "trial_ingestion": trial_res,
    }
