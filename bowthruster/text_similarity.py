"""Pure-stdlib orthographic, tokenization, and lexical-similarity helpers (`bowthruster.text_similarity`)."""

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
            is_punct = not any(c.isalnum() for c in s)
            tokens.append(_PureToken(s, lemma, s.isspace(), is_punct, low in _STOPWORDS))
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
        1
        for qt in (q_set - t_set)
        if max((lexical_token_similarity(qt, rt) for rt in t_set), default=0.0) >= threshold
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
