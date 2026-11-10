"""Spec 21 §1.2: Content-Word Tokenizer, Metadata Clamping, Token Cost, ACT-R Decay, and View Formatter."""

from __future__ import annotations

import copy
import math
from typing import Any

from bowthruster.nlp import _get_tokenizer_nlp, restore_ascii_dots

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
    """Extracts lowercase content/identifier tokens from `text` via `spaCy` and delimiter splitting."""
    if not isinstance(text, str) or not text.strip():
        return []
    clean = restore_ascii_dots(text).strip()
    doc = _get_tokenizer_nlp()(clean)
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
    """Enforces Spec 21 §1.2 metadata caps: <= 3 slots, <= 25 words/slot, <= 75 words total, <= 5 words/key.
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
    """Deterministic token-cost estimator over strings, lists, and dicts (Spec 21 §1.2)."""
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


def actr_base_level_activation(access_turns: list[int], current_turn: int) -> float:
    """Computes ACT-R power-law base-level activation $B_i(t)$ (Spec 21 §1.2)."""
    if not isinstance(access_turns, list) or not access_turns:
        raise ValueError("access_turns must be a non-empty list of ints")
    if isinstance(current_turn, bool) or not isinstance(current_turn, int) or current_turn < 0:
        raise ValueError(f"current_turn must be a non-negative int, got {current_turn!r}")

    total = 0.0
    for tk in access_turns:
        if isinstance(tk, bool) or not isinstance(tk, int):
            raise ValueError(f"access_turns entry must be int, got {tk!r}")
        dt = max(1, current_turn - tk)
        total += dt ** (-0.5)
    return round(math.log(total), 6)


def evict_from_ring(
    ring: list[dict[str, Any]],
    current_turn: int,
    max_ring: int = DEFAULT_MAX_RING,
) -> list[dict[str, Any]]:
    """Evicts lowest-$B_i(t)$ entries from `ring` until `len <= max_ring` (Spec 21 §1.2)."""
    if not isinstance(ring, list):
        raise ValueError("ring must be a list of ambient note dicts")
    if max_ring < 1:
        raise ValueError(f"max_ring must be >= 1, got {max_ring}")
    items: list[dict[str, Any]] = []
    for idx, n in enumerate(ring):
        if not n:
            raise ValueError(f"ring[{idx}] must be a dict, got {type(n).__name__}")
        items.append(copy.deepcopy(n))
    if len(items) <= max_ring:
        return items

    scored: list[tuple[float, int, int, dict[str, Any]]] = []
    for idx, n in enumerate(items):
        aturns = n.get("access_turns")
        if not isinstance(aturns, list) or not aturns:
            turns = [int(n.get("turn", current_turn))]
        else:
            turns = list(aturns)
        b_score = actr_base_level_activation(turns, current_turn)
        last_t = max(turns)
        scored.append((b_score, last_t, idx, n))

    scored.sort(key=lambda x: (x[0], x[1], str(x[3].get("id", ""))))
    kept = scored[len(items) - max_ring :]
    kept.sort(key=lambda x: x[2])
    return [x[3] for x in kept]


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
    """Two-pass packer governed by Bend 2 Law 8 (`lod1_never_starved_by_lod2`):
    Pass 1 packs all fitting `candidate_rids` at LOD 1 first
    Pass 2 attaches LOD 2 `_meta`
    within remaining `eff_budget` and `max_lines` (Spec 21 §1.2)."""
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
        if rmeta:
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


def matches_nd_slice(is_unconstrained: bool, has_dim_overlap: bool) -> bool:
    """Hyperplane slice admission governed by Bend 2 Law 10 (`nd_coordinate_slice_exact`) (Spec 21 §1.2)."""
    return True if is_unconstrained else bool(has_dim_overlap)


def _format_component_line(comp: dict[str, Any]) -> str:
    cid = next((_v for _v in (comp.get("id"), comp.get("scope_id")) if _v), None) or comp.get("scope") or ""
    tags: list[str] = []
    if comp.get("parent"):
        tags.append(f"parent={comp['parent']}")
    if comp.get("children"):
        tags.append(f"children=[{', '.join(str(x) for x in comp['children'])}]")
    if comp.get("depends_on"):
        tags.append(f"depends_on=[{', '.join(str(x) for x in comp['depends_on'])}]")
    if comp.get("owner"):
        tags.append(f"owner={comp['owner']}")
    if comp.get("stage"):
        tags.append(f"stage={comp['stage']}")
    prefix = f"@ COMPONENT [{cid}]" + (f" ({', '.join(tags)})" if tags else "")
    role_str = str(next((_v for _v in (comp.get("role"), comp.get("text")) if _v), None) or "").strip()
    why_str = str(
        comp.get("why") or ((comp.get("attributes") or {})).get("why")
        or ""
    ).strip()
    if why_str:
        role_str = f"{role_str} [why: {why_str}]" if role_str else f"[why: {why_str}]"
    ins = [str(x) for x in (comp.get("inputs") or []) if str(x).strip()]
    outs = [str(x) for x in (comp.get("outputs") or []) if str(x).strip()]
    io_str = f"in=[{', '.join(ins)}] -> out=[{', '.join(outs)}]" if (ins or outs) else ""
    if role_str and io_str:
        return f"{prefix}: {role_str} | {io_str}"
    if role_str:
        return f"{prefix}: {role_str}"
    if io_str:
        return f"{prefix}: {io_str}"
    return prefix


def _format_flow_line(fl: dict[str, Any]) -> str:
    step_val = fl.get("step")
    step_s = f" [{step_val}]" if step_val is not None and str(step_val).strip() != "" else ""
    src, tgt = fl.get("source", ""), fl.get("target", "")
    payload = next((_v for _v in (fl.get("payload"), fl.get("action")) if _v), None) or ""
    arrow = f"--[{payload}]-->" if payload else "-->"
    line = f"> FLOW{step_s} {src} {arrow} {tgt}"
    if fl.get("condition"):
        line += f" | if: {fl['condition']}"
    txt = str(next((_v for _v in (fl.get("text"), fl.get("summary")) if _v), None) or "").strip()
    if txt:
        line += f": {txt}"
    return line


def _format_artifact_line(art: dict[str, Any]) -> str:
    kind = str(art.get("kind") or "SCHEMA").upper()
    sigil = "#" if kind == "SCHEMA" else ("$" if kind == "INTERFACE" else ("%" if kind == "LAW" else "#"))
    name = next((_v for _v in (art.get("name"), art.get("id")) if _v), None) or ""
    scope_val = next((_v for _v in (art.get("scope_id"), art.get("scope")) if _v), None) or ""
    tags: list[str] = []
    if scope_val:
        tags.append(f"scope={scope_val}")
    if art.get("owner"):
        tags.append(f"owner={art['owner']}")
    if art.get("stage"):
        tags.append(f"stage={art['stage']}")
    ins = [str(x) for x in (art.get("inputs") or []) if str(x).strip()]
    outs = [str(x) for x in (art.get("outputs") or []) if str(x).strip()]
    if kind == "INTERFACE" and (ins or outs):
        tags.append(f"in=[{', '.join(ins)}] -> out=[{', '.join(outs)}]")
    tags_s = f" ({', '.join(tags)})" if tags else ""
    spec_or_text = str(art.get("spec") if art.get("spec") else art.get("text", art.get("summary", ""))).strip()
    return f"{sigil} {kind} [{name}]{tags_s}" + (f": {spec_or_text}" if spec_or_text else "")


def _collect_pre_rule_lines(view: dict[str, Any], scope_headers: list[str] | None) -> list[str]:
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
        if c:
            pre_rule_lines.append(f"! CONFLICT [{c.get('scope', '')}] {c.get('text', '')}")

    for b in view.get("open_blockers", []):
        if b.get("type") != "CONFLICT":
            pre_rule_lines.append(f"? BLOCKER ({b.get('type', 'OPEN')}) [{b.get('scope', '')}] {b.get('text', '')}")

    if view.get("node_type") == "SCOPE" and isinstance(view.get("scope"), dict):
        scope_obj = view["scope"]
        nid = next((_v for _v in (view.get("node_id"), scope_obj.get("id", "")) if _v), None)
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

    for comp in view.get("component_specs", []):
        if isinstance(comp, dict):
            pre_rule_lines.append(_format_component_line(comp))

    for fl in view.get("active_flows", []):
        if fl:
            pre_rule_lines.append(_format_flow_line(fl))

    for art in view.get("active_artifacts", []):
        if art:
            pre_rule_lines.append(_format_artifact_line(art))

    for t in view.get("active_tasks", []):
        if t:
            has_nd_tags = bool(
                next((_v for _v in (t.get("owner"), t.get("stage")) if _v), None) or t.get("depends_on")
                or (t.get("status") and t.get("status") != "ACTIVE")
            )
            if has_nd_tags:
                t_tags: list[str] = []
                if t.get("status"):
                    t_tags.append(f"status={t['status']}")
                if t.get("owner"):
                    t_tags.append(f"owner={t['owner']}")
                if t.get("stage"):
                    t_tags.append(f"stage={t['stage']}")
                if t.get("depends_on"):
                    t_tags.append(f"deps=[{', '.join(str(d) for d in t['depends_on'])}]")
                t_hdr = f"+ TASK {t.get('id', '')} [{t.get('scope', '')}] ({', '.join(t_tags)})"
                pre_rule_lines.append(f"{t_hdr}: {t.get('text', '')}")
            else:
                pre_rule_lines.append(f"+ TASK {t.get('id', '')} [{t.get('scope', '')}]: {t.get('text', '')}")
    return pre_rule_lines


def format_compact_view(
    view: dict[str, Any],
    *,
    max_lines: int = 35,
    token_budget: int = 400,
    scope_headers: list[str] | None = None,
) -> str:
    """Formats a scan/propagate/expand view dict into a compact multi-line representation within line and token caps,
    reserving capacity for all LOD 1 primary lines before admitting inline LOD 2 metadata/link lines (Spec 21 §1.2)."""
    pre_rule_lines = _collect_pre_rule_lines(view, scope_headers)
    lod2 = view.get("lod2_meta", {}) if isinstance(view.get("lod2_meta"), dict) else {}
    rule_blocks: list[tuple[str, list[str]]] = []
    for r in view.get("active_rules", []):
        if not r:
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
        if isinstance(r.get("external_links"), list):
            for lk in r["external_links"]:
                if isinstance(lk, dict) and lk.get("uri"):
                    sum_s = f" ({lk['summary']})" if lk.get("summary") else ""
                    meta_lines.append(f"  -> link[{lk.get('kind', 'reference')}]: {lk['uri']}{sum_s}")
        rule_blocks.append((r_line, meta_lines))

    note_lines = [
        f"~ NOTE {n.get('id', '')}: {n.get('text', '')}"
        for n in view.get("ambient_notes", []) if n and n.get("text")
    ]
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
            rule_part = [
                ln for i, (rl, _) in enumerate(admitted_rules)
                for ln in ([rl, *accepted_meta[i], m_ln] if i == idx else [rl, *accepted_meta[i]])
            ]
            trial_lines = [*admitted_pre, *rule_part]
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
