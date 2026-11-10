"""AST Metadata Extraction & Dangling-Import Verifier (`ast_imports.py`)."""

from __future__ import annotations

import ast
import functools
import os
from pathlib import Path
from typing import Any

from bowthruster.the_executive import (
    _clean_path,
    _extract_scope_files,
    _first_list,
    _first_str,
    _normalize_file_entry,
)


def _paths_match(p1: str, p2: str) -> bool:
    a, b = _clean_path(p1), _clean_path(p2)
    return bool(a and b and (a == b or a.endswith(f"/{b}") or b.endswith(f"/{a}")))


def _resolve_file_scopes(
    path: str,
    explicit_scope: str,
    scopes: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    matched: list[str] = [explicit_scope] if (explicit_scope and explicit_scope in scopes) else []
    for sid, sc in scopes.items():
        if sid != "global_defaults" and sid not in matched:
            matches_fp = any(_paths_match(path, fp) for fp in _extract_scope_files(sc))
            if matches_fp or f"/{sid}/" in f"/{path}/" or Path(path).stem == sid:
                matched.append(sid)
    for art in artifacts:
        asid = _first_str(art, "scope")
        if asid and asid not in matched and _paths_match(path, _first_str(art, "file")):
            matched.append(asid)
    return matched


@functools.lru_cache(maxsize=256)
def _cached_ast_nodes(src: str) -> tuple[ast.Module, tuple[ast.AST, ...]]:
    tree = ast.parse(src)
    return tree, tuple(ast.walk(tree))


@functools.lru_cache(maxsize=256)
def _parse_ast_imports(code_str: str) -> list[tuple[str, int, bool]]:
    imports: list[tuple[str, int, bool]] = []
    try:
        _, nodes = _cached_ast_nodes(code_str)
    except SyntaxError:
        return imports
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            level = int(node.level or 0)
            dots = "." * level
            if node.module:
                imports.append((f"{dots}{node.module}", level, False))
                for alias in node.names:
                    imports.append((f"{dots}{node.module}.{alias.name}", level, True))
            else:
                for alias in node.names:
                    imports.append((f"{dots}{alias.name}", level, False))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, 0, False))
    return imports


def _extract_class_metadata(
    tree: ast.Module,
    nodes: tuple[ast.AST, ...],
    file_ident_strings: set[str],
) -> dict[str, list[str]]:
    dict_subclasses = {"dict", "TypedDict"}
    class_nodes = [n for n in nodes if isinstance(n, ast.ClassDef)]
    for cls_node in class_nodes:
        for base in cls_node.bases:
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Subscript) and isinstance(base.value, ast.Name):
                base_name = base.value.id
            if base_name in dict_subclasses:
                dict_subclasses.add(cls_node.name)

    classes: dict[str, set[str]] = {}
    for cls_node in class_nodes:
        is_dict_cls = cls_node.name in dict_subclasses
        if not is_dict_cls:
            for base in cls_node.bases:
                if isinstance(base, ast.Name) and base.id in dict_subclasses:
                    is_dict_cls = True
                elif (
                    isinstance(base, ast.Subscript)
                    and isinstance(base.value, ast.Name)
                    and base.value.id in dict_subclasses
                ):
                    is_dict_cls = True
        ann_fields = {
            s.target.id
            for s in cls_node.body
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
        }
        assign_fields = {
            t.id
            for s in cls_node.body
            if isinstance(s, ast.Assign)
            for t in s.targets
            if isinstance(t, ast.Name)
        }
        method_args = {
            arg.arg
            for sub in ast.walk(cls_node)
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
            for arg in (*sub.args.posonlyargs, *sub.args.args, *sub.args.kwonlyargs)
            if arg.arg != "self"
        }
        str_consts = {
            sub.value
            for sub in ast.walk(cls_node)
            if isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and sub.value.isidentifier()
        }
        extra_keys = file_ident_strings if is_dict_cls else set()
        classes[cls_node.name] = ann_fields | assign_fields | method_args | str_consts | extra_keys

    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Name)
            and stmt.value.id in classes
        ):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    classes[target.id] = classes[stmt.value.id]
    return {name: sorted(fields) for name, fields in classes.items()}


def _populate_file_ast_metadata(file_entry: dict[str, Any], src: str) -> list[str]:
    if file_entry.get("imports") is not None:
        eff_imports = _first_list(file_entry, "imports")
    else:
        eff_imports = [
            imp.lstrip(".")
            for imp, _, is_sub in _parse_ast_imports(src)
            if not is_sub and imp.lstrip(".")
        ]
    try:
        tree, nodes = _cached_ast_nodes(src)
    except SyntaxError:
        return eff_imports

    file_ident_strings = {
        s.value
        for s in nodes
        if isinstance(s, ast.Constant) and isinstance(s.value, str) and s.value.isidentifier()
    }
    if file_entry.get("ast_constants") is None:
        constants: dict[str, float] = {}
        for stmt in tree.body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and isinstance(
                stmt.value, ast.Constant
            ):
                val = stmt.value.value
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            constants[target.id.lower()] = float(val)
        file_entry["ast_constants"] = constants

    if file_entry.get("ast_functions") is None:
        functions: dict[str, list[str]] = {}
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                all_args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                params = [a.arg for a in all_args if a.arg != "self"]
                if node.args.vararg:
                    params.append("args")
                if node.args.kwarg:
                    params.append("kwargs")
                functions.setdefault(node.name, []).extend(params)
        file_entry["ast_functions"] = functions

    if file_entry.get("ast_classes") is None and file_entry.get("ast_schemas") is None:
        file_entry["ast_classes"] = _extract_class_metadata(tree, nodes, file_ident_strings)
    return eff_imports


def _prepare_dangling_import_context(
    scopes: dict[str, Any],
    artifacts: list[dict[str, Any]],
    staged_files: list[dict[str, Any]],
    staged_diff_report: dict[str, Any],
) -> tuple[set[str], list[dict[str, Any]], set[str], set[str], set[str]]:
    raw_deleted = (
        *_first_list(staged_diff_report, "deleted_files"),
        *_first_list(staged_diff_report, "removed_files"),
    )
    del_paths = {str(p).strip() for p in raw_deleted if str(p).strip()}
    rem_files: list[dict[str, Any]] = []
    for file_entry in staged_files:
        _normalize_file_entry(file_entry)
        if file_entry["status"].startswith("D") or bool(file_entry.get("deleted")):
            if file_entry["path"]:
                del_paths.add(file_entry["path"])
        else:
            rem_files.append(file_entry)

    for rem in _first_list(staged_diff_report, "remaining_files", "remaining_modules"):
        entry = rem if isinstance(rem, dict) else {"path": str(rem)}
        rem_files.append(_normalize_file_entry(entry))

    if del_paths:
        seen_paths = {f["path"] for f in rem_files}
        for del_path in sorted(del_paths):
            parent_dir = Path(del_path).parent
            if str(parent_dir) not in ("", ".") and parent_dir.is_dir():
                for sib in sorted(parent_dir.glob("*.py")):
                    sib_str = str(sib)
                    if sib_str not in seen_paths and not any(
                        _paths_match(sib_str, d) for d in del_paths
                    ):
                        seen_paths.add(sib_str)
                        rem_files.append(_normalize_file_entry({"path": sib_str}))

    explicit_known = {f["path"] for f in rem_files if f["path"]}
    known_lists = (
        *_first_list(staged_diff_report, "existing_files"),
        *_first_list(staged_diff_report, "package_files"),
    )
    for existing in known_lists:
        clean_ep = str(existing).strip()
        if clean_ep and not any(_paths_match(clean_ep, d) for d in del_paths):
            explicit_known.add(clean_ep)

    lattice_known = set(explicit_known)
    scope_files = (fp for sc in scopes.values() if sc for fp in _extract_scope_files(sc))
    for path_cand in (*scope_files, *(_first_str(a, "file") for a in artifacts)):
        clean_cand = str(path_cand).strip()
        if clean_cand and not any(_paths_match(clean_cand, d) for d in del_paths):
            lattice_known.add(clean_cand)

    missing_mods = {
        str(m).strip().lstrip(".")
        for m in _first_list(staged_diff_report, "missing_modules")
        if str(m).strip()
    }
    return del_paths, rem_files, explicit_known, lattice_known, missing_mods


def _extract_declared_import_specs(file_entry: dict[str, Any]) -> list[tuple[str, int, bool]]:
    specs: list[tuple[str, int, bool]] = []
    rel_imports = _first_list(file_entry, "relative_imports")
    raw_imports = (
        *_first_list(file_entry, "imports"),
        *_first_list(file_entry, "ast_imports"),
        *rel_imports,
    )
    for imp in raw_imports:
        if isinstance(imp, dict):
            mod = _first_str(imp, "module", "target", "name")
            lvl = int(imp.get("level") or (1 if imp in rel_imports else 0))
            prefixed = ("." * lvl + mod.lstrip(".")) if lvl > 0 else mod
            dot_lvl = max(lvl, len(mod) - len(mod.lstrip(".")))
            specs.append((prefixed, dot_lvl, False))
        elif isinstance(imp, str) and imp.strip():
            clean_imp = imp.strip()
            if clean_imp.startswith(("from ", "import ")):
                specs.extend(_parse_ast_imports(clean_imp))
            else:
                specs.append((clean_imp, len(clean_imp) - len(clean_imp.lstrip(".")), False))
    return specs


def _is_dangling_module_import(
    fpath: str,
    raw_mod: str,
    lvl: int,
    is_sub: bool,
    del_paths: set[str],
    missing_mods: set[str],
    explicit_known: set[str],
    lattice_known: set[str],
) -> bool:
    clean = raw_mod.lstrip(".")
    if not clean:
        return False
    mod_py = clean.replace(".", "/") + ".py"
    mod_init = clean.replace(".", "/") + "/__init__.py"
    base = Path(fpath).parent if fpath else Path(".")
    for _ in range(max(0, lvl - 1)):
        base = base.parent
    if str(base) not in ("", "."):
        cand_py, cand_init = str(base / mod_py), str(base / mod_init)
    else:
        cand_py, cand_init = mod_py, mod_init

    for dp in del_paths:
        if any(_paths_match(dp, target) for target in (cand_py, cand_init, mod_py, mod_init)):
            return True
        dp_path = Path(dp)
        dp_name = dp_path.parent.name if dp_path.name == "__init__.py" else dp_path.stem
        same_dir_or_rel = lvl > 0 or "/" not in dp or (fpath and dp_path.parent == Path(fpath).parent)
        if not is_sub and dp_name == clean and same_dir_or_rel:
            return True

    if is_sub:
        return False
    if clean in missing_mods or raw_mod in missing_mods:
        return True
    if lvl == 1 and clean != "__init__":
        proj_root = Path(os.environ.get("BOWTHRUSTER_PROJECT_ROOT") or ".")
        has_local_dir = str(base) not in ("", ".") and (base.is_dir() or (proj_root / base).is_dir())
        active_known = explicit_known if has_local_dir else lattice_known
        in_known = any(
            _paths_match(kp, cand_py)
            or _paths_match(kp, cand_init)
            or _paths_match(kp, mod_py)
            or _paths_match(kp, mod_init)
            or Path(kp).stem == clean
            for kp in active_known
        )
        on_disk = str(base) not in ("", ".") and (
            (base / mod_py).exists()
            or (base / mod_init).exists()
            or (proj_root / base / mod_py).exists()
            or (proj_root / base / mod_init).exists()
        )
        return not (in_known or on_disk)
    return False


def _collect_dangling_imports(
    scopes: dict[str, Any],
    artifacts: list[dict[str, Any]],
    staged_files: list[dict[str, Any]],
    staged_diff_report: dict[str, Any],
) -> list[dict[str, str]]:
    del_paths, rem_files, explicit_known, lattice_known, missing_mods = (
        _prepare_dangling_import_context(scopes, artifacts, staged_files, staged_diff_report)
    )
    violations: list[dict[str, str]] = []
    seen_reasons: set[str] = set()

    def _record(fpath: str, mod_str: str, raw_mod: str = "") -> None:
        mod = str(mod_str or "").strip()
        reason = f"dangling_import:{fpath}:{mod}"
        if not mod or reason in seen_reasons:
            return
        seen_reasons.add(reason)
        file_scopes = _resolve_file_scopes(fpath, "", scopes, artifacts)
        scope_id = file_scopes[0] if file_scopes else "global_defaults"
        alt = f" (dangling_import:{fpath}:{raw_mod})" if raw_mod and raw_mod != mod else ""
        violations.append({
            "kind": "CONTRACT_VIOLATION",
            "scope": scope_id,
            "path": fpath,
            "file": fpath,
            "module": mod,
            "reason": reason,
            "description": f"{reason}{alt}",
        })

    raw_top = staged_diff_report.get("dangling_imports")
    if isinstance(raw_top, dict):
        top_items: list[tuple[str | None, Any]] = [
            (k, x)
            for k, v in raw_top.items()
            for x in (v if isinstance(v, (list, tuple, set)) else [v])
        ]
    else:
        top_items = [(None, x) for x in (raw_top or [])]
    file_items = [
        (f["path"], x) for f in rem_files for x in _first_list(f, "dangling_imports")
    ]
    for default_fp, item in [*top_items, *file_items]:
        if isinstance(item, dict):
            fp = _first_str(item, "file", "path", "importer") or str(default_fp or "").strip()
            mod = _first_str(item, "module", "target", "import", "name")
            _record(fp, mod, mod.lstrip("."))
        elif isinstance(item, str) and item.strip():
            raw_item = item.strip().removeprefix("dangling_import:")
            fallback_fp = str(default_fp or (rem_files[0]["path"] if rem_files else "")).strip()
            if ":" in raw_item and default_fp is None:
                fp, mod = raw_item.split(":", 1)
            else:
                fp, mod = fallback_fp, raw_item
            _record(fp, mod, mod.lstrip("."))

    for rf in rem_files:
        fpath = rf["path"]
        imp_specs = _extract_declared_import_specs(rf)
        src = rf["source"]
        if src is None and del_paths and fpath and Path(fpath).is_file():
            src = Path(fpath).read_text(encoding="utf-8", errors="ignore")
        if isinstance(src, str) and src.strip():
            imp_specs.extend(_parse_ast_imports(src))
        for raw_mod, lvl, is_sub in imp_specs:
            if _is_dangling_module_import(
                fpath, raw_mod, lvl, is_sub, del_paths, missing_mods, explicit_known, lattice_known
            ):
                _record(fpath, raw_mod.lstrip("."), raw_mod)
    return violations
