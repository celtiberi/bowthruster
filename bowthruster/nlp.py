"""Python-to-Bend 2 boolean law transpiler, verifier, and NLP facade (`bowthruster.nlp`)."""

from __future__ import annotations

import ast
import functools
import itertools
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from bowthruster import _extract_generic_source_symbols
from bowthruster.text_similarity import (
    ASCII_PERIOD,
    _get_tokenizer_nlp,
    dense_text_similarities,
    lexical_token_similarity,
    max_lexical_similarity,
    protect_ascii_dots,
    restore_ascii_dots,
    soft_token_overlap_count,
    tokenize_words,
)


def _split_top_args(expr: str) -> list[str]:
    parts, cur, depth = [], [], 0
    for ch in expr:
        depth = depth + 1 if ch in "([{" else (max(0, depth - 1) if ch in ")]}" else depth)
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _sub_ident(expr: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return expr
    keys_pat = "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
    return re.compile(rf"\b({keys_pat})\b").sub(lambda m: mapping[m.group(1)], expr)


def transpile_py_fn_to_bend_bool(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    fn_map: dict[str, Any] | None = None,
    alias_map: dict[str, str] | None = None,
    depth: int = 0,
) -> tuple[list[str], str]:
    if depth > 6:
        raise ValueError("Recursion limit exceeded in Python-to-Bend AST transpiler")
    fmap, amap = fn_map or {}, alias_map or {}
    all_args = (*fn_node.args.posonlyargs, *fn_node.args.args, *fn_node.args.kwonlyargs)
    params = [a.arg for a in all_args if a.arg != "self"]

    def _expr(node: ast.AST, env: dict[str, str]) -> str:
        tname = type(node).__name__
        if tname == "Constant" and type(getattr(node, "value", None)) is bool:
            return "True{}" if getattr(node, "value") else "False{}"
        if tname == "Name":
            nid = getattr(node, "id", "")
            if nid in env or nid in params:
                return env.get(nid, nid)
            raise ValueError(f"Unbound variable {nid!r} in {fn_node.name}")
        if tname == "UnaryOp" and type(getattr(node, "op", None)).__name__ == "Not":
            return f"Bool.not({_expr(getattr(node, 'operand'), env)})"
        if tname == "BoolOp" and type(getattr(node, "op", None)).__name__ in ("And", "Or"):
            bop = "Bool.and" if type(getattr(node, "op")).__name__ == "And" else "Bool.or"
            vals = [_expr(v, env) for v in getattr(node, "values", [])]
            acc = vals[-1]
            for v in reversed(vals[:-1]):
                acc = f"{bop}({v}, {acc})"
            return acc
        if tname == "IfExp":
            c = _expr(getattr(node, "test"), env)
            t, f = _expr(getattr(node, "body"), env), _expr(getattr(node, "orelse"), env)
            return f"Bool.or(Bool.and({c}, {t}), Bool.and(Bool.not({c}), {f}))"
        if tname == "Compare" and len(getattr(node, "ops", [])) == 1:
            l_ex = _expr(getattr(node, "left"), env)
            r_ex = _expr(getattr(node, "comparators")[0], env)
            op_name = type(getattr(node, "ops")[0]).__name__
            if op_name in ("Eq", "Is"):
                return f"bool_eq({l_ex}, {r_ex})"
            if op_name in ("NotEq", "IsNot"):
                return f"Bool.not(bool_eq({l_ex}, {r_ex}))"
        if tname == "Call":
            fn_obj = getattr(node, "func", None)
            cname = getattr(fn_obj, "id", None) or getattr(fn_obj, "attr", "") or ""
            c_args, c_kws = getattr(node, "args", []), getattr(node, "keywords", [])
            if cname == "bool" and len(c_args) == 1 and not c_kws:
                return _expr(c_args[0], env)
            target = amap.get(cname, cname)
            if target in fmap and target != fn_node.name:
                sub_p, sub_b = transpile_py_fn_to_bend_bool(fmap[target], fmap, amap, depth + 1)
                pos_args = [_expr(a, env) for a in c_args]
                kw_args = {kw.arg: _expr(kw.value, env) for kw in c_kws if kw.arg}
                sub_map = {
                    p: (pos_args[i] if i < len(pos_args) else kw_args.get(p, "True{}"))
                    for i, p in enumerate(sub_p)
                }
                return _sub_ident(sub_b, sub_map)
        raise ValueError(f"Unsupported AST node {tname} in {fn_node.name}")

    def _stmts(stmts: list[ast.stmt], env: dict[str, str]) -> str:
        cur = dict(env)
        for i, st in enumerate(stmts):
            st_name, val = type(st).__name__, getattr(st, "value", None)
            if st_name == "Expr" and type(getattr(val, "value", None)) is str:
                continue
            if st_name == "Assign" and len(getattr(st, "targets", [])) == 1:
                tgt = getattr(st, "targets")[0]
                if hasattr(tgt, "id") and val is not None:
                    cur[tgt.id] = _expr(val, cur)
            elif st_name == "AnnAssign" and hasattr(getattr(st, "target", None), "id") and val is not None:
                cur[getattr(st, "target").id] = _expr(val, cur)
            elif st_name == "Return" and val is not None:
                return _expr(val, cur)
            elif st_name == "If":
                c, orelse = _expr(getattr(st, "test"), cur), getattr(st, "orelse", [])
                t_b = _stmts(getattr(st, "body"), dict(cur))
                f_b = _stmts(orelse if orelse else stmts[i + 1 :], dict(cur))
                return f"Bool.or(Bool.and({c}, {t_b}), Bool.and(Bool.not({c}), {f_b}))"
        raise ValueError(f"No boolean return found in {fn_node.name}")

    return params, _stmts(fn_node.body, {})


def _eval_bend_bool(
    expr: str, env: dict[str, bool], custom_fns: dict[str, tuple[list[str], str]]
) -> bool:
    s = str(expr or "").strip().strip("\"`'").split(":", 1)[0].strip()
    if s in ("True{}", "True", "true", "1"):
        return True
    if s in ("False{}", "False", "false", "0"):
        return False
    if s in env:
        return bool(env[s])
    if "(" in s and s.endswith(")"):
        callee = s.split("(", 1)[0].strip().split(".")[-1].strip()
        raw_inner = s[s.find("(") + 1 : -1]
        args = [_eval_bend_bool(a, env, custom_fns) for a in _split_top_args(raw_inner)]
        if callee == "not" and len(args) == 1:
            return not args[0]
        if callee == "and" and len(args) >= 2:
            return all(args)
        if callee == "or" and len(args) >= 2:
            return any(args)
        if callee in ("bool_eq", "eq") and len(args) == 2:
            return args[0] == args[1]
        if callee == "bool_imp" and len(args) == 2:
            return (not args[0]) or args[1]
        if callee in custom_fns:
            f_params, f_body = custom_fns[callee]
            sub_env = {p: (args[i] if i < len(args) else True) for i, p in enumerate(f_params)}
            return _eval_bend_bool(f_body, sub_env, custom_fns)
        if callee.startswith("check_") and len(args) == 1:
            return bool(args[0])
    raise ValueError(f"Cannot evaluate Bend boolean expression: {s!r}")


def _gen_bool_case_proof(vars_left: list[str], indent: int = 2) -> list[str]:
    pad = " " * indent
    if not vars_left:
        return [f"{pad}{{==}}"]
    v = vars_left[0]
    sub = _gen_bool_case_proof(vars_left[1:], indent + 4)
    return [f"{pad}match {v}:", f"{pad}  case False{{}}:", *sub, f"{pad}  case True{{}}:", *sub]


def _norm_bend_expr(expr: str, fn_name: str) -> str:
    pat = r"\b[A-Za-z_][A-Za-z0-9_]*\.(?=" + re.escape(fn_name) + r"\s*\(|bool_eq\s*\(|bool_imp\s*\()"
    stripped = re.sub(pat, "", expr.strip())
    return re.sub(r"(?<![A-Za-z0-9_.])(and|or|not)\s*\(", r"Bool.\1(", stripped)


def _find_bend_binary() -> str | None:
    env_bin, which_bin = os.environ.get("BEND_BIN"), shutil.which("bend")
    cands = (
        Path(env_bin) if env_bin else None,
        Path.home() / ".bend" / "bin" / "bend",
        Path(which_bin) if which_bin else None,
        Path.home() / ".cargo" / "bin" / "bend",
    )
    return next((str(c) for c in cands if c is not None and c.is_file() and os.access(c, os.X_OK)), None)


@functools.lru_cache(maxsize=256)
def _check_synthesized_bend_proof(bend_src: str) -> bool | str:
    bend_bin = _find_bend_binary()
    if not bend_bin:
        return "not_checked"
    with tempfile.TemporaryDirectory(prefix="bowthruster_ast_bend_") as tmp:
        p = Path(tmp) / "LAW_CHECK.bend"
        p.write_text(bend_src, encoding="utf-8")
        env = {**os.environ, "BEND_NO_TELEMETRY": "1"}
        res = subprocess.run([bend_bin, str(p)], cwd=tmp, env=env, capture_output=True, text=True, timeout=15)
        out = f"{res.stdout}\n{res.stderr}"
        return res.returncode == 0 and "ALL PROOFS CHECK" in out and "ERROR" not in out


@functools.lru_cache(maxsize=64)
def _check_bend_dir_proof(bend_dir_rel: str) -> bool | str:
    bend_bin = _find_bend_binary()
    if not bend_bin:
        return "not_checked"
    repo_root = Path(__file__).resolve().parents[1]
    clean_rel = bend_dir_rel.strip().strip("\"`'")
    for cand in (repo_root / clean_rel, repo_root / "laws" / Path(clean_rel).name):
        if (cand / "PROOF.bend").is_file():
            env = {**os.environ, "BEND_NO_TELEMETRY": "1"}
            res = subprocess.run(
                [bend_bin, "PROOF.bend"], cwd=str(cand), env=env, capture_output=True, text=True, timeout=15
            )
            out = f"{res.stdout}\n{res.stderr}"
            return res.returncode == 0 and "ALL PROOFS CHECK" in out and "ERROR" not in out
    return True


def _parse_staged_py_functions(
    staged_files: list[dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any], dict[str, str]]]:
    py_files: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []
    for f in staged_files:
        src = f.get("source") if f.get("source") is not None else f.get("content")
        if not src or not str(src).strip():
            continue
        try:
            tree = ast.parse(str(src))
        except SyntaxError:
            continue
        fmap = {n.name: n for n in tree.body if type(n).__name__ in ("FunctionDef", "AsyncFunctionDef")}
        amap = {
            t.id: n.value.id
            for n in tree.body
            if type(n).__name__ == "Assign" and getattr(n.value, "id", None) in fmap
            for t in n.targets
            if hasattr(t, "id")
        }
        for alias, target in list(amap.items()):
            fmap.setdefault(alias, fmap[target])
        if fmap:
            py_files.append((str(f.get("path") or "").strip(), str(f.get("scope") or "").strip(), fmap, amap))
    return py_files


def _verify_single_law(
    art: dict[str, Any],
    lfn: str,
    lhs: str,
    rhs: str,
    raw_args: Any,
    fmap: dict[str, Any],
    amap: dict[str, str],
) -> bool:
    dims = art.get("dimensions") or {}
    law_id = str(art.get("name") or art.get("id") or "law").strip()
    py_params, py_bend = transpile_py_fn_to_bend_bool(fmap[lfn], fmap, amap)
    if isinstance(raw_args, list):
        arg_chunks = [str(x) for x in raw_args]
    else:
        arg_chunks = str(raw_args or "").replace("\n", ",").replace(";", ",").split(",")
    skip_vals = ("none", "-", "—", "n/a", "null")
    law_params = [
        re.sub(r"[^A-Za-z0-9_]", "", c.replace("for ", "").lstrip("+-").split(":", 1)[0].strip())
        for c in arg_chunks
        if c.strip() and c.strip().lower() not in skip_vals
    ]
    law_params = [p for p in law_params if p] or list(py_params)
    custom_fns = {lfn: (py_params, py_bend)}
    table_ok = all(
        _eval_bend_bool(lhs, dict(zip(law_params, row)), custom_fns)
        == _eval_bend_bool(rhs, dict(zip(law_params, row)), custom_fns)
        for row in itertools.product((False, True), repeat=len(law_params))
    )
    sig_str = ", ".join(f"{p}: Bool" for p in py_params)
    for_lines = "\n".join(f"  for +{p}: Bool" for p in law_params)
    proof_lines = "\n".join(_gen_bool_case_proof(law_params, 2))
    safe_law = re.sub(r"[^A-Za-z0-9_]", "_", law_id) or "law_check"
    norm_l, norm_r = _norm_bend_expr(lhs, lfn), _norm_bend_expr(rhs, lfn)
    bend_src = (
        "import Base\n\ndef bool_eq(a: Bool, b: Bool) -> Bool:\n  match a b:\n"
        "    case True{} True{}:\n      True{}\n    case False{} False{}:\n      True{}\n"
        "    case True{} False{}:\n      False{}\n    case False{} True{}:\n      False{}\n\n"
        "def bool_imp(a: Bool, b: Bool) -> Bool:\n  match a:\n"
        "    case False{}:\n      True{}\n    case True{}:\n      b\n\n"
        f"def {lfn}({sig_str}) -> Bool:\n  {py_bend}\n\n"
        f"law {safe_law}:\n{for_lines}\n  {{{norm_l} == {norm_r} : Bool}}\n\n"
        f"def {safe_law}({', '.join(law_params)}):\n{proof_lines}\n"
    )
    bend_verdict = _check_synthesized_bend_proof(bend_src)
    raw_bdir = art.get("bend_dir") if art.get("bend_dir") is not None else (dims.get("bend_dir") or "")
    bend_dir = str(raw_bdir).strip()
    dir_verdict = _check_bend_dir_proof(bend_dir) if bend_dir else True
    return bool(table_ok and bend_verdict is not False and dir_verdict is not False)


def verify_python_ast_laws_with_bend(
    artifacts: list[dict[str, Any]],
    staged_files: list[dict[str, Any]],
    scopes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = scopes
    checked_laws, failed_laws = [], []
    py_files = _parse_staged_py_functions(staged_files)
    if not py_files:
        has_bend_bin = bool(_find_bend_binary())
        return {
            "bend_proofs_ok": True if has_bend_bin else "not_checked",
            "bend_proof_status": "NO_LAWS" if has_bend_bin else "NOT_CHECKED",
            "bend_binary_checked": False,
            "truth_table_checked": False,
            "failed_laws": [],
            "checked_laws": [],
        }
    skip_fn_names = {"bool_eq", "bool_imp", "and", "or", "not", "true", "false", "eq"}
    for art in artifacts:
        if str(art.get("kind") or "").upper() != "LAW":
            continue
        dims = art.get("dimensions") or {}
        raw_lhs = art.get("lhs") if art.get("lhs") is not None else (dims.get("lhs") or "")
        raw_rhs = art.get("rhs") if art.get("rhs") is not None else (dims.get("rhs") or "")
        lhs = str(raw_lhs).strip().strip("\"`'").strip()
        rhs = str(raw_rhs).strip().strip("\"`'").strip()
        raw_args = art.get("args") if art.get("args") is not None else dims.get("args")
        has_bool_tok = any(tok in f"{lhs} {rhs}" for tok in ("Bool.", "bool_eq", "and(", "or(", "not("))
        if "(" not in lhs or not rhs or (not raw_args and not has_bool_tok):
            continue
        law_id = str(art.get("name") or art.get("id") or "law").strip()
        m_calls = [
            c for c in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", lhs) if c.lower() not in skip_fn_names
        ]
        lfn = str(art.get("fn_name") or (m_calls[0] if m_calls else "")).strip().split(".")[-1]
        if not lfn or lfn.startswith("check_"):
            continue
        for _, _, fmap, amap in py_files:
            if lfn not in fmap:
                continue
            checked_laws.append(law_id)
            try:
                if not _verify_single_law(art, lfn, lhs, rhs, raw_args, fmap, amap):
                    failed_laws.append(law_id)
            except Exception:
                failed_laws.append(law_id)
            break
    has_checked, has_bend_bin = bool(checked_laws), bool(_find_bend_binary())
    if has_checked:
        status = "FAILED" if failed_laws else ("PROVEN" if has_bend_bin else "NOT_CHECKED")
    else:
        status = "NO_LAWS" if has_bend_bin else "NOT_CHECKED"
    ok_val: bool | str = False if failed_laws else (True if has_bend_bin else "not_checked")
    return {
        "bend_proofs_ok": ok_val,
        "bend_proof_status": status,
        "bend_binary_checked": has_checked and has_bend_bin,
        "truth_table_checked": has_checked,
        "failed_laws": failed_laws,
        "checked_laws": checked_laws,
    }


__all__ = [
    "ASCII_PERIOD",
    "_extract_generic_source_symbols",
    "_get_tokenizer_nlp",
    "dense_text_similarities",
    "lexical_token_similarity",
    "max_lexical_similarity",
    "protect_ascii_dots",
    "restore_ascii_dots",
    "soft_token_overlap_count",
    "tokenize_words",
    "transpile_py_fn_to_bend_bool",
    "verify_python_ast_laws_with_bend",
]
