"""Deterministic AST package, symbol, dependency, and test extractors for `bootstrap` (`bootstrap_ast.py`)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", "node_modules",
    "build", "dist", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".whiteboard", "tests", "test", "docs", "doc", "examples", "benchmarks", "scripts", "wet_lab",
})
_SKIP_FILES = frozenset({"setup.py", "conftest.py"})


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
            all_args = list(stmt.args.posonlyargs) + list(stmt.args.args) + list(stmt.args.kwonlyargs)
            for arg in all_args:
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
                    "name": node.name,
                    "inputs": inputs,
                    "outputs": outputs,
                    "file": anchor,
                    "calls": calls,
                    "spec": "",
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
            mod_parts = parts[:-1] if parts[-1] == "__init__.py" else parts[:-1] + [Path(parts[-1]).stem]
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
    fpath: Path,
    rel_f: str,
    mod_to_comp: dict[str, str],
    sym_to_comp: dict[str, str],
    *,
    top_level_only: bool = False,
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
    repo_root: Path,
    comp_files: dict[str, list[str]],
    comp_symbols: dict[str, set[str]],
    test_files: list[str],
) -> dict[str, list[str]]:
    mod_to_comp, sym_to_comp = _build_module_and_symbol_maps(comp_files, comp_symbols)
    matched: dict[str, list[str]] = {cid: [] for cid in comp_files}
    for tf in test_files:
        stem = Path(tf).stem.lower()
        clean_stem = stem.removeprefix("test_").removesuffix("_test")
        direct_hits = [cid for cid in comp_files if cid == clean_stem or clean_stem.startswith(f"{cid}_")]
        targets = (
            set(direct_hits)
            if direct_hits
            else _extract_file_import_targets(repo_root / tf, tf, mod_to_comp, sym_to_comp)
        )
        for cid in sorted(targets):
            if cid in matched and tf not in matched[cid]:
                matched[cid].append(tf)
    return matched
