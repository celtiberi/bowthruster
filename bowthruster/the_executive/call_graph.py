"""AST Call-Graph Skeleton, Metadata & Dangling-Import Verifier (`call_graph.py`)."""

from __future__ import annotations

import ast
import functools
import os
from pathlib import Path
from typing import Any

class ExecutiveGateError(ValueError):
    """Raised when `the_executive` gate receives a malformed preflight report or whiteboard graph."""
    def __init__(self, code: str, errors: list[str]) -> None:
        self.code, self.errors = code, list(errors)
        super().__init__(f"{code}: {', '.join(self.errors)}")

_BEND_WRAPPER_FNS = frozenset({"bool_eq", "bool_imp", "and", "or", "not", "true", "false", "eq"})
_BUILTIN_CONST_NAMES: frozenset[str] = frozenset({
    "True", "False", "None", "bool", "int", "str", "float", "len", "tuple", "list", "dict", "set"})

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


def is_call_graph_skeleton_valid(interface_defined: bool, law_fn_defined: bool, interface_calls_law: bool) -> bool:
    """Pure AST call-graph skeleton gate governed by `skeleton_call_graph_law`."""
    return bool(interface_defined and law_fn_defined and interface_calls_law)


def _paths_match(p1: str, p2: str) -> bool:
    a, b = _clean_path(p1), _clean_path(p2)
    return bool(a and b and (a == b or a.endswith(f"/{b}") or b.endswith(f"/{a}")))


def _resolve_file_scopes(
    path: str, explicit_scope: str, scopes: dict[str, Any], artifacts: list[dict[str, Any]]
) -> list[str]:
    matched: list[str] = [explicit_scope] if (explicit_scope and explicit_scope in scopes) else []
    for sid, sc in scopes.items():
        if sid != "global_defaults" and sid not in matched:
            matches_fp = any(_paths_match(path, fp) for fp in _extract_scope_files(sc))
            if matches_fp or f"/{sid}/" in f"/{path}/" or Path(path).stem == sid:
                matched.append(sid)
    for art in artifacts:
        if (asid := _first_str(art, "scope")) and asid not in matched and _paths_match(path, _first_str(art, "file")):
            matched.append(asid)
    return matched

def _unpack_lattice_and_files(whiteboard_graph: dict[str, Any] | None, staged_diff_report: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        raise ExecutiveGateError("gate_invalid_whiteboard",
            [f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}"])
    if not isinstance(staged_diff_report, dict):
        raise ExecutiveGateError("gate_invalid_staged_diff_report",
            [f"staged_diff_report must be a dict, got {type(staged_diff_report).__name__}"])
    wb = whiteboard_graph or {}
    scopes = (wb.get("scopes") or {})
    raw_arts = wb.get("lattice_artifacts")
    art_iter = raw_arts.values() if isinstance(raw_arts, dict) else (raw_arts if isinstance(raw_arts, list) else [])
    artifacts = [a for a in art_iter]
    raw_files = _first_list(staged_diff_report, "files", "staged_files")
    return wb, scopes, artifacts, [_normalize_file_entry(f) for f in raw_files]

def _extract_law_fn_name(art: dict[str, Any]) -> str:
    if direct := _first_str(art, "fn_name", "function", "helper_fn", "target_fn"):
        return direct.split(".")[-1]
    desc = _first_str(art, "description")
    expr = _first_str(art, "lhs", "formula") or (desc.split(":", 1)[-1].strip() if "(" in desc else "")
    for part in expr.split("(")[:-1]:
        if tokens := part.replace(":", " ").replace(",", " ").replace("{", " ").replace("=", " ").split():
            cand = tokens[-1].split(".")[-1].strip()
            if cand and cand.isidentifier() and cand.lower() not in _BEND_WRAPPER_FNS and not cand.startswith("check_"):
                return cand
    return ""

def _has_dynamic_name(expr: ast.AST, const_vars: set[str]) -> bool:
    return any(isinstance(n, ast.Name) and n.id not in _BUILTIN_CONST_NAMES and n.id not in const_vars for n
        in ast.walk(expr))

def _is_valid_law_call_args(call_node: ast.Call, const_vars: set[str] | None = None) -> bool:
    args = [*call_node.args, *(kw.value for kw in call_node.keywords)]
    if not args:
        return True
    c_vars = const_vars or set()
    return not any(not _has_dynamic_name(a, c_vars) for a in args) and len({ast.dump(a) for a in args}) == len(args)

def _call_name(call_node: ast.Call) -> str:
    return call_node.func.id if isinstance(call_node.func, ast.Name) else (call_node.func.attr
        if isinstance(call_node.func, ast.Attribute) else "")

def _collect_fn_calls(
    node: ast.AST, law_fns: set[str], loaded_names: set[str] | None = None, const_vars: set[str] | None = None,
) -> set[str]:
    if const_vars is None:
        const_vars = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Assign) and not _has_dynamic_name(n.value, const_vars):
                const_vars.update(t.id for t in n.targets if isinstance(t, ast.Name))
    if loaded_names is None:
        loaded_names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    calls: set[str] = set()
    for ch in ast.iter_child_nodes(node):
        if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(ch, ast.Assign):
            targets = [t for e in ch.targets for t in (e.elts if isinstance(e, (ast.Tuple, ast.List)) else [e])
                if isinstance(t, ast.Name)]
            if targets and all(t.id == "_" or t.id not in loaded_names for t in targets):
                continue
        if isinstance(ch, ast.AnnAssign) and isinstance(ch.target, ast.Name) and (ch.target.id == "_"
            or ch.target.id not in loaded_names):
            continue
        if isinstance(ch, ast.Expr):
            for s in ast.walk(ch.value):
                if isinstance(s, ast.Call) and (fn := _call_name(s)):
                    if s is ch.value and fn in law_fns:
                        continue
                    if fn not in law_fns or _is_valid_law_call_args(s, const_vars=const_vars):
                        calls.add(fn)
            continue
        if isinstance(ch, ast.Call) and (fn := _call_name(ch)) and (fn not in law_fns or _is_valid_law_call_args(ch,
            const_vars=const_vars)):
            calls.add(fn)
        calls.update(_collect_fn_calls(ch, law_fns, loaded_names, const_vars))
    return calls

@functools.lru_cache(maxsize=256)
def _cached_ast_nodes(src: str) -> tuple[ast.Module, tuple[ast.AST, ...]]:
    tree = ast.parse(src)
    return tree, tuple(ast.walk(tree))

def _parse_file_call_graph(
    f: dict[str, Any], law_fns: set[str] | None = None
) -> tuple[bool, set[str], dict[str, set[str]]]:
    _normalize_file_entry(f)
    has_cg, fn_names = False, _normalize_symbol_map(f.get("ast_functions"))[0]
    cls_names = _normalize_symbol_map(_first_present(f, "ast_classes", "ast_schemas"))[0]
    lset, defined_fns, call_graph = (law_fns or set()), (fn_names | cls_names), {}
    if isinstance(raw_cg := f.get("ast_call_graph"), dict):
        has_cg = True
        for caller, callees in raw_cg.items():
            if c_name := str(caller).strip():
                defined_fns.add(c_name)
                c_set = {str(x).strip() for x in (callees if isinstance(callees, (list, tuple, set)) else [])
                    if str(x).strip()}
                call_graph.setdefault(c_name, set()).update(c_set)
                if f.get("ast_functions") is None:
                    defined_fns.update(c_set)
    if isinstance(src := f.get("source"), str) and src.strip():
        try:
            _, nodes = _cached_ast_nodes(src)
            has_cg = True
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defined_fns.add(node.name)
                    call_graph.setdefault(node.name, set()).update(_collect_fn_calls(node, lset))
                elif isinstance(node, ast.ClassDef):
                    defined_fns.add(node.name)
        except SyntaxError:
            pass
    defined_fns -= {str(x).strip() for x in _first_list(f, "removed_symbols") if str(x).strip()}
    return has_cg, defined_fns, call_graph

@functools.lru_cache(maxsize=256)
def _parse_ast_imports(code_str: str) -> list[tuple[str, int, bool]]:
    res: list[tuple[str, int, bool]] = []
    try:
        for n in _cached_ast_nodes(code_str)[1]:
            if isinstance(n, ast.ImportFrom):
                lvl, dots = int(n.level or 0), "." * int(n.level or 0)
                if n.module:
                    res.extend([(f"{dots}{n.module}", lvl, False), *((f"{dots}{n.module}.{a.name}", lvl, True) for a
                        in n.names)])
                else:
                    res.extend((f"{dots}{a.name}", lvl, False) for a in n.names)
            elif isinstance(n, ast.Import):
                res.extend((a.name, 0, False) for a in n.names)
    except SyntaxError:
        pass
    return res

def _populate_file_ast_metadata(f: dict[str, Any], src: str) -> list[str]:
    eff_imps = _first_list(f, "imports") if f.get("imports") is not None else [
        imp.lstrip(".") for imp, _, is_sub in _parse_ast_imports(src) if not is_sub and imp.lstrip(".")]
    try:
        tree, t_nodes = _cached_ast_nodes(src)
        f_keys = {s.value for s in t_nodes if isinstance(s, ast.Constant) and isinstance(s.value, str)
            and s.value.isidentifier()}
        if f.get("ast_constants") is None:
            f["ast_constants"] = {t.id.lower(): float(n.value.value) for n in tree.body
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, (int, float)) and not isinstance(n.value.value, bool)
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
        if f.get("ast_functions") is None:
            fns: dict[str, list[str]] = {}
            for n in (x for x in t_nodes if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))):
                params = [a.arg for a in (*n.args.posonlyargs, *n.args.args, *n.args.kwonlyargs) if a.arg != "self"]
                params.extend((["args"] if n.args.vararg else []) + (["kwargs"] if n.args.kwarg else []))
                fns.setdefault(n.name, []).extend(params)
            f["ast_functions"] = fns
        if f.get("ast_classes") is None and f.get("ast_schemas") is None:
            dict_subclasses = {"dict", "TypedDict"}
            for n in (x for x in t_nodes if isinstance(x, ast.ClassDef)):
                for b in n.bases:
                    b_name = b.id if isinstance(b, ast.Name) else (b.value.id if isinstance(b, ast.Subscript)
                        and isinstance(b.value, ast.Name) else "")
                    if b_name in dict_subclasses:
                        dict_subclasses.add(n.name)
            clss: dict[str, set[str]] = {}
            for n in (x for x in t_nodes if isinstance(x, ast.ClassDef)):
                is_dict_cls = n.name in dict_subclasses or any((isinstance(b, ast.Name) and b.id in dict_subclasses)
                    or (isinstance(b, ast.Subscript) and isinstance(b.value, ast.Name) and b.value.id
                        in dict_subclasses)
                    for b in n.bases)
                ann_f = {s.target.id for s in n.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)}
                ass_f = {
                    t.id for s in n.body if isinstance(s, ast.Assign) for t in s.targets if isinstance(t, ast.Name)
                }
                m_args = {a.arg for sub in ast.walk(n) if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for a in (*sub.args.posonlyargs, *sub.args.args, *sub.args.kwonlyargs) if a.arg != "self"}
                s_consts = {
                    sub.value
                    for sub in ast.walk(n)
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value.isidentifier()
                }
                clss[n.name] = ann_f | ass_f | m_args | s_consts | (f_keys if is_dict_cls else set())
            for n in (x for x in tree.body if isinstance(x, ast.Assign) and isinstance(x.value, ast.Name)
                and x.value.id in clss):
                for t in (x for x in n.targets if isinstance(x, ast.Name)):
                    clss[t.id] = clss[n.value.id]
            f["ast_classes"] = {k: sorted(v) for k, v in clss.items()}
    except SyntaxError:
        pass
    return eff_imps

def _prepare_dangling_import_context(
    scopes: dict[str, Any],
    artifacts: list[dict[str, Any]],
    staged_files: list[dict[str, Any]],
    staged_diff_report: dict[str, Any],
) -> tuple[set[str], list[dict[str, Any]], set[str], set[str], set[str]]:
    del_paths = {
        str(p).strip() for p in (*_first_list(staged_diff_report, "deleted_files"), *_first_list(staged_diff_report,
            "removed_files"))
        if str(p).strip()}
    rem_files: list[dict[str, Any]] = []
    for f in staged_files:
        _normalize_file_entry(f)
        if f["status"].startswith("D") or bool(f.get("deleted")):
            if f["path"]:
                del_paths.add(f["path"])
        else:
            rem_files.append(f)
    rem_files.extend(_normalize_file_entry(rf if isinstance(rf, dict) else {"path": str(rf)})
        for rf in _first_list(staged_diff_report, "remaining_files", "remaining_modules"))
    if del_paths:
        seen_p = {f["path"] for f in rem_files}
        for dp in sorted(del_paths):
            if str(pdir := Path(dp).parent) not in ("", ".") and pdir.is_dir():
                for sib in sorted(pdir.glob("*.py")):
                    if (sp := str(sib)) not in seen_p and not any(_paths_match(sp, d) for d in del_paths):
                        seen_p.add(sp)
                        rem_files.append(_normalize_file_entry({"path": sp}))
    explicit_known = {f["path"] for f in rem_files if f["path"]}
    for ep in (*_first_list(staged_diff_report, "existing_files"), *_first_list(staged_diff_report, "package_files")):
        if (eps := str(ep).strip()) and not any(_paths_match(eps, d) for d in del_paths):
            explicit_known.add(eps)
    lattice_known = set(explicit_known)
    sc_fps = (fp for sc in scopes.values() if sc for fp in _extract_scope_files(sc))
    for ep in (*sc_fps, *(_first_str(a, "file") for a in artifacts)):
        if (eps := str(ep).strip()) and not any(_paths_match(eps, d) for d in del_paths):
            lattice_known.add(eps)
    missing_mods = {str(m).strip().lstrip(".") for m in _first_list(staged_diff_report, "missing_modules")
        if str(m).strip()}
    return del_paths, rem_files, explicit_known, lattice_known, missing_mods

def _collect_dangling_imports(
    scopes: dict[str, Any],
    artifacts: list[dict[str, Any]],
    staged_files: list[dict[str, Any]],
    staged_diff_report: dict[str, Any],
) -> list[dict[str, str]]:
    del_paths, rem_files, explicit_known, lattice_known, missing_mods = _prepare_dangling_import_context(
        scopes, artifacts, staged_files, staged_diff_report)
    out, seen_reasons = [], set()
    def _add(fpath: str, mod_str: str, raw_mod: str = "") -> None:
        if (m := str(mod_str or "").strip()) and (reason := f"dangling_import:{fpath}:{m}") not in seen_reasons:
            seen_reasons.add(reason)
            f_sc = _resolve_file_scopes(fpath, "", scopes, artifacts)
            alt = f" (dangling_import:{fpath}:{raw_mod})" if raw_mod and raw_mod != m else ""
            out.append({"kind": "CONTRACT_VIOLATION", "scope": f_sc[0] if f_sc else "global_defaults",
                "path": fpath, "file": fpath, "module": m, "reason": reason, "description": f"{reason}{alt}"})
    raw_top = staged_diff_report.get("dangling_imports")
    top_items = ([(k, x) for k, v in raw_top.items() for x in (v if isinstance(v, (list, tuple, set)) else [v])]
        if isinstance(raw_top, dict) else [(None, x) for x in (raw_top or [])])
    for def_fp, item in [*top_items, *((f["path"], x) for f in rem_files for x in _first_list(f, "dangling_imports"))]:
        if isinstance(item, dict):
            fp = _first_str(item, "file", "path", "importer") or str(def_fp or "").strip()
            mod = _first_str(item, "module", "target", "import", "name")
            _add(fp, mod, mod.lstrip("."))
        elif isinstance(item, str) and item.strip():
            s = item.strip().removeprefix("dangling_import:")
            fallback_fp = str(def_fp or (rem_files[0]["path"] if rem_files else "")).strip()
            fp, mod = s.split(":", 1) if (":" in s and def_fp is None) else (fallback_fp, s)
            _add(fp, mod, mod.lstrip("."))
    for rf in rem_files:
        fpath, imp_specs, rel_imps = rf["path"], [], _first_list(rf, "relative_imports")
        for imp in (*_first_list(rf, "imports"), *_first_list(rf, "ast_imports"), *rel_imps):
            if isinstance(imp, dict):
                m = _first_str(imp, "module", "target", "name")
                lvl = int(imp.get("level") or (1 if imp in rel_imps else 0))
                imp_specs.append((("." * lvl + m.lstrip(".")) if lvl > 0 else m, max(lvl, len(m) - len(m.lstrip("."))),
                    False))
            elif isinstance(imp, str) and (s := imp.strip()):
                imp_specs.extend(_parse_ast_imports(s) if s.startswith(("from ", "import ")) else [(s,
                    len(s) - len(s.lstrip(".")), False)])
        src = rf["source"]
        if src is None and del_paths and fpath and Path(fpath).is_file():
            src = Path(fpath).read_text(encoding="utf-8", errors="ignore")
        if isinstance(src, str) and src.strip():
            imp_specs.extend(_parse_ast_imports(src))
        for raw_mod, lvl, is_sub in imp_specs:
            if not (clean := raw_mod.lstrip(".")):
                continue
            m_py, m_init = clean.replace(".", "/") + ".py", clean.replace(".", "/") + "/__init__.py"
            base = Path(fpath).parent if fpath else Path(".")
            for _ in range(max(0, lvl - 1)):
                base = base.parent
            c_py, c_init = (str(base / m_py), str(base / m_init)) if str(base) not in ("", ".") else (m_py, m_init)
            hit_del = any(
                _paths_match(dp, c_py) or _paths_match(dp, c_init) or _paths_match(dp, m_py) or _paths_match(dp, m_init)
                or (
                not is_sub and (Path(dp).parent.name if Path(dp).name == "__init__.py" else Path(dp).stem) == clean
                and (lvl > 0 or "/" not in dp or (fpath and Path(dp).parent == Path(fpath).parent)))
                for dp in del_paths)
            hit_miss = False
            if not hit_del and not is_sub:
                if clean in missing_mods or raw_mod in missing_mods:
                    hit_miss = True
                elif lvl == 1 and clean != "__init__":
                    proj_root = Path(os.environ.get("BOWTHRUSTER_PROJECT_ROOT") or ".")
                    active_known = explicit_known if (str(base) not in ("", ".") and (base.is_dir()
                        or (proj_root / base).is_dir())) else lattice_known
                    in_known = any(
                        _paths_match(kp, c_py) or _paths_match(kp, c_init) or _paths_match(kp, m_py) or _paths_match(kp,
                            m_init) or Path(kp).stem == clean
                        for kp in active_known)
                    on_disk = str(base) not in ("", ".") and (
                        (base / m_py).exists() or (base / m_init).exists()
                        or (proj_root / base / m_py).exists() or (proj_root / base / m_init).exists()
                    )
                    hit_miss = not (in_known or on_disk)
            if hit_del or hit_miss:
                _add(fpath, clean, raw_mod)
    return out

def _reachable_from_roots(roots: list[str], merged_cg: dict[str, set[str]]) -> set[str]:
    reachable, queue, visited = set(), list(roots), set(roots)
    while queue:
        for callee in merged_cg.get(queue.pop(0), set()):
            reachable.add(callee)
            if callee not in visited and callee in merged_cg:
                visited.add(callee)
                queue.append(callee)
    return reachable

def _collect_scope_call_graph_files(
    sid: str, ifaces: list[dict[str, Any]], law_fn_set: set[str], staged_files: list[dict[str, Any]],
    scopes: dict[str, Any], artifacts: list[dict[str, Any]], single_iface_scope: bool,
) -> tuple[list[dict[str, Any]], set[str]]:
    iface_files = {_first_str(a, "file") for a in ifaces if _first_str(a, "file")}
    cand_files = [f for f in staged_files if sid in _resolve_file_scopes(f["path"], f["scope"], scopes, artifacts)
        or any(_paths_match(f["path"], af) for af in iface_files) or (not f["path"] and single_iface_scope)]
    cg_files = [f for f in cand_files if _parse_file_call_graph(f, law_fn_set)[0]]
    if not cg_files:
        return [], iface_files
    proj_root = Path(os.environ.get("BOWTHRUSTER_PROJECT_ROOT") or ".")
    for raw_sfp in (*_extract_scope_files(scopes.get(sid) or {}), *sorted(iface_files)):
        sfp = _clean_path(raw_sfp)
        if sfp.endswith(".py") and not any(_paths_match(cf["path"], sfp) for cf in cg_files):
            dp = (proj_root / sfp) if (proj_root / sfp).is_file() else (Path(sfp) if Path(sfp).is_file() else None)
            if dp is not None:
                cg_files.append(_normalize_file_entry({"path": sfp, "source": dp.read_text(encoding="utf-8",
                    errors="ignore")}))
    return cg_files, iface_files

def verify_ast_call_graph_skeleton(whiteboard_graph: dict[str, Any] | None, staged_diff_report: dict[str, Any],
    ) -> dict[str, Any]:
    """Verifies that each scope's `$ INTERFACE` entrypoints invoke its `% LAW` functions in the AST call graph."""
    _, scopes, artifacts, staged_files = _unpack_lattice_and_files(whiteboard_graph, staged_diff_report)
    direct_keys = ("ast_call_graph", "source", "content", "code", "imports", "ast_imports", "relative_imports")
    if not staged_files and any(k in staged_diff_report for k in direct_keys):
        staged_files = [_normalize_file_entry(dict(staged_diff_report))]
    scope_ifaces: dict[str, list[dict[str, Any]]] = {}
    scope_laws: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for art in artifacts:
        akind, asid = _first_str(art, "kind").upper(), _first_str(art, "scope")
        if asid and akind == "INTERFACE" and _first_str(art, "name", "id"):
            scope_ifaces.setdefault(asid, []).append(art)
        elif asid and akind == "LAW" and (lfn := _extract_law_fn_name(art)):
            scope_laws.setdefault(asid, []).append((art, lfn))
    dangling_imports = _collect_dangling_imports(scopes, artifacts, staged_files, staged_diff_report)
    checked_scopes, disconnected_laws, violations, is_valid = [], [], list(dangling_imports), not bool(dangling_imports)
    for sid in sorted(set(scope_ifaces.keys()) & set(scope_laws.keys())):
        ifaces, laws = scope_ifaces[sid], scope_laws[sid]
        law_fn_set = {lfn for _, lfn in laws} | {_first_str(a, "name", "id") for a, _ in laws}
        cg_files, iface_files = _collect_scope_call_graph_files(
            sid, ifaces, law_fn_set, staged_files, scopes, artifacts, len(scope_ifaces) == 1)
        if not cg_files:
            continue
        checked_scopes.append(sid)
        iface_names = [_first_str(a, "name", "id") for a in ifaces]
        sc_fcnt = len(_extract_scope_files(scopes.get(sid) or {}))
        defined_fns, merged_cg, sib_roots, file_dfns_list = set(), {}, set(), []
        cpath = cg_files[0]["path"] or next(iter(iface_files), "")
        for cf in cg_files:
            _, dfns, cg = _parse_file_call_graph(cf, law_fn_set)
            defined_fns |= dfns
            file_dfns_list.append(dfns)
            if (len(cg_files) > 1 or sc_fcnt > 1) and not (dfns & set(iface_names)):
                sib_roots.update(fn for fn in dfns if not fn.startswith("_") and fn not in law_fn_set)
            for k, v in cg.items():
                merged_cg.setdefault(k, set()).update(v)
        active_ifaces = [fn for fn in iface_names if fn in defined_fns]
        reachable = _reachable_from_roots(active_ifaces + sorted(sib_roots), merged_cg)
        reach_by_iface = {ifn: _reachable_from_roots([ifn], merged_cg) for ifn in active_ifaces}
        iface_def = bool(iface_names) and (
            all(fn in defined_fns for fn in iface_names) or bool(sib_roots) or (sc_fcnt > 1 and any(fn in defined_fns
                for fn in iface_names))
        )
        unwired_iface_in_law_file = sc_fcnt <= 1 and any(
            bool(dfns & law_fn_set) and len(f_ifaces := [ifn for ifn in active_ifaces if ifn in dfns]) > 1
            and any(not (reach_by_iface[ifn] & (dfns & law_fn_set)) for ifn in f_ifaces) for dfns in file_dfns_list)
        for law_art, lfn in laws:
            law_id, lfn_def = (_first_str(law_art, "name", "id") or lfn), (lfn in defined_fns)
            calls_law = bool(lfn_def and (lfn in reachable or lfn in iface_names) and not unwired_iface_in_law_file)
            if not is_call_graph_skeleton_valid(iface_def, lfn_def, calls_law):
                is_valid = False
                disconnected_laws.append({
                    "scope": sid, "law_id": law_id, "law_fn": lfn, "interfaces": iface_names, "path": cpath,
                    "interface_defined": iface_def, "law_fn_defined": lfn_def, "interface_calls_law": calls_law})
                desc = (
                    f"LAW function '{lfn}' ({law_id}) in scope '{sid}' is not defined "
                    f"or not invoked by INTERFACE {iface_names} in {cpath}"
                )
                violations.append({"kind": "CONTRACT_VIOLATION", "scope": sid, "path": cpath, "description": desc})
    if staged_diff_report.get("call_graph_ok") is False:
        is_valid = False
        if not disconnected_laws and not dangling_imports:
            disconnected_laws.append({
                "scope": "global_defaults", "law_id": "call_graph_ok", "law_fn": "", "interfaces": [],
                "path": "", "interface_defined": True, "law_fn_defined": True, "interface_calls_law": False})
            desc = "Staged diff report flagged call_graph_ok=False"
            violations.append({"kind": "CONTRACT_VIOLATION", "scope": "global_defaults", "path": "",
                "description": desc})
    return {"is_valid": is_valid, "disconnected_law_functions": len(disconnected_laws),
        "disconnected_laws": disconnected_laws, "dangling_imports": dangling_imports,
        "checked_scopes": checked_scopes, "violations": violations}
