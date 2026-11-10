"""Spec 24A & Stage 26 — `ai_memory.uri_resolver`: External Link Resolution & Git Diff URI Healer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore

from .cue_extractor import _read_links_for_concept, _write_links_for_concept, verify_uri_resolver_entry
from .schema import (
    LOD_0,
    LOD_1,
    LOD_2,
    LOD_3,
    MAX_LINKS_PER_NODE,
    ExternalLinkPointer,
    _is_int,
    _pick,
    get_concept_frame,
    normalize_external_link,
    upsert_concept_frame,
)
from .uri_healer import _UriSyncState, is_symbol_uri_anchored
from .uri_manifest import (
    _extract_raw_manifest_entries,
    _parse_ast_symbols,
    _parse_git_diff_manifest,
    _resolve_ast_line_range,
    infer_cross_repo_renames,
    norm_file_path,
    parse_line_anchor,
    paths_match,
    replace_renamed_path,
    split_uri_parts,
)


class LinkCountBucket(str, Enum):
    LBZero = "LBZero"
    LB1To7 = "LB1To7"
    LBAtCap8 = "LBAtCap8"


LBZero, LB1To7, LBAtCap8 = (
    LinkCountBucket.LBZero,
    LinkCountBucket.LB1To7,
    LinkCountBucket.LBAtCap8,
)
_TIER_NORM_MAP = {"L0": LOD_0, "L1": LOD_1, "L2": LOD_2, "L3": LOD_3}


def bucket_for_link_count(
    count: int,
    max_links_per_node: int = MAX_LINKS_PER_NODE,
) -> LinkCountBucket:
    cap = min(max(1, int(max_links_per_node)), MAX_LINKS_PER_NODE)
    return LBZero if count <= 0 else (LBAtCap8 if count >= cap else LB1To7)


def _norm_bucket(b: Any, max_links_per_node: int = MAX_LINKS_PER_NODE) -> LinkCountBucket:
    if isinstance(b, LinkCountBucket):
        return b
    u = b["$"] if isinstance(b, dict) and "$" in b else b
    if isinstance(u, str) and (s := u.strip().rstrip("{}")) in ("LBZero", "LB1To7", "LBAtCap8"):
        return LinkCountBucket(s)
    if _is_int(u):
        return bucket_for_link_count(u, max_links_per_node)
    raise ValueError(f"Unsupported LinkCountBucket value: {b!r}")


def link_bucket_eq(a: Any, b: Any) -> bool:
    return _norm_bucket(a) == _norm_bucket(b)


def is_within_8_cap(b: Any) -> bool:
    return _norm_bucket(b) in (LBZero, LB1To7, LBAtCap8)


def step_uri_link_registration(cur_bucket: Any, is_duplicate_uri: bool) -> LinkCountBucket:
    bucket, dup = _norm_bucket(cur_bucket), bool(is_duplicate_uri)
    if bucket == LBZero:
        return LBZero if dup else LB1To7
    return (LB1To7 if dup else LBAtCap8) if bucket == LB1To7 else LBAtCap8


def register_external_link(
    store_or_frame: Any,
    concept_id: str | None = None,
    target_uri: str | None = None,
    *,
    artifact_kind: str = "doc",
    lod_tier: str = LOD_1,
    link_id: str | None = None,
    max_links_per_node: int = MAX_LINKS_PER_NODE,
) -> ExternalLinkPointer:
    if target_uri is None and isinstance(store_or_frame, dict) and "concept_id" in store_or_frame:
        if isinstance(concept_id, str):
            target_uri, concept_id = concept_id, str(store_or_frame["concept_id"])
    if not isinstance(concept_id, str) or not concept_id.strip():
        raise ValueError(f"concept_id must be a non-empty str, got {concept_id!r}")
    if not isinstance(target_uri, str) or not target_uri.strip():
        raise ValueError(f"target_uri must be a non-empty str, got {target_uri!r}")
    if not _is_int(max_links_per_node) or max_links_per_node <= 0:
        raise ValueError(f"max_links_per_node must be a positive int, got {max_links_per_node!r}")
    cid, uri_clean = concept_id.strip(), target_uri.strip()
    cap = min(max_links_per_node, MAX_LINKS_PER_NODE)
    new_ptr = normalize_external_link(
        ExternalLinkPointer(link_id, uri_clean, artifact_kind, lod_tier, concept_id=cid),
        concept_id=cid,
    )
    existing = _read_links_for_concept(store_or_frame, cid)
    match = next((lk for lk in existing if lk.target_uri == uri_clean), None)
    cur_b = bucket_for_link_count(len(existing), cap)
    next_b = step_uri_link_registration(cur_b, match is not None)
    if not is_within_8_cap(next_b):
        raise ValueError(
            f"uri_link_idempotence_law violated for {cid!r}: bucket {next_b!r} exceeds 8-link cap"
        )
    if match is not None:
        return match
    if cur_b == LBAtCap8 or len(existing) >= cap:
        return existing[-1] if existing else new_ptr
    _write_links_for_concept(store_or_frame, cid, [*existing, new_ptr][:cap])
    return new_ptr


def _matches_lod_tier(link_tier: str, requested_tier: str | None) -> bool:
    if requested_tier is None or (req := requested_tier.strip().upper()) in ("", "*", "ALL"):
        return True
    lt = link_tier.strip().upper()
    return _TIER_NORM_MAP.get(lt, lt) == _TIER_NORM_MAP.get(req, req)


def resolve_external_links(
    store_or_frame: Any,
    concept_id: str | list[str] | tuple[str, ...] | None = None,
    lod_tier: str | None = LOD_1,
    *,
    max_links_per_node: int = MAX_LINKS_PER_NODE,
) -> list[ExternalLinkPointer]:
    if not _is_int(max_links_per_node) or max_links_per_node < 0:
        raise ValueError(
            f"max_links_per_node must be a non-negative int, got {max_links_per_node!r}"
        )
    if (cap := min(max_links_per_node, MAX_LINKS_PER_NODE)) == 0:
        return []
    if isinstance(concept_id, (list, tuple)):
        return [
            lk
            for item in concept_id
            if isinstance(item, str) and item.strip()
            for lk in resolve_external_links(
                store_or_frame, item.strip(), lod_tier, max_links_per_node=cap
            )
        ]
    if isinstance(store_or_frame, dict) and "concept_id" in store_or_frame:
        fcid = str(store_or_frame.get("concept_id") or "").strip()
        if concept_id is None:
            concept_id = fcid
        elif isinstance(concept_id, str) and concept_id != fcid:
            if concept_id.upper() in (*_TIER_NORM_MAP, *_TIER_NORM_MAP.values()):
                lod_tier, concept_id = concept_id, fcid
    if not isinstance(concept_id, str) or not concept_id.strip():
        return []
    all_links = _read_links_for_concept(store_or_frame, concept_id.strip())[:cap]
    if not all_links or lod_tier is None or str(lod_tier).strip().upper() in ("", "*", "ALL"):
        return all_links[:cap]
    if exact := [lk for lk in all_links if _matches_lod_tier(lk.lod_tier, lod_tier)]:
        return exact[:cap]
    req_up = str(lod_tier).strip().upper()
    return all_links[:cap] if _TIER_NORM_MAP.get(req_up, req_up) in (LOD_2, LOD_3) else []


def is_commit_uri_state_valid(
    has_dangling_deleted_uri: bool,
    rename_pointers_updated: bool,
) -> bool:
    return (not bool(has_dangling_deleted_uri)) and bool(rename_pointers_updated)


ast_symbol_uri_anchor_law = is_symbol_uri_anchored


def _sync_lattice_artifact(
    art: dict[str, Any],
    ak: Any,
    graph: dict[str, Any],
    state: _UriSyncState,
    calls_by_file: dict[str, Any],
) -> None:
    acid = str(_pick(art, "concept_id", "scope_id", "scope", "name", "id", default=ak))
    asym = str(_pick(art, "name", "id", default=""))
    state.heal_container(art, acid, hint_symbol=asym)
    if isinstance(dims := art.get("dimensions"), dict):
        state.heal_container(dims, acid, hint_symbol=asym)
    if calls_by_file and (af_clean := norm_file_path(str(art.get("file") or ""))):
        f_calls = next(
            (v for k, v in calls_by_file.items() if paths_match(af_clean, k) and isinstance(v, dict)),
            None,
        )
        if f_calls is not None and asym in f_calls:
            art["calls"] = list(f_calls[asym])
    af = str(art.get("file") or "").strip()
    if af and "#" in af and acid and acid != "global_defaults":
        if not isinstance(graph.get("external_links"), dict):
            graph["external_links"] = {}
        full_u = af if af.startswith("uri://") else f"uri://file/{af.lstrip('./')}"
        base_u = full_u.split("#", 1)[0]
        ext_list = [
            x
            for x in graph.setdefault("external_links", {}).setdefault(acid, [])
            if not (
                isinstance(x, dict)
                and str(x.get("symbol") or "") == asym
                and (xu := str(_pick(x, "target_uri", "uri", default=""))).split("#", 1)[0] == base_u
                and xu != full_u
            )
        ]
        has_full = any(
            (isinstance(x, dict) and str(_pick(x, "target_uri", "uri", default="")) == full_u)
            or x == full_u
            for x in ext_list
        )
        if len(ext_list) < state.cap and not has_full:
            ptr_spec = {"target_uri": full_u, "uri": full_u, "symbol": asym, "lod_tier": LOD_2}
            ext_list.append(ExternalLinkPointer(ptr_spec, concept_id=acid))
        graph["external_links"][acid] = ext_list


def sync_git_diff_uris(
    store_or_graph: Any,
    git_diff_manifest: dict[str, Any] | list[dict[str, Any]],
    *,
    current_turn: int = 1,
    max_links_per_node: int = MAX_LINKS_PER_NODE,
) -> dict[str, Any]:
    (
        commit_sha,
        renames,
        ast_updates,
        deletes,
        candidates,
        manifest_hashes,
        line_shifts,
        renames_ok,
    ) = _parse_git_diff_manifest(git_diff_manifest)
    calls_by_file = (
        git_diff_manifest["ast_calls_by_file"]
        if isinstance(git_diff_manifest, dict)
        and isinstance(git_diff_manifest.get("ast_calls_by_file"), dict)
        else {}
    )
    is_sqlite = isinstance(store_or_graph, SQLiteGraphStore)
    graph = store_or_graph.load_graph() if is_sqlite else store_or_graph
    if not isinstance(graph, dict):
        raise ValueError(
            f"store_or_graph must be a SQLiteGraphStore or dict, got {type(store_or_graph).__name__}"
        )
    infer_cross_repo_renames(graph, candidates, renames, ast_updates)
    eff_cap = min(max(1, int(max_links_per_node)), MAX_LINKS_PER_NODE)
    state = _UriSyncState(
        renames,
        ast_updates,
        deletes,
        int(current_turn),
        eff_cap,
        manifest_hashes,
        line_shifts,
    )
    for sid, scope in (graph.get("scopes") or {}).items():
        if scope:
            cid = str(sid)
            state.heal_container(scope, cid)
            if isinstance(attrs := scope.get("attributes"), dict):
                state.heal_container(attrs, cid)
            for rule in scope.get("rules") or []:
                state.heal_container(rule, cid, is_rule=True)
            if cid in state.affected_scopes and commit_sha is not None:
                scope["last_commit_sha"] = commit_sha
                if isinstance(attrs, dict) and "last_commit_sha" in attrs:
                    attrs["last_commit_sha"] = commit_sha
    if isinstance(ext_top := graph.get("external_links"), dict):
        for nid, lk_list in list(ext_top.items()):
            if isinstance(lk_list, list):
                ext_top[nid], _ = state.heal_link_list(lk_list, str(nid))
    elif isinstance(ext_top, list) or "concept_id" in graph:
        state.heal_container(graph, str(_pick(graph, "concept_id", "id", default="root")))
    raw_arts = graph.get("lattice_artifacts")
    art_items = (
        raw_arts.items()
        if isinstance(raw_arts, dict)
        else enumerate(raw_arts if isinstance(raw_arts, list) else [])
    )
    for ak, art in art_items:
        if art:
            _sync_lattice_artifact(art, ak, graph, state, calls_by_file)
    if is_sqlite:
        graph.setdefault("external_links", {})
        store_or_graph.save_graph(graph)
    rename_pointers_updated = bool(renames_ok and all(state.rename_status.values()))
    has_dangling_deleted_uri = bool(state.dangling_deleted_uris)
    upd_u = list(
        dict.fromkeys(
            str(r["new_uri"])
            for r in (*state.renamed_uris, *state.updated_line_uris)
            if r.get("new_uri")
        )
    )
    anchors_ok = all("#" in str(r.get("new_uri") or "") for r in state.updated_line_uris)
    is_valid = is_commit_uri_state_valid(
        has_dangling_deleted_uri, rename_pointers_updated
    ) and is_symbol_uri_anchored(bool(state.updated_line_uris), anchors_ok)
    return {
        "is_valid": is_valid,
        "has_dangling_deleted_uri": has_dangling_deleted_uri,
        "rename_pointers_updated": rename_pointers_updated,
        "updated_uris": upd_u,
        "dangling_uri_pointers": len(state.dangling_deleted_uris),
        "dangling_deleted_uris": state.dangling_deleted_uris,
        "renamed_uris": state.renamed_uris,
        "updated_line_uris": state.updated_line_uris,
        "affected_scopes": state.affected_scopes,
        "commit_sha": commit_sha,
    }


_SQLITE_URI_METHODS = (
    ("register_external_link", register_external_link),
    ("resolve_external_links", resolve_external_links),
    ("sync_git_diff_uris", sync_git_diff_uris),
)
for _m, _f in _SQLITE_URI_METHODS:
    if not hasattr(SQLiteGraphStore, _m):
        setattr(SQLiteGraphStore, _m, _f)
