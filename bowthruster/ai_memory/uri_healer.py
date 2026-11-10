"""Git Diff URI Healing State Machine & Lattice Artifact Healer (`ai_memory/uri_healer.py`)."""

from __future__ import annotations

from typing import Any

from .schema import LOD_2, ExternalLinkPointer, _pick
from .uri_manifest import (
    _resolve_ast_line_range,
    norm_file_path,
    parse_line_anchor,
    paths_match,
    replace_renamed_path,
    split_uri_parts,
)


def is_symbol_uri_anchored(
    has_ast_symbol_range: bool = True,
    uri_has_line_anchor: bool = True,
) -> bool:
    return (not bool(has_ast_symbol_range)) or bool(uri_has_line_anchor)


class _UriSyncState:
    def __init__(
        self,
        renames: list[Any],
        ast_updates: list[Any],
        deletes: list[str],
        current_turn: int,
        cap: int,
        manifest_hashes: dict[str, str],
        line_shifts: dict[str, int],
    ) -> None:
        self.renames, self.ast_updates, self.deletes = renames, ast_updates, deletes
        self.current_turn, self.cap = current_turn, cap
        self.manifest_hashes, self.line_shifts = manifest_hashes, line_shifts
        self.renamed_uris: list[dict[str, str]] = []
        self.updated_line_uris: list[dict[str, str]] = []
        self.dangling_deleted_uris: list[str] = []
        self.affected_scopes: list[str] = []
        self.rename_status: dict[str, bool] = {}

    def mark_scope(self, cid: str) -> None:
        if cid and cid not in self.affected_scopes:
            self.affected_scopes.append(cid)

    def heal_uri(
        self,
        raw_uri: str,
        cid: str,
        *,
        hint_symbol: str = "",
        allow_bare_anchor: bool = False,
    ) -> str:
        scheme, orig_path, anchor = split_uri_parts(raw_uri)
        if not orig_path:
            return raw_uri
        path = orig_path
        was_ren, was_line, matched_any = False, False, False
        combined_syms: dict[str, tuple[int, int]] = {}
        applied: set[int] = set()
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
            new_rng = _resolve_ast_line_range(anchor, cid, hint_symbol, combined_syms)
            if new_rng is not None:
                new_anchor = f"L{new_rng[0]}-L{new_rng[1]}"
                if new_anchor != anchor and is_symbol_uri_anchored(bool(new_rng), bool(new_anchor)):
                    anchor, was_line = new_anchor, True
        delta = next(
            (
                d
                for kp, d in self.line_shifts.items()
                if paths_match(path, kp) or paths_match(orig_path, kp)
            ),
            0,
        )
        if not was_line and anchor and delta:
            new_a = "-".join(
                f"{t[:1] if t[:1] in ('L', 'l') else ''}{max(1, int(t.lstrip('Ll')) + delta)}"
                if t.lstrip("Ll").isdigit()
                else t
                for t in anchor.split("-")
            )
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
        is_deleted = any(
            paths_match(p, del_p)
            for p in (orig_path, path)
            if p
            for del_p in self.deletes
        )
        if is_deleted:
            if healed not in self.dangling_deleted_uris:
                self.dangling_deleted_uris.append(healed)
            self.mark_scope(cid)
        return healed

    def heal_link_list(self, raw_links: list[Any], cid: str) -> tuple[list[Any], bool]:
        changed = False
        healed_list: list[Any] = []
        seen_u: set[str] = set()
        for lk in raw_links:
            if isinstance(lk, str):
                new_u = self.heal_uri(lk, cid)
                changed = changed or (new_u != lk)
                if new_u not in seen_u:
                    seen_u.add(new_u)
                    healed_list.append(new_u)
            elif isinstance(lk, dict):
                old_u = str(_pick(lk, "target_uri", "uri", default="")).strip()
                meta = lk.get("metadata") or {}
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
                        rng = parse_line_anchor(split_uri_parts(new_u)[2])
                        if "line_range" in lk and rng is not None:
                            lk["line_range"] = list(rng) if isinstance(lk["line_range"], list) else rng
                        if "target_uri" in meta:
                            meta["target_uri"] = new_u
                if new_u not in seen_u:
                    seen_u.add(new_u)
                    healed_list.append(lk)
        return healed_list[: self.cap], changed

    def heal_container(
        self,
        container: dict[str, Any],
        cid: str,
        *,
        is_rule: bool = False,
        hint_symbol: str = "",
    ) -> None:
        for fkey in ("files", "_table_files", "source_links", "external_uris"):
            if isinstance(fval := container.get(fkey), list):
                container[fkey] = [
                    self.heal_uri(x, cid) if isinstance(x, str) else x for x in fval
                ]
            elif isinstance(fval, str) and fval:
                container[fkey] = ", ".join(
                    self.heal_uri(p.strip(), cid) for p in fval.split(",") if p.strip()
                )
        fh = container.get("file_hashes")
        has_files_list = not is_rule and isinstance(container.get("files"), list)
        if isinstance(fh, dict) or (has_files_list and self.manifest_hashes):
            new_fh: dict[str, Any] = {}
            for old_k, old_v in list((fh if isinstance(fh, dict) else {}).items()):
                new_k = self.heal_uri(str(old_k), cid)
                mh = self.manifest_hashes.get(norm_file_path(new_k)) or self.manifest_hashes.get(
                    norm_file_path(str(old_k))
                )
                new_v = mh if mh else old_v
                if new_k != old_k or new_v != old_v:
                    self.mark_scope(cid)
                new_fh[new_k] = new_v
            for f_item in container.get("files") or []:
                if isinstance(f_item, str) and (mh := self.manifest_hashes.get(norm_file_path(f_item))):
                    if new_fh.get(f_item) != mh:
                        new_fh[f_item] = mh
                        self.mark_scope(cid)
            if new_fh or "file_hashes" in container:
                container["file_hashes"] = new_fh
        for ukey in ("file", "target_uri", "uri"):
            if isinstance(uval := container.get(ukey), str) and uval.strip():
                container[ukey] = self.heal_uri(
                    uval, cid, hint_symbol=hint_symbol, allow_bare_anchor=bool(hint_symbol)
                )
        if isinstance(ext_links := container.get("external_links"), list):
            container["external_links"], l_changed = self.heal_link_list(ext_links, cid)
            if l_changed:
                if is_rule:
                    container["updated_turn"] = int(self.current_turn)
                uris = [
                    str(_pick(x, "target_uri", "uri", default="")) if isinstance(x, dict) else str(x)
                    for x in container["external_links"]
                ]
                for skey in (k for k in ("source_links", "external_uris") if k in container):
                    container[skey] = list(uris)
