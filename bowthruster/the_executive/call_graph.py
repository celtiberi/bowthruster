"""AST Call-Graph `% LAW` Reachability Verifier (`call_graph.py`)."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

from bowthruster.the_executive import (
    _check_numeric_bound,
    _clean_path,
    _extract_prohibited_tokens,
    _extract_scope_files,
    _first_list,
    _first_present,
    _first_str,
    _normalize_file_entry,
    _normalize_symbol_map,
)
from bowthruster.the_executive.ast_imports import (
    _cached_ast_nodes,
    _collect_dangling_imports,
    _parse_ast_imports,
    _paths_match,
    _populate_file_ast_metadata,
    _resolve_file_scopes,
)

_BEND_WRAPPER_FNS = frozenset({"bool_eq", "bool_imp", "and", "or", "not", "true", "false", "eq"})
_BUILTIN_CONST_NAMES = frozenset({
    "True", "False", "None", "bool", "int", "str", "float", "len", "tuple", "list", "dict", "set",
})


class ExecutiveGateError(ValueError):
    """Raised when `the_executive` gate receives a malformed preflight report or whiteboard graph."""

    def __init__(self, code: str, errors: list[str]) -> None:
        self.code = code
        self.errors = list(errors)
        super().__init__(f"{code}: {', '.join(self.errors)}")


def is_call_graph_skeleton_valid(
    interface_defined: bool,
    law_fn_defined: bool,
    interface_calls_law: bool,
) -> bool:
    """Pure AST call-graph skeleton gate governed by `skeleton_call_graph_law`."""
    return bool(interface_defined and law_fn_defined and interface_calls_law)


def _unpack_lattice_and_files(
    whiteboard_graph: dict[str, Any] | None,
    staged_diff_report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        msg = f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}"
        raise ExecutiveGateError("gate_invalid_whiteboard", [msg])
    if not isinstance(staged_diff_report, dict):
        msg = f"staged_diff_report must be a dict, got {type(staged_diff_report).__name__}"
        raise ExecutiveGateError("gate_invalid_staged_diff_report", [msg])
    wb = whiteboard_graph or {}
    scopes = wb.get("scopes") or {}
    raw_arts = wb.get("lattice_artifacts")
    if isinstance(raw_arts, dict):
        artifacts = list(raw_arts.values())
    elif isinstance(raw_arts, list):
        artifacts = list(raw_arts)
    else:
        artifacts = []
    raw_files = _first_list(staged_diff_report, "files", "staged_files")
    return wb, scopes, artifacts, [_normalize_file_entry(f) for f in raw_files]


def _extract_law_fn_name(art: dict[str, Any]) -> str:
    direct = _first_str(art, "fn_name", "function", "helper_fn", "target_fn")
    if direct:
        return direct.split(".")[-1]
    desc = _first_str(art, "description")
    expr = _first_str(art, "lhs", "formula")
    if not expr and "(" in desc:
        expr = desc.split(":", 1)[-1].strip()
    for part in expr.split("(")[:-1]:
        cleaned = part.replace(":", " ").replace(",", " ").replace("{", " ").replace("=", " ")
        tokens = cleaned.split()
        if tokens:
            cand = tokens[-1].split(".")[-1].strip()
            is_valid = (
                bool(cand)
                and cand.isidentifier()
                and cand.lower() not in _BEND_WRAPPER_FNS
                and not cand.startswith("check_")
            )
            if is_valid:
                return cand
    return ""


def _has_dynamic_variable(expr: ast.AST, const_vars: set[str]) -> bool:
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and node.id not in _BUILTIN_CONST_NAMES:
            if node.id not in const_vars:
                return True
    return False


def _is_valid_law_call_args(call_node: ast.Call, const_vars: set[str] | None = None) -> bool:
    args = [*call_node.args, *(kw.value for kw in call_node.keywords)]
    if not args:
        return True
    known_consts = const_vars or set()
    all_dynamic = all(_has_dynamic_variable(arg, known_consts) for arg in args)
    no_duplicate_args = len({ast.dump(arg) for arg in args}) == len(args)
    return all_dynamic and no_duplicate_args


def _call_name(call_node: ast.Call) -> str:
    if isinstance(call_node.func, ast.Name):
        return call_node.func.id
    if isinstance(call_node.func, ast.Attribute):
        return call_node.func.attr
    return ""


def _collect_fn_calls(
    node: ast.AST,
    law_fns: set[str],
    loaded_names: set[str] | None = None,
    const_vars: set[str] | None = None,
) -> set[str]:
    if const_vars is None:
        const_vars = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and not _has_dynamic_variable(sub.value, const_vars):
                const_vars.update(t.id for t in sub.targets if isinstance(t, ast.Name))
    if loaded_names is None:
        loaded_names = {
            sub.id
            for sub in ast.walk(node)
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
        }

    calls: set[str] = set()
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.Assign):
            targets = [
                t
                for elt in child.targets
                for t in (elt.elts if isinstance(elt, (ast.Tuple, ast.List)) else [elt])
                if isinstance(t, ast.Name)
            ]
            if targets and all(t.id == "_" or t.id not in loaded_names for t in targets):
                continue
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            if child.target.id == "_" or child.target.id not in loaded_names:
                continue
        if isinstance(child, ast.Expr):
            for sub in ast.walk(child.value):
                if isinstance(sub, ast.Call):
                    fn = _call_name(sub)
                    if not fn or (sub is child.value and fn in law_fns):
                        continue
                    if fn not in law_fns or _is_valid_law_call_args(sub, const_vars):
                        calls.add(fn)
            continue
        if isinstance(child, ast.Call):
            fn = _call_name(child)
            if fn and (fn not in law_fns or _is_valid_law_call_args(child, const_vars)):
                calls.add(fn)
        calls.update(_collect_fn_calls(child, law_fns, loaded_names, const_vars))
    return calls


def _parse_file_call_graph(
    file_entry: dict[str, Any],
    law_fns: set[str] | None = None,
) -> tuple[bool, set[str], dict[str, set[str]]]:
    _normalize_file_entry(file_entry)
    has_cg = False
    fn_names = _normalize_symbol_map(file_entry.get("ast_functions"))[0]
    cls_names = _normalize_symbol_map(
        _first_present(file_entry, "ast_classes", "ast_schemas")
    )[0]
    law_set = law_fns or set()
    defined_fns = fn_names | cls_names
    call_graph: dict[str, set[str]] = {}

    raw_cg = file_entry.get("ast_call_graph")
    if isinstance(raw_cg, dict):
        has_cg = True
        for caller, callees in raw_cg.items():
            caller_name = str(caller).strip()
            if not caller_name:
                continue
            defined_fns.add(caller_name)
            callee_iter = callees if isinstance(callees, (list, tuple, set)) else []
            callee_set = {str(x).strip() for x in callee_iter if str(x).strip()}
            call_graph.setdefault(caller_name, set()).update(callee_set)
            if file_entry.get("ast_functions") is None:
                defined_fns.update(callee_set)

    src = file_entry.get("source")
    if isinstance(src, str) and src.strip():
        try:
            _, nodes = _cached_ast_nodes(src)
            has_cg = True
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defined_fns.add(node.name)
                    callees = _collect_fn_calls(node, law_set)
                    call_graph.setdefault(node.name, set()).update(callees)
                elif isinstance(node, ast.ClassDef):
                    defined_fns.add(node.name)
        except SyntaxError:
            pass

    removed = {str(x).strip() for x in _first_list(file_entry, "removed_symbols") if str(x).strip()}
    defined_fns -= removed
    return has_cg, defined_fns, call_graph


def _reachable_from_roots(roots: list[str], merged_cg: dict[str, set[str]]) -> set[str]:
    reachable: set[str] = set()
    queue = list(roots)
    visited = set(roots)
    while queue:
        caller = queue.pop(0)
        for callee in merged_cg.get(caller, set()):
            reachable.add(callee)
            if callee not in visited and callee in merged_cg:
                visited.add(callee)
                queue.append(callee)
    return reachable


def _collect_scope_call_graph_files(
    sid: str,
    ifaces: list[dict[str, Any]],
    law_fn_set: set[str],
    staged_files: list[dict[str, Any]],
    scopes: dict[str, Any],
    artifacts: list[dict[str, Any]],
    single_iface_scope: bool,
) -> tuple[list[dict[str, Any]], set[str]]:
    iface_files = {_first_str(a, "file") for a in ifaces if _first_str(a, "file")}
    cand_files = [
        f
        for f in staged_files
        if sid in _resolve_file_scopes(f["path"], f["scope"], scopes, artifacts)
        or any(_paths_match(f["path"], af) for af in iface_files)
        or (not f["path"] and single_iface_scope)
    ]
    cg_files = [f for f in cand_files if _parse_file_call_graph(f, law_fn_set)[0]]
    if not cg_files:
        return [], iface_files

    proj_root = Path(os.environ.get("BOWTHRUSTER_PROJECT_ROOT") or ".")
    declared_paths = (*_extract_scope_files(scopes.get(sid) or {}), *sorted(iface_files))
    for raw_sfp in declared_paths:
        sfp = _clean_path(raw_sfp)
        if not sfp.endswith(".py") or any(_paths_match(cf["path"], sfp) for cf in cg_files):
            continue
        disk_path = proj_root / sfp if (proj_root / sfp).is_file() else Path(sfp)
        if disk_path.is_file():
            content = disk_path.read_text(encoding="utf-8", errors="ignore")
            cg_files.append(_normalize_file_entry({"path": sfp, "source": content}))
    return cg_files, iface_files


def _resolve_require_call_graph_laws(
    wb: dict[str, Any],
    staged_diff_report: dict[str, Any],
    explicit_flag: bool | None,
) -> bool:
    if explicit_flag is not None:
        return bool(explicit_flag)
    if "require_call_graph_laws" in staged_diff_report:
        return bool(staged_diff_report["require_call_graph_laws"])
    if "require_call_graph_laws" in wb:
        return bool(wb["require_call_graph_laws"])
    return True


def verify_ast_call_graph_skeleton(
    whiteboard_graph: dict[str, Any] | None,
    staged_diff_report: dict[str, Any],
    *,
    require_call_graph_laws: bool | None = None,
) -> dict[str, Any]:
    """Verifies that non-law `$ INTERFACE` entrypoints invoke `% LAW` helpers in the AST call graph."""
    wb, scopes, artifacts, staged_files = _unpack_lattice_and_files(
        whiteboard_graph, staged_diff_report
    )
    direct_keys = (
        "ast_call_graph", "source", "content", "code",
        "imports", "ast_imports", "relative_imports",
    )
    if not staged_files and any(k in staged_diff_report for k in direct_keys):
        staged_files = [_normalize_file_entry(dict(staged_diff_report))]

    dangling_imports = _collect_dangling_imports(scopes, artifacts, staged_files, staged_diff_report)
    enforce_laws = _resolve_require_call_graph_laws(wb, staged_diff_report, require_call_graph_laws)
    if not enforce_laws:
        return {
            "is_valid": not bool(dangling_imports),
            "call_graph_laws_enforced": False,
            "disconnected_law_functions": 0,
            "disconnected_laws": [],
            "dangling_imports": dangling_imports,
            "checked_scopes": [],
            "violations": list(dangling_imports),
        }

    scope_ifaces: dict[str, list[dict[str, Any]]] = {}
    scope_laws: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for art in artifacts:
        akind = _first_str(art, "kind").upper()
        asid = _first_str(art, "scope")
        if asid and akind == "INTERFACE" and _first_str(art, "name", "id"):
            scope_ifaces.setdefault(asid, []).append(art)
        elif asid and akind == "LAW":
            law_fn = _extract_law_fn_name(art)
            if law_fn:
                scope_laws.setdefault(asid, []).append((art, law_fn))

    checked_scopes: list[str] = []
    disconnected_laws: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = list(dangling_imports)
    is_valid = not bool(dangling_imports)

    for sid in sorted(set(scope_ifaces.keys()) & set(scope_laws.keys())):
        ifaces = scope_ifaces[sid]
        laws = scope_laws[sid]
        law_fn_set = {lfn for _, lfn in laws} | {_first_str(a, "name", "id") for a, _ in laws}
        cg_files, iface_files = _collect_scope_call_graph_files(
            sid, ifaces, law_fn_set, staged_files, scopes, artifacts, len(scope_ifaces) == 1
        )
        if not cg_files:
            continue

        checked_scopes.append(sid)
        raw_iface_names = [_first_str(a, "name", "id") for a in ifaces]
        op_iface_names = [fn for fn in raw_iface_names if fn not in law_fn_set]
        scope_file_count = len(_extract_scope_files(scopes.get(sid) or {}))

        defined_fns: set[str] = set()
        merged_cg: dict[str, set[str]] = {}
        sib_roots: set[str] = set()
        file_dfns_list: list[set[str]] = []
        primary_path = cg_files[0]["path"] or next(iter(iface_files), "")

        for cf in cg_files:
            _, dfns, cg = _parse_file_call_graph(cf, law_fn_set)
            defined_fns |= dfns
            file_dfns_list.append(dfns)
            if (len(cg_files) > 1 or scope_file_count > 1) and not (dfns & set(op_iface_names)):
                sib_roots.update(
                    fn for fn in dfns if not fn.startswith("_") and fn not in law_fn_set
                )
            for caller, callees in cg.items():
                merged_cg.setdefault(caller, set()).update(callees)

        active_ifaces = [fn for fn in op_iface_names if fn in defined_fns]
        reachable = _reachable_from_roots(active_ifaces + sorted(sib_roots), merged_cg)
        reach_by_iface = {ifn: _reachable_from_roots([ifn], merged_cg) for ifn in active_ifaces}

        iface_def = bool(op_iface_names) and (
            all(fn in defined_fns for fn in op_iface_names)
            or bool(sib_roots)
            or (scope_file_count > 1 and any(fn in defined_fns for fn in op_iface_names))
        )

        unwired_iface_in_law_file = False
        if scope_file_count <= 1:
            for dfns in file_dfns_list:
                file_laws = dfns & law_fn_set
                file_ifaces = [ifn for ifn in active_ifaces if ifn in dfns]
                if file_laws and len(file_ifaces) > 1:
                    if any(not (reach_by_iface[ifn] & file_laws) for ifn in file_ifaces):
                        unwired_iface_in_law_file = True

        for law_art, lfn in laws:
            law_id = _first_str(law_art, "name", "id") or lfn
            lfn_def = lfn in defined_fns
            calls_law = bool(lfn_def and lfn in reachable and not unwired_iface_in_law_file)
            if not is_call_graph_skeleton_valid(iface_def, lfn_def, calls_law):
                is_valid = False
                disconnected_laws.append({
                    "scope": sid,
                    "law_id": law_id,
                    "law_fn": lfn,
                    "interfaces": op_iface_names,
                    "path": primary_path,
                    "interface_defined": iface_def,
                    "law_fn_defined": lfn_def,
                    "interface_calls_law": calls_law,
                })
                desc = (
                    f"LAW function '{lfn}' ({law_id}) in scope '{sid}' is not defined "
                    f"or not invoked by INTERFACE {op_iface_names} in {primary_path}"
                )
                violations.append({
                    "kind": "CONTRACT_VIOLATION",
                    "scope": sid,
                    "path": primary_path,
                    "description": desc,
                })

    if staged_diff_report.get("call_graph_ok") is False:
        is_valid = False
        if not disconnected_laws and not dangling_imports:
            disconnected_laws.append({
                "scope": "global_defaults",
                "law_id": "call_graph_ok",
                "law_fn": "",
                "interfaces": [],
                "path": "",
                "interface_defined": True,
                "law_fn_defined": True,
                "interface_calls_law": False,
            })
            violations.append({
                "kind": "CONTRACT_VIOLATION",
                "scope": "global_defaults",
                "path": "",
                "description": "Staged diff report flagged call_graph_ok=False",
            })

    return {
        "is_valid": is_valid,
        "call_graph_laws_enforced": bool(require_call_graph_laws),
        "disconnected_law_functions": len(disconnected_laws),
        "disconnected_laws": disconnected_laws,
        "dangling_imports": dangling_imports,
        "checked_scopes": checked_scopes,
        "violations": violations,
    }
