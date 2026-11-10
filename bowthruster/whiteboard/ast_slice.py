"""Spec 22C §2.1: AST Symbol Span Cache, Code Window Formatter, & Slice Context Merger (`ast_slice.py`)."""

from __future__ import annotations

import ast
import copy
import os
from pathlib import Path
from typing import Any

from .table_helpers import _strip_file_uri

_AST_SPAN_CACHE: dict[
    str, tuple[int, list[tuple[str, int, int]], list[str], dict[str, list[str]]]
] = {}


def _format_code_window(src_lines: list[str], s: int, e: int, q_sub: list[str]) -> str:
    for tok in q_sub:
        if "-" in tok and all(p.lstrip("l").isdigit() for p in tok.split("-", 1)):
            ws, we = (max(1, min(len(src_lines), int(p.lstrip("l")))) for p in tok.split("-", 1))
            return f"[WINDOW #L{ws}-L{we}]\n" + "\n".join(src_lines[ws - 1 : we])
    if q_sub:
        hit = next(
            (
                i + 1
                for i in range(max(0, s - 1), min(len(src_lines), e))
                if any(k in src_lines[i].lower() for k in q_sub)
            ),
            None,
        )
        if hit:
            ws, we = max(s, hit - 5), min(e, max(s, hit - 5) + 90)
            return f"[WINDOW #L{ws}-L{we}]\n" + "\n".join(src_lines[ws - 1 : we])
    full_blk = "\n".join(src_lines[max(0, s - 1) : e])
    if len(full_blk) <= 15000:
        return full_blk
    head = src_lines[max(0, s - 1) : min(e, s + 59)]
    lm_prefixes = ("if ", "elif ", "else:", "for ", "while ", "try:", "except", "def ", "class ")
    lms = [
        f"#   L{i}: {st[:76]}"
        for i in range(s + 60, min(len(src_lines), e) + 1)
        if (st := src_lines[i - 1].strip()).startswith(lm_prefixes)
    ][:20]
    hdr = (
        f"# ... [LANDMARKS #L{s + 60}-L{e} — "
        "pass query='<symbol> <keyword>' or '<symbol> <start>-<end>']:"
    )
    return "\n".join([*head, hdr, *lms])


def _resolve_slice_file(cp: str) -> Path:
    lat_home = Path(__file__).resolve().parents[2]
    env_root = os.environ.get("BOWTHRUSTER_PROJECT_ROOT")
    cwd = Path.cwd().resolve()
    root = (
        Path(env_root).resolve()
        if env_root
        else (cwd if (cwd / ".whiteboard").is_dir() else lat_home)
    )
    cands = (root / cp, cwd / cp, lat_home / cp, lat_home.parent / cp)
    return next((c for c in cands if c.is_file()), root / cp)


def _load_cached_file_ast(
    fp: Path, cp: str
) -> tuple[list[tuple[str, int, int]], list[str], dict[str, list[str]]]:
    ckey = str(fp)
    mt = fp.stat().st_mtime_ns
    cached = _AST_SPAN_CACHE.get(ckey)
    if cached is not None and cached[0] == mt:
        return cached[1], cached[2], cached[3]
    src = fp.read_text(encoding="utf-8", errors="ignore")
    src_lines = src.splitlines()
    sym_spans: list[tuple[str, int, int]] = []
    calls_map: dict[str, list[str]] = {}
    if cp.endswith(".py"):
        try:
            tree = ast.parse(src)
            nodes = list(ast.walk(tree))
            sym_spans = [
                (n.name, int(n.lineno), int(getattr(n, "end_lineno", None) or n.lineno))
                for n in nodes
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and getattr(n, "lineno", 0) > 0
            ]
            sym_dict = {nm: (s, e) for nm, s, e in sym_spans}
            for fn in nodes:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    calls_map[fn.name] = sorted({
                        f"{c}#L{sym_dict[c][0]}-L{sym_dict[c][1]}"
                        for s in ast.walk(fn)
                        if isinstance(s, ast.Call)
                        and isinstance(s.func, ast.Name)
                        and (c := s.func.id) in sym_dict
                        and c != fn.name
                        and c.startswith("_")
                    })
        except SyntaxError:
            pass
    else:
        from bowthruster.nlp import _extract_generic_source_symbols

        raw_syms, calls_map = _extract_generic_source_symbols(fp)
        sym_spans = [(nm, s, e) for nm, (s, e) in raw_syms.items()]
    _AST_SPAN_CACHE[ckey] = (mt, sym_spans, src_lines, calls_map)
    return sym_spans, src_lines, calls_map


def _refresh_artifacts_from_disk(active_artifacts: list[dict[str, Any]]) -> None:
    for art in active_artifacts:
        if not isinstance(art, dict) or not (af := str(art.get("file") or "").strip()):
            continue
        cp = _strip_file_uri(af)
        fp = _resolve_slice_file(cp)
        if not fp.is_file():
            continue
        sym_spans, _, calls_map = _load_cached_file_ast(fp, cp)
        sym_dict = {nm: (s, e) for nm, s, e in sym_spans}
        asym = str(next((_v for _v in (art.get("name"), art.get("id")) if _v), None) or "").strip()
        if asym in sym_dict:
            s, e = sym_dict[asym]
            art["file"] = f"{cp}#L{s}-L{e}"
        if asym in calls_map:
            art["calls"] = list(calls_map[asym])


def _merge_matched_symbol_context(
    graph: dict[str, Any],
    scopes: dict[str, Any],
    matched_symbols: list[dict[str, Any]],
    slice_lists: tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ],
    include_code: bool,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    component_specs, active_flows, active_artifacts, active_rules = slice_lists
    if any(m.get("exact") for m in matched_symbols):
        exact_sids = {str(m.get("scope") or "") for m in matched_symbols if m.get("exact")}
        exact_syms = {str(m.get("symbol") or "").lower() for m in matched_symbols if m.get("exact")}
        component_specs = [copy.deepcopy(scopes[s]) for s in exact_sids if s in scopes]
        active_flows = [
            copy.deepcopy(fl)
            for fl in graph.get("flows", [])
            if (fl.get("source") in exact_sids or fl.get("target") in exact_sids)
        ]
        sc_arts = [
            copy.deepcopy(a)
            for a in graph.get("lattice_artifacts", [])
            if str(a.get("scope_id") or "") in exact_sids
        ]
        _refresh_artifacts_from_disk(sc_arts)
        if include_code:
            by_name = [a for a in sc_arts if str(a.get("name") or "").lower() in exact_syms]
            by_call = [
                a
                for a in sc_arts
                if any(
                    any(es in str(c).lower() for es in exact_syms)
                    for c in (a.get("calls") or [])
                )
            ]
            active_artifacts = by_name or by_call or sc_arts
        else:
            active_artifacts = sc_arts
        active_rules = [
            copy.deepcopy(r)
            for s in exact_sids
            for r in (scopes.get(s, {}).get("rules") or [])
        ]
    else:
        comp_ids = {str(c.get("id") or "") for c in component_specs}
        for m in matched_symbols:
            if (msid := str(m.get("scope") or "")) in scopes and msid not in comp_ids:
                comp_ids.add(msid)
                component_specs.append(copy.deepcopy(scopes[msid]))
                active_flows.extend(
                    copy.deepcopy(fl)
                    for fl in graph.get("flows", [])
                    if msid in (fl.get("source"), fl.get("target")) and fl not in active_flows
                )
                active_artifacts.extend(
                    copy.deepcopy(a)
                    for a in graph.get("lattice_artifacts", [])
                    if str(a.get("scope_id") or "") == msid and a not in active_artifacts
                )
                active_rules.extend(
                    copy.deepcopy(r)
                    for r in (scopes[msid].get("rules") or [])
                    if r not in active_rules
                )
        _refresh_artifacts_from_disk(active_artifacts)
    return component_specs, active_flows, active_artifacts, active_rules


def _art_file_path(art: dict[str, Any]) -> str:
    raw = str(art.get("file") or (art.get("dimensions") or {}).get("file") or "")
    return raw.split("#", 1)[0].split(":L", 1)[0].strip()
