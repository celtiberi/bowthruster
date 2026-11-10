"""Spec 24A & Stage 26 — `ai_memory.uri_resolver`: External Link Registration, Resolution, & Git Diff URI Healer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from bowthruster.graph_core import SQLiteGraphStore

from .schema import (
    LOD_0, LOD_1, LOD_2, LOD_3, MAX_LINKS_PER_NODE, ExternalLinkPointer, _is_int, _pick, get_concept_frame,
    infer_cross_repo_renames, norm_file_path, normalize_external_link, parse_line_anchor, paths_match,
    replace_renamed_path, split_uri_parts, upsert_concept_frame)

class LinkCountBucket(str, Enum):
    LBZero = "LBZero"
    LB1To7 = "LB1To7"
    LBAtCap8 = "LBAtCap8"

LBZero, LB1To7, LBAtCap8 = LinkCountBucket.LBZero, LinkCountBucket.LB1To7, LinkCountBucket.LBAtCap8
_TIER_NORM_MAP = {"L0": LOD_0, "L1": LOD_1, "L2": LOD_2, "L3": LOD_3}

def bucket_for_link_count(count: int, max_links_per_node: int = MAX_LINKS_PER_NODE) -> LinkCountBucket:
    cap = min(max(1, int(max_links_per_node)), MAX_LINKS_PER_NODE)
    return LBZero if count <= 0 else (LBAtCap8 if count >= cap else LB1To7)

def _norm_bucket(b: Any, max_links_per_node: int = MAX_LINKS_PER_NODE) -> LinkCountBucket:
    if isinstance(b, LinkCountBucket):
        return b
    u = b["$"] if "$" in b else b
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

from .cue_extractor import _read_links_for_concept, _write_links_for_concept, verify_uri_resolver_entry

def register_external_link(store_or_frame: Any, concept_id: str | None = None, target_uri: str | None = None,
    *, artifact_kind: str = "doc", lod_tier: str = LOD_1,
    link_id: str | None = None, max_links_per_node: int = MAX_LINKS_PER_NODE) -> ExternalLinkPointer:
    if target_uri is None and isinstance(store_or_frame, dict) and "concept_id" in store_or_frame:
        if isinstance(concept_id, str):
            target_uri, concept_id = concept_id, str(store_or_frame["concept_id"])
    if not isinstance(concept_id, str) or not concept_id.strip():
        raise ValueError(f"concept_id must be a non-empty str, got {concept_id!r}")
    if not isinstance(target_uri, str) or not target_uri.strip():
        raise ValueError(f"target_uri must be a non-empty str, got {target_uri!r}")
    if not _is_int(max_links_per_node) or max_links_per_node <= 0:
        raise ValueError(f"max_links_per_node must be a positive int, got {max_links_per_node!r}")
    cid, uri_clean, cap = concept_id.strip(), target_uri.strip(), min(max_links_per_node, MAX_LINKS_PER_NODE)
    new_ptr = normalize_external_link(
        ExternalLinkPointer(link_id, uri_clean, artifact_kind, lod_tier, concept_id=cid), concept_id=cid)
    existing = _read_links_for_concept(store_or_frame, cid)
    match = next((lk for lk in existing if lk.target_uri == uri_clean), None)
    cur_b = bucket_for_link_count(len(existing), cap)
    if not is_within_8_cap(next_b := step_uri_link_registration(cur_b, match is not None)):
        raise ValueError(f"uri_link_idempotence_law violated for {cid!r}: bucket {next_b!r} exceeds 8-link cap")
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

def resolve_external_links(store_or_frame: Any, concept_id: str | list[str] | tuple[str, ...] | None = None,
    lod_tier: str | None = LOD_1, *, max_links_per_node: int = MAX_LINKS_PER_NODE) -> list[ExternalLinkPointer]:
    if not _is_int(max_links_per_node) or max_links_per_node < 0:
        raise ValueError(f"max_links_per_node must be a non-negative int, got {max_links_per_node!r}")
    if (cap := min(max_links_per_node, MAX_LINKS_PER_NODE)) == 0:
        return []
    if isinstance(concept_id, (list, tuple)):
        return [lk for item in concept_id if isinstance(item, str) and item.strip()
            for lk in resolve_external_links(store_or_frame, item.strip(), lod_tier, max_links_per_node=cap)]
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

def is_commit_uri_state_valid(has_dangling_deleted_uri: bool, rename_pointers_updated: bool) -> bool:
    return (not bool(has_dangling_deleted_uri)) and bool(rename_pointers_updated)

def is_symbol_uri_anchored(has_ast_symbol_range: bool = True, uri_has_line_anchor: bool = True) -> bool:
    return (not bool(has_ast_symbol_range)) or bool(uri_has_line_anchor)

ast_symbol_uri_anchor_law = is_symbol_uri_anchored

def _parse_ast_symbols(raw_ast: Any) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    pairs = (
        [
            (str(_pick(x, "name", "symbol", "id", "function", default="")).strip(), x)
            for x in raw_ast
            if isinstance(x, dict)
        ]
        if isinstance(raw_ast, list)
        else (list(raw_ast.items()) if isinstance(raw_ast, dict) else [])
    )
    for k, v in pairs:
        if not (name := str(k).strip()):
            continue
        if isinstance(v, (tuple, list)) and len(v) >= 2 and _is_int(v[0]) and _is_int(v[1]):
            out[name] = (int(v[0]), int(v[1]))
        elif isinstance(v, dict):
            s_val = _pick(v, "start_line", "start", "lineno")
            e_val = _pick(v, "end_line", "end", default=s_val)
            if _is_int(s_val) and _is_int(e_val):
                out[name] = (int(s_val), int(e_val))
        elif isinstance(v, str) and (rng := parse_line_anchor(v)) is not None:
            out[name] = rng
    return out

def _extract_raw_manifest_entries(
    manifest: dict[str, Any] | list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    if isinstance(manifest, list):
        return None, [x for x in manifest if isinstance(x, dict)]
    if not isinstance(manifest, dict):
        raise ValueError(f"git_diff_manifest must be a dict or list, got {type(manifest).__name__}")
    sha_raw = _pick(manifest, "commit_sha", "commit", "sha")
    commit_sha = str(sha_raw).strip() if isinstance(sha_raw, str) and sha_raw.strip() else None
    out: list[dict[str, Any]] = []
    for k in ("changes", "files", "entries"):
        if isinstance(val := manifest.get(k), list):
            out.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.extend({"path": p, **(s if s else {"status": str(s)})} for p, s in val.items())
    if isinstance(fh := manifest.get("file_hashes"), dict):
        out.extend({"status": "A", "path": p, **(v if isinstance(v, dict) else {"file_hash": str(v)})} for p, v
            in fh.items())
    for k in ("renames", "renamed_files"):
        if isinstance(rv := manifest.get(k), dict):
            out.extend(
                {"status": "R", "old_path": op, **(np if isinstance(np, dict)
                    else {"new_path": np})} for op, np in rv.items()
            )
        elif isinstance(rv, list):
            out.extend(
                {"status": "R", **x} if isinstance(x, dict) else {"status": "R", "old_path": x[0], "new_path": x[1]}
                for x in rv if isinstance(x, dict) or (isinstance(x, (tuple, list)) and len(x) >= 2))
    for k in ("deletes", "deleted", "removed"):
        for x in (manifest.get(k) if isinstance(manifest.get(k), (list, tuple, set)) else []):
            out.append({"status": "D", **x} if isinstance(x, dict) else {"status": "D", "old_path": str(x),
                "path": str(x)})
    for k in ("modified", "added"):
        if isinstance(mv := manifest.get(k), dict):
            out.extend(
                {"status": "M", "path": p, **(s if "ast_symbols" in s else {"ast_symbols": s})}
                for p, s in mv.items())
        elif isinstance(mv, list):
            out.extend({"status": "M", **x} if isinstance(x, dict) else {"status": "M", "path": str(x)} for x in mv)
    if isinstance(abf := manifest.get("ast_symbols_by_file"), dict):
        out.extend({"status": "M", "path": p, "ast_symbols": s} for p, s in abf.items() if s)
    if any(k in manifest for k in ("status", "old_path", "from_path", "new_path", "to_path", "path")):
        out.append(manifest)
    return commit_sha, out

def _parse_git_diff_manifest(manifest: dict[str, Any] | list[dict[str, Any]]) -> tuple[Any, ...]:
    commit_sha, raw_entries = _extract_raw_manifest_entries(manifest)
    renames, ast_updates, deletes, candidates, manifest_hashes, renames_ok = [], [], [], {}, {}, True
    raw_sh = _pick(manifest, "line_shifts", "line_deltas", default={}) if isinstance(manifest, dict) else {}
    line_shifts = {
        str(k): int(v) for k, v in (raw_sh if isinstance(raw_sh, dict) else {}).items() if isinstance(v, int)
    }
    for entry in (e for e in raw_entries if e):
        st = str(_pick(entry, "status", "change_type", "action", default="")).strip().upper()
        old_p = str(_pick(entry, "old_path", "from_path", "source_path", "old_file", "old_uri", default="")).strip()
        new_p = str(
            _pick(entry, "new_path", "to_path", "target_path", "new_file", "new_uri", "path", "file", default="")
        ).strip()
        if isinstance(sh := _pick(entry, "line_shift", "line_delta"), int) and (tp := (new_p or old_p)):
            line_shifts[tp] = int(sh)
        f_hash = str(_pick(entry, "file_hash", "sha256", "hash", "content_hash", default="")).strip() or None
        syms = _parse_ast_symbols(_pick(entry, "ast_symbols", "symbols", "line_ranges"))
        st = st or ("R" if (old_p and new_p and old_p != new_p) else ("D" if entry.get("deleted") else "M"))
        is_del = st.startswith("D") or st in ("REMOVED", "UNLINKED")
        if st.startswith("R"):
            renames_ok = renames_ok and bool(old_p and new_p and not entry.get("rename_failed"))
            if old_p:
                renames.append((old_p, new_p, syms))
                if syms:
                    ast_updates.extend(((new_p, syms), (old_p, syms)) if new_p else ((old_p, syms),))
        elif is_del:
            if (dt := (old_p or new_p)) and dt not in deletes:
                deletes.append(dt)
        elif (tp := (new_p or old_p)) and syms:
            ast_updates.append((tp, syms))
        if not is_del and (cp := (new_p or old_p)):
            prev_h, prev_s = candidates.get(cp, (None, {}))
            candidates[cp] = (f_hash or prev_h, {**prev_s, **syms})
            if f_hash:
                manifest_hashes[norm_file_path(cp)] = f_hash
    return commit_sha, renames, ast_updates, deletes, candidates, manifest_hashes, line_shifts, renames_ok

def _resolve_ast_line_range(anchor: str, concept_id: str, hint_symbol: str, sym_map: dict[str, tuple[int, int]],
    ) -> tuple[int, int] | None:
    if not sym_map:
        return None
    asym = anchor.split(":", 1)[0].strip() if ":" in anchor else anchor.strip()
    for cand in (asym, hint_symbol.strip(), concept_id.strip(), concept_id.strip().rsplit(".", 1)[-1]):
        if cand and cand in sym_map:
            return sym_map[cand]
    for sname, rng in sym_map.items():
        if sname and (sname == asym or sname in (concept_id, hint_symbol) or (concept_id and concept_id in sname)):
            return rng
    return next(iter(sym_map.values())) if (not hint_symbol and len(sym_map) == 1
        and parse_line_anchor(anchor)) else None

class _UriSyncState:
    def __init__(self, renames: list[Any], ast_updates: list[Any], deletes: list[str],
        current_turn: int, cap: int, manifest_hashes: dict[str, str], line_shifts: dict[str, int]) -> None:
        self.renames, self.ast_updates, self.deletes = renames, ast_updates, deletes
        self.current_turn, self.cap = current_turn, cap
        self.manifest_hashes, self.line_shifts = manifest_hashes, line_shifts
        self.renamed_uris, self.updated_line_uris, self.dangling_deleted_uris, self.affected_scopes = [], [], [], []
        self.rename_status: dict[str, bool] = {}
    def mark_scope(self, cid: str) -> None:
        if cid and cid not in self.affected_scopes:
            self.affected_scopes.append(cid)
    def heal_uri(self, raw_uri: str, cid: str, *, hint_symbol: str = "", allow_bare_anchor: bool = False) -> str:
        scheme, orig_path, anchor = split_uri_parts(raw_uri)
        if not orig_path:
            return raw_uri
        path, was_ren, was_line, matched_any, combined_syms, applied = orig_path, False, False, False, {}, set()
        for _ in range(max(1, len(self.renames))):
            step_changed = False
            for r_idx, (old_p, new_p, r_syms) in enumerate(self.renames):
                if r_idx not in applied and paths_match(path, old_p):
                    applied.add(r_idx)
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
                new_anchor = f"L{new_rng[0]}-L{new_rng[1]}"
                if new_anchor != anchor and is_symbol_uri_anchored(bool(new_rng), bool(new_anchor)):
                    anchor, was_line = new_anchor, True
        delta = next((d for kp, d in self.line_shifts.items() if paths_match(path, kp) or paths_match(orig_path, kp)),
            0)
        if not was_line and anchor and delta:
            new_a = "-".join(f"{t[:1] if t[:1] in ('L', 'l') else ''}{max(1, int(t.lstrip('Ll')) + delta)}"
                if t.lstrip("Ll").isdigit() else t for t in anchor.split("-"))
            if new_a != anchor:
                anchor, was_line = new_a, True
        healed = f"{scheme}{path}#{anchor}" if anchor else f"{scheme}{path}"
        if healed != raw_uri:
            rec = {"old_uri": raw_uri, "new_uri": healed, "concept_id": cid}
            if was_ren and rec not in self.renamed_uris:
                self.renamed_uris.append(rec)
            if was_line and rec not in self.updated_line_uris:
                self.updated_line_uris.append(rec)
            self.mark_scope(cid)
        if any(paths_match(p, del_p) for p in (orig_path, path) if p for del_p in self.deletes):
            if healed not in self.dangling_deleted_uris:
                self.dangling_deleted_uris.append(healed)
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
                old_u = str(_pick(lk, "target_uri", "uri", default="")).strip()
                meta = (lk.get("metadata") or {})
                hint = str(_pick(lk, "symbol", "ast_symbol", default=meta.get("symbol") or "")).strip()
                new_u = self.heal_uri(old_u, cid, hint_symbol=hint, allow_bare_anchor=bool(hint))
                if new_u != old_u:
                    changed = True
                    if isinstance(lk, ExternalLinkPointer):
                        lk_d = {**dict(lk), "link_id": None, "target_uri": new_u}
                        for sk in (k for k in ("source_links", "external_uris") if k in lk_d):
                            lk_d[sk] = [new_u]
                        lk = ExternalLinkPointer(lk_d, concept_id=cid)
                    else:
                        if "target_uri" in lk or "uri" not in lk:
                            lk["target_uri"] = new_u
                        if "uri" in lk:
                            lk["uri"] = new_u
                        if "line_range" in lk and (rng := parse_line_anchor(split_uri_parts(new_u)[2])) is not None:
                            lk["line_range"] = list(rng) if isinstance(lk["line_range"], list) else rng
                        if "target_uri" in meta:
                            meta["target_uri"] = new_u
                if new_u not in seen_u:
                    seen_u.add(new_u)
                    healed_list.append(lk)
        return healed_list[: self.cap], changed
    def heal_container(self, container: dict[str, Any], cid: str, *, is_rule: bool = False, hint_symbol: str = "",
        ) -> None:
        for fkey in ("files", "_table_files", "source_links", "external_uris"):
            if isinstance(fval := container.get(fkey), list):
                container[fkey] = [self.heal_uri(x, cid) if isinstance(x, str) else x for x in fval]
            elif isinstance(fval, str) and fval:
                container[fkey] = ", ".join(self.heal_uri(p.strip(), cid) for p in fval.split(",") if p.strip())
        fh = container.get("file_hashes")
        if isinstance(fh, dict) or (not is_rule and isinstance(container.get("files"), list) and self.manifest_hashes):
            new_fh: dict[str, Any] = {}
            for old_k, old_v in list((fh if isinstance(fh, dict) else {}).items()):
                new_k = self.heal_uri(str(old_k), cid)
                mh = self.manifest_hashes.get(norm_file_path(new_k)) or self.manifest_hashes.get(
                    norm_file_path(str(old_k)))
                new_v = mh if mh else old_v
                if new_k != old_k or new_v != old_v:
                    self.mark_scope(cid)
                new_fh[new_k] = new_v
            for f_item in ((container.get("files") or [])):
                if isinstance(f_item, str) and (mh := self.manifest_hashes.get(norm_file_path(f_item))):
                    if new_fh.get(f_item) != mh:
                        new_fh[f_item] = mh
                        self.mark_scope(cid)
            if new_fh or "file_hashes" in container:
                container["file_hashes"] = new_fh
        for ukey in ("file", "target_uri", "uri"):
            if isinstance(uval := container.get(ukey), str) and uval.strip():
                container[ukey] = self.heal_uri(uval, cid, hint_symbol=hint_symbol, allow_bare_anchor=bool(hint_symbol))
        if isinstance(ext_links := container.get("external_links"), list):
            container["external_links"], l_changed = self.heal_link_list(ext_links, cid)
            if l_changed:
                if is_rule:
                    container["updated_turn"] = int(self.current_turn)
                uris = [str(_pick(x, "target_uri", "uri", default="")) if isinstance(x, dict) else str(x)
                    for x in container["external_links"]]
                for skey in (k for k in ("source_links", "external_uris") if k in container):
                    container[skey] = list(uris)

def _sync_lattice_artifact(
    art: dict[str, Any], ak: Any, graph: dict[str, Any], state: _UriSyncState, calls_by_file: dict[str, Any]) -> None:
    acid = str(_pick(art, "concept_id", "scope_id", "scope", "name", "id", default=ak))
    asym = str(_pick(art, "name", "id", default=""))
    state.heal_container(art, acid, hint_symbol=asym)
    if isinstance(dims := art.get("dimensions"), dict):
        state.heal_container(dims, acid, hint_symbol=asym)
    if calls_by_file and (af_clean := norm_file_path(str(art.get("file") or ""))):
        f_calls = next((v for k, v in calls_by_file.items() if paths_match(af_clean, k) and isinstance(v, dict)), None)
        if f_calls is not None and asym in f_calls:
            art["calls"] = list(f_calls[asym])
    if (af := str(art.get("file") or "").strip()) and "#" in af and acid and acid != "global_defaults":
        if not isinstance(graph.get("external_links"), dict):
            graph["external_links"] = {}
        full_u = af if af.startswith("uri://") else f"uri://file/{af.lstrip('./')}"
        base_u = full_u.split("#", 1)[0]
        ext_list = [
            x for x in graph.setdefault("external_links", {}).setdefault(acid, [])
            if not (
                isinstance(x, dict) and str(x.get("symbol") or "") == asym
                and (xu := str(_pick(x, "target_uri", "uri", default=""))).split("#", 1)[0] == base_u and xu != full_u
            )
        ]
        has_full = any(
            (isinstance(x, dict) and str(_pick(x, "target_uri", "uri", default="")) == full_u) or x == full_u
            for x in ext_list)
        if len(ext_list) < state.cap and not has_full:
            ptr_spec = {"target_uri": full_u, "uri": full_u, "symbol": asym, "lod_tier": LOD_2}
            ext_list.append(ExternalLinkPointer(ptr_spec, concept_id=acid))
        graph["external_links"][acid] = ext_list

def sync_git_diff_uris(store_or_graph: Any, git_diff_manifest: dict[str, Any] | list[dict[str, Any]],
    *, current_turn: int = 1, max_links_per_node: int = MAX_LINKS_PER_NODE) -> dict[str, Any]:
    commit_sha, renames, ast_updates, deletes, candidates, manifest_hashes, line_shifts, renames_ok = (
        _parse_git_diff_manifest(git_diff_manifest))
    calls_by_file = (git_diff_manifest["ast_calls_by_file"]
        if isinstance(git_diff_manifest, dict) and isinstance(git_diff_manifest.get("ast_calls_by_file"), dict)
        else {})
    is_sqlite = isinstance(store_or_graph, SQLiteGraphStore)
    graph = store_or_graph.load_graph() if is_sqlite else store_or_graph
    if not isinstance(graph, dict):
        raise ValueError(f"store_or_graph must be a SQLiteGraphStore or dict, got {type(store_or_graph).__name__}")
    infer_cross_repo_renames(graph, candidates, renames, ast_updates)
    state = _UriSyncState(renames, ast_updates, deletes, int(current_turn),
        min(max(1, int(max_links_per_node)), MAX_LINKS_PER_NODE), manifest_hashes, line_shifts)
    for sid, scope in ((graph.get("scopes") or {}).items()):
        if scope:
            cid = str(sid)
            state.heal_container(scope, cid)
            if isinstance(attrs := scope.get("attributes"), dict):
                state.heal_container(attrs, cid)
            for rule in (r for r in (scope.get("rules") or [])):
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
    art_items = raw_arts.items() if isinstance(raw_arts, dict) else enumerate(raw_arts if isinstance(raw_arts, list)
        else [])
    for ak, art in art_items:
        if art:
            _sync_lattice_artifact(art, ak, graph, state, calls_by_file)
    if is_sqlite:
        graph.setdefault("external_links", {})
        store_or_graph.save_graph(graph)
    rename_pointers_updated = bool(renames_ok and all(state.rename_status.values()))
    has_dangling_deleted_uri = bool(state.dangling_deleted_uris)
    upd_u = list(dict.fromkeys(str(r["new_uri"]) for r in (*state.renamed_uris, *state.updated_line_uris)
        if r.get("new_uri") ))
    anchors_ok = all("#" in str(r.get("new_uri") or "") for r in state.updated_line_uris)
    is_valid = is_commit_uri_state_valid(has_dangling_deleted_uri, rename_pointers_updated) and is_symbol_uri_anchored(
        bool(state.updated_line_uris), anchors_ok)
    return {"is_valid": is_valid, "has_dangling_deleted_uri": has_dangling_deleted_uri,
        "rename_pointers_updated": rename_pointers_updated, "updated_uris": upd_u,
        "dangling_uri_pointers": len(state.dangling_deleted_uris),
        "dangling_deleted_uris": state.dangling_deleted_uris, "renamed_uris": state.renamed_uris,
        "updated_line_uris": state.updated_line_uris, "affected_scopes": state.affected_scopes,
        "commit_sha": commit_sha}

for _m, _f in (("register_external_link", register_external_link), ("resolve_external_links", resolve_external_links),
    ("sync_git_diff_uris", sync_git_diff_uris)):
    if not hasattr(SQLiteGraphStore, _m):
        setattr(SQLiteGraphStore, _m, _f)
