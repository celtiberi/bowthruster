"""Spec 22B: Whiteboard Spin-Glass Conflict & Parent-Hierarchy Engine (`frustration.py`)."""

from __future__ import annotations

import math
from typing import Any

from bowthruster.regiment.intake_packet import _conditions_satisfiable, _interval_subsumes

MAX_ANCESTOR_HOPS = 16


def _numeric_interval(op: str | None, val: float) -> tuple[float, bool, float, bool, set[float]]:
    """Maps `(bound_op, numeric_val)` to `(low, low_closed, high, high_closed, excluded_points)` (Spec 22B §2.1)."""
    if op == "<=":
        return (-math.inf, True, val, True, set())
    if op == "<":
        return (-math.inf, True, val, False, set())
    if op == ">=":
        return (val, True, math.inf, True, set())
    if op == ">":
        return (val, False, math.inf, True, set())
    if op == "==":
        return (val, True, val, True, set())
    if op == "!=":
        return (-math.inf, True, math.inf, True, {val})
    return (-math.inf, True, math.inf, True, set())


def _intervals_satisfiable(
    op_a: str | None,
    va: float,
    op_b: str | None,
    vb: float,
) -> bool:
    """Returns `True` iff the intersection of numeric intervals `(op_a, va)` and `(op_b, vb)` is non-empty (Spec 22B §2.1)."""
    la, lac, ua, uac, neq_a = _numeric_interval(op_a, va)
    lb, lbc, ub, ubc, neq_b = _numeric_interval(op_b, vb)

    if la > lb:
        low, low_c = la, lac
    elif lb > la:
        low, low_c = lb, lbc
    else:
        low, low_c = la, (lac and lbc)

    if ua < ub:
        high, high_c = ua, uac
    elif ub < ua:
        high, high_c = ub, ubc
    else:
        high, high_c = ua, (uac and ubc)

    if low > high:
        return False
    if low == high:
        if not (low_c and high_c):
            return False
        if low in (neq_a | neq_b):
            return False
    return True


def _rules_clash(ra: dict[str, Any], rb: dict[str, Any]) -> bool:
    """Evaluates Spec 22B §2.1 intra-scope rule clash governed by Bend 2 Laws 1-5."""
    ta, tb = str(ra.get("type", "")), str(rb.get("type", ""))
    op_a, op_b = ra.get("bound_op"), rb.get("bound_op")
    va, vb = ra.get("bound_val"), rb.get("bound_val")

    a_is_num = isinstance(va, (int, float)) and not isinstance(va, bool)
    b_is_num = isinstance(vb, (int, float)) and not isinstance(vb, bool)
    if a_is_num and b_is_num and op_a in ("<=", "<", ">=", ">", "==", "!=") and op_b in ("<=", "<", ">=", ">", "==", "!="):
        if not _intervals_satisfiable(op_a, float(va), op_b, float(vb)):
            return True
        conds_a, conds_b = set(ra.get("conditions") or []), set(rb.get("conditions") or [])
        if ta == "BOUND" and tb == "BOUND" and op_a == op_b and op_a != "!=" and float(va) != float(vb):
            if not conds_a and conds_b:
                return not _interval_subsumes(op_a, float(va), op_b, float(vb))
            if conds_a and not conds_b:
                return not _interval_subsumes(op_b, float(vb), op_a, float(va))
            return conds_a == conds_b
        if op_a in ("<=", "<", ">=", ">") or op_b in ("<=", "<", ">=", ">"):
            return False

    if op_a == "!=" and op_b == "!=":
        return False

    same_scalar = (
        (float(va) == float(vb))
        if (a_is_num and b_is_num)
        else (str(va).strip().lower() == str(vb).strip().lower())
    )
    if va is not None and vb is not None and op_a in ("==", "!=") and op_b in ("==", "!="):
        if op_a == "==" and op_b == "==" and not same_scalar:
            return True
        if {op_a, op_b} == {"==", "!="} and same_scalar:
            return True

    type_pair = {ta, tb}
    if type_pair in (
        {"PROHIBITION", "ACTION"},
        {"PROHIBITION", "OBLIGATION"},
        {"PROHIBITION", "POLICY"},
        {"PROHIBITION", "BOUND"},
    ):
        if va is None or vb is None or same_scalar:
            return True

    return False


def _inherited_rules_clash(ra: dict[str, Any], rb: dict[str, Any], is_exception: bool) -> bool:
    """Evaluates rule clash with Lex Specialis exception shielding governed by Bend 2 Law 6 (`lex_specialis_shields_clash`)."""
    if is_exception:
        return False
    return _rules_clash(ra, rb)


def dependency_or_flow_clashes(
    has_prohibition_on_target: bool,
    is_upward_leaf_import: bool,
    is_self_or_cycle: bool,
    is_exception: bool,
) -> bool:
    """Evaluates structural dependency or signal-flow prohibition/cycle clash governed by Bend 2 Law 7 (`prohibited_dependency_or_flow_clashes`)."""
    if is_exception:
        return False
    return bool(has_prohibition_on_target or is_upward_leaf_import or is_self_or_cycle)


def flow_payload_clashes(
    source_declares_outputs: bool,
    payload_in_source_outputs: bool,
    target_declares_inputs: bool,
    payload_in_target_inputs: bool,
) -> bool:
    """Evaluates signal-flow interface payload compatibility governed by Bend 2 Law 8 (`flow_interface_payload_compatibility`)."""
    return bool(
        (source_declares_outputs and not payload_in_source_outputs)
        or (target_declares_inputs and not payload_in_target_inputs)
    )


def classify_lattice_proof_status(
    has_hard_violation: bool,
    has_sbf_components: bool,
    all_components_complete: bool,
    all_flows_connected: bool,
) -> str:
    """Evaluates continuous lattice proof readiness governed by Bend 2 Law 9 (`lattice_proof_proven_iff_complete_and_conflict_free`)."""
    if has_hard_violation:
        return "BROKEN_INVARIANT"
    if has_sbf_components and all_components_complete and all_flows_connected:
        return "PROVEN_COMPLETE"
    return "INCOMPLETE_DRAFT"


def _can_reach_edge(
    scopes: dict[str, Any], start_sid: str,
    target_sid: str, edge_key: str, max_hops: int = MAX_ANCESTOR_HOPS
) -> bool:
    """Checks whether `target_sid` is reachable from `start_sid` via `edge_key` (`'depends_on'` or `'parent'`) within `max_hops`."""
    if start_sid == target_sid:
        return True
    frontier: list[tuple[str, int]] = [(start_sid, 0)]
    visited: set[str] = {start_sid}
    while frontier:
        cur, depth = frontier.pop(0)
        if depth >= max_hops:
            continue
        sc = scopes.get(cur)
        if not isinstance(sc, dict):
            continue
        raw_edges = [sc.get("parent")] if edge_key == "parent" else sc.get("depends_on", [])
        for nxt in (raw_edges if isinstance(raw_edges, list) else []):
            nxt_s = str(nxt or "").strip()
            if not nxt_s:
                continue
            if nxt_s == target_sid:
                return True
            if nxt_s not in visited:
                visited.add(nxt_s)
                frontier.append((nxt_s, depth + 1))
    return False


def _can_reach_depends_on(
    scopes: dict[str, Any], start_sid: str,
    target_sid: str, max_hops: int = MAX_ANCESTOR_HOPS
) -> bool:
    """Checks whether `target_sid` is reachable from `start_sid` via `depends_on` edges within `max_hops` (Spec 22B §2.2 item 3)."""
    return _can_reach_edge(scopes, start_sid, target_sid, "depends_on", max_hops)


def _find_prohibition_on_target(
    scopes: dict[str, Any],
    ancestors_by_scope: dict[str, set[str]],
    sid: str,
    target: str,
    *,
    filter_ids: set[str] | None = None,
    allow_upward_imports: bool = False,
) -> tuple[bool, str | None]:
    """Checks whether `sid` or any of its ancestor scopes has an active `PROHIBITION` rule against `target`."""
    t_low = target.strip().lower()
    if not t_low:
        return False, None
    check_scopes = [sid, *sorted(ancestors_by_scope.get(sid, set()))]
    match_vals = {t_low, f"{t_low}_imports"} | ({"upward_imports"} if allow_upward_imports else set())
    for c_sid in check_scopes:
        sc = scopes.get(c_sid)
        if not isinstance(sc, dict):
            continue
        for r in sc.get("rules", []):
            if not isinstance(r, dict) or str(r.get("type", "")) != "PROHIBITION":
                continue
            rid = str(r.get("id") or "")
            if filter_ids is not None and rid and rid not in filter_ids:
                continue
            raw_v = r.get("value") if r.get("value") is not None else r.get("bound_val")
            v_low = str(raw_v or "").strip().lower()
            tp_low = str(r.get("target_prop") or "").strip().lower()
            if v_low in match_vals or (tp_low in ("depends_on", "module_dependencies", "imports") and v_low == t_low):
                return True, (rid or None)
    return False, None


def _ancestor_scopes(scopes: dict[str, Any], sid: str, max_hops: int = MAX_ANCESTOR_HOPS) -> set[str]:
    """Walks `scopes[cur].get('parent')` up to `max_hops` hops with cycle protection (Spec 22B §2.2)."""
    ancestors: set[str] = set()
    visited: set[str] = {sid}
    cur: str | None = sid
    hops = 0
    while cur is not None and hops < max_hops:
        scope_obj = scopes.get(cur)
        parent = scope_obj.get("parent") if isinstance(scope_obj, dict) else None
        if not isinstance(parent, str) or not parent.strip() or parent.strip() in visited:
            break
        p_sid = parent.strip()
        ancestors.add(p_sid)
        visited.add(p_sid)
        cur = p_sid
        hops += 1
    return ancestors


def _collect_rule_pair_conflicts(
    scoped_rules: list[tuple[str, dict[str, Any]]],
    ancestors_by_scope: dict[str, set[str]],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    n = len(scoped_rules)
    for i in range(n):
        sid_a, ra = scoped_rules[i]
        rid_a = str(ra["id"])
        tp_a = ra.get("target_prop")
        if tp_a is None:
            continue
        root_a = sid_a.split("::")[0]
        anc_a = ancestors_by_scope.get(sid_a, set())
        ov_a = set(ra.get("overrides", []))
        ex_a = {ex.get("rule_id") for ex in ra.get("exceptions", []) if isinstance(ex, dict)}
        conds_a = set(ra.get("conditions", []))
        for j in range(i + 1, n):
            sid_b, rb = scoped_rules[j]
            rid_b = str(rb["id"])
            if rb.get("target_prop") != tp_a:
                continue
            root_b = sid_b.split("::")[0]
            anc_b = ancestors_by_scope.get(sid_b, set())
            same_root = root_a == root_b
            is_ancestor_pair = (
                (sid_a in anc_b)
                or (sid_b in anc_a)
                or (sid_a != sid_b and (sid_b.startswith(f"{sid_a}::") or sid_a.startswith(f"{sid_b}::")))
            )
            if not (same_root or is_ancestor_pair):
                continue
            conds_b = set(rb.get("conditions", []))
            if conds_a and conds_b and not _conditions_satisfiable(conds_a, conds_b):
                continue
            ov_b = set(rb.get("overrides", []))
            ex_b = {ex.get("rule_id") for ex in rb.get("exceptions", []) if isinstance(ex, dict)}
            is_shielded = (
                (rid_b in ov_a) or (rid_a in ov_b)
                or (rid_b in ex_a) or (rid_a in ex_b)
                or (is_ancestor_pair and (bool(ra.get("is_exception")) or bool(rb.get("is_exception"))))
            )
            if _inherited_rules_clash(ra, rb, is_shielded):
                pair_ids = sorted([rid_a, rid_b])
                conflict_scope = sid_a if sid_a == sid_b else root_a
                conflicts.append({
                    "id": f"conflict_{pair_ids[0]}_{pair_ids[1]}",
                    "type": "CONFLICT",
                    "scope": conflict_scope,
                    "target_prop": tp_a,
                    "rule_ids": pair_ids,
                    "text": (
                        f"Frustrated rules {pair_ids[0]} ({ra.get('type')} {ra.get('bound_op')} {ra.get('bound_val')}) "
                        f"and {pair_ids[1]} ({rb.get('type')} {rb.get('bound_op')} {rb.get('bound_val')}) "
                        f"on target_prop={tp_a!r} in scope={conflict_scope!r}"
                    ),
                })
    return conflicts


def _collect_dependency_and_flow_conflicts(
    graph: dict[str, Any],
    scopes: dict[str, Any],
    ancestors_by_scope: dict[str, set[str]],
    filter_ids: set[str] | None,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for sid, scope in scopes.items():
        if not isinstance(scope, dict):
            continue
        seen_dep_conflicts: set[str] = set()
        for dep in scope.get("depends_on", []):
            dep_s = str(dep or "").strip()
            if not dep_s:
                continue
            has_prohib, prohib_rid = _find_prohibition_on_target(
                scopes, ancestors_by_scope, sid, dep_s, filter_ids=filter_ids, allow_upward_imports=True
            )
            is_cycle = (dep_s == sid) or _can_reach_depends_on(scopes, dep_s, sid, MAX_ANCESTOR_HOPS)
            if dependency_or_flow_clashes(has_prohib, False, is_cycle, False):
                cid = f"conflict_dep_{sid}_{dep_s}"
                seen_dep_conflicts.add(cid)
                conflicts.append({
                    "id": cid, "type": "CONFLICT", "scope": sid.split("::")[0], "target_prop": "depends_on",
                    "rule_ids": [prohib_rid] if prohib_rid else [f"dep:{sid}->{dep_s}"],
                    "text": f"Structural dependency conflict: {sid} depends_on {dep_s}.",
                })
        parent_s = str(scope.get("parent") or "").strip()
        if parent_s and f"conflict_dep_{sid}_{parent_s}" not in seen_dep_conflicts:
            is_parent_cycle = (parent_s == sid) or _can_reach_edge(scopes, parent_s, sid, "parent", MAX_ANCESTOR_HOPS)
            if dependency_or_flow_clashes(False, False, is_parent_cycle, False):
                conflicts.append({
                    "id": f"conflict_dep_{sid}_{parent_s}", "type": "CONFLICT", "scope": sid.split("::")[0],
                    "target_prop": "depends_on", "rule_ids": [f"dep:{sid}->{parent_s}"],
                    "text": f"Structural dependency conflict: {sid} depends_on {parent_s}.",
                })

    flows_raw = graph.get("flows", [])
    if isinstance(flows_raw, list):
        for idx, fl in enumerate(flows_raw):
            if not isinstance(fl, dict):
                continue
            fid = str(fl.get("id") or f"flow_{idx + 1}")
            src = str(fl.get("source") or "").strip()
            tgt = str(fl.get("target") or "").strip()
            payload = str(fl.get("payload") or "").strip()
            has_prohib, _ = (
                _find_prohibition_on_target(
                    scopes, ancestors_by_scope, src, tgt,
                    filter_ids=filter_ids, allow_upward_imports=False
                )
                if (src and tgt)
                else (False, None)
            )
            if dependency_or_flow_clashes(has_prohib, False, bool(src and src == tgt), False):
                conflicts.append({
                    "id": f"conflict_flow_{fid}", "type": "CONFLICT",
                    "scope": src.split("::")[0] if src else "global_defaults",
                    "target_prop": "flow", "rule_ids": [fid],
                    "text": f"Signal flow conflict on {fid}: {src} -> {tgt}.",
                })
            if payload:
                src_sc = scopes.get(src, {}) if isinstance(scopes.get(src), dict) else {}
                tgt_sc = scopes.get(tgt, {}) if isinstance(scopes.get(tgt), dict) else {}
                src_outs = [str(x).strip() for x in src_sc.get("outputs", []) if str(x).strip()]
                tgt_ins = [str(x).strip() for x in tgt_sc.get("inputs", []) if str(x).strip()]
                if flow_payload_clashes(bool(src_outs), payload in src_outs, bool(tgt_ins), payload in tgt_ins):
                    conflicts.append({
                        "id": f"conflict_payload_{fid}", "type": "CONFLICT",
                        "scope": src.split("::")[0] if src else "global_defaults",
                        "target_prop": "payload", "rule_ids": [fid],
                        "text": f"Flow payload mismatch on {fid}: {src} --[{payload}]--> {tgt}.",
                    })
    return conflicts


def detect_frustration(
    graph: dict[str, Any],
    active_rule_ids: set[str] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Detects Spin-Glass Frustration (`CONFLICT`) across same-root-family and ancestor-descendant scopes (Spec 22B §2.2)."""
    if not isinstance(graph, dict):
        return []
    scopes = graph.get("scopes")
    if not isinstance(scopes, dict):
        return []

    filter_ids = set(active_rule_ids) if active_rule_ids is not None else None
    ancestors_by_scope: dict[str, set[str]] = {
        sid: _ancestor_scopes(scopes, sid, MAX_ANCESTOR_HOPS)
        for sid, s in scopes.items()
        if isinstance(s, dict)
    }

    scoped_rules: list[tuple[str, dict[str, Any]]] = []
    for sid, scope in scopes.items():
        if not isinstance(scope, dict):
            continue
        rules = scope.get("rules", [])
        if not isinstance(rules, list):
            continue
        for r in rules:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            rid = str(r["id"])
            raw_exceptions = r.get("exceptions", [])
            if not isinstance(raw_exceptions, list):
                raise ValueError(f"rule {rid!r} exceptions must be a list of dicts, got {raw_exceptions!r}")
            for ex in raw_exceptions:
                if isinstance(ex, str) or not isinstance(ex, dict):
                    raise ValueError(f"rule {rid!r} exceptions entry must be a dict, got {ex!r}")
            if filter_ids is not None and rid not in filter_ids:
                continue
            scoped_rules.append((sid, r))

    conflicts = _collect_rule_pair_conflicts(scoped_rules, ancestors_by_scope)
    conflicts.extend(_collect_dependency_and_flow_conflicts(graph, scopes, ancestors_by_scope, filter_ids))
    return conflicts


def _render_board_bend_proof(has_viol: bool, has_comp: bool, comp_ok: bool, flow_ok: bool) -> str:
    """Synthesizes a self-contained Bend 2 module + formal `law` proof for Law 9 (Spec 22B §2.3)."""
    bv, bc, bco, bfo = ("True{}" if x else "False{}" for x in (has_viol, has_comp, comp_ok, flow_ok))
    return (
        "import Base\n\n"
        "type LPStatus is Data:\n  LPBroken{}\n  LPProven{}\n  LPDraft{}\n\n"
        "def is_lp_proven(s: LPStatus) -> Bool:\n  match s:\n    case LPBroken{}: False{}\n    case LPProven{}: True{}\n    case LPDraft{}: False{}\n\n"
        "def classify_lattice_proof_status(has_viol: Bool, has_comp: Bool, comp_ok: Bool, flow_ok: Bool) -> LPStatus:\n"
        "  match has_viol has_comp comp_ok flow_ok:\n"
        "    case True{} has_comp comp_ok flow_ok:\n      LPBroken{}\n"
        "    case False{} True{} True{} True{}:\n      LPProven{}\n"
        "    case False{} True{} True{} False{}:\n      LPDraft{}\n"
        "    case False{} True{} False{} flow_ok:\n      LPDraft{}\n"
        "    case False{} False{} comp_ok flow_ok:\n      LPDraft{}\n\n"
        "law lattice_proof_proven_iff_complete_and_conflict_free:\n"
        "  for +has_viol: Bool\n  for +has_comp: Bool\n  for +comp_ok: Bool\n  for +flow_ok: Bool\n"
        "  {is_lp_proven(classify_lattice_proof_status(has_viol, has_comp, comp_ok, flow_ok)) == Bool.and(Bool.not(has_viol), Bool.and(has_comp, Bool.and(comp_ok, flow_ok))) : Bool}\n\n"
        "def lattice_proof_proven_iff_complete_and_conflict_free(has_viol, has_comp, comp_ok, flow_ok):\n"
        "  match has_viol has_comp comp_ok flow_ok:\n"
        "    case True{} has_comp comp_ok flow_ok: {==}\n"
        "    case False{} True{} True{} True{}: {==}\n"
        "    case False{} True{} True{} False{}: {==}\n"
        "    case False{} True{} False{} flow_ok: {==}\n"
        "    case False{} False{} comp_ok flow_ok: {==}\n\n"
        f"def board_proof_verdict() -> LPStatus:\n  classify_lattice_proof_status({bv}, {bc}, {bco}, {bfo})\n"
    )


def _check_component_proof_gaps(
    sid: str,
    scopes: dict[str, Any],
    flows: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    total_owned: int,
) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    sc = scopes[sid]
    anc_sids = _ancestor_scopes(scopes, sid, MAX_ANCESTOR_HOPS)
    raw_ch = sc.get("children")
    child_sids = {str(c).strip() for c in (raw_ch if isinstance(raw_ch, list) else []) if str(c).strip()} | {
        k for k, v in scopes.items() if isinstance(v, dict) and str(v.get("parent") or "").strip() == sid
    }
    family_sids = {sid, *anc_sids, *child_sids}
    owner = next(
        (
            str(scopes.get(s, {}).get("owner") or "").strip().lower()
            for s in (sid, *anc_sids) if isinstance(scopes.get(s), dict)
            and str(scopes.get(s, {}).get("owner") or "").strip()
        ),
        ""
    )
    if not owner:
        gaps.append({"kind": "MISSING_OWNER", "scope": sid, "description": f"Component '{sid}' has no assigned division owner."})

    has_law = any(a.get("kind") in ("LAW", "INTERFACE", "SCHEMA") and str(a.get("scope") or a.get("scope_id") or "").strip() in family_sids for a in artifacts) or any(
        bool(scopes.get(s, {}).get("laws") or scopes.get(s, {}).get("files") or scopes.get(s, {}).get("rules"))
        for s in family_sids if isinstance(scopes.get(s), dict)
    )
    if not has_law:
        gaps.append({"kind": "MISSING_LAW", "scope": sid, "description": f"Component '{sid}' has no Bend 2 formal law (% LAW)."})

    is_qa = sid.startswith(("test_", "tests/", "qa_")) or owner in ("qa", "q-division")
    has_contract = (
        any(a.get("kind") in ("SCHEMA", "INTERFACE") and str(a.get("scope") or a.get("scope_id") or "").strip() in family_sids for a in artifacts)
        or any(
            bool(scopes.get(s, {}).get("inputs") or scopes.get(s, {}).get("outputs") or scopes.get(s, {}).get("schemas") or scopes.get(s, {}).get("interfaces"))
            for s in family_sids if isinstance(scopes.get(s), dict)
        )
        or any(
            (str(f.get("source") or "").strip() in family_sids or str(f.get("target") or "").strip() in family_sids)
            and bool(str(f.get("payload") or "").strip())
            for f in flows
        )
        or (is_qa and any(bool(scopes.get(s, {}).get("rules") or scopes.get(s, {}).get("tasks")) for s in family_sids if isinstance(scopes.get(s), dict)))
    )
    if not has_contract:
        gaps.append({"kind": "MISSING_CONTRACT", "scope": sid, "description": f"Component '{sid}' has no schema, interface, or typed flow payload."})

    if not is_qa and total_owned > 1:
        if not any(str(f.get("source") or "").strip() in family_sids or str(f.get("target") or "").strip() in family_sids for f in flows):
            gaps.append({"kind": "UNWIRED_COMPONENT", "scope": sid, "description": f"Component '{sid}' is not connected to any > FLOW edge."})
    return gaps


def evaluate_lattice_proof(graph: dict[str, Any]) -> dict[str, Any]:
    """Evaluates continuous architectural consistency and completeness of `graph` governed by Bend 2 Law 9 (Spec 22B §2.3)."""
    if not isinstance(graph, dict):
        graph = {}
    scopes = graph.get("scopes") if isinstance(graph.get("scopes"), dict) else {}
    raw_flows = graph.get("flows") if isinstance(graph.get("flows"), list) else []
    flows = [f for f in raw_flows if isinstance(f, dict)]
    raw_arts = graph.get("lattice_artifacts")
    artifacts = [
        a for a in (raw_arts.values() if isinstance(raw_arts, dict) else (raw_arts if isinstance(raw_arts, list) else []))
        if isinstance(a, dict)
    ]
    hard_violations = detect_frustration(graph)

    owned_components = [
        sid for sid, sc in scopes.items()
        if sid != "global_defaults" and not str(sid).startswith("global_defaults::") and isinstance(sc, dict)
        and bool(sc.get("owner") or sc.get("role") or sc.get("depends_on") or sc.get("children") or sc.get("parent"))
    ]
    open_gaps: list[dict[str, str]] = []
    for sid in owned_components:
        open_gaps.extend(_check_component_proof_gaps(sid, scopes, flows, artifacts, len(owned_components)))

    all_components_complete = not any(
        g["kind"] in ("MISSING_OWNER", "MISSING_LAW", "MISSING_CONTRACT")
        for g in open_gaps
    )
    all_flows_connected = not any(g["kind"] == "UNWIRED_COMPONENT" for g in open_gaps)
    status = classify_lattice_proof_status(
        bool(hard_violations), bool(owned_components),
        all_components_complete, all_flows_connected
    )
    return {
        "status": status,
        "is_proven": status == "PROVEN_COMPLETE",
        "component_count": len(owned_components),
        "flow_count": len(raw_flows),
        "law_count": sum(1 for a in artifacts if a.get("kind") == "LAW"),
        "contract_count": sum(1 for a in artifacts if a.get("kind") in ("SCHEMA", "INTERFACE")),
        "hard_violations": hard_violations,
        "open_gaps": open_gaps,
        "bend_proof_source": _render_board_bend_proof(
            bool(hard_violations), bool(owned_components),
            all_components_complete, all_flows_connected
        ),
    }
