"""Git-Lattice Manifest Builder, Polyglot AST Extractors, Flight Recorder & Delta Test Runner (`git_manifest.py`)."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from typing import Any

from bowthruster.nlp import _extract_generic_source_symbols, verify_python_ast_laws_with_bend

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
    decision: str | None = None,
    violations: list[Any] | None = None,
    modified_scopes: list[str] | None = None,
    stale_cascaded_scopes: list[str] | None = None,
    response_bytes: int = 0,
    error: dict[str, Any] | None = None,
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
    limit: int = 20,
    board: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    logs_dir = _active_repo_root() / ".whiteboard" / "logs"
    flog_p = logs_dir / "bowthruster_events.jsonl"
    if not flog_p.is_file():
        return []
    rows = [json.loads(ln) for ln in flog_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    filtered = [
        r
        for r in rows
        if (not board or r.get("board") == board)
        and (not status or str(r.get("status") or "").upper() == status.upper())
    ]
    return filtered[-max(1, int(limit)) :]


_PY_AST_CACHE: dict[tuple[str, int], tuple[dict[str, tuple[int, int]], dict[str, list[str]]]] = {}


def _py_ast_extractor(fp: Path) -> tuple[dict[str, tuple[int, int]], dict[str, list[str]]]:
    ckey = (str(fp), fp.stat().st_mtime_ns if fp.is_file() else 0)
    if (hit := _PY_AST_CACHE.get(ckey)) is not None:
        return hit
    try:
        tree = ast.parse(fp.read_text(encoding="utf-8", errors="ignore"))
        nodes = list(ast.walk(tree))
    except SyntaxError:
        return {}, {}
    fn_nodes = [n for n in reversed(nodes) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    syms = {
        n.name: (int(n.lineno), int(getattr(n, "end_lineno", None) or n.lineno))
        for n in reversed(nodes)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and getattr(n, "lineno", 0) > 0
    }
    for n in getattr(tree, "body", []):
        if isinstance(n, ast.Assign) and getattr(n, "lineno", 0) > 0:
            for t in n.targets:
                if isinstance(t, ast.Name):
                    syms.setdefault(t.id, (int(n.lineno), int(getattr(n, "end_lineno", None) or n.lineno)))
    calls = {
        fn.name: sorted({
            f"{c}#L{syms[c][0]}-L{syms[c][1]}"
            for s in ast.walk(fn)
            if isinstance(s, ast.Call) and isinstance(s.func, ast.Name) and (c := s.func.id) in syms and c != fn.name and c.startswith("_")
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
            ".ts", ".tsx", ".js", ".jsx", ".mjs",
            ".go", ".rs", ".java", ".kt", ".c",
            ".cpp", ".cc", ".h", ".hpp", ".cs",
            ".swift", ".rb", ".bend"
        )
    },
}


def register_ast_extractor(ext: str, extractor_fn: Any) -> None:
    AST_EXTRACTORS[ext if ext.startswith(".") else f".{ext}"] = extractor_fn


def _extract_py_ast_symbols(fp: Path) -> dict[str, tuple[int, int]]:
    return AST_EXTRACTORS.get(fp.suffix, _extract_generic_source_symbols)(fp)[0] if fp.is_file() else {}


def _extract_ast_local_calls(fp: Path) -> dict[str, list[str]]:
    return AST_EXTRACTORS.get(fp.suffix, _extract_generic_source_symbols)(fp)[1] if fp.is_file() else {}


def _clean_ref_path(raw: str) -> str:
    s = str(raw or "").strip()
    for pfx in ("uri://file/", "uri://", "file://", "./"):
        s = s.removeprefix(pfx)
    return s.split("#", 1)[0].split(":", 1)[0].strip()


def _collect_ref_map(graph: dict[str, Any] | None, comp_filter: str | None = None) -> dict[str, tuple[str, str]]:
    ref_map: dict[str, tuple[str, str]] = {}
    for sid, sc in ((graph or {}).get("scopes") or {}).items():
        if isinstance(sc, dict) and (not comp_filter or sid == comp_filter):
            attrs = sc.get("attributes") if isinstance(sc.get("attributes"), dict) else {}
            for r in (*(sc.get("files") or []), *(attrs.get("files") or []), *(attrs.get("_table_files") or [])):
                s = str(r).strip()
                if s and (cp := _clean_ref_path(s)):
                    ref_map[cp] = (sid, s.split("#", 1)[0])
    raw_arts = (graph or {}).get("lattice_artifacts") or []
    for art in (raw_arts.values() if isinstance(raw_arts, dict) else raw_arts):
        if isinstance(art, dict):
            r = str(art.get("file") or "").strip()
            asid = str(art.get("scope") or "").strip()
            if r and asid and (not comp_filter or asid == comp_filter):
                cp = _clean_ref_path(r)
                if cp and cp not in ref_map:
                    ref_map[cp] = (asid, r.split("#", 1)[0])
    return ref_map


def _verify_component_files(graph: dict[str, Any], comp: str) -> None:
    gate_cfg = next(
        (json.loads(p.read_text(encoding="utf-8")) for p in DEFAULT_CONFIG_PATHS if p.is_file()),
        {}
    ).get("gate") or {}
    if not gate_cfg.get("verify_files_exist_on_status", True):
        return
    root = _active_repo_root()
    ref_m = _collect_ref_map(graph, comp)
    comp_files = list(ref_m.keys())
    has_env_root = bool(os.environ.get("BOWTHRUSTER_PROJECT_ROOT"))

    def res_fp(cp: str) -> Path:
        if (root / cp).is_file() or has_env_root or not (BOWTHRUSTER_HOME.parent / cp).is_file():
            return root / cp
        return BOWTHRUSTER_HOME.parent / cp

    if missing := [cp for cp in comp_files if not res_fp(cp).is_file()]:
        raise ValueError(f"Cannot mark '{comp}' VERIFIED; missing declared file(s) on disk: {', '.join(missing)}")
    raw_mlb = graph.get("max_file_lines") if graph.get("max_file_lines") is not None else gate_cfg.get("max_file_lines")
    mlb = int(raw_mlb) if isinstance(raw_mlb, (int, float)) and not isinstance(raw_mlb, bool) and raw_mlb > 0 else None
    for s_id in ("global_defaults", "max_file_lines", comp):
        for r in (((graph.get("scopes") or {}).get(s_id) or {}).get("rules") or []):
            if isinstance(r, dict) and (
                str(r.get("type") or "").upper() == "BOUND"
                or r.get("bound_op") is not None
            ) and r.get("bound_val") is not None:
                if str(r.get("target_prop") or "").strip().lower() in (
                    "max_file_lines", "file_lines", "line_count",
                    "lines", "max_lines"
                ) or "max_file_lines" in str(r.get("text") or "").lower():
                    try:
                        mlb = int(float(r["bound_val"]))
                    except (TypeError, ValueError):
                        pass
    if mlb is not None:
        over = [
            f"{cp} ({lcnt} > max_file_lines={mlb})"
            for cp in comp_files
            if cp.endswith((".py", ".bend")) and (lcnt := len(res_fp(cp).read_text(encoding="utf-8", errors="ignore").splitlines())) > mlb
        ]
        if over:
            raise ValueError(f"Cannot mark '{comp}' VERIFIED; max_file_lines exceeded: {', '.join(over)}")
    from bowthruster.the_executive.gate import evaluate_git_commit_gate

    manifest = _build_git_manifest("pre", None, graph)
    gate_res = evaluate_git_commit_gate(graph, manifest)
    if not gate_res.get("allowed"):
        viols = "; ".join(str(v.get("description") or v) for v in (gate_res.get("violations") or []))
        raise ValueError(
            f"Cannot mark '{comp}' VERIFIED; pre-commit gate rejected ({gate_res.get('bounce_reason')}): {viols}"
        )
    tests_ran, tests_ok, test_out = run_delta_tests(root, [comp], graph)
    if tests_ok is False:
        raise ValueError(f"Cannot mark '{comp}' VERIFIED; delta tests failed:\n{test_out[-600:]}")
    if isinstance(sc := ((graph.get("scopes") or {}).get(comp)), dict):
        from bowthruster.ai_memory import sync_git_diff_uris

        f_hashes = {
            raw_r: hashlib.sha256(res_fp(cp).read_bytes()).hexdigest()[:16]
            for cp, (_, raw_r) in ref_m.items()
            if res_fp(cp).is_file()
        }
        sc["file_hashes"] = dict(f_hashes)
        if isinstance(sc.get("attributes"), dict):
            sc["attributes"]["file_hashes"] = dict(f_hashes)
        sync_git_diff_uris(
            graph,
            {
                "ast_symbols_by_file": {
                    cp: _extract_py_ast_symbols(res_fp(cp))
                    for cp in comp_files if res_fp(cp).suffix in AST_EXTRACTORS
                    and res_fp(cp).is_file()
                },
                "ast_calls_by_file": {
                    cp: _extract_ast_local_calls(res_fp(cp))
                    for cp in comp_files if res_fp(cp).suffix in AST_EXTRACTORS
                    and res_fp(cp).is_file()
                },
            },
        )


def _match_component_test_files(
    active_root: Path, stale_sids: list[str],
    graph: dict[str, Any] | None = None
) -> list[str]:
    matched: list[str] = []
    scopes = (graph or {}).get("scopes") if isinstance((graph or {}).get("scopes"), dict) else {}
    for sid in stale_sids:
        sc = scopes.get(sid) if isinstance(scopes.get(sid), dict) else {}
        attrs = sc.get("attributes") if isinstance(sc.get("attributes"), dict) else {}
        raw_t = sc.get("tests") or sc.get("test_files") or attrs.get("tests") or attrs.get("test_files") or []
        t_items = raw_t if isinstance(raw_t, list) else ([p.strip() for p in str(raw_t).split(",") if p.strip()] if raw_t else [])
        for f_item in (sc.get("files") or []):
            s_item = str(f_item).strip()
            if s_item.startswith("tests:"):
                t_items.append(s_item.removeprefix("tests:").strip())
        for tf_rel in t_items:
            clean_t = _clean_ref_path(str(tf_rel))
            if clean_t and (active_root / clean_t).is_file() and clean_t not in matched:
                matched.append(clean_t)
    tests_dir = active_root / "tests"
    if not tests_dir.is_dir():
        return matched
    all_test_files = sorted(tests_dir.rglob("test_*.py"))
    for tf in all_test_files:
        rel_str = str(tf.relative_to(active_root))
        if rel_str in matched:
            continue
        stem_low = tf.stem.lower()
        short_stem = stem_low.removeprefix("test_")
        for sid in stale_sids:
            s_low = sid.lower()
            s_core = s_low.removeprefix("the_")
            if (
                s_low in stem_low
                or (short_stem and short_stem in s_low)
                or (len(s_core) >= 4 and s_core in stem_low)
            ):
                matched.append(rel_str)
                break
    return matched


def run_delta_tests(
    active_root: Path, stale_sids: list[str],
    graph: dict[str, Any] | None = None
) -> tuple[bool, bool | str, str]:
    """
    Runs discovered delta `pytest` files for `stale_sids` under `active_root / 'tests'`.
    Returns `(tests_ran, tests_passed_or_not_checked, output_summary)`.
    """
    if not stale_sids:
        return False, "not_checked", "no stale scopes"
    matched = _match_component_test_files(active_root, stale_sids, graph)
    if not matched:
        return False, "not_checked", "no matching test files found"
    if active_root.resolve() == BOWTHRUSTER_HOME.resolve() and os.environ.get("PYTEST_CURRENT_TEST"):
        return True, True, f"already_in_pytest_session ({', '.join(matched)})"
    env = {**os.environ, "PYTHONPATH": str(active_root)}
    pytest_bin = shutil.which("pytest")
    cmd = [pytest_bin, "-q", *matched] if pytest_bin else [sys.executable, "-m", "unittest", *matched]
    proc = subprocess.run(cmd, cwd=str(active_root), env=env, text=True, capture_output=True, timeout=60)
    out = (proc.stdout + "\n" + proc.stderr).strip()
    return True, proc.returncode == 0, out


def install_git_precommit_hook(
    repo_root: Path, board: str = "lattice_core",
    *, overwrite: bool = False
) -> Path | None:
    """Installs a `.git/hooks/pre-commit` script invoking `bowthruster.cli git-sync --pre-commit`."""
    hooks_dir = repo_root / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return None
    hook_path = hooks_dir / "pre-commit"
    if hook_path.exists() and not overwrite:
        return hook_path
    script = (
        "#!/usr/bin/env sh\n"
        f"# BowThruster pre-commit verification gate (board: {board})\n"
        f'"{sys.executable}" -m bowthruster.cli --board "{board}" git-sync --pre-commit\n'
    )
    hook_path.write_text(script, encoding="utf-8")
    hook_path.chmod(0o755)
    return hook_path


def _collect_git_changed_files(
    repos: list[Path],
    eff_root: Path,
    ref_map: dict[str, tuple[str, str]],
    mode: str,
    git_cmd: list[str],
    is_base: bool,
    graph: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], str | None]:
    sha: str | None = None
    files: list[dict[str, Any]] = []
    for r in ([] if is_base else repos):
        rel_pref = "" if r == eff_root else (r.relative_to(eff_root).as_posix() + "/")
        lines = [
            ln.strip() for ln in subprocess.run(
                git_cmd, cwd=str(r), text=True, capture_output=True, timeout=15
            ).stdout.splitlines()
            if ln.strip()
        ]
        if mode == "post" and lines and len(lines[0]) == 40:
            sha = lines.pop(0)
        for ln in lines:
            parts = ln.split("\t") if "\t" in ln else ln.split(None, 1)
            if len(parts) >= 2:
                st = parts[0].strip()
                path = rel_pref + parts[-1].strip()
                old_p = rel_pref + parts[1].strip() if len(parts) >= 3 else ""
                fp = eff_root / path
                sid_hit = (ref_map.get(path) or ref_map.get(old_p) or (None, ""))[0]
                lcnt = (
                    len(fp.read_text(encoding="utf-8", errors="ignore").splitlines())
                    if path.endswith((".py", ".bend")) else None
                ) if fp.is_file() else None
                fhash = hashlib.sha256(fp.read_bytes()).hexdigest()[:16] if fp.is_file() else ""
                files.append({
                    "status": st, "path": path,
                    "contract_changed": bool(old_p or fp.suffix in AST_EXTRACTORS),
                    **({"scope": sid_hit} if sid_hit else {}),
                    **({"old_path": old_p} if old_p else {}),
                    **({"line_count": lcnt} if lcnt is not None else {}),
                    **({"file_hash": fhash} if fhash else {}),
                    **(
                        {"ast_symbols": _extract_py_ast_symbols(fp)}
                        if mode in ("pre", "post") and fp.is_file() and fp.suffix in AST_EXTRACTORS else {}
                    ),
                    **(
                        {"source": fp.read_text(encoding="utf-8", errors="ignore")}
                        if mode == "pre" and fp.is_file() and fp.suffix in AST_EXTRACTORS else {}
                    ),
                })
    if mode == "pre":
        seen = {f["path"] for f in files} | {f.get("old_path") for f in files if f.get("old_path")}
        sc_map = (graph or {}).get("scopes") or {}
        touched_sc = {f.get("scope") for f in files if f.get("scope")} | {
            sid
            for cp, (sid, raw_r) in ref_map.items()
            if (fp := eff_root / cp).is_file()
            and (
                str((sc := sc_map.get(sid) or {}).get("status") or sc.get("build_status") or "").upper() != "VERIFIED"
                or hashlib.sha256(fp.read_bytes()).hexdigest()[:16] != ((sc.get("file_hashes") or (sc.get("attributes") or {}).get("file_hashes") or {}).get(raw_r))
            )
        }
        for cp, (sid_hit, _) in ref_map.items():
            fp = eff_root / cp
            if not fp.is_file() and cp not in seen:
                files.append({"status": "D", "path": cp, "scope": sid_hit})
            elif (sid_hit in touched_sc and cp not in seen) and fp.is_file():
                files.append({
                    "status": "M", "path": cp, "scope": sid_hit,
                    "contract_changed": fp.suffix in AST_EXTRACTORS,
                    **(
                        {"line_count": len(fp.read_text(encoding="utf-8", errors="ignore").splitlines())}
                        if cp.endswith((".py", ".bend")) else {}
                    ),
                    "file_hash": hashlib.sha256(fp.read_bytes()).hexdigest()[:16],
                    **(
                        {"ast_symbols": _extract_py_ast_symbols(fp), "source": fp.read_text(encoding="utf-8", errors="ignore")}
                        if fp.suffix in AST_EXTRACTORS else {}
                    ),
                })
    return files, sha


def _build_git_manifest(mode: str, manifest_arg: str | None, graph: dict[str, Any] | None = None) -> dict[str, Any]:
    if manifest_arg:
        p = Path(manifest_arg)
        return json.loads(p.read_text(encoding="utf-8") if p.is_file() else manifest_arg)
    repo_root = _active_repo_root()
    ref_map = _collect_ref_map(graph)
    is_base = mode == "baseline" or (mode == "post" and not bool((graph or {}).get("last_commit_sha")))
    has_env_root = bool(os.environ.get("BOWTHRUSTER_PROJECT_ROOT"))
    eff_root = (
        BOWTHRUSTER_HOME.parent
        if (not has_env_root and ref_map and any((BOWTHRUSTER_HOME.parent / cp).is_file() and not (repo_root / cp).is_file() for cp in ref_map))
        else repo_root
    )
    all_repos = [eff_root, *(p for p in sorted(BOWTHRUSTER_HOME.iterdir()) if p.is_dir() and (p / ".git").exists())]
    if mode in ("post", "baseline"):
        cand_repos = [
            r for r in all_repos
            if any(cp.startswith(r.relative_to(eff_root).as_posix() + "/") for cp in ref_map if r != eff_root)
            or (r == eff_root and any(not cp.startswith(BOWTHRUSTER_HOME.name + "/") for cp in ref_map))
        ] or all_repos
        ts_list = [
            (
                int(subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=str(r), text=True, capture_output=True).stdout.strip() or 0),
                r
            )
            for r in cand_repos
        ]
        repos = [max(ts_list, key=lambda x: x[0])[1]] if ts_list else [eff_root]
    else:
        repos = all_repos
    git_cmd = (
        ["git", "diff", "--cached", "-M", "--name-status"]
        if mode == "pre"
        else (["git", "show", "-M", "--name-status", "--format=%H", "HEAD"] if mode in ("post", "baseline") else ["git", "status", "--porcelain"])
    )
    sha = (
        subprocess.run(["git", "log", "-1", "--format=%H"], cwd=str(repos[0]), text=True, capture_output=True, timeout=15).stdout.strip() or None
        if is_base else None
    )
    file_hashes = (
        {raw_r: hashlib.sha256((eff_root / cp).read_bytes()).hexdigest()[:16] for cp, (_, raw_r) in ref_map.items() if (eff_root / cp).is_file()}
        if mode in ("post", "baseline") else {}
    )
    files, post_sha = _collect_git_changed_files(repos, eff_root, ref_map, mode, git_cmd, is_base, graph)
    sha = post_sha or sha
    if is_base and files:
        raise ValueError("initial_baseline_stamp_law violated")
    cfg_mlb = (
        next((json.loads(p.read_text(encoding="utf-8")) for p in DEFAULT_CONFIG_PATHS if p.is_file()), {}).get("gate") or {}
    ).get("max_file_lines")
    ast_by_f = {
        cp: _extract_py_ast_symbols(eff_root / cp)
        for cp in ref_map if (eff_root / cp).suffix in AST_EXTRACTORS and (eff_root / cp).is_file()
    } if mode in ("post", "baseline") else {}
    calls_by_f = {
        cp: _extract_ast_local_calls(eff_root / cp)
        for cp in ref_map if (eff_root / cp).suffix in AST_EXTRACTORS and (eff_root / cp).is_file()
    } if mode in ("post", "baseline") else {}
    bend_rep = verify_python_ast_laws_with_bend(
        list((graph or {}).get("lattice_artifacts") or []), files, (graph or {}).get("scopes")
    ) if mode == "pre" else {"bend_proofs_ok": True, "failed_laws": []}
    return {
        "commit_sha": sha,
        "files": files,
        "bend_proofs_ok": bend_rep["bend_proofs_ok"],
        **({"failed_laws": bend_rep["failed_laws"]} if bend_rep["failed_laws"] else {}),
        **({"file_hashes": file_hashes} if file_hashes else {}),
        **({"ast_symbols_by_file": ast_by_f} if ast_by_f else {}),
        **({"ast_calls_by_file": calls_by_f} if calls_by_f else {}),
        **({"max_file_lines": cfg_mlb} if cfg_mlb is not None else {}),
    }


def _run_bend_proof_check(session_dir: Path, bend_source: str) -> tuple[bool, Path, str]:
    proof_path = session_dir / "LATTICE_PROOF.bend"
    proof_path.write_text(bend_source, encoding="utf-8")
    cand = Path(os.environ.get("BEND_BIN", str(Path.home() / ".bend" / "bin" / "bend")))
    bend_exe = cand if (cand.is_file() and os.access(cand, os.X_OK)) else (Path(w) if (w := shutil.which("bend")) else None)
    if bend_exe is None:
        return False, proof_path, "bend binary not found"
    env = {**os.environ, "BEND_NO_TELEMETRY": "1"}
    proc = subprocess.run(
        [str(bend_exe), str(proof_path)], cwd=str(session_dir),
        env=env, text=True, capture_output=True,
        timeout=30
    )
    if proc.returncode != 0 and "unknown" in (proc.stderr or "").lower():
        proc = subprocess.run(
            [str(bend_exe), "check", str(proof_path)],
            cwd=str(session_dir), env=env, text=True,
            capture_output=True, timeout=30
        )
    ok = proc.returncode == 0 and ("ALL PROOFS CHECK" in proc.stdout or not proc.stderr.strip())
    return ok, proof_path, (proc.stdout + "\n" + proc.stderr).strip()


def _print_proof_radar(proof: dict[str, Any] | None) -> None:
    if isinstance(proof, dict):
        gaps = proof.get("open_gaps") or []
        viols = proof.get("hard_violations") or []
        print(
            f"\n[Lattice Proof Status]: {proof.get('status')} "
            f"(components={proof.get('component_count', 0)}, flows={proof.get('flow_count', 0)}, "
            f"laws={proof.get('law_count', 0)}, contracts={proof.get('contract_count', 0)}, gaps={len(gaps)})"
        )
        for v in viols:
            print(f"  ! [VIOLATION] {v.get('id', 'conflict')}: {v.get('text', v)}")
        for g in gaps:
            print(f"  - [{g.get('kind', 'GAP')}] scope={g.get('scope')}: {g.get('description', '')}")


def _remove_item_from_board(graph: dict[str, Any], target_id: str) -> list[str]:
    removed: list[str] = []
    scopes = graph.get("scopes", {})
    if target_id.startswith("scope:") and (del_sid := target_id.removeprefix("scope:").strip()) in scopes:
        scopes.pop(del_sid, None)
        removed.append(f"scope:{del_sid}")
        for snode in (v for v in scopes.values() if isinstance(v, dict)):
            if isinstance(snode.get("depends_on"), list):
                snode["depends_on"] = [d for d in snode["depends_on"] if d != del_sid]
    if isinstance(graph.get("lattice_artifacts"), list):
        kept_arts = [
            a for a in graph["lattice_artifacts"]
            if not (isinstance(a, dict) and str(a.get("id") or "") == target_id)
        ]
        if len(kept_arts) < len(graph["lattice_artifacts"]):
            removed.append(f"artifact:{target_id}")
            graph["lattice_artifacts"] = kept_arts
    has_rule_id = any(
        any(isinstance(r, dict) and r.get("id") == target_id for r in s.get("rules", []))
        for s in scopes.values() if isinstance(s, dict)
    )
    if (target_id.startswith("attr:") or ("." in target_id and not has_rule_id)) and "." in target_id.removeprefix("attr:"):
        sid, prop = target_id.removeprefix("attr:").split(".", 1)
        if isinstance(snode := scopes.get(sid), dict) and isinstance(snode.get("attributes"), dict) and prop in snode["attributes"]:
            removed.append(f"attr:{sid}.{prop}={snode['attributes'].pop(prop)!r}")
    for sid, snode in ((k, v) for k, v in scopes.items() if isinstance(v, dict)):
        kept = []
        for r in snode.get("rules", []):
            if isinstance(r, dict) and r.get("id") == target_id:
                removed.append(f"{r.get('type', 'RULE')}:{target_id}@{sid} ({r.get('text', '')})")
                if isinstance(snode.get("_meta"), dict):
                    snode["_meta"].pop(target_id, None)
            else:
                kept.append(r)
        snode["rules"] = kept
    if removed:
        for snode in (v for v in scopes.values() if isinstance(v, dict)):
            if target_id in snode.get("in_edges", []):
                snode["in_edges"].remove(target_id)
            for r in (x for x in snode.get("rules", []) if isinstance(x, dict)):
                if target_id in r.get("overrides", []):
                    r["overrides"].remove(target_id)
                r["exceptions"] = [
                    ex for ex in r.get("exceptions", [])
                    if not (isinstance(ex, dict) and ex.get("rule_id") == target_id)
                ]
        graph["open_blockers"] = [
            b for b in graph.get("open_blockers", [])
            if not (isinstance(b, dict) and (b.get("id") == target_id or target_id in b.get("rule_ids", [])))
        ]
    return removed


def _assemble_board_summary(
    graph: dict[str, Any], scope_filter: str | None = None,
    eval_fn: Any = None
) -> dict[str, Any]:
    import copy
    from bowthruster.whiteboard import annotate_active_tasks, expand_node, scan_graph

    annotate_active_tasks(graph)
    standing_types = ("BOUND", "PROHIBITION", "OBLIGATION", "POLICY")
    scopes_out, standing, facts, goals, actions = {}, [], [], [], []
    for sid, snode in sorted(graph.get("scopes", {}).items()):
        if not isinstance(snode, dict) or (
            scope_filter and sid != scope_filter
            and not sid.startswith(f"{scope_filter}::")
        ):
            continue
        attrs = {k: v for k, v in (snode.get("attributes") or {}).items() if k not in ("updated_turn", "heading_path")}
        rules = [dict(r) for r in snode.get("rules", []) if isinstance(r, dict)]
        scopes_out[sid] = {
            **{k: snode.get(k) for k in ("parent", "role", "owner", "stage", "status", "build_tag", "core_tag", "failure_diagnostic", "active_ticket_id")},
            "children": list(snode.get("children", [])),
            "attributes": attrs,
            "rules": rules,
        }
        for r in rules:
            rtype = str(r.get("type", "OBLIGATION"))
            (
                standing if rtype in standing_types else facts
                if rtype == "FACT" else goals if rtype == "GOAL" else actions
            ).append(r)
    raw_arts = graph.get("lattice_artifacts") or {}
    return {
        "turn": graph.get("turn", 0), "scopes": scopes_out, "standing_rules": standing, "facts": facts, "goals": goals, "actions": actions,
        "flows": list(graph.get("flows", [])), "lattice_artifacts": list(raw_arts.values()) if isinstance(raw_arts, dict) else list(raw_arts),
        "active_tasks": list(graph.get("active_tasks", [])), "open_blockers": list(graph.get("open_blockers", [])),
        "lattice_proof": eval_fn(graph) if eval_fn else graph.get("lattice_proof"),
        "assemble_context_pack": expand_node(copy.deepcopy(graph), scope_filter) if scope_filter else scan_graph(copy.deepcopy(graph)),
    }
