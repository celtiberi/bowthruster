"""Spec 22B §2.3: Whiteboard Continuous Lattice Proof, Blocker Resolution, & nD Coordinate Matching."""

from __future__ import annotations

from typing import Any

from .schema import normalize_board_graph, sync_to_sqlite

MAX_ANCESTOR_HOPS = 16


def classify_lattice_proof_status(
    has_hard_violation: bool,
    has_sbf_components: bool,
    all_components_complete: bool,
    all_flows_connected: bool,
) -> str:
    """Evaluates continuous lattice proof readiness governed by Bend 2 Law 9."""
    if has_hard_violation:
        return "BROKEN_INVARIANT"
    if has_sbf_components and all_components_complete and all_flows_connected:
        return "PROVEN_COMPLETE"
    return "INCOMPLETE_DRAFT"


def _ancestor_scopes(
    scopes: dict[str, Any], sid: str, max_hops: int = MAX_ANCESTOR_HOPS
) -> set[str]:
    """Walks `scopes[cur].get('parent')` up to `max_hops` hops with cycle protection."""
    ancestors, visited, cur, hops = set(), {sid}, sid, 0
    while cur is not None and hops < max_hops:
        scope_obj = scopes.get(cur)
        parent = scope_obj.get("parent") if bool(scope_obj) else None
        if not isinstance(parent, str) or not parent.strip() or parent.strip() in visited:
            break
        cur = parent.strip()
        ancestors.add(cur)
        visited.add(cur)
        hops += 1
    return ancestors


def _render_board_bend_proof(
    has_viol: bool, has_comp: bool, comp_ok: bool, flow_ok: bool
) -> str:
    """Synthesizes a self-contained Bend 2 module + formal `law` proof for Law 9."""
    bv, bc, bco, bfo = (
        "True{}" if x else "False{}" for x in (has_viol, has_comp, comp_ok, flow_ok)
    )
    law_eq = (
        "is_lp_proven(classify_lattice_proof_status(has_viol, has_comp, comp_ok, flow_ok)) "
        "== Bool.and(Bool.not(has_viol), Bool.and(has_comp, Bool.and(comp_ok, flow_ok)))"
    )
    return (
        "import Base\n\n"
        "type LPStatus is Data:\n  LPBroken{}\n  LPProven{}\n  LPDraft{}\n\n"
        "def is_lp_proven(s: LPStatus) -> Bool:\n  match s:\n"
        "    case LPBroken{}: False{}\n"
        "    case LPProven{}: True{}\n"
        "    case LPDraft{}: False{}\n\n"
        "def classify_lattice_proof_status("
        "has_viol: Bool, has_comp: Bool, comp_ok: Bool, flow_ok: Bool) -> LPStatus:\n"
        "  match has_viol has_comp comp_ok flow_ok:\n"
        "    case True{} has_comp comp_ok flow_ok:\n      LPBroken{}\n"
        "    case False{} True{} True{} True{}:\n      LPProven{}\n"
        "    case False{} True{} True{} False{}:\n      LPDraft{}\n"
        "    case False{} True{} False{} flow_ok:\n      LPDraft{}\n"
        "    case False{} False{} comp_ok flow_ok:\n      LPDraft{}\n\n"
        "law lattice_proof_proven_iff_complete_and_conflict_free:\n"
        "  for +has_viol: Bool\n  for +has_comp: Bool\n"
        "  for +comp_ok: Bool\n  for +flow_ok: Bool\n"
        f"  {{{law_eq} : Bool}}\n\n"
        "def lattice_proof_proven_iff_complete_and_conflict_free("
        "has_viol, has_comp, comp_ok, flow_ok):\n"
        "  match has_viol has_comp comp_ok flow_ok:\n"
        "    case True{} has_comp comp_ok flow_ok: {==}\n"
        "    case False{} True{} True{} True{}: {==}\n"
        "    case False{} True{} True{} False{}: {==}\n"
        "    case False{} True{} False{} flow_ok: {==}\n"
        "    case False{} False{} comp_ok flow_ok: {==}\n\n"
        f"def board_proof_verdict() -> LPStatus:\n"
        f"  classify_lattice_proof_status({bv}, {bc}, {bco}, {bfo})\n"
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
    child_sids = {str(c).strip() for c in (raw_ch or []) if str(c).strip()} | {
        k
        for k, v in scopes.items()
        if isinstance(v, dict) and str(v.get("parent") or "").strip() == sid
    }
    family_sids = {sid, *anc_sids, *child_sids}
    owner = next(
        (
            str(scopes.get(s, {}).get("owner") or "").strip().lower()
            for s in (sid, *anc_sids)
            if scopes.get(s) and str(scopes.get(s, {}).get("owner") or "").strip()
        ),
        "",
    )
    if not owner:
        gaps.append({
            "kind": "MISSING_OWNER",
            "scope": sid,
            "description": f"Component '{sid}' has no assigned division owner.",
        })
    if sc.get("_role_missing") or (
        sc.get("_bootstrapped") and not str(sc.get("role") or "").strip()
    ):
        gaps.append({
            "kind": "MISSING_ROLE",
            "scope": sid,
            "description": f"Component '{sid}' has no Role summary.",
        })
    if sc.get("_why_missing") or (
        sc.get("_bootstrapped") and not str(sc.get("why") or "").strip()
    ):
        gaps.append({
            "kind": "MISSING_WHY",
            "scope": sid,
            "description": f"Component '{sid}' has no Why rationale.",
        })
    for a in artifacts:
        art_sid = a.get("scope") or a.get("scope_id")
        if art_sid == sid and (a.get("dimensions") or {}).get("spec_missing"):
            aname = str(a.get("name") or "")
            gaps.append({
                "kind": "MISSING_SPEC",
                "scope": sid,
                "artifact": aname,
                "description": f"{a.get('kind')} '{aname}' in '{sid}' has no Spec text.",
            })
    has_law = (
        any(
            a.get("kind") in ("LAW", "INTERFACE", "SCHEMA") and a.get("scope") in family_sids
            for a in artifacts
        )
        or any(
            any(scopes.get(s, {}).get(k) for k in ("laws", "files", "rules"))
            for s in family_sids
            if scopes.get(s)
        )
    )
    if not has_law:
        gaps.append({
            "kind": "MISSING_LAW",
            "scope": sid,
            "description": f"Component '{sid}' has no Bend 2 formal law (% LAW).",
        })
    is_qa = sid.startswith(("test_", "tests/", "qa_")) or owner in ("qa", "q-division")
    has_contract = (
        any(
            a.get("kind") in ("SCHEMA", "INTERFACE") and a.get("scope") in family_sids
            for a in artifacts
        )
        or any(
            any(scopes.get(s, {}).get(k) for k in ("inputs", "outputs", "schemas", "interfaces"))
            for s in family_sids
            if scopes.get(s)
        )
        or any(
            (
                str(f.get("source") or "").strip() in family_sids
                or str(f.get("target") or "").strip() in family_sids
            )
            and bool(str(f.get("payload") or "").strip())
            for f in flows
        )
        or (
            is_qa
            and any(
                any(scopes.get(s, {}).get(k) for k in ("rules", "tasks"))
                for s in family_sids
                if scopes.get(s)
            )
        )
    )
    if not has_contract:
        gaps.append({
            "kind": "MISSING_CONTRACT",
            "scope": sid,
            "description": f"Component '{sid}' has no schema, interface, or typed flow payload.",
        })
    if not is_qa and total_owned > 1:
        is_wired = any(
            str(f.get("source") or "").strip() in family_sids
            or str(f.get("target") or "").strip() in family_sids
            for f in flows
        )
        if not is_wired:
            gaps.append({
                "kind": "UNWIRED_COMPONENT",
                "scope": sid,
                "description": f"Component '{sid}' is not connected to any > FLOW edge.",
            })
    return gaps


def evaluate_lattice_proof(graph: dict[str, Any]) -> dict[str, Any]:
    """Evaluates continuous architectural consistency and completeness governed by Law 9."""
    from .frustration import detect_frustration

    if not isinstance(graph, dict):
        graph = {}
    normalize_board_graph(graph)
    scopes, raw_flows, raw_arts = graph["scopes"], graph["flows"], graph["lattice_artifacts"]
    flows = list(raw_flows)
    artifacts = list(raw_arts.values() if isinstance(raw_arts, dict) else raw_arts)
    hard_violations = detect_frustration(graph)
    comp_keys = ("owner", "role", "depends_on", "children", "parent", "files", "_bootstrapped")
    owned_components = [
        sid
        for sid, sc in scopes.items()
        if sid != "global_defaults"
        and not str(sid).startswith("global_defaults::")
        and any(sc.get(k) for k in comp_keys)
    ]
    open_gaps: list[dict[str, str]] = []
    charter_sum = str((graph.get("charter") or {}).get("summary") or "").strip()
    if graph.get("bootstrapped") and not charter_sum:
        open_gaps.append({
            "kind": "MISSING_CHARTER",
            "scope": "charter",
            "description": "Board Charter has no Summary or Principles.",
        })
    for sid in owned_components:
        open_gaps.extend(
            _check_component_proof_gaps(sid, scopes, flows, artifacts, len(owned_components))
        )
    comp_gap_kinds = (
        "MISSING_OWNER",
        "MISSING_LAW",
        "MISSING_CONTRACT",
        "MISSING_CHARTER",
        "MISSING_ROLE",
        "MISSING_WHY",
        "MISSING_SPEC",
    )
    all_components_complete = not any(g["kind"] in comp_gap_kinds for g in open_gaps)
    all_flows_connected = not any(g["kind"] == "UNWIRED_COMPONENT" for g in open_gaps)
    status = classify_lattice_proof_status(
        bool(hard_violations),
        bool(owned_components),
        all_components_complete,
        all_flows_connected,
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
            bool(hard_violations),
            bool(owned_components),
            all_components_complete,
            all_flows_connected,
        ),
    }


def resolve_open_blocker(
    graph: dict[str, Any], blocker_id: str, remove: bool = False
) -> bool:
    """Resolves any open blocker in `graph['open_blockers']` matching `blocker_id`."""
    if not isinstance(graph, dict) or not isinstance(blocker_id, str) or not blocker_id:
        return False
    blockers = graph.get("open_blockers")
    if not isinstance(blockers, list):
        return False
    target_id, matched, kept = str(blocker_id), False, []
    for b in blockers:
        if str(b.get("id", "")) == target_id:
            matched, b["status"] = True, "RESOLVED"
            if not remove:
                kept.append(b)
        else:
            kept.append(b)
    if matched:
        graph["open_blockers"] = kept
        sync_to_sqlite(graph)
    return matched


def _scope_chain_values(
    scopes: dict[str, Any], start_sid: str | None, field_name: str
) -> list[str]:
    """Collects lowercase values for `field_name` along `start_sid` and its ancestors."""
    out: list[str] = []
    cur = str(start_sid or "").strip()
    visited: set[str] = set()
    for _ in range(8):
        if not cur or cur in visited or not isinstance(scopes.get(cur), dict):
            break
        visited.add(cur)
        sc = scopes[cur]
        attrs = sc.get("attributes") or {}
        for cand in (sc.get(field_name), attrs.get(field_name)):
            if cand is not None and str(cand).strip():
                out.append(str(cand).strip().lower())
        cur = str(sc.get("parent") or "").strip()
    return out


def _node_coord_overlap(
    node: dict[str, Any],
    scope_obj: dict[str, Any],
    sid: str | None,
    dims: dict[str, Any],
) -> tuple[bool, bool, bool, bool]:
    scopes, alias_idx = dims["scopes"], dims["alias_idx"]
    component, division = dims["component"], dims["division"]
    stage, schema = dims["stage"], dims["schema"]
    comp_ov = False
    if component and str(component).strip():
        c_low = str(component).strip().lower()
        c_res = str(alias_idx.get(c_low, c_low)).lower()
        comp_cands = [
            str(node[k]).strip().lower()
            for k in ("scope", "scope_id", "id", "parent", "source", "target")
            if node.get(k)
        ]
        for list_k in ("depends_on", "children", "refs", "aliases"):
            comp_cands.extend(
                str(item).strip().lower()
                for item in (node.get(list_k) or [])
                if str(item).strip()
            )
        if scope_obj:
            comp_cands.extend(
                str(scope_obj[k]).strip().lower()
                for k in ("id", "parent")
                if scope_obj.get(k)
            )
            comp_cands.extend(
                str(al).strip().lower()
                for al in (scope_obj.get("aliases") or [])
                if str(al).strip()
            )
        comp_ov = any(
            v in (c_low, c_res)
            or v.startswith(f"{c_low}::")
            or v.startswith(f"{c_res}::")
            for v in comp_cands
        )
    div_ov = False
    if division and str(division).strip():
        d_low = str(division).strip().lower()
        dims_d = node.get("dimensions") or {}
        attrs_d = node.get("attributes") or {}
        raw_div_vals = (
            node.get("owner"),
            node.get("division"),
            dims_d.get("owner"),
            dims_d.get("division"),
            attrs_d.get("owner"),
            attrs_d.get("division"),
        )
        div_cands = [
            str(v).strip().lower() for v in raw_div_vals if v is not None and str(v).strip()
        ]
        for chain_sid in (sid, node.get("source"), node.get("target")):
            div_cands.extend(_scope_chain_values(scopes, chain_sid, "owner"))
            div_cands.extend(_scope_chain_values(scopes, chain_sid, "division"))
        div_ov = any(d_low == v or d_low in v for v in div_cands)
    stg_ov = False
    if stage and str(stage).strip():
        s_low = str(stage).strip().lower()
        dims_s = node.get("dimensions") or {}
        attrs_s = node.get("attributes") or {}
        stg_cands = [
            str(v).strip().lower()
            for v in (node.get("stage"), dims_s.get("stage"), attrs_s.get("stage"))
            if v is not None and str(v).strip()
        ]
        for chain_sid in (sid, node.get("source"), node.get("target")):
            stg_cands.extend(_scope_chain_values(scopes, chain_sid, "stage"))
        stg_ov = any(s_low == v or s_low in v for v in stg_cands)
    sch_ov = False
    if schema and str(schema).strip():
        sch_low = str(schema).strip().lower()
        sch_cands = [
            str(v).strip().lower()
            for v in (node.get("payload"), node.get("name"), node.get("target_prop"))
            if v is not None and str(v).strip()
        ]
        for list_k in ("inputs", "outputs"):
            sch_cands.extend(
                str(item).strip().lower()
                for item in (node.get(list_k) or [])
                if str(item).strip()
            )
            if bool(scope_obj):
                sch_cands.extend(
                    str(item).strip().lower()
                    for item in (scope_obj.get(list_k) or [])
                    if str(item).strip()
                )
        sch_ov = any(sch_low == v or sch_low in v for v in sch_cands)
    return comp_ov, div_ov, stg_ov, sch_ov
