"""URI Path Normalization and Git Diff Manifest Parsing Helpers (`ai_memory/uri_manifest.py`)."""

from __future__ import annotations

from typing import Any


def _pick_val(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if (v := d.get(k)) is not None and v != "":
            return v
    return next((d[k] for k in keys if k in d and d[k] is not None), default)


def _is_int_val(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def split_uri_parts(raw_uri: str) -> tuple[str, str, str]:
    s = str(raw_uri or "").strip()
    base, anchor = s.split("#", 1) if "#" in s else (s, "")
    for prefix in ("uri://file/", "file:///", "file://"):
        if base.startswith(prefix):
            return prefix, base[len(prefix) :], anchor
    if "://" in base:
        idx = base.find("://") + 3
        return base[:idx], base[idx:], anchor
    return "", base, anchor


def norm_file_path(path_or_uri: str) -> str:
    _, path, _ = split_uri_parts(path_or_uri)
    while path.startswith("./") or path.startswith("/"):
        path = path[2:] if path.startswith("./") else path[1:]
    return path.strip()


def paths_match(uri_path: str, manifest_path: str) -> bool:
    np1, np2 = norm_file_path(uri_path), norm_file_path(manifest_path)
    return bool(
        np1
        and np2
        and (np1 == np2 or np1.endswith("/" + np2) or np2.endswith("/" + np1))
    )


def replace_renamed_path(
    orig_path: str,
    old_manifest_path: str,
    new_manifest_path: str,
) -> str:
    if not new_manifest_path.strip():
        return orig_path
    leading = (
        "/"
        if orig_path.startswith("/")
        else ("./" if orig_path.startswith("./") else "")
    )
    np_orig = norm_file_path(orig_path)
    np_old = norm_file_path(old_manifest_path)
    np_new = norm_file_path(new_manifest_path)
    if np_orig == np_old:
        return leading + np_new
    if np_orig.endswith("/" + np_old):
        prefix = np_orig[: len(np_orig) - len(np_old)]
        same_root = (
            "/" not in np_old
            or np_old.split("/", 1)[0] == np_new.split("/", 1)[0]
        )
        suffix = prefix + np_new if (same_root and not np_new.startswith(prefix)) else np_new
        return leading + suffix
    if np_old.endswith("/" + np_orig):
        prefix = np_old[: len(np_old) - len(np_orig)]
        suffix = np_new[len(prefix) :] if np_new.startswith(prefix) else np_new
        return leading + suffix
    return orig_path


def _scan_container_paths(
    c: dict[str, Any],
    local_hashes: dict[str, str],
    out: list[tuple[str, str | None]],
) -> None:
    raw_fh = c.get("file_hashes")
    fh_items = (raw_fh or {}).items() if isinstance(raw_fh, dict) else []
    for fp, hval in fh_items:
        if isinstance(fp, str) and (p := split_uri_parts(fp)[1]):
            if h_str := str(hval or "").strip():
                local_hashes[norm_file_path(p)] = h_str
            out.append((p, h_str or None))
    for fkey in ("files", "_table_files", "source_links", "external_uris"):
        items = c.get(fkey) if isinstance(c.get(fkey), list) else []
        for x in items:
            if isinstance(x, str) and (p := split_uri_parts(x)[1]):
                out.append((p, local_hashes.get(norm_file_path(p))))
    for ukey in ("file", "target_uri", "uri"):
        if isinstance(uval := c.get(ukey), str) and (p := split_uri_parts(uval)[1]):
            out.append((p, local_hashes.get(norm_file_path(p))))
    for lk in c.get("external_links") or []:
        u = (
            lk
            if isinstance(lk, str)
            else (
                str(_pick_val(lk, "target_uri", "uri", default=""))
                if isinstance(lk, dict)
                else ""
            )
        )
        if u and (p := split_uri_parts(u)[1]):
            out.append((p, local_hashes.get(norm_file_path(p))))


def infer_cross_repo_renames(
    graph: dict[str, Any],
    candidates: dict[str, tuple[str | None, dict[str, tuple[int, int]]]],
    renames: list[tuple[str, str, dict[str, tuple[int, int]]]],
    ast_updates: list[tuple[str, dict[str, tuple[int, int]]]],
) -> None:
    if not candidates or not isinstance(graph, dict):
        return
    cand_norms = {norm_file_path(cp) for cp in candidates if norm_file_path(cp)}
    cand_segs = {
        cp: [s for s in norm_file_path(cp).split("/") if s]
        for cp in candidates
    }
    found: list[tuple[str, str | None]] = []
    all_hashes: dict[str, str] = {}
    _scan_container_paths(graph, all_hashes, found)
    for sc in (graph.get("scopes") or {}).values():
        if sc:
            _scan_container_paths(sc, all_hashes, found)
            if isinstance(attrs := sc.get("attributes"), dict):
                _scan_container_paths(attrs, all_hashes, found)
            for rule in sc.get("rules") or []:
                _scan_container_paths(rule, all_hashes, found)
    raw_arts = graph.get("lattice_artifacts")
    art_values = (
        raw_arts.values()
        if isinstance(raw_arts, dict)
        else (raw_arts if isinstance(raw_arts, list) else [])
    )
    for art in art_values:
        if art:
            _scan_container_paths(art, all_hashes, found)
            if isinstance(dims := art.get("dimensions"), dict):
                _scan_container_paths(dims, all_hashes, found)
    raw_ext = graph.get("external_links")
    ext_values = raw_ext.values() if isinstance(raw_ext, dict) else []
    for lk_list in ext_values:
        if isinstance(lk_list, list):
            _scan_container_paths({"external_links": lk_list}, all_hashes, found)
    for orig_p, raw_h in found:
        np_orig = norm_file_path(orig_p)
        orig_h = raw_h or all_hashes.get(np_orig)
        already_renamed = any(
            norm_file_path(old_p) == np_orig for old_p, _, _ in renames
        )
        if not np_orig or np_orig in cand_norms or already_renamed:
            continue
        h_matches = (
            [cp for cp, (ch, _) in candidates.items() if ch and ch == orig_h]
            if orig_h
            else []
        )
        matched_cp = h_matches[0] if len(h_matches) == 1 else None
        o_segs = [s for s in np_orig.split("/") if s]
        if matched_cp is None and len(o_segs) >= 2:
            for k in range(len(o_segs), 1, -1):
                s_matches = [
                    cp
                    for cp, c_s in cand_segs.items()
                    if len(c_s) >= k and c_s[-k:] == o_segs[-k:]
                ]
                if s_matches:
                    matched_cp = s_matches[0] if len(s_matches) == 1 else None
                    break
        if matched_cp is not None:
            _, c_syms = candidates[matched_cp]
            renames.append((orig_p, matched_cp, c_syms))
            if c_syms:
                ast_updates.extend(((matched_cp, c_syms), (orig_p, c_syms)))


def parse_line_anchor(anchor: str) -> tuple[int, int] | None:
    if not (a := anchor.strip()):
        return None
    if ":" in a:
        return next(
            (
                parsed
                for part in a.split(":")
                if (parsed := parse_line_anchor(part)) is not None
            ),
            None,
        )
    if "-" in a:
        left, right = a.split("-", 1)
        l_num = left.removeprefix("L").removeprefix("l").strip()
        r_num = right.removeprefix("L").removeprefix("l").strip()
        return (int(l_num), int(r_num)) if (l_num.isdigit() and r_num.isdigit()) else None
    single = a.removeprefix("L").removeprefix("l").strip()
    return (int(single), int(single)) if single.isdigit() else None


def _parse_ast_symbols(raw_ast: Any) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    pairs = (
        [
            (
                str(_pick_val(x, "name", "symbol", "id", "function", default="")).strip(),
                x,
            )
            for x in raw_ast
            if isinstance(x, dict)
        ]
        if isinstance(raw_ast, list)
        else (list(raw_ast.items()) if isinstance(raw_ast, dict) else [])
    )
    for k, v in pairs:
        if not (name := str(k).strip()):
            continue
        if isinstance(v, (tuple, list)) and len(v) >= 2 and _is_int_val(v[0]) and _is_int_val(v[1]):
            out[name] = (int(v[0]), int(v[1]))
        elif isinstance(v, dict):
            s_val = _pick_val(v, "start_line", "start", "lineno")
            e_val = _pick_val(v, "end_line", "end", default=s_val)
            if _is_int_val(s_val) and _is_int_val(e_val):
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
        raise ValueError(
            f"git_diff_manifest must be a dict or list, got {type(manifest).__name__}"
        )
    sha_raw = _pick_val(manifest, "commit_sha", "commit", "sha")
    commit_sha = (
        str(sha_raw).strip()
        if isinstance(sha_raw, str) and sha_raw.strip()
        else None
    )
    out: list[dict[str, Any]] = []
    for k in ("changes", "files", "entries"):
        if isinstance(val := manifest.get(k), list):
            out.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.extend(
                {"path": p, **(s if isinstance(s, dict) else {"status": str(s)})}
                for p, s in val.items()
            )
    if isinstance(fh := manifest.get("file_hashes"), dict):
        out.extend(
            {
                "status": "A",
                "path": p,
                **(v if isinstance(v, dict) else {"file_hash": str(v)}),
            }
            for p, v in fh.items()
        )
    for k in ("renames", "renamed_files"):
        if isinstance(rv := manifest.get(k), dict):
            out.extend(
                {
                    "status": "R",
                    "old_path": op,
                    **(np if isinstance(np, dict) else {"new_path": np}),
                }
                for op, np in rv.items()
            )
        elif isinstance(rv, list):
            for x in rv:
                if isinstance(x, dict):
                    out.append({"status": "R", **x})
                elif isinstance(x, (tuple, list)) and len(x) >= 2:
                    out.append({"status": "R", "old_path": x[0], "new_path": x[1]})
    for k in ("deletes", "deleted", "removed"):
        raw_del = manifest.get(k)
        for x in raw_del if isinstance(raw_del, (list, tuple, set)) else []:
            entry = (
                {"status": "D", **x}
                if isinstance(x, dict)
                else {"status": "D", "old_path": str(x), "path": str(x)}
            )
            out.append(entry)
    for k in ("modified", "added"):
        if isinstance(mv := manifest.get(k), dict):
            for p, s in mv.items():
                extra = s if isinstance(s, dict) and "ast_symbols" in s else {"ast_symbols": s}
                out.append({"status": "M", "path": p, **extra})
        elif isinstance(mv, list):
            out.extend(
                {"status": "M", **x} if isinstance(x, dict) else {"status": "M", "path": str(x)}
                for x in mv
            )
    if isinstance(abf := manifest.get("ast_symbols_by_file"), dict):
        out.extend({"status": "M", "path": p, "ast_symbols": s} for p, s in abf.items() if s)
    direct_keys = ("status", "old_path", "from_path", "new_path", "to_path", "path")
    if any(k in manifest for k in direct_keys):
        out.append(manifest)
    return commit_sha, out


def _parse_git_diff_manifest(
    manifest: dict[str, Any] | list[dict[str, Any]],
) -> tuple[Any, ...]:
    commit_sha, raw_entries = _extract_raw_manifest_entries(manifest)
    renames: list[tuple[str, str, dict[str, tuple[int, int]]]] = []
    ast_updates: list[tuple[str, dict[str, tuple[int, int]]]] = []
    deletes: list[str] = []
    candidates: dict[str, tuple[str | None, dict[str, tuple[int, int]]]] = {}
    manifest_hashes: dict[str, str] = {}
    renames_ok = True
    raw_sh = (
        _pick_val(manifest, "line_shifts", "line_deltas", default={})
        if isinstance(manifest, dict)
        else {}
    )
    line_shifts = {
        str(k): int(v)
        for k, v in (raw_sh if isinstance(raw_sh, dict) else {}).items()
        if isinstance(v, int)
    }
    for entry in (e for e in raw_entries if e):
        st = str(_pick_val(entry, "status", "change_type", "action", default="")).strip().upper()
        old_p = str(
            _pick_val(entry, "old_path", "from_path", "source_path", "old_file", "old_uri", default="")
        ).strip()
        new_p = str(
            _pick_val(
                entry,
                "new_path",
                "to_path",
                "target_path",
                "new_file",
                "new_uri",
                "path",
                "file",
                default="",
            )
        ).strip()
        sh = _pick_val(entry, "line_shift", "line_delta")
        if isinstance(sh, int) and (tp := (new_p or old_p)):
            line_shifts[tp] = int(sh)
        f_hash = (
            str(_pick_val(entry, "file_hash", "sha256", "hash", "content_hash", default="")).strip()
            or None
        )
        syms = _parse_ast_symbols(_pick_val(entry, "ast_symbols", "symbols", "line_ranges"))
        st = st or (
            "R"
            if (old_p and new_p and old_p != new_p)
            else ("D" if entry.get("deleted") else "M")
        )
        is_del = st.startswith("D") or st in ("REMOVED", "UNLINKED")
        if st.startswith("R"):
            renames_ok = renames_ok and bool(
                old_p and new_p and not entry.get("rename_failed")
            )
            if old_p:
                renames.append((old_p, new_p, syms))
                if syms:
                    pairs = ((new_p, syms), (old_p, syms)) if new_p else ((old_p, syms),)
                    ast_updates.extend(pairs)
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
    return (
        commit_sha,
        renames,
        ast_updates,
        deletes,
        candidates,
        manifest_hashes,
        line_shifts,
        renames_ok,
    )


def _resolve_ast_line_range(
    anchor: str,
    concept_id: str,
    hint_symbol: str,
    sym_map: dict[str, tuple[int, int]],
) -> tuple[int, int] | None:
    if not sym_map:
        return None
    asym = anchor.split(":", 1)[0].strip() if ":" in anchor else anchor.strip()
    candidates = (
        asym,
        hint_symbol.strip(),
        concept_id.strip(),
        concept_id.strip().rsplit(".", 1)[-1],
    )
    for cand in candidates:
        if cand and cand in sym_map:
            return sym_map[cand]
    for sname, rng in sym_map.items():
        if sname and (
            sname == asym
            or sname in (concept_id, hint_symbol)
            or (concept_id and concept_id in sname)
        ):
            return rng
    if not hint_symbol and len(sym_map) == 1 and parse_line_anchor(anchor):
        return next(iter(sym_map.values()))
    return None
