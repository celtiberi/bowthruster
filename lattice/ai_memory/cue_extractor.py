"""Spec 24A — `ai_memory.cue_extractor`: Deterministic Zero-Regex Concept Seed Cue Extraction & Fan-Out Bound (`core/circuit/ai_memory/cue_extractor.py`)."""

from __future__ import annotations

from typing import Iterable

from .schema import MAX_SEED_CUES


def should_accept_seed_cue(is_non_empty_token: bool, within_12_cap: bool, is_duplicate_seed: bool) -> bool:
    """Bend 2 Law 2 (`cue_seed_determinism_law`):
    Returns `True` iff `is_non_empty_token is True` and `within_12_cap is True` and `is_duplicate_seed is False`."""
    if not bool(is_non_empty_token):
        return False
    return bool(within_12_cap) and (not bool(is_duplicate_seed))


def _normalize_cue_token(raw_token: str) -> str:
    """Normalizes a raw whitespace token by stripping leading/trailing non-alphanumeric characters and lowercasing (zero `import re`)."""
    if not isinstance(raw_token, str):
        return ""
    s = raw_token.strip().lower()
    start, end = 0, len(s)
    while start < end and not (s[start].isalnum() or s[start] == "_"):
        start += 1
    while end > start and not (s[end - 1].isalnum() or s[end - 1] == "_"):
        end -= 1
    return s[start:end]


def extract_concept_cues(
    query_text: str | Iterable[str] | None,
    *,
    max_seed_cues: int = MAX_SEED_CUES,
) -> list[str]:
    """Extracts up to `min(max_seed_cues, MAX_SEED_CUES)` deterministic, deduplicated lowercase concept cues
    from `query_text` via `str.split()` and character-class boundary trimming governed by `should_accept_seed_cue`."""
    if query_text is None:
        return []
    if not isinstance(max_seed_cues, int) or isinstance(max_seed_cues, bool) or max_seed_cues < 0:
        raise ValueError(f"max_seed_cues must be a non-negative int, got {max_seed_cues!r}")

    effective_cap = min(max_seed_cues, MAX_SEED_CUES)
    if isinstance(query_text, str):
        raw_parts = query_text.replace(",", " ").replace(";", " ").split()
    elif isinstance(query_text, (list, tuple, set, frozenset)):
        items = sorted(query_text) if isinstance(query_text, (set, frozenset)) else list(query_text)
        raw_parts = [w for item in items if isinstance(item, str) for w in item.replace(",", " ").replace(";", " ").split()]
    else:
        raise ValueError(f"query_text must be a str or iterable of str, got {type(query_text).__name__}")

    seed_cues: list[str] = []
    seen_cues: set[str] = set()
    for raw_tok in raw_parts:
        tok = _normalize_cue_token(raw_tok)
        is_non_empty = bool(tok)
        within_cap = len(seed_cues) < effective_cap
        is_dup = tok in seen_cues
        if should_accept_seed_cue(is_non_empty, within_cap, is_dup):
            seed_cues.append(tok)
            seen_cues.add(tok)
    return seed_cues
