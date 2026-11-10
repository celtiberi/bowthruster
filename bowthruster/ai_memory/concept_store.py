"""ConceptFrame SQLite Persistence & Link Graph Helpers (`ai_memory/concept_store.py`)."""

from __future__ import annotations

import copy
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore

from .schema import (
    MAX_LINKS_PER_NODE,
    ConceptFrame,
    ExternalLinkPointer,
    _pick,
    normalize_external_link,
    validate_concept_frame,
)


def upsert_concept_frame(
    store: SQLiteGraphStore | dict[str, Any],
    concept_id_or_frame: str | dict[str, Any] | ConceptFrame | None = None,
    lod1_synopsis: str | None = None,
    external_links: list[Any] | None = None,
    *,
    concept_id: str | None = None,
    synopsis: str | None = None,
    related_concepts: list[str] | None = None,
    related_frames: list[str] | None = None,
    aliases: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
    activation: float | None = None,
    hop_distance: int | None = None,
    updated_turn: int | None = None,
    current_turn: int | None = None,
    refs: list[str] | None = None,
    source_links: list[str] | None = None,
    external_uris: list[str] | None = None,
) -> ConceptFrame:
    """Validates and persists a `ConceptFrame` into `SQLiteGraphStore` (or graph dict)."""
    rels_in = related_concepts or related_frames
    turn_val = (
        updated_turn
        if updated_turn is not None
        else (current_turn if current_turn is not None else 1)
    )
    extra = {
        k: v
        for k, v in (
            ("refs", refs),
            ("source_links", source_links),
            ("external_uris", external_uris),
        )
        if v is not None
    }
    if isinstance(concept_id_or_frame, dict):
        cand = dict(concept_id_or_frame)
        if lod1_synopsis is not None or (synopsis is not None and "lod1_synopsis" not in cand):
            cand["lod1_synopsis"] = lod1_synopsis if lod1_synopsis is not None else synopsis
        if external_links is not None:
            cand["external_links"] = external_links
        if updated_turn is not None or current_turn is not None:
            cand["updated_turn"] = turn_val
        cand.update(extra)
    else:
        cid_arg = str(
            concept_id_or_frame if concept_id_or_frame is not None else (concept_id or "")
        ).strip()
        syn_arg = (
            lod1_synopsis
            if lod1_synopsis is not None
            else (synopsis if synopsis is not None else "")
        )
        cand = {
            "concept_id": cid_arg,
            "lod1_synopsis": syn_arg,
            "external_links": [] if external_links is None else external_links,
            "updated_turn": turn_val,
            **extra,
        }
    for k, v in (("activation", activation), ("hop_distance", hop_distance)):
        if v is not None:
            cand[k] = v
    validated = validate_concept_frame(cand)
    cid = validated.concept_id
    raw_rels = rels_in or _pick(validated, "related_concepts", "refs", default=[])
    rels = [
        str(r).strip()
        for r in raw_rels
        if isinstance(r, str) and str(r).strip() and str(r).strip() != cid
    ]
    is_sqlite = isinstance(store, SQLiteGraphStore)
    graph = store.load_graph() if is_sqlite else store
    if not isinstance(graph, dict):
        raise ValueError(f"store must be a SQLiteGraphStore or graph dict, got {type(store).__name__}")
    scopes = graph.setdefault("scopes", {})
    ex_sc = scopes.get(cid) if scopes.get(cid) else {}
    ex_rules = ex_sc.get("rules") or []
    prev_refs = [ref for r in ex_rules if r for ref in r.get("refs", []) if isinstance(ref, str)]
    merged_refs = list(dict.fromkeys([*prev_refs, *rels]))
    attrs = dict(ex_sc.get("attributes") or {}) | (
        copy.deepcopy(attributes) if isinstance(attributes, dict) else {}
    )
    attrs.update(
        role="concept_frame",
        lod1_synopsis=validated.lod1_synopsis,
        updated_turn=int(validated.updated_turn),
    )
    for k in (k for k in ("activation", "hop_distance") if k in validated):
        attrs[k] = validated[k]
    rule_id = f"{cid}.synopsis"
    created_t = int(ex_rules[0].get("created_turn", turn_val)) if ex_rules else int(turn_val)
    syn_rule = {
        "id": rule_id,
        "scope": cid,
        "type": "FACT",
        "facet": "context_facts",
        "text": validated.lod1_synopsis,
        "target_prop": cid,
        "bound_op": None,
        "bound_val": None,
        "conditions": [],
        "refs": merged_refs,
        "overrides": [],
        "exceptions": [],
        "meta_keys": [],
        "meta_tokens": [],
        "created_turn": created_t,
        "updated_turn": int(validated.updated_turn),
    }
    ex_al = ex_sc.get("aliases") or []
    scopes[cid] = {
        "id": cid,
        "parent": ex_sc.get("parent"),
        "aliases": list(dict.fromkeys([*ex_al, *(aliases or [])])),
        "children": list(ex_sc["children"]) if isinstance(ex_sc.get("children"), list) else [],
        "rules": [syn_rule, *[r for r in ex_rules if r.get("id") != rule_id]],
        "in_edges": list(ex_sc["in_edges"]) if isinstance(ex_sc.get("in_edges"), list) else [],
        "attributes": attrs,
        "_meta": dict(ex_sc.get("_meta") or {}),
    }
    if not isinstance(ext_map := graph.setdefault("external_links", {}), dict):
        ext_map = graph["external_links"] = {}
    ext_map[cid] = [
        {
            "link_id": p.link_id,
            "node_id": cid,
            "uri": p.target_uri,
            "kind": p.artifact_kind,
            "summary": p.lod_tier,
            "metadata": p.get("metadata"),
        }
        for p in validated.external_links
    ]
    for tgt_cid in merged_refs:
        tgt_sc = scopes.get(tgt_cid)
        if isinstance(tgt_sc, dict) and rule_id not in tgt_sc.setdefault("in_edges", []):
            tgt_sc["in_edges"].append(rule_id)
    if is_sqlite:
        store.save_graph(graph)
    return validated


def get_concept_frame(
    store: SQLiteGraphStore | dict[str, Any],
    concept_id: str,
) -> ConceptFrame | None:
    """Retrieves a `ConceptFrame` by `concept_id` from `SQLiteGraphStore` (or graph dict)."""
    if not isinstance(concept_id, str) or not concept_id.strip():
        return None
    cid, is_sqlite = concept_id.strip(), isinstance(store, SQLiteGraphStore)
    graph = store.load_graph() if is_sqlite else store
    scopes = graph.get("scopes") if isinstance(graph, dict) else None
    scope = scopes.get(cid) if isinstance(scopes, dict) else None
    if not isinstance(scope, dict):
        return None
    attrs = scope.get("attributes") or {}
    dict_rules = list(scope.get("rules") or [])
    synopsis = attrs.get("lod1_synopsis")
    if not isinstance(synopsis, str):
        synopsis = next(
            (r["text"].strip() for r in dict_rules if isinstance(r.get("text"), str) and r["text"].strip()),
            "",
        )
    ext_dict = graph.get("external_links") if isinstance(graph.get("external_links"), dict) else {}
    raw_links = (store.get_external_links(cid) if is_sqlite else ext_dict.get(cid, [])) or next(
        (
            r["external_links"]
            for r in dict_rules
            if isinstance(r.get("external_links"), list) and r["external_links"]
        ),
        [],
    )
    norm_links: list[ExternalLinkPointer] = []
    seen_uris: set[str] = set()
    for lk in raw_links:
        ptr = normalize_external_link(lk, concept_id=cid)
        if ptr.target_uri not in seen_uris and len(norm_links) < MAX_LINKS_PER_NODE:
            seen_uris.add(ptr.target_uri)
            norm_links.append(ptr)
    refs = list(dict.fromkeys(ref for r in dict_rules for ref in r.get("refs", []) if isinstance(ref, str)))
    default_turn = dict_rules[0].get("updated_turn", 1) if dict_rules else 1
    return ConceptFrame(
        cid,
        synopsis,
        norm_links,
        activation=attrs.get("activation"),
        hop_distance=attrs.get("hop_distance"),
        updated_turn=attrs.get("updated_turn", default_turn),
        **({"refs": refs} if refs else {}),
    )


def list_concept_frames(store: SQLiteGraphStore | dict[str, Any]) -> list[ConceptFrame]:
    """Lists all `ConceptFrame` records stored in `SQLiteGraphStore` (or graph dict)."""
    is_sqlite = isinstance(store, SQLiteGraphStore)
    graph = store.load_graph() if is_sqlite else store
    scopes = graph.get("scopes") if isinstance(graph, dict) else {}
    target = store if is_sqlite else graph
    return [
        f
        for cid in (scopes if isinstance(scopes, dict) else {})
        if (f := get_concept_frame(target, str(cid))) is not None
    ]


def link_concepts(
    store: SQLiteGraphStore | dict[str, Any],
    source_id: str,
    target_id: str,
    *,
    bidirectional: bool = True,
    turn: int = 1,
) -> dict[str, Any]:
    """Links `source_id` and `target_id` in `SQLiteGraphStore` (or graph dict)."""
    valid_src = isinstance(source_id, str) and source_id.strip()
    valid_tgt = isinstance(target_id, str) and target_id.strip()
    if not (valid_src and valid_tgt):
        raise ValueError("source_id and target_id must be non-empty strings")
    src, tgt = source_id.strip(), target_id.strip()
    if src == tgt:
        return {"source": src, "target": tgt, "bidirectional": bidirectional}
    is_sqlite = isinstance(store, SQLiteGraphStore)
    graph = store.load_graph() if is_sqlite else store
    scopes = graph.setdefault("scopes", {})
    for cid in (c for c in (src, tgt) if not isinstance(scopes.get(c), dict)):
        upsert_concept_frame(graph, cid, "", [], updated_turn=turn)
    pairs = [(src, tgt), (tgt, src)] if bidirectional else [(src, tgt)]
    for u, v in pairs:
        if not (u_rules := scopes[u].setdefault("rules", [])):
            upsert_concept_frame(graph, u, "", [], updated_turn=turn)
            u_rules = scopes[u]["rules"]
        refs = u_rules[0].setdefault("refs", [])
        in_edges = scopes[v].setdefault("in_edges", [])
        rid = str(u_rules[0]["id"])
        if v not in refs:
            refs.append(v)
        if rid not in in_edges:
            in_edges.append(rid)
    if is_sqlite:
        store.save_graph(graph)
    return {"source": src, "target": tgt, "bidirectional": bidirectional}


_SQLITE_CONCEPT_METHODS = (
    ("upsert_concept_frame", upsert_concept_frame),
    ("get_concept_frame", get_concept_frame),
    ("list_concept_frames", list_concept_frames),
    ("link_concepts", link_concepts),
)
for _m, _f in _SQLITE_CONCEPT_METHODS:
    if not hasattr(SQLiteGraphStore, _m):
        setattr(SQLiteGraphStore, _m, _f)
