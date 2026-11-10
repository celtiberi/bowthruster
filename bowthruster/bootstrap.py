"""Deterministic, zero-LLM repository bootstrapper for BowThruster (`bowthruster.bootstrap`)."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

from bowthruster.whiteboard.frustration import evaluate_lattice_proof
from bowthruster.whiteboard.mutations import ensure_scope_node

_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", "node_modules",
    "build", "dist", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".whiteboard", "tests", "test", "docs", "doc", "examples", "benchmarks", "scripts", "wet_lab",
})
_SKIP_FILES = frozenset({"setup.py", "conftest.py", "_pep517.py"})
_PROPOSAL_NOTE = (
    "Candidate component boundaries were proposed mechanically from package/module structure. "
    "You may keep them as-is, or merge/split components by editing the Component table Files column via `turn`."
)


def _slug(raw: str) -> str:
    chars = [ch if (ch.isalnum() or ch == "_") else "_" for ch in raw.strip().lower()]
    out = "_".join(part for part in "".join(chars).split("_") if part)
    return out or "core"


def _escape_cell(text: str) -> str:
    return str(text).replace("|", "\\|").strip()


def _clean_ann(node: ast.AST | None) -> str:
    if node is None:
        return "Any"
    try:
        return " ".join(ast.unparse(node).split())
    except Exception:
        return "Any"


def _is_test_file(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    if any(p in ("tests", "test") for p in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    return name.startswith("test_") or name.endswith("_test.py")


def _iter_repo_py_files(repo_root: Path) -> tuple[list[str], list[str]]:
    src_files: list[str] = []
    test_files: list[str] = []
    for path in sorted(repo_root.rglob("*.py")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        parts = rel.parts
        if any(p.startswith(".") or p.endswith(".egg-info") for p in parts):
            continue
        if any(p in _SKIP_DIRS for p in parts[:-1]):
            if _is_test_file(rel.as_posix()) and not any(
                p in _SKIP_DIRS - {"tests", "test"} for p in parts[:-1]
            ):
                test_files.append(rel.as_posix())
            continue
        if parts[-1] in _SKIP_FILES:
            continue
        if _is_test_file(rel.as_posix()):
            test_files.append(rel.as_posix())
        else:
            src_files.append(rel.as_posix())
    return src_files, test_files


def _discover_candidate_components(repo_root: Path) -> tuple[dict[str, list[str]], list[str]]:
    src_files, test_files = _iter_repo_py_files(repo_root)
    if not src_files:
        return {}, test_files
    strip_src = all(Path(f).parts[0] == "src" and len(Path(f).parts) > 1 for f in src_files)
    rel_parts = [Path(f).parts[1:] if strip_src else Path(f).parts for f in src_files]
    top_dirs = {p[0] for p in rel_parts if len(p) > 1}
    has_root_py = any(len(p) == 1 for p in rel_parts)
    unwrap_single_pkg = len(top_dirs) == 1 and not has_root_py
    comp_map: dict[str, list[str]] = {}
    root_init_files: list[str] = []
    for orig_rel, parts in zip(src_files, rel_parts):
        eff = parts[1:] if unwrap_single_pkg else parts
        if len(eff) == 1 and eff[0] == "__init__.py":
            root_init_files.append(orig_rel)
            continue
        comp_raw = eff[0] if len(eff) > 1 else Path(eff[0]).stem
        cid = _slug(comp_raw)
        comp_map.setdefault(cid, []).append(orig_rel)
    if root_init_files:
        pkg_cid = _slug(next(iter(top_dirs))) if top_dirs else "core"
        target_cid = pkg_cid if (pkg_cid in comp_map or not comp_map) else next(iter(comp_map))
        comp_map.setdefault(target_cid, [])
        comp_map[target_cid] = root_init_files + comp_map[target_cid]
    return comp_map, test_files


def _extract_class_fields(node: ast.ClassDef) -> list[str]:
    fields: list[str] = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fname = stmt.target.id
            if not fname.startswith("_"):
                fields.append(f"{fname}: {_clean_ann(stmt.annotation)}")
    if fields:
        return fields
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "__init__":
            for arg in list(stmt.args.posonlyargs) + list(stmt.args.args) + list(stmt.args.kwonlyargs):
                if arg.arg not in ("self", "cls") and not arg.arg.startswith("_"):
                    fields.append(f"{arg.arg}: {_clean_ann(arg.annotation)}")
            break
    return fields or ["value: Any"]


def _extract_func_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], list[str]]:
    inputs: list[str] = []
    for arg in list(node.args.posonlyargs) + list(node.args.args):
        if arg.arg in ("self", "cls"):
            continue
        inputs.append(f"{arg.arg}: {_clean_ann(arg.annotation)}" if arg.annotation else arg.arg)
    if node.args.vararg:
        va = node.args.vararg
        inputs.append(f"*{va.arg}: {_clean_ann(va.annotation)}" if va.annotation else f"*{va.arg}")
    for arg in node.args.kwonlyargs:
        inputs.append(f"{arg.arg}: {_clean_ann(arg.annotation)}" if arg.annotation else arg.arg)
    if node.args.kwarg:
        ka = node.args.kwarg
        inputs.append(f"**{ka.arg}: {_clean_ann(ka.annotation)}" if ka.annotation else f"**{ka.arg}")
    outputs = [_clean_ann(node.returns)] if node.returns is not None else ["Any"]
    return inputs, outputs


def _extract_component_symbols(
    repo_root: Path, rel_files: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    schemas: list[dict[str, Any]] = []
    interfaces: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    ordered = sorted(rel_files, key=lambda f: (Path(f).name == "__init__.py", f))
    for rel_path in ordered:
        fpath = repo_root / rel_path
        if not fpath.is_file():
            continue
        try:
            tree = ast.parse(fpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        local_funcs: dict[str, tuple[int, int]] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local_funcs[node.name] = (node.lineno, getattr(node, "end_lineno", node.lineno))
        for node in tree.body:
            end_ln = getattr(node, "end_lineno", node.lineno)
            anchor = f"{rel_path}#L{node.lineno}-L{end_ln}"
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_") and node.name not in seen_names:
                seen_names.add(node.name)
                schemas.append({
                    "name": node.name, "fields": _extract_class_fields(node), "file": anchor, "spec": "",
                })
            elif (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_")
                and node.name != "main"
                and node.name not in seen_names
            ):
                seen_names.add(node.name)
                inputs, outputs = _extract_func_signature(node)
                calls: list[str] = []
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                        callee = sub.func.id
                        if callee != node.name and callee in local_funcs:
                            c_s, c_e = local_funcs[callee]
                            ref = f"{rel_path}:{callee}#L{c_s}-L{c_e}"
                            if ref not in calls:
                                calls.append(ref)
                interfaces.append({
                    "name": node.name, "inputs": inputs, "outputs": outputs,
                    "file": anchor, "calls": calls, "spec": "",
                })
    return schemas, interfaces, seen_names


def _build_module_and_symbol_maps(
    comp_files: dict[str, list[str]], comp_symbols: dict[str, set[str]]
) -> tuple[dict[str, str], dict[str, str]]:
    mod_to_comp: dict[str, str] = {}
    for cid, files in comp_files.items():
        for rel_f in files:
            p = Path(rel_f)
            parts = list(p.parts[1:] if p.parts[0] == "src" and len(p.parts) > 1 else p.parts)
            if parts[-1] == "__init__.py":
                mod_parts = parts[:-1]
            else:
                mod_parts = parts[:-1] + [Path(parts[-1]).stem]
            if mod_parts:
                full_mod = ".".join(mod_parts)
                mod_to_comp[full_mod] = cid
                if len(mod_parts) > 1:
                    mod_to_comp.setdefault(".".join(mod_parts[1:]), cid)
                mod_to_comp.setdefault(mod_parts[-1], cid)
    sym_to_comp: dict[str, str] = {}
    for cid, syms in comp_symbols.items():
        for s in syms:
            sym_to_comp.setdefault(s, cid)
    return mod_to_comp, sym_to_comp


def _extract_file_import_targets(
    fpath: Path, rel_f: str, mod_to_comp: dict[str, str], sym_to_comp: dict[str, str],
    *, top_level_only: bool = False,
) -> set[str]:
    targets: set[str] = set()
    try:
        tree = ast.parse(fpath.read_text(encoding="utf-8"))
    except Exception:
        return targets
    pkg_parts = list(Path(rel_f).parts[:-1])
    if pkg_parts and pkg_parts[0] == "src":
        pkg_parts = pkg_parts[1:]
    nodes = list(tree.body) if top_level_only else list(ast.walk(tree))
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in mod_to_comp:
                    targets.add(mod_to_comp[alias.name])
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and pkg_parts:
                up = max(0, len(pkg_parts) - (node.level - 1))
                prefix = ".".join(pkg_parts[:up])
                base = f"{prefix}.{base}".strip(".") if prefix else base
            if base in mod_to_comp:
                targets.add(mod_to_comp[base])
            for alias in node.names:
                sub_mod = f"{base}.{alias.name}".strip(".") if base else alias.name
                if sub_mod in mod_to_comp:
                    targets.add(mod_to_comp[sub_mod])
                elif alias.name in sym_to_comp:
                    targets.add(sym_to_comp[alias.name])
    return targets


def _extract_component_dependencies(
    repo_root: Path, comp_files: dict[str, list[str]], comp_symbols: dict[str, set[str]]
) -> dict[str, list[str]]:
    mod_to_comp, sym_to_comp = _build_module_and_symbol_maps(comp_files, comp_symbols)
    all_deps: dict[str, set[str]] = {cid: set() for cid in comp_files}
    top_deps: dict[str, set[str]] = {cid: set() for cid in comp_files}
    for cid, files in comp_files.items():
        for rel_f in files:
            fp = repo_root / rel_f
            all_deps[cid] |= _extract_file_import_targets(fp, rel_f, mod_to_comp, sym_to_comp) - {cid}
            top_deps[cid] |= _extract_file_import_targets(
                fp, rel_f, mod_to_comp, sym_to_comp, top_level_only=True
            ) - {cid}
    for cid in sorted(all_deps):
        for other in list(all_deps[cid]):
            if cid in all_deps.get(other, set()):
                if other not in top_deps[cid] and cid in top_deps[other]:
                    all_deps[cid].discard(other)
                elif other in top_deps[cid] and cid not in top_deps[other]:
                    all_deps[other].discard(cid)
                elif cid > other:
                    all_deps[cid].discard(other)
    return {cid: sorted(deps) for cid, deps in all_deps.items()}


def _match_component_tests(
    repo_root: Path, comp_files: dict[str, list[str]], comp_symbols: dict[str, set[str]], test_files: list[str]
) -> dict[str, list[str]]:
    mod_to_comp, sym_to_comp = _build_module_and_symbol_maps(comp_files, comp_symbols)
    matched: dict[str, list[str]] = {cid: [] for cid in comp_files}
    for tf in test_files:
        stem = Path(tf).stem.lower()
        clean_stem = stem.removeprefix("test_").removesuffix("_test")
        direct_hits = [cid for cid in comp_files if cid == clean_stem or clean_stem.startswith(f"{cid}_")]
        targets = set(direct_hits) if direct_hits else _extract_file_import_targets(
            repo_root / tf, tf, mod_to_comp, sym_to_comp
        )
        for cid in sorted(targets):
            if cid in matched and tf not in matched[cid]:
                matched[cid].append(tf)
    return matched


def _build_onboarding_packet(
    board: str, cid: str, snode: dict[str, Any], artifacts: list[dict[str, Any]], open_gaps: list[dict[str, Any]]
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
            "", "| Interface | Component | Inputs | Outputs | File | Spec |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
        for fn in ifaces:
            dims = fn.get("dimensions") or {}
            spec_txt = "" if dims.get("spec_missing") else _escape_cell(str(fn.get("spec") or ""))
            ins = _escape_cell(", ".join(str(x) for x in (dims.get("inputs") or [])))
            outs = _escape_cell(", ".join(str(x) for x in (dims.get("outputs") or [])))
            lines.append(f"| {fn.get('name')} | {cid} | {ins} | {outs} | {dims.get('file') or ''} | {spec_txt} |")
    template = "\n".join(lines)
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
        "pipe_table_template": template,
    }


def _populate_bootstrapped_board(
    graph: dict[str, Any], repo_root: Path, comp_files: dict[str, list[str]],
    comp_tests: dict[str, list[str]], comp_deps: dict[str, list[str]],
    comp_schemas: dict[str, list[dict[str, Any]]], comp_ifaces: dict[str, list[dict[str, Any]]],
) -> None:
    graph["bootstrapped"] = True
    charter = graph.setdefault("charter", {})
    charter.setdefault("summary", "")
    charter.setdefault("principles", [])
    scopes = graph.setdefault("scopes", {})
    artifacts = graph.setdefault("lattice_artifacts", [])
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
                    "scope": cid, "scope_id": cid, "kind": "SCHEMA", "name": sc["name"], "spec": "",
                    "inputs": list(sc["fields"]), "outputs": [], "file": sc["file"],
                    "dimensions": {
                        "_from_table": True, "fields": list(sc["fields"]),
                        "file": sc["file"], "spec_missing": True,
                    },
                })
        for fn in comp_ifaces.get(cid) or []:
            key = (cid, "INTERFACE", fn["name"])
            if key not in existing_art_map:
                artifacts.append({
                    "id": f"interface_{cid}_{_slug(fn['name'])}",
                    "scope": cid, "scope_id": cid, "kind": "INTERFACE", "name": fn["name"], "spec": "",
                    "inputs": list(fn["inputs"]), "outputs": list(fn["outputs"]), "file": fn["file"],
                    "dimensions": {
                        "_from_table": True, "inputs": list(fn["inputs"]), "outputs": list(fn["outputs"]),
                        "file": fn["file"], "calls": list(fn["calls"]), "spec_missing": True,
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
            "", "[Charter Template (fill in Summary & Principles via `turn`)]:",
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
