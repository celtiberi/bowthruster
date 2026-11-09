"""Spec 24 (`ai_memory`): Hippocampal Concept Index, Cue Extractor, & External URI Resolver Package."""

from __future__ import annotations

import importlib
import importlib.util

from .hippocampal_index import (
    AIMemoryStore,
    DEFAULT_ACTIVATION_TAU,
    DEFAULT_BRIDGE_BETA,
    DEFAULT_DECAY_GAMMA,
    DEFAULT_PUSH_EPSILON,
    HopBucket,
    LOD_0,
    LOD_1,
    LOD_2,
    MAX_LINKS_PER_NODE,
    MAX_LOD1_SYNOPSIS_WORDS,
    MAX_LOD1_TOKEN_BUDGET,
    MAX_RECALL_HOPS,
    MAX_RECALL_TOKEN_BUDGET,
    MAX_SEED_CUES,
    MAX_SYNOPSIS_WORDS,
    bounded_spreading_activation,
    can_propagate_from_node,
    classify_hop_bucket,
    expand_concept_neighbors,
    index_committed_rules,
    recall_concepts,
    should_dereference_external,
    should_include_recalled_concept,
    step_lod_level,
)

__all__ = [
    "AIMemoryStore",
    "DEFAULT_ACTIVATION_TAU",
    "DEFAULT_BRIDGE_BETA",
    "DEFAULT_DECAY_GAMMA",
    "DEFAULT_PUSH_EPSILON",
    "HopBucket",
    "LOD_0",
    "LOD_1",
    "LOD_2",
    "MAX_LINKS_PER_NODE",
    "MAX_LOD1_SYNOPSIS_WORDS",
    "MAX_LOD1_TOKEN_BUDGET",
    "MAX_RECALL_HOPS",
    "MAX_RECALL_TOKEN_BUDGET",
    "MAX_SEED_CUES",
    "MAX_SYNOPSIS_WORDS",
    "bounded_spreading_activation",
    "can_propagate_from_node",
    "classify_hop_bucket",
    "expand_concept_neighbors",
    "index_committed_rules",
    "recall_concepts",
    "should_dereference_external",
    "should_include_recalled_concept",
    "step_lod_level",
]

for _submod in ("schema", "cue_extractor", "uri_resolver"):
    _full_name = f"{__name__}.{_submod}"
    if importlib.util.find_spec(_full_name) is not None:
        _mod = importlib.import_module(_full_name)
        for _attr in getattr(_mod, "__all__", [k for k in dir(_mod) if not k.startswith("_")]):
            if _attr not in globals():
                globals()[_attr] = getattr(_mod, _attr)
                __all__.append(_attr)
