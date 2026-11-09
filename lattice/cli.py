#!/usr/bin/env python3
"""Multi-Round Executive <-> Whiteboard Dogfooding CLI (`scripts/whiteboard_session.py`)."""
from __future__ import annotations

import argparse, ast, contextlib, copy, hashlib, importlib.util, inspect, json, os, re, shutil, subprocess, sys, threading, time, traceback, urllib.request
try: import fcntl
except ImportError: fcntl = None  # type: ignore[assignment]
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LATTICE_HOME = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(os.environ["LATTICE_PROJECT_ROOT"]).resolve() if os.environ.get("LATTICE_PROJECT_ROOT") else LATTICE_HOME
if str(LATTICE_HOME) not in sys.path:
    sys.path.insert(0, str(LATTICE_HOME))

import lattice.whiteboard as _wb  # noqa: E402
from lattice.graph_core import SQLiteGraphStore  # noqa: E402
from lattice.the_table import render_lattice_atlas  # noqa: E402
from lattice.whiteboard import ContextGraph, annotate_active_tasks, create_empty_context_graph, expand_node, frustration as _wb_frust, ingest as _wb_ingest, mutations as _wb_mut, scan_graph, slice_lattice, sync_to_sqlite  # noqa: E402

SESSIONS_ROOT, DEFAULT_CONFIG_PATHS = Path("/tmp/whiteboard_sessions"), (LATTICE_HOME / "config.json", LATTICE_HOME / "benchmarks" / "configs" / "config.json")
LIMITS_PATH, GEN_MODEL, FLIGHT_LOG_PATH = LATTICE_HOME / "size_limits.json", "anthropic/claude-haiku-4.5", REPO_ROOT / ".whiteboard" / "logs" / "lattice_events.jsonl"
STANDING_TYPES, BUILD_STATUSES = ("BOUND", "PROHIBITION", "OBLIGATION", "POLICY"), ("DRAFT", "BLOCKED_BY_DEP", "READY", "IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE")
DIVISION_CIDS: dict[str, str] = dict((next((json.loads(p.read_text(encoding="utf-8")) for p in DEFAULT_CONFIG_PATHS if p.is_file()), {}).get("division_cids") or {}))
_get_evaluate_lattice_proof = lambda: getattr(_wb, "evaluate_lattice_proof", None) or getattr(_wb_frust, "evaluate_lattice_proof", None)
_get_compile_work_packets = lambda: getattr(_wb, "compile_work_packets", None) or getattr(_wb_ingest, "compile_work_packets", None)
_get_transition_build_status = lambda: getattr(_wb, "transition_component_build_status", None) or getattr(_wb_mut, "transition_component_build_status", None)
_get_render_lattice_atlas = lambda: getattr(sys.modules.get("lattice.the_table.surface"), "render_lattice_atlas", render_lattice_atlas)
def format_exception_error(exc: BaseException) -> dict[str, Any]:
    last = tb[-1] if (tb := traceback.extract_tb(exc.__traceback__)) else None
    return {"type": type(exc).__name__, "file": last.filename if last else "", "line": int(last.lineno) if last else 0, "message": str(exc), "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))}
def record_flight_event(board: str, tool_or_cmd: str, duration_ms: float, status: str = "OK", *, decision: str | None = None, violations: list[Any] | None = None, modified_scopes: list[str] | None = None, stale_cascaded_scopes: list[str] | None = None, response_bytes: int = 0, error: dict[str, Any] | None = None) -> dict[str, Any]:
    flog_p = REPO_ROOT / ".whiteboard" / "logs" / "lattice_events.jsonl"; flog_p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"timestamp": datetime.now(timezone.utc).isoformat(), "board": str(board or "default"), "tool_or_cmd": str(tool_or_cmd), "duration_ms": round(float(duration_ms), 2), "status": str(status).upper(), "decision": decision, "violations": list(violations or []), "modified_scopes": list(modified_scopes or []), "stale_cascaded_scopes": list(stale_cascaded_scopes or []), "response_bytes": int(response_bytes or 0), "error": error}
    with flog_p.open("a", encoding="utf-8") as fh: fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec
def read_flight_logs(limit: int = 20, board: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    flog_p = REPO_ROOT / ".whiteboard" / "logs" / "lattice_events.jsonl"
    rows = [json.loads(ln) for ln in flog_p.read_text(encoding="utf-8").splitlines() if ln.strip()] if flog_p.is_file() else []
    return [r for r in rows if (not board or r.get("board") == board) and (not status or str(r.get("status") or "").upper() == status.upper())][-max(1, int(limit)):]
def _call_compile_packets(graph: dict[str, Any], *, force: bool = False, status_filter: str | None = None) -> dict[str, Any]:
    if (pkt_fn := _get_compile_work_packets()) is None: raise RuntimeError("compile_work_packets is not yet promoted to lattice.whiteboard")
    if status_filter and "status_filter" in inspect.signature(pkt_fn).parameters: return pkt_fn(graph, force=force, status_filter=status_filter)
    res = pkt_fn(graph, force=force)
    return {**res, "packets": [p for p in (res.get("packets") or []) if str(p.get("build_status") or "").upper() == status_filter.upper()]} if status_filter else res
def _run_tickets_cli(argv: list[str], body: str | None = None) -> tuple[int, str]:
    t_py = REPO_ROOT / "scripts" / "tickets.py"
    if not t_py.is_file(): return 0, ""
    proc = subprocess.run([sys.executable, str(t_py), *argv, *(["-F", "-"] if body is not None else [])], cwd=str(REPO_ROOT), input=body, text=True, capture_output=True, timeout=30)
    return proc.returncode, (proc.stdout + "\n" + proc.stderr).strip()
def _format_packet_ticket_body(board: str, comp: str, snode: dict[str, Any], pkt: dict[str, Any] | None, *, for_qa: bool = False) -> str:
    pkt_id, btag = (pkt or {}).get("packet_id") or f"pkt_{comp}", snode.get("build_tag") or "<build-tag>"
    lines = [f"[Lattice Work Packet: {pkt_id}] board={board} component={comp} status={snode.get('status')} build_tag={snode.get('build_tag')} core_tag={snode.get('core_tag')}", "", "## Prompt Slice", (pkt or {}).get("prompt_slice") or "(empty slice)"]
    if (pkt or {}).get("laws_skeleton"): lines.extend(["", "## LAWS.bend Skeleton", "```bend", str(pkt["laws_skeleton"]).strip(), "```"])
    cmds = [f"- Fast Incremental QA Rule (< 5s): NEVER re-run untouched VERIFIED suites; run Stage 27 `evaluate_git_commit_gate` + `verify_ast_call_graph_skeleton` first, `python3 scripts/bend_gate.py check <bend_dir> --only-law <changed_law>`, and `pytest -k {comp}` (<= 80 lines delta tests on `changed_scopes | stale_dependents` only).", f"- Pass QA: `.venv/bin/python scripts/whiteboard_session.py --board {board} status {comp} VERIFIED --build-tag {btag}`", f"- Fail QA: `.venv/bin/python scripts/whiteboard_session.py --board {board} status {comp} FAILED --build-tag {btag} --reason \"<failure_diagnostic>\"`"] if for_qa else [f"- Inspect READY packets: `.venv/bin/python scripts/whiteboard_session.py --board {board} packets --component {comp} --force`", f"- Start build: `.venv/bin/python scripts/whiteboard_session.py --board {board} status {comp} IN_PROGRESS`", f"- Submit tagged build to QA: `.venv/bin/python scripts/whiteboard_session.py --board {board} status {comp} IN_QA --build-tag <build-tag>`"]
    return "\n".join([*lines, "", "## Lattice Build Lifecycle Commands", *cmds])
def _sync_status_ticket(board: str, comp: str, new_status: str, snode: dict[str, Any], pkt: dict[str, Any] | None, for_div: str | None, dispatch: bool) -> dict[str, Any]:
    owner, tid = (for_div or snode.get("owner") or (pkt or {}).get("owner") or "").strip().lower(), snode.get("active_ticket_id")
    info: dict[str, Any] = {"ticket_id": tid, "ticket_action": None, "wake_line": None, "wake_recipient": None, "wake_cid": None, "ticket_output": None}
    rc, out = 0, ""
    if (new_status == "READY" or dispatch) and owner in DIVISION_CIDS:
        title = f"[Lattice {board}] Build {comp} ({(pkt or {}).get('packet_id') or comp})" + (f" [{snode['build_tag']}]" if snode.get("build_tag") else "")
        rc, out = _run_tickets_cli(["open", "--for", owner, "--priority", "P0", "--blocks", f"Lattice component {comp} READY on board {board}", "-t", title], _format_packet_ticket_body(board, comp, snode, pkt, for_qa=(owner == "bobo")))
        if rc == 0 and (m_id := re.search(r"\b([0-9a-f]{7})\s+opened\b", out)):
            snode["active_ticket_id"] = tid = m_id.group(1); info.update({"ticket_id": tid, "ticket_action": "opened", "wake_recipient": owner, "wake_cid": DIVISION_CIDS.get(owner)})
    elif new_status == "IN_PROGRESS" and tid:
        rc, out = _run_tickets_cli(["comment", str(tid)], f"[Lattice {board}] `{comp}` -> `IN_PROGRESS`" + (f" (build_tag={snode['build_tag']})" if snode.get("build_tag") else "")); info["ticket_action"] = "commented" if rc == 0 else None
    elif new_status == "IN_QA":
        if (for_div or "").strip().lower() == "bobo":
            rc, out = _run_tickets_cli(["open", "--for", "bobo", "--priority", "P0", "--blocks", f"Unit QA for {comp} ({snode.get('build_tag')})", "-t", f"[Lattice {board}] QA {comp} ({snode.get('build_tag') or 'untagged'})"], _format_packet_ticket_body(board, comp, snode, pkt, for_qa=True))
            if rc == 0 and (m_id := re.search(r"\b([0-9a-f]{7})\s+opened\b", out)):
                snode["active_ticket_id"] = tid = m_id.group(1); info.update({"ticket_id": tid, "ticket_action": "opened_qa", "wake_recipient": "bobo", "wake_cid": DIVISION_CIDS["bobo"]})
        elif tid:
            rc, out = _run_tickets_cli(["close", str(tid)], f"[Lattice {board}] `{comp}` -> `IN_QA` (build_tag={snode.get('build_tag')})")
            if rc == 0: info.update({"ticket_action": "closed_for_review", "wake_recipient": "bethe", "wake_cid": DIVISION_CIDS["bethe"]})
    elif new_status == "FAILED":
        body = f"[Lattice {board}] `{comp}` -> `FAILED`: {snode.get('failure_diagnostic')}\n\n" + _format_packet_ticket_body(board, comp, snode, pkt)
        if tid and not for_div:
            rc, out = _run_tickets_cli(["reopen", str(tid)], body)
            if rc != 0: rc, out = _run_tickets_cli(["close", str(tid)], body)
            info["ticket_action"] = "updated_failed" if rc == 0 else None
        elif owner in DIVISION_CIDS:
            rc, out = _run_tickets_cli(["open", "--for", owner, "--priority", "P0", "--blocks", f"Fix FAILED component {comp} on board {board}", "-t", f"[Lattice {board}] Remand {comp} (FAILED: {(snode.get('failure_diagnostic') or '')[:48]})"], body)
            if rc == 0 and (m_id := re.search(r"\b([0-9a-f]{7})\s+opened\b", out)):
                snode["active_ticket_id"] = tid = m_id.group(1); info.update({"ticket_id": tid, "ticket_action": "opened_remand", "wake_recipient": owner, "wake_cid": DIVISION_CIDS.get(owner)})
    elif new_status == "VERIFIED" and tid:
        rc, out = _run_tickets_cli(["close", str(tid)], f"[Lattice {board}] `{comp}` -> `VERIFIED` (build_tag={snode.get('build_tag')}, core_tag={snode.get('core_tag')})"); info["ticket_action"] = "closed_verified" if rc == 0 else None
    if out:
        info["ticket_output"] = out
        if m_wake := re.search(r"(P0 ticket [0-9a-f]{7}(?: closed)?: run `scripts/tickets\.py show [0-9a-f]{7}`)", out): info["wake_line"] = m_wake.group(1)
        if (m_who := re.search(r"Wake ([a-z0-9_, ]+):", out)) and not info.get("wake_recipient"):
            recip = m_who.group(1).split(",")[0].strip(); info.update({"wake_recipient": recip, "wake_cid": DIVISION_CIDS.get(recip)})
    return info
def _run_bend_proof_check(session_dir: Path, bend_source: str) -> tuple[bool, Path, str]:
    proof_path = session_dir / "LATTICE_PROOF.bend"
    proof_path.write_text(bend_source, encoding="utf-8")
    cand = Path(os.environ.get("BEND_BIN", str(Path.home() / ".bend" / "bin" / "bend")))
    bend_exe = cand if (cand.is_file() and os.access(cand, os.X_OK)) else (Path(w) if (w := shutil.which("bend")) else None)
    if bend_exe is None: return False, proof_path, "bend binary not found"
    env = {**os.environ, "BEND_NO_TELEMETRY": "1"}
    proc = subprocess.run([str(bend_exe), str(proof_path)], cwd=str(session_dir), env=env, text=True, capture_output=True, timeout=30)
    if proc.returncode != 0 and "unknown" in (proc.stderr or "").lower():
        proc = subprocess.run([str(bend_exe), "check", str(proof_path)], cwd=str(session_dir), env=env, text=True, capture_output=True, timeout=30)
    return proc.returncode == 0 and ("ALL PROOFS CHECK" in proc.stdout or not proc.stderr.strip()), proof_path, (proc.stdout + "\n" + proc.stderr).strip()
def _print_proof_radar(proof: dict[str, Any] | None) -> None:
    if isinstance(proof, dict):
        gaps, viols = proof.get("open_gaps") or [], proof.get("hard_violations") or []
        print(f"\n[Lattice Proof Status]: {proof.get('status')} (components={proof.get('component_count', 0)}, flows={proof.get('flow_count', 0)}, laws={proof.get('law_count', 0)}, contracts={proof.get('contract_count', 0)}, gaps={len(gaps)})")
        for v in viols: print(f"  ! [VIOLATION] {v.get('id', 'conflict')}: {v.get('text', v)}")
        for g in gaps: print(f"  - [{g.get('kind', 'GAP')}] scope={g.get('scope')}: {g.get('description', '')}")

_META_LOCK, _RLOCKS, _LOCK_DEPTH = threading.Lock(), {}, {}
@contextlib.contextmanager
def _board_lock(board: str):
    bdir = SESSIONS_ROOT / ("".join(c for c in board.strip() if c.isalnum() or c in ("_", "-")) or "default"); bdir.mkdir(parents=True, exist_ok=True); key = str(bdir)
    with _META_LOCK: rlock = _RLOCKS.setdefault(key, threading.RLock())
    with rlock:
        depth, fh = _LOCK_DEPTH.get(key) or (0, None)
        if depth == 0:
            fh = (bdir / ".board.lock").open("a+", encoding="utf-8")
            if fcntl is not None: fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _LOCK_DEPTH[key] = (depth + 1, fh)
        try: yield
        finally:
            if depth == 0: _LOCK_DEPTH.pop(key, None); (fcntl.flock(fh.fileno(), fcntl.LOCK_UN) if fcntl is not None else None); fh.close()
            else: _LOCK_DEPTH[key] = (depth, fh)
def _board_paths(board: str) -> tuple[Path, Path, Path, Path]:
    slug, wb_local = "".join(c for c in board.strip() if c.isalnum() or c in ("_", "-")) or "default", REPO_ROOT / ".whiteboard" / f"{board}.json"
    bdir = SESSIONS_ROOT / slug; bdir.mkdir(parents=True, exist_ok=True)
    if slug not in ("default", "dogfood") and not slug.endswith("_live") and (wb_local.parent.is_dir() or os.environ.get("LATTICE_PROJECT_ROOT")): wb_local.parent.mkdir(parents=True, exist_ok=True)
    use_local = wb_local.is_file() or ((not (bdir / "whiteboard.json").is_file() or bool(os.environ.get("LATTICE_PROJECT_ROOT"))) and wb_local.parent.is_dir() and slug not in ("default", "dogfood") and not slug.endswith("_live"))
    return bdir, (wb_local if use_local else bdir / "whiteboard.json"), bdir / "whiteboard.sqlite", bdir / "transcript.jsonl"
def _save_board(graph: dict[str, Any], wb_json: Path, wb_sqlite: Path) -> None:
    if isinstance(graph, ContextGraph): object.__setattr__(graph, "_sqlite_store", SQLiteGraphStore(wb_sqlite))
    sync_to_sqlite(graph); tmp_p = wb_json.with_name(f".{wb_json.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_p.write_text(json.dumps(copy.deepcopy({k: v for k, v in dict(graph).items() if k != "_sqlite_store"}), indent=2, sort_keys=True) + "\n", encoding="utf-8"); os.replace(tmp_p, wb_json)
def _load_board(board: str) -> tuple[ContextGraph, Path, Path, Path]:
    with _board_lock(board):
        _, wb_json, wb_sqlite, transcript_path = _board_paths(board); store = SQLiteGraphStore(wb_sqlite)
        if wb_json.is_file():
            raw_data = json.loads(wb_json.read_text(encoding="utf-8")); graph = ContextGraph(raw_data, store=store)
            for sid, raw_sc in (raw_data.get("scopes") or {}).items():
                if isinstance(raw_sc, dict) and raw_sc.get("active_ticket_id") and sid in graph.get("scopes", {}): graph["scopes"][sid]["active_ticket_id"] = raw_sc["active_ticket_id"]
            sync_to_sqlite(graph)
        else: graph = create_empty_context_graph(store=store)
        return graph, wb_json, wb_sqlite, transcript_path
def _load_atlas_boards(explicit_board: str | None, boards_csv: str | None, load_all: bool) -> dict[str, dict[str, Any]]:
    vault_wb = REPO_ROOT / ".whiteboard"
    if boards_csv: names = [x.strip() for x in boards_csv.split(",") if x.strip()]
    elif not load_all and explicit_board: names = [explicit_board]
    else:
        roots = (vault_wb,) if vault_wb.is_dir() else (SESSIONS_ROOT,)
        cands = sorted({p.stem if p.name != "whiteboard.json" else p.parent.name for r in roots if r.is_dir() for p in r.rglob("*.json")})
        names = [n for n in cands if n not in ("default", "dogfood") and not n.endswith("_live")] or [explicit_board or "default"]
    out: dict[str, dict[str, Any]] = {}
    for n in names:
        cand = next((c for c in (vault_wb / f"{n}.json", vault_wb / n / "whiteboard.json", SESSIONS_ROOT / n / "whiteboard.json") if c.is_file()), None)
        if cand:
            d = json.loads(cand.read_text(encoding="utf-8"))
            if boards_csv or (not load_all and explicit_board) or [k for k in (d.get("scopes") or {}) if k != "global_defaults"] or d.get("flows"): out[n] = d
        elif boards_csv or (not load_all and explicit_board): out[n] = dict(_load_board(n)[0])
    return out or {explicit_board or "default": dict(_load_board(explicit_board or "default")[0])}


def is_table_file_indexed(has_table_files: bool = True, in_ref_map: bool = True) -> bool:
    return bool((not has_table_files) or in_ref_map)
def is_baseline_stamp_clean(is_initial_baseline: bool = True, diff_files_emitted: bool = False) -> bool:
    return bool((not is_initial_baseline) or (not diff_files_emitted))
_PY_AST_CACHE: dict[tuple[str, int], tuple[dict[str, tuple[int, int]], dict[str, list[str]]]] = {}
def _py_ast_extractor(fp: Path) -> tuple[dict[str, tuple[int, int]], dict[str, list[str]]]:
    if (hit := _PY_AST_CACHE.get(ckey := (str(fp), fp.stat().st_mtime_ns if fp.is_file() else 0))) is not None: return hit
    try: nodes = list(ast.walk(tree := ast.parse(fp.read_text(encoding="utf-8", errors="ignore"))))
    except SyntaxError: return {}, {}
    fn_nodes = [n for n in reversed(nodes) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    syms = {n.name: (int(n.lineno), int(getattr(n, "end_lineno", None) or n.lineno)) for n in reversed(nodes) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and getattr(n, "lineno", 0) > 0}
    for n in getattr(tree, "body", []):
        if isinstance(n, ast.Assign) and getattr(n, "lineno", 0) > 0:
            for t in n.targets:
                if isinstance(t, ast.Name): syms.setdefault(t.id, (int(n.lineno), int(getattr(n, "end_lineno", None) or n.lineno)))
    calls = {fn.name: sorted({f"{c}#L{syms[c][0]}-L{syms[c][1]}" for s in ast.walk(fn) if isinstance(s, ast.Call) and isinstance(s.func, ast.Name) and (c := s.func.id) in syms and c != fn.name and c.startswith("_")}) for fn in fn_nodes}
    _PY_AST_CACHE[ckey] = res = (syms, {k: v for k, v in calls.items() if v})
    return res
from lattice.nlp import _extract_generic_source_symbols
AST_EXTRACTORS: dict[str, Any] = {".py": _py_ast_extractor, **{e: _extract_generic_source_symbols for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java", ".kt", ".c", ".cpp", ".cc", ".h", ".hpp", ".cs", ".swift", ".rb", ".bend")}}
def register_ast_extractor(ext: str, extractor_fn: Any) -> None: AST_EXTRACTORS[ext if ext.startswith(".") else f".{ext}"] = extractor_fn
def _extract_py_ast_symbols(fp: Path) -> dict[str, tuple[int, int]]:
    return (AST_EXTRACTORS.get(fp.suffix, _extract_generic_source_symbols)(fp)[0] if fp.is_file() else {})
def _extract_ast_local_calls(fp: Path) -> dict[str, list[str]]:
    return (AST_EXTRACTORS.get(fp.suffix, _extract_generic_source_symbols)(fp)[1] if fp.is_file() else {})
def _collect_ref_map(graph: dict[str, Any] | None, comp_filter: str | None = None) -> dict[str, tuple[str, str]]:
    ref_map: dict[str, tuple[str, str]] = {}
    for sid, sc in ((graph or {}).get("scopes") or {}).items():
        if isinstance(sc, dict) and (not comp_filter or sid == comp_filter):
            attrs = sc.get("attributes") if isinstance(sc.get("attributes"), dict) else {}
            for r in [*(sc.get("files") or []), *(attrs.get("files") or []), *(attrs.get("_table_files") or [])]:
                if (s := str(r).strip()) and (cp := s.removeprefix("uri://file/").removeprefix("uri://").removeprefix("file://").removeprefix("./").split("#", 1)[0].split(":", 1)[0].strip()):
                    ref_map[cp] = (sid, s.split("#", 1)[0])
    raw_arts = (graph or {}).get("lattice_artifacts") or []
    for art in (raw_arts.values() if isinstance(raw_arts, dict) else raw_arts):
        if isinstance(art, dict) and (r := str(art.get("file") or "").strip()) and (asid := str(art.get("scope") or "").strip()) and (not comp_filter or asid == comp_filter):
            if (cp := r.removeprefix("uri://file/").removeprefix("uri://").removeprefix("file://").removeprefix("./").split("#", 1)[0].split(":", 1)[0].strip()) and cp not in ref_map:
                ref_map[cp] = (asid, r.split("#", 1)[0])
    has_tf = any(bool((sc.get("attributes") or {}).get("_table_files")) for sid, sc in ((graph or {}).get("scopes") or {}).items() if isinstance(sc, dict) and (not comp_filter or sid == comp_filter))
    if not is_table_file_indexed(has_tf, not has_tf or bool(ref_map)):
        raise ValueError("pipe_table_ref_map_law violated")
    return ref_map

def _verify_component_files(graph: dict[str, Any], comp: str) -> None:
    gate_cfg = (next((json.loads(p.read_text(encoding="utf-8")) for p in DEFAULT_CONFIG_PATHS if p.is_file()), {}).get("gate") or {})
    if not gate_cfg.get("verify_files_exist_on_status", True): return
    root, ref_m = Path(os.environ["LATTICE_PROJECT_ROOT"]).resolve() if os.environ.get("LATTICE_PROJECT_ROOT") else REPO_ROOT, _collect_ref_map(graph, comp)
    comp_files = list(ref_m.keys())
    res_fp = lambda cp: (root / cp) if (root / cp).is_file() or os.environ.get("LATTICE_PROJECT_ROOT") or not (LATTICE_HOME.parent / cp).is_file() else (LATTICE_HOME.parent / cp)
    if missing := [cp for cp in comp_files if not res_fp(cp).is_file()]:
        raise ValueError(f"Cannot mark '{comp}' VERIFIED; missing declared file(s) on disk: {', '.join(missing)}")
    raw_mlb = graph.get("max_file_lines") if graph.get("max_file_lines") is not None else gate_cfg.get("max_file_lines")
    mlb = int(raw_mlb) if isinstance(raw_mlb, (int, float)) and not isinstance(raw_mlb, bool) and raw_mlb > 0 else None
    for s_id in ("global_defaults", "max_file_lines", comp):
        for r in (((graph.get("scopes") or {}).get(s_id) or {}).get("rules") or []):
            if isinstance(r, dict) and (str(r.get("type") or "").upper() == "BOUND" or r.get("bound_op") is not None) and r.get("bound_val") is not None:
                if str(r.get("target_prop") or "").strip().lower() in ("max_file_lines", "file_lines", "line_count", "lines", "max_lines") or "max_file_lines" in str(r.get("text") or "").lower():
                    try: mlb = int(float(r["bound_val"]))
                    except (TypeError, ValueError): pass
    if mlb is not None and (over := [f"{cp} ({lcnt} > max_file_lines={mlb})" for cp in comp_files if cp.endswith((".py", ".bend")) and (lcnt := len(res_fp(cp).read_text(encoding="utf-8", errors="ignore").splitlines())) > mlb]):
        raise ValueError(f"Cannot mark '{comp}' VERIFIED; max_file_lines exceeded: {', '.join(over)}")
    if isinstance(sc := ((graph.get("scopes") or {}).get(comp)), dict):
        from lattice.ai_memory import sync_git_diff_uris
        f_hashes = {raw_r: hashlib.sha256(res_fp(cp).read_bytes()).hexdigest()[:16] for cp, (_, raw_r) in ref_m.items() if res_fp(cp).is_file()}
        sc["file_hashes"] = dict(f_hashes)
        if isinstance(sc.get("attributes"), dict): sc["attributes"]["file_hashes"] = dict(f_hashes)
        sync_git_diff_uris(graph, {"ast_symbols_by_file": {cp: _extract_py_ast_symbols(res_fp(cp)) for cp in comp_files if res_fp(cp).suffix in AST_EXTRACTORS and res_fp(cp).is_file()}, "ast_calls_by_file": {cp: _extract_ast_local_calls(res_fp(cp)) for cp in comp_files if res_fp(cp).suffix in AST_EXTRACTORS and res_fp(cp).is_file()}})

def _build_git_manifest(mode: str, manifest_arg: str | None, graph: dict[str, Any] | None = None) -> dict[str, Any]:
    if manifest_arg: return json.loads(Path(manifest_arg).read_text(encoding="utf-8") if Path(manifest_arg).is_file() else manifest_arg)
    ref_map, is_base = _collect_ref_map(graph), mode == "baseline" or (mode == "post" and not bool((graph or {}).get("last_commit_sha")))
    eff_root = LATTICE_HOME.parent if (not os.environ.get("LATTICE_PROJECT_ROOT") and ref_map and any((LATTICE_HOME.parent / cp).is_file() and not (REPO_ROOT / cp).is_file() for cp in ref_map)) else REPO_ROOT
    all_repos = [eff_root, *(p for p in sorted(LATTICE_HOME.iterdir()) if p.is_dir() and (p / ".git").exists())]
    if mode in ("post", "baseline"):
        cand_repos = [r for r in all_repos if any(cp.startswith(r.relative_to(eff_root).as_posix() + "/") for cp in ref_map if r != eff_root) or (r == eff_root and any(not cp.startswith(LATTICE_HOME.name + "/") for cp in ref_map))] or all_repos
        ts_list = [(int(subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=str(r), text=True, capture_output=True).stdout.strip() or 0), r) for r in cand_repos]
        repos = [max(ts_list, key=lambda x: x[0])[1]] if ts_list else [eff_root]
    else: repos = all_repos
    git_cmd = ["git", "diff", "--cached", "-M", "--name-status"] if mode == "pre" else (["git", "show", "-M", "--name-status", "--format=%H", "HEAD"] if mode in ("post", "baseline") else ["git", "status", "--porcelain"])
    sha, files, file_hashes = (subprocess.run(["git", "log", "-1", "--format=%H"], cwd=str(repos[0]), text=True, capture_output=True, timeout=15).stdout.strip() or None if is_base else None), [], ({raw_r: hashlib.sha256((eff_root / cp).read_bytes()).hexdigest()[:16] for cp, (_, raw_r) in ref_map.items() if (eff_root / cp).is_file()} if mode in ("post", "baseline") else {})
    for r in ([] if is_base else repos):
        rel_pref = "" if r == eff_root else (r.relative_to(eff_root).as_posix() + "/")
        lines = [ln.strip() for ln in subprocess.run(git_cmd, cwd=str(r), text=True, capture_output=True, timeout=15).stdout.splitlines() if ln.strip()]
        if mode == "post" and lines and len(lines[0]) == 40: sha = lines.pop(0)
        for ln in lines:
            parts = ln.split("\t") if "\t" in ln else ln.split(None, 1)
            if len(parts) >= 2:
                st, path, old_p = parts[0].strip(), rel_pref + parts[-1].strip(), (rel_pref + parts[1].strip() if len(parts) >= 3 else "")
                fp, sid_hit = eff_root / path, (ref_map.get(path) or ref_map.get(old_p) or (None, ""))[0]
                lcnt, fhash = (len(fp.read_text(encoding="utf-8", errors="ignore").splitlines()) if path.endswith((".py", ".bend")) else None, hashlib.sha256(fp.read_bytes()).hexdigest()[:16]) if fp.is_file() else (None, "")
                files.append({"status": st, "path": path, "contract_changed": bool(old_p or fp.suffix in AST_EXTRACTORS), **({"scope": sid_hit} if sid_hit else {}), **({"old_path": old_p} if old_p else {}), **({"line_count": lcnt} if lcnt is not None else {}), **({"file_hash": fhash} if fhash else {}), **({"ast_symbols": _extract_py_ast_symbols(fp)} if mode in ("pre", "post") and fp.is_file() and fp.suffix in AST_EXTRACTORS else {}), **({"source": fp.read_text(encoding="utf-8", errors="ignore")} if mode == "pre" and fp.is_file() and fp.suffix in AST_EXTRACTORS else {})})
    if mode == "pre":
        seen, sc_map = {f["path"] for f in files} | {f.get("old_path") for f in files if f.get("old_path")}, (graph or {}).get("scopes") or {}
        touched_sc = {f.get("scope") for f in files if f.get("scope")} | ({sid for cp, (sid, raw_r) in ref_map.items() if (fp := eff_root / cp).is_file() and (str((sc := sc_map.get(sid) or {}).get("status") or sc.get("build_status") or "").upper() != "VERIFIED" or hashlib.sha256(fp.read_bytes()).hexdigest()[:16] != ((sc.get("file_hashes") or (sc.get("attributes") or {}).get("file_hashes") or {}).get(raw_r)))} if not seen else set())
        for cp, (sid_hit, _) in ref_map.items():
            fp = eff_root / cp
            if not fp.is_file() and cp not in seen: files.append({"status": "D", "path": cp, "scope": sid_hit})
            elif (sid_hit in touched_sc and cp not in seen) and fp.is_file():
                files.append({"status": "M", "path": cp, "scope": sid_hit, "contract_changed": fp.suffix in AST_EXTRACTORS, **({"line_count": len(fp.read_text(encoding="utf-8", errors="ignore").splitlines())} if cp.endswith((".py", ".bend")) else {}), "file_hash": hashlib.sha256(fp.read_bytes()).hexdigest()[:16], **({"ast_symbols": _extract_py_ast_symbols(fp), "source": fp.read_text(encoding="utf-8", errors="ignore")} if fp.suffix in AST_EXTRACTORS else {})})
    if not is_baseline_stamp_clean(is_base, bool(files)): raise ValueError("initial_baseline_stamp_law violated")
    cfg_mlb = (next((json.loads(p.read_text(encoding="utf-8")) for p in DEFAULT_CONFIG_PATHS if p.is_file()), {}).get("gate") or {}).get("max_file_lines")
    ast_by_f = {cp: _extract_py_ast_symbols(eff_root / cp) for cp in ref_map if (eff_root / cp).suffix in AST_EXTRACTORS and (eff_root / cp).is_file()} if mode in ("post", "baseline") else {}
    calls_by_f = {cp: _extract_ast_local_calls(eff_root / cp) for cp in ref_map if (eff_root / cp).suffix in AST_EXTRACTORS and (eff_root / cp).is_file()} if mode in ("post", "baseline") else {}
    return {"commit_sha": sha, "files": files, **({"file_hashes": file_hashes} if file_hashes else {}), **({"ast_symbols_by_file": ast_by_f} if ast_by_f else {}), **({"ast_calls_by_file": calls_by_f} if calls_by_f else {}), **({"max_file_lines": cfg_mlb} if cfg_mlb is not None else {})}
_read_transcript = lambda p: [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.is_file() else []
def _append_transcript(p: Path, record: dict[str, Any]) -> None:
    with p.open("a", encoding="utf-8") as fh: fh.write(json.dumps(record, ensure_ascii=False) + "\n")
def _read_input_text(message: str | None, file_path: str | None) -> str:
    if message is not None or file_path is not None: return (message if message is not None else (sys.stdin.read() if file_path == "-" else Path(file_path).read_text(encoding="utf-8"))).strip()
    raise ValueError("Provide either -m/--message or -F/--file")
def _remove_item_from_board(graph: dict[str, Any], target_id: str) -> list[str]:
    removed, scopes = [], graph.get("scopes", {})
    if target_id.startswith("scope:") and (del_sid := target_id.removeprefix("scope:").strip()) in scopes:
        scopes.pop(del_sid, None); removed.append(f"scope:{del_sid}")
        for snode in (v for v in scopes.values() if isinstance(v, dict)):
            if isinstance(snode.get("depends_on"), list): snode["depends_on"] = [d for d in snode["depends_on"] if d != del_sid]
    if isinstance(graph.get("lattice_artifacts"), list):
        kept_arts = [a for a in graph["lattice_artifacts"] if not (isinstance(a, dict) and str(a.get("id") or "") == target_id)]
        if len(kept_arts) < len(graph["lattice_artifacts"]): removed.append(f"artifact:{target_id}"); graph["lattice_artifacts"] = kept_arts
    has_rule_id = any(any(isinstance(r, dict) and r.get("id") == target_id for r in s.get("rules", [])) for s in scopes.values() if isinstance(s, dict))
    if (target_id.startswith("attr:") or ("." in target_id and not has_rule_id)) and "." in target_id.removeprefix("attr:"):
        sid, prop = target_id.removeprefix("attr:").split(".", 1)
        if isinstance(snode := scopes.get(sid), dict) and isinstance(snode.get("attributes"), dict) and prop in snode["attributes"]:
            removed.append(f"attr:{sid}.{prop}={snode['attributes'].pop(prop)!r}")
    for sid, snode in ((k, v) for k, v in scopes.items() if isinstance(v, dict)):
        kept = []
        for r in snode.get("rules", []):
            if isinstance(r, dict) and r.get("id") == target_id:
                removed.append(f"{r.get('type', 'RULE')}:{target_id}@{sid} ({r.get('text', '')})")
                (snode.get("_meta") if isinstance(snode.get("_meta"), dict) else {}).pop(target_id, None)
            else: kept.append(r)
        snode["rules"] = kept
    if removed:
        for snode in (v for v in scopes.values() if isinstance(v, dict)):
            if target_id in snode.get("in_edges", []): snode["in_edges"].remove(target_id)
            for r in (x for x in snode.get("rules", []) if isinstance(x, dict)):
                if target_id in r.get("overrides", []): r["overrides"].remove(target_id)
                r["exceptions"] = [ex for ex in r.get("exceptions", []) if not (isinstance(ex, dict) and ex.get("rule_id") == target_id)]
        graph["open_blockers"] = [b for b in graph.get("open_blockers", []) if not (isinstance(b, dict) and (b.get("id") == target_id or target_id in b.get("rule_ids", [])))]
    return removed
def _execute_turn(board: str, speaker: str, raw_text: str, round_num: int | None, profile: str | None = None, config_path: str | None = None) -> int:
    _ = (profile, config_path)
    from lattice.the_executive import run_executive_turn
    with _board_lock(board):
        graph, wb_json, wb_sqlite, transcript_path = _load_board(board); history = _read_transcript(transcript_path)
        if round_num is None:
            last_turn = next((h for h in reversed(history) if h.get("event", "turn") == "turn"), None)
            round_num = int(last_turn.get("round", 1)) + 1 if (last_turn and last_turn.get("decision") == "SEND_BACK_TO_USER") else 1
        t0 = time.perf_counter(); res = run_executive_turn(graph, raw_text, db_path=wb_sqlite); elapsed_s = round(time.perf_counter() - t0, 3)
        if res["whiteboard_mutated"]: _save_board(res["whiteboard_graph"], wb_json, wb_sqlite)
        eval_fn = _get_evaluate_lattice_proof(); proof = res.get("lattice_proof") or (eval_fn(res["whiteboard_graph"]) if eval_fn else None); parsed_mcus = res.get("intake_packet", {}).get("parsed_mcus", [])
        _append_transcript(transcript_path, {"event": "turn", "timestamp_utc": datetime.now(timezone.utc).isoformat(), "speaker": speaker, "round": round_num, "turn_index": res["turn_index"], "raw_text": raw_text, "decision": res["decision"], "bounce_reason": res["bounce_reason"], "whiteboard_mutated": res["whiteboard_mutated"], "user_message": res["user_message"], "cited_items": res["cited_items"], "committed_items": res.get("committed_items", []), "conflicts": res.get("conflicts", []), "flawed_ideas": res.get("flawed_ideas", []), "clarification_questions": res.get("clarification_questions", []), "open_questions": res.get("open_questions", []), "parsed_mcus": parsed_mcus, "lattice_proof_status": proof.get("status") if isinstance(proof, dict) else None, "elapsed_s": elapsed_s})
    record_flight_event(board, "turn", elapsed_s * 1000.0, "OK" if res["decision"] == "COMMIT_TO_WHITEBOARD" else "REJECTED", decision=res["decision"], violations=list(res.get("conflicts") or []) + list(res.get("flawed_ideas") or []))
    print(f"=== EXECUTIVE TURN {res['turn_index']} (Round {round_num} | Speaker: {speaker}) ===\nDecision:          {res['decision']}\nBounce Reason:     {res['bounce_reason']}\nWhiteboard Mutated:{res['whiteboard_mutated']} (elapsed {elapsed_s:.2f}s)\nExecutive Reply:   {res['user_message']}" + (f"\nCited Items:       {', '.join(str(x) for x in res['cited_items'])}" if res["cited_items"] else "") + f"\nParsed MCUs ({len(parsed_mcus)}):")
    for m in parsed_mcus: print(f"  - [mcu_{m.get('mcu_id')}] {m.get('type')} scope={m.get('scope_hint') or 'auto'} prop={m.get('target_prop')!r}: {m.get('text')}")
    for label, key in (("Conflicts", "conflicts"), ("Flawed Ideas", "flawed_ideas"), ("Clarification Questions", "clarification_questions"), ("Open Questions", "open_questions")):
        if res.get(key): print(f"{label} ({len(res[key])}):\n" + "\n".join(f"  * {json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item}" for item in res[key]))
    _print_proof_radar(proof)
    return 0
def _assemble_board_summary(graph: dict[str, Any], scope_filter: str | None = None) -> dict[str, Any]:
    annotate_active_tasks(graph); scopes_out, standing, facts, goals, actions = {}, [], [], [], []
    for sid, snode in sorted(graph.get("scopes", {}).items()):
        if not isinstance(snode, dict) or (scope_filter and sid != scope_filter and not sid.startswith(f"{scope_filter}::")): continue
        attrs, rules = {k: v for k, v in (snode.get("attributes") or {}).items() if k not in ("updated_turn", "heading_path")}, [dict(r) for r in snode.get("rules", []) if isinstance(r, dict)]
        scopes_out[sid] = {**{k: snode.get(k) for k in ("parent", "role", "owner", "stage", "status", "build_tag", "core_tag", "failure_diagnostic", "active_ticket_id")}, "children": list(snode.get("children", [])), "attributes": attrs, "rules": rules}
        for r in rules:
            rtype = str(r.get("type", "OBLIGATION"))
            (standing if rtype in STANDING_TYPES else facts if rtype == "FACT" else goals if rtype == "GOAL" else actions).append(r)
    eval_fn, raw_arts = _get_evaluate_lattice_proof(), graph.get("lattice_artifacts") or {}
    return {"turn": graph.get("turn", 0), "scopes": scopes_out, "standing_rules": standing, "facts": facts, "goals": goals, "actions": actions, "flows": list(graph.get("flows", [])), "lattice_artifacts": list(raw_arts.values()) if isinstance(raw_arts, dict) else list(raw_arts), "active_tasks": list(graph.get("active_tasks", [])), "open_blockers": list(graph.get("open_blockers", [])), "lattice_proof": eval_fn(graph) if eval_fn else graph.get("lattice_proof"), "assemble_context_pack": expand_node(copy.deepcopy(graph), scope_filter) if scope_filter else scan_graph(copy.deepcopy(graph))}
def main() -> int:
    common, parser = argparse.ArgumentParser(add_help=False), argparse.ArgumentParser(description="Multi-Round Executive <-> Whiteboard Dogfooding CLI")
    for flg in ("--board", "--profile", "--config"):
        common.add_argument(flg, default=argparse.SUPPRESS); parser.add_argument(flg, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_turn, p_rep, p_rev = sub.add_parser("turn", parents=[common]), sub.add_parser("replace", parents=[common]), sub.add_parser("revoke", parents=[common])
    for p in (p_rep, p_rev): p.add_argument("rule_id")
    for p in (p_turn, p_rep, p_rev):
        p.add_argument("--speaker", default="agent"); p.add_argument("-m", "--message", default="Explicitly revoked" if p is p_rev else None)
        if p is not p_rev: p.add_argument("--round", type=int, default=None, dest="round_num"); p.add_argument("-F", "--file", default=None)
    p_show, p_slice = sub.add_parser("show", parents=[common]), sub.add_parser("slice", parents=[common])
    p_show.add_argument("--scope", default=None)
    for flag in ("--component", "--division", "--stage", "--schema", "--kind"): p_slice.add_argument(flag, default=None)
    p_slice.add_argument("-q", "--query", default=""); p_slice.add_argument("--code", action="store_true", dest="include_code"); p_slice.add_argument("--mark-stale", action="store_true", dest="mark_stale")
    p_pkts, p_stat, p_atlas, p_gsync, p_logs = sub.add_parser("packets", parents=[common]), sub.add_parser("status", parents=[common]), sub.add_parser("atlas", parents=[common]), sub.add_parser("git-sync", parents=[common]), sub.add_parser("logs", parents=[common])
    p_pkts.add_argument("--wave", type=int, choices=(0, 1, 2), default=None); p_pkts.add_argument("--component", default=None); p_pkts.add_argument("--status", choices=BUILD_STATUSES, type=str.upper, default=None); p_pkts.add_argument("--force", action="store_true")
    p_stat.add_argument("component"); p_stat.add_argument("new_status", choices=BUILD_STATUSES, type=str.upper)
    for flg, dst in (("--build-tag", "build_tag"), ("--core-tag", "core_tag"), ("--reason", "reason"), ("--for", "for_div")): p_stat.add_argument(flg, default=None, dest=dst)
    for flg, dst in (("--dispatch", "dispatch"), ("--no-ticket", "no_ticket")): p_stat.add_argument(flg, action="store_true", dest=dst)
    p_atlas.add_argument("--boards", default=None)
    for flg, dst in (("--all", "load_all"), ("--mermaid", "mermaid"), ("--spec", "spec")): p_atlas.add_argument(flg, action="store_true", dest=dst)
    for flg, dst in (("--pre-commit", "pre_commit"), ("--post-commit", "post_commit"), ("--baseline", "baseline"), ("--drift", "drift"), ("--mark-verified", "mark_verified")): p_gsync.add_argument(flg, action="store_true", dest=dst)
    p_gsync.add_argument("--manifest", default=None)
    p_logs.add_argument("--limit", type=int, default=20); p_logs.add_argument("--status", default=None)
    for p in (p_show, p_slice, sub.add_parser("prove", parents=[common]), p_pkts, p_stat, p_atlas, p_gsync, p_logs, sub.add_parser("history", parents=[common])): p.add_argument("--json", action="store_true", dest="as_json")
    sub.add_parser("reset", parents=[common])
    args = parser.parse_args()
    explicit_board, profile, config_path = getattr(args, "board", None), getattr(args, "profile", None), getattr(args, "config", None)
    board, t0_cmd = explicit_board or "default", time.perf_counter()
    try:
        if args.cmd == "logs":
            entries = read_flight_logs(limit=args.limit, board=explicit_board, status=args.status)
            print(json.dumps(entries, indent=2, ensure_ascii=False) if args.as_json else "\n".join(f"[{e.get('timestamp')}] board={e.get('board')} cmd={e.get('tool_or_cmd')} status={e.get('status')} decision={e.get('decision')} ({e.get('duration_ms')}ms, bytes={e.get('response_bytes', 0)}) violations={len(e.get('violations') or [])}" + (f" error={e['error'].get('type')}: {e['error'].get('message')}" if e.get("error") else "") for e in entries) or "(no flight log entries)")
            return 0
        if args.cmd == "git-sync":
            from lattice.ai_memory import sync_git_diff_uris
            from lattice.the_executive import detect_working_tree_drift, sync_post_commit_to_lattice
            from lattice.the_executive.gate import evaluate_git_commit_gate
            graph, wb_json, wb_sqlite, _ = _load_board(board)
            if args.pre_commit:
                manifest = _build_git_manifest("pre", args.manifest, graph)
                gate_res = dict(evaluate_git_commit_gate(graph, manifest, uri_sync_report=sync_git_diff_uris(copy.deepcopy(graph), manifest)))
                allow = bool(gate_res.get("allowed", gate_res.get("allow_commit", True)))
                if allow and args.mark_verified:
                    stale_sids = [sid for sid, sc in sorted((graph.get("scopes") or {}).items()) if isinstance(sc, dict) and sid != "global_defaults" and str(sc.get("status") or sc.get("build_status") or "").upper() == "STALE"]
                    for sid in stale_sids: _verify_component_files(graph, sid); _get_transition_build_status()(graph, sid, "VERIFIED")
                    if stale_sids: _save_board(graph, wb_json, wb_sqlite)
                    gate_res["marked_verified_scopes"] = stale_sids
                record_flight_event(board, "git-sync:pre-commit", (time.perf_counter() - t0_cmd) * 1000.0, "OK" if allow else "REJECTED", decision=gate_res.get("decision"), violations=gate_res.get("violations"), modified_scopes=gate_res.get("marked_verified_scopes"))
                print(json.dumps(gate_res, indent=2, sort_keys=True) if args.as_json else f"[GIT PRE-COMMIT GATE '{board}'] allowed={allow} decision={gate_res.get('decision')} bounds_ok={gate_res.get('bounds_ok')} contracts_ok={gate_res.get('contracts_ok')} bend_proofs_ok={gate_res.get('bend_proofs_ok')} no_dangling_deletes={gate_res.get('no_dangling_deletes')} violations={len(gate_res.get('violations') or [])}" + (f" marked_verified={gate_res['marked_verified_scopes']}" if gate_res.get("marked_verified_scopes") else ""))
                return 0 if allow else 1
            if args.post_commit or args.baseline:
                sync_res = dict(sync_post_commit_to_lattice(graph, _build_git_manifest("baseline" if args.baseline else "post", args.manifest, graph), store=SQLiteGraphStore(wb_sqlite)))
                wb_out = sync_res.get("whiteboard_graph") or graph
                if sync_res.get("commit_sha"): wb_out["last_commit_sha"] = sync_res["commit_sha"]
                _save_board(wb_out, wb_json, wb_sqlite)
                out_d = {k: v for k, v in sync_res.items() if k != "whiteboard_graph"}
                record_flight_event(board, "git-sync:post-commit", (time.perf_counter() - t0_cmd) * 1000.0, "OK", modified_scopes=out_d.get("modified_scopes"), stale_cascaded_scopes=out_d.get("cascaded_stale_scopes"))
                print(json.dumps(out_d, indent=2, sort_keys=True) if args.as_json else f"[GIT POST-COMMIT SYNC '{board}'] sha={out_d.get('commit_sha')} modified={out_d.get('modified_scopes')} cascaded_stale={out_d.get('cascaded_stale_scopes')}")
                return 0
            drift_res = dict(detect_working_tree_drift(graph, _build_git_manifest("drift", args.manifest, graph)))
            record_flight_event(board, "git-sync:drift", (time.perf_counter() - t0_cmd) * 1000.0, "OK", modified_scopes=drift_res.get("drifted_scopes"))
            print(json.dumps(drift_res, indent=2, sort_keys=True) if args.as_json else f"[GIT DRIFT '{board}'] has_drift={drift_res.get('has_drift')} drifted_scopes={drift_res.get('drifted_scopes')} uncommitted={len(drift_res.get('uncommitted_files') or [])} dangling_uris={drift_res.get('dangling_uris')}")
            return 0
        if args.cmd == "atlas":
            boards_dict = _load_atlas_boards(explicit_board, args.boards, args.load_all)
            view = _get_render_lattice_atlas()(boards_dict, has_local_seed_cues=False, explicit_atlas_request=True)
            out_payload = {"boards": list(boards_dict.keys()), **{k: view.get(k) for k in ("atlas_mounted", "atlas_schematic", "atlas_metrics", "atlas_mermaid", "atlas_spec")}}
            record_flight_event(board, "atlas", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            print(json.dumps(out_payload, indent=2, sort_keys=True, ensure_ascii=False) if args.as_json else (str(view.get("atlas_spec") or "") if args.spec else f"{view.get('atlas_schematic') or ''}\n\n[Atlas Metrics]: {json.dumps(view.get('atlas_metrics') or {}, sort_keys=True)}" + (f"\n\n[Atlas Mermaid]:\n{view['atlas_mermaid']}" if args.mermaid and view.get("atlas_mermaid") else "")))
            return 0
        if args.cmd == "reset":
            bdir, wb_json, wb_sqlite, transcript_path = _board_paths(board)
            for p in (wb_json, wb_sqlite, transcript_path, bdir / "LATTICE_PROOF.bend"): p.unlink(missing_ok=True)
            _save_board(create_empty_context_graph(store=SQLiteGraphStore(wb_sqlite)), wb_json, wb_sqlite)
            record_flight_event(board, "reset", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            print(f"Reset Whiteboard session '{board}' at {bdir}")
            return 0
        if args.cmd == "turn":
            return _execute_turn(board, args.speaker, _read_input_text(args.message, args.file), args.round_num, profile, config_path)
        if args.cmd in ("revoke", "replace"):
            graph, wb_json, wb_sqlite, transcript_path = _load_board(board)
            removed = _remove_item_from_board(graph, args.rule_id)
            if not removed: raise KeyError(f"Item {args.rule_id!r} not found on Whiteboard '{board}'")
            _save_board(graph, wb_json, wb_sqlite)
            _append_transcript(transcript_path, {"event": args.cmd, "timestamp_utc": datetime.now(timezone.utc).isoformat(), "speaker": args.speaker, "rule_id": args.rule_id, "removed": removed, "reason": args.message or ""})
            record_flight_event(board, args.cmd, (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            print(f"[{args.cmd.upper()}] Removed from '{board}': {'; '.join(removed)}")
            return _execute_turn(board, args.speaker, _read_input_text(args.message, args.file), args.round_num, profile, config_path) if args.cmd == "replace" else 0
        if args.cmd == "slice":
            graph, wb_json, wb_sqlite, _ = _load_board(board)
            view = dict(slice_lattice(graph, query=args.query, component=args.component, division=args.division, stage=args.stage, schema=args.schema, kind=args.kind, include_code=args.include_code))
            if args.mark_stale:
                scopes_map, c_arg = graph.get("scopes") or {}, str(args.component or "").strip()
                stale_sids = [c_arg] if c_arg and c_arg in scopes_map else sorted({str(m.get("scope")) for m in (view.get("matched_symbols") or []) if isinstance(m, dict) and m.get("scope") in scopes_map} or {str(c.get("id")) for c in (view.get("component_specs") or []) if isinstance(c, dict) and c.get("id") in scopes_map and c.get("id") != "global_defaults"})
                for sid in stale_sids: _get_transition_build_status()(graph, sid, "STALE")
                if stale_sids: _save_board(graph, wb_json, wb_sqlite)
                view["marked_stale_scopes"] = stale_sids
            record_flight_event(board, "slice", (time.perf_counter() - t0_cmd) * 1000.0, "OK", modified_scopes=view.get("marked_stale_scopes"))
            print(json.dumps(view, indent=2, sort_keys=True, ensure_ascii=False) if args.as_json else (view.get("compact_view") or view.get("text") or "[EMPTY_SLICE]") + (f"\n[MARKED STALE]: {view['marked_stale_scopes']}" if view.get("marked_stale_scopes") else ""))
            return 0
        if args.cmd == "prove":
            bdir, _, _, _ = _board_paths(board)
            if (eval_fn := _get_evaluate_lattice_proof()) is None: raise RuntimeError("evaluate_lattice_proof is not yet promoted to lattice.whiteboard")
            proof = dict(eval_fn(_load_board(board)[0]))
            b_ok, b_path, b_out = _run_bend_proof_check(bdir, str(proof.get("bend_proof_source") or ""))
            proof.update({"bend_check_passed": b_ok, "bend_proof_file": str(b_path), "bend_output": b_out})
            record_flight_event(board, "prove", (time.perf_counter() - t0_cmd) * 1000.0, "OK" if proof.get("is_proven") and b_ok else "REJECTED", decision=proof.get("status"))
            print(json.dumps(proof, indent=2, sort_keys=True, ensure_ascii=False) if args.as_json else f"=== LATTICE PROOF '{board}' ===\nStatus:            {proof.get('status')} (is_proven={proof.get('is_proven')})\nCounts:            components={proof.get('component_count', 0)}, flows={proof.get('flow_count', 0)}, laws={proof.get('law_count', 0)}, contracts={proof.get('contract_count', 0)}\nBend 2 Check:      {'PASS' if b_ok else 'FAIL'} ({b_path})")
            if not args.as_json: _print_proof_radar(proof)
            return 0
        if args.cmd == "status":
            if (trans_fn := _get_transition_build_status()) is None: raise RuntimeError("transition_component_build_status is not yet promoted to lattice.whiteboard")
            graph, wb_json, wb_sqlite, transcript_path = _load_board(board)
            if args.new_status == "VERIFIED": _verify_component_files(graph, args.component)
            prev_tid = (graph.get("scopes", {}).get(args.component) or {}).get("active_ticket_id")
            prev_ready = {str(p.get("packet_id")) for p in (_call_compile_packets(graph, force=True, status_filter="READY").get("packets") or [])} if _get_compile_work_packets() else set()
            snode = trans_fn(graph, args.component, args.new_status, build_tag=args.build_tag, core_tag=args.core_tag, failure_diagnostic=args.reason)
            if prev_tid and not snode.get("active_ticket_id"): snode["active_ticket_id"] = prev_tid
            all_pkts = (_call_compile_packets(graph, force=True).get("packets") or []) if _get_compile_work_packets() else []
            comp_pkt, ready_pkts = next((p for p in all_pkts if p.get("component") == args.component), None), [p for p in all_pkts if str(p.get("build_status") or "").upper() == "READY"]
            unlocked_pkts = [p for p in ready_pkts if str(p.get("packet_id")) not in prev_ready]
            t_info = _sync_status_ticket(board, args.component, args.new_status, snode, comp_pkt, args.for_div, args.dispatch) if not args.no_ticket else {"ticket_id": snode.get("active_ticket_id")}
            _save_board(graph, wb_json, wb_sqlite)
            payload = {"component": args.component, "status": snode.get("status"), "build_tag": snode.get("build_tag"), "core_tag": snode.get("core_tag"), "failure_diagnostic": snode.get("failure_diagnostic"), "active_ticket_id": snode.get("active_ticket_id"), "ticket_info": t_info, "ready_packets": [p.get("packet_id") for p in ready_pkts], "newly_unlocked_packets": [p.get("packet_id") for p in unlocked_pkts]}
            _append_transcript(transcript_path, {"event": "status", "timestamp_utc": datetime.now(timezone.utc).isoformat(), **payload})
            record_flight_event(board, f"status:{args.component}", (time.perf_counter() - t0_cmd) * 1000.0, "OK", decision=snode.get("status"), modified_scopes=[args.component])
            if args.as_json: print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)); return 0
            print(f"[STATUS] {args.component} -> status={snode.get('status')} build_tag={snode.get('build_tag')} core_tag={snode.get('core_tag')} ticket={snode.get('active_ticket_id')} failure_diagnostic={snode.get('failure_diagnostic')!r}" + (f"\n[NEWLY UNLOCKED READY PACKETS ({len(unlocked_pkts)})]: " + ", ".join(f"{p.get('packet_id')} (owner={p.get('owner')})" for p in unlocked_pkts) if unlocked_pkts else "") + f"\n[READY PACKETS ({len(ready_pkts)})]: {', '.join(str(p.get('packet_id')) for p in ready_pkts) or 'none'}" + (f"\n\n[Automated Ticket ({t_info.get('ticket_action')})]:\n{t_info['ticket_output']}" if t_info.get("ticket_output") else "") + (f"\n\n[WAKE DISPATCH] Recipient: {t_info.get('wake_recipient')} ({t_info.get('wake_cid')})\n  {t_info['wake_line']}" if t_info.get("wake_line") else ""))
            return 0
        if args.cmd == "packets":
            res = _call_compile_packets(_load_board(board)[0], force=args.force, status_filter=args.status)
            pkts = [p for p in (res.get("packets") or []) if (args.wave is None or p.get("wave") == args.wave) and (not args.component or p.get("component") == args.component) and (not args.status or str(p.get("build_status") or "").upper() == args.status)]
            record_flight_event(board, "packets", (time.perf_counter() - t0_cmd) * 1000.0, "OK" if res.get("ready_to_emit") or args.force else "REJECTED", decision=res.get("status"))
            if args.as_json: print(json.dumps({**res, "packets": pkts} if (args.wave is not None or args.component or args.status) else res, indent=2, sort_keys=True, ensure_ascii=False)); return 0
            if not res.get("ready_to_emit") and not args.force: print(f"=== WORK PACKETS LOCKED ('{board}') ===\nStatus: {res.get('status')} (requires PROVEN_COMPLETE; pass --force to preview draft packets)"); _print_proof_radar(res.get("lattice_proof") or res); return 0
            print(f"=== WORK PACKETS '{board}' ({len(pkts)} packets | status={res.get('status')} | ready_to_emit={res.get('ready_to_emit')}) ===")
            for p in pkts: print(f"\n[{p.get('packet_id')}] wave={p.get('wave')} kind={p.get('kind')} component={p.get('component')} owner={p.get('owner')} stage={p.get('stage')} build_status={p.get('build_status')} build_tag={p.get('build_tag')} core_tag={p.get('core_tag')} failure_diagnostic={p.get('failure_diagnostic')!r}\n{p.get('prompt_slice') or ''}")
            return 0
        if args.cmd == "show":
            summary = _assemble_board_summary(_load_board(board)[0], args.scope)
            record_flight_event(board, "show", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            if args.as_json: print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False)); return 0
            print(f"=== WHITEBOARD '{board}' (Turn {summary['turn']}) ===")
            _print_proof_radar(summary.get("lattice_proof"))
            for sid, sdata in summary["scopes"].items():
                print(f"\n[Scope: {sid}] (parent={sdata['parent']}, children={sdata['children']}, owner={sdata.get('owner')}, stage={sdata.get('stage')}, status={sdata.get('status')}, ticket={sdata.get('active_ticket_id')}, build_tag={sdata.get('build_tag')}, core_tag={sdata.get('core_tag')})" + (f"\n  ! Failure Diagnostic: {sdata['failure_diagnostic']}" if sdata.get("failure_diagnostic") else "") + (f"\n  Attributes: {json.dumps(sdata['attributes'], sort_keys=True)}" if sdata["attributes"] else ""))
                for r in sdata["rules"]: print(f"  - [{r['id']}] ({r['type']}) prop={r.get('target_prop')!r} op={r.get('bound_op')!r} val={r.get('bound_val')!r}: {r['text']}")
            for label, key, fmt in (("Flows", "flows", lambda f: f"  - [{f.get('id')}] {f.get('source')} -> {f.get('target')} ({f.get('payload')}): {f.get('text')}"), ("Lattice Artifacts", "lattice_artifacts", lambda a: f"  - [{a.get('id')}] {a.get('kind')}@{a.get('scope_id')} {a.get('name')}: {a.get('spec') or a.get('text')}"), ("Active Tasks", "active_tasks", lambda t: f"  - [{t.get('id')}] status={t.get('status')} scope={t.get('scope')}: {t.get('text')}")):
                if summary[key]: print(f"\n[{label}]:\n" + "\n".join(fmt(x) for x in summary[key]))
            print("\n[Read Engine Context Pack]:\n" + (summary["assemble_context_pack"].get("compact_view") or summary["assemble_context_pack"].get("text") or "[EMPTY_WHITEBOARD]"))
            return 0
        if args.cmd == "history":
            records = _read_transcript(_board_paths(board)[3])
            record_flight_event(board, "history", (time.perf_counter() - t0_cmd) * 1000.0, "OK")
            if args.as_json: print(json.dumps(records, indent=2, ensure_ascii=False)); return 0
            for idx, rec in enumerate(records, 1): print(f"[{idx}] {rec['event'].upper()} by {rec.get('speaker')}: rule_id={rec.get('rule_id')} removed={rec.get('removed')}" if rec.get("event") in ("revoke", "replace") else f"[{idx}] STATUS {rec.get('component')} -> {rec.get('status')} (ticket={rec.get('active_ticket_id')}, build_tag={rec.get('build_tag')}, core_tag={rec.get('core_tag')})" if rec.get("event") == "status" else f"[{idx}] Turn {rec.get('turn_index')} (Round {rec.get('round')}, {rec.get('speaker')}): {rec.get('decision')} ({rec.get('bounce_reason')}) mutated={rec.get('whiteboard_mutated')}\n    Input: {rec.get('raw_text')}\n    Reply: {rec.get('user_message')}")
            return 0
    except Exception as exc:
        record_flight_event(board, args.cmd, (time.perf_counter() - t0_cmd) * 1000.0, "ERROR", error=format_exception_error(exc))
        raise
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
