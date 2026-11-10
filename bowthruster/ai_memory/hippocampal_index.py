"""Stage 24B (`ai_memory`): Hippocampal Concept Index, Multi-Cue Steiner Bridge Recall, & Neighbor Expansion (`hippocampal_index.py`)."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from bowthruster.ai_memory.schema import ConceptFrame
from bowthruster.graph_core import SQLiteGraphStore, compute_seed_activations, propagate_nodes, token_cost, tokenize_content_words

MAX_RECALL_HOPS, MAX_LOD1_TOKEN_BUDGET, MAX_LOD1_SYNOPSIS_WORDS, MAX_LINKS_PER_NODE, MAX_SEED_CUES = 6, 400, 50, 8, 12
MAX_RECALL_TOKEN_BUDGET, MAX_SYNOPSIS_WORDS, MAX_RADIAL_HOPS = MAX_LOD1_TOKEN_BUDGET, MAX_LOD1_SYNOPSIS_WORDS, 2
DEFAULT_DECAY_GAMMA, DEFAULT_BRIDGE_BETA, DEFAULT_ACTIVATION_TAU, DEFAULT_PUSH_EPSILON = 0.85, 0.85, 0.15, 0.04
LOD_0, LOD_1, LOD_2 = "LOD0", "LOD1", "LOD2"
HOP1_DECAY, HOP2_DECAY, SEED_SCORE_FLOOR = 0.70, 0.50, 0.20


class HopBucket(str, Enum):
    """Discrete hop-distance bucket governed by Bend 2 Law 1 (`recall_hop_bound_law`)."""
    Hop0Seed = "Hop0Seed"
    Hop1Neighbor = "Hop1Neighbor"
    Hop2Second = "Hop2Second"
    HopExceeds2 = "HopExceeds2"


@dataclass(frozen=True)
class _ActivationNode:
    """Immutable per-scope activation state `(hop, activation, reached_via_sink, is_bridge)`."""
    hop: int
    activation: float
    reached_via_sink: bool = False
    is_bridge: bool = False


_HOP_BUCKET_MAP = {
    "Hop0Seed": HopBucket.Hop0Seed, "HOP_0_SEED": HopBucket.Hop0Seed, "0": HopBucket.Hop0Seed,
    "Hop1Neighbor": HopBucket.Hop1Neighbor, "HOP_1_NEIGHBOR": HopBucket.Hop1Neighbor, "1": HopBucket.Hop1Neighbor,
    "Hop2Second": HopBucket.Hop2Second, "HOP_2_SECOND": HopBucket.Hop2Second, "2": HopBucket.Hop2Second,
    "HopExceeds2": HopBucket.HopExceeds2, "HOP_EXCEEDS_2": HopBucket.HopExceeds2,
}


def _req_int(val: Any, name: str, lo: int, hi: int | None = None) -> int:
    if isinstance(val, bool) or not isinstance(val, int) or val < lo or (hi is not None and val > hi):
        raise ValueError(f"{name} must be an int in [{lo}, {hi if hi is not None else 'inf'}], got {val!r}")
    return val

def _req_str(val: Any, name: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"{name} must be a non-empty str, got {val!r}")
    return val.strip()

def step_lod_level(level: str) -> str:
    """Maps `LOD0 -> LOD1 -> LOD2 -> LOD2` (Spec 24 §2.1 Law 1)."""
    lvl = str(level or "").strip().upper()
    if lvl in (LOD_0, LOD_1, LOD_2):
        return LOD_1 if lvl == LOD_0 else LOD_2
    raise ValueError(f"Invalid LOD level: {level!r}")

def should_dereference_external(level: str, has_links: bool, deep_detail_needed: bool) -> bool:
    """Returns `True` iff `level == 'LOD2' and has_links and deep_detail_needed` (Spec 24 §2.1 Law 2)."""
    return str(level or "").strip().upper() == LOD_2 and bool(has_links) and bool(deep_detail_needed)

def classify_hop_bucket(
    hop_distance: int, *, is_steiner_bridge: bool = False,
    max_hops: int = MAX_RADIAL_HOPS
) -> HopBucket:
    """Maps a non-negative integer hop distance to its canonical `HopBucket`."""
    _req_int(hop_distance, "hop_distance", 0)
    if hop_distance <= MAX_RADIAL_HOPS and hop_distance <= max_hops:
        return (HopBucket.Hop0Seed, HopBucket.Hop1Neighbor, HopBucket.Hop2Second)[hop_distance]
    return HopBucket.Hop2Second if (is_steiner_bridge and hop_distance <= max_hops) else HopBucket.HopExceeds2

def should_include_recalled_concept(
    hop_bucket: HopBucket | str | int, budget_within_400: bool,
    words_within_50: bool
) -> bool:
    """Pure recall gate governed by Bend 2 Law 1 (`recall_hop_bound_law` in `bend_mem_24b/LAWS.bend`)."""
    if isinstance(hop_bucket, int) and not isinstance(hop_bucket, bool):
        bucket = classify_hop_bucket(hop_bucket)
    elif isinstance(hop_bucket, HopBucket):
        bucket = hop_bucket
    elif str(hop_bucket or "").strip() in _HOP_BUCKET_MAP:
        bucket = _HOP_BUCKET_MAP[str(hop_bucket or "").strip()]
    else:
        raise ValueError(f"Unknown hop_bucket: {hop_bucket!r}")
    return False if bucket is HopBucket.HopExceeds2 else (bool(budget_within_400) and bool(words_within_50))

def can_propagate_from_node(
    reached_via_depends_on_sink: bool, step_energy_above_epsilon: bool,
    within_max_hops: bool
) -> bool:
    """Pure propagation gate governed by Bend 2 Law 2 (`steiner_bridge_and_sink_isolation_law` in `bend_mem_24b/LAWS.bend`)."""
    return False if bool(reached_via_depends_on_sink) else (bool(step_energy_above_epsilon) and bool(within_max_hops))

def _validate_no_raw_documents(entry: dict[str, Any]) -> None:
    for k in ("raw_document", "raw_documents"):
        if entry.get(k) or any(isinstance(entry.get(s), dict) and entry[s].get(k) for s in ("attributes", "metadata")):
            raise ValueError(f"pointer_only_storage_law violation: {k!r} forbidden in node_storage")

def _clamp_synopsis(text: str, max_words: int = MAX_LOD1_SYNOPSIS_WORDS) -> str:
    _req_int(max_words, "lod1_synopsis_words", 1, MAX_LOD1_SYNOPSIS_WORDS)
    return " ".join(str(text or "").strip().split()[:max_words])

def _normalize_external_links(concept_id: str, raw_links: list[Any] | None) -> list[dict[str, Any]]:
    if not raw_links:
        return []
    if not isinstance(raw_links, list) or len(raw_links) > MAX_LINKS_PER_NODE:
        raise ValueError(f"external_links must be a list of <= {MAX_LINKS_PER_NODE} items")
    out, seen_uris = [], set()
    for idx, item in enumerate(raw_links):
        if isinstance(item, str) and item.strip() and item.strip() not in seen_uris:
            uri = item.strip()
            seen_uris.add(uri)
            out.append({
                "link_id": f"link_{concept_id}_{idx + 1}",
                "node_id": concept_id, "uri": uri,
                "target_uri": uri, "kind": "reference",
                "artifact_kind": "reference",
                "lod_tier": LOD_2, "summary": "",
                "metadata": {}
            })
        elif isinstance(item, dict):
            _validate_no_raw_documents(item)
            uri = str(item.get("uri") or item.get("target_uri") or "").strip()
            if uri and uri not in seen_uris:
                seen_uris.add(uri)
                kind = str(item.get("kind") or item.get("artifact_kind") or "reference").strip() or "reference"
                lod_tier = str(item.get("lod_tier") or LOD_2).strip() or LOD_2
                lid = str(item.get("link_id") or f"link_{concept_id}_{len(out) + 1}").strip()
                out.append({
                    "link_id": lid, "node_id": str(item.get("node_id") or concept_id).strip(),
                    "uri": uri, "target_uri": uri,
                    "kind": kind, "artifact_kind": kind,
                    "lod_tier": lod_tier, "summary": _clamp_synopsis(str(item.get("summary") or ""), MAX_LOD1_SYNOPSIS_WORDS),
                    "metadata": copy.deepcopy(item["metadata"]) if isinstance(item.get("metadata"), dict) else {}
                })
    return out

def _resolve_store_and_graph(store_or_graph: Any) -> tuple[SQLiteGraphStore | None, dict[str, Any]]:
    if isinstance(store_or_graph, AIMemoryStore):
        return store_or_graph.store, store_or_graph.graph
    if isinstance(store_or_graph, SQLiteGraphStore):
        return store_or_graph, store_or_graph.load_graph()
    if isinstance(store_or_graph, dict):
        store = getattr(store_or_graph, "_sqlite_store", None) or getattr(store_or_graph, "store", None) or store_or_graph.get("_sqlite_store")
        if store is not None and not isinstance(store, SQLiteGraphStore):
            raise ValueError(f"_sqlite_store must be SQLiteGraphStore, got {type(store).__name__}")
        return store, store_or_graph
    raise ValueError(f"Expected AIMemoryStore, SQLiteGraphStore, or graph dict, got {type(store_or_graph).__name__}")

def _ensure_concept_scope(graph: dict[str, Any], concept_id: str) -> dict[str, Any]:
    scopes = graph.setdefault("scopes", {})
    sc = scopes.get(concept_id) if isinstance(scopes.get(concept_id), dict) else {}
    for k, d in (
        ("id", concept_id), ("parent", None),
        ("aliases", []), ("children", []),
        ("rules", []), ("in_edges", []), ("attributes", {}),
        ("_meta", {})
    ):
        sc.setdefault(k, copy.deepcopy(d))
    scopes[concept_id] = sc
    return sc

def index_committed_rules(
    store_or_graph: Any, committed_rules: list[dict[str, Any]],
    *, current_turn: int = 1, lod1_synopsis_words: int = MAX_LOD1_SYNOPSIS_WORDS
) -> list[str]:
    """Indexes committed Whiteboard rules into pointer-only `ConceptFrame` nodes on `graph_core`."""
    if not isinstance(committed_rules, list):
        raise ValueError(f"committed_rules must be a list, got {type(committed_rules).__name__}")
    _req_int(lod1_synopsis_words, "lod1_synopsis_words", 1, MAX_LOD1_SYNOPSIS_WORDS)
    store, graph = _resolve_store_and_graph(store_or_graph)
    indexed_ids: list[str] = []
    for idx, rule in enumerate(committed_rules):
        if not isinstance(rule, dict):
            raise ValueError(f"committed_rules[{idx}] must be a dict, got {type(rule).__name__}")
        _validate_no_raw_documents(rule)
        if "lod1_synopsis" in rule and rule["lod1_synopsis"] is not None:
            synopsis = str(rule["lod1_synopsis"]).strip()
            if len(synopsis.split()) > lod1_synopsis_words:
                raise ValueError(f"lod1_synopsis word count ({len(synopsis.split())}) exceeds limit ({lod1_synopsis_words})")
        else:
            synopsis = _clamp_synopsis(
                str(rule.get("canonical_text") or rule.get("text") or rule.get("summary") or "").strip(),
                lod1_synopsis_words
            )
        concept_id = _req_str(
            str(
                rule.get("concept_id") or rule.get("primary_scope")
                or rule.get("scope") or rule.get("id")
                or f"concept_{idx + 1}"
            ),
            "concept_id"
        )
        raw_refs = rule.get("secondary_refs") if isinstance(rule.get("secondary_refs"), list) else rule.get("refs", [])
        refs = [
            str(r).strip() for r in (raw_refs if isinstance(raw_refs, list) else [])
            if str(r).strip() and str(r).strip() != concept_id
        ]
        if (
            isinstance(rule.get("external_links"), list)
            and isinstance(rule.get("source_links"), list)
            and _normalize_external_links(concept_id, rule["external_links"]) != _normalize_external_links(concept_id, rule["source_links"])
        ):
            raise ValueError("r_uri_resolver_7 violation: source_links must equal external_links")
        norm_links = _normalize_external_links(
            concept_id, rule.get("external_links")
            if "external_links" in rule else rule.get("source_links")
        )
        sc = _ensure_concept_scope(graph, concept_id)
        if synopsis:
            sc["attributes"]["lod1_synopsis"] = synopsis
        rule_id = str(rule.get("id") or f"{concept_id}.synopsis").strip()
        node_entry: dict[str, Any] = {
            "id": rule_id, "scope": concept_id,
            "type": str(rule.get("type") or "FACT"),
            "facet": str(rule.get("facet") or "context_facts"),
            "text": synopsis or concept_id, "target_prop": rule.get("target_prop"),
            "bound_op": rule.get("bound_op"), "bound_val": rule.get("bound_val"),
            "conditions": list(rule.get("conditions", []))
            if isinstance(rule.get("conditions"), list) else [],
            "refs": refs, "overrides": [], "exceptions": [],
            "meta_keys": [], "meta_tokens": tokenize_content_words(synopsis or concept_id),
            "created_turn": int(current_turn),
            "updated_turn": int(current_turn),
            "access_turns": [int(current_turn)]
        }
        if norm_links:
            node_entry["external_links"] = copy.deepcopy(norm_links)
            graph.setdefault("external_links", {})[rule_id] = copy.deepcopy(norm_links)
            graph.setdefault("external_links", {})[concept_id] = copy.deepcopy(norm_links)
        sc["rules"] = [r for r in sc["rules"] if isinstance(r, dict) and r.get("id") != rule_id] + [node_entry]
        for target_ref in refs:
            tgt_sc = _ensure_concept_scope(graph, target_ref)
            if rule_id not in tgt_sc["in_edges"]:
                tgt_sc["in_edges"].append(rule_id)
        if concept_id not in indexed_ids:
            indexed_ids.append(concept_id)
    if store is not None:
        store.save_graph(graph)
        for cid in indexed_ids:
            for lk in graph.get("external_links", {}).get(cid, []):
                if isinstance(lk, dict) and (u := str(lk.get("uri") or lk.get("target_uri") or "")):
                    store.upsert_external_link(
                        cid, u, lk.get("kind", lk.get("artifact_kind", "reference")),
                        lk.get("summary", lk.get("lod_tier", "")),
                        lk.get("metadata"), link_id=lk.get("link_id")
                    )
    if isinstance(store_or_graph, AIMemoryStore):
        store_or_graph.graph = graph
    return indexed_ids

def _extract_directed_topology(graph: dict[str, Any]) -> tuple[
    dict[str, list[str]], dict[str, list[str]],
    dict[str, list[str]]
]:
    """Extracts `(fwd_flow, bwd_flow, fwd_dep)` over valid scopes in `graph` (`s24b-spec-laws-3`)."""
    scopes = graph.get("scopes") if isinstance(graph.get("scopes"), dict) else {}
    valid = {s for s, sc in scopes.items() if s != "global_defaults" and isinstance(sc, dict)}
    fwd_flow, bwd_flow, fwd_dep = ({s: [] for s in valid} for _ in range(3))

    def _add(adj: dict[str, list[str]], u: str, v: str) -> None:
        if u in valid and v in valid and u != v and v not in adj[u]:
            adj[u].append(v)

    for sid in valid:
        sc = scopes[sid]
        raw_deps = list(sc.get("depends_on") if isinstance(sc.get("depends_on"), list) else ([sc["depends_on"]] if sc.get("depends_on") else []))
        ad = (sc.get("attributes") or {}).get("depends_on") if isinstance(sc.get("attributes"), dict) else None
        raw_deps.extend(ad if isinstance(ad, list) else (ad.replace(",", " ").split() if isinstance(ad, str) else []))
        for d in raw_deps:
            _add(fwd_dep, sid, str(d or "").strip())
        for r in (sc.get("rules") if isinstance(sc.get("rules"), list) else []):
            if isinstance(r, dict) and (
                "depends on" in str(r.get("text") or "").lower()
                or "depends_on" in str(r.get("text") or "").lower()
                or r.get("target_prop") == "depends_on"
            ):
                for ref_s in (r.get("refs") if isinstance(r.get("refs"), list) else []):
                    _add(fwd_dep, sid, str(ref_s or "").strip())
    for fl in (graph.get("flows") if isinstance(graph.get("flows"), list) else []):
        if isinstance(fl, dict):
            u, v = str(fl.get("source") or "").strip(), str(fl.get("target") or "").strip()
            _add(fwd_flow, u, v)
            _add(bwd_flow, v, u)
    for sid in valid:
        sc = scopes[sid]
        for nbr in [sc.get("parent"), *(sc.get("children") if isinstance(sc.get("children"), list) else [])]:
            ns = str(nbr or "").strip()
            if ns in valid and ns not in fwd_dep[sid] and sid not in fwd_dep.get(ns, []):
                for a, b in ((sid, ns), (ns, sid)):
                    _add(fwd_flow, a, b)
                    _add(bwd_flow, b, a)
        for r in (sc.get("rules") if isinstance(sc.get("rules"), list) else []):
            for rs in (str(x or "").strip() for x in (r.get("refs") if isinstance(r, dict) and isinstance(r.get("refs"), list) else [])):
                if rs in valid and rs not in fwd_dep[sid]:
                    _add(fwd_flow, sid, rs)
                    _add(bwd_flow, rs, sid)
        for es in (str(x or "").strip() for x in (sc.get("in_edges") if isinstance(sc.get("in_edges"), list) else [])):
            src_s = es.split(".", 1)[0] if "." in es else es
            if src_s in valid and sid not in fwd_dep.get(src_s, []):
                _add(fwd_flow, src_s, sid)
                _add(bwd_flow, sid, src_s)
    return fwd_flow, bwd_flow, fwd_dep

def _normalize_cues(concept_cues: list[str] | str) -> list[str]:
    if isinstance(concept_cues, str):
        s = concept_cues.strip()
        return list(dict.fromkeys([s, *(tokenize_content_words(s) if " " in s else [s])]))[:MAX_SEED_CUES] if s else []
    if isinstance(concept_cues, (list, tuple, set)):
        return list(dict.fromkeys([str(x or "").strip() for x in concept_cues if str(x or "").strip()]))[:MAX_SEED_CUES]
    raise ValueError(f"concept_cues must be str or sequence, got {type(concept_cues).__name__}")

def _seed_activation_state(graph: dict[str, Any], cues: list[str]) -> dict[str, _ActivationNode]:
    scopes = graph.get("scopes") if isinstance(graph.get("scopes"), dict) else {}
    cue_set, state = {c.lower() for c in cues}, {}
    for sid, sc in scopes.items():
        if sid != "global_defaults" and isinstance(sc, dict):
            aliases = {
                str(a).strip().lower() for a in (sc.get("aliases") if isinstance(sc.get("aliases"), list) else [])
                if str(a).strip()
            }
            if sid.lower() in cue_set or bool(cue_set & aliases):
                state[sid] = _ActivationNode(hop=0, activation=1.0)
    if state:
        return state
    seed_acts, seed_scopes = compute_seed_activations(graph, " ".join(cues))
    for sid, sc in scopes.items():
        if sid != "global_defaults" and isinstance(sc, dict):
            score = float(seed_acts.get(sid, 0.0))
            for r in (sc.get("rules") if isinstance(sc.get("rules"), list) else []):
                if isinstance(r, dict):
                    score = max(
                        score, float(seed_acts.get(str(r.get("id") or ""), 0.0)),
                        HOP1_DECAY if (cue_set & set(tokenize_content_words(str(r.get("text") or "")))) else 0.0
                    )
            if sid in seed_scopes or score >= SEED_SCORE_FLOOR:
                state[sid] = _ActivationNode(hop=0, activation=round(min(1.0, max(score, HOP2_DECAY)), 6))
    return state

def _run_directed_wave(
    seed_id: str, seed_energy: float, flow_adj: dict[str, list[str]],
    dep_adj: dict[str, list[str]], other_seeds: set[str],
    max_hops: int, decay_gamma: float,
    push_epsilon: float
) -> dict[str, _ActivationNode]:
    """Propagates a single seed wave along `flow_adj` and 1-hop `dep_adj`, gating each step with `can_propagate_from_node`."""
    state: dict[str, _ActivationNode] = {seed_id: _ActivationNode(hop=0, activation=seed_energy, reached_via_sink=False)}
    frontier: list[str] = [seed_id]
    for cur_hop in range(1, max_hops + 1):
        next_frontier: list[str] = []
        for u in frontier:
            u_node = state[u]
            if (u != seed_id and u in other_seeds) or not can_propagate_from_node(
                u_node.reached_via_sink, u_node.activation >= push_epsilon,
                cur_hop <= max_hops
            ):
                continue
            flow_nbrs = flow_adj.get(u, [])
            if flow_nbrs:
                step_e = round(u_node.activation * (decay_gamma / math.sqrt(max(1, len(flow_nbrs)))), 6)
                if can_propagate_from_node(u_node.reached_via_sink, step_e >= push_epsilon, cur_hop <= max_hops):
                    for v in flow_nbrs:
                        prev = state.get(v)
                        if prev is None or prev.reached_via_sink or (prev.hop == cur_hop and step_e > prev.activation):
                            state[v] = _ActivationNode(hop=cur_hop, activation=step_e, reached_via_sink=False)
                            if v not in next_frontier:
                                next_frontier.append(v)
            if cur_hop == 1 and dep_adj:
                dep_nbrs = [d for d in dep_adj.get(u, []) if d not in other_seeds and d not in flow_nbrs]
                if dep_nbrs:
                    step_d = round(u_node.activation * (decay_gamma / math.sqrt(max(1, len(dep_nbrs)))), 6)
                    if can_propagate_from_node(u_node.reached_via_sink, step_d >= push_epsilon, cur_hop <= max_hops):
                        for d in dep_nbrs:
                            if d not in state:
                                state[d] = _ActivationNode(hop=1, activation=step_d, reached_via_sink=True)
                                if d not in next_frontier:
                                    next_frontier.append(d)
        frontier = next_frontier
        if not frontier:
            break
    return state

def bounded_spreading_activation(
    store_or_graph: Any, seed_cues: list[str] | str,
    *, max_recall_hops: int = MAX_RECALL_HOPS,
    decay_gamma: float = DEFAULT_DECAY_GAMMA,
    bridge_beta: float = DEFAULT_BRIDGE_BETA,
    activation_tau: float = DEFAULT_ACTIVATION_TAU,
    push_epsilon: float = DEFAULT_PUSH_EPSILON
) -> list[dict[str, Any]]:
    """Runs directed multi-cue epsilon-push activation + Steiner bridge intersection (`max_recall_hops <= 6`)."""
    _req_int(max_recall_hops, "max_recall_hops", 0, MAX_RECALL_HOPS)
    _, graph = _resolve_store_and_graph(store_or_graph)
    cues = _normalize_cues(seed_cues)
    if not cues or not isinstance(graph.get("scopes"), dict):
        return []
    seeds = _seed_activation_state(graph, cues)
    if not seeds or max_recall_hops == 0:
        return [
            {"concept_id": s, "hop": 0, "hop_bucket": HopBucket.Hop0Seed.value, "activation": seeds[s].activation}
            for s in sorted(seeds, key=lambda k: (-seeds[k].activation, k))
        ]

    fwd_flow, bwd_flow, fwd_dep = _extract_directed_topology(graph)
    seed_ids, seed_set = sorted(seeds.keys()), set(seeds.keys())
    eff_wave_hops = max_recall_hops if len(seed_ids) >= 2 else min(max_recall_hops, MAX_RADIAL_HOPS)
    w_plus = {
        s: _run_directed_wave(
            s, seeds[s].activation, fwd_flow, fwd_dep,
            seed_set - {s}, eff_wave_hops, decay_gamma,
            push_epsilon
        )
        for s in seed_ids
    }
    w_minus = {
        s: _run_directed_wave(
            s, seeds[s].activation, bwd_flow, {},
            seed_set - {s}, eff_wave_hops, decay_gamma,
            push_epsilon
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
        vis = [n for s in seed_ids for n in (w_plus[s].get(v), w_minus[s].get(v)) if n is not None and n.hop > 0]
        if not vis:
            continue
        min_hop = min(n.hop for n in vis)
        base_v = max((n.activation for n in vis if n.hop <= radial_cap), default=0.0)
        bridge_sum, bridge_hops = 0.0, []
        for idx1, s1 in enumerate(seed_ids):
            for s2 in seed_ids[idx1 + 1:]:
                n1p, n2m, n2p, n1m = w_plus[s1].get(v), w_minus[s2].get(v), w_plus[s2].get(v), w_minus[s1].get(v)
                b1 = math.sqrt(n1p.activation * n2m.activation) if (
                    n1p and n2m and not n1p.reached_via_sink
                    and not n2m.reached_via_sink and n1p.hop + n2m.hop <= max_recall_hops
                ) else 0.0
                b2 = math.sqrt(n2p.activation * n1m.activation) if (
                    n2p and n1m and not n2p.reached_via_sink
                    and not n1m.reached_via_sink and n2p.hop + n1m.hop <= max_recall_hops
                ) else 0.0
                if b1 > 0.0 or b2 > 0.0:
                    bridge_sum += max(b1, b2)
                    bridge_hops.append((n1p.hop + n2m.hop) if b1 >= b2 else (n2p.hop + n1m.hop))
        is_bridge = bridge_sum > 0.0
        if is_bridge and base_v == 0.0:
            base_v = max(n.activation for n in vis)
        total_v = round(min(1.0, base_v + bridge_beta * bridge_sum), 6) if is_bridge else round(min(1.0, base_v), 6)
        eff_hop = min(min_hop, min(bridge_hops)) if is_bridge else min_hop
        is_only_sink = all(n.reached_via_sink for n in vis)
        if total_v >= activation_tau and (min_hop <= radial_cap or (is_bridge and eff_hop <= max_recall_hops)):
            results[v] = _ActivationNode(
                hop=eff_hop, activation=total_v, reached_via_sink=is_only_sink,
                is_bridge=is_bridge
            )

    ordered = sorted(results, key=lambda s: (0 if results[s].hop == 0 else (1 if results[s].is_bridge else 2), -results[s].activation, results[s].hop, s))
    return [
        {
            "concept_id": s, "hop": results[s].hop,
            "hop_bucket": classify_hop_bucket(
                results[s].hop, is_steiner_bridge=results[s].is_bridge,
                max_hops=max_recall_hops
            ).value,
            "activation": results[s].activation
        }
        for s in ordered
    ]

def _extract_scope_synopsis_and_links(
    store: SQLiteGraphStore | None, graph: dict[str, Any],
    concept_id: str, max_words: int
) -> tuple[str, list[dict[str, Any]]]:
    sc = (graph.get("scopes") or {}).get(concept_id, {})
    if not isinstance(sc, dict):
        return concept_id, []
    attrs = sc.get("attributes") if isinstance(sc.get("attributes"), dict) else {}
    rules = [r for r in (sc.get("rules") if isinstance(sc.get("rules"), list) else []) if isinstance(r, dict)]
    synopsis = _clamp_synopsis(
        str(
            attrs.get("lod1_synopsis") or (rules[0].get("text") if rules else "")
            or sc.get("role") or concept_id
        ).strip(),
        max_words
    )
    raw_links: list[dict[str, Any]] = []
    if store is not None:
        raw_links.extend(store.get_external_links(concept_id))
        for r in rules:
            if r.get("id"):
                raw_links.extend(store.get_external_links(str(r["id"])))
    top_links = graph.get("external_links")
    if isinstance(top_links, dict):
        for key in (concept_id, *(str(r.get("id") or "") for r in rules)):
            if key and isinstance(top_links.get(key), list):
                raw_links.extend(top_links[key])
    for r in rules:
        if isinstance(r.get("external_links"), list):
            raw_links.extend(r["external_links"])
    return synopsis, _normalize_external_links(concept_id, raw_links[:MAX_LINKS_PER_NODE])

def _build_concept_frame_record(
    cid: str, synopsis: str, links: list[dict[str, Any]],
    hop: int, hop_bucket: str, activation: float,
    cost: int
) -> ConceptFrame:
    return ConceptFrame({
        "concept_id": cid, "lod1_synopsis": synopsis,
        "external_links": links,
        "source_links": copy.deepcopy(links),
        "hop": hop, "hop_distance": hop,
        "hop_bucket": hop_bucket,
        "activation": activation,
        "token_cost": cost
    })  # type: ignore[misc]

def recall_concepts(
    store_or_graph: Any, concept_cues: list[str] | str,
    token_budget: int = MAX_LOD1_TOKEN_BUDGET,
    *, max_recall_hops: int = MAX_RECALL_HOPS,
    lod1_synopsis_words: int = MAX_LOD1_SYNOPSIS_WORDS,
    max_words: int | None = None, decay_gamma: float = DEFAULT_DECAY_GAMMA,
    bridge_beta: float = DEFAULT_BRIDGE_BETA,
    activation_tau: float = DEFAULT_ACTIVATION_TAU,
    push_epsilon: float = DEFAULT_PUSH_EPSILON
) -> list[ConceptFrame]:
    """Recalls `ConceptFrame` records within `max_recall_hops <= 6` and `token_budget <= 400` (Laws 1–2)."""
    eff_words = max_words if max_words is not None else lod1_synopsis_words
    _req_int(token_budget, "lod1_token_budget", 1, MAX_LOD1_TOKEN_BUDGET)
    _req_int(max_recall_hops, "max_recall_hops", 0, MAX_RECALL_HOPS)
    _req_int(eff_words, "lod1_synopsis_words", 1, MAX_LOD1_SYNOPSIS_WORDS)
    store, graph = _resolve_store_and_graph(store_or_graph)
    activated = bounded_spreading_activation(
        graph, concept_cues, max_recall_hops=max_recall_hops,
        decay_gamma=decay_gamma, bridge_beta=bridge_beta,
        activation_tau=activation_tau, push_epsilon=push_epsilon
    )
    frames: list[ConceptFrame] = []
    used_tokens = 0
    for item in activated:
        cid, hop, hop_bucket = item["concept_id"], int(item["hop"]), item["hop_bucket"]
        synopsis, links = _extract_scope_synopsis_and_links(store, graph, cid, eff_words)
        cost = max(1, token_cost(synopsis))
        if should_include_recalled_concept(
            hop_bucket, (used_tokens + cost <= token_budget)
            and (token_budget <= MAX_LOD1_TOKEN_BUDGET),
            (len(synopsis.split()) <= eff_words)
            and (eff_words <= MAX_LOD1_SYNOPSIS_WORDS)
        ):
            used_tokens += cost
            frames.append(_build_concept_frame_record(cid, synopsis, links, hop, hop_bucket, item["activation"], cost))
    return frames

def expand_concept_neighbors(
    graph_or_store: Any, concept_id: str,
    *, max_hops: int = 1, token_budget: int = MAX_RECALL_TOKEN_BUDGET,
    max_words: int = MAX_SYNOPSIS_WORDS,
    decay_gamma: float = DEFAULT_DECAY_GAMMA
) -> list[ConceptFrame]:
    """Stage 2 Deliberate Neighbor Expansion: expands `max_hops` from `concept_id` along flow, hierarchy, and 1-hop `depends_on` edges."""
    cid_clean = _req_str(concept_id, "concept_id")
    _req_int(max_hops, "max_hops", 1, MAX_RECALL_HOPS)
    _req_int(token_budget, "token_budget", 1, MAX_LOD1_TOKEN_BUDGET)
    _req_int(max_words, "max_words", 1, MAX_LOD1_SYNOPSIS_WORDS)
    store, graph = _resolve_store_and_graph(graph_or_store)
    scopes = graph.get("scopes") if isinstance(graph.get("scopes"), dict) else {}
    target = cid_clean if cid_clean in scopes else next((s for s in scopes if s.lower() == cid_clean.lower()), "")
    if not target or target == "global_defaults":
        return []

    fwd_flow, bwd_flow, fwd_dep = _extract_directed_topology(graph)
    bi_flow = {
        s: list(dict.fromkeys([*fwd_flow.get(s, []), *bwd_flow.get(s, [])]))
        for s in scopes if s != "global_defaults"
    }
    wave_state = _run_directed_wave(target, 1.0, bi_flow, fwd_dep, set(), max_hops, decay_gamma, DEFAULT_PUSH_EPSILON)

    frames: list[ConceptFrame] = []
    used_tokens = 0
    for cid in sorted(
        [s for s in wave_state if s != target],
        key=lambda s: (-wave_state[s].activation, wave_state[s].hop, s)
    ):
        node = wave_state[cid]
        hop_bucket = classify_hop_bucket(
            node.hop, is_steiner_bridge=(node.hop > MAX_RADIAL_HOPS),
            max_hops=max_hops
        ).value
        synopsis, links = _extract_scope_synopsis_and_links(store, graph, cid, max_words)
        cost = max(1, token_cost(synopsis))
        if should_include_recalled_concept(
            hop_bucket, (used_tokens + cost <= token_budget)
            and (token_budget <= MAX_LOD1_TOKEN_BUDGET),
            (len(synopsis.split()) <= max_words)
            and (max_words <= MAX_LOD1_SYNOPSIS_WORDS)
        ):
            used_tokens += cost
            frames.append(_build_concept_frame_record(cid, synopsis, links, node.hop, hop_bucket, node.activation, cost))
    return frames


class AIMemoryStore:
    """Hippocampal Concept & Episodic Index backed by `SQLiteGraphStore` (Spec 24 §2.2 & Stage 24B)."""

    def __init__(self, store_or_db_path: SQLiteGraphStore | str | Path = ":memory:") -> None:
        self.store = store_or_db_path if isinstance(store_or_db_path, SQLiteGraphStore) else SQLiteGraphStore(store_or_db_path)
        self.graph: dict[str, Any] = self.store.load_graph()

    def close(self) -> None:
        self.store.close()

    def upsert_concept_frame(
        self, frame_id: str, synopsis: str,
        *, category: str = "CONCEPT", aliases: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
        related_frames: list[str] | None = None,
        external_links: list[dict[str, Any]] | None = None,
        current_turn: int = 1
    ) -> dict[str, Any]:
        fid = _req_str(frame_id, "frame_id")
        if attributes:
            _validate_no_raw_documents({"attributes": attributes})
        index_committed_rules(
            self, [{
                "concept_id": fid, "id": f"{fid}.synopsis",
                "text": synopsis, "refs": list(related_frames or []),
                "external_links": list(external_links or [])
            }],
            current_turn=current_turn
        )
        sc = _ensure_concept_scope(self.graph, fid)
        sc["aliases"] = list(dict.fromkeys([*sc.get("aliases", []), *(aliases or [])]))
        sc["attributes"].update({"category": str(category or "CONCEPT"), **(attributes or {})})
        for rel_s in (str(r).strip() for r in (related_frames or []) if str(r).strip() and str(r).strip() != fid):
            rel_sc = _ensure_concept_scope(self.graph, rel_s)
            if f"{fid}.synopsis" not in rel_sc["in_edges"]:
                rel_sc["in_edges"].append(f"{fid}.synopsis")
        self.store.save_graph(self.graph)
        return sc

    def record_episode(
        self, episode_id: str, summary: str,
        *, related_frames: list[str] | None = None,
        turn: int = 1
    ) -> dict[str, Any]:
        eid, sc, clamped = _req_str(episode_id, "episode_id"), _ensure_concept_scope(self.graph, "episodes"), _clamp_synopsis(summary, MAX_LOD1_SYNOPSIS_WORDS)
        ep_node = {
            "id": eid, "scope": "episodes", "type": "FACT",
            "facet": "context_facts", "text": clamped,
            "target_prop": None, "bound_op": None,
            "bound_val": None, "conditions": [],
            "refs": [str(r).strip() for r in (related_frames or []) if str(r).strip()],
            "overrides": [], "exceptions": [],
            "meta_keys": [], "meta_tokens": tokenize_content_words(clamped),
            "created_turn": int(turn), "updated_turn": int(turn),
            "access_turns": [int(turn)]
        }
        sc["rules"] = [r for r in sc["rules"] if isinstance(r, dict) and r.get("id") != eid] + [ep_node]
        self.store.save_graph(self.graph)
        return ep_node

    def index_committed_rules(self, committed_rules: list[dict[str, Any]], *, current_turn: int = 1) -> list[str]:
        return index_committed_rules(self, committed_rules, current_turn=current_turn)

    def recall_concepts(
        self, concept_cues: list[str] | str,
        token_budget: int = MAX_LOD1_TOKEN_BUDGET,
        *, max_recall_hops: int = MAX_RECALL_HOPS,
        lod1_synopsis_words: int = MAX_LOD1_SYNOPSIS_WORDS,
        max_words: int | None = None, decay_gamma: float = DEFAULT_DECAY_GAMMA,
        bridge_beta: float = DEFAULT_BRIDGE_BETA,
        activation_tau: float = DEFAULT_ACTIVATION_TAU,
        push_epsilon: float = DEFAULT_PUSH_EPSILON
    ) -> list[ConceptFrame]:
        return recall_concepts(
            self, concept_cues, token_budget=token_budget,
            max_recall_hops=max_recall_hops, lod1_synopsis_words=lod1_synopsis_words,
            max_words=max_words, decay_gamma=decay_gamma,
            bridge_beta=bridge_beta, activation_tau=activation_tau,
            push_epsilon=push_epsilon
        )

    def expand_concept_neighbors(
        self, concept_id: str, *, max_hops: int = 1,
        token_budget: int = MAX_RECALL_TOKEN_BUDGET,
        max_words: int = MAX_SYNOPSIS_WORDS,
        decay_gamma: float = DEFAULT_DECAY_GAMMA
    ) -> list[ConceptFrame]:
        return expand_concept_neighbors(
            self, concept_id, max_hops=max_hops,
            token_budget=token_budget, max_words=max_words,
            decay_gamma=decay_gamma
        )

    def recall_into_working_memory(
        self, query: str, *, deep_detail_needed: bool = False,
        token_budget: int = MAX_LOD1_TOKEN_BUDGET,
        max_lines: int = 25, max_recall_hops: int = MAX_RECALL_HOPS,
        external_fetcher: Callable[[str], str] | None = None
    ) -> dict[str, Any]:
        _req_str(query, "query")
        _req_int(token_budget, "lod1_token_budget", 1, MAX_LOD1_TOKEN_BUDGET)
        _req_int(max_lines, "max_lines", 1)
        prop_res = propagate_nodes(
            self.graph, query, max_tokens=token_budget,
            max_lines=max_lines, max_hops=min(max_recall_hops, MAX_RADIAL_HOPS)
        )
        frames = self.recall_concepts(query, token_budget=token_budget, max_recall_hops=max_recall_hops)
        activated_ids = list(dict.fromkeys([*(f["concept_id"] for f in frames), *prop_res.get("active_scopes", [])]))
        lod_level, ext_map, deref_map = (LOD_2 if deep_detail_needed else LOD_1), {}, {}
        for cid in activated_ids:
            _, links = _extract_scope_synopsis_and_links(self.store, self.graph, cid, MAX_LOD1_SYNOPSIS_WORDS)
            if links:
                ext_map[cid] = links
                if should_dereference_external(lod_level, bool(links), deep_detail_needed) and external_fetcher is not None:
                    for lk in links:
                        deref_map[lk["uri"]] = _clamp_synopsis(external_fetcher(lk["uri"]), MAX_LOD1_SYNOPSIS_WORDS)
        return {
            "activated_frames": activated_ids, "synopses": frames
            or prop_res.get("active_rules", []),
            "lod_level": lod_level, "external_links": ext_map,
            "dereferenced_sources": deref_map,
            "text": str(prop_res.get("text", ""))
        }
