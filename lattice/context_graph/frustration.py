"""Spec 20 §2: Canonical Spin-Glass Frustration Detection (`detect_frustration`) and Rule Constraint Clash Predicates."""

from __future__ import annotations

import math
from typing import Any

from .activation import (
    _build_rule_index,
    _scope_cross_degree,
    _tau_min_for_temperature,
    compute_seed_activations,
    compute_tau,
    infer_query_temperature,
)


def _numeric_interval(op: str | None, val: float) -> tuple[float, bool, float, bool, set[float]]:
    """Maps `(bound_op, numeric_val)` to `(low, low_closed, high, high_closed, excluded_points)` (Spec 20 §2)."""
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
    val_a: float,
    op_b: str | None,
    val_b: float,
) -> bool:
    """Returns `True` iff the intersection of intervals for `(op_a, val_a)` and `(op_b, val_b)` is non-empty."""
    la, lac, ua, uac, neq_a = _numeric_interval(op_a, val_a)
    lb, lbc, ub, ubc, neq_b = _numeric_interval(op_b, val_b)

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
    """Evaluates Spec 20 §2 Condition 3 (Bound Clash, Equality/Inequality Value Clash, or Deontic Polarity Clash)."""
    ta, tb = str(ra.get("type", "")), str(rb.get("type", ""))
    op_a, op_b = ra.get("bound_op"), rb.get("bound_op")
    va, vb = ra.get("bound_val"), rb.get("bound_val")

    a_is_num = isinstance(va, (int, float)) and not isinstance(va, bool)
    b_is_num = isinstance(vb, (int, float)) and not isinstance(vb, bool)
    if a_is_num and b_is_num and op_a in ("<=", "<", ">=", ">", "==", "!=") and op_b in ("<=", "<", ">=", ">", "==", "!="):
        if not _intervals_satisfiable(op_a, float(va), op_b, float(vb)):
            return True
        if ta == "BOUND" and tb == "BOUND" and op_a == op_b and op_a != "!=" and float(va) != float(vb):
            return True
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


def detect_frustration(
    graph: dict[str, Any],
    active_rule_ids: set[str] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Detects Spin-Glass Frustration (`CONFLICT`) across active rules in `graph` (Spec 20 §2)."""
    if not isinstance(graph, dict):
        return []
    filter_ids = set(active_rule_ids) if active_rule_ids is not None else None

    rules_by_root_scope: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for sid, scope in graph.get("scopes", {}).items():
        if not isinstance(scope, dict):
            continue
        root_sid = sid.split("::")[0]
        for r in scope.get("rules", []):
            if not isinstance(r, dict) or not r.get("id"):
                continue
            rid = str(r["id"])
            for ex in r.get("exceptions", []):
                if isinstance(ex, str) or not isinstance(ex, dict):
                    raise ValueError(f"rule {rid!r} exceptions entry must be a dict, got {ex!r}")
            if filter_ids is not None and rid not in filter_ids:
                continue
            rules_by_root_scope.setdefault(root_sid, []).append((sid, r))

    conflicts: list[dict[str, Any]] = []
    for root_sid, scoped_rules in sorted(rules_by_root_scope.items()):
        n = len(scoped_rules)
        for i in range(n):
            sid_a, ra = scoped_rules[i]
            rid_a = str(ra["id"])
            tp_a = ra.get("target_prop")
            if tp_a is None:
                continue
            ov_a = set(ra.get("overrides", []))
            ex_a = {ex.get("rule_id") for ex in ra.get("exceptions", []) if isinstance(ex, dict)}
            conds_a = set(ra.get("conditions", []))

            for j in range(i + 1, n):
                sid_b, rb = scoped_rules[j]
                rid_b = str(rb["id"])
                if rb.get("target_prop") != tp_a:
                    continue
                ov_b = set(rb.get("overrides", []))
                ex_b = {ex.get("rule_id") for ex in rb.get("exceptions", []) if isinstance(ex, dict)}

                if rid_b in ov_a or rid_a in ov_b or rid_b in ex_a or rid_a in ex_b:
                    continue

                conds_b = set(rb.get("conditions", []))
                if conds_a and conds_b and not (conds_a & conds_b):
                    continue

                if _rules_clash(ra, rb):
                    pair_ids = sorted([rid_a, rid_b])
                    conflicts.append({
                        "id": f"conflict_{pair_ids[0]}_{pair_ids[1]}",
                        "type": "CONFLICT",
                        "scope": sid_a if sid_a == sid_b else root_sid,
                        "target_prop": tp_a,
                        "rule_ids": pair_ids,
                        "text": (
                            f"Frustrated rules {pair_ids[0]} ({ra.get('type')} {ra.get('bound_op')} {ra.get('bound_val')}) "
                            f"and {pair_ids[1]} ({rb.get('type')} {rb.get('bound_op')} {rb.get('bound_val')}) "
                            f"on target_prop={tp_a!r} in scope={root_sid!r}"
                        ),
                    })
    return conflicts
