"""Spreading-Activation Recall & Neighbor Expansion (`ai_memory/spreading_recall.py`)."""

from __future__ import annotations

import copy
import math
from typing import Any

from bowthruster.ai_memory.schema import ConceptFrame, _pick
from bowthruster.graph_core import SQLiteGraphStore, token_cost

from .cue_extractor import (
    _ActivationNode,
    _extract_directed_topology,
    _normalize_cues,
    _run_directed_wave,
    _seed_activation_state,
)
from .hippocampal_index import (
    DEFAULT_ACTIVATION_TAU,
    DEFAULT_BRIDGE_BETA,
    DEFAULT_DECAY_GAMMA,
    DEFAULT_PUSH_EPSILON,
    HopBucket,
    MAX_LINKS_PER_NODE,
    MAX_LOD1_SYNOPSIS_WORDS,
    MAX_LOD1_TOKEN_BUDGET,
    MAX_RADIAL_HOPS,
    MAX_RECALL_HOPS,
    MAX_RECALL_TOKEN_BUDGET,
    MAX_SYNOPSIS_WORDS,
    _clamp_synopsis,
    _normalize_external_links,
    _req_int,
    _req_str,
    _resolve_store_and_graph,
    classify_hop_bucket,
    should_include_recalled_concept,
)


def bounded_spreading_activation(
    store_or_graph: Any,
    seed_cues: list[str] | str,
    *,
    max_recall_hops: int = MAX_RECALL_HOPS,
    decay_gamma: float = DEFAULT_DECAY_GAMMA,
    bridge_beta: float = DEFAULT_BRIDGE_BETA,
    activation_tau: float = DEFAULT_ACTIVATION_TAU,
    push_epsilon: float = DEFAULT_PUSH_EPSILON,
) -> list[dict[str, Any]]:
    """Runs directed multi-cue epsilon-push activation + Steiner bridge intersection."""
    _req_int(max_recall_hops, "max_recall_hops", 0, MAX_RECALL_HOPS)
    _, graph = _resolve_store_and_graph(store_or_graph)
    cues = _normalize_cues(seed_cues)
    if not cues or not graph.get("scopes"):
        return []
    seeds = _seed_activation_state(graph, cues)
    if not seeds or max_recall_hops == 0:
        return [
            {
                "concept_id": s,
                "hop": 0,
                "hop_bucket": HopBucket.Hop0Seed.value,
                "activation": seeds[s].activation,
            }
            for s in sorted(seeds, key=lambda k: (-seeds[k].activation, k))
        ]
    fwd_flow, bwd_flow, fwd_dep = _extract_directed_topology(graph)
    seed_ids, seed_set = sorted(seeds.keys()), set(seeds.keys())
    eff_hops = max_recall_hops if len(seed_ids) >= 2 else min(max_recall_hops, MAX_RADIAL_HOPS)
    w_plus = {
        s: _run_directed_wave(
            s,
            seeds[s].activation,
            fwd_flow,
            fwd_dep,
            seed_set - {s},
            eff_hops,
            decay_gamma,
            push_epsilon,
        )
        for s in seed_ids
    }
    w_minus = {
        s: _run_directed_wave(
            s,
            seeds[s].activation,
            bwd_flow,
            {},
            seed_set - {s},
            eff_hops,
            decay_gamma,
            push_epsilon,
        )
        for s in seed_ids
    }
    all_cands = set(seed_ids) | {v for s in seed_ids for v in (*w_plus[s], *w_minus[s])}
    results: dict[str, _ActivationNode] = {}
    radial_cap = min(max_recall_hops, MAX_RADIAL_HOPS)
    for v in sorted(all_cands):
        if v in seeds:
            results[v] = seeds[v]
            continue
        vis = [
            n
            for s in seed_ids
            for n in (w_plus[s].get(v), w_minus[s].get(v))
            if n is not None and n.hop > 0
        ]
        if not vis:
            continue
        min_hop = min(n.hop for n in vis)
        base_v = max((n.activation for n in vis if n.hop <= radial_cap), default=0.0)
        bridge_sum = 0.0
        bridge_hops: list[int] = []
        for idx1, s1 in enumerate(seed_ids):
            for s2 in seed_ids[idx1 + 1 :]:
                n1p, n2m = w_plus[s1].get(v), w_minus[s2].get(v)
                n2p, n1m = w_plus[s2].get(v), w_minus[s1].get(v)
                ok1 = bool(
                    n1p
                    and n2m
                    and not n1p.reached_via_sink
                    and not n2m.reached_via_sink
                    and n1p.hop + n2m.hop <= max_recall_hops
                )
                ok2 = bool(
                    n2p
                    and n1m
                    and not n2p.reached_via_sink
                    and not n1m.reached_via_sink
                    and n2p.hop + n1m.hop <= max_recall_hops
                )
                b1 = math.sqrt(n1p.activation * n2m.activation) if ok1 else 0.0
                b2 = math.sqrt(n2p.activation * n1m.activation) if ok2 else 0.0
                if b1 > 0.0 or b2 > 0.0:
                    bridge_sum += max(b1, b2)
                    bridge_hops.append((n1p.hop + n2m.hop) if b1 >= b2 else (n2p.hop + n1m.hop))
        is_bridge = bridge_sum > 0.0
        if is_bridge and base_v == 0.0:
            base_v = max(n.activation for n in vis)
        total_v = round(min(1.0, base_v + (bridge_beta * bridge_sum if is_bridge else 0.0)), 6)
        eff_hop = min(min_hop, min(bridge_hops)) if is_bridge else min_hop
        within_reach = min_hop <= radial_cap or (is_bridge and eff_hop <= max_recall_hops)
        if total_v >= activation_tau and within_reach:
            results[v] = _ActivationNode(
                hop=eff_hop,
                activation=total_v,
                reached_via_sink=all(n.reached_via_sink for n in vis),
                is_bridge=is_bridge,
            )
    ordered = sorted(
        results,
        key=lambda s: (
            0 if results[s].hop == 0 else (1 if results[s].is_bridge else 2),
            -results[s].activation,
            results[s].hop,
            s,
        ),
    )
    return [
        {
            "concept_id": s,
            "hop": results[s].hop,
            "activation": results[s].activation,
            "hop_bucket": classify_hop_bucket(
                results[s].hop,
                is_steiner_bridge=results[s].is_bridge,
                max_hops=max_recall_hops,
            ).value,
        }
        for s in ordered
    ]


def _extract_scope_synopsis_and_links(
    store: SQLiteGraphStore | None,
    graph: dict[str, Any],
    concept_id: str,
    max_words: int,
) -> tuple[str, list[dict[str, Any]]]:
    sc = (graph.get("scopes") or {}).get(concept_id, {})
    if not sc:
        return concept_id, []
    attrs = sc.get("attributes") or {}
    rules = list(sc.get("rules") or [])
    fallback_txt = (rules[0].get("text") if rules else "") or sc.get("role") or concept_id
    raw_syn = _pick(attrs, "lod1_synopsis", default=fallback_txt)
    synopsis = _clamp_synopsis(str(raw_syn).strip(), max_words)
    raw_links: list[dict[str, Any]] = []
    if store is not None:
        raw_links.extend(store.get_external_links(concept_id))
        for r in (r for r in rules if r.get("id")):
            raw_links.extend(store.get_external_links(str(r["id"])))
    if isinstance(top_links := graph.get("external_links"), dict):
        for key in (concept_id, *(str(r.get("id") or "") for r in rules)):
            if key and isinstance(top_links.get(key), list):
                raw_links.extend(top_links[key])
    for r in (r for r in rules if isinstance(r.get("external_links"), list)):
        raw_links.extend(r["external_links"])
    return synopsis, _normalize_external_links(concept_id, raw_links[:MAX_LINKS_PER_NODE])


def _build_concept_frame_record(
    cid: str,
    synopsis: str,
    links: list[dict[str, Any]],
    hop: int,
    hop_bucket: str,
    activation: float,
    cost: int,
) -> ConceptFrame:
    return ConceptFrame(
        {
            "concept_id": cid,
            "lod1_synopsis": synopsis,
            "external_links": links,
            "source_links": copy.deepcopy(links),
            "hop": hop,
            "hop_distance": hop,
            "hop_bucket": hop_bucket,
            "activation": activation,
            "token_cost": cost,
        }
    )


def expand_concept_neighbors(
    graph_or_store: Any,
    concept_id: str,
    *,
    max_hops: int = 1,
    token_budget: int = MAX_RECALL_TOKEN_BUDGET,
    max_words: int = MAX_SYNOPSIS_WORDS,
    decay_gamma: float = DEFAULT_DECAY_GAMMA,
) -> list[ConceptFrame]:
    """Stage 2 Deliberate Neighbor Expansion along flow, hierarchy, and 1-hop `depends_on` edges."""
    cid_clean = _req_str(concept_id, "concept_id")
    _req_int(max_hops, "max_hops", 1, MAX_RECALL_HOPS)
    _req_int(token_budget, "token_budget", 1, MAX_LOD1_TOKEN_BUDGET)
    _req_int(max_words, "max_words", 1, MAX_LOD1_SYNOPSIS_WORDS)
    store, graph = _resolve_store_and_graph(graph_or_store)
    scopes = graph.get("scopes") or {}
    target = (
        cid_clean
        if cid_clean in scopes
        else next((s for s in scopes if s.lower() == cid_clean.lower()), "")
    )
    if not target or target == "global_defaults":
        return []
    fwd_flow, bwd_flow, fwd_dep = _extract_directed_topology(graph)
    bi_flow = {
        s: list(dict.fromkeys([*fwd_flow.get(s, []), *bwd_flow.get(s, [])]))
        for s in scopes
        if s != "global_defaults"
    }
    wave_state = _run_directed_wave(
        target, 1.0, bi_flow, fwd_dep, set(), max_hops, decay_gamma, DEFAULT_PUSH_EPSILON
    )
    frames: list[ConceptFrame] = []
    used_tokens = 0
    ordered_cids = sorted(
        (s for s in wave_state if s != target),
        key=lambda s: (-wave_state[s].activation, wave_state[s].hop, s),
    )
    for cid in ordered_cids:
        node = wave_state[cid]
        hop_bucket = classify_hop_bucket(
            node.hop,
            is_steiner_bridge=(node.hop > MAX_RADIAL_HOPS),
            max_hops=max_hops,
        ).value
        synopsis, links = _extract_scope_synopsis_and_links(store, graph, cid, max_words)
        cost = max(1, token_cost(synopsis))
        budget_ok = (used_tokens + cost <= token_budget) and (token_budget <= MAX_LOD1_TOKEN_BUDGET)
        words_ok = (len(synopsis.split()) <= max_words) and (max_words <= MAX_LOD1_SYNOPSIS_WORDS)
        if should_include_recalled_concept(hop_bucket, budget_ok, words_ok):
            used_tokens += cost
            frames.append(
                _build_concept_frame_record(
                    cid, synopsis, links, node.hop, hop_bucket, node.activation, cost
                )
            )
    return frames
