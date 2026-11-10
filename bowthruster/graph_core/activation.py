"""Spec 21 §1.3: Order-Independent Seed Activation (`compute_seed_activations`), Topological Temperature (`infer_query_temperature`), and Adaptive Barrier $\\tau(T)$ (`compute_tau`)."""

from __future__ import annotations

from typing import Any

from bowthruster.nlp import (
    dense_text_similarities,
    lexical_token_similarity,
    max_lexical_similarity,
    restore_ascii_dots,
    soft_token_overlap_count,
)
from .formatting import tokenize_content_words

_lexical_token_similarity = lexical_token_similarity


def _build_rule_index(graph: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """Maps `rule_id -> (scope_id, rule_dict)` across all scopes in `graph`."""
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for sid, scope in graph.get("scopes", {}).items():
        if not isinstance(scope, dict):
            continue
        for r in scope.get("rules", []):
            if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"]:
                out[r["id"]] = (sid, r)
    return out


def _scope_cross_degree(graph: dict[str, Any], scope_id: str, rule_idx: dict[str, tuple[str, dict[str, Any]]]) -> int:
    """Computes the cross-scope degree of `scope_id` across cross-scope `refs`, `in_edges`, `overrides`, and `exceptions` (Spec 21 §1.3)."""
    scopes = graph.get("scopes", {})
    scope = scopes.get(scope_id)
    if not isinstance(scope, dict):
        return 0
    neighbors: set[str] = set()
    for r in scope.get("rules", []):
        if not isinstance(r, dict):
            continue
        for ref_s in r.get("refs", []):
            if isinstance(ref_s, str) and ref_s in scopes and ref_s != scope_id:
                neighbors.add(ref_s)
        for ov_id in r.get("overrides", []):
            if ov_id in rule_idx and rule_idx[ov_id][0] != scope_id:
                neighbors.add(rule_idx[ov_id][0])
        for ex in r.get("exceptions", []):
            if isinstance(ex, dict) and ex.get("rule_id") in rule_idx:
                ex_sid = rule_idx[ex["rule_id"]][0]
                if ex_sid != scope_id:
                    neighbors.add(ex_sid)
    for in_rid in scope.get("in_edges", []):
        if in_rid in rule_idx and rule_idx[in_rid][0] != scope_id:
            neighbors.add(rule_idx[in_rid][0])
    for dep_s in scope.get("depends_on", []):
        if isinstance(dep_s, str) and dep_s in scopes and dep_s != scope_id:
            neighbors.add(dep_s)
    for fl in graph.get("flows", []):
        if isinstance(fl, dict):
            src, tgt = fl.get("source"), fl.get("target")
            if src == scope_id and isinstance(tgt, str) and tgt in scopes and tgt != scope_id:
                neighbors.add(tgt)
            elif tgt == scope_id and isinstance(src, str) and src in scopes and src != scope_id:
                neighbors.add(src)
    return len(neighbors)


def _rule_matches_target_or_refs(r: dict[str, Any], q_clean: str, q_tokens: set[str]) -> bool:
    """Returns `True` iff rule `r` matches `query` via `target_prop` or `refs` (Spec 21 §1.3)."""
    tp = str(r.get("target_prop") or "").lower().strip()
    if tp and (tp in q_clean or tp in q_tokens):
        return True
    for ref_item in r.get("refs", []):
        ref_low = str(ref_item or "").lower().strip()
        if not ref_low:
            continue
        ref_toks = set(tokenize_content_words(ref_low))
        if ref_low in q_clean or ref_low in q_tokens or bool(q_tokens & ref_toks):
            return True
    return False


def _score_single_rule(
    r: dict[str, Any],
    bge_sim: float,
    q_clean: str,
    q_tokens: set[str],
    scope_anchored: bool,
    scope_meta: dict[str, Any],
    initial_scope_score: float,
    s_tokens: set[str] | None = None,
) -> tuple[float, bool]:
    """Pure, order-independent single-rule seed scorer returning `(r_score, rule_anchors_scope)` (Spec 21 §1.3)."""
    rid = str(r["id"])
    r_score = 0.0
    rule_anchors_scope = False
    tp = str(r.get("target_prop") or "").lower().strip()
    if _rule_matches_target_or_refs(r, q_clean, q_tokens):
        r_score = max(r_score, 0.80)
        rule_anchors_scope = True

    r_tokens = set(tokenize_content_words(f"{r.get('text', '')} {tp}"))
    for item in list(r.get("conditions", [])) + list(r.get("refs", [])):
        r_tokens.update(tokenize_content_words(str(item)))

    type_facet_toks = set(tokenize_content_words(f"{r.get('type', '')} {r.get('facet', '')}"))
    if scope_anchored:
        r_tokens.update(type_facet_toks)
        if bool(q_tokens & type_facet_toks) or max_lexical_similarity(q_tokens, type_facet_toks) >= 0.65:
            r_score = max(r_score, 0.80)
            rule_anchors_scope = True

    rmeta = scope_meta.get(rid) if isinstance(scope_meta, dict) else None
    m_toks: set[str] = set()
    if isinstance(rmeta, dict) and rmeta:
        for mk, mv in rmeta.items():
            if mk != "heading_path":
                m_toks.update(tokenize_content_words(f"{mk} {mv}"))
    else:
        m_toks = {
            str(t).lower()
            for t in r.get("meta_tokens", [])
            if str(t).strip() and str(t).lower() not in ("heading_path", "heading", "path")
        }
    if s_tokens:
        m_toks -= s_tokens

    overlap_r, soft_r = soft_token_overlap_count(q_tokens, r_tokens, threshold=0.70)
    if overlap_r or soft_r > 0:
        denom_r = max(1, min(len(q_tokens), len(r_tokens)))
        r_score = max(r_score, min(0.95, 0.50 * ((len(overlap_r) + 0.85 * soft_r) / denom_r)))

    if scope_anchored and bge_sim >= 0.72:
        r_score = max(r_score, round(bge_sim, 6))
        rule_anchors_scope = True

    overlap_m = q_tokens & m_toks
    if overlap_m:
        r_score = max(r_score, min(0.85, r_score + 0.25 * len(overlap_m)))
    if initial_scope_score >= 0.50:
        r_score = max(r_score, round(initial_scope_score * 0.75, 6))
    return r_score, rule_anchors_scope


def _score_scope_initial(
    graph: dict[str, Any], sid: str, scope: dict[str, Any],
    q_clean: str, q_tokens: set[str]
) -> tuple[float, set[str]]:
    sid_low = sid.lower()
    base_sid = sid_low.split("::")[0]
    initial_scope_score = 0.0
    if sid_low in q_clean or (base_sid and base_sid != "global_defaults" and base_sid in q_clean):
        initial_scope_score = max(initial_scope_score, 0.85)
    for alias in scope.get("aliases", []):
        al_low = str(alias).lower().strip()
        if al_low and al_low in q_clean:
            initial_scope_score = max(initial_scope_score, 0.85)

    s_tokens = set(tokenize_content_words(sid))
    for alias in scope.get("aliases", []):
        s_tokens.update(tokenize_content_words(str(alias)))
    for ak, av in scope.get("attributes", {}).items():
        s_tokens.update(tokenize_content_words(f"{ak} {av}"))
    if overlap_s := (q_tokens & s_tokens):
        denom = max(1, min(len(q_tokens), len(s_tokens)))
        initial_scope_score = max(initial_scope_score, min(0.90, 0.50 * (len(overlap_s) / denom)))

    sbf_tokens: set[str] = set()
    for k_sbf in ("role", "owner", "stage", "kind", "status"):
        if scope.get(k_sbf):
            val_s = str(scope[k_sbf]).strip()
            sbf_tokens.update(tokenize_content_words(val_s))
            if k_sbf in ("owner", "stage") and val_s.lower() in q_clean:
                initial_scope_score = max(initial_scope_score, 0.85)
    for k_list in ("inputs", "outputs", "depends_on"):
        for item in scope.get(k_list, []):
            it_s = str(item).strip()
            sbf_tokens.update(tokenize_content_words(it_s))
            if it_s and it_s.lower() in q_clean:
                initial_scope_score = max(initial_scope_score, 0.80)
    for fl in graph.get("flows", []):
        if isinstance(fl, dict) and (fl.get("source") == sid or fl.get("target") == sid):
            for fk in ("payload", "action", "condition", "text", "summary"):
                if fl.get(fk):
                    sbf_tokens.update(tokenize_content_words(str(fl[fk])))
            if fl.get("payload") and str(fl["payload"]).lower() in q_clean:
                initial_scope_score = max(initial_scope_score, 0.80)
    for art in graph.get("lattice_artifacts", []):
        if isinstance(art, dict) and (art.get("scope_id") == sid or art.get("scope") == sid):
            for ak in ("name", "kind", "spec", "text", "summary", "owner", "stage"):
                if art.get(ak):
                    sbf_tokens.update(tokenize_content_words(str(art[ak])))
            for io_x in list(art.get("inputs", [])) + list(art.get("outputs", [])):
                sbf_tokens.update(tokenize_content_words(str(io_x)))
            if art.get("name") and str(art["name"]).lower() in q_clean:
                initial_scope_score = max(initial_scope_score, 0.85)
    if overlap_sbf := (q_tokens & sbf_tokens):
        denom_sbf = max(1, min(len(q_tokens), len(sbf_tokens)))
        initial_scope_score = max(initial_scope_score, min(0.90, 0.75 * (len(overlap_sbf) / denom_sbf)))
    return initial_scope_score, s_tokens


def compute_seed_activations(graph: dict[str, Any], query: str) -> tuple[dict[str, float], set[str]]:
    """Computes initial seed activations $A^{(0)}$ across scopes, rules, `meta_tokens`, and `active_tasks`,
    returning `(seed_activations, seed_scopes)` (Spec 21 §1.3)."""
    if not isinstance(graph, dict) or not isinstance(query, str) or not query.strip():
        return {}, set()
    q_clean = restore_ascii_dots(query).strip().lower()
    q_tokens = set(tokenize_content_words(query))
    scopes = graph.get("scopes", {})
    if (not q_tokens and not q_clean) or not isinstance(scopes, dict):
        return {}, set()

    activations: dict[str, float] = {}
    seed_scopes: set[str] = set()

    for sid, scope in scopes.items():
        if not isinstance(scope, dict):
            continue
        initial_scope_score, s_tokens = _score_scope_initial(graph, sid, scope, q_clean, q_tokens)
        rules_list = [r for r in scope.get("rules", []) if isinstance(r, dict) and r.get("id")]
        scope_anchored = initial_scope_score > 0.0 or any(
            _rule_matches_target_or_refs(r, q_clean, q_tokens) for r in rules_list
        )
        bge_sims = (
            dense_text_similarities(
                query,
                [f"[{r.get('type', '')} {r.get('facet', '')}] {r.get('text', '')}" for r in rules_list],
            )
            if scope_anchored and rules_list
            else [0.0] * len(rules_list)
        )
        scope_meta = scope.get("_meta", {}) if isinstance(scope.get("_meta"), dict) else {}
        max_rule_score = 0.0
        any_rule_anchored = False

        for idx, r in enumerate(rules_list):
            rid = str(r["id"])
            r_score, rule_anchors_scope = _score_single_rule(
                r, bge_sims[idx], q_clean, q_tokens, scope_anchored, scope_meta, initial_scope_score, s_tokens
            )
            if rule_anchors_scope:
                any_rule_anchored = True
            if r_score >= 0.20:
                activations[rid] = round(min(1.0, r_score), 6)
                max_rule_score = max(max_rule_score, r_score)

        scope_seed_score = max(initial_scope_score, 0.50 if any_rule_anchored else 0.0)
        scope_score = max(scope_seed_score, round(max_rule_score * 0.90, 6) if max_rule_score >= 0.45 else 0.0)
        if scope_score >= 0.20:
            activations[sid] = round(min(1.0, scope_score), 6)
        if scope_seed_score >= 0.35 or max_rule_score >= 0.45:
            seed_scopes.add(sid)

    for task in graph.get("active_tasks", []):
        if not isinstance(task, dict) or not task.get("id"):
            continue
        tid = str(task["id"]).strip()
        if not tid:
            continue
        tid_low = tid.lower()
        if tid_low in q_clean or tid_low in q_tokens:
            t_score = 1.0
        else:
            t_extra = f"{task.get('owner', '')} {task.get('stage', '')} {task.get('target_prop', '')} {task.get('condition', '')}"
            t_tokens = set(tokenize_content_words(f"{tid} {task.get('text', '')} {t_extra}"))
            overlap_t = q_tokens & t_tokens
            if overlap_t:
                denom_t = max(1, min(len(q_tokens), len(t_tokens)))
                t_score = min(0.90, 0.50 * (len(overlap_t) / denom_t) + 0.15 * len(overlap_t))
            else:
                t_score = 0.0
            for tk_coord in ("owner", "stage"):
                if task.get(tk_coord) and str(task[tk_coord]).lower().strip() in q_clean:
                    t_score = max(t_score, 0.80)

        if t_score >= 0.20:
            activations[tid] = round(t_score, 6)
            tsid = str(task.get("scope") or "")
            if tsid in scopes and t_score >= 0.35:
                activations[tsid] = max(activations.get(tsid, 0.0), round(t_score * 0.85, 6))
                seed_scopes.add(tsid)

    return activations, seed_scopes


def infer_query_temperature(graph: dict[str, Any], query: str) -> float:
    """Total function mapping seed activations and cross-scope topology onto `{0.20, 0.30, 0.70}`
    governed by Bend 2 Law 9 (`temperature_barrier_monotonic`) (Spec 21 §1.3)."""
    seed_acts, seed_scopes = compute_seed_activations(graph, query)
    if not seed_acts or len(seed_scopes) == 0:
        return 0.30
    if len(seed_scopes) >= 3:
        return 0.70
    has_active_task_seed = any(
        isinstance(t, dict) and seed_acts.get(str(t.get("id", "")), 0.0) >= 0.50
        for t in graph.get("active_tasks", [])
    )
    rule_idx = _build_rule_index(graph)
    max_deg = max((_scope_cross_degree(graph, sid, rule_idx) for sid in seed_scopes), default=0)
    if len(seed_scopes) == 1 and max_deg == 0 and not has_active_task_seed:
        return 0.20
    return 0.30


def _tau_min_for_temperature(temp: float) -> float:
    """Computes $\\tau_{\\min}(T) = \\text{clip}(0.35 - 0.50 \\cdot T, 0.10, 0.25)$ (Spec 21 §1.3)."""
    return round(max(0.10, min(0.25, 0.35 - 0.50 * float(temp))), 6)


def compute_tau(h_max: float, temperature: float) -> float:
    """Computes adaptive activation threshold $\\tau(T) = \\max(\\tau_{\\min}(T), \\alpha(T) \\cdot A_{\\max})$
    under the single monotone linear law (Spec 21 §1.3)."""
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise ValueError(f"temperature must be a float in [0.05, 1.00], got {temperature!r}")
    temp = float(temperature)
    if not (0.05 <= temp <= 1.00):
        raise ValueError(f"temperature {temp} out of valid range [0.05, 1.00]")
    if isinstance(h_max, bool) or not isinstance(h_max, (int, float)) or float(h_max) < 0.0:
        raise ValueError(f"h_max must be a non-negative float, got {h_max!r}")

    alpha = max(0.15, min(0.50, 0.55 - 0.50 * temp))
    tau_min = max(0.10, min(0.25, 0.35 - 0.50 * temp))
    return round(max(tau_min, alpha * float(h_max)), 6)
