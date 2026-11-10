"""SQLite-Backed Hippocampal Concept & Episodic Store (`ai_memory/memory_store.py`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from bowthruster.ai_memory.schema import ConceptFrame
from bowthruster.graph_core import (
    SQLiteGraphStore,
    propagate_nodes,
    tokenize_content_words,
)

from .hippocampal_index import (
    DEFAULT_ACTIVATION_TAU,
    DEFAULT_BRIDGE_BETA,
    DEFAULT_DECAY_GAMMA,
    DEFAULT_PUSH_EPSILON,
    LOD_1,
    LOD_2,
    MAX_LOD1_SYNOPSIS_WORDS,
    MAX_LOD1_TOKEN_BUDGET,
    MAX_RADIAL_HOPS,
    MAX_RECALL_HOPS,
    MAX_RECALL_TOKEN_BUDGET,
    MAX_SYNOPSIS_WORDS,
    _clamp_synopsis,
    _ensure_concept_scope,
    _extract_scope_synopsis_and_links,
    _req_int,
    _req_str,
    _validate_no_raw_documents,
    expand_concept_neighbors,
    index_committed_rules,
    recall_concepts,
    should_dereference_external,
)


class AIMemoryStore:
    """Hippocampal Concept & Episodic Index backed by `SQLiteGraphStore` (Spec 24 §2.2 & Stage 24B)."""

    def __init__(
        self,
        store_or_db_path: SQLiteGraphStore | str | Path = ":memory:",
    ) -> None:
        self.store = (
            store_or_db_path
            if isinstance(store_or_db_path, SQLiteGraphStore)
            else SQLiteGraphStore(store_or_db_path)
        )
        self.graph: dict[str, Any] = self.store.load_graph()

    def close(self) -> None:
        self.store.close()

    def upsert_concept_frame(
        self,
        frame_id: str,
        synopsis: str,
        *,
        category: str = "CONCEPT",
        aliases: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
        related_frames: list[str] | None = None,
        external_links: list[Any] | None = None,
        current_turn: int = 1,
    ) -> dict[str, Any]:
        fid = _req_str(frame_id, "frame_id")
        if attributes:
            _validate_no_raw_documents({"attributes": attributes})
        index_committed_rules(
            self,
            [
                {
                    "concept_id": fid,
                    "id": f"{fid}.synopsis",
                    "text": synopsis,
                    "refs": list(related_frames or []),
                    "external_links": list(external_links or []),
                }
            ],
            current_turn=int(current_turn),
        )
        sc = _ensure_concept_scope(self.graph, fid)
        sc["aliases"] = list(dict.fromkeys([*sc.get("aliases", []), *(aliases or [])]))
        sc["attributes"].update(
            {"category": str(category or "CONCEPT"), **(attributes or {})}
        )
        rel_ids = (
            str(r).strip()
            for r in (related_frames or [])
            if str(r).strip() and str(r).strip() != fid
        )
        for rel_s in rel_ids:
            rel_sc = _ensure_concept_scope(self.graph, rel_s)
            if f"{fid}.synopsis" not in rel_sc["in_edges"]:
                rel_sc["in_edges"].append(f"{fid}.synopsis")
        self.store.save_graph(self.graph)
        return sc

    def record_episode(
        self,
        episode_id: str,
        summary: str,
        *,
        related_frames: list[str] | None = None,
        turn: int = 1,
    ) -> dict[str, Any]:
        eid = _req_str(episode_id, "episode_id")
        sc = _ensure_concept_scope(self.graph, "episodes")
        clamped = _clamp_synopsis(summary, MAX_LOD1_SYNOPSIS_WORDS)
        ep_node = {
            "id": eid,
            "scope": "episodes",
            "type": "FACT",
            "facet": "context_facts",
            "text": clamped,
            "target_prop": None,
            "bound_op": None,
            "bound_val": None,
            "conditions": [],
            "refs": [str(r).strip() for r in (related_frames or []) if str(r).strip()],
            "overrides": [],
            "exceptions": [],
            "meta_keys": [],
            "meta_tokens": tokenize_content_words(clamped),
            "created_turn": int(turn),
            "updated_turn": int(turn),
            "access_turns": [int(turn)],
        }
        sc["rules"] = [r for r in sc["rules"] if r.get("id") != eid] + [ep_node]
        self.store.save_graph(self.graph)
        return ep_node

    def index_committed_rules(
        self,
        committed_rules: list[dict[str, Any]],
        *,
        current_turn: int = 1,
    ) -> list[str]:
        return index_committed_rules(self, committed_rules, current_turn=current_turn)

    def recall_concepts(
        self,
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
        return recall_concepts(
            self,
            concept_cues,
            token_budget=token_budget,
            max_recall_hops=max_recall_hops,
            lod1_synopsis_words=lod1_synopsis_words,
            max_words=max_words,
            decay_gamma=decay_gamma,
            bridge_beta=bridge_beta,
            activation_tau=activation_tau,
            push_epsilon=push_epsilon,
        )

    def expand_concept_neighbors(
        self,
        concept_id: str,
        *,
        max_hops: int = 1,
        token_budget: int = MAX_RECALL_TOKEN_BUDGET,
        max_words: int = MAX_SYNOPSIS_WORDS,
        decay_gamma: float = DEFAULT_DECAY_GAMMA,
    ) -> list[ConceptFrame]:
        return expand_concept_neighbors(
            self,
            concept_id,
            max_hops=max_hops,
            token_budget=token_budget,
            max_words=max_words,
            decay_gamma=decay_gamma,
        )

    def recall_into_working_memory(
        self,
        query: str,
        *,
        deep_detail_needed: bool = False,
        token_budget: int = MAX_LOD1_TOKEN_BUDGET,
        max_lines: int = 25,
        max_recall_hops: int = MAX_RECALL_HOPS,
        external_fetcher: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        _req_str(query, "query")
        _req_int(token_budget, "lod1_token_budget", 1, MAX_LOD1_TOKEN_BUDGET)
        _req_int(max_lines, "max_lines", 1)
        prop_res = propagate_nodes(
            self.graph,
            query,
            max_tokens=token_budget,
            max_lines=max_lines,
            max_hops=min(max_recall_hops, MAX_RADIAL_HOPS),
        )
        frames = self.recall_concepts(
            query,
            token_budget=token_budget,
            max_recall_hops=max_recall_hops,
        )
        activated_ids = list(
            dict.fromkeys(
                [*(f["concept_id"] for f in frames), *prop_res.get("active_scopes", [])]
            )
        )
        lod_level = LOD_2 if deep_detail_needed else LOD_1
        ext_map: dict[str, list[dict[str, Any]]] = {}
        deref_map: dict[str, str] = {}
        for cid in activated_ids:
            _, links = _extract_scope_synopsis_and_links(
                self.store, self.graph, cid, MAX_LOD1_SYNOPSIS_WORDS
            )
            if links:
                ext_map[cid] = links
                can_deref = should_dereference_external(
                    lod_level, bool(links), deep_detail_needed
                )
                if can_deref and external_fetcher:
                    for lk in links:
                        deref_map[lk["uri"]] = _clamp_synopsis(
                            external_fetcher(lk["uri"]), MAX_LOD1_SYNOPSIS_WORDS
                        )
        return {
            "activated_frames": activated_ids,
            "synopses": frames or prop_res.get("active_rules", []),
            "lod_level": lod_level,
            "external_links": ext_map,
            "dereferenced_sources": deref_map,
            "text": str(prop_res.get("text", "")),
        }
