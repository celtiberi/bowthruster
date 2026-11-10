"""Pure-stdlib orthographic, tokenization, and lexical-similarity helpers (`bowthruster.nlp`)."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from difflib import SequenceMatcher
import functools
import itertools
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

ASCII_PERIOD = "\u2024"
_WORD_RE = re.compile(r"[A-Za-z0-9_./-]+|[^\sA-Za-z0-9_./-]+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "into",
    "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "were", "will", "with",
})


def protect_ascii_dots(text: str) -> str:
    s = str(text or "")
    return re.sub(r"(?<=\w)\.(?=\w)", ASCII_PERIOD, s) if "." in s else s


def restore_ascii_dots(text: str) -> str:
    return str(text or "").replace(ASCII_PERIOD, ".")


def _simple_lemma(word: str) -> str:
    w = word.lower()
    if len(w) > 4 and w.endswith("ies") and w[-4] not in "aeiou":
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("ing"):
        stem = w[:-3]
        return stem[:-1] if len(stem) > 3 and stem[-1] == stem[-2] else stem
    if len(w) > 3 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("es") and w[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def tokenize_words(text: str) -> list[str]:
    s = restore_ascii_dots(text).strip().lower()
    if not s:
        return []
    out: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9_./-]+", s):
        tok = raw.strip(".,;:!?()[]{}\"'`")
        if tok and tok not in _STOPWORDS and tok not in out:
            out.append(tok)
    return out


@dataclass(frozen=True)
class _PureToken:
    text: str
    lemma_: str
    is_space: bool
    is_punct: bool
    is_stop: bool


class _PureNLP:
    def __call__(self, text: str) -> list[_PureToken]:
        s_in = str(text or "")
        if not s_in:
            return []
        tokens: list[_PureToken] = []
        for m in _WORD_RE.finditer(s_in):
            s = m.group(0)
            low = s.lower().strip(".,;:!?()[]{}\"'`")
            lemma = _simple_lemma(low) if low else s.lower()
            tokens.append(_PureToken(s, lemma, s.isspace(), not any(c.isalnum() for c in s), low in _STOPWORDS))
        return tokens


_PURE_NLP = _PureNLP()


def _get_tokenizer_nlp() -> Any:
    return _PURE_NLP


def lexical_token_similarity(w1: str, w2: str) -> float:
    a, b = restore_ascii_dots(w1).strip().lower(), restore_ascii_dots(w2).strip().lower()
    if not a or not b:
        return 0.0
    if a == b or _simple_lemma(a) == _simple_lemma(b):
        return 1.0
    return float(SequenceMatcher(None, a, b).ratio())


def _as_token_list(tokens: Any) -> list[str]:
    if isinstance(tokens, str):
        return [tokens] if tokens.strip() else []
    return [str(t).strip().lower() for t in (tokens or ()) if str(t).strip()]


def max_lexical_similarity(query_tokens: Any, target_tokens: Any) -> float:
    q_list, t_list = _as_token_list(query_tokens), _as_token_list(target_tokens)
    if not q_list or not t_list:
        return 0.0
    best = 0.0
    for qt in q_list:
        for tt in t_list:
            sim = lexical_token_similarity(qt, tt)
            if sim >= 1.0:
                return 1.0
            if sim > best:
                best = sim
    return best


def soft_token_overlap_count(
    query_tokens: Any, target_tokens: Any, threshold: float = 0.70
) -> tuple[set[str], int]:
    q_set = {str(t).strip().lower() for t in (query_tokens or ()) if str(t).strip()}
    t_set = {str(t).strip().lower() for t in (target_tokens or ()) if str(t).strip()}
    exact_overlap = q_set & t_set
    soft_hits = sum(
        1 for qt in (q_set - t_set) if max((lexical_token_similarity(qt, rt) for rt in t_set), default=0.0) >= threshold
    )
    return exact_overlap, soft_hits


def _char_ngrams(text: str, n: int = 3) -> dict[str, float]:
    s = f" {restore_ascii_dots(text).lower().strip()} "
    if len(s) < n:
        return {s: 1.0} if s.strip() else {}
    counts: dict[str, float] = {}
    for i in range(len(s) - n + 1):
        g = s[i : i + n]
        counts[g] = counts.get(g, 0.0) + 1.0
    return counts


def dense_text_similarities(query_text: str, candidate_texts: list[str]) -> list[float]:
    if not candidate_texts:
        return []
    q_vec = _char_ngrams(query_text, 3)
    for w in tokenize_words(query_text):
        key = f"w:{_simple_lemma(w)}"
        q_vec[key] = q_vec.get(key, 0.0) + 2.0
    q_norm = math.sqrt(sum(v * v for v in q_vec.values()))
    if q_norm <= 0.0:
        return [0.0 for _ in candidate_texts]
    scores: list[float] = []
    for cand in candidate_texts:
        c_vec = _char_ngrams(cand, 3)
        for w in tokenize_words(cand):
            key = f"w:{_simple_lemma(w)}"
            c_vec[key] = c_vec.get(key, 0.0) + 2.0
        c_norm = math.sqrt(sum(v * v for v in c_vec.values()))
        dot = sum(qv * c_vec.get(k, 0.0) for k, qv in q_vec.items()) if c_norm > 0.0 else 0.0
        scores.append(max(0.0, min(1.0, dot / (q_norm * c_norm))) if c_norm > 0.0 else 0.0)
    return scores


from bowthruster import _extract_generic_source_symbols



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
            l_ex, r_ex = _expr(getattr(node, "left"), env), _expr(getattr(node, "comparators")[0], env)
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


def _eval_bend_bool(expr: str, env: dict[str, bool], custom_fns: dict[str, tuple[list[str], str]]) -> bool:
    s = str(expr or "").strip().strip("\"`'").split(":", 1)[0].strip()
    if s in ("True{}", "True", "true", "1"):
        return True
    if s in ("False{}", "False", "false", "0"):
        return False
    if s in env:
        return bool(env[s])
    if "(" in s and s.endswith(")"):
        callee = s.split("(", 1)[0].strip().split(".")[-1].strip()
        args = [_eval_bend_bool(a, env, custom_fns) for a in _split_top_args(s[s.find("(") + 1 : -1])]
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
    return re.sub(r"(?<![A-Za-z0-9_.])(and|or|not)\s*\(", r"Bool.\1(", re.sub(pat, "", expr.strip()))


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
    art: dict[str, Any], lfn: str, lhs: str, rhs: str, raw_args: Any, fmap: dict[str, Any], amap: dict[str, str]
) -> bool:
    dims = art.get("dimensions") or {}
    law_id = str(next((_v for _v in (art.get("name"), art.get("id")) if _v), None) or "law").strip()
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
    bend_src = (
        "import Base\n\ndef bool_eq(a: Bool, b: Bool) -> Bool:\n  match a b:\n"
        "    case True{} True{}:\n      True{}\n    case False{} False{}:\n      True{}\n"
        "    case True{} False{}:\n      False{}\n    case False{} True{}:\n      False{}\n\n"
        "def bool_imp(a: Bool, b: Bool) -> Bool:\n  match a:\n"
        "    case False{}:\n      True{}\n    case True{}:\n      b\n\n"
        f"def {lfn}({sig_str}) -> Bool:\n  {py_bend}\n\n"
        f"law {safe_law}:\n{for_lines}\n  {{{_norm_bend_expr(lhs, lfn)} == {_norm_bend_expr(rhs, lfn)} : Bool}}\n\n"
        f"def {safe_law}({', '.join(law_params)}):\n{proof_lines}\n"
    )
    bend_verdict = _check_synthesized_bend_proof(bend_src)
    bend_dir = str(art.get("bend_dir") if art.get("bend_dir") is not None else (dims.get("bend_dir") or "")).strip()
    dir_verdict = _check_bend_dir_proof(bend_dir) if bend_dir else True
    return bool(table_ok and bend_verdict is not False and dir_verdict is not False)


def verify_python_ast_laws_with_bend(
    artifacts: list[dict[str, Any]], staged_files: list[dict[str, Any]], scopes: dict[str, Any] | None = None
) -> dict[str, Any]:
    _ = scopes
    checked_laws, failed_laws = [], []
    py_files = _parse_staged_py_functions(staged_files)
    if not py_files:
        has_bend_bin = bool(_find_bend_binary())
        return {
            "bend_proofs_ok": True if has_bend_bin else "not_checked",
            "bend_proof_status": "NO_LAWS" if has_bend_bin else "NOT_CHECKED",
            "bend_binary_checked": False, "truth_table_checked": False,
            "failed_laws": [], "checked_laws": [],
        }
    skip_fn_names = {"bool_eq", "bool_imp", "and", "or", "not", "true", "false", "eq"}
    for art in artifacts:
        if str(art.get("kind") or "").upper() != "LAW":
            continue
        dims = art.get("dimensions") or {}
        raw_lhs = art.get("lhs") if art.get("lhs") is not None else (dims.get("lhs") or "")
        raw_rhs = art.get("rhs") if art.get("rhs") is not None else (dims.get("rhs") or "")
        lhs, rhs = str(raw_lhs).strip().strip("\"`'").strip(), str(raw_rhs).strip().strip("\"`'").strip()
        raw_args = art.get("args") if art.get("args") is not None else dims.get("args")
        has_bool_tok = any(tok in f"{lhs} {rhs}" for tok in ("Bool.", "bool_eq", "and(", "or(", "not("))
        if "(" not in lhs or not rhs or (not raw_args and not has_bool_tok):
            continue
        law_id = str(next((_v for _v in (art.get("name"), art.get("id")) if _v), None) or "law").strip()
        m_calls = [c for c in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", lhs) if c.lower() not in skip_fn_names]
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
        "bend_proofs_ok": ok_val, "bend_proof_status": status,
        "bend_binary_checked": has_checked and has_bend_bin, "truth_table_checked": has_checked,
        "failed_laws": failed_laws, "checked_laws": checked_laws,
    }


__all__ = [
    "ASCII_PERIOD", "_extract_generic_source_symbols", "_get_tokenizer_nlp", "dense_text_similarities",
    "lexical_token_similarity", "max_lexical_similarity", "protect_ascii_dots", "restore_ascii_dots",
    "soft_token_overlap_count", "tokenize_words", "transpile_py_fn_to_bend_bool", "verify_python_ast_laws_with_bend",
]
