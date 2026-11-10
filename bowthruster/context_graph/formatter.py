"""Spec 20 §1.1 & §1.4: Content-Word Tokenizer, Metadata Clamping, Token Cost Estimator, Rule Packer, and Compact View Formatter."""

from __future__ import annotations

import copy
from typing import Any

from bowthruster.nlp import _get_spacy_nlp, restore_ascii_dots

DEFAULT_K_CAP = 5
DEFAULT_MAX_RING = 10
MAX_META_SLOTS = 3
MAX_META_SLOT_WORDS = 25
MAX_META_TOTAL_WORDS = 75
MAX_META_KEY_WORDS = 5

_TYPE_TO_FACET: dict[str, str] = {
    "PROHIBITION": "prohibitions",
    "REVOCATION": "prohibitions",
    "OBLIGATION": "obligations",
    "POLICY": "obligations",
    "BOUND": "Numeric_Bounds",
    "ACTION": "actions",
    "COMMIT": "actions",
    "FACT": "context_facts",
    "GOAL": "context_facts",
    "PROPOSAL": "context_facts",
    "QUESTION": "open_questions",
}


def tokenize_content_words(text: str) -> list[str]:
    """Extracts lowercase content/identifier tokens from `text` via `spaCy` and delimiter splitting (zero `import re`)."""
    if not isinstance(text, str) or not text.strip():
        return []
    clean = restore_ascii_dots(text).strip()
    doc = _get_spacy_nlp()(clean)
    out: list[str] = []
    for t in doc:
        if t.is_space or t.is_punct:
            continue
        raw_low = t.text.lower().strip(".,;:!?()[]{}\"'`")
        if raw_low and not t.is_stop:
            if raw_low not in out:
                out.append(raw_low)
            lemma_low = t.lemma_.lower().strip(".,;:!?()[]{}\"'`")
            if lemma_low and lemma_low != raw_low and not t.is_stop and lemma_low not in out:
                out.append(lemma_low)
        for sep in ("_", "-", "/", "."):
            if sep in raw_low:
                for sub in raw_low.replace("-", "_").replace("/", "_").replace(".", "_").split("_"):
                    if sub and len(sub) > 1 and sub not in out:
                        out.append(sub)
    return out


def clamp_rule_meta(meta: dict[str, Any] | None) -> tuple[dict[str, str], list[str], list[str]]:
    """Enforces Spec 20 §1.1 metadata caps: <= 3 slots, <= 25 words/slot, <= 75 words total, <= 5 words/key.
    Returns `(clamped_meta, meta_keys, meta_tokens)`."""
    if not isinstance(meta, dict) or not meta:
        return {}, [], []
    clamped: dict[str, str] = {}
    total_words = 0
    for raw_k in sorted(meta.keys()):
        if len(clamped) >= MAX_META_SLOTS:
            break
        raw_v = meta[raw_k]
        if raw_v is None:
            continue
        k_words = str(raw_k).strip().split()[:MAX_META_KEY_WORDS]
        k_str = "_".join(k_words) if k_words else "note"
        v_words = restore_ascii_dots(str(raw_v)).strip().split()
        if not v_words:
            continue
        rem = MAX_META_TOTAL_WORDS - total_words
        if rem <= 0:
            break
        v_sliced = v_words[: min(MAX_META_SLOT_WORDS, rem)]
        clamped[k_str] = " ".join(v_sliced)
        total_words += len(v_sliced)
    meta_keys = sorted(clamped.keys())
    meta_tokens = tokenize_content_words(" ".join(f"{k} {v}" for k, v in clamped.items()))
    return clamped, meta_keys, meta_tokens


def token_cost(obj: Any) -> int:
    """Deterministic token-cost estimator over strings, lists, and dicts (Spec 20 §1.1)."""
    if obj is None:
        return 0
    if isinstance(obj, str):
        s = obj.strip()
        if not s:
            return 0
        words = s.split()
        punct_extra = sum(1 for ch in s if ch in "[]{}:,;()<>=") // 3
        return len(words) + punct_extra
    if isinstance(obj, (int, float, bool)):
        return 1
    if isinstance(obj, list):
        return sum(token_cost(x) for x in obj)
    if isinstance(obj, dict):
        return sum(token_cost(k) + token_cost(v) for k, v in obj.items())
    return len(str(obj).split())


def _pack_active_rules_and_lod2(
    scopes: dict[str, Any],
    rule_idx: dict[str, tuple[str, dict[str, Any]]],
    candidate_rids: list[str],
    activations: dict[str, float],
    q_tokens: set[str],
    conflicts: list[dict[str, Any]],
    eff_budget: int,
    max_lines: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Two-pass packer that packs all fitting `candidate_rids` at LOD 1 first, then attaches LOD 2 `_meta`
    within remaining `eff_budget` and `max_lines` (Spec 20 §1.4)."""
    packed_rules: list[dict[str, Any]] = []
    lod2_meta: dict[str, dict[str, str]] = {}
    used_tokens = 10 + token_cost(conflicts)
    used_lines = 1 + len(conflicts)

    for rid in candidate_rids:
        if used_lines >= max_lines:
            break
        sid, r = rule_idx[rid]
        r_copy = copy.deepcopy(r)
        r_copy["activation"] = round(activations.get(rid, 0.0), 6)
        r_cost = token_cost(r_copy.get("text", "")) + 6
        if used_tokens + r_cost > eff_budget and packed_rules:
            break
        packed_rules.append(r_copy)
        used_tokens += r_cost
        used_lines += 1

    for r_copy in packed_rules:
        rid = str(r_copy.get("id", ""))
        if not rid or rid not in rule_idx:
            continue
        sid, r = rule_idx[rid]
        scope_meta = scopes.get(sid, {}).get("_meta", {})
        rmeta = scope_meta.get(rid, {}) if isinstance(scope_meta, dict) else {}
        meta_tok_set: set[str] = set()
        if isinstance(rmeta, dict) and rmeta:
            for mk, mv in rmeta.items():
                if mk != "heading_path":
                    meta_tok_set.update(tokenize_content_words(f"{mk} {mv}"))
        else:
            meta_tok_set = {str(t).lower() for t in r.get("meta_tokens", []) if str(t).strip()}
        meta_tok_set -= set(tokenize_content_words(sid))
        if rmeta and bool(q_tokens & meta_tok_set):
            m_cost = token_cost(rmeta)
            if used_tokens + m_cost <= eff_budget and used_lines + len(rmeta) <= max_lines:
                lod2_meta[rid] = copy.deepcopy(rmeta)
                used_tokens += m_cost
                used_lines += len(rmeta)

    return packed_rules, lod2_meta


def format_compact_view(
    view: dict[str, Any],
    *,
    max_lines: int = 35,
    token_budget: int = 400,
    scope_headers: list[str] | None = None,
) -> str:
    """Formats a scan/propagate/expand view dict into a compact multi-line representation within line and token caps,
    reserving capacity for all LOD 1 primary lines before admitting inline LOD 2 metadata lines (Spec 20 §1.1)."""
    pre_rule_lines: list[str] = []
    status = view.get("status", "OK")
    temp = view.get("temperature")
    tau = view.get("tau")
    hdr = f"[GRAPH_VIEW status={status}"
    if temp is not None:
        hdr += f" T={temp:.2f}"
    if tau is not None:
        hdr += f" tau={tau:.2f}"
    hdr += "]"
    pre_rule_lines.append(hdr)

    for c in view.get("conflicts", []):
        if isinstance(c, dict):
            pre_rule_lines.append(f"! CONFLICT [{c.get('scope', '')}] {c.get('text', '')}")

    for b in view.get("open_blockers", []):
        if isinstance(b, dict) and b.get("type") != "CONFLICT":
            pre_rule_lines.append(f"? BLOCKER ({b.get('type', 'OPEN')}) [{b.get('scope', '')}] {b.get('text', '')}")

    if view.get("node_type") == "SCOPE" and isinstance(view.get("scope"), dict):
        scope_obj = view["scope"]
        nid = view.get("node_id") or scope_obj.get("id", "")
        s_line = f"@ SCOPE [{nid}]"
        attrs = scope_obj.get("attributes")
        if isinstance(attrs, dict):
            attr_parts = [f"{k}={v}" for k, v in sorted(attrs.items()) if k != "updated_turn" and v is not None]
            if attr_parts:
                s_line += f" attrs: {', '.join(attr_parts)}"
        children = scope_obj.get("children")
        if isinstance(children, list) and children:
            s_line += f" children=[{', '.join(str(ch) for ch in children)}]"
        pre_rule_lines.append(s_line)
    elif scope_headers:
        for sh in scope_headers:
            if isinstance(sh, str) and sh:
                pre_rule_lines.append(sh)

    for t in view.get("active_tasks", []):
        if isinstance(t, dict):
            pre_rule_lines.append(f"+ TASK {t.get('id', '')} [{t.get('scope', '')}]: {t.get('text', '')}")

    lod2 = view.get("lod2_meta", {}) if isinstance(view.get("lod2_meta"), dict) else {}
    rule_blocks: list[tuple[str, list[str]]] = []
    for r in view.get("active_rules", []):
        if not isinstance(r, dict):
            continue
        rid = r.get("id", "")
        r_line = f"* [{r.get('scope', '')}] {rid} ({r.get('type', '')}): {r.get('text', '')}"
        if r.get("conditions"):
            r_line += f" | if: {'; '.join(str(x) for x in r['conditions'])}"
        if r.get("meta_keys"):
            r_line += f" [meta: {','.join(str(k) for k in r['meta_keys'])}]"
        meta_lines: list[str] = []
        if rid in lod2 and isinstance(lod2[rid], dict):
            for mk, mv in sorted(lod2[rid].items()):
                meta_lines.append(f"  -> meta.{mk}: {mv}")
        rule_blocks.append((r_line, meta_lines))

    note_lines: list[str] = []
    for n in view.get("ambient_notes", []):
        if isinstance(n, dict) and n.get("text"):
            note_lines.append(f"~ NOTE {n.get('id', '')}: {n.get('text', '')}")

    admitted_pre: list[str] = []
    for ln in pre_rule_lines:
        if len(admitted_pre) >= max_lines:
            break
        cand = "\n".join([*admitted_pre, ln])
        if token_cost(cand) > token_budget:
            if not admitted_pre:
                words = ln.split()
                while words and token_cost(" ".join(words)) > token_budget:
                    words.pop()
                if words:
                    admitted_pre.append(" ".join(words))
            break
        admitted_pre.append(ln)

    admitted_rules: list[tuple[str, list[str]]] = []
    if len(admitted_pre) == len(pre_rule_lines):
        for r_line, meta_lines in rule_blocks:
            if len(admitted_pre) + len(admitted_rules) >= max_lines:
                break
            cand = "\n".join([*admitted_pre, *(rl for rl, _ in admitted_rules), r_line])
            if token_cost(cand) > token_budget:
                break
            admitted_rules.append((r_line, meta_lines))

    accepted_meta: list[list[str]] = [[] for _ in admitted_rules]
    for idx, (_, meta_lines) in enumerate(admitted_rules):
        for m_ln in meta_lines:
            trial_lines = [
                *admitted_pre,
                *(
                    ln
                    for i, (rl, _) in enumerate(admitted_rules)
                    for ln in (
                        [rl, *accepted_meta[i], m_ln] if i == idx else [rl, *accepted_meta[i]]
                    )
                ),
            ]
            if len(trial_lines) <= max_lines and token_cost("\n".join(trial_lines)) <= token_budget:
                accepted_meta[idx].append(m_ln)
            else:
                break

    bounded_lines: list[str] = [
        *admitted_pre,
        *(ln for i, (rl, _) in enumerate(admitted_rules) for ln in [rl, *accepted_meta[i]]),
    ]
    for n_ln in note_lines:
        if len(bounded_lines) >= max_lines:
            break
        cand = "\n".join([*bounded_lines, n_ln])
        if token_cost(cand) > token_budget:
            break
        bounded_lines.append(n_ln)

    return "\n".join(bounded_lines)


def _finalize_view(
    view: dict[str, Any],
    *,
    max_lines: int,
    token_budget: int,
    scope_headers: list[str] | None = None,
) -> dict[str, Any]:
    """Renders `view['text']` within `max_lines` and `token_budget` and sets true `token_count` and `line_count`."""
    text_view = format_compact_view(view, max_lines=max_lines, token_budget=token_budget, scope_headers=scope_headers)
    view["text"] = text_view
    view["token_count"] = token_cost(text_view)
    view["line_count"] = len(text_view.splitlines()) if text_view else 0
    return view
