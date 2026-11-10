"""Spec 24A — `ai_memory.schema`: ConceptFrame & ExternalLinkPointer Schemas (`schema.py`)."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from .uri_manifest import (
    _scan_container_paths,
    infer_cross_repo_renames,
    norm_file_path,
    parse_line_anchor,
    paths_match,
    replace_renamed_path,
    split_uri_parts,
)

MAX_SEED_CUES, MAX_LINKS_PER_NODE, MAX_SYNOPSIS_WORDS = 12, 8, 50
DEFAULT_LOD1_TOKEN_BUDGET, MAX_LOD1_TOKEN_BUDGET, MAX_RECALL_HOPS = 250, 400, 2
LOD_0, LOD_1, LOD_2, LOD_3 = "LOD0", "LOD1", "LOD2", "LOD3"
_FORBIDDEN_RAW_DOC_KEYS: frozenset[str] = frozenset(
    {
        "raw_document",
        "raw_documents",
        "raw_text",
        "document_body",
        "full_text",
        "raw_payload",
        "raw_content",
    }
)
_LINK_ATTRS = (
    "link_id",
    "target_uri",
    "artifact_kind",
    "lod_tier",
    "uri",
    "kind",
    "source_links",
    "external_uris",
)
_LINK_ALIAS = {"uri": "target_uri", "kind": "artifact_kind", "summary": "lod_tier"}
_CONCEPT_BASE_KEYS = frozenset(
    {
        "concept_id",
        "id",
        "lod1_synopsis",
        "synopsis",
        "external_links",
        "activation",
        "hop_distance",
        "updated_turn",
    }
)


def _pick(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if (v := d.get(k)) is not None and v != "":
            return v
    return next((d[k] for k in keys if k in d and d[k] is not None), default)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_valid_concept_storage(
    has_raw_document: bool,
    words_within_50: bool,
    links_within_8: bool,
) -> bool:
    """Bend 2 Law 1 (`pointer_only_storage_law`): True iff no raw doc, words <= 50, and links <= 8."""
    return (not bool(has_raw_document)) and bool(words_within_50) and bool(links_within_8)


class ExternalLinkPointer(dict):
    """Canonical `uri_resolver` external artifact pointer (`link_id`, `target_uri`, `artifact_kind`)."""

    def __init__(
        self,
        link_id: str | dict[str, Any] | None = None,
        target_uri: str = "",
        artifact_kind: str = "doc",
        lod_tier: str = LOD_1,
        *,
        concept_id: str = "",
        **extra: Any,
    ) -> None:
        super().__init__()
        is_d = isinstance(link_id, dict)
        src = dict(link_id) if is_d else dict(extra)
        meta = src["metadata"] if isinstance(src.get("metadata"), dict) else {}
        merged = {**meta, **src}
        raw_uri = (
            _pick(merged, "target_uri", "uri", default=target_uri)
            if is_d
            else (target_uri or src.pop("uri", ""))
        )
        raw_kind = (
            _pick(merged, "artifact_kind", "kind", default=artifact_kind)
            if is_d
            else (
                src.pop("kind", artifact_kind)
                if artifact_kind == "doc" and "kind" in src
                else artifact_kind
            )
        )
        raw_tier = _pick(merged, "lod_tier", "summary", default=lod_tier) if is_d else lod_tier
        raw_lid = src.get("link_id") if is_d else link_id
        raw_cid = (
            _pick(src, "concept_id", "node_id", default=concept_id)
            if is_d
            else (concept_id or src.pop("node_id", ""))
        )
        cid, uri_clean = str(raw_cid or "").strip(), str(raw_uri or "").strip()
        for fk in (*_FORBIDDEN_RAW_DOC_KEYS, "source_links", "external_uris"):
            if fk in src and src[fk] is not None:
                self[fk] = src[fk]
        kind_clean = str(raw_kind or "doc").strip() or "doc"
        tier_clean = str(raw_tier or LOD_1).strip() or LOD_1
        if tier_clean not in (LOD_0, LOD_1, LOD_2, LOD_3, "L0", "L1", "L2", "L3"):
            tier_clean = LOD_1
        digest = hashlib.sha256(f"{cid}|{uri_clean}|{kind_clean}".encode("utf-8")).hexdigest()[:12]
        default_lid = f"link_{cid}_{digest}" if cid else f"link_{digest}"
        lid_clean = raw_lid.strip() if isinstance(raw_lid, str) and raw_lid.strip() else default_lid
        self.update(
            link_id=lid_clean,
            target_uri=uri_clean,
            artifact_kind=kind_clean,
            lod_tier=tier_clean,
        )

    def __getattr__(self, name: str) -> Any:
        if name in _LINK_ATTRS:
            return self.get(name)
        raise AttributeError(name)

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return super().get(key, default)
        if key in _LINK_ALIAS:
            return super().get(_LINK_ALIAS[key], default)
        if key == "metadata":
            return {
                "lod_tier": super().get("lod_tier", LOD_1),
                "artifact_kind": super().get("artifact_kind", "doc"),
                "target_uri": super().get("target_uri", ""),
            }
        if key in ("source_links", "external_uris"):
            return [str(u)] if (u := super().get("target_uri", "")) else []
        return default

    def __getitem__(self, key: Any) -> Any:
        alias_keys = ("uri", "kind", "summary", "metadata", "source_links", "external_uris")
        if key in self or key in alias_keys:
            return self.get(key)
        raise KeyError(key)

    def to_dict(self) -> dict[str, Any]:
        return {k: self[k] for k in ("link_id", "target_uri", "artifact_kind", "lod_tier")}


def normalize_external_link(link: Any, *, concept_id: str = "") -> ExternalLinkPointer:
    """Normalizes a link dict, URI string, or `ExternalLinkPointer` into `ExternalLinkPointer`."""
    if isinstance(link, dict):
        ptr = ExternalLinkPointer(dict(link), concept_id=concept_id)
    elif isinstance(link, str) and link.strip():
        ptr = ExternalLinkPointer(None, link.strip(), "doc", LOD_1, concept_id=concept_id)
    else:
        raise ValueError(f"Invalid external link record: {link!r}")
    if not ptr.target_uri:
        raise ValueError(f"ExternalLinkPointer target_uri must be non-empty, got {link!r}")
    if any(fk in ptr and ptr[fk] is not None for fk in _FORBIDDEN_RAW_DOC_KEYS):
        raise ValueError(
            "pointer_only_storage_law violated: forbidden raw document field in external link"
        )
    has_sync_keys = "source_links" in ptr or "external_uris" in ptr
    if has_sync_keys and list(ptr.get("source_links")) != list(ptr.get("external_uris")):
        raise ValueError("uri_resolver obligation violated: source_links != external_uris")
    return ptr


class ConceptFrame(dict):
    """Canonical `ai_memory` ConceptFrame schema (`concept_id`, `lod1_synopsis`, `external_links`)."""

    def __init__(
        self,
        concept_id: str | dict[str, Any] = "",
        lod1_synopsis: str = "",
        external_links: list[Any] | None = None,
        *,
        activation: float | None = None,
        hop_distance: int | None = None,
        updated_turn: int | None = None,
        **extra: Any,
    ) -> None:
        super().__init__()
        if isinstance(concept_id, dict):
            src = dict(concept_id)
            cid = str(_pick(src, "concept_id", "id", default="")).strip()
            syn = _pick(src, "lod1_synopsis", "synopsis", default="")
            raw_links = src.get("external_links", [] if external_links is None else external_links)
            act = src.get("activation", activation)
            hop = src.get("hop_distance", hop_distance)
            turn = src.get("updated_turn", updated_turn)
            extra_fields = {k: v for k, v in src.items() if k not in _CONCEPT_BASE_KEYS} | extra
        else:
            cid = str(concept_id or "").strip()
            syn = (
                lod1_synopsis
                if lod1_synopsis or "synopsis" not in extra
                else extra.pop("synopsis", "")
            )
            raw_links = [] if external_links is None else external_links
            act, hop, turn, extra_fields = activation, hop_distance, updated_turn, dict(extra)
        self["concept_id"] = cid
        self["lod1_synopsis"] = " ".join(str(syn).split()) if isinstance(syn, str) else syn
        if isinstance(raw_links, list):
            self["external_links"] = [
                ExternalLinkPointer(lk, concept_id=cid)
                if isinstance(lk, dict)
                else (ExternalLinkPointer(None, lk, concept_id=cid) if isinstance(lk, str) else lk)
                for lk in raw_links
            ]
        else:
            self["external_links"] = raw_links
        numeric_fields = (
            ("activation", act, float),
            ("hop_distance", hop, int),
            ("updated_turn", turn, int),
        )
        for k, v, fn in numeric_fields:
            if v is not None:
                self[k] = fn(v)
        self.update(extra_fields)

    def __getattr__(self, name: str) -> Any:
        if name in ("concept_id", "lod1_synopsis", "external_links", "source_links", "external_uris"):
            return self.get(name)
        if name in ("activation", "hop_distance", "updated_turn"):
            defaults = {"activation": (0.0, float), "hop_distance": (0, int), "updated_turn": (1, int)}
            dval, fn = defaults[name]
            return fn(super().get(name, dval) or dval)
        raise AttributeError(name)

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return super().get(key, default)
        if key in ("source_links", "external_uris"):
            links = super().get("external_links", [])
            return [
                str(u)
                for lk in (links or [])
                if isinstance(lk, dict) and (u := _pick(lk, "target_uri", "uri"))
            ]
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
    cid = frame.get("concept_id")
    synopsis = frame.get("lod1_synopsis")
    raw_links = frame.get("external_links", [])
    if (
        not isinstance(cid, str)
        or not cid.strip()
        or not isinstance(synopsis, str)
        or not isinstance(raw_links, list)
    ):
        raise ValueError(
            "ConceptFrame requires non-empty concept_id, lod1_synopsis: str, and external_links: list"
        )
    cid_clean, words = cid.strip(), synopsis.split()
    has_raw_top = any(k in frame and frame[k] is not None for k in _FORBIDDEN_RAW_DOC_KEYS)
    has_raw_link = any(
        isinstance(lk, dict)
        and any(k in lk and lk[k] is not None for k in _FORBIDDEN_RAW_DOC_KEYS)
        for lk in raw_links
    )
    if not is_valid_concept_storage(
        has_raw_top or has_raw_link,
        len(words) <= MAX_SYNOPSIS_WORDS,
        len(raw_links) <= MAX_LINKS_PER_NODE,
    ):
        raise ValueError(f"pointer_only_storage_law violated on {cid_clean!r}")
    norm_links: list[ExternalLinkPointer] = []
    seen_uris: set[str] = set()
    for lk in raw_links:
        ptr = normalize_external_link(lk, concept_id=cid_clean)
        if ptr.target_uri not in seen_uris:
            seen_uris.add(ptr.target_uri)
            norm_links.append(ptr)
    uris = [p.target_uri for p in norm_links]
    bad_src = "source_links" in frame and list(frame["source_links"]) != uris
    bad_ext = "external_uris" in frame and list(frame["external_uris"]) != uris
    if bad_src or bad_ext:
        raise ValueError(
            f"uri_resolver obligation violated on {cid_clean!r}: source_links/external_uris mismatch"
        )
    hop_val = frame.get("hop_distance")
    if hop_val is not None and (not _is_int(hop_val) or hop_val < 0):
        raise ValueError(f"ConceptFrame.hop_distance must be a non-negative int, got {hop_val!r}")
    skip = _CONCEPT_BASE_KEYS | {"source_links", "external_uris"}
    extra_clean = {k: copy.deepcopy(v) for k, v in frame.items() if k not in skip}
    return ConceptFrame(
        cid_clean,
        " ".join(words),
        norm_links,
        activation=frame.get("activation"),
        hop_distance=hop_val,
        updated_turn=frame.get("updated_turn"),
        **extra_clean,
    )


from .concept_store import (  # noqa: E402
    get_concept_frame,
    link_concepts,
    list_concept_frames,
    upsert_concept_frame,
)
