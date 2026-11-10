"""Board Schema Versioning & Deterministic Forward Migration Engine (`bowthruster/whiteboard/migrations.py`)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable

from bowthruster.graph_core import DEFAULT_K_CAP, DEFAULT_MAX_RING
from bowthruster.whiteboard.schema import normalize_board_graph

BOARD_SCHEMA_VERSION = 2


class BoardSchemaVersionError(ValueError):
    """Raised when a board's `schema_version` is malformed, unsupported, or newer than this binary."""


@dataclass(frozen=True)
class BoardMigrationReport:
    """Structured report of an ordered board schema migration run."""

    board: str = ""
    from_version: int = 0
    to_version: int = BOARD_SCHEMA_VERSION
    migrated: bool = False
    steps_applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "from_version": int(self.from_version),
            "to_version": int(self.to_version),
            "migrated": bool(self.migrated),
            "steps_applied": list(self.steps_applied),
        }


def detect_board_schema_version(raw_board: dict[str, Any]) -> int:
    """Detect explicit `schema_version` on raw board JSON, falling back to v1/v0 for pre-versioned boards."""
    if not isinstance(raw_board, dict):
        raise BoardSchemaVersionError(f"Board payload must be a dict, got {type(raw_board).__name__}")
    if "schema_version" in raw_board and raw_board["schema_version"] is not None:
        sv = raw_board["schema_version"]
        if isinstance(sv, bool) or not isinstance(sv, int) or sv < 0:
            raise BoardSchemaVersionError(f"Invalid board schema_version={sv!r}; expected non-negative integer")
        return sv
    rev = raw_board.get("version")
    if isinstance(rev, int) and not isinstance(rev, bool) and rev >= 1:
        return 1
    if any(k in raw_board for k in ("turn", "turn_index", "charter")):
        return 1
    return 0


def _step_v0_to_v1_initialize_containers(data: dict[str, Any]) -> None:
    turn_val = int(data.get("turn", data.get("turn_index", 0)) or 0)
    data.setdefault("version", 1)
    data["turn"] = turn_val
    data["turn_index"] = int(data.get("turn_index", turn_val) or turn_val)
    data.setdefault("k_cap", DEFAULT_K_CAP)
    data.setdefault("max_ring", DEFAULT_MAX_RING)
    for dict_key in ("scopes", "alias_index"):
        if not isinstance(data.get(dict_key), dict):
            data[dict_key] = {}
    for list_key in ("flows", "lattice_artifacts", "ambient_notes", "active_tasks", "open_blockers"):
        if not isinstance(data.get(list_key), list):
            data[list_key] = []
    ext = data.get("external_links")
    if isinstance(ext, list):
        by_node: dict[str, list[dict[str, Any]]] = {}
        for lk in ext:
            if isinstance(lk, dict) and lk.get("node_id"):
                by_node.setdefault(str(lk["node_id"]), []).append(lk)
        data["external_links"] = by_node
    elif not isinstance(ext, dict):
        data["external_links"] = {}


def _step_v1_to_v2_canonicalize_scopes(data: dict[str, Any]) -> None:
    normalize_board_graph(data)
    scopes = data.get("scopes")
    if isinstance(scopes, dict):
        for sc in scopes.values():
            if isinstance(sc, dict):
                sc.pop("test_files", None)
                attrs = sc.get("attributes")
                if isinstance(attrs, dict):
                    attrs.pop("test_files", None)


_MIGRATION_STEPS: dict[int, tuple[str, Callable[[dict[str, Any]], None]]] = {
    0: ("v0_to_v1_initialize_containers", _step_v0_to_v1_initialize_containers),
    1: ("v1_to_v2_canonicalize_scopes_tests_claims_and_artifacts", _step_v1_to_v2_canonicalize_scopes),
}


def migrate_board_data(
    raw_board: dict[str, Any],
    target_version: int = BOARD_SCHEMA_VERSION,
    board_name: str = "",
) -> tuple[dict[str, Any], BoardMigrationReport]:
    """Apply ordered deterministic migrations up to `target_version`, rejecting newer board versions."""
    if isinstance(target_version, bool) or not isinstance(target_version, int) or target_version < 1:
        raise BoardSchemaVersionError(f"Invalid target_version={target_version!r}; expected integer >= 1")
    start_ver = detect_board_schema_version(raw_board)
    charter_name = (raw_board.get("charter") or {}).get("name") if isinstance(raw_board.get("charter"), dict) else ""
    b_label = str(board_name or charter_name or "board").strip() or "board"
    if start_ver > target_version:
        raise BoardSchemaVersionError(
            f"Board {b_label!r} has schema_version={start_ver}, which is newer than supported "
            f"schema_version={target_version}; upgrade bowthruster before opening or modifying this board"
        )
    clean_in = {k: v for k, v in raw_board.items() if k not in ("_sqlite_store", "_on_disk_schema_version")}
    data = copy.deepcopy(clean_in)
    history_raw = data.get("schema_migrations")
    history: list[dict[str, Any]] = [dict(x) for x in history_raw if isinstance(x, dict)] if isinstance(history_raw, list) else []
    steps_applied: list[str] = []
    cur = start_ver
    while cur < target_version:
        if cur not in _MIGRATION_STEPS:
            raise BoardSchemaVersionError(
                f"No registered board migration step from schema_version={cur} to {cur + 1}"
            )
        step_name, step_fn = _MIGRATION_STEPS[cur]
        step_fn(data)
        steps_applied.append(step_name)
        entry = {"from_version": cur, "to_version": cur + 1, "step": step_name}
        if entry not in history:
            history.append(entry)
        cur += 1
    normalize_board_graph(data)
    data["schema_version"] = target_version
    data["schema_migrations"] = history
    report = BoardMigrationReport(
        board=b_label,
        from_version=start_ver,
        to_version=target_version,
        migrated=(start_ver != target_version),
        steps_applied=steps_applied,
    )
    return data, report


def migrate_board_file(
    board_path: Path,
    target_version: int = BOARD_SCHEMA_VERSION,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Read a `.whiteboard` JSON file, migrate to `target_version`, and atomically write when not `dry_run`."""
    fp = Path(board_path).resolve()
    if not fp.is_file():
        raise FileNotFoundError(f"Board file not found: {fp}")
    b_name = fp.parent.name if fp.name == "whiteboard.json" else fp.stem
    raw_obj = json.loads(fp.read_text(encoding="utf-8"))
    migrated_data, report = migrate_board_data(raw_obj, target_version=target_version, board_name=b_name)
    if report.migrated and not dry_run:
        tmp_p = fp.with_name(f".{fp.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp_p.write_text(json.dumps(migrated_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp_p, fp)
    return {
        **report.to_dict(),
        "path": str(fp),
        "dry_run": bool(dry_run),
        "written": bool(report.migrated and not dry_run),
    }


def migrate_repo_boards(
    repo_root: Path,
    board: str | None = None,
    migrate_all: bool = False,
    dry_run: bool = False,
    target_version: int = BOARD_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Migrate a single named board or all discovered `.whiteboard/*.json` files in `repo_root`."""
    root = Path(repo_root).resolve()
    wb_dir = root / ".whiteboard"
    targets: list[Path] = []
    if migrate_all:
        if wb_dir.is_dir():
            targets = sorted(p for p in wb_dir.glob("*.json") if p.is_file())
    else:
        b_slug = str(board or "lattice_core").strip() or "lattice_core"
        cand = wb_dir / f"{b_slug}.json"
        if not cand.is_file():
            sess_cand = Path("/tmp/whiteboard_sessions") / b_slug / "whiteboard.json"
            cand = sess_cand if sess_cand.is_file() else cand
        if not cand.is_file():
            raise FileNotFoundError(f"Board {b_slug!r} not found at {wb_dir / f'{b_slug}.json'}")
        targets = [cand]
    results = [migrate_board_file(p, target_version=target_version, dry_run=dry_run) for p in targets]
    migrated_count = sum(1 for r in results if r["migrated"])
    return {
        "schema_version": int(target_version),
        "dry_run": bool(dry_run),
        "board_count": len(results),
        "migrated_count": migrated_count,
        "boards": results,
    }


def format_migration_report(payload: dict[str, Any]) -> str:
    """Render bounded human/agent-readable summary of a `migrate_repo_boards` payload."""
    mode_tag = "DRY-RUN" if payload.get("dry_run") else "APPLIED"
    hdr = (
        f"[BOARD SCHEMA MIGRATION ({mode_tag}) target=v{payload.get('schema_version')} "
        f"migrated={payload.get('migrated_count', 0)}/{payload.get('board_count', 0)}]"
    )
    lines = [hdr]
    for item in payload.get("boards") or []:
        steps = ", ".join(item.get("steps_applied") or []) or "already_up_to_date"
        state = ("would_migrate" if payload.get("dry_run") else "migrated") if item.get("migrated") else "up_to_date"
        lines.append(
            f"- {item.get('board')}: v{item.get('from_version')} -> v{item.get('to_version')} ({state}: {steps})"
        )
    return "\n".join(lines)


__all__ = [
    "BOARD_SCHEMA_VERSION",
    "BoardMigrationReport",
    "BoardSchemaVersionError",
    "detect_board_schema_version",
    "format_migration_report",
    "migrate_board_data",
    "migrate_board_file",
    "migrate_repo_boards",
]
