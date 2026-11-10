"""Spec 24A — `ai_memory.cue_extractor`: Deterministic Zero-Regex Concept Seed Cue Extraction & Helpers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

from bowthruster.graph_core import SQLiteGraphStore, compute_seed_activations, tokenize_content_words

from .schema import (
     MAX_LINKS_PER_NODE,
    MAX_SEED_CUES,
    ExternalLinkPointer,
    _pick,
    get_concept_frame,
    normalize_external_link,
    upsert_concept_frame,
)

HOP1_DECAY, HOP2_DECAY, SEED_SCORE_FLOOR = 0.70, 0.50, 0.20


@dataclass(frozen=True)
class _ActivationNode:
    hop: int
    activation: float
    reached_via_sink: bool = False
    is_bridge: bool = False


def can_propagate_from_node(reached_via_sink: bool, above_push_epsilon: bool, within_max_hops: bool) -> bool:
    """Bend 2 Law 2 (`no_thru_traffic_sink_law`)."""
    return (not bool(reached_via_sink)) and bool(above_push_epsilon) and bool(within_max_hops)


def should_accept_seed_cue(is_non_empty_token: bool, within_12_cap: bool, is_duplicate_seed: bool) -> bool:
    """Bend 2 Law 2 (`cue_seed_determinism_law`):
    Returns `True` iff `is_non_empty_token is True` and `within_12_cap is True` and `is_duplicate_seed is False`."""
    if not bool(is_non_empty_token):
        return False
    return bool(within_12_cap) and (not bool(is_duplicate_seed))


def _normalize_cue_token(raw_token: str) -> str:
    """Normalizes a raw whitespace token by stripping leading/trailing non-alphanumeric chars (zero `import re`)."""
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
        raw_parts = [
            w for item in items if isinstance(item, str)
            for w in item.replace(",", " ").replace(";", " ").split()
        ]
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


def _extract_directed_topology(
    graph: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]]:
    scopes = (graph.get("scopes") or {})
    valid = {s for s, sc in scopes.items() if s != "global_defaults" }
    fwd_flow, bwd_flow, fwd_dep = ({s: [] for s in valid} for _ in range(3))

    def _add(adj: dict[str, list[str]], u: str, v: str) -> None:
        if u in valid and v in valid and u != v and v not in adj[u]:
            adj[u].append(v)

    for sid in valid:
        sc = scopes[sid]
        sc_dep = sc.get("depends_on")
        raw_deps = list(sc_dep) if isinstance(sc_dep, list) else ([sc_dep] if sc_dep else [])
        ad = sc["attributes"].get("depends_on") if bool(sc.get("attributes")) else None
        raw_deps.extend(ad if isinstance(ad, list) else (ad.replace(",", " ").split() if isinstance(ad, str) else []))
        for d in raw_deps:
            _add(fwd_dep, sid, str(d or "").strip())
        for r in (r for r in (sc.get("rules") or [])):
            rtxt = str(r.get("text") or "").lower()
            if "depends on" in rtxt or "depends_on" in rtxt or r.get("target_prop") == "depends_on":
                for ref_s in ((r.get("refs") or [])):
                    _add(fwd_dep, sid, str(ref_s or "").strip())
    for fl in (f for f in (graph.get("flows") or [])):
        u, v = str(fl.get("source") or "").strip(), str(fl.get("target") or "").strip()
        _add(fwd_flow, u, v)
        _add(bwd_flow, v, u)
    for sid in valid:
        sc = scopes[sid]
        children = (sc.get("children") or [])
        for ns in (str(nbr or "").strip() for nbr in [sc.get("parent"), *children]):
            if ns in valid and ns not in fwd_dep[sid] and sid not in fwd_dep.get(ns, []):
                for a, b in ((sid, ns), (ns, sid)):
                    _add(fwd_flow, a, b)
                    _add(bwd_flow, b, a)
        for r in (r for r in (sc.get("rules") or [])):
            for rs in (str(x or "").strip() for x in ((r.get("refs") or []))):
                if rs in valid and rs not in fwd_dep[sid]:
                    _add(fwd_flow, sid, rs)
                    _add(bwd_flow, rs, sid)
        for es in (str(x or "").strip() for x in ((sc.get("in_edges") or []))):
            src_s = es.split(".", 1)[0] if "." in es else es
            if src_s in valid and sid not in fwd_dep.get(src_s, []):
                _add(fwd_flow, src_s, sid)
                _add(bwd_flow, sid, src_s)
    return fwd_flow, bwd_flow, fwd_dep


def _normalize_cues(concept_cues: list[str] | str) -> list[str]:
    if isinstance(concept_cues, str):
        s = concept_cues.strip()
        if not s:
            return []
        words = tokenize_content_words(s) if " " in s else [s]
        return list(dict.fromkeys([s, *words]))[:MAX_SEED_CUES]
    if isinstance(concept_cues, (list, tuple, set)):
        cleaned = [str(x or "").strip() for x in concept_cues if str(x or "").strip()]
        return list(dict.fromkeys(cleaned))[:MAX_SEED_CUES]
    raise ValueError(f"concept_cues must be str or sequence, got {type(concept_cues).__name__}")


def _seed_activation_state(graph: dict[str, Any], cues: list[str]) -> dict[str, _ActivationNode]:
    scopes = (graph.get("scopes") or {})
    cue_set, state = {c.lower() for c in cues}, {}
    for sid, sc in scopes.items():
        if sid != "global_defaults" :
            raw_al = (sc.get("aliases") or [])
            al_set = {str(a).strip().lower() for a in raw_al if str(a).strip()}
            if sid.lower() in cue_set or bool(cue_set & al_set):
                state[sid] = _ActivationNode(hop=0, activation=1.0)
    if state:
        return state
    seed_acts, seed_scopes = compute_seed_activations(graph, " ".join(cues))
    for sid, sc in scopes.items():
        if sid != "global_defaults" :
            score = float(seed_acts.get(sid, 0.0))
            for r in (r for r in (sc.get("rules") or [])):
                has_tok = bool(cue_set & set(tokenize_content_words(str(r.get("text") or ""))))
                r_score = float(seed_acts.get(str(r.get("id") or ""), 0.0))
                score = max(score, r_score, HOP1_DECAY if has_tok else 0.0)
            if sid in seed_scopes or score >= SEED_SCORE_FLOOR:
                state[sid] = _ActivationNode(hop=0, activation=round(min(1.0, max(score, HOP2_DECAY)), 6))
    return state


def _run_directed_wave(
    seed_id: str,
    seed_energy: float,
    flow_adj: dict[str, list[str]],
    dep_adj: dict[str, list[str]],
    other_seeds: set[str],
    max_hops: int,
    decay_gamma: float,
    push_epsilon: float,
) -> dict[str, _ActivationNode]:
    state: dict[str, _ActivationNode] = {seed_id: _ActivationNode(hop=0, activation=seed_energy)}
    frontier: list[str] = [seed_id]
    for cur_hop in range(1, max_hops + 1):
        next_frontier: list[str] = []
        for u in frontier:
            u_node = state[u]
            can_prop = can_propagate_from_node(
                u_node.reached_via_sink, u_node.activation >= push_epsilon, cur_hop <= max_hops
            )
            if (u != seed_id and u in other_seeds) or not can_prop:
                continue
            if flow_nbrs := flow_adj.get(u, []):
                step_e = round(u_node.activation * (decay_gamma / math.sqrt(max(1, len(flow_nbrs)))), 6)
                if can_propagate_from_node(u_node.reached_via_sink, step_e >= push_epsilon, cur_hop <= max_hops):
                    for v in flow_nbrs:
                        prev = state.get(v)
                        if prev is None or prev.reached_via_sink or (prev.hop == cur_hop and step_e > prev.activation):
                            state[v] = _ActivationNode(hop=cur_hop, activation=step_e, reached_via_sink=False)
                            if v not in next_frontier:
                                next_frontier.append(v)
            if cur_hop == 1 and dep_adj:
                if dep_nbrs := [d for d in dep_adj.get(u, []) if d not in other_seeds and d not in flow_nbrs]:
                    step_d = round(u_node.activation * (decay_gamma / math.sqrt(max(1, len(dep_nbrs)))), 6)
                    if can_propagate_from_node(u_node.reached_via_sink, step_d >= push_epsilon, cur_hop <= max_hops):
                        for d in (d for d in dep_nbrs if d not in state):
                            state[d] = _ActivationNode(hop=1, activation=step_d, reached_via_sink=True)
                            if d not in next_frontier:
                                next_frontier.append(d)
        if not (frontier := next_frontier):
            break
    return state


def verify_uri_resolver_entry(entry: dict[str, Any], *, max_links_per_node: int = MAX_LINKS_PER_NODE) -> bool:
    if not isinstance(entry, dict):
        raise ValueError(f"uri_resolver entry must be a dict, got {type(entry).__name__}")
    cap = min(max(0, int(max_links_per_node)), MAX_LINKS_PER_NODE)
    if "source_links" in entry and "external_uris" in entry:
        if list(entry["source_links"]) != list(entry["external_uris"]):
            raise ValueError("uri_resolver obligation violated: source_links != external_uris")
    if isinstance(ext_links := entry.get("external_links"), list):
        if len(ext_links) > cap:
            raise ValueError(f"uri_resolver bound violated: {len(ext_links)} links > {cap}")
        uris = [str(u) for lk in ext_links if isinstance(lk, dict) and (u := _pick(lk, "target_uri", "uri"))]
        if ("source_links" in entry and list(entry["source_links"]) != uris) or (
            "external_uris" in entry and list(entry["external_uris"]) != uris
        ):
            raise ValueError("uri_resolver obligation violated: source_links/external_uris mismatch")
    return True


def _read_links_for_concept(store_or_frame: Any, concept_id: str) -> list[ExternalLinkPointer]:
    if isinstance(store_or_frame, SQLiteGraphStore):
        fr = get_concept_frame(store_or_frame, concept_id)
        raw = store_or_frame.get_external_links(concept_id) or (fr.external_links if fr is not None else [])
    elif isinstance(store_or_frame, dict):
        verify_uri_resolver_entry(store_or_frame)
        ext, cval = store_or_frame.get("external_links"), store_or_frame.get(concept_id)
        if store_or_frame.get("concept_id") == concept_id and isinstance(ext, list):
            raw = ext
        elif isinstance(ext, dict) and isinstance(ext.get(concept_id), list):
            raw = ext[concept_id]
        elif "scopes" in store_or_frame:
            fr = get_concept_frame(store_or_frame, concept_id)
            raw = fr.external_links if fr is not None else []
        elif isinstance(cval, dict):
            verify_uri_resolver_entry(cval)
            raw = cval.get("external_links", [])
        else:
            raw = cval if isinstance(cval, list) else []
    else:
        raise ValueError(f"Unsupported store_or_frame type: {type(store_or_frame).__name__}")
    out, seen = [], set()
    for lk in raw:
        if (ptr := normalize_external_link(lk, concept_id=concept_id)).target_uri not in seen:
            seen.add(ptr.target_uri)
            out.append(ptr)
    return out


def _write_links_for_concept(store_or_frame: Any, concept_id: str, links: list[ExternalLinkPointer]) -> None:
    is_store_or_wb = isinstance(store_or_frame, SQLiteGraphStore) or (
        isinstance(store_or_frame, dict) and "scopes" in store_or_frame
    )
    if is_store_or_wb:
        if (fr := get_concept_frame(store_or_frame, concept_id)) is not None:
            fr["external_links"] = list(links)
            upsert_concept_frame(store_or_frame, fr)
        else:
            upsert_concept_frame(store_or_frame, concept_id, "", list(links))
        return
    if not isinstance(store_or_frame, dict):
        raise ValueError(f"Unsupported store_or_frame type: {type(store_or_frame).__name__}")
    uris = [p.target_uri for p in links]
    if "concept_id" in store_or_frame and store_or_frame.get("concept_id") in (concept_id, "", None):
        store_or_frame["concept_id"], store_or_frame["external_links"] = concept_id, list(links)
        for k in (k for k in ("source_links", "external_uris") if k in store_or_frame):
            store_or_frame[k] = list(uris)
    elif isinstance(ext := store_or_frame.get("external_links"), dict):
        ext[concept_id] = list(links)
    elif isinstance(cval := store_or_frame.get(concept_id), dict):
        cval["external_links"] = list(links)
        for k in (k for k in ("source_links", "external_uris") if k in cval):
            cval[k] = list(uris)
    else:
        store_or_frame[concept_id] = list(links)
