"""Stage 24B (`ai_memory`): Hippocampal Concept Index & Multi-Cue Steiner Bridge Recall."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from typing import Any

from bowthruster.ai_memory.schema import ConceptFrame, _pick
from bowthruster.graph_core import SQLiteGraphStore, token_cost, tokenize_content_words

MAX_RECALL_HOPS, MAX_LOD1_TOKEN_BUDGET, MAX_LOD1_SYNOPSIS_WORDS = 6, 400, 50
MAX_LINKS_PER_NODE, MAX_SEED_CUES = 8, 12
MAX_RECALL_TOKEN_BUDGET = MAX_LOD1_TOKEN_BUDGET
MAX_SYNOPSIS_WORDS, MAX_RADIAL_HOPS = MAX_LOD1_SYNOPSIS_WORDS, 2
DEFAULT_DECAY_GAMMA, DEFAULT_BRIDGE_BETA = 0.85, 0.85
DEFAULT_ACTIVATION_TAU, DEFAULT_PUSH_EPSILON = 0.15, 0.04
LOD_0, LOD_1, LOD_2 = "LOD0", "LOD1", "LOD2"
HOP1_DECAY, HOP2_DECAY, SEED_SCORE_FLOOR = 0.70, 0.50, 0.20


class HopBucket(str, Enum):
    Hop0Seed = "Hop0Seed"
    Hop1Neighbor = "Hop1Neighbor"
    Hop2Second = "Hop2Second"
    HopExceeds2 = "HopExceeds2"


@dataclass(frozen=True)
class _ActivationNode:
    hop: int
    activation: float
    reached_via_sink: bool = False
    is_bridge: bool = False


_HOP_BUCKET_MAP = {
    "Hop0Seed": HopBucket.Hop0Seed,
    "HOP_0_SEED": HopBucket.Hop0Seed,
    "0": HopBucket.Hop0Seed,
    "Hop1Neighbor": HopBucket.Hop1Neighbor,
    "HOP_1_NEIGHBOR": HopBucket.Hop1Neighbor,
    "1": HopBucket.Hop1Neighbor,
    "Hop2Second": HopBucket.Hop2Second,
    "HOP_2_SECOND": HopBucket.Hop2Second,
    "2": HopBucket.Hop2Second,
    "HopExceeds2": HopBucket.HopExceeds2,
    "HOP_EXCEEDS_2": HopBucket.HopExceeds2,
}


def _req_int(val: Any, name: str, lo: int, hi: int | None = None) -> int:
    bad = isinstance(val, bool) or not isinstance(val, int) or val < lo or (hi is not None and val > hi)
    if bad:
        bound = hi if hi is not None else "inf"
        raise ValueError(f"{name} must be an int in [{lo}, {bound}], got {val!r}")
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


def should_dereference_external(
    level: str,
    has_links: bool,
    deep_detail_needed: bool,
) -> bool:
    """Returns `True` iff `level == 'LOD2' and has_links and deep_detail_needed`."""
    return (
        str(level or "").strip().upper() == LOD_2
        and bool(has_links)
        and bool(deep_detail_needed)
    )


def classify_hop_bucket(
    hop_distance: int,
    *,
    is_steiner_bridge: bool = False,
    max_hops: int = MAX_RADIAL_HOPS,
) -> HopBucket:
    """Maps a non-negative integer hop distance to its canonical `HopBucket`."""
    _req_int(hop_distance, "hop_distance", 0)
    if hop_distance <= MAX_RADIAL_HOPS and hop_distance <= max_hops:
        return (HopBucket.Hop0Seed, HopBucket.Hop1Neighbor, HopBucket.Hop2Second)[hop_distance]
    if is_steiner_bridge and hop_distance <= max_hops:
        return HopBucket.Hop2Second
    return HopBucket.HopExceeds2


def should_include_recalled_concept(
    hop_bucket: HopBucket | str | int,
    budget_within_400: bool,
    words_within_50: bool,
) -> bool:
    """Pure recall gate governed by Bend 2 Law 1 (`recall_hop_bound_law`)."""
    if isinstance(hop_bucket, int) and not isinstance(hop_bucket, bool):
        bucket = classify_hop_bucket(hop_bucket)
    elif isinstance(hop_bucket, HopBucket):
        bucket = hop_bucket
    elif (key := str(hop_bucket or "").strip()) in _HOP_BUCKET_MAP:
        bucket = _HOP_BUCKET_MAP[key]
    else:
        raise ValueError(f"Unknown hop_bucket: {hop_bucket!r}")
    if bucket is HopBucket.HopExceeds2:
        return False
    return bool(budget_within_400) and bool(words_within_50)


def can_propagate_from_node(
    reached_via_depends_on_sink: bool,
    step_energy_above_epsilon: bool,
    within_max_hops: bool,
) -> bool:
    """Pure propagation gate governed by Bend 2 Law 2 (`steiner_bridge_and_sink_isolation_law`)."""
    if bool(reached_via_depends_on_sink):
        return False
    return bool(step_energy_above_epsilon) and bool(within_max_hops)


def _validate_no_raw_documents(entry: dict[str, Any]) -> None:
    for k in ("raw_document", "raw_documents"):
        nested = any(
            isinstance(entry.get(s), dict) and entry[s].get(k)
            for s in ("attributes", "metadata")
        )
        if entry.get(k) or nested:
            raise ValueError(f"pointer_only_storage_law violation: {k!r} forbidden in node_storage")


def _clamp_synopsis(text: str, max_words: int = MAX_LOD1_SYNOPSIS_WORDS) -> str:
    _req_int(max_words, "lod1_synopsis_words", 1, MAX_LOD1_SYNOPSIS_WORDS)
    return " ".join(str(text or "").strip().split()[:max_words])


def _normalize_external_links(
    concept_id: str,
    raw_links: list[Any] | None,
) -> list[dict[str, Any]]:
    if not raw_links:
        return []
    if not isinstance(raw_links, list) or len(raw_links) > MAX_LINKS_PER_NODE:
        raise ValueError(f"external_links must be a list of <= {MAX_LINKS_PER_NODE} items")
    out: list[dict[str, Any]] = []
    seen_uris: set[str] = set()
    for idx, item in enumerate(raw_links):
        if isinstance(item, str) and (uri := item.strip()) and uri not in seen_uris:
            seen_uris.add(uri)
            out.append(
                {
                    "link_id": f"link_{concept_id}_{idx + 1}",
                    "node_id": concept_id,
                    "uri": uri,
                    "target_uri": uri,
                    "kind": "reference",
                    "artifact_kind": "reference",
                    "lod_tier": LOD_2,
                    "summary": "",
                    "metadata": {},
                }
            )
        elif isinstance(item, dict):
            _validate_no_raw_documents(item)
            uri = str(_pick(item, "uri", "target_uri", default="")).strip()
            if uri and uri not in seen_uris:
                seen_uris.add(uri)
                kind = str(_pick(item, "kind", "artifact_kind", default="reference")).strip() or "reference"
                lod_tier = str(item.get("lod_tier") or LOD_2).strip() or LOD_2
                lid = str(item.get("link_id") or f"link_{concept_id}_{len(out) + 1}").strip()
                meta = copy.deepcopy(item["metadata"]) if isinstance(item.get("metadata"), dict) else {}
                out.append(
                    {
                        "link_id": lid,
                        "node_id": str(item.get("node_id") or concept_id).strip(),
                        "uri": uri,
                        "target_uri": uri,
                        "kind": kind,
                        "artifact_kind": kind,
                        "lod_tier": lod_tier,
                        "summary": _clamp_synopsis(str(item.get("summary") or ""), MAX_LOD1_SYNOPSIS_WORDS),
                        "metadata": meta,
                    }
                )
    return out


def _resolve_store_and_graph(
    store_or_graph: Any,
) -> tuple[SQLiteGraphStore | None, dict[str, Any]]:
    if (
        not isinstance(store_or_graph, dict)
        and hasattr(store_or_graph, "store")
        and hasattr(store_or_graph, "graph")
        and isinstance(store_or_graph.graph, dict)
    ):
        return store_or_graph.store, store_or_graph.graph
    if isinstance(store_or_graph, SQLiteGraphStore):
        return store_or_graph, store_or_graph.load_graph()
    if isinstance(store_or_graph, dict):
        store = (
            getattr(store_or_graph, "_sqlite_store", None)
            or getattr(store_or_graph, "store", None)
            or store_or_graph.get("_sqlite_store")
        )
        if store is not None and not isinstance(store, SQLiteGraphStore):
            raise ValueError(f"_sqlite_store must be SQLiteGraphStore, got {type(store).__name__}")
        return store, store_or_graph
    raise ValueError(
        f"Expected AIMemoryStore, SQLiteGraphStore, or graph dict, got {type(store_or_graph).__name__}"
    )


def _ensure_concept_scope(graph: dict[str, Any], concept_id: str) -> dict[str, Any]:
    scopes = graph.setdefault("scopes", {})
    sc = scopes.get(concept_id) if scopes.get(concept_id) else {}
    defaults = (
        ("id", concept_id),
        ("parent", None),
        ("aliases", []),
        ("children", []),
        ("rules", []),
        ("in_edges", []),
        ("attributes", {}),
        ("_meta", {}),
    )
    for k, d in defaults:
        sc.setdefault(k, copy.deepcopy(d))
    scopes[concept_id] = sc
    return sc


def index_committed_rules(
    store_or_graph: Any,
    committed_rules: list[dict[str, Any]],
    *,
    current_turn: int = 1,
    lod1_synopsis_words: int = MAX_LOD1_SYNOPSIS_WORDS,
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
        if (raw_syn := rule.get("lod1_synopsis")) is not None:
            synopsis = str(raw_syn).strip()
            if (w_cnt := len(synopsis.split())) > lod1_synopsis_words:
                raise ValueError(
                    f"lod1_synopsis word count ({w_cnt}) exceeds limit ({lod1_synopsis_words})"
                )
        else:
            raw_txt = str(_pick(rule, "canonical_text", "text", "summary", default="")).strip()
            synopsis = _clamp_synopsis(raw_txt, lod1_synopsis_words)
        raw_cid = str(
            _pick(rule, "concept_id", "primary_scope", "scope", "id", default=f"concept_{idx + 1}")
        )
        concept_id = _req_str(raw_cid, "concept_id")
        raw_refs = (
            rule.get("secondary_refs")
            if isinstance(rule.get("secondary_refs"), list)
            else rule.get("refs", [])
        )
        refs = [
            str(r).strip()
            for r in (raw_refs or [])
            if str(r).strip() and str(r).strip() != concept_id
        ]
        ext_in, src_in = rule.get("external_links"), rule.get("source_links")
        if isinstance(ext_in, list) and isinstance(src_in, list):
            if _normalize_external_links(concept_id, ext_in) != _normalize_external_links(concept_id, src_in):
                raise ValueError("r_uri_resolver_7 violation: source_links must equal external_links")
        norm_links = _normalize_external_links(
            concept_id, ext_in if "external_links" in rule else src_in
        )
        sc = _ensure_concept_scope(graph, concept_id)
        if synopsis:
            sc["attributes"]["lod1_synopsis"] = synopsis
        rule_id = str(rule.get("id") or f"{concept_id}.synopsis").strip()
        conds = list(rule["conditions"]) if isinstance(rule.get("conditions"), list) else []
        node_entry: dict[str, Any] = {
            "id": rule_id,
            "scope": concept_id,
            "type": str(rule.get("type") or "FACT"),
            "facet": str(rule.get("facet") or "context_facts"),
            "text": synopsis or concept_id,
            "target_prop": rule.get("target_prop"),
            "bound_op": rule.get("bound_op"),
            "bound_val": rule.get("bound_val"),
            "conditions": conds,
            "refs": refs,
            "overrides": [],
            "exceptions": [],
            "meta_keys": [],
            "meta_tokens": tokenize_content_words(synopsis or concept_id),
            "created_turn": int(current_turn),
            "updated_turn": int(current_turn),
            "access_turns": [int(current_turn)],
        }
        if norm_links:
            node_entry["external_links"] = copy.deepcopy(norm_links)
            ext_map = graph.setdefault("external_links", {})
            ext_map[rule_id] = copy.deepcopy(norm_links)
            ext_map[concept_id] = copy.deepcopy(norm_links)
        sc["rules"] = [r for r in sc["rules"] if r.get("id") != rule_id] + [node_entry]
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
                if isinstance(lk, dict) and (u := str(_pick(lk, "uri", "target_uri", default=""))):
                    store.upsert_external_link(
                        cid,
                        u,
                        _pick(lk, "kind", "artifact_kind", default="reference"),
                        _pick(lk, "summary", "lod_tier", default=""),
                        lk.get("metadata"),
                        link_id=lk.get("link_id"),
                    )
    if not isinstance(store_or_graph, dict) and hasattr(store_or_graph, "graph"):
        store_or_graph.graph = graph
    return indexed_ids


from .cue_extractor import _ActivationNode, can_propagate_from_node  # noqa: E402
from .spreading_recall import (  # noqa: E402
    _build_concept_frame_record,
    _extract_scope_synopsis_and_links,
    bounded_spreading_activation,
    expand_concept_neighbors,
)


def recall_concepts(
    store_or_graph: Any,
    concept_cues: list[str] | str,
    token_budget: int = MAX_LOD1_TOKEN_BUDGET,
    *,
    max_recall_hops: int = MAX_RECALL_HOPS,
    lod1_synopsis_words: int = MAX_LOD1_SYNOPSIS_WORDS,
    max_words: int | None = None,
    decay_gamma: float = DEFAULT_DECAY_GAMMA,
    bridge_beta: float = DEFAULT_BRIDGE_BETA,
    activation_tau: float = DEFAULT_ACTIVATION_TAU,
    push_epsilon: float = DEFAULT_PUSH_EPSILON,
) -> list[ConceptFrame]:
    """Recalls `ConceptFrame` records within `max_recall_hops <= 6` and `token_budget <= 400`."""
    eff_words = max_words if max_words is not None else lod1_synopsis_words
    _req_int(token_budget, "lod1_token_budget", 1, MAX_LOD1_TOKEN_BUDGET)
    _req_int(max_recall_hops, "max_recall_hops", 0, MAX_RECALL_HOPS)
    _req_int(eff_words, "lod1_synopsis_words", 1, MAX_LOD1_SYNOPSIS_WORDS)
    store, graph = _resolve_store_and_graph(store_or_graph)
    activated = bounded_spreading_activation(
        graph,
        concept_cues,
        max_recall_hops=max_recall_hops,
        decay_gamma=decay_gamma,
        bridge_beta=bridge_beta,
        activation_tau=activation_tau,
        push_epsilon=push_epsilon,
    )
    frames: list[ConceptFrame] = []
    used_tokens = 0
    for item in activated:
        cid, hop, hop_bucket = item["concept_id"], int(item["hop"]), item["hop_bucket"]
        synopsis, links = _extract_scope_synopsis_and_links(store, graph, cid, eff_words)
        cost = max(1, token_cost(synopsis))
        budget_ok = (used_tokens + cost <= token_budget) and (token_budget <= MAX_LOD1_TOKEN_BUDGET)
        words_ok = (len(synopsis.split()) <= eff_words) and (eff_words <= MAX_LOD1_SYNOPSIS_WORDS)
        if should_include_recalled_concept(hop_bucket, budget_ok, words_ok):
            used_tokens += cost
            frames.append(
                _build_concept_frame_record(
                    cid, synopsis, links, hop, hop_bucket, item["activation"], cost
                )
            )
    return frames


from .memory_store import AIMemoryStore  # noqa: E402
