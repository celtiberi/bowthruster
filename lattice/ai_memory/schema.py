"""Spec 24A — `ai_memory.schema`: ConceptFrame & ExternalLinkPointer Schemas, Pointer-Only Storage Law, and SQLite Graph Persistence (`core/circuit/ai_memory/schema.py`)."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from lattice.graph_core import SQLiteGraphStore

MAX_SEED_CUES, MAX_LINKS_PER_NODE, MAX_SYNOPSIS_WORDS = 12, 8, 50
DEFAULT_LOD1_TOKEN_BUDGET, MAX_LOD1_TOKEN_BUDGET, MAX_RECALL_HOPS = 250, 400, 2
LOD_0, LOD_1, LOD_2, LOD_3 = "LOD0", "LOD1", "LOD2", "LOD3"
_FORBIDDEN_RAW_DOC_KEYS: frozenset[str] = frozenset({
    "raw_document", "raw_documents", "raw_text", "document_body", "full_text", "raw_payload", "raw_content",
})


def is_valid_concept_storage(has_raw_document: bool, words_within_50: bool, links_within_8: bool) -> bool:
    """Bend 2 Law 1 (`pointer_only_storage_law`): returns `True` iff `not has_raw_document and words_within_50 and links_within_8`."""
    return (not bool(has_raw_document)) and bool(words_within_50) and bool(links_within_8)


class ExternalLinkPointer(dict):
    """Canonical `uri_resolver` external artifact pointer (`link_id`, `target_uri`, `artifact_kind`, `lod_tier`)."""

    def __init__(
        self, link_id: str | dict[str, Any] | None = None, target_uri: str = "",
        artifact_kind: str = "doc", lod_tier: str = LOD_1, *, concept_id: str = "", **extra: Any,
    ) -> None:
        super().__init__()
        src = dict(link_id) if isinstance(link_id, dict) else extra
        meta = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
        raw_uri = (src.get("target_uri") or meta.get("target_uri") or src.get("uri") or target_uri) if isinstance(link_id, dict) else (target_uri or src.pop("uri", ""))
        raw_kind = (src.get("artifact_kind") or meta.get("artifact_kind") or src.get("kind") or artifact_kind) if isinstance(link_id, dict) else (src.pop("kind", artifact_kind) if artifact_kind == "doc" and "kind" in src else artifact_kind)
        raw_tier = (src.get("lod_tier") or meta.get("lod_tier") or src.get("summary") or lod_tier) if isinstance(link_id, dict) else lod_tier
        raw_lid = src.get("link_id") if isinstance(link_id, dict) else link_id
        cid = str((src.get("concept_id") or src.get("node_id") or concept_id) if isinstance(link_id, dict) else (concept_id or src.pop("node_id", "")) or "").strip()
        for fk in (*_FORBIDDEN_RAW_DOC_KEYS, "source_links", "external_uris"):
            if fk in src and src[fk] is not None:
                self[fk] = src[fk]
        uri_clean, kind_clean = str(raw_uri or "").strip(), str(raw_kind or "doc").strip() or "doc"
        tier_clean = str(raw_tier or LOD_1).strip() or LOD_1
        if tier_clean not in (LOD_0, LOD_1, LOD_2, LOD_3, "L0", "L1", "L2", "L3"):
            tier_clean = LOD_1
        digest = hashlib.sha256(f"{cid}|{uri_clean}|{kind_clean}".encode("utf-8")).hexdigest()[:12]
        lid_clean = raw_lid.strip() if isinstance(raw_lid, str) and raw_lid.strip() else (f"link_{cid}_{digest}" if cid else f"link_{digest}")
        self.update(link_id=lid_clean, target_uri=uri_clean, artifact_kind=kind_clean, lod_tier=tier_clean)

    def __getattr__(self, name: str) -> Any:
        if name in ("link_id", "target_uri", "artifact_kind", "lod_tier", "uri", "kind", "source_links", "external_uris"):
            return self.get(name)
        raise AttributeError(name)

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return super().get(key, default)
        if key in ("uri", "kind", "summary"):
            return super().get({"uri": "target_uri", "kind": "artifact_kind", "summary": "lod_tier"}[key], default)
        if key == "metadata":
            return {"lod_tier": super().get("lod_tier", LOD_1), "artifact_kind": super().get("artifact_kind", "doc"), "target_uri": super().get("target_uri", "")}
        if key in ("source_links", "external_uris"):
            u = super().get("target_uri", "")
            return [str(u)] if u else []
        return default

    def __getitem__(self, key: Any) -> Any:
        if key in self or key in ("uri", "kind", "summary", "metadata", "source_links", "external_uris"):
            return self.get(key)
        raise KeyError(key)

    def to_dict(self) -> dict[str, Any]:
        return {"link_id": self.link_id, "target_uri": self.target_uri, "artifact_kind": self.artifact_kind, "lod_tier": self.lod_tier}


def normalize_external_link(link: Any, *, concept_id: str = "") -> ExternalLinkPointer:
    """Normalizes a link dict, URI string, or `ExternalLinkPointer` into a validated `ExternalLinkPointer`."""
    if isinstance(link, dict):
        ptr = ExternalLinkPointer(dict(link), concept_id=concept_id)
    elif isinstance(link, str) and link.strip():
        ptr = ExternalLinkPointer(None, link.strip(), "doc", LOD_1, concept_id=concept_id)
    else:
        raise ValueError(f"Invalid external link record: {link!r}")
    if not ptr.target_uri:
        raise ValueError(f"ExternalLinkPointer target_uri must be non-empty, got {link!r}")
    if any(fk in ptr and ptr[fk] is not None for fk in _FORBIDDEN_RAW_DOC_KEYS):
        raise ValueError("pointer_only_storage_law violated: forbidden raw document field in external link")
    if ("source_links" in ptr or "external_uris" in ptr) and list(ptr.get("source_links")) != list(ptr.get("external_uris")):
        raise ValueError("uri_resolver obligation violated: source_links != external_uris")
    return ptr


class ConceptFrame(dict):
    """Canonical `ai_memory` ConceptFrame schema (`concept_id`, `lod1_synopsis`, `external_links`, plus optional recall fields)."""

    def __init__(
        self, concept_id: str | dict[str, Any] = "", lod1_synopsis: str = "", external_links: list[Any] | None = None,
        *, activation: float | None = None, hop_distance: int | None = None, updated_turn: int | None = None, **extra: Any,
    ) -> None:
        super().__init__()
        if isinstance(concept_id, dict):
            src = dict(concept_id)
            cid = str(src.get("concept_id") or src.get("id") or "").strip()
            syn = src.get("lod1_synopsis") if "lod1_synopsis" in src else src.get("synopsis", "")
            raw_links = src.get("external_links", [] if external_links is None else external_links)
            act, hop, turn = src.get("activation", activation), src.get("hop_distance", hop_distance), src.get("updated_turn", updated_turn)
            extra_fields = {k: v for k, v in src.items() if k not in ("concept_id", "id", "lod1_synopsis", "synopsis", "external_links", "activation", "hop_distance", "updated_turn")} | extra
        else:
            cid = str(concept_id or "").strip()
            syn = lod1_synopsis if lod1_synopsis or "synopsis" not in extra else extra.pop("synopsis", "")
            raw_links, act, hop, turn, extra_fields = ([] if external_links is None else external_links), activation, hop_distance, updated_turn, dict(extra)
        self["concept_id"] = cid
        self["lod1_synopsis"] = " ".join(str(syn).split()) if isinstance(syn, str) else syn
        self["external_links"] = [
            ExternalLinkPointer(lk, concept_id=cid) if isinstance(lk, dict) else (ExternalLinkPointer(None, lk, concept_id=cid) if isinstance(lk, str) else lk)
            for lk in raw_links
        ] if isinstance(raw_links, list) else raw_links
        if act is not None:
            self["activation"] = float(act)
        if hop is not None:
            self["hop_distance"] = int(hop)
        if turn is not None:
            self["updated_turn"] = int(turn)
        self.update(extra_fields)

    def __getattr__(self, name: str) -> Any:
        if name in ("concept_id", "lod1_synopsis", "external_links", "source_links", "external_uris"):
            return self.get(name)
        if name == "activation":
            return float(super().get("activation", 0.0) or 0.0)
        if name == "hop_distance":
            return int(super().get("hop_distance", 0) or 0)
        if name == "updated_turn":
            return int(super().get("updated_turn", 1) or 1)
        raise AttributeError(name)

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return super().get(key, default)
        if key in ("source_links", "external_uris"):
            links = super().get("external_links", [])
            return [str(lk.get("target_uri") or lk.get("uri")) for lk in (links if isinstance(links, list) else []) if isinstance(lk, dict) and (lk.get("target_uri") or lk.get("uri"))]
        return default

    def __getitem__(self, key: Any) -> Any:
        if key in self or key in ("source_links", "external_uris"):
            return self.get(key)
        raise KeyError(key)

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


def validate_concept_frame(frame: dict[str, Any] | ConceptFrame) -> ConceptFrame:
    """Enforces Bend 2 Law 1 (`pointer_only_storage_law`) and schema bounds on `frame`."""
    if not isinstance(frame, dict):
        raise ValueError(f"ConceptFrame must be a dict or ConceptFrame, got {type(frame).__name__}")
    cid, synopsis, raw_links = frame.get("concept_id"), frame.get("lod1_synopsis"), frame.get("external_links", [])
    if not isinstance(cid, str) or not cid.strip() or not isinstance(synopsis, str) or not isinstance(raw_links, list):
        raise ValueError("ConceptFrame requires non-empty concept_id: str, lod1_synopsis: str, and external_links: list")
    cid_clean, synopsis_words = cid.strip(), synopsis.split()
    has_raw_doc = any(k in frame and frame[k] is not None for k in _FORBIDDEN_RAW_DOC_KEYS) or any(
        isinstance(lk, dict) and any(k in lk and lk[k] is not None for k in _FORBIDDEN_RAW_DOC_KEYS) for lk in raw_links
    )
    if not is_valid_concept_storage(has_raw_doc, len(synopsis_words) <= MAX_SYNOPSIS_WORDS, len(raw_links) <= MAX_LINKS_PER_NODE):
        raise ValueError(f"pointer_only_storage_law violated on {cid_clean!r} (raw={has_raw_doc}, words={len(synopsis_words)}, links={len(raw_links)})")
    norm_links: list[ExternalLinkPointer] = []
    seen_uris: set[str] = set()
    for lk in raw_links:
        ptr = normalize_external_link(lk, concept_id=cid_clean)
        if ptr.target_uri not in seen_uris:
            seen_uris.add(ptr.target_uri)
            norm_links.append(ptr)
    expected_uris = [p.target_uri for p in norm_links]
    if ("source_links" in frame and "external_uris" in frame and list(frame["source_links"]) != list(frame["external_uris"])) or (
        "source_links" in frame and list(frame["source_links"]) != expected_uris
    ) or ("external_uris" in frame and list(frame["external_uris"]) != expected_uris):
        raise ValueError(f"uri_resolver obligation violated on {cid_clean!r}: source_links/external_uris mismatch")
    hop_val = frame.get("hop_distance")
    if hop_val is not None and (not isinstance(hop_val, int) or isinstance(hop_val, bool) or hop_val < 0):
        raise ValueError(f"ConceptFrame.hop_distance must be a non-negative int, got {hop_val!r}")
    extra_clean = {k: copy.deepcopy(v) for k, v in frame.items() if k not in ("concept_id", "lod1_synopsis", "external_links", "activation", "hop_distance", "updated_turn", "source_links", "external_uris")}
    return ConceptFrame(cid_clean, " ".join(synopsis_words), norm_links, activation=frame.get("activation"), hop_distance=hop_val, updated_turn=frame.get("updated_turn"), **extra_clean)


def upsert_concept_frame(
    store: SQLiteGraphStore | dict[str, Any], concept_id_or_frame: str | dict[str, Any] | ConceptFrame | None = None,
    lod1_synopsis: str | None = None, external_links: list[Any] | None = None, *, concept_id: str | None = None,
    synopsis: str | None = None, related_concepts: list[str] | None = None, related_frames: list[str] | None = None,
    aliases: list[str] | None = None, attributes: dict[str, Any] | None = None, activation: float | None = None,
    hop_distance: int | None = None, updated_turn: int | None = None, current_turn: int | None = None, **extra: Any,
) -> ConceptFrame:
    """Validates and persists a `ConceptFrame` into `SQLiteGraphStore` (or an in-memory graph dict)."""
    turn_val = updated_turn if updated_turn is not None else (current_turn if current_turn is not None else 1)
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
        cid_arg = str(concept_id_or_frame if concept_id_or_frame is not None else (concept_id or "")).strip()
        syn_arg = lod1_synopsis if lod1_synopsis is not None else (synopsis if synopsis is not None else "")
        cand = {"concept_id": cid_arg, "lod1_synopsis": syn_arg, "external_links": [] if external_links is None else external_links, "updated_turn": turn_val, **extra}
    if activation is not None:
        cand["activation"] = activation
    if hop_distance is not None:
        cand["hop_distance"] = hop_distance
    validated = validate_concept_frame(cand)
    cid = validated.concept_id
    rels = [str(r).strip() for r in (related_concepts or related_frames or validated.get("related_concepts") or validated.get("refs") or []) if isinstance(r, str) and str(r).strip() and str(r).strip() != cid]
    is_sqlite = isinstance(store, SQLiteGraphStore)
    graph = store.load_graph() if is_sqlite else store
    if not isinstance(graph, dict):
        raise ValueError(f"store must be a SQLiteGraphStore or graph dict, got {type(store).__name__}")
    scopes = graph.setdefault("scopes", {})
    ex_scope = scopes.get(cid) if isinstance(scopes.get(cid), dict) else {}
    ex_rules = ex_scope.get("rules", []) if isinstance(ex_scope.get("rules"), list) else []
    merged_refs = list(dict.fromkeys([*(ref for r in ex_rules if isinstance(r, dict) for ref in r.get("refs", []) if isinstance(ref, str)), *rels]))
    attrs = (dict(ex_scope.get("attributes", {})) if isinstance(ex_scope.get("attributes"), dict) else {}) | (copy.deepcopy(attributes) if isinstance(attributes, dict) else {})
    attrs.update(role="concept_frame", lod1_synopsis=validated.lod1_synopsis, updated_turn=int(validated.updated_turn))
    for k in ("activation", "hop_distance"):
        if k in validated:
            attrs[k] = validated[k]
    rule_id = f"{cid}.synopsis"
    synopsis_rule = {
        "id": rule_id, "scope": cid, "type": "FACT", "facet": "context_facts", "text": validated.lod1_synopsis,
        "target_prop": cid, "bound_op": None, "bound_val": None, "conditions": [], "refs": merged_refs,
        "overrides": [], "exceptions": [], "meta_keys": [], "meta_tokens": [],
        "created_turn": int(ex_rules[0].get("created_turn", turn_val)) if ex_rules else int(turn_val), "updated_turn": int(validated.updated_turn),
    }
    scopes[cid] = {
        "id": cid, "parent": ex_scope.get("parent"),
        "aliases": list(dict.fromkeys([*(ex_scope.get("aliases", []) if isinstance(ex_scope.get("aliases"), list) else []), *(aliases or [])])),
        "children": list(ex_scope.get("children", [])) if isinstance(ex_scope.get("children"), list) else [],
        "rules": [synopsis_rule, *[r for r in ex_rules if isinstance(r, dict) and r.get("id") != rule_id]],
        "in_edges": list(ex_scope.get("in_edges", [])) if isinstance(ex_scope.get("in_edges"), list) else [],
        "attributes": attrs, "_meta": dict(ex_scope.get("_meta", {})) if isinstance(ex_scope.get("_meta"), dict) else {},
    }
    ext_map = graph.setdefault("external_links", {})
    if not isinstance(ext_map, dict):
        ext_map = graph["external_links"] = {}
    ext_map[cid] = [{"link_id": p.link_id, "node_id": cid, "uri": p.target_uri, "kind": p.artifact_kind, "summary": p.lod_tier, "metadata": p.get("metadata")} for p in validated.external_links]
    for tgt_cid in merged_refs:
        if tgt_cid in scopes and isinstance(scopes[tgt_cid], dict) and rule_id not in scopes[tgt_cid].setdefault("in_edges", []):
            scopes[tgt_cid]["in_edges"].append(rule_id)
    if is_sqlite:
        store.save_graph(graph)
    return validated


def get_concept_frame(store: SQLiteGraphStore | dict[str, Any], concept_id: str) -> ConceptFrame | None:
    """Retrieves a `ConceptFrame` by `concept_id` from `SQLiteGraphStore` (or an in-memory graph dict)."""
    if not isinstance(concept_id, str) or not concept_id.strip():
        return None
    cid, is_sqlite = concept_id.strip(), isinstance(store, SQLiteGraphStore)
    graph = store.load_graph() if is_sqlite else store
    scope = graph.get("scopes", {}).get(cid) if isinstance(graph, dict) and isinstance(graph.get("scopes"), dict) else None
    if not isinstance(scope, dict):
        return None
    attrs, rules = (scope.get("attributes") or {}), (scope.get("rules") or [])
    synopsis = attrs.get("lod1_synopsis")
    if not isinstance(synopsis, str):
        synopsis = next((r["text"].strip() for r in rules if isinstance(r, dict) and isinstance(r.get("text"), str) and r["text"].strip()), "")
    raw_links = store.get_external_links(cid) if is_sqlite else ((graph.get("external_links") or {}).get(cid, []) if isinstance(graph.get("external_links"), dict) else [])
    if not raw_links:
        raw_links = next((r["external_links"] for r in rules if isinstance(r, dict) and isinstance(r.get("external_links"), list) and r["external_links"]), [])
    norm_links: list[ExternalLinkPointer] = []
    seen_uris: set[str] = set()
    for lk in raw_links:
        ptr = normalize_external_link(lk, concept_id=cid)
        if ptr.target_uri not in seen_uris and len(norm_links) < MAX_LINKS_PER_NODE:
            seen_uris.add(ptr.target_uri)
            norm_links.append(ptr)
    refs = list(dict.fromkeys(ref for r in rules if isinstance(r, dict) for ref in r.get("refs", []) if isinstance(ref, str)))
    updated_turn = attrs.get("updated_turn", rules[0].get("updated_turn", 1) if rules and isinstance(rules[0], dict) else 1)
    return ConceptFrame(cid, synopsis, norm_links, activation=attrs.get("activation"), hop_distance=attrs.get("hop_distance"), updated_turn=updated_turn, **({"refs": refs} if refs else {}))


def list_concept_frames(store: SQLiteGraphStore | dict[str, Any]) -> list[ConceptFrame]:
    """Lists all `ConceptFrame` records stored in `SQLiteGraphStore` (or an in-memory graph dict)."""
    is_sqlite = isinstance(store, SQLiteGraphStore)
    graph = store.load_graph() if is_sqlite else store
    scopes = graph.get("scopes", {}) if isinstance(graph, dict) else {}
    return [f for cid in (scopes if isinstance(scopes, dict) else {}) if (f := get_concept_frame(store if is_sqlite else graph, str(cid))) is not None]


def link_concepts(store: SQLiteGraphStore | dict[str, Any], source_id: str, target_id: str, *, bidirectional: bool = True, turn: int = 1) -> dict[str, Any]:
    """Links `source_id` and `target_id` in `SQLiteGraphStore` (or graph dict) for associative spreading activation."""
    if not isinstance(source_id, str) or not source_id.strip() or not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("source_id and target_id must be non-empty strings")
    src, tgt = source_id.strip(), target_id.strip()
    if src == tgt:
        return {"source": src, "target": tgt, "bidirectional": bidirectional}
    is_sqlite = isinstance(store, SQLiteGraphStore)
    graph = store.load_graph() if is_sqlite else store
    scopes = graph.setdefault("scopes", {})
    for cid in (src, tgt):
        if cid not in scopes or not isinstance(scopes[cid], dict):
            upsert_concept_frame(graph, cid, "", [], updated_turn=turn)
    for u, v in ([(src, tgt), (tgt, src)] if bidirectional else [(src, tgt)]):
        u_rules = scopes[u].setdefault("rules", [])
        if not u_rules:
            upsert_concept_frame(graph, u, "", [], updated_turn=turn)
            u_rules = scopes[u]["rules"]
        refs, in_edges, rid = u_rules[0].setdefault("refs", []), scopes[v].setdefault("in_edges", []), str(u_rules[0]["id"])
        if v not in refs:
            refs.append(v)
        if rid not in in_edges:
            in_edges.append(rid)
    if is_sqlite:
        store.save_graph(graph)
    return {"source": src, "target": tgt, "bidirectional": bidirectional}


def split_uri_parts(raw_uri: str) -> tuple[str, str, str]:
    """Splits a canonical or bare URI into `(scheme_prefix, path, anchor)`."""
    s = str(raw_uri or "").strip()
    base, anchor = s.split("#", 1) if "#" in s else (s, "")
    for prefix in ("uri://file/", "file:///", "file://"):
        if base.startswith(prefix):
            return prefix, base[len(prefix) :], anchor
    return (base[: (idx := base.find("://") + 3)], base[idx:], anchor) if "://" in base else ("", base, anchor)


def norm_file_path(path_or_uri: str) -> str:
    """Normalizes a relative/absolute path or URI path for structural comparison."""
    _, path, _ = split_uri_parts(path_or_uri)
    while path.startswith("./") or path.startswith("/"):
        path = path[2:] if path.startswith("./") else path[1:]
    return path.strip()


def paths_match(uri_path: str, manifest_path: str) -> bool:
    """Returns True when `uri_path` and `manifest_path` refer to the same file path."""
    np1, np2 = norm_file_path(uri_path), norm_file_path(manifest_path)
    return bool(np1 and np2 and (np1 == np2 or np1.endswith("/" + np2) or np2.endswith("/" + np1)))


def replace_renamed_path(orig_path: str, old_manifest_path: str, new_manifest_path: str) -> str:
    """Rewrites `orig_path` from `old_manifest_path` to `new_manifest_path` preserving relative prefixes."""
    if not new_manifest_path.strip():
        return orig_path
    leading = "/" if orig_path.startswith("/") else ("./" if orig_path.startswith("./") else "")
    np_orig, np_old, np_new = norm_file_path(orig_path), norm_file_path(old_manifest_path), norm_file_path(new_manifest_path)
    if np_orig == np_old:
        return leading + np_new
    if np_orig.endswith("/" + np_old):
        prefix = np_orig[: len(np_orig) - len(np_old)]
        same_root = "/" not in np_old or np_old.split("/", 1)[0] == np_new.split("/", 1)[0]
        return leading + (prefix + np_new if (same_root and not np_new.startswith(prefix)) else np_new)
    if np_old.endswith("/" + np_orig):
        prefix = np_old[: len(np_old) - len(np_orig)]
        return leading + (np_new[len(prefix) :] if np_new.startswith(prefix) else np_new)
    return orig_path


def _scan_container_paths(c: dict[str, Any], local_hashes: dict[str, str], out: list[tuple[str, str | None]]) -> None:
    if isinstance(fh := c.get("file_hashes"), dict):
        for fp, hval in fh.items():
            if isinstance(fp, str) and (p := split_uri_parts(fp)[1]):
                h_str = str(hval or "").strip()
                if h_str:
                    local_hashes[norm_file_path(p)] = h_str
                out.append((p, h_str or None))
    for fkey in ("files", "_table_files", "source_links", "external_uris"):
        if isinstance(c.get(fkey), list):
            for x in c[fkey]:
                if isinstance(x, str) and (p := split_uri_parts(x)[1]):
                    out.append((p, local_hashes.get(norm_file_path(p))))
    for ukey in ("file", "target_uri", "uri"):
        if isinstance(c.get(ukey), str) and (p := split_uri_parts(c[ukey])[1]):
            out.append((p, local_hashes.get(norm_file_path(p))))
    if isinstance(c.get("external_links"), list):
        for lk in c["external_links"]:
            u = lk if isinstance(lk, str) else (str(lk.get("target_uri") or lk.get("uri") or "") if isinstance(lk, dict) else "")
            if u and (p := split_uri_parts(u)[1]):
                out.append((p, local_hashes.get(norm_file_path(p))))


def infer_cross_repo_renames(
    graph: dict[str, Any],
    candidates: dict[str, tuple[str | None, dict[str, tuple[int, int]]]],
    renames: list[tuple[str, str, dict[str, tuple[int, int]]]],
    ast_updates: list[tuple[str, dict[str, tuple[int, int]]]],
) -> None:
    """Infers `(orig_p, new_p, syms)` renames across git init / prefix moves via content hash or unique `>=2`-segment suffix."""
    if not candidates or not isinstance(graph, dict):
        return
    cand_norms = {norm_file_path(cp) for cp in candidates if norm_file_path(cp)}
    cand_segs = {cp: [s for s in norm_file_path(cp).split("/") if s] for cp in candidates}
    found: list[tuple[str, str | None]] = []
    all_hashes: dict[str, str] = {}
    _scan_container_paths(graph, all_hashes, found)
    if isinstance(scopes := graph.get("scopes"), dict):
        for sc in (s for s in scopes.values() if isinstance(s, dict)):
            _scan_container_paths(sc, all_hashes, found)
            if isinstance(sc.get("attributes"), dict):
                _scan_container_paths(sc["attributes"], all_hashes, found)
            for rule in (sc.get("rules") if isinstance(sc.get("rules"), list) else []):
                if isinstance(rule, dict):
                    _scan_container_paths(rule, all_hashes, found)
    raw_arts = graph.get("lattice_artifacts")
    for art in (raw_arts.values() if isinstance(raw_arts, dict) else (raw_arts if isinstance(raw_arts, list) else [])):
        if isinstance(art, dict):
            _scan_container_paths(art, all_hashes, found)
            if isinstance(art.get("dimensions"), dict):
                _scan_container_paths(art["dimensions"], all_hashes, found)
    if isinstance(ext_top := graph.get("external_links"), dict):
        for lk_list in ext_top.values():
            if isinstance(lk_list, list):
                _scan_container_paths({"external_links": lk_list}, all_hashes, found)
    for orig_p, raw_h in found:
        np_orig = norm_file_path(orig_p)
        orig_h = raw_h or all_hashes.get(np_orig)
        if not np_orig or np_orig in cand_norms or any(norm_file_path(old_p) == np_orig for old_p, _, _ in renames):
            continue
        matched_cp: str | None = None
        if orig_h and len(h_matches := [cp for cp, (ch, _) in candidates.items() if ch and ch == orig_h]) == 1:
            matched_cp = h_matches[0]
        if matched_cp is None and len(o_segs := [s for s in np_orig.split("/") if s]) >= 2:
            for k in range(len(o_segs), 1, -1):
                s_matches = [cp for cp, c_s in cand_segs.items() if len(c_s) >= k and c_s[-k:] == o_segs[-k:]]
                if len(s_matches) == 1:
                    matched_cp = s_matches[0]
                    break
                if len(s_matches) > 1:
                    break
        if matched_cp is not None:
            _, c_syms = candidates[matched_cp]
            renames.append((orig_p, matched_cp, c_syms))
            if c_syms:
                ast_updates.extend(((matched_cp, c_syms), (orig_p, c_syms)))


def parse_line_anchor(anchor: str) -> tuple[int, int] | None:
    """Parses `#L<start>-L<end>`, `#L<line>`, `#<line>`, or `#symbol:L<start>-L<end>` into `(start, end)`."""
    if not (a := anchor.strip()):
        return None
    if ":" in a:
        return next((parsed for part in a.split(":") if (parsed := parse_line_anchor(part)) is not None), None)
    if "-" in a:
        left, right = a.split("-", 1)
        l_num, r_num = left.removeprefix("L").removeprefix("l").strip(), right.removeprefix("L").removeprefix("l").strip()
        return (int(l_num), int(r_num)) if (l_num.isdigit() and r_num.isdigit()) else None
    single = a.removeprefix("L").removeprefix("l").strip()
    return (int(single), int(single)) if single.isdigit() else None


for _m, _f in (("upsert_concept_frame", upsert_concept_frame), ("get_concept_frame", get_concept_frame), ("list_concept_frames", list_concept_frames), ("link_concepts", link_concepts)):
    if not hasattr(SQLiteGraphStore, _m):
        setattr(SQLiteGraphStore, _m, _f)
