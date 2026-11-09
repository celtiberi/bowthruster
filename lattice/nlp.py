"""Pure-stdlib orthographic, tokenization, and lexical-similarity helpers (`lattice.nlp`).

Zero `spacy`, `torch`, `onnxruntime`, or LLM dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import math
import re
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


__all__ = [
    "ASCII_PERIOD",
    "_extract_generic_source_symbols",
    "_get_spacy_nlp",
    "dense_text_similarities",
    "lexical_token_similarity",
    "max_lexical_similarity",
    "protect_ascii_dots",
    "restore_ascii_dots",
    "soft_token_overlap_count",
    "tokenize_words",
]
