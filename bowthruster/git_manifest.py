"""Git-Lattice Manifest Builder & Delta Test Runner (`git_manifest.py`)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from bowthruster.flight_recorder import (
    AST_EXTRACTORS,
    BOWTHRUSTER_HOME,
    DEFAULT_CONFIG_PATHS,
    _PY_AST_CACHE,
    _active_repo_root,
    _extract_ast_local_calls,
    _extract_py_ast_symbols,
    _load_gate_config,
    _print_proof_radar,
    _py_ast_extractor,
    _run_bend_proof_check,
    format_exception_error,
    install_git_precommit_hook,
    read_flight_logs,
    record_flight_event,
    register_ast_extractor,
)
from bowthruster.nlp import verify_python_ast_laws_with_bend

_SKIP_SCAN_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "node_modules", "build", "dist", ".whiteboard", ".pytest_cache",
})


def _clean_ref_path(raw: str) -> str:
    s = str(raw or "").strip()
    for pfx in ("uri://file/", "uri://", "file://", "./"):
        s = s.removeprefix(pfx)
    return s.split("#", 1)[0].split(":", 1)[0].strip()


def _collect_ref_map(
    graph: dict[str, Any] | None, comp_filter: str | None = None
) -> dict[str, tuple[str, str]]:
    ref_map: dict[str, tuple[str, str]] = {}
    for sid, sc in ((graph or {}).get("scopes") or {}).items():
        if not comp_filter or sid == comp_filter:
            attrs = sc.get("attributes") or {}
            raw_files = (
                *(sc.get("files") or []),
                *(attrs.get("files") or []),
                *(attrs.get("_table_files") or []),
            )
            for r in raw_files:
                s = str(r).strip()
                if s and not s.startswith("tests:") and (cp := _clean_ref_path(s)):
                    ref_map[cp] = (sid, s.split("#", 1)[0])
    raw_arts = (graph or {}).get("lattice_artifacts") or []
    for art in (raw_arts.values() if isinstance(raw_arts, dict) else raw_arts):
        r = str(art.get("file") or "").strip()
        asid = str(art.get("scope") or "").strip()
        if r and asid and (not comp_filter or asid == comp_filter):
            if (cp := _clean_ref_path(r)) and cp not in ref_map:
                ref_map[cp] = (asid, r.split("#", 1)[0])
    return ref_map


def _resolve_max_lines_bound(
    graph: dict[str, Any], gate_cfg: dict[str, Any], comp: str
) -> int | None:
    raw_mlb = (
        graph.get("max_file_lines")
        if graph.get("max_file_lines") is not None
        else gate_cfg.get("max_file_lines")
    )
    mlb = int(raw_mlb) if type(raw_mlb) in (int, float) and raw_mlb > 0 else None
    scopes = graph.get("scopes") or {}
    line_props = ("max_file_lines", "file_lines", "line_count", "lines", "max_lines")
    for s_id in ("global_defaults", "max_file_lines", comp):
        for r in ((scopes.get(s_id) or {}).get("rules") or []):
            is_bound = str(r.get("type") or "").upper() == "BOUND" or r.get("bound_op") is not None
            if is_bound and r.get("bound_val") is not None:
                prop = str(r.get("target_prop") or "").strip().lower()
                if prop in line_props or "max_file_lines" in str(r.get("text") or "").lower():
                    try:
                        mlb = int(float(r["bound_val"]))
                    except (TypeError, ValueError):
                        pass
    return mlb


def _scan_repo_test_files(active_root: Path) -> list[str]:
    found: list[str] = []
    if not active_root.is_dir():
        return found
    for path in sorted(active_root.rglob("*.py")):
        if not path.is_file():
            continue
        rel = path.relative_to(active_root)
        parts = rel.parts
        if any(p.startswith(".") or p in _SKIP_SCAN_DIRS or p.endswith(".egg-info") for p in parts):
            continue
        in_test_dir = any(p in ("tests", "test") for p in parts[:-1])
        is_test_name = parts[-1].startswith("test_") or parts[-1].endswith("_test.py")
        if in_test_dir or is_test_name:
            found.append(rel.as_posix())
    return found


def resolve_component_tests(
    active_root: Path, sids: list[str], graph: dict[str, Any] | None = None
) -> tuple[list[str], dict[str, list[str]], list[str], dict[str, list[str]]]:
    """Resolves declared tests per component and detects undeclared matching test files on disk."""
    all_declared: list[str] = []
    by_scope: dict[str, list[str]] = {}
    untested: list[str] = []
    scopes = (graph or {}).get("scopes") or {}
    for sid in sids:
        sc = scopes.get(sid) or {}
        raw_t = sc.get("tests") or (sc.get("attributes") or {}).get("tests") or []
        t_items = (
            list(raw_t)
            if isinstance(raw_t, list)
            else [p.strip() for p in str(raw_t).split(",") if p.strip()]
        )
        for f_item in (sc.get("files") or []):
            s_item = str(f_item).strip()
            if s_item.startswith("tests:"):
                t_items.append(s_item.removeprefix("tests:").strip())
        sc_tests: list[str] = []
        for tf_rel in t_items:
            clean_t = _clean_ref_path(str(tf_rel))
            if clean_t and (active_root / clean_t).is_file():
                if clean_t not in sc_tests:
                    sc_tests.append(clean_t)
                if clean_t not in all_declared:
                    all_declared.append(clean_t)
        by_scope[sid] = sc_tests
        if not sc_tests:
            untested.append(sid)
    undeclared_matches: dict[str, list[str]] = {}
    if untested:
        disk_tests = _scan_repo_test_files(active_root)
        for sid in untested:
            sc = scopes.get(sid) or {}
            tokens = {sid.lower()}
            for f_raw in (sc.get("files") or []):
                stem = Path(_clean_ref_path(str(f_raw))).stem.lower()
                if stem and stem != "__init__" and len(stem) >= 3:
                    tokens.add(stem)
            hits = [
                tf for tf in disk_tests if any(tok and tok in Path(tf).stem.lower() for tok in tokens)
            ]
            if hits:
                undeclared_matches[sid] = hits
    return all_declared, by_scope, untested, undeclared_matches


def format_undeclared_test_warnings(undeclared_map: dict[str, list[str]]) -> list[str]:
    return [
        f"Component '{sid}' has no declared tests on the board, but matching test file(s) exist "
        f"on disk: {', '.join(files)}. Declare them in the Component table Tests column via `turn`."
        for sid, files in sorted(undeclared_map.items())
        if files
    ]


def _match_component_test_files(
    active_root: Path, stale_sids: list[str], graph: dict[str, Any] | None = None
) -> list[str]:
    return resolve_component_tests(active_root, stale_sids, graph)[0]


def run_delta_tests(
    active_root: Path, stale_sids: list[str], graph: dict[str, Any] | None = None
) -> tuple[bool, bool | str, str]:
    """Runs declared delta `pytest` files for `stale_sids`."""
    if not stale_sids:
        return False, "not_checked", "no stale scopes"
    matched = _match_component_test_files(active_root, stale_sids, graph)
    if not matched:
        return False, "not_checked", "no matching test files found"
    same_home = active_root.resolve() == BOWTHRUSTER_HOME.resolve()
    in_pytest = bool(
        os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("BOWTHRUSTER_IN_DELTA_TESTS") == "1"
    )
    if same_home and in_pytest:
        return True, True, f"already_in_pytest_session ({', '.join(matched)})"
    env = {**os.environ, "PYTHONPATH": str(active_root), "BOWTHRUSTER_IN_DELTA_TESTS": "1"}
    has_py_pytest = importlib.util.find_spec("pytest") is not None
    pytest_bin = shutil.which("pytest")
    if has_py_pytest:
        cmd = [sys.executable, "-m", "pytest", "-q", *matched]
    elif pytest_bin:
        cmd = [pytest_bin, "-q", *matched]
    else:
        cmd = [sys.executable, "-m", "unittest", *matched]
    proc = subprocess.run(
        cmd, cwd=str(active_root), env=env, text=True, capture_output=True, timeout=60
    )
    out = (proc.stdout + "\n" + proc.stderr).strip()
    return True, proc.returncode == 0, out


def _verify_component_files(graph: dict[str, Any], comp: str) -> dict[str, Any]:
    root = _active_repo_root()
    gate_cfg = _load_gate_config(root)
    if not gate_cfg.get("verify_files_exist_on_status", True):
        return {"verified_untested": False, "undeclared_matching_tests": [], "warnings": []}
    ref_m = _collect_ref_map(graph, comp)
    comp_files = list(ref_m.keys())
    has_env_root = bool(os.environ.get("BOWTHRUSTER_PROJECT_ROOT"))

    def res_fp(cp: str) -> Path:
        in_parent = (BOWTHRUSTER_HOME.parent / cp).is_file()
        if (root / cp).is_file() or has_env_root or not in_parent:
            return root / cp
        return BOWTHRUSTER_HOME.parent / cp

    if missing := [cp for cp in comp_files if not res_fp(cp).is_file()]:
        msg = f"Cannot mark '{comp}' VERIFIED; missing declared file(s) on disk: {', '.join(missing)}"
        raise ValueError(msg)
    if (mlb := _resolve_max_lines_bound(graph, gate_cfg, comp)) is not None:
        over = [
            f"{cp} ({lcnt} > max_file_lines={mlb})"
            for cp in comp_files
            if cp.endswith((".py", ".bend"))
            and (lcnt := len(res_fp(cp).read_text(encoding="utf-8", errors="ignore").splitlines())) > mlb
        ]
        if over:
            raise ValueError(
                f"Cannot mark '{comp}' VERIFIED; max_file_lines exceeded: {', '.join(over)}"
            )
    from bowthruster.the_executive.gate import evaluate_git_commit_gate

    clean_g = {k: v for k, v in graph.items() if k != "_sqlite_store"}
    trial_graph = json.loads(json.dumps(clean_g))
    if comp in (trial_graph.get("scopes") or {}):
        trial_graph["scopes"][comp]["status"] = "READY"
        trial_graph["scopes"][comp]["build_status"] = "READY"
    req_cg_laws = bool(gate_cfg.get("require_call_graph_laws", True))
    manifest = _build_git_manifest("pre", None, trial_graph)
    gate_res = evaluate_git_commit_gate(trial_graph, manifest, require_call_graph_laws=req_cg_laws)
    if not gate_res.get("allowed"):
        viols = "; ".join(
            str(v.get("description") or v) for v in (gate_res.get("violations") or [])
        )
        reason = gate_res.get("bounce_reason")
        raise ValueError(f"Cannot mark '{comp}' VERIFIED; pre-commit gate rejected ({reason}): {viols}")
    _, _, untested_sids, undeclared_map = resolve_component_tests(root, [comp], graph)
    _, tests_ok, test_out = run_delta_tests(root, [comp], graph)
    if tests_ok is False:
        raise ValueError(f"Cannot mark '{comp}' VERIFIED; delta tests failed:\n{test_out[-600:]}")
    is_untested = bool(comp in untested_sids or tests_ok == "not_checked")
    warnings = format_undeclared_test_warnings(undeclared_map)
    if (sc := (graph.get("scopes") or {}).get(comp)) is not None:
        from bowthruster.ai_memory import sync_git_diff_uris

        sc["verified_untested"] = is_untested
        f_hashes = {
            raw_r: hashlib.sha256(res_fp(cp).read_bytes()).hexdigest()[:16]
            for cp, (_, raw_r) in ref_m.items()
            if res_fp(cp).is_file()
        }
        sc["file_hashes"] = dict(f_hashes)
        if sc.get("attributes") is not None:
            sc["attributes"]["file_hashes"] = dict(f_hashes)
        ast_files = [
            cp for cp in comp_files
            if res_fp(cp).suffix in AST_EXTRACTORS and res_fp(cp).is_file()
        ]
        sync_git_diff_uris(
            graph,
            {
                "ast_symbols_by_file": {cp: _extract_py_ast_symbols(res_fp(cp)) for cp in ast_files},
                "ast_calls_by_file": {cp: _extract_ast_local_calls(res_fp(cp)) for cp in ast_files},
            },
        )
    return {
        "verified_untested": is_untested,
        "undeclared_matching_tests": undeclared_map.get(comp, []),
        "warnings": warnings,
    }


def _build_file_entry(
    fp: Path, path: str, st: str, sid_hit: str | None, old_p: str, mode: str
) -> dict[str, Any]:
    is_code = fp.is_file() and path.endswith((".py", ".bend"))
    lcnt = len(fp.read_text(encoding="utf-8", errors="ignore").splitlines()) if is_code else None
    fhash = hashlib.sha256(fp.read_bytes()).hexdigest()[:16] if fp.is_file() else ""
    has_ast = fp.is_file() and fp.suffix in AST_EXTRACTORS
    out: dict[str, Any] = {
        "status": st,
        "path": path,
        "contract_changed": bool(old_p or fp.suffix in AST_EXTRACTORS),
    }
    if sid_hit:
        out["scope"] = sid_hit
    if old_p:
        out["old_path"] = old_p
    if lcnt is not None:
        out["line_count"] = lcnt
    if fhash:
        out["file_hash"] = fhash
    if mode in ("pre", "post") and has_ast:
        out["ast_symbols"] = _extract_py_ast_symbols(fp)
    if mode == "pre" and has_ast:
        out["source"] = fp.read_text(encoding="utf-8", errors="ignore")
    return out


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
        proc = subprocess.run(git_cmd, cwd=str(r), text=True, capture_output=True, timeout=15)
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if mode == "post" and lines and len(lines[0]) == 40:
            sha = lines.pop(0)
        for ln in lines:
            parts = ln.split("\t") if "\t" in ln else ln.split(None, 1)
            if len(parts) >= 2:
                st, path = parts[0].strip(), rel_pref + parts[-1].strip()
                old_p = rel_pref + parts[1].strip() if len(parts) >= 3 else ""
                hit_entry = ref_map.get(path) or ref_map.get(old_p)
                sid_h = hit_entry[0] if hit_entry else None
                files.append(_build_file_entry(eff_root / path, path, st, sid_h, old_p, mode))
    if mode == "pre":
        seen = {f["path"] for f in files} | {f.get("old_path") for f in files if f.get("old_path")}
        sc_map = (graph or {}).get("scopes") or {}
        touched_sc = {f.get("scope") for f in files if f.get("scope")}
        for cp, (sid, raw_r) in ref_map.items():
            fp = eff_root / cp
            if fp.is_file():
                sc = sc_map.get(sid) or {}
                cur_st = str(sc.get("status") or "").upper()
                cur_h = hashlib.sha256(fp.read_bytes()).hexdigest()[:16]
                if cur_st != "VERIFIED" or cur_h != (sc.get("file_hashes") or {}).get(raw_r):
                    touched_sc.add(sid)
        for cp, (sid_hit, _) in ref_map.items():
            fp = eff_root / cp
            if not fp.is_file() and cp not in seen:
                files.append({"status": "D", "path": cp, "scope": sid_hit})
            elif sid_hit in touched_sc and cp not in seen and fp.is_file():
                files.append(_build_file_entry(fp, cp, "M", sid_hit, "", "pre"))
    return files, sha


def _build_git_manifest(
    mode: str, manifest_arg: str | None, graph: dict[str, Any] | None = None
) -> dict[str, Any]:
    if manifest_arg:
        p = Path(manifest_arg)
        return json.loads(p.read_text(encoding="utf-8") if p.is_file() else manifest_arg)
    repo_root, ref_map = _active_repo_root(), _collect_ref_map(graph)
    is_base = mode == "baseline" or (mode == "post" and not bool((graph or {}).get("last_commit_sha")))
    has_env_root = bool(os.environ.get("BOWTHRUSTER_PROJECT_ROOT"))
    parent_has_files = any(
        (BOWTHRUSTER_HOME.parent / cp).is_file() and not (repo_root / cp).is_file()
        for cp in ref_map
    )
    eff_root = BOWTHRUSTER_HOME.parent if (not has_env_root and ref_map and parent_has_files) else repo_root
    sub_repos = [
        p for p in sorted(BOWTHRUSTER_HOME.iterdir())
        if p.is_dir() and (p / ".git").exists()
    ]
    all_repos = [eff_root, *sub_repos]
    if mode in ("post", "baseline"):
        cand_repos = [
            r for r in all_repos
            if any(
                cp.startswith(r.relative_to(eff_root).as_posix() + "/")
                for cp in ref_map
                if r != eff_root
            )
            or (r == eff_root and any(not cp.startswith(BOWTHRUSTER_HOME.name + "/") for cp in ref_map))
        ] or all_repos
        ts_cmd = ["git", "log", "-1", "--format=%ct"]
        ts_list = [
            (int(subprocess.run(ts_cmd, cwd=str(r), text=True, capture_output=True).stdout.strip() or 0), r)
            for r in cand_repos
        ]
        repos = [max(ts_list, key=lambda x: x[0])[1]] if ts_list else [eff_root]
    else:
        repos = all_repos
    if mode == "pre":
        git_cmd = ["git", "diff", "--cached", "-M", "--name-status"]
    elif mode in ("post", "baseline"):
        git_cmd = ["git", "show", "-M", "--name-status", "--format=%H", "HEAD"]
    else:
        git_cmd = ["git", "status", "--porcelain"]
    sha: str | None = None
    if is_base:
        sha_proc = subprocess.run(
            ["git", "log", "-1", "--format=%H"],
            cwd=str(repos[0]),
            text=True,
            capture_output=True,
            timeout=15,
        )
        sha = sha_proc.stdout.strip() or None
    is_post = mode in ("post", "baseline")
    file_hashes = (
        {
            raw_r: hashlib.sha256((eff_root / cp).read_bytes()).hexdigest()[:16]
            for cp, (_, raw_r) in ref_map.items()
            if (eff_root / cp).is_file()
        }
        if is_post
        else {}
    )
    files, post_sha = _collect_git_changed_files(repos, eff_root, ref_map, mode, git_cmd, is_base, graph)
    sha = post_sha or sha
    if is_base and files:
        raise ValueError("initial_baseline_stamp_law violated")
    gate_cfg = _load_gate_config(eff_root)
    cfg_mlb = gate_cfg.get("max_file_lines")
    req_cg_laws = bool(gate_cfg.get("require_call_graph_laws", True))
    ast_paths = [
        cp for cp in ref_map
        if (eff_root / cp).suffix in AST_EXTRACTORS and (eff_root / cp).is_file()
    ]
    ast_by_f = {cp: _extract_py_ast_symbols(eff_root / cp) for cp in ast_paths} if is_post else {}
    calls_by_f = {cp: _extract_ast_local_calls(eff_root / cp) for cp in ast_paths} if is_post else {}
    arts = list((graph or {}).get("lattice_artifacts") or [])
    bend_rep = (
        verify_python_ast_laws_with_bend(arts, files, (graph or {}).get("scopes"))
        if mode == "pre"
        else {"bend_proofs_ok": True, "failed_laws": []}
    )
    out: dict[str, Any] = {
        "commit_sha": sha,
        "files": files,
        "bend_proofs_ok": bend_rep["bend_proofs_ok"],
        "require_call_graph_laws": req_cg_laws,
    }
    if bend_rep["failed_laws"]:
        out["failed_laws"] = bend_rep["failed_laws"]
    if file_hashes:
        out["file_hashes"] = file_hashes
    if ast_by_f:
        out["ast_symbols_by_file"] = ast_by_f
    if calls_by_f:
        out["ast_calls_by_file"] = calls_by_f
    if cfg_mlb is not None:
        out["max_file_lines"] = cfg_mlb
    return out
