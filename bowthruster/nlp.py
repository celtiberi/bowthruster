"""Pure-stdlib orthographic, tokenization, and lexical-similarity helpers (`lattice.nlp`).

Zero `spacy`, `torch`, `onnxruntime`, or LLM dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import ast
import functools
import itertools
import math
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
    """Replaces ASCII periods inside tokens with `ASCII_PERIOD` (`\\uE000`)."""
    if not isinstance(text, str) or "." not in text:
        return str(text or "")
    return re.sub(r"(?<=\w)\.(?=\w)", ASCII_PERIOD, text)


def restore_ascii_dots(text: str) -> str:
    """Restores `ASCII_PERIOD` (`\\uE000`) placeholders back to ASCII `.`."""
    if not isinstance(text, str):
        return str(text or "")
    return text.replace(ASCII_PERIOD, ".")


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
    """Splits `text` into lowercase alphanumeric/identifier words using pure stdlib."""
    if not isinstance(text, str) or not text.strip():
        return []
    clean = restore_ascii_dots(text).strip()
    out: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9_./-]+", clean.lower()):
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
    """Lightweight pure-stdlib callable emulating basic token iteration for `tokenize_content_words`."""

    def __call__(self, text: str) -> list[_PureToken]:
        if not isinstance(text, str) or not text:
            return []
        tokens: list[_PureToken] = []
        for m in _WORD_RE.finditer(text):
            s = m.group(0)
            low = s.lower().strip(".,;:!?()[]{}\"'`")
            is_punct = not any(c.isalnum() for c in s)
            tokens.append(_PureToken(
                text=s,
                lemma_=_simple_lemma(low) if low else s.lower(),
                is_space=s.isspace(),
                is_punct=is_punct,
                is_stop=low in _STOPWORDS,
            ))
        return tokens


_PURE_NLP = _PureNLP()


def _get_spacy_nlp() -> Any:
    """Returns the pure-stdlib tokenizer wrapper (zero spacy/torch dependency)."""
    return _PURE_NLP


def lexical_token_similarity(w1: str, w2: str) -> float:
    """Pure-stdlib normalized lexical similarity in `[0.0, 1.0]` between two tokens."""
    a, b = restore_ascii_dots(str(w1 or "")).strip().lower(), restore_ascii_dots(str(w2 or "")).strip().lower()
    if not a or not b:
        return 0.0
    if a == b or _simple_lemma(a) == _simple_lemma(b):
        return 1.0
    return float(SequenceMatcher(None, a, b).ratio())


def max_lexical_similarity(query_tokens: Any, target_tokens: Any) -> float:
    """Returns the maximum `lexical_token_similarity(qt, tt)` across `query_tokens x target_tokens`."""
    q_list = [query_tokens] if isinstance(query_tokens, str) else [str(qt).strip().lower() for qt in (query_tokens or ()) if str(qt).strip()]
    t_list = [target_tokens] if isinstance(target_tokens, str) else [str(tt).strip().lower() for tt in (target_tokens or ()) if str(tt).strip()]
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
    query_tokens: Any,
    target_tokens: Any,
    threshold: float = 0.70,
) -> tuple[set[str], int]:
    """Returns `(exact_overlap, soft_hits)` where `exact_overlap = q_set & t_set` and `soft_hits` counts `q_set - t_set` tokens >= `threshold`."""
    q_set = {str(t).strip().lower() for t in (query_tokens or ()) if str(t).strip()}
    t_set = {str(t).strip().lower() for t in (target_tokens or ()) if str(t).strip()}
    exact_overlap = q_set & t_set
    soft_hits = sum(
        1
        for qt in (q_set - t_set)
        if max((lexical_token_similarity(qt, rt) for rt in t_set), default=0.0) >= threshold
    )
    return exact_overlap, soft_hits


def _char_ngrams(text: str, n: int = 3) -> dict[str, float]:
    s = f" {restore_ascii_dots(str(text or '')).lower().strip()} "
    if len(s) < n:
        return {s: 1.0} if s.strip() else {}
    counts: dict[str, float] = {}
    for i in range(len(s) - n + 1):
        g = s[i : i + n]
        counts[g] = counts.get(g, 0.0) + 1.0
    return counts


def dense_text_similarities(query_text: str, candidate_texts: list[str]) -> list[float]:
    """Pure-stdlib character-trigram + word cosine similarity in `[0.0, 1.0]` (zero ONNX dependency)."""
    if not candidate_texts:
        return []
    q_vec = _char_ngrams(query_text, 3)
    for w in tokenize_words(query_text):
        q_vec[f"w:{_simple_lemma(w)}"] = q_vec.get(f"w:{_simple_lemma(w)}", 0.0) + 2.0
    q_norm = math.sqrt(sum(v * v for v in q_vec.values()))
    if q_norm <= 0.0:
        return [0.0 for _ in candidate_texts]
    scores: list[float] = []
    for cand in candidate_texts:
        c_vec = _char_ngrams(cand, 3)
        for w in tokenize_words(cand):
            c_vec[f"w:{_simple_lemma(w)}"] = c_vec.get(f"w:{_simple_lemma(w)}", 0.0) + 2.0
        c_norm = math.sqrt(sum(v * v for v in c_vec.values()))
        if c_norm <= 0.0:
            scores.append(0.0)
        else:
            dot = sum(qv * c_vec.get(k, 0.0) for k, qv in q_vec.items())
            scores.append(max(0.0, min(1.0, dot / (q_norm * c_norm))))
    return scores


_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+)?"
    r"(?:(?:public|private|protected|internal|static|async|inline|virtual|override|abstract|final|const)\s+)*"
    r"(?:fn|func|function|def|class|interface|type|struct|enum)\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)"
)
_ARROW_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>"
)
_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _extract_generic_source_symbols(fp: Any) -> tuple[dict[str, tuple[int, int]], dict[str, list[str]]]:
    """Extracts `(symbol -> (start_line, end_line), fn -> [_helper#Lstart-Lend])` for non-Python source files (`.ts`, `.js`, `.go`, `.rs`, `.java`, `.c`, `.cpp`, `.bend`, etc.)."""
    try:
        lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return {}, {}
    syms: dict[str, tuple[int, int]] = {}
    n_lines = len(lines)
    for i, line in enumerate(lines):
        m = _DECL_RE.match(line) or _ARROW_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        s = i + 1
        e = s
        depth = line.count("{") - line.count("}")
        if "{" in line or (i + 1 < n_lines and "{" in lines[i + 1] and ";" not in line):
            j = i
            depth = 0
            started = False
            while j < n_lines:
                dl = lines[j].split("//", 1)[0]
                if "{" in dl:
                    started = True
                depth += dl.count("{") - dl.count("}")
                e = j + 1
                if started and depth <= 0:
                    break
                j += 1
        elif line.rstrip().endswith((":", "=")):
            base_indent = len(line) - len(line.lstrip())
            j = i + 1
            while j < n_lines:
                nxt = lines[j]
                if nxt.strip() and not nxt.lstrip().startswith(("#", "//")) and (len(nxt) - len(nxt.lstrip())) <= base_indent:
                    break
                if nxt.strip():
                    e = j + 1
                j += 1
        syms.setdefault(name, (s, max(s, e)))
    calls: dict[str, list[str]] = {}
    for fn_name, (s, e) in syms.items():
        body = "\n".join(lines[max(0, s - 1) : e])
        c_list = sorted({
            f"{c}#L{syms[c][0]}-L{syms[c][1]}"
            for c in _CALL_RE.findall(body)
            if c in syms and c != fn_name and c.startswith("_")
        })
        if c_list:
            calls[fn_name] = c_list
    return syms, calls


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
    pat = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")\b")
    return pat.sub(lambda m: mapping[m.group(1)], expr)


def transpile_py_fn_to_bend_bool(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    fn_map: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] | None = None,
    alias_map: dict[str, str] | None = None,
    depth: int = 0,
) -> tuple[list[str], str]:
    """Transpiles a pure Python boolean decision function AST (`def <fn>`) into a Bend 2 boolean expression `(params, bend_expr)`."""
    if depth > 6:
        raise ValueError("Recursion limit exceeded in Python-to-Bend AST transpiler")
    fmap, amap = fn_map or {}, alias_map or {}
    params = [a.arg for a in (*fn_node.args.posonlyargs, *fn_node.args.args, *fn_node.args.kwonlyargs) if a.arg != "self"]

    def _expr(node: ast.AST, env: dict[str, str]) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return "True{}" if node.value else "False{}"
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            if node.id in params:
                return node.id
            raise ValueError(f"Unbound variable {node.id!r} in {fn_node.name}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return f"Bool.not({_expr(node.operand, env)})"
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            bop = "Bool.and" if isinstance(node.op, ast.And) else "Bool.or"
            vals = [_expr(v, env) for v in node.values]
            acc = vals[-1]
            for v in reversed(vals[:-1]):
                acc = f"{bop}({v}, {acc})"
            return acc
        if isinstance(node, ast.IfExp):
            c, t, f = _expr(node.test, env), _expr(node.body, env), _expr(node.orelse, env)
            return f"Bool.or(Bool.and({c}, {t}), Bool.and(Bool.not({c}), {f}))"
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
            l_ex, r_ex = _expr(node.left, env), _expr(node.comparators[0], env)
            if isinstance(node.ops[0], (ast.Eq, ast.Is)):
                return f"bool_eq({l_ex}, {r_ex})"
            if isinstance(node.ops[0], (ast.NotEq, ast.IsNot)):
                return f"Bool.not(bool_eq({l_ex}, {r_ex}))"
        if isinstance(node, ast.Call):
            cname = node.func.id if isinstance(node.func, ast.Name) else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
            if cname == "bool" and len(node.args) == 1 and not node.keywords:
                return _expr(node.args[0], env)
            target = amap.get(cname, cname)
            if target in fmap and target != fn_node.name:
                sub_p, sub_b = transpile_py_fn_to_bend_bool(fmap[target], fmap, amap, depth + 1)
                pos_args = [_expr(a, env) for a in node.args]
                kw_args = {kw.arg: _expr(kw.value, env) for kw in node.keywords if kw.arg}
                sub_map = {p: (pos_args[i] if i < len(pos_args) else kw_args.get(p, "True{}")) for i, p in enumerate(sub_p)}
                return _sub_ident(sub_b, sub_map)
        raise ValueError(f"Unsupported AST node {type(node).__name__} in {fn_node.name}")

    def _stmts(stmts: list[ast.stmt], env: dict[str, str]) -> str:
        cur = dict(env)
        for i, st in enumerate(stmts):
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
                continue
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                cur[st.targets[0].id] = _expr(st.value, cur)
            elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.value is not None:
                cur[st.target.id] = _expr(st.value, cur)
            elif isinstance(st, ast.Return) and st.value is not None:
                return _expr(st.value, cur)
            elif isinstance(st, ast.If):
                c = _expr(st.test, cur)
                t_b = _stmts(st.body, dict(cur))
                f_b = _stmts(st.orelse if st.orelse else stmts[i + 1 :], dict(cur))
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
    return [
        f"{pad}match {v}:",
        f"{pad}  case False{{}}:",
        *_gen_bool_case_proof(vars_left[1:], indent + 4),
        f"{pad}  case True{{}}:",
        *_gen_bool_case_proof(vars_left[1:], indent + 4),
    ]


def _norm_bend_expr(expr: str, fn_name: str) -> str:
    s = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\.(?=" + re.escape(fn_name) + r"\s*\(|bool_eq\s*\(|bool_imp\s*\()", "", expr.strip())
    s = re.sub(r"(?<![A-Za-z0-9_.])(and|or|not)\s*\(", r"Bool.\1(", s)
    return s


def _find_bend_binary() -> str | None:
    return shutil.which("bend") or (str(Path.home() / ".cargo" / "bin" / "bend") if (Path.home() / ".cargo" / "bin" / "bend").is_file() else None)


@functools.lru_cache(maxsize=256)
def _check_synthesized_bend_proof(bend_src: str) -> bool:
    bend_bin = _find_bend_binary()
    if not bend_bin:
        return True
    with tempfile.TemporaryDirectory(prefix="lattice_ast_bend_") as tmp:
        p = Path(tmp) / "LAW_CHECK.bend"
        p.write_text(bend_src, encoding="utf-8")
        res = subprocess.run([bend_bin, str(p)], cwd=tmp, capture_output=True, text=True, timeout=15)
        out = f"{res.stdout}\n{res.stderr}"
        return res.returncode == 0 and "ERROR" not in out


def verify_python_ast_laws_with_bend(
    artifacts: list[dict[str, Any]],
    staged_files: list[dict[str, Any]],
    scopes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transpiles staged Python `% LAW` functions to Bend 2 and verifies `LHS == RHS` across all truth-table assignments and via `bend`."""
    checked_laws, failed_laws = [], []
    py_files: list[tuple[str, str, dict[str, ast.FunctionDef | ast.AsyncFunctionDef], dict[str, str]]] = []
    for f in staged_files:
        src = f.get("source") if f.get("source") is not None else (f.get("content") if f.get("content") is not None else f.get("code"))
        if not isinstance(src, str) or not src.strip():
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        fmap = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        amap = {t.id: n.value.id for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in fmap for t in n.targets if isinstance(t, ast.Name)}
        for alias, target in list(amap.items()):
            if alias not in fmap and target in fmap:
                fmap[alias] = fmap[target]
        if fmap:
            py_files.append((str(f.get("path") or f.get("file") or "").strip(), str(f.get("scope") or f.get("component") or "").strip(), fmap, amap))

    if not py_files:
        return {"bend_proofs_ok": True, "bend_binary_checked": False, "truth_table_checked": False, "failed_laws": [], "checked_laws": []}

    for art in artifacts:
        if str(art.get("kind") or "").upper() != "LAW":
            continue
        dims = art.get("dimensions") if isinstance(art.get("dimensions"), dict) else {}
        lhs = str(art.get("lhs") if art.get("lhs") is not None else (dims.get("lhs") or "")).strip().strip("\"`'").strip()
        rhs = str(art.get("rhs") if art.get("rhs") is not None else (dims.get("rhs") or "")).strip().strip("\"`'").strip()
        raw_args = art.get("args") if art.get("args") is not None else dims.get("args")
        if "(" not in lhs or not rhs or (not raw_args and not any(tok in f"{lhs} {rhs}" for tok in ("Bool.", "bool_eq", "and(", "or(", "not("))):
            continue
        law_id = str(art.get("name") or art.get("law") or art.get("id") or "law").strip()
        m_calls = [c for c in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", lhs) if c.lower() not in {"bool_eq", "bool_imp", "and", "or", "not", "true", "false", "eq"}]
        lfn = str(art.get("fn_name") or art.get("function") or (m_calls[0] if m_calls else "")).strip().split(".")[-1]
        if not lfn or lfn.startswith("check_"):
            continue
        for _, _, fmap, amap in py_files:
            if lfn not in fmap:
                continue
            checked_laws.append(law_id)
            try:
                py_params, py_bend = transpile_py_fn_to_bend_bool(fmap[lfn], fmap, amap)
                arg_chunks = [str(x) for x in raw_args] if isinstance(raw_args, list) else (str(raw_args or "").replace("\n", ",").replace(";", ",").split(","))
                law_params = [re.sub(r"[^A-Za-z0-9_]", "", c.replace("for ", "").lstrip("+-").split(":", 1)[0].strip()) for c in arg_chunks if c.strip() and c.strip().lower() not in ("none", "-", "—", "n/a", "null")]
                law_params = [p for p in law_params if p] or list(py_params)
                custom_fns = {lfn: (py_params, py_bend)}
                table_ok = all(_eval_bend_bool(lhs, dict(zip(law_params, row)), custom_fns) == _eval_bend_bool(rhs, dict(zip(law_params, row)), custom_fns) for row in itertools.product((False, True), repeat=len(law_params)))
                sig_str, for_lines, proof_lines = ", ".join(f"{p}: Bool" for p in py_params), "\n".join(f"  for +{p}: Bool" for p in law_params), "\n".join(_gen_bool_case_proof(law_params, 2))
                safe_law = re.sub(r"[^A-Za-z0-9_]", "_", law_id) or "law_check"
                bend_src = (
                    "import Base\n\ndef bool_eq(a: Bool, b: Bool) -> Bool:\n  match a b:\n"
                    "    case True{} True{}:\n      True{}\n    case False{} False{}:\n      True{}\n"
                    f"    case _ _:\n      False{{}}\n\ndef {lfn}({sig_str}) -> Bool:\n  {py_bend}\n\n"
                    f"law {safe_law}:\n{for_lines}\n  {{{_norm_bend_expr(lhs, lfn)} == {_norm_bend_expr(rhs, lfn)} : Bool}}\n{proof_lines}\n"
                )
                if not table_ok or not _check_synthesized_bend_proof(bend_src):
                    failed_laws.append(law_id)
            except Exception:
                failed_laws.append(law_id)
            break

    has_checked = bool(checked_laws)
    return {"bend_proofs_ok": len(failed_laws) == 0, "bend_binary_checked": has_checked and bool(_find_bend_binary()), "truth_table_checked": has_checked, "failed_laws": failed_laws, "checked_laws": checked_laws}


__all__ = [
    "ASCII_PERIOD", "_extract_generic_source_symbols", "_get_spacy_nlp", "dense_text_similarities",
    "lexical_token_similarity", "max_lexical_similarity", "protect_ascii_dots", "restore_ascii_dots",
    "soft_token_overlap_count", "tokenize_words", "transpile_py_fn_to_bend_bool", "verify_python_ast_laws_with_bend",
]
