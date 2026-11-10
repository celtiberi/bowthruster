"""Spec 22B: Whiteboard Spin-Glass Conflict & Parent-Hierarchy Engine (`frustration.py`)."""

from __future__ import annotations

import math
from typing import Any

from bowthruster.regiment.intake_packet import _conditions_satisfiable, _interval_subsumes
from .proof_engine import (
    MAX_ANCESTOR_HOPS,
    _ancestor_scopes,
    _check_component_proof_gaps,
    _node_coord_overlap,
    _render_board_bend_proof,
    _scope_chain_values,
    classify_lattice_proof_status,
    evaluate_lattice_proof,
    resolve_open_blocker,
)


def _numeric_interval(
    op: str | None, val: float
) -> tuple[float, bool, float, bool, set[float]]:
    """Maps `(bound_op, numeric_val)` to `(low, low_closed, high, high_closed, excluded)`."""
    table = {
        "<=": (-math.inf, True, val, True, set()),
        "<": (-math.inf, True, val, False, set()),
        ">=": (val, True, math.inf, True, set()),
        ">": (val, False, math.inf, True, set()),
        "==": (val, True, val, True, set()),
        "!=": (-math.inf, True, math.inf, True, {val}),
    }
    return table.get(op or "", (-math.inf, True, math.inf, True, set()))


def _intervals_satisfiable(
    op_a: str | None, va: float, op_b: str | None, vb: float
) -> bool:
    """Returns `True` iff numeric intervals `(op_a, va)` and `(op_b, vb)` intersect."""
    la, lac, ua, uac, neq_a = _numeric_interval(op_a, va)
    lb, lbc, ub, ubc, neq_b = _numeric_interval(op_b, vb)
    low, low_c = (la, lac) if la > lb else ((lb, lbc) if lb > la else (la, lac and lbc))
    high, high_c = (ua, uac) if ua < ub else ((ub, ubc) if ub < ua else (ua, uac and ubc))
    if low > high:
        return False
    return not (low == high and (not (low_c and high_c) or low in (neq_a | neq_b)))


def _rules_clash(ra: dict[str, Any], rb: dict[str, Any]) -> bool:
    """Evaluates Spec 22B §2.1 intra-scope rule clash governed by Bend 2 Laws 1-5."""
    ta, tb = str(ra.get("type", "")), str(rb.get("type", ""))
    op_a, op_b = ra.get("bound_op"), rb.get("bound_op")
    va, vb = ra.get("bound_val"), rb.get("bound_val")
    a_is_num = isinstance(va, (int, float)) and not isinstance(va, bool)
    b_is_num = isinstance(vb, (int, float)) and not isinstance(vb, bool)
    num_ops = ("<=", "<", ">=", ">", "==", "!=")
    if a_is_num and b_is_num and op_a in num_ops and op_b in num_ops:
        if not _intervals_satisfiable(op_a, float(va), op_b, float(vb)):
            return True
        conds_a, conds_b = set(ra.get("conditions") or []), set(rb.get("conditions") or [])
        same_bound = (
            ta == "BOUND" and tb == "BOUND" and op_a == op_b
            and op_a != "!=" and float(va) != float(vb)
        )
        if same_bound:
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
        if (op_a == "==" and op_b == "==" and not same_scalar) or (
            {op_a, op_b} == {"==", "!="} and same_scalar
        ):
            return True
    prohib_pairs = (
        {"PROHIBITION", "ACTION"},
        {"PROHIBITION", "OBLIGATION"},
        {"PROHIBITION", "POLICY"},
        {"PROHIBITION", "BOUND"},
    )
    if {ta, tb} in prohib_pairs:
        return bool(va is None or vb is None or same_scalar)
    return False


def _inherited_rules_clash(
    ra: dict[str, Any], rb: dict[str, Any], is_exception: bool
) -> bool:
    """Evaluates rule clash with Lex Specialis exception shielding governed by Law 6."""
    return False if is_exception else _rules_clash(ra, rb)


def dependency_or_flow_clashes(
    has_prohibition_on_target: bool,
    is_upward_leaf_import: bool,
    is_self_or_cycle: bool,
    is_exception: bool,
) -> bool:
    """Evaluates structural dependency or flow prohibition/cycle clash governed by Law 7."""
    if is_exception:
        return False
    return bool(has_prohibition_on_target or is_upward_leaf_import or is_self_or_cycle)


def flow_payload_clashes(
    source_declares_outputs: bool,
    payload_in_source_outputs: bool,
    target_declares_inputs: bool,
    payload_in_target_inputs: bool,
) -> bool:
    """Evaluates signal-flow interface payload compatibility governed by Bend 2 Law 8."""
    src_bad = source_declares_outputs and not payload_in_source_outputs
    tgt_bad = target_declares_inputs and not payload_in_target_inputs
    return bool(src_bad or tgt_bad)


def _can_reach_edge(
    scopes: dict[str, Any],
    start_sid: str,
    target_sid: str,
    edge_key: str,
    max_hops: int = MAX_ANCESTOR_HOPS,
) -> bool:
    """Checks whether `target_sid` is reachable from `start_sid` via `edge_key`."""
    if start_sid == target_sid:
        return True
    frontier: list[tuple[str, int]] = [(start_sid, 0)]
    visited: set[str] = {start_sid}
    while frontier:
        cur, depth = frontier.pop(0)
        if depth >= max_hops or not isinstance(sc := scopes.get(cur), dict):
            continue
        raw_edges = [sc.get("parent")] if edge_key == "parent" else sc.get("depends_on", [])
        for nxt in (raw_edges or []):
            if not (nxt_s := str(nxt or "").strip()):
                continue
            if nxt_s == target_sid:
                return True
            if nxt_s not in visited:
                visited.add(nxt_s)
                frontier.append((nxt_s, depth + 1))
    return False


def _can_reach_depends_on(
    scopes: dict[str, Any],
    start_sid: str,
    target_sid: str,
    max_hops: int = MAX_ANCESTOR_HOPS,
) -> bool:
    """Checks whether `target_sid` is reachable from `start_sid` via `depends_on` edges."""
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
    """Checks whether `sid` or any ancestor has an active `PROHIBITION` against `target`."""
    t_low = target.strip().lower()
    if not t_low:
        return False, None
    match_vals = {t_low, f"{t_low}_imports"} | (
        {"upward_imports"} if allow_upward_imports else set()
    )
    for c_sid in [sid, *sorted(ancestors_by_scope.get(sid, set()))]:
        if not isinstance(sc := scopes.get(c_sid), dict):
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
            is_dep_prop = tp_low in ("depends_on", "module_dependencies", "imports")
            if v_low in match_vals or (is_dep_prop and v_low == t_low):
                return True, (rid or None)
    return False, None


def _collect_rule_pair_conflicts(
    scoped_rules: list[tuple[str, dict[str, Any]]],
    ancestors_by_scope: dict[str, set[str]],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    n = len(scoped_rules)
    for i in range(n):
        sid_a, ra = scoped_rules[i]
        rid_a, tp_a = str(ra["id"]), ra.get("target_prop")
        if tp_a is None:
            continue
        root_a, anc_a = sid_a.split("::")[0], ancestors_by_scope.get(sid_a, set())
        ov_a = set(ra.get("overrides", []))
        ex_a = {ex.get("rule_id") for ex in ra.get("exceptions", [])}
        conds_a = set(ra.get("conditions", []))
        for j in range(i + 1, n):
            sid_b, rb = scoped_rules[j]
            rid_b = str(rb["id"])
            if rb.get("target_prop") != tp_a:
                continue
            root_b, anc_b = sid_b.split("::")[0], ancestors_by_scope.get(sid_b, set())
            is_ancestor_pair = (
                (sid_a in anc_b)
                or (sid_b in anc_a)
                or (
                    sid_a != sid_b
                    and (sid_b.startswith(f"{sid_a}::") or sid_a.startswith(f"{sid_b}::"))
                )
            )
            if not (root_a == root_b or is_ancestor_pair):
                continue
            conds_b = set(rb.get("conditions", []))
            if conds_a and conds_b and not _conditions_satisfiable(conds_a, conds_b):
                continue
            ov_b = set(rb.get("overrides", []))
            ex_b = {ex.get("rule_id") for ex in rb.get("exceptions", [])}
            is_shielded = (
                (rid_b in ov_a)
                or (rid_a in ov_b)
                or (rid_b in ex_a)
                or (rid_a in ex_b)
                or (
                    is_ancestor_pair
                    and (bool(ra.get("is_exception")) or bool(rb.get("is_exception")))
                )
            )
            if _inherited_rules_clash(ra, rb, is_shielded):
                pair_ids = sorted([rid_a, rid_b])
                c_scope = sid_a if sid_a == sid_b else root_a
                desc_a = f"{ra.get('type')} {ra.get('bound_op')} {ra.get('bound_val')}"
                desc_b = f"{rb.get('type')} {rb.get('bound_op')} {rb.get('bound_val')}"
                msg = (
                    f"Frustrated rules {pair_ids[0]} ({desc_a}) and {pair_ids[1]} ({desc_b}) "
                    f"on target_prop={tp_a!r} in scope={c_scope!r}"
                )
                conflicts.append({
                    "id": f"conflict_{pair_ids[0]}_{pair_ids[1]}",
                    "type": "CONFLICT",
                    "scope": c_scope,
                    "target_prop": tp_a,
                    "rule_ids": pair_ids,
                    "text": msg,
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
        if not scope:
            continue
        seen_dep: set[str] = set()
        for dep in scope.get("depends_on", []):
            if not (dep_s := str(dep or "").strip()):
                continue
            has_prohib, prohib_rid = _find_prohibition_on_target(
                scopes,
                ancestors_by_scope,
                sid,
                dep_s,
                filter_ids=filter_ids,
                allow_upward_imports=True,
            )
            is_cycle = (dep_s == sid) or _can_reach_depends_on(
                scopes, dep_s, sid, MAX_ANCESTOR_HOPS
            )
            if dependency_or_flow_clashes(has_prohib, False, is_cycle, False):
                cid = f"conflict_dep_{sid}_{dep_s}"
                seen_dep.add(cid)
                conflicts.append({
                    "id": cid,
                    "type": "CONFLICT",
                    "scope": sid.split("::")[0],
                    "target_prop": "depends_on",
                    "rule_ids": [prohib_rid] if prohib_rid else [f"dep:{sid}->{dep_s}"],
                    "text": f"Structural dependency conflict: {sid} depends_on {dep_s}.",
                })
        parent_s = str(scope.get("parent") or "").strip()
        if parent_s and f"conflict_dep_{sid}_{parent_s}" not in seen_dep:
            is_p_cycle = (parent_s == sid) or _can_reach_edge(
                scopes, parent_s, sid, "parent", MAX_ANCESTOR_HOPS
            )
            if dependency_or_flow_clashes(False, False, is_p_cycle, False):
                conflicts.append({
                    "id": f"conflict_dep_{sid}_{parent_s}",
                    "type": "CONFLICT",
                    "scope": sid.split("::")[0],
                    "target_prop": "depends_on",
                    "rule_ids": [f"dep:{sid}->{parent_s}"],
                    "text": f"Structural dependency conflict: {sid} depends_on {parent_s}.",
                })
    flows_raw = graph.get("flows", [])
    if isinstance(flows_raw, list):
        for idx, fl in enumerate(flows_raw):
            if not fl:
                continue
            fid = str(fl.get("id") or f"flow_{idx + 1}")
            src = str(fl.get("source") or "").strip()
            tgt = str(fl.get("target") or "").strip()
            payload = str(fl.get("payload") or "").strip()
            has_prohib, _ = (
                _find_prohibition_on_target(
                    scopes, ancestors_by_scope, src, tgt, filter_ids=filter_ids
                )
                if (src and tgt)
                else (False, None)
            )
            if dependency_or_flow_clashes(has_prohib, False, bool(src and src == tgt), False):
                conflicts.append({
                    "id": f"conflict_flow_{fid}",
                    "type": "CONFLICT",
                    "scope": src.split("::")[0] if src else "global_defaults",
                    "target_prop": "flow",
                    "rule_ids": [fid],
                    "text": f"Signal flow conflict on {fid}: {src} -> {tgt}.",
                })
            if payload:
                src_sc = scopes.get(src) or {}
                tgt_sc = scopes.get(tgt) or {}
                src_outs = [
                    str(x).strip() for x in src_sc.get("outputs", []) if str(x).strip()
                ]
                tgt_ins = [
                    str(x).strip() for x in tgt_sc.get("inputs", []) if str(x).strip()
                ]
                if flow_payload_clashes(
                    bool(src_outs), payload in src_outs, bool(tgt_ins), payload in tgt_ins
                ):
                    conflicts.append({
                        "id": f"conflict_payload_{fid}",
                        "type": "CONFLICT",
                        "scope": src.split("::")[0] if src else "global_defaults",
                        "target_prop": "payload",
                        "rule_ids": [fid],
                        "text": f"Flow payload mismatch on {fid}: {src} --[{payload}]--> {tgt}.",
                    })
    return conflicts


def detect_frustration(
    graph: dict[str, Any], active_rule_ids: set[str] | list[str] | None = None
) -> list[dict[str, Any]]:
    """Detects Spin-Glass Frustration (`CONFLICT`) across family and ancestor scopes."""
    if not isinstance(graph, dict) or not isinstance(scopes := graph.get("scopes"), dict):
        return []
    filter_ids = set(active_rule_ids) if active_rule_ids is not None else None
    ancestors_by_scope = {
        sid: _ancestor_scopes(scopes, sid, MAX_ANCESTOR_HOPS) for sid in scopes
    }
    scoped_rules: list[tuple[str, dict[str, Any]]] = []
    for sid, scope in scopes.items():
        if not isinstance(scope, dict) or not isinstance(rules := scope.get("rules", []), list):
            continue
        for r in rules:
            if not r or not r.get("id"):
                continue
            rid = str(r["id"])
            raw_exceptions = r.get("exceptions", [])
            if not isinstance(raw_exceptions, list):
                raise ValueError(
                    f"rule {rid!r} exceptions must be a list of dicts, got {raw_exceptions!r}"
                )
            for ex in raw_exceptions:
                if isinstance(ex, str) or not isinstance(ex, dict):
                    raise ValueError(
                        f"rule {rid!r} exceptions entry must be a dict, got {ex!r}"
                    )
            if filter_ids is None or rid in filter_ids:
                scoped_rules.append((sid, r))
    conflicts = _collect_rule_pair_conflicts(scoped_rules, ancestors_by_scope)
    conflicts.extend(
        _collect_dependency_and_flow_conflicts(graph, scopes, ancestors_by_scope, filter_ids)
    )
    return conflicts
