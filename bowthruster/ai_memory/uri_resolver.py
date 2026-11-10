"""Spec 24A & Stage 26 — `ai_memory.uri_resolver`: Idempotent External Link Registration, Resolution, 8-Link Cap, & Git Diff URI Healer (`core/circuit/ai_memory/uri_resolver.py`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore

from .schema import (
    LOD_0,
    LOD_1,
    LOD_2,
    LOD_3,
    MAX_LINKS_PER_NODE,
    ExternalLinkPointer,
    get_concept_frame,
    infer_cross_repo_renames,
    norm_file_path,
    normalize_external_link,
    parse_line_anchor,
    paths_match,
    replace_renamed_path,
    split_uri_parts,
    upsert_concept_frame,
)


class LinkCountBucket(str, Enum):
    """Quotient link-count bucket state from `LAWS.bend` (`LBZero`, `LB1To7`, `LBAtCap8`)."""
    LBZero = "LBZero"
    LB1To7 = "LB1To7"
    LBAtCap8 = "LBAtCap8"


LBZero, LB1To7, LBAtCap8 = LinkCountBucket.LBZero, LinkCountBucket.LB1To7, LinkCountBucket.LBAtCap8


def bucket_for_link_count(count: int, max_links_per_node: int = MAX_LINKS_PER_NODE) -> LinkCountBucket:
    """Maps an integer link count into its canonical `LinkCountBucket` (`LBZero`, `LB1To7`, `LBAtCap8`)."""
    cap = min(max(1, int(max_links_per_node)), MAX_LINKS_PER_NODE)
    return LinkCountBucket.LBZero if count <= 0 else (LinkCountBucket.LBAtCap8 if count >= cap else LinkCountBucket.LB1To7)


def _norm_bucket(b: Any, max_links_per_node: int = MAX_LINKS_PER_NODE) -> LinkCountBucket:
    if isinstance(b, LinkCountBucket):
        return b
    if isinstance(b, dict) and "$" in b:
        b = b["$"]
    if isinstance(b, str) and (s := b.strip().rstrip("{}")) in ("LBZero", "LB1To7", "LBAtCap8"):
        return LinkCountBucket(s)
    if isinstance(b, int) and not isinstance(b, bool):
        return bucket_for_link_count(b, max_links_per_node)
    raise ValueError(f"Unsupported LinkCountBucket value: {b!r}")


def link_bucket_eq(a: Any, b: Any) -> bool:
    """Bend 2 Law 3 helper (`MEMA.link_bucket_eq`): structural equality on `LinkCountBucket`."""
    return _norm_bucket(a) == _norm_bucket(b)


def is_within_8_cap(b: Any) -> bool:
    """Bend 2 Law 3 helper (`MEMA.is_within_8_cap`): returns `True` for all valid `LinkCountBucket` states."""
    return _norm_bucket(b) in (LinkCountBucket.LBZero, LinkCountBucket.LB1To7, LinkCountBucket.LBAtCap8)


def step_uri_link_registration(cur_bucket: Any, is_duplicate_uri: bool) -> LinkCountBucket:
    """Bend 2 Law 3 (`uri_link_idempotence_law`): preserves bucket on duplicate and saturates at `LBAtCap8`."""
    bucket, dup = _norm_bucket(cur_bucket), bool(is_duplicate_uri)
    if bucket == LinkCountBucket.LBZero:
        return LinkCountBucket.LBZero if dup else LinkCountBucket.LB1To7
    if bucket == LinkCountBucket.LB1To7:
        return LinkCountBucket.LB1To7 if dup else LinkCountBucket.LBAtCap8
    return LinkCountBucket.LBAtCap8


def verify_uri_resolver_entry(entry: dict[str, Any], *, max_links_per_node: int = MAX_LINKS_PER_NODE) -> bool:
    """Verifies `r_uri_resolver_7` (`source_links == external_uris`) and `r_uri_resolver_18` (`<= 8` links)."""
    if not isinstance(entry, dict):
        raise ValueError(f"uri_resolver entry must be a dict, got {type(entry).__name__}")
    cap = min(max(0, int(max_links_per_node)), MAX_LINKS_PER_NODE)
    if "source_links" in entry and "external_uris" in entry and list(entry["source_links"]) != list(entry["external_uris"]):
        raise ValueError("uri_resolver obligation violated: source_links != external_uris")
    if "external_links" in entry and isinstance(entry["external_links"], list):
        if len(entry["external_links"]) > cap:
            raise ValueError(f"uri_resolver bound violated: {len(entry['external_links'])} links > {cap}")
        uris = [
            str(lk.get("target_uri") or lk.get("uri"))
            for lk in entry["external_links"] if isinstance(lk, dict)
            and (lk.get("target_uri") or lk.get("uri"))
        ]
        if ("source_links" in entry and list(entry["source_links"]) != uris) or ("external_uris" in entry and list(entry["external_uris"]) != uris):
            raise ValueError("uri_resolver obligation violated: source_links/external_uris mismatch")
    return True


def _read_links_for_concept(store_or_frame: Any, concept_id: str) -> list[ExternalLinkPointer]:
    if isinstance(store_or_frame, SQLiteGraphStore):
        raw_links = (
            store_or_frame.get_external_links(concept_id)
            or (frame.external_links if (frame := get_concept_frame(store_or_frame, concept_id)) is not None else [])
        )
    elif isinstance(store_or_frame, dict):
        verify_uri_resolver_entry(store_or_frame)
        if store_or_frame.get("concept_id") == concept_id and isinstance(store_or_frame.get("external_links"), list):
            raw_links = store_or_frame["external_links"]
        elif isinstance(store_or_frame.get("external_links"), dict) and isinstance(
            store_or_frame["external_links"].get(concept_id),
            list
        ):
            raw_links = store_or_frame["external_links"][concept_id]
        elif "scopes" in store_or_frame:
            raw_links = frame.external_links if (frame := get_concept_frame(store_or_frame, concept_id)) is not None else []
        elif isinstance(store_or_frame.get(concept_id), list):
            raw_links = store_or_frame[concept_id]
        elif isinstance(store_or_frame.get(concept_id), dict):
            verify_uri_resolver_entry(store_or_frame[concept_id])
            raw_links = store_or_frame[concept_id].get("external_links", [])
        else:
            raw_links = []
    else:
        raise ValueError(f"Unsupported store_or_frame type: {type(store_or_frame).__name__}")
    out, seen_uris = [], set()
    for lk in raw_links:
        if (ptr := normalize_external_link(lk, concept_id=concept_id)).target_uri not in seen_uris:
            seen_uris.add(ptr.target_uri)
            out.append(ptr)
    return out


def _write_links_for_concept(store_or_frame: Any, concept_id: str, links: list[ExternalLinkPointer]) -> None:
    if isinstance(store_or_frame, SQLiteGraphStore) or (isinstance(store_or_frame, dict) and "scopes" in store_or_frame):
        if (frame := get_concept_frame(store_or_frame, concept_id)) is not None:
            frame["external_links"] = list(links)
            upsert_concept_frame(store_or_frame, frame)
        else:
            upsert_concept_frame(store_or_frame, concept_id, "", list(links))
        return
    if not isinstance(store_or_frame, dict):
        raise ValueError(f"Unsupported store_or_frame type: {type(store_or_frame).__name__}")
    uris = [p.target_uri for p in links]
    if "concept_id" in store_or_frame and (
        store_or_frame.get("concept_id") == concept_id
        or not store_or_frame.get("concept_id")
    ):
        store_or_frame["concept_id"], store_or_frame["external_links"] = concept_id, list(links)
        for k in ("source_links", "external_uris"):
            if k in store_or_frame:
                store_or_frame[k] = list(uris)
    elif isinstance(store_or_frame.get("external_links"), dict):
        store_or_frame["external_links"][concept_id] = list(links)
    elif isinstance(store_or_frame.get(concept_id), dict):
        store_or_frame[concept_id]["external_links"] = list(links)
        for k in ("source_links", "external_uris"):
            if k in store_or_frame[concept_id]:
                store_or_frame[concept_id][k] = list(uris)
    else:
        store_or_frame[concept_id] = list(links)


def register_external_link(
    store_or_frame: Any, concept_id: str | None = None, target_uri: str | None = None, *,
    artifact_kind: str = "doc", lod_tier: str = LOD_1, link_id: str | None = None, max_links_per_node: int = MAX_LINKS_PER_NODE,
) -> ExternalLinkPointer:
    """Registers an `ExternalLinkPointer` for `concept_id` on `store_or_frame` (`r_uri_resolver_7`, `r_uri_resolver_18`)."""
    if target_uri is None and isinstance(store_or_frame, dict) and "concept_id" in store_or_frame and isinstance(concept_id, str):
        target_uri, concept_id = concept_id, str(store_or_frame["concept_id"])
    if not isinstance(concept_id, str) or not concept_id.strip():
        raise ValueError(f"concept_id must be a non-empty str, got {concept_id!r}")
    if not isinstance(target_uri, str) or not target_uri.strip():
        raise ValueError(f"target_uri must be a non-empty str, got {target_uri!r}")
    if not isinstance(max_links_per_node, int) or isinstance(max_links_per_node, bool) or max_links_per_node <= 0:
        raise ValueError(f"max_links_per_node must be a positive int, got {max_links_per_node!r}")
    cid, uri_clean, effective_cap = concept_id.strip(), target_uri.strip(), min(max_links_per_node, MAX_LINKS_PER_NODE)
    new_ptr = normalize_external_link(
        ExternalLinkPointer(link_id, uri_clean, artifact_kind, lod_tier, concept_id=cid),
        concept_id=cid
    )
    existing_links = _read_links_for_concept(store_or_frame, cid)
    existing_match = next((lk for lk in existing_links if lk.target_uri == uri_clean), None)
    cur_bucket = bucket_for_link_count(len(existing_links), effective_cap)
    if not is_within_8_cap(next_bucket := step_uri_link_registration(cur_bucket, existing_match is not None)):
        raise ValueError(f"uri_link_idempotence_law violated for {cid!r}: bucket {next_bucket!r} exceeds 8-link cap")
    if existing_match is not None:
        return existing_match
    if cur_bucket == LinkCountBucket.LBAtCap8 or len(existing_links) >= effective_cap:
        return existing_links[-1] if existing_links else new_ptr
    _write_links_for_concept(store_or_frame, cid, [*existing_links, new_ptr][:effective_cap])
    return new_ptr


def _matches_lod_tier(link_tier: str, requested_tier: str | None) -> bool:
    if requested_tier is None or (req := requested_tier.strip().upper()) in ("", "*", "ALL"):
        return True
    tier_map = {"L0": LOD_0, "L1": LOD_1, "L2": LOD_2, "L3": LOD_3}
    return tier_map.get(link_tier.strip().upper(), link_tier.strip().upper()) == tier_map.get(req, req)


def resolve_external_links(
    store_or_frame: Any, concept_id: str | list[str] | tuple[str, ...] | None = None,
    lod_tier: str | None = LOD_1, *, max_links_per_node: int = MAX_LINKS_PER_NODE,
) -> list[ExternalLinkPointer]:
    """Resolves deduplicated `ExternalLinkPointer` records for `concept_id` at `lod_tier` (`<= 8` links)."""
    if not isinstance(max_links_per_node, int) or isinstance(max_links_per_node, bool) or max_links_per_node < 0:
        raise ValueError(f"max_links_per_node must be a non-negative int, got {max_links_per_node!r}")
    if (effective_cap := min(max_links_per_node, MAX_LINKS_PER_NODE)) == 0:
        return []
    if isinstance(concept_id, (list, tuple)):
        return [
            lk for item in concept_id if isinstance(item, str)
            and item.strip() for lk in resolve_external_links(
                store_or_frame, item.strip(), lod_tier,
                max_links_per_node=effective_cap
            )
        ]
    if isinstance(store_or_frame, dict) and "concept_id" in store_or_frame:
        frame_cid = str(store_or_frame.get("concept_id") or "").strip()
        if concept_id is None:
            concept_id = frame_cid
        elif isinstance(concept_id, str) and concept_id.upper() in (LOD_0, LOD_1, LOD_2, LOD_3, "L0", "L1", "L2", "L3") and concept_id != frame_cid:
            lod_tier, concept_id = concept_id, frame_cid
    if not isinstance(concept_id, str) or not concept_id.strip():
        return []
    all_links = _read_links_for_concept(store_or_frame, concept_id.strip())[:effective_cap]
    if not all_links or lod_tier is None or str(lod_tier).strip().upper() in ("", "*", "ALL"):
        return all_links[:effective_cap]
    if exact := [lk for lk in all_links if _matches_lod_tier(lk.lod_tier, lod_tier)]:
        return exact[:effective_cap]
    req_norm = {"L0": LOD_0, "L1": LOD_1, "L2": LOD_2, "L3": LOD_3}.get(str(lod_tier).strip().upper(), str(lod_tier).strip().upper())
    return all_links[:effective_cap] if req_norm in (LOD_2, LOD_3) else []


def is_commit_uri_state_valid(has_dangling_deleted_uri: bool, rename_pointers_updated: bool) -> bool:
    """Bend 2 Law 4 (`git_uri_integrity_law`, Stage 26 `uri_git_healer`)."""
    return (not bool(has_dangling_deleted_uri)) and bool(rename_pointers_updated)


def is_symbol_uri_anchored(has_ast_symbol_range: bool = True, uri_has_line_anchor: bool = True) -> bool:
    """Bend 2 Law (`ast_symbol_uri_anchor_law`): returns `True` iff `not has_ast_symbol_range or uri_has_line_anchor`."""
    return (not bool(has_ast_symbol_range)) or bool(uri_has_line_anchor)


ast_symbol_uri_anchor_law = is_symbol_uri_anchored


def _parse_ast_symbols(raw_ast: Any) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if isinstance(raw_ast, list):
        for item in (x for x in raw_ast if isinstance(x, dict)):
            name = str(item.get("name") or item.get("symbol") or item.get("id") or item.get("function") or "").strip()
            s_val = (
                item.get("start_line") if item.get("start_line") is not None
                else (item.get("start") if item.get("start") is not None else item.get("lineno"))
            )
            e_val = item.get("end_line") if item.get("end_line") is not None else (item.get("end") if item.get("end") is not None else s_val)
            if name and isinstance(s_val, int) and not isinstance(s_val, bool) and isinstance(e_val, int) and not isinstance(e_val, bool):
                out[name] = (int(s_val), int(e_val))
    elif isinstance(raw_ast, dict):
        for k, v in raw_ast.items():
            if not (name := str(k).strip()):
                continue
            if isinstance(v, (tuple, list)) and len(v) >= 2 and isinstance(v[0], int) and isinstance(v[1], int):
                out[name] = (int(v[0]), int(v[1]))
            elif isinstance(v, dict):
                s_val = v.get("start_line") if v.get("start_line") is not None else v.get("start")
                e_val = v.get("end_line") if v.get("end_line") is not None else (v.get("end") if v.get("end") is not None else s_val)
                if isinstance(s_val, int) and isinstance(e_val, int):
                    out[name] = (int(s_val), int(e_val))
            elif isinstance(v, str) and (rng := parse_line_anchor(v)) is not None:
                out[name] = rng
    return out


def _parse_git_diff_manifest(
    manifest: dict[str, Any] | list[dict[str, Any]],
) -> tuple[
    str | None, list[tuple[str, str, dict[str, tuple[int, int]]]],
    list[tuple[str, dict[str, tuple[int, int]]]],
    list[str], dict[str, tuple[str | None, dict[str, tuple[int, int]]]],
    dict[str, str], bool
]:
    commit_sha, raw_entries = None, []
    if isinstance(manifest, list):
        raw_entries.extend(x for x in manifest if isinstance(x, dict))
    elif isinstance(manifest, dict):
        sha_raw = manifest.get("commit_sha") or manifest.get("commit") or manifest.get("sha")
        commit_sha = str(sha_raw).strip() if isinstance(sha_raw, str) and sha_raw.strip() else None
        for list_key in ("changes", "files", "entries"):
            if isinstance(val := manifest.get(list_key), list):
                raw_entries.extend(x for x in val if isinstance(x, dict))
            elif isinstance(val, dict):
                raw_entries.extend(
                    {"path": p, **(spec if isinstance(spec, dict) else {"status": str(spec)})}
                    for p, spec in val.items()
                )
        if isinstance(manifest.get("file_hashes"), dict):
            raw_entries.extend(
                {"status": "A", "path": p, **(v if isinstance(v, dict) else {"file_hash": str(v)})}
                for p, v in manifest["file_hashes"].items()
            )
        for ren_k in ("renames", "renamed_files"):
            if isinstance(manifest.get(ren_k), dict):
                raw_entries.extend(
                    {"status": "R", "old_path": op, **(np if isinstance(np, dict) else {"new_path": np})}
                    for op, np in manifest[ren_k].items()
                )
            elif isinstance(manifest.get(ren_k), list):
                raw_entries.extend(
                    {"status": "R", **item} if isinstance(item, dict) else (
                        {"status": "R", "old_path": item[0], "new_path": item[1]}
                        if isinstance(item, (tuple, list))
                        and len(item) >= 2 else {}
                    )
                    for item in manifest[ren_k]
                )
        for del_key in ("deletes", "deleted", "removed"):
            if isinstance(manifest.get(del_key), (list, tuple, set)):
                raw_entries.extend(
                    {"status": "D", **item} if isinstance(item, dict) else {
                        "status": "D", "old_path": str(item),
                        "path": str(item)
                    }
                    for item in manifest[del_key]
                )
        for mod_key in ("modified", "added"):
            if isinstance(manifest.get(mod_key), dict):
                raw_entries.extend(
                    {"status": "M", "path": p, **(s if isinstance(s, dict) and "ast_symbols" in s else {"ast_symbols": s})}
                    for p, s in manifest[mod_key].items()
                )
            elif isinstance(manifest.get(mod_key), list):
                raw_entries.extend(
                    {"status": "M", **item} if isinstance(item, dict) else {"status": "M", "path": str(item)}
                    for item in manifest[mod_key]
                )
        if isinstance(manifest.get("ast_symbols_by_file"), dict):
            raw_entries.extend(
                {"status": "M", "path": p, "ast_symbols": s}
                for p, s in manifest["ast_symbols_by_file"].items()
                if s
            )
        if any(k in manifest for k in ("status", "old_path", "from_path", "new_path", "to_path", "path")):
            raw_entries.append(manifest)
    else:
        raise ValueError(f"git_diff_manifest must be a dict or list, got {type(manifest).__name__}")
    renames, ast_updates, deletes, candidates, manifest_hashes, all_renames_well_formed = [], [], [], {}, {}, True
    line_shifts = {
        str(k): int(v) for k, v in (
            (manifest.get("line_shifts") or manifest.get("line_deltas") or {})
            if isinstance(manifest, dict) else {}
        ).items()
        if isinstance(v, int)
    }
    for entry in (e for e in raw_entries if e):
        st_raw = str(entry.get("status") or entry.get("change_type") or entry.get("action") or "").strip().upper()
        old_p = str(
            entry.get("old_path") or entry.get("from_path")
            or entry.get("source_path") or entry.get("old_file")
            or entry.get("old_uri") or ""
        ).strip()
        new_p = str(
            entry.get("new_path") or entry.get("to_path")
            or entry.get("target_path") or entry.get("new_file")
            or entry.get("new_uri") or entry.get("path")
            or entry.get("file") or ""
        ).strip()
        if isinstance(
            sh := entry.get("line_shift", entry.get("line_delta")),
            int
        ) and (tp := (new_p or old_p)): line_shifts[tp] = int(sh)
        f_hash = str(
            entry.get("file_hash") or entry.get("sha256")
            or entry.get("hash") or entry.get("content_hash")
            or ""
        ).strip() or None
        syms = _parse_ast_symbols(entry.get("ast_symbols") or entry.get("symbols") or entry.get("line_ranges"))
        st_raw = st_raw or ("R" if (old_p and new_p and old_p != new_p) else ("D" if entry.get("deleted") else "M"))
        if st_raw.startswith("R"):
            all_renames_well_formed = all_renames_well_formed and bool(old_p and new_p and not entry.get("rename_failed"))
            if old_p:
                renames.append((old_p, new_p, syms))
                if syms:
                    ast_updates.extend(((new_p, syms), (old_p, syms)) if new_p else ((old_p, syms),))
        elif st_raw.startswith("D") or st_raw in ("REMOVED", "UNLINKED"):
            if (del_target := (old_p or new_p)) and del_target not in deletes:
                deletes.append(del_target)
        elif (target_p := (new_p or old_p)) and syms:
            ast_updates.append((target_p, syms))
        if not (st_raw.startswith("D") or st_raw in ("REMOVED", "UNLINKED")) and (cand_p := (new_p or old_p)):
            prev_h, prev_s = candidates.get(cand_p, (None, {}))
            candidates[cand_p] = (f_hash or prev_h, {**prev_s, **syms})
            if f_hash:
                manifest_hashes[norm_file_path(cand_p)] = f_hash
    return commit_sha, renames, ast_updates, deletes, candidates, manifest_hashes, line_shifts, all_renames_well_formed


def _resolve_ast_line_range(
    anchor: str, concept_id: str, hint_symbol: str,
    sym_map: dict[str, tuple[int, int]]
) -> tuple[int, int] | None:
    if not sym_map: return None
    anchor_sym = anchor.split(":", 1)[0].strip() if ":" in anchor else anchor.strip()
    for cand in (anchor_sym, hint_symbol.strip(), concept_id.strip(), concept_id.strip().rsplit(".", 1)[-1]):
        if cand and cand in sym_map: return sym_map[cand]
    for sym_name, rng in sym_map.items():
        if sym_name and (
            sym_name == anchor_sym or sym_name in (concept_id, hint_symbol)
            or (concept_id and concept_id in sym_name)
        ): return rng
    return next(iter(sym_map.values())) if (
        not hint_symbol and len(sym_map) == 1
        and parse_line_anchor(anchor) is not None
    ) else None


@dataclass
class _UriSyncState:
    """Explicit state machine tracking renamed URIs, refreshed AST line ranges, and dangling deletes."""
    renames: list[tuple[str, str, dict[str, tuple[int, int]]]]
    ast_updates: list[tuple[str, dict[str, tuple[int, int]]]]
    deletes: list[str]
    current_turn: int = 1
    cap: int = MAX_LINKS_PER_NODE
    manifest_hashes: dict[str, str] = field(default_factory=dict)
    line_shifts: dict[str, int] = field(default_factory=dict)
    renamed_uris: list[dict[str, str]] = field(default_factory=list)
    updated_line_uris: list[dict[str, str]] = field(default_factory=list)
    dangling_deleted_uris: list[str] = field(default_factory=list)
    affected_scopes: list[str] = field(default_factory=list)
    rename_status: dict[str, bool] = field(default_factory=dict)

    def mark_scope(self, cid: str) -> None:
        if cid and cid not in self.affected_scopes: self.affected_scopes.append(cid)

    def heal_uri(self, raw_uri: str, cid: str, *, hint_symbol: str = "", allow_bare_anchor: bool = False) -> str:
        scheme, orig_path, anchor = split_uri_parts(raw_uri)
        if not orig_path:
            return raw_uri
        path, was_ren, was_line, matched_any, combined_syms, applied_rules = orig_path, False, False, False, {}, set()
        for _ in range(max(1, len(self.renames))):
            step_changed = False
            for r_idx, (old_p, new_p, r_syms) in enumerate(self.renames):
                if r_idx not in applied_rules and paths_match(path, old_p):
                    applied_rules.add(r_idx)
                    matched_any = True
                    combined_syms.update(r_syms)
                    new_path = replace_renamed_path(path, old_p, new_p) if new_p else path
                    if new_p and new_path != path:
                        path, was_ren, step_changed = new_path, True, True
            if not step_changed:
                break
        if matched_any:
            self.rename_status[raw_uri] = self.rename_status.get(raw_uri, False) or was_ren
        for target_p, m_syms in self.ast_updates:
            if paths_match(path, target_p) or paths_match(orig_path, target_p):
                combined_syms.update(m_syms)
        if combined_syms and (anchor or allow_bare_anchor):
            if (new_rng := _resolve_ast_line_range(anchor, cid, hint_symbol, combined_syms)) is not None:
                if (new_anchor := f"L{new_rng[0]}-L{new_rng[1]}") != anchor and is_symbol_uri_anchored(bool(new_rng), bool(new_anchor)):
                    anchor, was_line = new_anchor, True
        if not was_line and anchor and (delta := next((d for kp, d in self.line_shifts.items() if paths_match(path, kp) or paths_match(orig_path, kp)), 0)):
            new_a = "-".join(
                f"{t[:1] if t[:1] in ('L', 'l') else ''}{max(1, int(t.lstrip('Ll')) + delta)}"
                if t.lstrip("Ll").isdigit() else t
                for t in anchor.split("-")
            )
            if new_a != anchor: anchor, was_line = new_a, True
        healed = f"{scheme}{path}#{anchor}" if anchor else f"{scheme}{path}"
        if healed != raw_uri:
            rec = {"old_uri": raw_uri, "new_uri": healed, "concept_id": cid}
            if was_ren and rec not in self.renamed_uris: self.renamed_uris.append(rec)
            self.mark_scope(cid)
            if was_line and rec not in self.updated_line_uris: self.updated_line_uris.append(rec)
            self.mark_scope(cid)
        if any(paths_match(p, del_p) for p in (orig_path, path) if p for del_p in self.deletes):
            if healed not in self.dangling_deleted_uris: self.dangling_deleted_uris.append(healed)
            self.mark_scope(cid)
        return healed

    def heal_link_list(self, raw_links: list[Any], cid: str) -> tuple[list[Any], bool]:
        changed, healed_list, seen_u = False, [], set()
        for lk in raw_links:
            if isinstance(lk, str):
                new_u = self.heal_uri(lk, cid)
                changed = changed or (new_u != lk)
                if new_u not in seen_u:
                    seen_u.add(new_u)
                    healed_list.append(new_u)
            elif isinstance(lk, dict):
                old_u = str(lk.get("target_uri") or lk.get("uri") or "").strip()
                meta = lk.get("metadata") if isinstance(lk.get("metadata"), dict) else {}
                hint = str(lk.get("symbol") or lk.get("ast_symbol") or meta.get("symbol") or "").strip()
                new_u = self.heal_uri(old_u, cid, hint_symbol=hint, allow_bare_anchor=bool(hint))
                if new_u != old_u:
                    changed = True
                    if isinstance(lk, ExternalLinkPointer):
                        lk_d = {**dict(lk), "link_id": None, "target_uri": new_u}
                        for sk in ("source_links", "external_uris"):
                            if sk in lk_d: lk_d[sk] = [new_u]
                        lk = ExternalLinkPointer(lk_d, concept_id=cid)
                    else:
                        if "target_uri" in lk or "uri" not in lk: lk["target_uri"] = new_u
                        if "uri" in lk: lk["uri"] = new_u
                        if "line_range" in lk and (rng := parse_line_anchor(split_uri_parts(new_u)[2])) is not None:
                            lk["line_range"] = list(rng) if isinstance(lk["line_range"], list) else rng
                        if isinstance(meta, dict) and "target_uri" in meta: meta["target_uri"] = new_u
                if new_u not in seen_u:
                    seen_u.add(new_u)
                    healed_list.append(lk)
        return healed_list[: self.cap], changed

    def heal_container(
        self, container: dict[str, Any], cid: str,
        *, is_rule: bool = False, hint_symbol: str = ""
    ) -> None:
        for fkey in ("files", "_table_files", "source_links", "external_uris"):
            if isinstance(container.get(fkey), list):
                container[fkey] = [self.heal_uri(x, cid) if isinstance(x, str) else x for x in container[fkey]]
            elif isinstance(container.get(fkey), str) and container[fkey]:
                container[fkey] = ", ".join(
                    self.heal_uri(p.strip(), cid) for p in container[fkey].split(",")
                    if p.strip()
                )
        if isinstance(fh := container.get("file_hashes"), dict) or (
            not is_rule and isinstance(container.get("files"), list)
            and self.manifest_hashes
        ):
            new_fh: dict[str, Any] = {}
            for old_k, old_v in list((fh if isinstance(fh, dict) else {}).items()):
                new_k = self.heal_uri(str(old_k), cid)
                m_hash = self.manifest_hashes.get(norm_file_path(new_k)) or self.manifest_hashes.get(norm_file_path(str(old_k)))
                new_v = m_hash if m_hash else old_v
                if new_k != old_k or new_v != old_v: self.mark_scope(cid)
                new_fh[new_k] = new_v
            if isinstance(container.get("files"), list):
                for f_item in container["files"]:
                    if isinstance(f_item, str) and (mh := self.manifest_hashes.get(norm_file_path(f_item))) and new_fh.get(f_item) != mh:
                        new_fh[f_item] = mh
                        self.mark_scope(cid)
            if new_fh or "file_hashes" in container: container["file_hashes"] = new_fh
        for ukey in ("file", "target_uri", "uri"):
            if isinstance(container.get(ukey), str) and container[ukey].strip():
                container[ukey] = self.heal_uri(
                    container[ukey], cid, hint_symbol=hint_symbol,
                    allow_bare_anchor=bool(hint_symbol)
                )
        if isinstance(container.get("external_links"), list):
            container["external_links"], l_changed = self.heal_link_list(container["external_links"], cid)
            if l_changed:
                if is_rule: container["updated_turn"] = int(self.current_turn)
                uris = [
                    str(x.get("target_uri") or x.get("uri"))
                    if isinstance(x, dict) else str(x)
                    for x in container["external_links"]
                ]
                for skey in ("source_links", "external_uris"):
                    if skey in container: container[skey] = list(uris)


def sync_git_diff_uris(
    store_or_graph: Any, git_diff_manifest: dict[str, Any] | list[dict[str, Any]], *,
    current_turn: int = 1, max_links_per_node: int = MAX_LINKS_PER_NODE,
) -> dict[str, Any]:
    """Stage 26 `uri_git_healer` — Auto-heals renamed file URIs, refreshes AST symbol line ranges,
    detects dangling deleted-file URIs across `store_or_graph`, and enforces Bend 2 Law 4 (`git_uri_integrity_law`)."""
    commit_sha, renames, ast_updates, deletes, candidates, manifest_hashes, line_shifts, all_renames_well_formed = _parse_git_diff_manifest(git_diff_manifest)
    calls_by_file = (
        git_diff_manifest.get("ast_calls_by_file")
        if isinstance(git_diff_manifest, dict)
        and isinstance(git_diff_manifest.get("ast_calls_by_file"), dict) else {}
    )
    is_sqlite = isinstance(store_or_graph, SQLiteGraphStore)
    graph = store_or_graph.load_graph() if is_sqlite else store_or_graph
    if not isinstance(graph, dict):
        raise ValueError(f"store_or_graph must be a SQLiteGraphStore or dict, got {type(store_or_graph).__name__}")
    infer_cross_repo_renames(graph, candidates, renames, ast_updates)
    state = _UriSyncState(
        renames, ast_updates, deletes, int(current_turn),
        min(max(1, int(max_links_per_node)), MAX_LINKS_PER_NODE),
        manifest_hashes, line_shifts
    )
    if isinstance(scopes := graph.get("scopes"), dict):
        for sid, scope in scopes.items():
            if not isinstance(scope, dict): continue
            cid = str(sid)
            state.heal_container(scope, cid)
            if isinstance(scope.get("attributes"), dict): state.heal_container(scope["attributes"], cid)
            for rule in (scope.get("rules") if isinstance(scope.get("rules"), list) else []):
                if isinstance(rule, dict): state.heal_container(rule, cid, is_rule=True)
            if cid in state.affected_scopes and commit_sha is not None:
                scope["last_commit_sha"] = commit_sha
                if isinstance(scope.get("attributes"), dict) and "last_commit_sha" in scope["attributes"]:
                    scope["attributes"]["last_commit_sha"] = commit_sha
    if isinstance(ext_top := graph.get("external_links"), dict):
        for nid, lk_list in list(ext_top.items()):
            if isinstance(lk_list, list): ext_top[nid], _ = state.heal_link_list(lk_list, str(nid))
    elif isinstance(ext_top, list) or "concept_id" in graph:
        state.heal_container(graph, str(graph.get("concept_id") or graph.get("id") or "root"))
    raw_arts = graph.get("lattice_artifacts")
    art_items = raw_arts.items() if isinstance(raw_arts, dict) else enumerate(raw_arts if isinstance(raw_arts, list) else [])
    for ak, art in art_items:
        if isinstance(art, dict):
            acid = str(
                art.get("concept_id") or art.get("scope_id")
                or art.get("scope") or art.get("name")
                or art.get("id") or ak
            )
            asym = str(art.get("name") or art.get("id") or "")
            state.heal_container(art, acid, hint_symbol=asym)
            if isinstance(art.get("dimensions"), dict): state.heal_container(art["dimensions"], acid, hint_symbol=asym)
            if calls_by_file and (af_clean := norm_file_path(str(art.get("file") or ""))) and (f_calls := next(
                (v for k, v in calls_by_file.items() if paths_match(af_clean, k) and isinstance(v, dict)),
                None
            )):
                if asym in f_calls and f_calls[asym]: art["calls"] = list(f_calls[asym])
            if (af := str(art.get("file") or "").strip()) and "#" in af and acid and acid != "global_defaults":
                if not isinstance(graph.get("external_links"), dict): graph["external_links"] = {}
                full_u, base_u = (af if af.startswith("uri://") else f"uri://file/{af.lstrip('./')}"), (af if af.startswith("uri://") else f"uri://file/{af.lstrip('./')}").split("#", 1)[0]
                ext_list = [
                    x for x in graph.setdefault("external_links", {}).setdefault(acid, [])
                    if not (
                        isinstance(x, dict) and str(x.get("symbol") or "") == asym
                        and str(x.get("target_uri") or x.get("uri") or "").split("#", 1)[0] == base_u
                        and str(x.get("target_uri") or x.get("uri") or "") != full_u
                    )
                ]
                if len(ext_list) < state.cap and not any(
                    (isinstance(x, dict) and str(x.get("target_uri") or x.get("uri") or "") == full_u)
                    or x == full_u for x in ext_list
                ):
                    ext_list.append(ExternalLinkPointer({"target_uri": full_u, "uri": full_u, "symbol": asym, "lod_tier": LOD_2}, concept_id=acid))
                graph["external_links"][acid] = ext_list
    if is_sqlite:
        graph.setdefault("external_links", {})
        store_or_graph.save_graph(graph)
    rename_pointers_updated = bool(all_renames_well_formed and all(state.rename_status.values()))
    has_dangling_deleted_uri = bool(state.dangling_deleted_uris)
    upd_u = list(dict.fromkeys(str(r.get("new_uri")) for r in (*state.renamed_uris, *state.updated_line_uris) if isinstance(r, dict) and r.get("new_uri")))
    anchors_ok = all("#" in str(r.get("new_uri") or "") for r in state.updated_line_uris if isinstance(r, dict))
    is_valid = is_commit_uri_state_valid(has_dangling_deleted_uri, rename_pointers_updated) and is_symbol_uri_anchored(bool(state.updated_line_uris), anchors_ok)
    return {
        "is_valid": is_valid, "has_dangling_deleted_uri": has_dangling_deleted_uri,
        "rename_pointers_updated": rename_pointers_updated, "updated_uris": upd_u,
        "dangling_uri_pointers": len(state.dangling_deleted_uris),
        "dangling_deleted_uris": state.dangling_deleted_uris, "renamed_uris": state.renamed_uris,
        "updated_line_uris": state.updated_line_uris, "affected_scopes": state.affected_scopes, "commit_sha": commit_sha,
    }


for _m, _f in (
    ("register_external_link", register_external_link),
    ("resolve_external_links", resolve_external_links),
    ("sync_git_diff_uris", sync_git_diff_uris)
):
    if not hasattr(SQLiteGraphStore, _m):
        setattr(SQLiteGraphStore, _m, _f)
