"""Spec 22C: Whiteboard Table Cell Parsing, Scope Resolution, and Slice View Helpers (`table_helpers.py`)."""

from __future__ import annotations

from typing import Any

from bowthruster.graph_core import _build_rule_index, matches_nd_slice, token_cost, tokenize_content_words
from bowthruster.nlp import restore_ascii_dots
from bowthruster.whiteboard.mutations import ensure_scope_node
from bowthruster.whiteboard.schema import _clean_str_list, sync_to_sqlite

_CANONICAL_HEADERS_MSG = (
    "Unrecognized pipe-table headers. Expected canonical Lattice pipe-table headers:\n"
    "  - | Component | Role | Owner | Stage | Depends_On | Files |\n"
    "  - | Flow | Source | Target | Payload | Condition |\n"
    "  - | Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |\n"
    "  - | Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |\n"
    "  - | Schema | Component | Fields | Owner | Stage | File | Spec |\n"
    "  - | Rule | Component | Type | Property | Op | Value | Condition |"
)
_EMPTY_TOKENS = frozenset({"", "none", "-", "—", "n/a", "null"})


def _slugify_scope_id(raw: str) -> str:
    """Normalizes a surface entity/scope string into a canonical snake_case/path scope ID."""
    cleaned = restore_ascii_dots(str(raw)).strip().strip("\"`'").strip().rstrip("/")
    chars = [
        ch if (ch.isalnum() or ch in ("_", "/", ".")) else ("_" if ch in ("-", " ", ":") else " ")
        for ch in cleaned.lower()
    ]
    joined = "_".join(part for part in "".join(chars).split() if part)
    while "__" in joined:
        joined = joined.replace("__", "_")
    return joined.strip("_/")


def _extract_canonical_and_aliases(raw_name: str) -> tuple[str, list[str]]:
    """Extracts `(canonical_slug, surface_aliases)` from a raw scope/entity string (Spec 22C §2.2)."""
    if not (clean_raw := restore_ascii_dots(str(raw_name)).strip().strip("\"`'").strip().rstrip("/")):
        return "global_defaults", ["global_defaults"]
    aliases, canonical_cand, bt1 = [clean_raw], clean_raw, clean_raw.find("`")
    if bt1 >= 0 and (bt2 := clean_raw.find("`", bt1 + 1)) > bt1 + 1:
        inner_bt = clean_raw[bt1 + 1 : bt2].strip()
        if inner_bt and " " not in inner_bt:
            canonical_cand = inner_bt
            if inner_bt not in aliases:
                aliases.append(inner_bt)
    elif "(" in clean_raw and clean_raw.endswith(")"):
        inner_p = clean_raw[clean_raw.rfind("(") + 1 : -1].strip().strip("\"`'*").strip()
        if inner_p and " " not in inner_p:
            canonical_cand = inner_p
            if inner_p not in aliases:
                aliases.append(inner_p)
    if (prefix := clean_raw.split("(", 1)[0].strip().strip("*").strip()) and prefix not in aliases:
        aliases.append(prefix)
    for sep in ("—", "–", " - "):
        if sep in prefix:
            for seg in prefix.split(sep):
                if (seg_clean := seg.strip().strip("*").strip()) and seg_clean not in aliases:
                    aliases.append(seg_clean)
    return _slugify_scope_id(canonical_cand) or "global_defaults", aliases


def _parse_scalar_cell(val_str: str) -> int | float | bool | str:
    """Parses a Markdown table cell string into an `int`, `float`, `bool`, or normalized `str`."""
    s = restore_ascii_dots(str(val_str)).strip().strip("\"`'").strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if s and (s.isdigit() or (s.startswith("-") and s[1:].isdigit())):
        return int(s)
    if s and s.count(".") == 1:
        left, right = s.split(".", 1)
        if (left[1:] if left.startswith("-") else left).isdigit() and right.isdigit():
            return float(s)
    return s


def _parse_list_cell(val_str: str) -> list[str]:
    """Splits a comma- or semicolon-separated Markdown table cell into clean non-placeholder tokens."""
    if not (raw := restore_ascii_dots(str(val_str)).strip()) or raw.lower() in _EMPTY_TOKENS:
        return []
    chunks, buf, depth = [], [], 0
    for ch in raw:
        depth += 1 if ch in "([{" else (-1 if ch in ")]}" and depth > 0 else 0)
        if ch in (",", ";") and depth == 0:
            chunks.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    chunks.append("".join(buf))
    items: list[str] = []
    for chunk in chunks:
        tok = chunk.strip().strip("\"`'*").strip()
        if tok and tok.lower() not in _EMPTY_TOKENS and tok not in items:
            items.append(tok)
    return items


def _register_scope_aliases(graph: dict[str, Any], scope_id: str, aliases: list[str]) -> None:
    """Registers all surface and slugified forms of `aliases` for `scope_id`."""
    s_aliases = graph["scopes"][scope_id].setdefault("aliases", [])
    alias_idx = graph.setdefault("alias_index", {})
    alias_idx[scope_id.lower()] = alias_idx[_slugify_scope_id(scope_id)] = scope_id
    for al in (str(a).strip() for a in aliases if str(a).strip()):
        if al != scope_id and al not in s_aliases:
            s_aliases.append(al)
        alias_idx[al.lower()] = scope_id
        if al_slug := _slugify_scope_id(al):
            alias_idx[al_slug] = scope_id


def _resolve_or_register_scope(graph: dict[str, Any], raw_name: str, *, create: bool = True) -> str:
    """Resolves `raw_name` via `graph['alias_index']` or `graph['scopes']`, creating the scope if `create=True`."""
    scopes, alias_idx = graph.setdefault("scopes", {}), graph.setdefault("alias_index", {})
    canonical_slug, aliases = _extract_canonical_and_aliases(raw_name)
    for cand in [canonical_slug, *aliases]:
        c_low, c_slug = cand.lower().strip(), _slugify_scope_id(cand)
        for key in (cand, alias_idx.get(c_low), c_slug, alias_idx.get(c_slug)):
            if key and key in scopes:
                _register_scope_aliases(graph, key, aliases)
                return key
    for sid in scopes:
        base_sid = sid.split("::")[0]
        matches_base = (
            base_sid == canonical_slug or sid.endswith(f"/{canonical_slug}") or canonical_slug.endswith(f"/{base_sid}")
        )
        if matches_base and base_sid in scopes:
            _register_scope_aliases(graph, base_sid, aliases)
            return base_sid
    if create:
        ensure_scope_node(graph, canonical_slug)
        _register_scope_aliases(graph, canonical_slug, aliases)
        sync_to_sqlite(graph)
    return canonical_slug


def _is_existing_component_scope(sc: Any, sid: str) -> bool:
    """Returns True if `sc` is an existing non-default SBF component scope."""
    sbf_keys = ("role", "owner", "stage", "depends_on", "children", "parent", "files", "inputs", "outputs")
    return (
        bool(sid)
        and sid != "global_defaults"
        and (
            str(sc.get("kind") or "").strip().upper() == "COMPONENT"
            or str(sc.get("status") or "DRAFT").strip().upper() != "DRAFT"
            or any(sc.get(k) for k in sbf_keys)
        )
    )


def _resolve_known_component_scope(
    graph: dict[str, Any], raw_primary: str, mcu_text: str, parsed_mcu: dict[str, Any] | None = None
) -> str:
    """Resolves `raw_primary` to an existing component `c` in `graph['scopes']` when `mcu_text` mentions `c`."""
    scopes = (graph.get("scopes") or {})
    sbf_comps = [sid for sid, sc in scopes.items() if _is_existing_component_scope(sc, sid)]
    existing_comps = sbf_comps or [
        sid for sid, sc in scopes.items() if sid != "global_defaults"
    ]
    cand = _resolve_or_register_scope(graph, raw_primary, create=False)
    if cand in existing_comps:
        return _resolve_or_register_scope(graph, raw_primary, create=True)
    pm = parsed_mcu or {}
    lp = (pm.get("lattice_payload") or {})
    op_kind = str(lp.get("op") or "").removeprefix("UPSERT_")
    lkind = str(
        pm.get("lattice_kind") if pm.get("lattice_kind") else (lp.get("kind") or op_kind)
    ).strip().upper()
    if lkind == "COMPONENT" and cand and cand != "global_defaults":
        return _resolve_or_register_scope(graph, raw_primary, create=True)
    if existing_comps and mcu_text:
        text_low = restore_ascii_dots(mcu_text).lower()
        text_toks = "".join(ch if (ch.isalnum() or ch == "_") else " " for ch in text_low).split()
        matches: list[tuple[int, int, str]] = []
        suffixes = ("module", "component", "system", "subsystem", "service")
        for c in existing_comps:
            c_low, c_base = c.lower(), c.lower().split("::")[0]
            c_aliases = [str(a).strip().lower() for a in (scopes.get(c, {}).get("aliases") or []) if str(a).strip()]
            names = [n for n in dict.fromkeys([c_low, c_base, *c_aliases]) if n and " " not in n]
            has_match = any(
                n in text_toks
                or any(f"{n}_{sfx}" in text_toks for sfx in suffixes)
                or cand.lower().startswith(f"{n}_")
                for n in names
            )
            if not has_match:
                continue
            tok_pos = min(
                (i for i, t in enumerate(text_toks) if t in names or any(t.startswith(f"{n}_") for n in names)),
                default=9999,
            )
            phrases = tuple(
                p
                for n in names
                for p in (
                    f"the {n} module",
                    f"the {n} component",
                    f"the {n} system",
                    f"the {n} service",
                    f"in {n}",
                    f"for {n}",
                    f"on {n}",
                    f"under {n}",
                    f"within {n}",
                )
            )
            in_phrase = (
                any(p in text_low for p in phrases)
                or tok_pos == 0
                or (tok_pos == 1 and text_toks[0] in ("the", "in", "for", "on"))
            )
            cand_pos = text_low.find(cand.lower()) if cand and cand != "global_defaults" else 99999
            c_pos = min((text_low.find(n) for n in names if n in text_low), default=99999)
            if (
                cand == "global_defaults"
                or not cand
                or bool(sbf_comps)
                or in_phrase
                or cand.lower().startswith(f"{c_low}_")
                or c_pos < cand_pos
            ):
                matches.append((0 if in_phrase else 1, tok_pos, c))
        if matches:
            matches.sort(key=lambda x: (x[0], x[1], x[2]))
            return matches[0][2]
    if lkind in ("SCHEMA", "INTERFACE", "LAW") and cand not in scopes:
        if cand == _slugify_scope_id(str(next((_v for _v in (lp.get("name"), lp.get("id")) if _v), None) or "")):
            return "global_defaults"
    return _resolve_or_register_scope(graph, raw_primary, create=True)


def _get_table_cell(row: dict[str, Any], headers: list[Any], norm_headers: list[str], keys: tuple[str, ...]) -> str:
    for k in keys:
        if k in norm_headers:
            idx = norm_headers.index(k)
            if (raw_hdr := headers[idx]) in row and row[raw_hdr] is not None:
                return str(row[raw_hdr])
            if k in row and row[k] is not None:
                return str(row[k])
    return next((str(row[k]) for k in keys if k in row and row[k] is not None), "")


def _clean_cell_text(row: dict[str, Any], headers: list[Any], norm_headers: list[str], keys: tuple[str, ...]) -> str:
    return restore_ascii_dots(_get_table_cell(row, headers, norm_headers, keys)).strip().strip("\"`'*").strip()


def _validate_table_headers(t_idx: int, headers: list[Any], norm_headers: list[str]) -> tuple[bool, bool, bool]:
    has_comp = any(k in norm_headers for k in ("component", "scope", "scope_id", "module"))
    is_charter_t = (
        "charter" in norm_headers
        or "project" in norm_headers
        or ("summary" in norm_headers and any(k in norm_headers for k in ("principles", "purpose", "why", "non_goals")))
    )
    is_flow_t = "flow" in norm_headers or ("source" in norm_headers and "target" in norm_headers)
    is_rule_t = (
        "rule" in norm_headers
        or "bound" in norm_headers
        or (
            "type" in norm_headers
            and any(k in norm_headers for k in ("property", "target_prop", "value", "bound_val"))
        )
    )
    comp_attr_cols = (
        "role",
        "purpose",
        "responsibility",
        "why",
        "rationale",
        "owner",
        "division",
        "stage",
        "phase",
        "depends_on",
        "deps",
        "dependencies",
        "files",
        "file",
        "tests",
        "test",
        "test_files",
        "description",
        "tier",
        "media",
        "inputs",
        "outputs",
        "parent",
        "children",
    )
    if is_charter_t:
        valid_hdrs = any(k in norm_headers for k in ("summary", "purpose", "why", "principles", "description"))
    elif "interface" in norm_headers:
        valid_hdrs = has_comp
    elif "law" in norm_headers:
        law_cols = ("lhs", "rhs", "theorem", "equation", "formula", "invariant", "spec", "description")
        valid_hdrs = has_comp and any(k in norm_headers for k in law_cols)
    elif "schema" in norm_headers:
        schema_cols = ("fields", "field", "component", "scope", "scope_id", "module", "spec", "description")
        valid_hdrs = any(k in norm_headers for k in schema_cols)
    elif is_flow_t:
        valid_hdrs = "source" in norm_headers and "target" in norm_headers
    elif is_rule_t:
        rule_cols = (
            "property",
            "target_prop",
            "value",
            "bound_val",
            "condition",
            "rule",
            "bound",
            "spec",
            "description",
        )
        valid_hdrs = any(k in norm_headers for k in rule_cols)
    else:
        entity_cols = ("component", "scope", "scope_id", "module", "service", "division", "division_persona")
        valid_hdrs = (
            len(norm_headers) >= 2
            and any(k in norm_headers for k in entity_cols)
            and (not ("component" in norm_headers) or any(k in norm_headers for k in comp_attr_cols))
        )
    if not valid_hdrs:
        raise ValueError(f"doc_tables[{t_idx}] headers {headers!r}: {_CANONICAL_HEADERS_MSG}")
    return is_charter_t, is_flow_t, is_rule_t


def _norm_files_list(raw_files: Any) -> list[str]:
    return _clean_str_list(raw_files)


def _strip_file_uri(af: str) -> str:
    return (
        af.removeprefix("uri://file/")
        .removeprefix("file://")
        .removeprefix("./")
        .split("#", 1)[0]
        .split(":", 1)[0]
        .strip()
    )


def _insert_tag_into_line(ln: str, extra_tag: str) -> str:
    if (b_end := ln.find("]")) < 0:
        return ln
    if ln[b_end + 1 : b_end + 3] == " (":
        depth = 0
        for idx in range(b_end + 2, len(ln)):
            depth += 1 if ln[idx] == "(" else (-1 if ln[idx] == ")" else 0)
            if depth == 0:
                return f"{ln[:idx]}, {extra_tag}{ln[idx:]}"
    return f"{ln[: b_end + 1]} ({extra_tag}){ln[b_end + 1 :]}"


def _enrich_view_compact_lines(
    graph: dict[str, Any], view: dict[str, Any], *, eff_budget: int = 400
) -> dict[str, Any]:
    scopes = graph.get("scopes", {}) if isinstance(graph, dict) else {}
    comp_files: dict[str, list[str]] = {}
    for comp in view.get("component_specs", []):
        if isinstance(comp, dict):
            cid = str(comp.get("id", comp.get("scope_id", comp.get("scope"))) or "")
            sc = (scopes.get(cid) or {})
            flist = _norm_files_list(comp.get("files", sc.get("files", (sc.get("attributes") or {}).get("files"))))
            if flist:
                comp["files"] = comp_files[cid] = flist
    iface_by_name: dict[str, dict[str, Any]] = {}
    law_by_name: dict[str, dict[str, Any]] = {}
    all_arts = list((graph or {}).get("lattice_artifacts", []) or []) + list(
        view.get("active_artifacts", [])
    )
    for art in all_arts:
        if (aname := str(next((_v for _v in (art.get("name"), art.get("id")) if _v), None) or "").lower()):
            akind = str(art.get("kind") or "").upper()
            if akind in ("INTERFACE", "SCHEMA"):
                iface_by_name[aname] = art
            elif akind == "LAW":
                law_by_name[aname] = art
    raw_txt = str(next((_v for _v in (view.get("compact_view"), view.get("text")) if _v), None) or "")
    if not raw_txt and not view.get("matched_symbols"):
        return view
    out_lines: list[str] = []
    for ln in raw_txt.splitlines():
        cand_ln = ln
        if ln.startswith("@ COMPONENT [") and "]" in ln:
            cid = ln[len("@ COMPONENT [") : ln.find("]")]
            sc_d = scopes.get(cid) or {}
            flist = comp_files.get(cid) or _norm_files_list(sc_d.get("files"))
            if flist and "files=[" not in ln:
                cand_ln = _insert_tag_into_line(cand_ln, f"files=[{', '.join(flist)}]")
            c_by = str(sc_d.get("claimed_by") or "").strip()
            if c_by and str(sc_d.get("status") or "").upper() != "VERIFIED" and "claimed_by=" not in cand_ln:
                cand_ln = _insert_tag_into_line(cand_ln, f"claimed_by={c_by}")
        elif ln.startswith(("$ INTERFACE [", "# SCHEMA [")) and "]" in ln:
            art = iface_by_name.get(ln[ln.find("[") + 1 : ln.find("]")].lower())
            fval = str((art or {}).get("file") or ((art or {}).get("dimensions") or {}).get("file") or "").strip()
            if fval and "file=" not in ln:
                cand_ln = _insert_tag_into_line(ln, f"file={fval}")
            if isinstance(calls := (art or {}).get("calls"), list) and calls and "calls=[" not in cand_ln:
                cand_ln = _insert_tag_into_line(cand_ln, f"calls=[{', '.join(str(x) for x in calls)}]")
        elif ln.startswith("% LAW [") and "]" in ln:
            art = law_by_name.get(ln[len("% LAW [") : ln.find("]")].lower())
            if art:
                dims = (art.get("dimensions") or {})
                ltags = [
                    f"{k}={v}"
                    for k in ("bend_dir", "args", "lhs", "rhs")
                    if (v := str(art.get(k, dims.get(k, "")) or "").strip()) and f"{k}=" not in ln
                ]
                if ltags:
                    cand_ln = _insert_tag_into_line(ln, " ".join(ltags))
        out_lines.append(cand_ln)
    for m in view.get("matched_symbols") or []:
        if isinstance(m, dict) and m.get("symbol"):
            sym_ln = f"@ AST_SYMBOL [{m['symbol']}] (scope={m.get('scope')}, file={m.get('file')})"
            if sym_ln not in out_lines:
                out_lines.append(sym_ln)
            if m.get("code"):
                out_lines.append(f"[CODE {m.get('file')}] ({m['symbol']})\n{m['code']}")
    enriched_txt = "\n".join(out_lines)
    if eff_budget >= 400 or token_cost(enriched_txt) <= eff_budget:
        view.update(
            {
                "compact_view": enriched_txt,
                "text": enriched_txt,
                "compact_lines": out_lines,
                "token_count": token_cost(enriched_txt),
                "line_count": len(out_lines),
            }
        )
    else:
        view["compact_lines"] = raw_txt.splitlines()
    return view
