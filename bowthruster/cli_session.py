"""Board session locking, persistence, atlas loading, and turn execution (`bowthruster.cli_session`)."""

from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

import bowthruster.whiteboard as _wb
from bowthruster.git_manifest import _print_proof_radar, record_flight_event
from bowthruster.graph_core import SQLiteGraphStore
from bowthruster.whiteboard import ContextGraph, create_empty_context_graph, sync_to_sqlite

_META_LOCK = threading.Lock()
_RLOCKS: dict[str, threading.RLock] = {}
_LOCK_DEPTH: dict[str, tuple[int, Any]] = {}


def _sessions_root() -> Path:
    import bowthruster.cli as _cli

    return _cli.SESSIONS_ROOT


def _repo_root() -> Path:
    import bowthruster.cli as _cli

    return _cli.REPO_ROOT


@contextlib.contextmanager
def _board_lock(board: str):
    slug = "".join(c for c in board.strip() if c.isalnum() or c in ("_", "-")) or "default"
    bdir = _sessions_root() / slug
    bdir.mkdir(parents=True, exist_ok=True)
    key = str(bdir)
    with _META_LOCK:
        rlock = _RLOCKS.setdefault(key, threading.RLock())
    with rlock:
        depth, fh = _LOCK_DEPTH.get(key) or (0, None)
        if depth == 0:
            fh = (bdir / ".board.lock").open("a+", encoding="utf-8")
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _LOCK_DEPTH[key] = (depth + 1, fh)
        try:
            yield
        finally:
            if depth == 0:
                _LOCK_DEPTH.pop(key, None)
                if fcntl is not None and fh is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                if fh is not None:
                    fh.close()
            else:
                _LOCK_DEPTH[key] = (depth, fh)


def _board_paths(board: str, *, create_dirs: bool = True) -> tuple[Path, Path, Path, Path]:
    slug = "".join(c for c in board.strip() if c.isalnum() or c in ("_", "-")) or "default"
    wb_local = _repo_root() / ".whiteboard" / f"{board}.json"
    bdir = _sessions_root() / slug
    if create_dirs:
        bdir.mkdir(parents=True, exist_ok=True)
    has_env_root = bool(os.environ.get("BOWTHRUSTER_PROJECT_ROOT"))
    is_named = slug not in ("default", "dogfood") and not slug.endswith("_live")
    if create_dirs and is_named and (wb_local.parent.is_dir() or has_env_root):
        wb_local.parent.mkdir(parents=True, exist_ok=True)
    has_session_wb = (bdir / "whiteboard.json").is_file()
    use_local = wb_local.is_file() or (
        is_named and (has_env_root or (not has_session_wb and wb_local.parent.is_dir()))
    )
    wb_json = wb_local if use_local else bdir / "whiteboard.json"
    return bdir, wb_json, bdir / "whiteboard.sqlite", bdir / "transcript.jsonl"


def _save_board(graph: dict[str, Any], wb_json: Path, wb_sqlite: Path) -> None:
    wb_json.parent.mkdir(parents=True, exist_ok=True)
    wb_sqlite.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(graph, ContextGraph):
        object.__setattr__(graph, "_sqlite_store", SQLiteGraphStore(wb_sqlite))
    graph["schema_version"] = int(graph.get("schema_version") or _wb.BOARD_SCHEMA_VERSION)
    graph.setdefault("schema_migrations", [])
    sync_to_sqlite(graph)
    tmp_p = wb_json.with_name(f".{wb_json.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    skip_k = ("_sqlite_store", "_on_disk_schema_version")
    clean_graph = copy.deepcopy({k: v for k, v in dict(graph).items() if k not in skip_k})
    tmp_p.write_text(json.dumps(clean_graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_p, wb_json)


def _load_board(board: str) -> tuple[ContextGraph, Path, Path, Path]:
    with _board_lock(board):
        _, wb_json, wb_sqlite, transcript_path = _board_paths(board)
        store = SQLiteGraphStore(wb_sqlite)
        if wb_json.is_file():
            raw_obj = json.loads(wb_json.read_text(encoding="utf-8"))
            raw_data, _ = _wb.migrate_board_data(raw_obj, board_name=board)
            graph = ContextGraph(raw_data, store=store)
            for sid, raw_sc in (raw_data.get("scopes") or {}).items():
                if isinstance(raw_sc, dict) and raw_sc.get("active_ticket_id"):
                    if sid in graph.get("scopes", {}):
                        graph["scopes"][sid]["active_ticket_id"] = raw_sc["active_ticket_id"]
            sync_to_sqlite(graph)
        else:
            graph = create_empty_context_graph(store=store)
        return graph, wb_json, wb_sqlite, transcript_path


def _load_atlas_boards(
    explicit_board: str | None, boards_csv: str | None, load_all: bool
) -> dict[str, dict[str, Any]]:
    vault_wb = _repo_root() / ".whiteboard"
    sess_root = _sessions_root()
    if boards_csv:
        names = [x.strip() for x in boards_csv.split(",") if x.strip()]
    elif not load_all and explicit_board:
        names = [explicit_board]
    else:
        roots = (vault_wb,) if vault_wb.is_dir() else (sess_root,)
        cands = sorted({
            p.stem if p.name != "whiteboard.json" else p.parent.name
            for r in roots
            if r.is_dir()
            for p in r.rglob("*.json")
        })
        names = [
            n for n in cands
            if n not in ("default", "dogfood") and not n.endswith("_live")
        ] or [explicit_board or "default"]
    out: dict[str, dict[str, Any]] = {}
    for n in names:
        cand_paths = (
            vault_wb / f"{n}.json",
            vault_wb / n / "whiteboard.json",
            sess_root / n / "whiteboard.json",
        )
        if cand := next((c for c in cand_paths if c.is_file()), None):
            raw_d = json.loads(cand.read_text(encoding="utf-8"))
            disk_v = _wb.detect_board_schema_version(raw_d)
            d, _ = _wb.migrate_board_data(raw_d, board_name=n)
            d["_on_disk_schema_version"] = disk_v
            has_scopes = [k for k in (d.get("scopes") or {}) if k != "global_defaults"]
            if boards_csv or (not load_all and explicit_board) or has_scopes or d.get("flows"):
                out[n] = d
        elif boards_csv or (not load_all and explicit_board):
            out[n] = dict(_load_board(n)[0])
    return out or {explicit_board or "default": dict(_load_board(explicit_board or "default")[0])}


def _read_transcript(p: Path) -> list[dict[str, Any]]:
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _append_transcript(p: Path, record: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _execute_turn(
    board: str,
    speaker: str,
    raw_text: str,
    round_num: int | None = None,
    profile: str | None = None,
    config_path: str | None = None,
) -> int:
    import bowthruster.cli as _cli
    from bowthruster.the_executive import run_executive_turn

    _ = (profile, config_path)
    with _board_lock(board):
        graph, wb_json, wb_sqlite, transcript_path = _load_board(board)
        history = _read_transcript(transcript_path)
        if round_num is None:
            last_turn = next((h for h in reversed(history) if h.get("event", "turn") == "turn"), None)
            is_retry = bool(last_turn and last_turn.get("decision") == "SEND_BACK_TO_USER")
            round_num = int(last_turn.get("round", 1)) + 1 if is_retry and last_turn else 1
        t0 = time.perf_counter()
        res = run_executive_turn(graph, raw_text, db_path=wb_sqlite)
        elapsed_s = round(time.perf_counter() - t0, 3)
        if res["whiteboard_mutated"]:
            _save_board(res["whiteboard_graph"], wb_json, wb_sqlite)
        eval_fn = _cli._get_evaluate_lattice_proof()
        proof = res.get("lattice_proof") or (eval_fn(res["whiteboard_graph"]) if eval_fn else None)
        parsed_mcus = (res.get("intake_packet") or {}).get("parsed_mcus") or []
        _append_transcript(
            transcript_path,
            {
                "event": "turn",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "speaker": speaker,
                "round": round_num,
                "turn_index": res["turn_index"],
                "raw_text": raw_text,
                "decision": res["decision"],
                "bounce_reason": res["bounce_reason"],
                "whiteboard_mutated": res["whiteboard_mutated"],
                "user_message": res["user_message"],
                "cited_items": res["cited_items"],
                "committed_items": res.get("committed_items") or [],
                "conflicts": res.get("conflicts") or [],
                "flawed_ideas": res.get("flawed_ideas") or [],
                "clarification_questions": res.get("clarification_questions") or [],
                "open_questions": res.get("open_questions") or [],
                "parsed_mcus": parsed_mcus,
                "lattice_proof_status": (proof or {}).get("status"),
                "elapsed_s": elapsed_s,
            },
        )
    st_turn = "OK" if res["decision"] == "COMMIT_TO_WHITEBOARD" else "REJECTED"
    viols = list(res.get("conflicts") or []) + list(res.get("flawed_ideas") or [])
    record_flight_event(
        board, "turn", elapsed_s * 1000.0, st_turn, decision=res["decision"], violations=viols
    )
    cited = ", ".join(str(x) for x in res["cited_items"])
    cited_str = f"\nCited Items:       {cited}" if cited else ""
    print(
        f"=== EXECUTIVE TURN {res['turn_index']} (Round {round_num} | Speaker: {speaker}) ===\n"
        f"Decision:          {res['decision']}\n"
        f"Bounce Reason:     {res['bounce_reason']}\n"
        f"Whiteboard Mutated:{res['whiteboard_mutated']} (elapsed {elapsed_s:.2f}s)\n"
        f"Executive Reply:   {res['user_message']}{cited_str}\n"
        f"Parsed MCUs ({len(parsed_mcus)}):"
    )
    for m in parsed_mcus:
        scope_h = m.get("scope_hint") or "auto"
        print(f"  - [mcu_{m.get('mcu_id')}] {m.get('type')} scope={scope_h} prop={m.get('target_prop')!r}: {m.get('text')}")
    diag_keys = (
        ("Conflicts", "conflicts"),
        ("Flawed Ideas", "flawed_ideas"),
        ("Clarification Questions", "clarification_questions"),
        ("Open Questions", "open_questions"),
    )
    for label, key in diag_keys:
        if res.get(key):
            items_fmt = "\n".join(
                f"  * {json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item}"
                for item in res[key]
            )
            print(f"{label} ({len(res[key])}):\n{items_fmt}")
    _print_proof_radar(proof)
    return 0
