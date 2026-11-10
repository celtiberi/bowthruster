"""Flight recorder, gate config, Bend proof runner, and AST extractors (`flight_recorder.py`)."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from typing import Any

from bowthruster.nlp import _extract_generic_source_symbols

BOWTHRUSTER_HOME = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATHS = (
    BOWTHRUSTER_HOME / "config.json",
    BOWTHRUSTER_HOME / "benchmarks" / "configs" / "config.json",
)


def _active_repo_root() -> Path:
    cli_mod = sys.modules.get("bowthruster.cli")
    if cli_mod is not None and hasattr(cli_mod, "REPO_ROOT"):
        return Path(getattr(cli_mod, "REPO_ROOT")).resolve()
    env_root = os.environ.get("BOWTHRUSTER_PROJECT_ROOT")
    return Path(env_root).resolve() if env_root else BOWTHRUSTER_HOME


def _load_gate_config(active_root: Path | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for cfg_path in DEFAULT_CONFIG_PATHS:
        if cfg_path.is_file():
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data.get("gate"), dict):
                merged.update(data["gate"])
            break
    repo_cfg = (active_root or _active_repo_root()) / "config.json"
    if repo_cfg.is_file():
        try:
            repo_data = json.loads(repo_cfg.read_text(encoding="utf-8"))
            if isinstance(repo_data.get("gate"), dict):
                merged.update(repo_data["gate"])
        except (OSError, json.JSONDecodeError):
            pass
    return merged


def format_exception_error(exc: BaseException) -> dict[str, Any]:
    tb = traceback.extract_tb(exc.__traceback__)
    last = tb[-1] if tb else None
    return {
        "type": type(exc).__name__,
        "file": last.filename if last else "",
        "line": int(last.lineno) if last else 0,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }


def record_flight_event(
    board: str,
    tool_or_cmd: str,
    duration_ms: float,
    status: str = "OK",
    *,
    decision: Any = None,
    violations: list[Any] | None = None,
    modified_scopes: list[str] | None = None,
    stale_cascaded_scopes: list[str] | None = None,
    response_bytes: int = 0,
    error: Any = None,
) -> dict[str, Any]:
    flog_p = _active_repo_root() / ".whiteboard" / "logs" / "bowthruster_events.jsonl"
    flog_p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "board": str(board or "default"),
        "tool_or_cmd": str(tool_or_cmd),
        "duration_ms": round(float(duration_ms), 2),
        "status": str(status).upper(),
        "decision": decision,
        "violations": list(violations or []),
        "modified_scopes": list(modified_scopes or []),
        "stale_cascaded_scopes": list(stale_cascaded_scopes or []),
        "response_bytes": int(response_bytes or 0),
        "error": error,
    }
    with flog_p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


def read_flight_logs(
    limit: int = 20, board: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    flog_p = _active_repo_root() / ".whiteboard" / "logs" / "bowthruster_events.jsonl"
    if not flog_p.is_file():
        return []
    lines = flog_p.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(ln) for ln in lines if ln.strip()]
    filtered = [
        r
        for r in rows
        if (not board or r.get("board") == board)
        and (not status or str(r.get("status") or "").upper() == status.upper())
    ]
    return filtered[-max(1, int(limit)) :]


_PY_AST_CACHE: dict[
    tuple[str, int], tuple[dict[str, tuple[int, int]], dict[str, list[str]]]
] = {}


def _py_ast_extractor(fp: Path) -> tuple[dict[str, tuple[int, int]], dict[str, list[str]]]:
    ckey = (str(fp), fp.stat().st_mtime_ns if fp.is_file() else 0)
    if (hit := _PY_AST_CACHE.get(ckey)) is not None:
        return hit
    try:
        tree = ast.parse(fp.read_text(encoding="utf-8", errors="ignore"))
        nodes = list(ast.walk(tree))
    except SyntaxError:
        return {}, {}
    fn_names = ("FunctionDef", "AsyncFunctionDef")
    fn_nodes = [n for n in reversed(nodes) if type(n).__name__ in fn_names]
    syms = {
        n.name: (int(n.lineno), int(getattr(n, "end_lineno", None) or n.lineno))
        for n in reversed(nodes)
        if type(n).__name__ in (*fn_names, "ClassDef") and getattr(n, "lineno", 0) > 0
    }
    for n in getattr(tree, "body", []):
        if type(n).__name__ == "Assign" and getattr(n, "lineno", 0) > 0:
            end_ln = int(getattr(n, "end_lineno", None) or n.lineno)
            for t in n.targets:
                if hasattr(t, "id"):
                    syms.setdefault(t.id, (int(n.lineno), end_ln))
    calls = {
        fn.name: sorted({
            f"{c}#L{syms[c][0]}-L{syms[c][1]}"
            for s in ast.walk(fn)
            if (
                type(s).__name__ == "Call"
                and (c := getattr(s.func, "id", "")) in syms
                and c != fn.name
                and c.startswith("_")
            )
        })
        for fn in fn_nodes
    }
    res = (syms, calls)
    _PY_AST_CACHE[ckey] = res
    return res


AST_EXTRACTORS: dict[str, Any] = {
    ".py": _py_ast_extractor,
    **{
        e: _extract_generic_source_symbols
        for e in (
            ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java", ".kt",
            ".c", ".cpp", ".cc", ".h", ".hpp", ".cs", ".swift", ".rb", ".bend",
        )
    },
}


def register_ast_extractor(ext: str, extractor_fn: Any) -> None:
    AST_EXTRACTORS[ext if ext.startswith(".") else f".{ext}"] = extractor_fn


def _extract_py_ast_symbols(fp: Path) -> dict[str, tuple[int, int]]:
    fn = AST_EXTRACTORS.get(fp.suffix, _extract_generic_source_symbols)
    return fn(fp)[0] if fp.is_file() else {}


def _extract_ast_local_calls(fp: Path) -> dict[str, list[str]]:
    fn = AST_EXTRACTORS.get(fp.suffix, _extract_generic_source_symbols)
    return fn(fp)[1] if fp.is_file() else {}


def install_git_precommit_hook(
    repo_root: Path, board: str = "lattice_core", *, overwrite: bool = True
) -> Path | None:
    """Installs a portable `.git/hooks/pre-commit` script bound to `REPO_TOP` and `board`."""
    hooks_dir = repo_root / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return None
    hook_path = hooks_dir / "pre-commit"
    if hook_path.exists() and not overwrite:
        return hook_path
    cli_args = f'--project-root "$REPO_TOP" --board "{board}" git-sync --pre-commit'
    script = (
        "#!/usr/bin/env sh\n"
        f"# BowThruster pre-commit verification gate (board: {board})\n"
        'REPO_TOP="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"\n'
        'export BOWTHRUSTER_PROJECT_ROOT="$REPO_TOP"\n'
        'if [ -x "$REPO_TOP/.venv/bin/bowthruster" ]; then\n'
        f'  exec "$REPO_TOP/.venv/bin/bowthruster" {cli_args}\n'
        "fi\n"
        'if [ -x "$REPO_TOP/.venv/bin/python" ] && '
        '"$REPO_TOP/.venv/bin/python" -c "import bowthruster.cli" >/dev/null 2>&1; then\n'
        f'  exec "$REPO_TOP/.venv/bin/python" -m bowthruster.cli {cli_args}\n'
        "fi\n"
        "if command -v bowthruster >/dev/null 2>&1; then\n"
        f"  exec bowthruster {cli_args}\n"
        "fi\n"
        "if command -v python3 >/dev/null 2>&1 && "
        'python3 -c "import bowthruster.cli" >/dev/null 2>&1; then\n'
        f"  exec python3 -m bowthruster.cli {cli_args}\n"
        "fi\n"
        'echo "[BowThruster] pre-commit hook skipped: '
        "module 'bowthruster' not installed (failing open).\" >&2\n"
        "exit 0\n"
    )
    hook_path.write_text(script, encoding="utf-8")
    hook_path.chmod(0o755)
    return hook_path


def _run_bend_proof_check(session_dir: Path, bend_source: str) -> tuple[bool, Path, str]:
    proof_path = session_dir / "LATTICE_PROOF.bend"
    proof_path.write_text(bend_source, encoding="utf-8")
    cand = Path(os.environ.get("BEND_BIN", str(Path.home() / ".bend" / "bin" / "bend")))
    which_bend = shutil.which("bend")
    bend_exe = (
        cand
        if (cand.is_file() and os.access(cand, os.X_OK))
        else (Path(which_bend) if which_bend else None)
    )
    if bend_exe is None:
        return False, proof_path, "bend binary not found"
    env = {**os.environ, "BEND_NO_TELEMETRY": "1"}
    proc = subprocess.run(
        [str(bend_exe), str(proof_path)],
        cwd=str(session_dir),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0 and "unknown" in (proc.stderr or "").lower():
        proc = subprocess.run(
            [str(bend_exe), "check", str(proof_path)],
            cwd=str(session_dir),
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
    ok = proc.returncode == 0 and ("ALL PROOFS CHECK" in proc.stdout or not proc.stderr.strip())
    return ok, proof_path, (proc.stdout + "\n" + proc.stderr).strip()


def _print_proof_radar(proof: dict[str, Any] | None) -> None:
    if proof:
        gaps = proof.get("open_gaps") or []
        viols = proof.get("hard_violations") or []
        c_cnt = proof.get("component_count", 0)
        f_cnt = proof.get("flow_count", 0)
        l_cnt = proof.get("law_count", 0)
        k_cnt = proof.get("contract_count", 0)
        print(
            f"\n[Lattice Proof Status]: {proof.get('status')} "
            f"(components={c_cnt}, flows={f_cnt}, laws={l_cnt}, "
            f"contracts={k_cnt}, gaps={len(gaps)})"
        )
        for v in viols:
            print(f"  ! [VIOLATION] {v.get('id', 'conflict')}: {v.get('text', v)}")
        for g in gaps:
            print(
                f"  - [{g.get('kind', 'GAP')}] scope={g.get('scope')}: "
                f"{g.get('description', '')}"
            )
