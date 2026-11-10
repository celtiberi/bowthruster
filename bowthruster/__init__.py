"""BowThruster: Deterministic Markdown Pipe-Table Architecture Memory, AST Context Slicer & Verification Engine."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
from typing import Any

__version__ = "0.2.0"

_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+)?"
    r"(?:(?:public|private|protected|internal|static|async|inline|virtual|override|abstract|final|const)\s+)*"
    r"(?:fn|func|function|def|class|interface|type|struct|enum)\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)"
)
_ARROW_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*"
    r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>"
)
_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _extract_generic_source_symbols(fp: Any) -> tuple[dict[str, tuple[int, int]], dict[str, list[str]]]:
    try:
        lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return {}, {}
    syms: dict[str, tuple[int, int]] = {}
    n_lines = len(lines)
    for i, line in enumerate(lines):
        m = _DECL_RE.match(line) or _ARROW_RE.match(line)
        if not m:
            continue
        name, s, e = m.group(1), i + 1, i + 1
        if "{" in line or (i + 1 < n_lines and "{" in lines[i + 1] and ";" not in line):
            j, depth, started = i, 0, False
            while j < n_lines:
                dl = lines[j].split("//", 1)[0]
                started = started or ("{" in dl)
                depth += dl.count("{") - dl.count("}")
                e = j + 1
                if started and depth <= 0:
                    break
                j += 1
        elif line.rstrip().endswith((":", "=")):
            base_indent, j = len(line) - len(line.lstrip()), i + 1
            while j < n_lines:
                nxt = lines[j]
                is_comment = nxt.lstrip().startswith(("#", "//"))
                if nxt.strip() and not is_comment and (len(nxt) - len(nxt.lstrip())) <= base_indent:
                    break
                if nxt.strip():
                    e = j + 1
                j += 1
        syms.setdefault(name, (s, max(s, e)))
    calls: dict[str, list[str]] = {}
    for fn_name, (s, e) in syms.items():
        body = "\n".join(lines[max(0, s - 1) : e])
        c_list = sorted({
            f"{c}#L{syms[c][0]}-L{syms[c][1]}"
            for c in _CALL_RE.findall(body)
            if c in syms and c != fn_name and c.startswith("_")
        })
        if c_list:
            calls[fn_name] = c_list
    return syms, calls


@contextmanager
def open_board_session(board: str, project_root: Any = None, save_on_exit: bool = False) -> Any:
    """Acquire cross-process board lock, load ContextGraph for `board`, and optionally persist on exit."""
    import bowthruster.cli as _cli
    from bowthruster.mcp_server import _resolve_project_root

    if project_root and str(project_root).strip():
        active_root = Path(str(project_root).strip()).expanduser().resolve()
    elif os.environ.get("BOWTHRUSTER_PROJECT_ROOT"):
        active_root = Path(os.environ["BOWTHRUSTER_PROJECT_ROOT"]).expanduser().resolve()
    elif (Path.cwd() / ".whiteboard").is_dir():
        active_root = Path.cwd().resolve()
    else:
        active_root = _resolve_project_root(board, None, None)

    with _cli._board_lock(board):
        prev_root, prev_env = _cli.REPO_ROOT, os.environ.get("BOWTHRUSTER_PROJECT_ROOT")
        _cli.REPO_ROOT = active_root
        os.environ["BOWTHRUSTER_PROJECT_ROOT"] = str(active_root)
        try:
            graph, wb_json, wb_sqlite, _ = _cli._load_board(board)
            yield graph, active_root
            if save_on_exit:
                _cli._save_board(graph, wb_json, wb_sqlite)
        finally:
            _cli.REPO_ROOT = prev_root
            if prev_env is None:
                os.environ.pop("BOWTHRUSTER_PROJECT_ROOT", None)
            else:
                os.environ["BOWTHRUSTER_PROJECT_ROOT"] = prev_env


__all__ = ["__version__", "_extract_generic_source_symbols", "open_board_session"]
