"""Deterministic, zero-LLM repository bootstrapper for BowThruster (`bowthruster.bootstrap`)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from bowthruster.bootstrap_ast import (
    _discover_candidate_components,
    _escape_cell,
    _extract_component_dependencies,
    _extract_component_symbols,
    _match_component_tests,
    _slug,
)
from bowthruster.whiteboard.frustration import evaluate_lattice_proof
from bowthruster.whiteboard.mutations import ensure_scope_node

_PROPOSAL_NOTE = (
    "Candidate component boundaries were proposed mechanically from package/module structure. "
    "You may keep them as-is, or merge/split components by editing the Component table Files column via `turn`."
)


def _build_onboarding_packet(
    board: str,
    cid: str,
    snode: dict[str, Any],
    artifacts: list[dict[str, Any]],
    open_gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    files = list(snode.get("files") or [])
    tests = list(snode.get("tests") or [])
    deps = list(snode.get("depends_on") or [])
    role = "" if snode.get("_role_missing") else str(snode.get("role") or "")
    why = "" if snode.get("_why_missing") else str(snode.get("why") or "")
    comp_arts = [a for a in artifacts if str(a.get("scope") or "") == cid]
    schemas = [a for a in comp_arts if str(a.get("kind") or "").upper() == "SCHEMA"]
    ifaces = [a for a in comp_arts if str(a.get("kind") or "").upper() == "INTERFACE"]
    c_gaps = [g for g in open_gaps if g.get("scope") in (cid, "charter")]
    comp_row = (
        f"| {cid} | {_escape_cell(role)} | {', '.join(files)} | "
        f"{', '.join(deps)} | {', '.join(tests)} | {_escape_cell(why)} |"
    )
    lines = [
        "| Component | Role | Files | Depends_On | Tests | Why |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
        comp_row,
    ]
    if schemas:
        lines.extend(["", "| Schema | Component | Fields | File | Spec |", "| :--- | :--- | :--- | :--- | :--- |"])
        for sc in schemas:
            dims = sc.get("dimensions") or {}
            spec_txt = "" if dims.get("spec_missing") else _escape_cell(str(sc.get("spec") or ""))
            flds = _escape_cell(", ".join(str(x) for x in (dims.get("fields") or [])))
            lines.append(f"| {sc.get('name')} | {cid} | {flds} | {dims.get('file') or ''} | {spec_txt} |")
    if ifaces:
        lines.extend([
            "",
            "| Interface | Component | Inputs | Outputs | File | Spec |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
        for fn in ifaces:
            dims = fn.get("dimensions") or {}
            spec_txt = "" if dims.get("spec_missing") else _escape_cell(str(fn.get("spec") or ""))
            ins = _escape_cell(", ".join(str(x) for x in (dims.get("inputs") or [])))
            outs = _escape_cell(", ".join(str(x) for x in (dims.get("outputs") or [])))
            lines.append(f"| {fn.get('name')} | {cid} | {ins} | {outs} | {dims.get('file') or ''} | {spec_txt} |")
    return {
        "packet_id": f"pkt_onboard_{cid}",
        "component": cid,
        "board": board,
        "status": "ONBOARDING_DRAFT" if c_gaps else "COMPLETE",
        "proposal_note": _PROPOSAL_NOTE,
        "files": files,
        "tests": tests,
        "depends_on": deps,
        "schema_count": len(schemas),
        "interface_count": len(ifaces),
        "open_gaps": c_gaps,
        "pipe_table_template": "\n".join(lines),
    }


def _declared_law_helpers_by_comp(artifacts: list[dict[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for art in artifacts:
        if str(art.get("kind") or "").upper() != "LAW":
            continue
        cid = str(art.get("scope") or art.get("scope_id") or "")
        lhs = str(art.get("lhs") or (art.get("dimensions") or {}).get("lhs") or "").strip()
        head = lhs.split("(", 1)[0].strip() if "(" in lhs else ""
        fn_name = head.split(".")[-1].strip() if head else ""
        if cid and fn_name:
            out.setdefault(cid, set()).add(fn_name)
    return out


def _populate_bootstrapped_board(
    graph: dict[str, Any],
    repo_root: Path,
    comp_files: dict[str, list[str]],
    comp_tests: dict[str, list[str]],
    comp_deps: dict[str, list[str]],
    comp_schemas: dict[str, list[dict[str, Any]]],
    comp_ifaces: dict[str, list[dict[str, Any]]],
) -> None:
    graph["bootstrapped"] = True
    charter = graph.setdefault("charter", {})
    charter.setdefault("summary", "")
    charter.setdefault("principles", [])
    graph.setdefault("scopes", {})
    artifacts = graph.setdefault("lattice_artifacts", [])
    law_helpers = _declared_law_helpers_by_comp(artifacts)
    existing_art_map = {(str(a.get("scope")), str(a.get("kind")), str(a.get("name"))): a for a in artifacts}
    fhashes = graph.setdefault("file_hashes", {})
    for cid, files in comp_files.items():
        snode = ensure_scope_node(graph, cid)
        snode["_bootstrapped"] = True
        if not snode.get("role") or snode.get("_role_missing"):
            snode["role"] = ""
            snode["_role_missing"] = True
        if not snode.get("why") or snode.get("_why_missing"):
            snode["why"] = ""
            snode["_why_missing"] = True
        snode["files"] = list(files)
        snode["tests"] = list(comp_tests.get(cid) or [])
        snode["depends_on"] = list(comp_deps.get(cid) or [])
        for rel_f in snode["files"] + snode["tests"]:
            fp = repo_root / rel_f
            if fp.is_file():
                fhashes[rel_f] = hashlib.sha256(fp.read_bytes()).hexdigest()[:16]
        for sc in comp_schemas.get(cid) or []:
            key = (cid, "SCHEMA", sc["name"])
            if key not in existing_art_map:
                artifacts.append({
                    "id": f"schema_{cid}_{_slug(sc['name'])}",
                    "scope": cid,
                    "scope_id": cid,
                    "kind": "SCHEMA",
                    "name": sc["name"],
                    "spec": "",
                    "inputs": list(sc["fields"]),
                    "outputs": [],
                    "file": sc["file"],
                    "dimensions": {
                        "_from_table": True,
                        "fields": list(sc["fields"]),
                        "file": sc["file"],
                        "spec_missing": True,
                    },
                })
        for fn in comp_ifaces.get(cid) or []:
            if fn["name"] in law_helpers.get(cid, set()):
                continue
            key = (cid, "INTERFACE", fn["name"])
            if key not in existing_art_map:
                artifacts.append({
                    "id": f"interface_{cid}_{_slug(fn['name'])}",
                    "scope": cid,
                    "scope_id": cid,
                    "kind": "INTERFACE",
                    "name": fn["name"],
                    "spec": "",
                    "inputs": list(fn["inputs"]),
                    "outputs": list(fn["outputs"]),
                    "file": fn["file"],
                    "dimensions": {
                        "_from_table": True,
                        "inputs": list(fn["inputs"]),
                        "outputs": list(fn["outputs"]),
                        "file": fn["file"],
                        "calls": list(fn["calls"]),
                        "spec_missing": True,
                    },
                })


def bootstrap_repository(
    repo_root: Path, board: str, graph: dict[str, Any] | None = None, *, component: str | None = None
) -> dict[str, Any]:
    """Deterministically extracts candidate components, public symbols, imports, and tests into a draft board."""
    eff_root = repo_root.resolve()
    target_graph = graph if isinstance(graph, dict) else {}
    if not (target_graph.get("scopes") or {}):
        comp_files, test_files = _discover_candidate_components(eff_root)
        comp_schemas: dict[str, list[dict[str, Any]]] = {}
        comp_ifaces: dict[str, list[dict[str, Any]]] = {}
        comp_syms: dict[str, set[str]] = {}
        for cid, files in comp_files.items():
            sc_list, fn_list, syms = _extract_component_symbols(eff_root, files)
            comp_schemas[cid] = sc_list
            comp_ifaces[cid] = fn_list
            comp_syms[cid] = syms
        comp_deps = _extract_component_dependencies(eff_root, comp_files, comp_syms)
        comp_tests = _match_component_tests(eff_root, comp_files, comp_syms, test_files)
        _populate_bootstrapped_board(
            target_graph, eff_root, comp_files, comp_tests, comp_deps, comp_schemas, comp_ifaces
        )
    proof = evaluate_lattice_proof(target_graph)
    target_graph["lattice_proof"] = proof
    scopes = target_graph.get("scopes") or {}
    artifacts = target_graph.get("lattice_artifacts") or []
    open_gaps = list(proof.get("open_gaps") or [])
    cids = [component] if (component and component in scopes) else sorted(scopes.keys())
    packets = [_build_onboarding_packet(board, cid, scopes[cid], artifacts, open_gaps) for cid in cids]
    charter_tpl = "| Charter | Summary | Principles |\n| :--- | :--- | :--- |\n| Project Charter | | |"
    return {
        "board": board,
        "status": proof.get("status", "INCOMPLETE_DRAFT"),
        "is_proven": bool(proof.get("is_proven", False)),
        "proposal_note": _PROPOSAL_NOTE,
        "candidate_component_count": len(scopes),
        "schema_count": sum(1 for a in artifacts if str(a.get("kind") or "").upper() == "SCHEMA"),
        "interface_count": sum(1 for a in artifacts if str(a.get("kind") or "").upper() == "INTERFACE"),
        "open_gap_count": len(open_gaps),
        "open_gaps": open_gaps,
        "charter_template": charter_tpl,
        "onboarding_packets": packets,
    }


def format_bootstrap_report(payload: dict[str, Any]) -> str:
    """Renders a bounded human/agent-readable summary of a `bootstrap_repository` payload."""
    lines = [
        f"=== BOOTSTRAP DRAFT BOARD '{payload.get('board')}' ===",
        f"Status:       {payload.get('status')} (is_proven={payload.get('is_proven')})",
        f"Extracted:    components={payload.get('candidate_component_count', 0)}, "
        f"schemas={payload.get('schema_count', 0)}, interfaces={payload.get('interface_count', 0)}",
        f"Open Gaps:    {payload.get('open_gap_count', 0)}",
        f"Note:         {payload.get('proposal_note')}",
    ]
    if any(g.get("kind") == "MISSING_CHARTER" for g in (payload.get("open_gaps") or [])):
        lines.extend([
            "",
            "[Charter Template (fill in Summary & Principles via `turn`)]:",
            str(payload.get("charter_template") or ""),
        ])
    for pkt in payload.get("onboarding_packets") or []:
        gaps = pkt.get("open_gaps") or []
        lines.extend([
            "",
            f"--- ONBOARDING PACKET: {pkt.get('packet_id')} (component={pkt.get('component')}, "
            f"status={pkt.get('status')}, gaps={len(gaps)}) ---",
            str(pkt.get("pipe_table_template") or ""),
        ])
    return "\n".join(lines)
