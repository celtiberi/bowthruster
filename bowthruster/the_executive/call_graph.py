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
        self.code = code
        self.errors = list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


_BEND_WRAPPER_FNS = frozenset({"bool_eq", "bool_imp", "and", "or", "not", "true", "false", "eq"})


def is_call_graph_skeleton_valid(interface_defined: bool, law_fn_defined: bool, interface_calls_law: bool) -> bool:
    """Pure AST call-graph skeleton gate governed by `skeleton_call_graph_law`."""
    return bool(interface_defined and law_fn_defined and interface_calls_law)


def _clean_path(raw: str) -> str:
    s = str(raw or "").strip()
    for pfx in ("uri://file/", "uri://", "file://", "./"):
        s = s.removeprefix(pfx)
    return s.split("#", 1)[0].split(":", 1)[0].strip()


def _paths_match(p1: str, p2: str) -> bool:
    a, b = _clean_path(p1), _clean_path(p2)
    return bool(a and b and (a == b or a.endswith(f"/{b}") or b.endswith(f"/{a}")))


def _check_numeric_bound(actual: float, op: str, limit: float) -> bool:
    ops = {
        "<=": actual <= limit, "le": actual <= limit, "at_most": actual <= limit, "max": actual <= limit,
        "<": actual < limit, "lt": actual < limit,
        ">=": actual >= limit, "ge": actual >= limit, "at_least": actual >= limit, "min": actual >= limit,
        ">": actual > limit, "gt": actual > limit,
        "==": actual == limit, "=": actual == limit, "eq": actual == limit,
        "!=": actual != limit, "ne": actual != limit,
    }
    return ops.get(op, True)


def _extract_prohibited_tokens(r: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    if prop := str(r.get("target_prop") or "").strip():
        for piece in prop.replace(";", ",").split(","):
            if (tok := piece.strip().strip("`'\".").lower()) and tok.isidentifier():
                out.add(tok)
    for key in ("prohibited_imports", "banned_imports"):
        if isinstance(raw_list := r.get(key), list):
            for item in raw_list:
                if (tok := str(item).strip().strip("`'\".").lower()) and tok.isidentifier():
                    out.add(tok)
    return out


def _extract_scope_files(sc: dict[str, Any]) -> list[str]:
    attrs = sc.get("attributes") if isinstance(sc.get("attributes"), dict) else {}
    return [
        str(x).strip()
        for k, src in (("files", sc), ("files", attrs), ("_table_files", attrs))
        for x in (src.get(k) if isinstance(src.get(k), list) else [])
        if str(x).strip()
    ]


def _resolve_file_scopes(
    path: str, explicit_scope: str, scopes: dict[str, Any],
    artifacts: list[dict[str, Any]]
) -> list[str]:
    matched: list[str] = [explicit_scope] if (explicit_scope and explicit_scope in scopes) else []
    for sid, sc in scopes.items():
        if sid != "global_defaults" and isinstance(sc, dict) and sid not in matched:
            if any(_paths_match(path, fp) for fp in _extract_scope_files(sc)) or f"/{sid}/" in f"/{path}/" or Path(path).stem == sid:
                matched.append(sid)
    for art in artifacts:
        if (asid := str(art.get("scope") or "").strip()) and asid not in matched and _paths_match(path, str(art.get("file") or "").strip()):
            matched.append(asid)
    return matched


def _normalize_symbol_map(raw_syms: Any) -> tuple[set[str], dict[str, set[str]]]:
    names: set[str] = set()
    params_by_name: dict[str, set[str]] = {}
    if isinstance(raw_syms, dict):
        items = [(str(k).strip(), v) for k, v in raw_syms.items()]
    elif isinstance(raw_syms, (list, tuple, set)):
        items = [
            (item.strip(), None) if isinstance(item, str) else (
                str(item.get("name") or item.get("id") or "").strip(),
                item
            )
            for item in raw_syms if isinstance(item, (str, dict))
        ]
    else:
        items = []
    for nm, val in items:
        if nm:
            names.add(nm)
            raw_p = val if isinstance(val, (list, tuple, set)) else (
                (val.get("args") or val.get("inputs") or val.get("params") or val.get("fields") or [])
                if isinstance(val, dict) else None
            )
            if isinstance(raw_p, (list, tuple, set)):
                params_by_name[nm] = {str(p).strip().lstrip("*") for p in raw_p if str(p).strip()}
    return names, params_by_name


def _unpack_lattice_and_files(
    whiteboard_graph: dict[str, Any] | None,
    staged_diff_report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        raise ExecutiveGateError("gate_invalid_whiteboard", [f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}"])
    if not isinstance(staged_diff_report, dict):
        raise ExecutiveGateError("gate_invalid_staged_diff_report", [f"staged_diff_report must be a dict, got {type(staged_diff_report).__name__}"])
    wb = whiteboard_graph or {}
    scopes = wb.get("scopes") if isinstance(wb.get("scopes"), dict) else {}
    raw_arts = wb.get("lattice_artifacts")
    art_iter = raw_arts.values() if isinstance(raw_arts, dict) else (raw_arts if isinstance(raw_arts, list) else [])
    artifacts = [a for a in art_iter if isinstance(a, dict)]
    raw_files = staged_diff_report.get("files") if isinstance(staged_diff_report.get("files"), list) else (staged_diff_report.get("staged_files") or [])
    return wb, scopes, artifacts, [f for f in raw_files if isinstance(f, dict)]


def _extract_law_fn_name(art: dict[str, Any]) -> str:
    for k in ("fn_name", "function", "helper_fn", "target_fn"):
        if str(art.get(k) or "").strip():
            return str(art[k]).strip().split(".")[-1]
    desc = str(art.get("description") or "")
    expr = str(art.get("lhs") or art.get("formula") or (desc.split(":", 1)[-1] if "(" in desc else "")).strip()
    for part in expr.split("(")[:-1]:
        if tokens := part.replace(":", " ").replace(",", " ").replace("{", " ").replace("=", " ").split():
            cand = tokens[-1].split(".")[-1].strip()
            if cand and cand.isidentifier() and cand.lower() not in _BEND_WRAPPER_FNS and not cand.startswith("check_"):
                return cand
    return ""


_BUILTIN_CONST_NAMES: frozenset[str] = frozenset({
    "True", "False", "None", "bool", "int", "str", "float", "len", "tuple", "list", "dict", "set"
})


def _has_dynamic_name(expr: ast.AST, const_vars: set[str]) -> bool:
    return any(
        isinstance(n, ast.Name) and n.id not in _BUILTIN_CONST_NAMES and n.id not in const_vars
        for n in ast.walk(expr)
    )


def _is_tautological_assert(node: ast.Assert, const_vars: set[str]) -> bool:
    if isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.Or):
        return any(not _has_dynamic_name(v, const_vars) for v in node.test.values)
    return not _has_dynamic_name(node.test, const_vars)


def _is_valid_law_call_args(call_node: ast.Call, const_vars: set[str] | None = None) -> bool:
    args = [*call_node.args, *(kw.value for kw in call_node.keywords)]
    if not args:
        return True
    c_vars = const_vars or set()
    if any(not _has_dynamic_name(a, c_vars) for a in args):
        return False
    return len({ast.dump(a) for a in args}) == len(args)


def _call_name(call_node: ast.Call) -> str:
    if isinstance(call_node.func, ast.Name):
        return call_node.func.id
    return call_node.func.attr if isinstance(call_node.func, ast.Attribute) else ""


def _collect_fn_calls(
    node: ast.AST, law_fns: set[str], loaded_names: set[str] | None = None,
    const_vars: set[str] | None = None
) -> set[str]:
    if const_vars is None:
        const_vars = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Assign) and not _has_dynamic_name(n.value, const_vars):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        const_vars.add(t.id)
    if loaded_names is None:
        taut_nodes = {
            sub
            for ch in ast.walk(node)
            if isinstance(ch, ast.Assert) and _is_tautological_assert(ch, const_vars)
            for sub in ast.walk(ch)
        }
        loaded_names = {
            n.id
            for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n not in taut_nodes
        }
    calls: set[str] = set()
    for ch in ast.iter_child_nodes(node):
        if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(ch, ast.Assert) and _is_tautological_assert(ch, const_vars):
            continue
        if isinstance(ch, ast.Assign):
            targets = [
                t for e in ch.targets for t in (e.elts if isinstance(e, (ast.Tuple, ast.List)) else [e])
                if isinstance(t, ast.Name)
            ]
            if targets and all(t.id == "_" or t.id not in loaded_names for t in targets):
                continue
            for s in ast.walk(ch.value):
                if isinstance(s, ast.Call) and (fn := _call_name(s)):
                    if fn not in law_fns or _is_valid_law_call_args(s, const_vars=const_vars):
                        calls.add(fn)
            continue
        if isinstance(ch, ast.AnnAssign) and isinstance(ch.target, ast.Name):
            if ch.target.id == "_" or ch.target.id not in loaded_names:
                continue
        if isinstance(ch, ast.Expr):
            for s in ast.walk(ch.value):
                if isinstance(s, ast.Call) and (fn := _call_name(s)):
                    if s is ch.value and fn in law_fns and (
                        fn.startswith(("is_", "check_", "verify_", "should_"))
                        or fn.endswith("_law")
                    ):
                        continue
                    if fn not in law_fns or _is_valid_law_call_args(s, const_vars=const_vars):
                        calls.add(fn)
            continue
        if isinstance(ch, ast.Call) and (fn := _call_name(ch)):
            if fn not in law_fns or _is_valid_law_call_args(ch, const_vars=const_vars):
                calls.add(fn)
        calls.update(_collect_fn_calls(ch, law_fns, loaded_names, const_vars))
    return calls


@functools.lru_cache(maxsize=256)
def _cached_ast_nodes(src: str) -> tuple[ast.Module, tuple[ast.AST, ...]]:
    tree = ast.parse(src)
    return tree, tuple(ast.walk(tree))


def _parse_file_call_graph(f: dict[str, Any], law_fns: set[str] | None = None) -> tuple[bool, set[str], dict[str, set[str]]]:
    has_cg = False
    fn_names, _ = _normalize_symbol_map(f.get("ast_functions"))
    cls_names, _ = _normalize_symbol_map(f.get("ast_classes") if "ast_classes" in f else f.get("ast_schemas"))
    lset, defined_fns, call_graph = (law_fns or set()), (fn_names | cls_names), {}
    if isinstance(raw_cg := f.get("ast_call_graph"), dict):
        has_cg = True
        for caller, callees in raw_cg.items():
            if c_name := str(caller).strip():
                defined_fns.add(c_name)
                c_set = {
                    str(x).strip() for x in (callees if isinstance(callees, (list, tuple, set)) else [])
                    if str(x).strip()
                }
                call_graph.setdefault(c_name, set()).update(c_set)
                if "ast_functions" not in f or f.get("ast_functions") is None:
                    defined_fns.update(c_set)
    src = f.get("source") if f.get("source") is not None else (f.get("content") if f.get("content") is not None else f.get("code"))
    if isinstance(src, str) and src.strip():
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
    defined_fns -= {str(x).strip() for x in (f.get("removed_symbols") or []) if str(x).strip()}
    return has_cg, defined_fns, call_graph


@functools.lru_cache(maxsize=256)
def _parse_ast_imports(code_str: str) -> list[tuple[str, int, bool]]:
    res: list[tuple[str, int, bool]] = []
    try:
        for n in _cached_ast_nodes(code_str)[1]:
            if isinstance(n, ast.ImportFrom):
                lvl, dots = int(n.level or 0), "." * int(n.level or 0)
                if n.module:
                    res.extend([(f"{dots}{n.module}", lvl, False), *((f"{dots}{n.module}.{a.name}", lvl, True) for a in n.names)])
                else:
                    res.extend((f"{dots}{a.name}", lvl, False) for a in n.names)
            elif isinstance(n, ast.Import):
                res.extend((a.name, 0, False) for a in n.names)
    except SyntaxError:
        pass
    return res


def _populate_file_ast_metadata(f: dict[str, Any], src: str) -> list[str]:
    eff_imps = list(f.get("imports") or [])
    if f.get("imports") is None:
        eff_imps = [imp.lstrip(".") for imp, _, is_sub in _parse_ast_imports(src) if not is_sub and imp.lstrip(".")]
    try:
        tree, t_nodes = _cached_ast_nodes(src)
        f_keys = {
            s.value for s in t_nodes if isinstance(s, ast.Constant)
            and isinstance(s.value, str) and s.value.isidentifier()
        }
        if f.get("ast_constants") is None:
            f["ast_constants"] = {
                t.id.lower(): float(n.value.value)
                for n in tree.body
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, (int, float)) and not isinstance(n.value.value, bool)
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)
            }
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
                    b_name = b.id if isinstance(b, ast.Name) else (
                        b.value.id if isinstance(b, ast.Subscript)
                        and isinstance(b.value, ast.Name) else ""
                    )
                    if b_name in dict_subclasses:
                        dict_subclasses.add(n.name)
            clss: dict[str, set[str]] = {}
            for n in (x for x in t_nodes if isinstance(x, ast.ClassDef)):
                is_dict_cls = n.name in dict_subclasses or any(
                    (isinstance(b, ast.Name) and b.id in dict_subclasses) or (
                        isinstance(b, ast.Subscript) and isinstance(b.value, ast.Name)
                        and b.value.id in dict_subclasses
                    )
                    for b in n.bases
                )
                ann_fields = {
                    s.target.id for s in n.body if isinstance(s, ast.AnnAssign)
                    and isinstance(s.target, ast.Name)
                }
                assign_fields = {
                    t.id for s in n.body if isinstance(s, ast.Assign)
                    for t in s.targets if isinstance(t, ast.Name)
                }
                method_args = {
                    a.arg for sub in ast.walk(n) if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for a in (*sub.args.posonlyargs, *sub.args.args, *sub.args.kwonlyargs)
                    if a.arg != "self"
                }
                str_consts = {
                    sub.value for sub in ast.walk(n) if isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str) and sub.value.isidentifier()
                }
                clss[n.name] = ann_fields | assign_fields | method_args | str_consts | (f_keys if is_dict_cls else set())
            for n in (
                x for x in tree.body if isinstance(x, ast.Assign)
                and isinstance(x.value, ast.Name) and x.value.id in clss
            ):
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
        str(p).strip() for p in (*(staged_diff_report.get("deleted_files") or []), *(staged_diff_report.get("removed_files") or []))
        if str(p).strip()
    }
    rem_files: list[dict[str, Any]] = []
    for f in staged_files:
        p, st = str(f.get("path") or f.get("file") or "").strip(), str(f.get("status") or "M").strip().upper()
        if st.startswith("D") or bool(f.get("deleted")):
            if p:
                del_paths.add(p)
        else:
            rem_files.append(f)
    rem_files.extend(
        rf if isinstance(rf, dict) else {"path": str(rf)}
        for rf in (staged_diff_report.get("remaining_files") or staged_diff_report.get("remaining_modules") or [])
    )
    if del_paths:
        seen_p = {str(f.get("path") or f.get("file") or "").strip() for f in rem_files}
        for dp in sorted(del_paths):
            if str(pdir := Path(dp).parent) not in ("", ".") and pdir.is_dir():
                for sib in sorted(pdir.glob("*.py")):
                    if (sp := str(sib)) not in seen_p and not any(_paths_match(sp, d) for d in del_paths):
                        seen_p.add(sp)
                        rem_files.append({"path": sp})
    explicit_known = {str(f.get("path") or f.get("file") or "").strip() for f in rem_files if str(f.get("path") or f.get("file") or "").strip()}
    for ep in (*(staged_diff_report.get("existing_files") or []), *(staged_diff_report.get("package_files") or [])):
        if (eps := str(ep).strip()) and not any(_paths_match(eps, d) for d in del_paths):
            explicit_known.add(eps)
    lattice_known = set(explicit_known)
    for ep in (*(fp for sc in scopes.values() if isinstance(sc, dict) for fp in _extract_scope_files(sc)), *(str(a.get("file") or "") for a in artifacts)):
        if (eps := str(ep).strip()) and not any(_paths_match(eps, d) for d in del_paths):
            lattice_known.add(eps)
    missing_mods = {str(m).strip().lstrip(".") for m in (staged_diff_report.get("missing_modules") or []) if str(m).strip()}
    return del_paths, rem_files, explicit_known, lattice_known, missing_mods


def _collect_dangling_imports(
    scopes: dict[str, Any],
    artifacts: list[dict[str, Any]],
    staged_files: list[dict[str, Any]],
    staged_diff_report: dict[str, Any],
) -> list[dict[str, str]]:
    del_paths, rem_files, explicit_known, lattice_known, missing_mods = _prepare_dangling_import_context(
        scopes, artifacts, staged_files, staged_diff_report
    )
    out, seen_reasons = [], set()

    def _add(fpath: str, mod_str: str, raw_mod: str = "") -> None:
        if (m := str(mod_str or "").strip()) and (reason := f"dangling_import:{fpath}:{m}") not in seen_reasons:
            seen_reasons.add(reason)
            f_sc = _resolve_file_scopes(fpath, "", scopes, artifacts)
            alt = f" (dangling_import:{fpath}:{raw_mod})" if raw_mod and raw_mod != m else ""
            out.append({
                "kind": "CONTRACT_VIOLATION", "scope": f_sc[0] if f_sc else "global_defaults",
                "path": fpath, "file": fpath, "module": m, "reason": reason, "description": f"{reason}{alt}"
            })

    raw_top = staged_diff_report.get("dangling_imports")
    top_items = [
        (k, x) for k, v in raw_top.items() for x in (v if isinstance(v, (list, tuple, set)) else [v])
    ] if isinstance(raw_top, dict) else [(None, x) for x in (raw_top if isinstance(raw_top, list) else [])]
    for def_fp, item in [*top_items, *((str(f.get("path") or f.get("file") or "").strip(), x) for f in rem_files for x in (f.get("dangling_imports") or []))]:
        if isinstance(item, dict):
            fp = str(item.get("file") or item.get("path") or item.get("importer") or def_fp or "").strip()
            mod = str(item.get("module") or item.get("target") or item.get("import") or item.get("name") or "").strip()
            _add(fp, mod, mod.lstrip("."))
        elif isinstance(item, str) and item.strip():
            s = item.strip().removeprefix("dangling_import:")
            fp, mod = s.split(":", 1) if (":" in s and def_fp is None) else (
                str(def_fp or (rem_files[0].get("path") or rem_files[0].get("file") if rem_files else "") or "").strip(), s
            )
            _add(fp, mod, mod.lstrip("."))

    for rf in rem_files:
        fpath, imp_specs = str(rf.get("path") or rf.get("file") or "").strip(), []
        for imp in (*(rf.get("imports") or []), *(rf.get("ast_imports") or []), *(rf.get("relative_imports") or [])):
            if isinstance(imp, dict):
                m = str(imp.get("module") or imp.get("target") or imp.get("name") or "").strip()
                lvl = int(imp.get("level") or (1 if imp in (rf.get("relative_imports") or []) else 0))
                imp_specs.append((("." * lvl + m.lstrip(".")) if lvl > 0 else m, max(lvl, len(m) - len(m.lstrip("."))), False))
            elif isinstance(imp, str) and (s := imp.strip()):
                imp_specs.extend(_parse_ast_imports(s) if s.startswith(("from ", "import ")) else [(s, len(s) - len(s.lstrip(".")), False)])
        src = rf.get("source") if rf.get("source") is not None else (rf.get("content") if rf.get("content") is not None else rf.get("code"))
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
                    and (lvl > 0 or "/" not in dp or (fpath and Path(dp).parent == Path(fpath).parent))
                )
                for dp in del_paths
            )
            hit_miss = False
            if not hit_del and not is_sub:
                if clean in missing_mods or raw_mod in missing_mods:
                    hit_miss = True
                elif lvl == 1 and clean != "__init__":
                    proj_root = Path(os.environ.get("BOWTHRUSTER_PROJECT_ROOT") or ".")
                    active_known = explicit_known if (str(base) not in ("", ".") and (base.is_dir() or (proj_root / base).is_dir())) else lattice_known
                    in_known = any(_paths_match(kp, c_py) or _paths_match(kp, c_init) or _paths_match(kp, m_py) or _paths_match(kp, m_init) or Path(kp).stem == clean for kp in active_known)
                    on_disk = str(base) not in ("", ".") and ((base / m_py).exists() or (base / m_init).exists() or (proj_root / base / m_py).exists() or (proj_root / base / m_init).exists())
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
    sid: str,
    ifaces: list[dict[str, Any]],
    law_fn_set: set[str],
    staged_files: list[dict[str, Any]],
    scopes: dict[str, Any],
    artifacts: list[dict[str, Any]],
    single_iface_scope: bool,
) -> tuple[list[dict[str, Any]], set[str]]:
    iface_files = {str(a.get("file") or "").strip() for a in ifaces if str(a.get("file") or "").strip()}
    cand_files = [
        f for f in staged_files
        if sid in _resolve_file_scopes(str(f.get("path") or f.get("file") or "").strip(), str(f.get("scope") or f.get("component") or "").strip(), scopes, artifacts)
        or any(_paths_match(str(f.get("path") or f.get("file") or ""), af) for af in iface_files)
        or (not f.get("path") and not f.get("file") and single_iface_scope)
    ]
    cg_files = [f for f in cand_files if _parse_file_call_graph(f, law_fn_set)[0]]
    if not cg_files:
        return [], iface_files
    proj_root = Path(os.environ.get("BOWTHRUSTER_PROJECT_ROOT") or ".")
    for raw_sfp in (*_extract_scope_files(scopes.get(sid) or {}), *sorted(iface_files)):
        sfp = _clean_path(raw_sfp)
        if sfp.endswith(".py") and not any(_paths_match(str(cf.get("path") or cf.get("file") or ""), sfp) for cf in cg_files):
            dp = (proj_root / sfp) if (proj_root / sfp).is_file() else (Path(sfp) if Path(sfp).is_file() else None)
            if dp is not None:
                cg_files.append({"path": sfp, "source": dp.read_text(encoding="utf-8", errors="ignore")})
    return cg_files, iface_files


def verify_ast_call_graph_skeleton(
    whiteboard_graph: dict[str, Any] | None,
    staged_diff_report: dict[str, Any],
) -> dict[str, Any]:
    """Verifies that each scope's `$ INTERFACE` entrypoints invoke its `% LAW` helper functions in the AST call graph."""
    _, scopes, artifacts, staged_files = _unpack_lattice_and_files(whiteboard_graph, staged_diff_report)
    if not staged_files and any(k in staged_diff_report for k in ("ast_call_graph", "source", "content", "code", "imports", "ast_imports", "relative_imports")):
        staged_files = [staged_diff_report]
    scope_ifaces: dict[str, list[dict[str, Any]]] = {}
    scope_laws: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for art in artifacts:
        akind, asid = str(art.get("kind") or "").upper(), str(art.get("scope") or "").strip()
        if asid and akind == "INTERFACE" and str(art.get("name") or art.get("id") or "").strip():
            scope_ifaces.setdefault(asid, []).append(art)
        elif asid and akind == "LAW" and (lfn := _extract_law_fn_name(art)):
            scope_laws.setdefault(asid, []).append((art, lfn))

    dangling_imports = _collect_dangling_imports(scopes, artifacts, staged_files, staged_diff_report)
    checked_scopes, disconnected_laws, violations, is_valid = [], [], list(dangling_imports), not bool(dangling_imports)

    for sid in sorted(set(scope_ifaces.keys()) & set(scope_laws.keys())):
        ifaces, laws = scope_ifaces[sid], scope_laws[sid]
        law_fn_set = {lfn for _, lfn in laws} | {str(a.get("name") or a.get("id") or "").strip() for a, _ in laws}
        cg_files, iface_files = _collect_scope_call_graph_files(
            sid, ifaces, law_fn_set, staged_files, scopes, artifacts, len(scope_ifaces) == 1
        )
        if not cg_files:
            continue
        checked_scopes.append(sid)
        iface_names = [str(a.get("name") or a.get("id") or "").strip() for a in ifaces]
        sc_fcnt = len(_extract_scope_files(scopes.get(sid) or {}))
        defined_fns, merged_cg, sib_roots, file_dfns_list = set(), {}, set(), []
        cpath = str(cg_files[0].get("path") or cg_files[0].get("file") or next(iter(iface_files), "")).strip()
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
            all(fn in defined_fns for fn in iface_names) or bool(sib_roots) or (sc_fcnt > 1 and any(fn in defined_fns for fn in iface_names))
        )
        unwired_iface_in_law_file = sc_fcnt <= 1 and any(
            bool(dfns & law_fn_set) and len(f_ifaces := [ifn for ifn in active_ifaces if ifn in dfns]) > 1
            and any(not (reach_by_iface[ifn] & (dfns & law_fn_set)) for ifn in f_ifaces)
            for dfns in file_dfns_list
        )
        for law_art, lfn in laws:
            law_id, lfn_def = str(law_art.get("name") or law_art.get("id") or lfn).strip(), (lfn in defined_fns)
            calls_law = bool(lfn_def and (lfn in reachable or lfn in iface_names) and not unwired_iface_in_law_file)
            if not is_call_graph_skeleton_valid(iface_def, lfn_def, calls_law):
                is_valid = False
                disconnected_laws.append({
                    "scope": sid, "law_id": law_id, "law_fn": lfn, "interfaces": iface_names,
                    "path": cpath, "interface_defined": iface_def, "law_fn_defined": lfn_def, "interface_calls_law": calls_law
                })
                violations.append({
                    "kind": "CONTRACT_VIOLATION", "scope": sid, "path": cpath,
                    "description": f"LAW function '{lfn}' ({law_id}) in scope '{sid}' is not defined or not invoked by INTERFACE {iface_names} in {cpath}"
                })

    if staged_diff_report.get("call_graph_ok") is False:
        is_valid = False
        if not disconnected_laws and not dangling_imports:
            disconnected_laws.append({
                "scope": "global_defaults", "law_id": "call_graph_ok", "law_fn": "", "interfaces": [],
                "path": "", "interface_defined": True, "law_fn_defined": True, "interface_calls_law": False
            })
            violations.append({"kind": "CONTRACT_VIOLATION", "scope": "global_defaults", "path": "", "description": "Staged diff report flagged call_graph_ok=False"})
    return {
        "is_valid": is_valid, "disconnected_law_functions": len(disconnected_laws),
        "disconnected_laws": disconnected_laws, "dangling_imports": dangling_imports,
        "checked_scopes": checked_scopes, "violations": violations,
    }
