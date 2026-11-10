"""Spec 23A: Pre-Flight Whiteboard Trial & MCU Collision Inspector (`preflight.py`)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from typing import Any

from bowthruster.nlp import restore_ascii_dots
from bowthruster.regiment.intake_packet import compile_intake_packet
from bowthruster.whiteboard import (
    _ancestor_scopes, _inherited_rules_clash, _rules_clash, create_empty_context_graph,
    detect_frustration, ingest_intake_packet, resolve_mcu_scopes_and_refs)

MCU_STATUS_CLEAN, MCU_STATUS_NEEDS_CLARIFICATION = "CLEAN", "NEEDS_CLARIFICATION"
MCU_STATUS_HAS_OPEN_QUESTION, MCU_STATUS_FLAWED_IDEA = "HAS_OPEN_QUESTION", "FLAWED_IDEA"
MCU_STATUS_RULE_CONFLICT = "RULE_CONFLICT"
VALID_MCU_PREFLIGHT_STATUSES = frozenset({MCU_STATUS_CLEAN, MCU_STATUS_NEEDS_CLARIFICATION,
    MCU_STATUS_HAS_OPEN_QUESTION, MCU_STATUS_FLAWED_IDEA, MCU_STATUS_RULE_CONFLICT})
FLAW_KIND_FACT_CONTRADICTION, FLAW_KIND_GOAL_SABOTAGE = "FACT_CONTRADICTION", "GOAL_SABOTAGE"
FLAW_KIND_CRITIQUE_FLAGGED = "CRITIQUE_FLAGGED"
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
        self.code, self.errors = code, list(errors)
        super().__init__(f"{code}: {', '.join(self.errors)}")

def _err(code: str, msg: str) -> ExecutivePreflightError:
    return ExecutivePreflightError(code, [msg])

@dataclass
class _McuContext:
    pmcu: dict[str, Any]
    mcu_id: int
    mcu_text: str
    scope_id: str
    root_sid: str
    ancestors: set[str]
    delta_entry: dict[str, Any] | None
    eff_turn: int

def classify_mcu_status(has_unclear: bool, is_question: bool, has_clash: bool, has_flawed_idea: bool = False) -> str:
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
    has_ambiguity: bool, has_open_questions: bool, has_rule_conflict: bool, has_flawed_idea: bool = False) -> bool:
    """Pure turn-level cleanliness gate governed by Bend 2 Law 5 (`preflight_clean_iff_no_blockers`)."""
    return not (bool(has_ambiguity) or bool(has_open_questions) or bool(has_rule_conflict) or bool(has_flawed_idea))

def normalize_intake_packet(packet_or_phase3: dict[str, Any], *, turn_index: int = 1) -> dict[str, Any]:
    """Normalizes an `IntakePacket` or `Phase3Result` dict into a canonical deep-copied `IntakePacket`."""
    if not isinstance(packet_or_phase3, dict):
        raise _err("preflight_invalid_packet",
            f"packet_or_phase3 must be a dict, got {type(packet_or_phase3).__name__}")
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
        msg = "packet_or_phase3 must contain 'parsed_mcus', 'intake_packet', or ('mcus' and 'propositions')"
        raise _err("preflight_invalid_packet", msg)
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
        pm["text"] = str(pm.get("text", pm.get("canonical_text", "")) or "")
        for span in pm.get("unclear_spans", []):
            if (s_clean := str(span).strip()) and s_clean not in cqs:
                cqs.append(s_clean)
        if pm.get("type") == "QUESTION" and (q_txt := pm["text"].strip()) and q_txt not in oqs:
            oqs.append(q_txt)
    if isinstance(raw_pkt.get("grounding_status"), str) and raw_pkt["grounding_status"].strip():
        grounding_status = raw_pkt["grounding_status"].strip()
    elif cqs or any(bool(pm.get("unclear_spans")) or pm.get("grounding_status") == "NEEDS_CLARIFICATION" for pm
        in mcus_copy):
        grounding_status = "NEEDS_CLARIFICATION"
    elif oqs or any(pm.get("type") == "QUESTION" or pm.get("grounding_status") == "HAS_OPEN_QUESTIONS" for pm
        in mcus_copy):
        grounding_status = "HAS_OPEN_QUESTIONS"
    else:
        grounding_status = "GROUNDED"
    is_locked = bool(raw_pkt.get("is_locked", grounding_status == "GROUNDED"))
    return {"turn_index": int(raw_turn), "doc_tables": doc_tables, "parsed_mcus": mcus_copy,
        "grounding_status": grounding_status, "is_locked": is_locked,
        "clarification_questions": cqs, "open_questions": oqs}

def _detach_sqlite_store(graph: dict[str, Any]) -> None:
    try:
        object.__setattr__(graph, "_sqlite_store", None)
    except (AttributeError, TypeError):
        pass
    graph.pop("_sqlite_store", None)

def clone_whiteboard_for_trial(whiteboard_graph: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-copies `whiteboard_graph` with `_sqlite_store` detached so trial runs never touch live state."""
    if whiteboard_graph is None:
        trial = create_empty_context_graph()
        _detach_sqlite_store(trial)
        return trial
    if not isinstance(whiteboard_graph, dict):
        raise _err("preflight_invalid_whiteboard",
            f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}")
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
    return (cand_sid in (scope_id, "global_defaults") or cand_sid.split("::")[0] in (root_sid, "global_defaults")
        or cand_sid in ancestors)

def _conditions_compatible(pmcu: dict[str, Any], r_old: dict[str, Any]) -> bool:
    conds_a = {str(c).strip() for c in pmcu.get("conditions", []) if str(c).strip()}
    conds_b = {str(c).strip() for c in r_old.get("conditions", []) if str(c).strip()}
    return not (conds_a and conds_b and not (conds_a & conds_b))

def _normalized_rule_view(entry: dict[str, Any], *, default_type: str | None = None) -> dict[str, Any]:
    rtype = default_type or str(entry.get("type") or "OBLIGATION")
    b_val, b_op = entry.get("bound_val"), entry.get("bound_op")
    if b_op is None and b_val is not None:
        b_op = "!=" if rtype == "PROHIBITION" else "=="
    return {"type": rtype, "target_prop": entry.get("target_prop"),
        "bound_op": b_op, "bound_val": b_val, "conditions": list(entry.get("conditions", []))}

def _check_standing_rule_clash(pmcu: dict[str, Any], r_old: dict[str, Any]) -> bool:
    r_type = str(r_old.get("type") or "")
    if r_type not in _STANDING_RULE_TYPES:
        return False
    if bool(pmcu.get("is_exception")) or r_old.get("id") in (pmcu.get("overrides") or []):
        return False
    if not _conditions_compatible(pmcu, r_old):
        return False
    pmcu_type = str(pmcu.get("type") or "OBLIGATION")
    tp_new, tp_old = pmcu.get("target_prop"), r_old.get("target_prop")
    pmcu_view, old_view = _normalized_rule_view(pmcu), _normalized_rule_view(r_old)
    same_prop_clash = tp_new is not None and tp_old == tp_new and (_inherited_rules_clash(pmcu_view, old_view, False)
        or (r_type == "BOUND" and pmcu.get("bound_val") is not None and _rules_clash(_normalized_rule_view(pmcu,
            default_type="BOUND"), old_view))
        )
    if pmcu_type == "ACTION":
        if r_type == "PROHIBITION" and (tp_old is None or tp_old == tp_new):
            missing_bval = tp_old is None or pmcu.get("bound_val") is None or r_old.get("bound_val") is None
            if missing_bval or _inherited_rules_clash(pmcu_view, old_view, False):
                return True
        return bool(same_prop_clash)
    return bool(pmcu_type in _STANDING_RULE_TYPES and not pmcu.get("superseded_literals") and same_prop_clash)

def _check_fact_or_goal_clash(pmcu: dict[str, Any], r_ref: dict[str, Any], *, ref_kind: str) -> bool:
    tp_new = pmcu.get("target_prop")
    prop_mismatch = tp_new is None or r_ref.get("target_prop") != tp_new or bool(pmcu.get("is_exception"))
    if prop_mismatch or not _conditions_compatible(pmcu, r_ref):
        return False
    pmcu_type = str(pmcu.get("type") or "OBLIGATION")
    allowed = ("PROPOSAL", "ACTION", "OBLIGATION", "BOUND", "PROHIBITION", "POLICY")
    if (ref_kind == "FACT" and pmcu_type not in allowed) or (ref_kind == "GOAL" and pmcu_type not in (*allowed,
        "GOAL")):
        return False
    pmcu_view, ref_view = _normalized_rule_view(pmcu), _normalized_rule_view(r_ref, default_type="OBLIGATION")
    if _inherited_rules_clash(pmcu_view, ref_view, False):
        return True
    if pmcu.get("bound_val") is not None and r_ref.get("bound_val") is not None:
        if _rules_clash(_normalized_rule_view(pmcu, default_type="BOUND"), _normalized_rule_view(r_ref,
            default_type="BOUND")):
            return True
    is_neg = pmcu_type == "PROHIBITION" or pmcu_view.get("bound_op") == "!="
    if ref_kind == "GOAL" and is_neg != (ref_view.get("bound_op") == "!="):
        return pmcu.get("bound_val") is None or r_ref.get("bound_val") is None
    return False

def _collect_rules_by_id(graph: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    return {str(r["id"]): (sid, r) for sid, snode in graph.get("scopes", {}).items() if bool(snode)
        for r in snode.get("rules", []) if r.get("id")}

def _make_flaw(ctx: _McuContext, flaw_id: str, flaw_kind: str, basis_id: str, basis_text: str,
    description: str, *, scope: str | None = None, target_prop: Any = ...) -> dict[str, Any]:
    return {"flaw_id": flaw_id, "mcu_id": ctx.mcu_id, "flaw_kind": flaw_kind, "scope": scope or ctx.scope_id,
        "target_prop": ctx.pmcu.get("target_prop") if target_prop is ... else target_prop,
        "incoming_text": ctx.mcu_text, "basis_id": basis_id, "basis_text": basis_text, "description": description}

def _make_conflict(ctx: _McuContext, cid: str, rule_ids: list[str], old_rid: str, old_text: str,
    desc: str, base: dict[str, Any] | None = None, target_prop: Any = ...) -> dict[str, Any]:
    eff_prop = ctx.pmcu.get("target_prop") if target_prop is ... else target_prop
    out = copy.deepcopy(base) if base else {"id": cid, "type": "CONFLICT", "scope": ctx.scope_id,
        "target_prop": eff_prop, "rule_ids": rule_ids, "text": desc}
    out.update({"conflict_id": cid, "mcu_id": ctx.mcu_id, "existing_rule_id": old_rid,
        "existing_rule_text": old_text, "incoming_text": ctx.mcu_text, "description": desc})
    return out

def _collect_mcu_conflicts(ctx: _McuContext, new_frustrations: list[dict[str, Any]],
    all_rules_by_id: dict[str, tuple[str, dict[str, Any]]], cand_base_rules: list[tuple[str, dict[str, Any]]],
    seen_conflict_keys: set[tuple[int, str]], conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mcu_conflicts: list[dict[str, Any]] = []
    d_entry = ctx.delta_entry if isinstance(ctx.delta_entry, dict) else {}
    delta_rule_id = str(d_entry["rule_id"]) if d_entry.get("rule_id") else None
    delta_op, delta_fid = str(d_entry.get("operation") or ""), str(d_entry.get("flow_id") or "")
    lkind = str(ctx.pmcu.get("lattice_kind") or "").strip().upper()
    is_flow_mcu, is_comp_mcu = (delta_op == "UPSERT_FLOW" or lkind == "FLOW"), (delta_op == "UPSERT_COMPONENT" or lkind
        == "COMPONENT")
    pmcu_type, target_prop = str(ctx.pmcu.get("type") or "OBLIGATION"), ctx.pmcu.get("target_prop")
    for frust in new_frustrations:
        cid, rids = str(frust.get("id", "")), [str(x) for x in frust.get("rule_ids", [])]
        if delta_rule_id and pmcu_type not in ("FACT", "GOAL") and delta_rule_id in rids:
            other_rid = next((x for x in rids if x != delta_rule_id), delta_rule_id)
            other_rule = all_rules_by_id.get(other_rid, ("", {}))[1]
            if str(other_rule.get("type") or "") in _STANDING_RULE_TYPES and (ctx.mcu_id,
                other_rid) not in seen_conflict_keys:
                seen_conflict_keys.add((ctx.mcu_id, other_rid))
                c_entry = _make_conflict(ctx, cid or f"conflict_m{ctx.mcu_id}_{other_rid}", rids, other_rid,
                    str(other_rule.get("text", "")), str(frust.get("text", "")), frust)
                mcu_conflicts.append(c_entry)
                conflicts.append(c_entry)
        elif (is_flow_mcu or is_comp_mcu) and cid.startswith(_SBF_CONFLICT_PREFIXES):
            matches_sbf = ((delta_fid in rids if delta_fid else frust.get("scope") == ctx.root_sid)
                if is_flow_mcu else (frust.get("scope") == ctx.root_sid or cid.endswith(f"_{ctx.scope_id}")))
            if matches_sbf and (ctx.mcu_id, cid) not in seen_conflict_keys:
                seen_conflict_keys.add((ctx.mcu_id, cid))
                ref_rid, r_dict = rids[0] if rids else cid, all_rules_by_id.get(rids[0] if rids else cid, ("", {}))[1]
                ref_txt = str(r_dict.get("text", frust.get("text")) or "")
                eff_p = target_prop if target_prop is not None else frust.get("target_prop")
                c_entry = _make_conflict(ctx, cid, rids or [cid], ref_rid, ref_txt, str(frust.get("text", "")), frust,
                    eff_p)
                mcu_conflicts.append(c_entry)
                conflicts.append(c_entry)
    for _, r_old in cand_base_rules:
        old_rid = str(r_old.get("id", ""))
        if old_rid and (ctx.mcu_id, old_rid) not in seen_conflict_keys and _check_standing_rule_clash(ctx.pmcu, r_old):
            seen_conflict_keys.add((ctx.mcu_id, old_rid))
            cid = f"conflict_t{ctx.eff_turn}_m{ctx.mcu_id}_{old_rid}"
            desc = (f"MCU {ctx.mcu_id} ({pmcu_type} on target_prop={target_prop!r}) conflicts with "
                f"standing {r_old.get('type')} rule {old_rid!r}: {r_old.get('text', '')}")
            rids = [old_rid] if not delta_rule_id else sorted({old_rid, delta_rule_id})
            eff_p = target_prop if target_prop is not None else r_old.get("target_prop")
            c_entry = _make_conflict(ctx, cid, rids, old_rid, str(r_old.get("text", "")), desc, target_prop=eff_p)
            mcu_conflicts.append(c_entry)
            conflicts.append(c_entry)
    return mcu_conflicts

def _collect_mcu_flaws(ctx: _McuContext, base_graph: dict[str, Any], trial_graph: dict[str, Any],
    cand_all_rules: list[tuple[str, dict[str, Any]]], flawed_idea_critiques: list[dict[str, Any]] | None,
    seen_flaw_keys: set[tuple[int, str, str]], flawed_ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mcu_flaws: list[dict[str, Any]] = []
    pmcu_type, target_prop = str(ctx.pmcu.get("type") or "OBLIGATION"), ctx.pmcu.get("target_prop")
    for _, r_ref in cand_all_rules:
        ref_id, ref_type = str(r_ref.get("id", "")), str(r_ref.get("type") or "")
        if ref_type in ("FACT", "GOAL") and _check_fact_or_goal_clash(ctx.pmcu, r_ref, ref_kind=ref_type):
            f_kind = FLAW_KIND_FACT_CONTRADICTION if ref_type == "FACT" else FLAW_KIND_GOAL_SABOTAGE
            verb = "contradicts established FACT" if ref_type == "FACT" else "sabotages active GOAL"
            if (ctx.mcu_id, f_kind, ref_id) not in seen_flaw_keys:
                seen_flaw_keys.add((ctx.mcu_id, f_kind, ref_id))
                desc = f"MCU {ctx.mcu_id} {verb} {ref_id!r} on {target_prop!r}: {r_ref.get('text', '')}"
                f_entry = _make_flaw(ctx, f"flaw_t{ctx.eff_turn}_m{ctx.mcu_id}_{ref_id}", f_kind, ref_id,
                    str(r_ref.get("text", "")), desc)
                mcu_flaws.append(f_entry)
                flawed_ideas.append(f_entry)
    if target_prop is not None and pmcu_type in ("PROPOSAL", "ACTION", "OBLIGATION", "BOUND", "PROHIBITION"):
        for g_src in (base_graph, trial_graph):
            for sid, snode in g_src.get("scopes", {}).items():
                if not bool(snode) or not _is_in_scope_family(sid, ctx.scope_id, ctx.root_sid,
                    ctx.ancestors):
                    continue
                attrs = snode.get("attributes", {})
                if isinstance(attrs, dict) and target_prop in attrs and target_prop not in _META_ATTR_KEYS:
                    attr_val, basis_id = attrs[target_prop], f"attr:{sid}.{target_prop}"
                    synth_fact = {"type": "FACT", "target_prop": target_prop, "bound_op": "==", "bound_val": attr_val}
                    if ((ctx.mcu_id, FLAW_KIND_FACT_CONTRADICTION, basis_id) not in seen_flaw_keys
                        and _rules_clash(_normalized_rule_view(ctx.pmcu, default_type="BOUND"), synth_fact)):
                        seen_flaw_keys.add((ctx.mcu_id, FLAW_KIND_FACT_CONTRADICTION, basis_id))
                        basis_txt = f"{sid}.{target_prop} == {attr_val!r}"
                        f_entry = _make_flaw(ctx, f"flaw_t{ctx.eff_turn}_m{ctx.mcu_id}_attr_{target_prop}",
                            FLAW_KIND_FACT_CONTRADICTION, basis_id, basis_txt,
                            f"MCU {ctx.mcu_id} contradicts table attribute {basis_txt}")
                        mcu_flaws.append(f_entry)
                        flawed_ideas.append(f_entry)
    flawed_reason = ctx.pmcu.get("flawed_reason")
    if isinstance(flawed_reason, str) and flawed_reason.strip():
        reason_clean = restore_ascii_dots(flawed_reason).strip()
        basis_id = str(ctx.pmcu.get("flawed_basis_id") or f"mcu_{ctx.mcu_id}_flawed_reason")
        if (ctx.mcu_id, FLAW_KIND_CRITIQUE_FLAGGED, basis_id) not in seen_flaw_keys:
            seen_flaw_keys.add((ctx.mcu_id, FLAW_KIND_CRITIQUE_FLAGGED, basis_id))
            b_txt = str(ctx.pmcu.get("flawed_basis_text") or reason_clean)
            f_entry = _make_flaw(ctx, f"flaw_t{ctx.eff_turn}_m{ctx.mcu_id}_critique", FLAW_KIND_CRITIQUE_FLAGGED,
                basis_id, b_txt, reason_clean)
            mcu_flaws.append(f_entry)
            flawed_ideas.append(f_entry)
    for c_idx, crit in enumerate(flawed_idea_critiques or []):
        if isinstance(crit, dict) and crit.get("mcu_id") == ctx.mcu_id:
            f_kind = str(crit.get("flaw_kind") or FLAW_KIND_CRITIQUE_FLAGGED)
            basis_id = str(crit.get("basis_id") or f"critique_{c_idx + 1}")
            if (ctx.mcu_id, f_kind, basis_id) not in seen_flaw_keys:
                seen_flaw_keys.add((ctx.mcu_id, f_kind, basis_id))
                raw_desc = crit.get("description", crit.get("reason", crit.get("basis_text")))
                desc = restore_ascii_dots(str(raw_desc or "Critique flagged flawed premise")).strip()
                fid = str(crit.get("flaw_id") or f"flaw_t{ctx.eff_turn}_m{ctx.mcu_id}_crit_{c_idx + 1}")
                c_ctx = _McuContext(ctx.pmcu, ctx.mcu_id, str(crit.get("incoming_text") or ctx.mcu_text),
                    ctx.scope_id, ctx.root_sid, ctx.ancestors, ctx.delta_entry, ctx.eff_turn)
                f_entry = _make_flaw(c_ctx, fid, f_kind, basis_id, str(crit.get("basis_text") or desc), desc,
                    scope=str(crit.get("scope") or ctx.scope_id), target_prop=crit.get("target_prop", target_prop))
                mcu_flaws.append(f_entry)
                flawed_ideas.append(f_entry)
    return mcu_flaws

def _prepare_preflight_trial(packet_or_phase3: dict[str, Any], whiteboard_graph: dict[str, Any] | None,
    turn_index: int | None, flawed_idea_critiques: list[dict[str, Any]] | None,
    ) -> tuple[int, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        raise _err("preflight_invalid_whiteboard",
            f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}")
    if not isinstance(packet_or_phase3, dict):
        raise _err("preflight_invalid_packet",
            f"packet_or_phase3 must be a dict, got {type(packet_or_phase3).__name__}")
    if flawed_idea_critiques is not None and not isinstance(flawed_idea_critiques, list):
        raise _err("preflight_invalid_critiques",
            f"flawed_idea_critiques must be a list or None, got {type(flawed_idea_critiques).__name__}")
    wb_dict = whiteboard_graph or {}
    prev_turn = max(int(wb_dict.get("turn", 0) or 0), int(wb_dict.get("turn_index", 0) or 0))
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
    pre_conflict_ids = {str(c["id"]) for c in detect_frustration(base_graph) if c.get("id")}
    trial_res = ingest_intake_packet(trial_graph, packet)
    new_frustrations = [copy.deepcopy(c) for c in trial_res.get("conflicts", [])
        if str(c.get("id", "")) not in pre_conflict_ids]
    return eff_turn, packet, base_graph, trial_graph, trial_res, new_frustrations

def inspect_turn_preflight(packet_or_phase3: dict[str, Any], whiteboard_graph: dict[str, Any] | None = None, *,
    turn_index: int | None = None, flawed_idea_critiques: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Executes the non-destructive Spec 23A pre-flight inspection on a detached trial Whiteboard (§2.5)."""
    eff_turn, packet, base_graph, trial_graph, trial_res, new_frustrations = _prepare_preflight_trial(
        packet_or_phase3, whiteboard_graph, turn_index, flawed_idea_critiques)
    base_rules_by_id, trial_rules_by_id = _collect_rules_by_id(base_graph), _collect_rules_by_id(trial_graph)
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
        mcu_text = restore_ascii_dots(str(pmcu.get("text") or "")).strip()
        delta_entry = delta_by_mcu.get(pmcu.get("mcu_id"), delta_by_mcu.get(mcu_id))
        if isinstance(delta_entry, dict) and delta_entry.get("scope"):
            scope_id = str(delta_entry["scope"])
        else:
            scope_id = resolve_mcu_scopes_and_refs(clone_whiteboard_for_trial(base_graph), pmcu)[0]
        root_sid = scope_id.split("::")[0]
        ancestors = _ancestor_scopes(base_graph.get("scopes", {}),
            scope_id) | _ancestor_scopes(trial_graph.get("scopes", {}), scope_id)
        cand_base_rules = [(sid, r) for sid, snode in base_graph.get("scopes", {}).items()
            if _is_in_scope_family(sid, scope_id, root_sid, ancestors)
            for r in snode.get("rules", []) if r.get("id")]
        cand_all_rules = cand_base_rules + [
            (sid, r) for sid, r in prior_turn_rules if _is_in_scope_family(sid, scope_id, root_sid, ancestors)]
        ctx = _McuContext(pmcu, mcu_id, mcu_text, scope_id, root_sid, ancestors, delta_entry, eff_turn)
        mcu_conflicts = _collect_mcu_conflicts(ctx, new_frustrations, all_rules_by_id, cand_base_rules,
            seen_conflict_keys, conflicts)
        mcu_flaws = _collect_mcu_flaws(ctx, base_graph, trial_graph, cand_all_rules, flawed_idea_critiques,
            seen_flaw_keys, flawed_ideas)
        pmcu_type, target_prop = str(pmcu.get("type") or "OBLIGATION"), pmcu.get("target_prop")
        has_unclear = bool(pmcu.get("unclear_spans")) or pmcu.get("grounding_status") == "NEEDS_CLARIFICATION"
        is_question = pmcu_type == "QUESTION" or pmcu.get("grounding_status") == "HAS_OPEN_QUESTIONS"
        status = classify_mcu_status(has_unclear, is_question, bool(mcu_conflicts), bool(mcu_flaws))
        finding = {"mcu_id": mcu_id, "status": status, "type": pmcu_type, "text": mcu_text, "scope": scope_id,
            "target_prop": target_prop, "unclear_spans": list(pmcu.get("unclear_spans", [])),
            "conflicts": mcu_conflicts, "flawed_ideas": mcu_flaws}
        mcu_findings.append(finding)
        bucket_map = {MCU_STATUS_CLEAN: clean_mcus, MCU_STATUS_NEEDS_CLARIFICATION: ambiguous_mcus,
            MCU_STATUS_HAS_OPEN_QUESTION: question_mcus, MCU_STATUS_RULE_CONFLICT: conflicting_mcus,
            MCU_STATUS_FLAWED_IDEA: flawed_mcus}
        bucket_map[status].append(finding)
        delta_rule_id = (
            str(delta_entry["rule_id"]) if isinstance(delta_entry, dict) and delta_entry.get("rule_id") else None
        )
        if delta_rule_id and delta_rule_id in trial_rules_by_id:
            prior_turn_rules.append(trial_rules_by_id[delta_rule_id])
        elif pmcu_type in ("FACT", "GOAL") and target_prop is not None:
            prior_turn_rules.append((scope_id, {
                "id": delta_rule_id or f"turn{eff_turn}_m{mcu_id}", "type": pmcu_type, "text": mcu_text,
                "target_prop": target_prop, "bound_op": pmcu.get("bound_op"),
                "bound_val": pmcu.get("bound_val"), "conditions": list(pmcu.get("conditions", []))}))
    has_ambiguity = (
        bool(ambiguous_mcus) or bool(packet.get("clarification_questions"))
        or packet.get("grounding_status") == "NEEDS_CLARIFICATION"
    )
    has_open_questions = (
        bool(question_mcus) or bool(packet.get("open_questions"))
        or packet.get("grounding_status") == "HAS_OPEN_QUESTIONS"
    )
    has_rule_conflict, has_flawed_idea = bool(conflicts), bool(flawed_ideas)
    return {"turn_index": eff_turn, "intake_packet": packet,
        "is_clean": is_preflight_clean(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea),
        "has_ambiguity": has_ambiguity, "has_open_questions": has_open_questions,
        "has_rule_conflict": has_rule_conflict, "has_flawed_idea": has_flawed_idea,
        "mcu_findings": mcu_findings, "clean_mcus": clean_mcus, "ambiguous_mcus": ambiguous_mcus,
        "question_mcus": question_mcus, "conflicting_mcus": conflicting_mcus, "flawed_mcus": flawed_mcus,
        "conflicts": conflicts, "flawed_ideas": flawed_ideas, "new_frustrations": new_frustrations,
        "clarification_questions": list(packet.get("clarification_questions", [])),
        "open_questions": list(packet.get("open_questions", [])), "trial_ingestion": trial_res}
