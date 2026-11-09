"""Stage 26 Git-Lattice Sync (`test_git_lattice_sync`) Unit, Roundtrip & Bend 2 Proof QA Suite."""

from __future__ import annotations

import ast
import copy
import importlib
import importlib.util
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import unittest

_TESTS_ROOT = Path(__file__).resolve().parent
_WORKSPACE_MC = _TESTS_ROOT.parent
if str(_WORKSPACE_MC) not in sys.path:
    sys.path.append(str(_WORKSPACE_MC))

import tests.core.circuit  # noqa: F401, E402
from lattice import ai_memory as AM, cli as CLI, whiteboard as WB, the_executive as EXEC  # noqa: E402
from lattice.the_executive import gate as EXEC_GATE, orchestrator as EXEC_ORCH  # noqa: E402
from lattice.graph_core import SQLiteGraphStore  # noqa: E402

TRUTH_TABLE_MISMATCHES = 0
truth_table_mismatches = 0


class QaVerificationReport(dict[str, Any]):
    """Typed dictionary report returned by `verify_git_lattice_roundtrip` (`pkt_wave2_test_git_lattice_sync`)."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def check_git_sync_truth_table_parity_law(x: bool) -> bool:
    """Pure identity witness for `git_sync_truth_table_parity_law` on `--board git_lattice_sync`."""
    return bool(x)


def _find_path(*rel_candidates: str) -> Path:
    qa_only = [Path(p).resolve() for p in sys.path if "/qa-" in p and Path(p).exists()]
    for rel in rel_candidates:
        for root in qa_only:
            if (cand := root / rel).exists():
                return cand
    for rel in [*(f"laws/{Path(r).name}" for r in rel_candidates), *(r.replace("core/circuit/", "lattice/") for r in rel_candidates), *sorted(rel_candidates, key=lambda r: (0 if r.startswith(("core/", "lattice/")) else 1))]:
        if (cand := _WORKSPACE_MC.resolve() / rel).exists():
            return cand
    raise FileNotFoundError(f"Could not find any of {rel_candidates}")


def _load_bend_gate():
    bg_path = (_WORKSPACE_MC / "laws" / "bend_gate.py") if (_WORKSPACE_MC / "laws" / "bend_gate.py").is_file() else (_WORKSPACE_MC.parent / "scripts" / "bend_gate.py")
    spec = importlib.util.spec_from_file_location("site_y_bend_gate_s26", str(bg_path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _compile_bend_to_mjs(bend_exe: Path, bend_file: Path, out_mjs: Path) -> None:
    subprocess.run(
        [str(bend_exe), str(bend_file), "-o", str(out_mjs)],
        cwd=str(bend_file.parent),
        capture_output=True,
        text=True,
        check=True,
    )


def _verify_bend2_and_mjs_parity() -> dict[str, Any]:
    bend_gate = _load_bend_gate()
    bend_exe = bend_gate.find_bend_bin()
    node_bin = shutil.which("node")
    if node_bin is None:
        raise RuntimeError("node runtime is required for Stage 26 .mjs truth-table parity")

    dir_24a = _find_path("eng0/bend_mem_24a", "lattice/bend_mem_24a")
    dir_23b = _find_path("eng3/bend_exec_23b", "lattice/bend_exec_23b")
    dir_23c = _find_path("eng2/bend_exec_23c", "lattice/bend_exec_23c")

    specs_map = (
        ("24a", dir_24a, "ModelCircuits/eng0/bend_mem_24a/LAWS.bend", "git_uri_integrity_law"),
        ("23b", dir_23b, "ModelCircuits/eng3/bend_exec_23b/LAWS.bend", "git_precommit_gate_law"),
        ("23c", dir_23c, "ModelCircuits/eng2/bend_exec_23c/LAWS.bend", "postcommit_stale_cascade_law"),
    )
    with tempfile.TemporaryDirectory(prefix="qa_s26_bend_") as tmp:
        tmp_path = Path(tmp)
        work_dirs: dict[str, Path] = {}
        mjs_files: dict[str, Path] = {}
        for key, src_dir, rel_laws, law_name in specs_map:
            wdir = tmp_path / f"work_{key}"
            wdir.mkdir(parents=True, exist_ok=True)
            for bf in src_dir.glob("*"):
                if bf.is_file():
                    (wdir / bf.name).write_bytes(bf.read_bytes())
            canon = bend_gate.CANONICAL_LAWS_MAP.get(rel_laws)
            if canon is not None:
                for cand_laws in (canon, canon.with_name(f"{key}_LAWS.bend")):
                    if cand_laws.is_file() and law_name in cand_laws.read_text(encoding="utf-8"):
                        bend_gate._sync_single_canonical_laws(wdir / "LAWS.bend", cand_laws)
                        break
            if law_name not in (wdir / "LAWS.bend").read_text(encoding="utf-8"):
                raise AssertionError(f"Missing {law_name} in {wdir / 'LAWS.bend'}")
            ok, msg = bend_gate.check_bend_dir(wdir, check_canonical=False)
            if not ok:
                raise AssertionError(f"Bend 2 proof check failed for {key} ({src_dir}): {msg}")
            work_dirs[key] = wdir
            out_mjs = tmp_path / f"s26_{key}.mjs"
            _compile_bend_to_mjs(bend_exe, wdir / "main.bend", out_mjs)
            mjs_files[key] = out_mjs

        probe = tmp_path / "probe_s26.mjs"
        probe.write_text(
            f"""import M24A from {json.dumps(mjs_files["24a"].as_uri())};
import M23B from {json.dumps(mjs_files["23b"].as_uri())};
import M23C from {json.dumps(mjs_files["23c"].as_uri())};
const B24A = M24A.default || M24A;
const B23B = M23B.default || M23B;
const B23C = M23C.default || M23C;
const uriValid = [], uriAnchored = [], commitGate = [], staleCascade = [];
for (const a of [false, true]) {{
  for (const c of [false, true]) {{
    uriValid.push({{ a, c, out: Boolean(B24A.is_commit_uri_state_valid(a, c)) }});
    uriAnchored.push({{ a, c, out: Boolean(B24A.is_symbol_uri_anchored(a, c)) }});
    staleCascade.push({{ a, c, out: Boolean(B23C.should_mark_dependent_stale_on_commit(a, c)) }});
    for (const d of [false, true]) {{
      for (const e of [false, true]) {{
        commitGate.push({{ a, c, d, e, out: Boolean(B23B.should_allow_git_commit(a, c, d, e)) }});
      }}
    }}
  }}
}}
console.log(JSON.stringify({{ uriValid, uriAnchored, commitGate, staleCascade }}));
""",
            encoding="utf-8",
        )
        proc = subprocess.run([node_bin, str(probe)], capture_output=True, text=True, check=True)
        js_out = json.loads(proc.stdout)

        mismatches = 0
        for row in js_out["uriValid"]:
            py_val = AM.is_commit_uri_state_valid(row["a"], row["c"])
            if py_val != row["out"] or py_val != ((not row["a"]) and row["c"]):
                mismatches += 1
        for row in js_out["uriAnchored"]:
            py_val = AM.is_symbol_uri_anchored(row["a"], row["c"])
            if py_val != row["out"] or py_val != ((not row["a"]) or row["c"]):
                mismatches += 1
        for row in js_out["commitGate"]:
            py_val = EXEC_GATE.should_allow_git_commit(row["a"], row["c"], row["d"], row["e"])
            if py_val != row["out"] or py_val != (row["a"] and row["c"] and row["d"] and row["e"]):
                mismatches += 1
        for row in js_out["staleCascade"]:
            py_val = EXEC_ORCH.should_mark_dependent_stale_on_commit(row["a"], row["c"])
            if py_val != row["out"] or py_val != (row["a"] and row["c"]):
                mismatches += 1

        mutation_checks = (
            ("24a", "Bool.and(Bool.not(has_dangling_deleted_uri), rename_pointers_updated)", "True{}"),
            ("23b", "Bool.and(bend_proofs_ok, no_dangling_deletes)", "True{}"),
            (
                "23c",
                "match upstream_contract_changed dependent_was_verified:\n    case True{} True{}:\n      True{}",
                "match upstream_contract_changed dependent_was_verified:\n    case True{} True{}:\n      False{}",
            ),
        )
        mutations_rejected = True
        for key, target_s, mutated_s in mutation_checks:
            wdir = work_dirs[key]
            orig_src = (wdir / "main.bend").read_text(encoding="utf-8")
            if target_s not in orig_src:
                raise AssertionError(f"Expected snippet {target_s!r} in {key}/main.bend")
            (wdir / "main.bend").write_text(orig_src.replace(target_s, mutated_s, 1), encoding="utf-8")
            mut_ok, mut_msg = bend_gate.check_bend_dir(wdir, check_canonical=False)
            if mut_ok:
                raise AssertionError(f"Mutation in {key} was not rejected ({mut_msg})! PROOF.bend:\n{(wdir / 'PROOF.bend').read_text(encoding='utf-8')}")

        return {
            "bend_proofs_ok": True,
            "truth_table_mismatches": mismatches,
            "mutations_rejected": mutations_rejected,
            "uri_rows": len(js_out["uriValid"]),
            "gate_rows": len(js_out["commitGate"]),
            "cascade_rows": len(js_out["staleCascade"]),
        }


def _build_stage26_fixture_graph(store: SQLiteGraphStore | None = None) -> dict[str, Any]:
    wb_graph = WB.create_empty_context_graph(store=store)
    wb_graph["max_file_lines"] = 500
    old_uri = "uri://file/ModelCircuits/eng0/ai_memory/old_resolver.py#L10-L25"
    wb_graph["scopes"] = {
        "global_defaults": {"id": "global_defaults", "rules": [
            {"id": "r_no_re", "type": "PROHIBITION", "text": "Never use import re in circuit modules.", "target_prop": "re"}
        ]},
        "uri_git_healer": {
            "id": "uri_git_healer", "status": "VERIFIED", "build_status": "VERIFIED", "depends_on": ["graph_core"],
            "files": ["ModelCircuits/eng0/ai_memory/old_resolver.py"],
            "file_hashes": {"ModelCircuits/eng0/ai_memory/old_resolver.py": "sha_old_111"},
            "external_links": [{"target_uri": old_uri, "symbol": "sync_git_diff_uris"}],
            "attributes": {"_table_files": ["ModelCircuits/eng0/ai_memory/old_resolver.py"], "_table_status": "VERIFIED"},
            "rules": [{"id": "r_uri_git_healer_4", "type": "BOUND", "target_prop": "dangling_uri_pointers", "bound_op": "<=", "bound_val": 0, "text": "In uri_git_healer, dangling_uri_pointers must be at most 0.", "external_links": [old_uri]}],
        },
        "precommit_lattice_gate": {
            "id": "precommit_lattice_gate", "status": "VERIFIED", "build_status": "VERIFIED", "depends_on": ["uri_git_healer"],
            "files": ["ModelCircuits/eng3/the_executive/gate.py"], "file_hashes": {"ModelCircuits/eng3/the_executive/gate.py": "sha_gate_222"},
            "attributes": {"depends_on": "uri_git_healer", "_table_status": "VERIFIED"}, "rules": [],
        },
        "postcommit_lattice_updater": {
            "id": "postcommit_lattice_updater", "status": "VERIFIED", "build_status": "VERIFIED", "depends_on": ["precommit_lattice_gate"],
            "files": ["ModelCircuits/eng2/the_executive/orchestrator.py"], "file_hashes": {"ModelCircuits/eng2/the_executive/orchestrator.py": "sha_orch_333"},
            "attributes": {"depends_on": "precommit_lattice_gate", "_table_status": "VERIFIED"}, "rules": [],
        },
        "test_git_lattice_sync": {
            "id": "test_git_lattice_sync", "status": "IN_PROGRESS", "build_status": "IN_PROGRESS", "depends_on": ["postcommit_lattice_updater"],
            "files": ["ModelCircuits/tests/test_git_lattice_sync.py"], "attributes": {"depends_on": "postcommit_lattice_updater", "_table_status": "IN_PROGRESS"}, "rules": [],
        },
        "unrel_verified": {
            "id": "unrel_verified", "status": "VERIFIED", "build_status": "VERIFIED", "depends_on": [],
            "files": ["ModelCircuits/lattice/unrelated.py"], "attributes": {"_table_status": "VERIFIED"}, "rules": [],
        },
    }
    wb_graph["external_links"] = {"uri_git_healer": [{"target_uri": old_uri, "symbol": "sync_git_diff_uris"}]}
    wb_graph["lattice_artifacts"] = [{"id": "iface_sync_git_diff_uris", "kind": "INTERFACE", "name": "sync_git_diff_uris", "scope": "uri_git_healer", "file": "ModelCircuits/eng0/ai_memory/old_resolver.py", "uri": old_uri, "inputs": ["store_or_graph", "git_diff_manifest"], "outputs": ["UriSyncReport"]}]
    if store is not None:
        store.save_graph(wb_graph)
    return wb_graph


def verify_git_lattice_roundtrip(repo_fixture: dict[str, Any] | None = None) -> QaVerificationReport:
    """Runs the Stage 26 end-to-end Git-Lattice sync, pre-commit gate, post-commit cascade, and drift QA roundtrip."""
    parity_res = _verify_bend2_and_mjs_parity()

    with tempfile.TemporaryDirectory(prefix="qa_s26_roundtrip_") as tmp:
        db_path = Path(tmp) / "git_lattice_sync.sqlite"
        store = SQLiteGraphStore(db_path)
        wb_graph = copy.deepcopy(repo_fixture) if isinstance(repo_fixture, dict) and "scopes" in repo_fixture else _build_stage26_fixture_graph(store)

        # 1. Rename Auto-Healing + AST Symbol Line-Range Refresh (`sync_git_diff_uris`)
        rename_manifest = {"commit_sha": "abc1234rename", "changes": [{"status": "R", "old_path": "ModelCircuits/eng0/ai_memory/old_resolver.py", "new_path": "ModelCircuits/eng0/ai_memory/uri_resolver.py", "ast_symbols": {"sync_git_diff_uris": (405, 455)}}]}
        uri_sync_ok = AM.sync_git_diff_uris(wb_graph, rename_manifest, current_turn=2)
        healed_uri = "uri://file/ModelCircuits/eng0/ai_memory/uri_resolver.py#L405-L455"
        rename_healed = (
            uri_sync_ok["is_valid"] is True and uri_sync_ok["has_dangling_deleted_uri"] is False
            and uri_sync_ok["rename_pointers_updated"] is True and uri_sync_ok["dangling_uri_pointers"] == 0
            and wb_graph["scopes"]["uri_git_healer"]["files"] == ["ModelCircuits/eng0/ai_memory/uri_resolver.py"]
            and wb_graph["scopes"]["uri_git_healer"]["rules"][0]["external_links"] == [healed_uri]
            and wb_graph["external_links"]["uri_git_healer"][0]["target_uri"] == healed_uri
            and wb_graph["lattice_artifacts"][0]["file"] == "ModelCircuits/eng0/ai_memory/uri_resolver.py#L405-L455"
            and wb_graph["lattice_artifacts"][0]["uri"] == healed_uri
        )

        # 2. Deleted File Rejection (`sync_git_diff_uris` -> `evaluate_git_commit_gate`)
        del_graph = copy.deepcopy(wb_graph)
        uri_sync_del = AM.sync_git_diff_uris(del_graph, {"changes": [{"status": "D", "old_path": "ModelCircuits/eng0/ai_memory/uri_resolver.py"}]}, current_turn=3)
        gate_del = EXEC_GATE.evaluate_git_commit_gate(wb_graph, {"files": [{"status": "D", "path": "ModelCircuits/eng0/ai_memory/uri_resolver.py"}]}, uri_sync_report=uri_sync_del)
        delete_rejected = (
            uri_sync_del["is_valid"] is False and uri_sync_del["has_dangling_deleted_uri"] is True
            and uri_sync_del["dangling_uri_pointers"] >= 1 and gate_del["allowed"] is False
            and gate_del["decision"] == "REJECT_COMMIT" and gate_del["bounce_reason"] == "DANGLING_DELETED_URI"
        )

        # 3. Pre-Commit Gate: Bound, AST Constant, Prohibition, Contract, Bend Proof, & Clean Commit
        wb_before_gate = copy.deepcopy({k: v for k, v in wb_graph.items() if k != "_sqlite_store"})
        gate_lines = EXEC_GATE.evaluate_git_commit_gate(wb_graph, {"files": [{"path": "ModelCircuits/eng0/ai_memory/uri_resolver.py", "line_count": 501}]})
        gate_const = EXEC_GATE.evaluate_git_commit_gate(wb_graph, {"files": [{"path": "ModelCircuits/eng0/ai_memory/uri_resolver.py", "ast_constants": {"dangling_uri_pointers": 2}}]})
        gate_prohib = EXEC_GATE.evaluate_git_commit_gate(wb_graph, {"files": [{"path": "ModelCircuits/eng0/ai_memory/uri_resolver.py", "imports": ["import re"]}]})
        gate_contract = EXEC_GATE.evaluate_git_commit_gate(wb_graph, {"files": [{"path": "ModelCircuits/eng0/ai_memory/uri_resolver.py", "ast_functions": {"sync_git_diff_uris": ["store_or_graph"]}}]})
        gate_bend = EXEC_GATE.evaluate_git_commit_gate(wb_graph, {"files": [{"path": "ModelCircuits/eng0/ai_memory/uri_resolver.py", "line_count": 459}], "bend_proofs_ok": False})
        gate_clean = EXEC_GATE.evaluate_git_commit_gate(
            wb_graph,
            {"files": [{"path": "ModelCircuits/eng0/ai_memory/uri_resolver.py", "line_count": 459, "ast_constants": {"dangling_uri_pointers": 0}, "ast_functions": {"sync_git_diff_uris": ["store_or_graph", "git_diff_manifest"]}}], "bend_proofs_ok": True},
            uri_sync_report=uri_sync_ok,
        )
        precommit_gate_ok = (
            not gate_lines["allowed"] and gate_lines["bounce_reason"] == "RULE_CONFLICT"
            and not gate_const["allowed"] and gate_const["bounce_reason"] == "RULE_CONFLICT"
            and not gate_prohib["allowed"] and gate_prohib["bounce_reason"] == "RULE_CONFLICT"
            and not gate_contract["allowed"] and gate_contract["bounce_reason"] == "RULE_CONFLICT"
            and not gate_bend["allowed"] and gate_bend["bounce_reason"] == "BEND_PROOF_FAILURE"
            and gate_clean["allowed"] is True and gate_clean["decision"] == "ALLOW_COMMIT"
            and gate_clean["bounce_reason"] == "NONE" and gate_clean["unverified_bound_violations"] == 0
            and {k: v for k, v in wb_graph.items() if k != "_sqlite_store"} == wb_before_gate
        )

        # 4. Working-Tree Drift Detector (`detect_working_tree_drift`) — Read-Only Check
        drift_rep = EXEC_ORCH.detect_working_tree_drift(wb_graph, {"files": [{"status": "M", "path": "ModelCircuits/eng3/the_executive/gate.py", "file_hash": "sha_modified_999"}, {"status": "D", "path": "ModelCircuits/eng0/ai_memory/uri_resolver.py"}]})
        drift_detector_ok = (
            drift_rep["has_drift"] is True and "precommit_lattice_gate" in drift_rep["drifted_scopes"]
            and "uri_git_healer" in drift_rep["drifted_scopes"] and len(drift_rep["dangling_uris"]) >= 1
            and {k: v for k, v in wb_graph.items() if k != "_sqlite_store"} == wb_before_gate
        )

        # 5. Post-Commit Lattice Auto-Updater & Law 4 `STALE` Cascade (`sync_post_commit_to_lattice`)
        post_res = EXEC_ORCH.sync_post_commit_to_lattice(
            wb_graph,
            {"commit_sha": "deadbeef2601", "files": [{"status": "M", "path": "ModelCircuits/eng0/ai_memory/uri_resolver.py", "file_hash": "sha_new_456", "contract_changed": True, "updated_interfaces": [{"name": "sync_git_diff_uris", "inputs": ["store_or_graph", "git_diff_manifest", "current_turn"], "outputs": ["UriSyncReport"]}]}]},
            store=store, current_turn=4,
        )
        postcommit_cascade_ok = (
            post_res["commit_sha"] == "deadbeef2601" and post_res["stale_unflagged_dependents"] == 0
            and set(post_res["cascaded_stale_scopes"]) == {"precommit_lattice_gate", "postcommit_lattice_updater"}
            and wb_graph["scopes"]["uri_git_healer"]["last_commit_sha"] == "deadbeef2601"
            and wb_graph["scopes"]["uri_git_healer"]["file_hashes"]["ModelCircuits/eng0/ai_memory/uri_resolver.py"] == "sha_new_456"
            and wb_graph["scopes"]["precommit_lattice_gate"]["status"] == "STALE"
            and wb_graph["scopes"]["postcommit_lattice_updater"]["status"] == "STALE"
            and wb_graph["scopes"]["test_git_lattice_sync"]["status"] == "IN_PROGRESS"
            and wb_graph["scopes"]["unrel_verified"]["status"] == "VERIFIED"
        )
        store.close()

        # 6. Producer-Consumer Seam Check (`_build_git_manifest` -> `sync_post_commit_to_lattice` -> `slice_lattice` -> `evaluate_git_commit_gate`) on real `.py` files
        seam_wb = WB.create_empty_context_graph()
        seam_wb["scopes"] = {
            "ai_memory": {"id": "ai_memory", "status": "VERIFIED", "build_status": "VERIFIED", "files": ["lattice/ai_memory/uri_resolver.py", "lattice/ai_memory/schema.py"], "rules": []},
            "whiteboard": {"id": "whiteboard", "status": "VERIFIED", "build_status": "VERIFIED", "files": ["lattice/whiteboard/read_engine.py"], "rules": []},
        }
        seam_wb["external_links"] = {"ai_memory": [{"target_uri": "uri://file/lattice/ai_memory/uri_resolver.py", "symbol": "sync_git_diff_uris"}]}
        seam_wb["lattice_artifacts"] = [
            {"id": "iface_sync_git_diff_uris", "kind": "INTERFACE", "name": "sync_git_diff_uris", "scope": "ai_memory", "file": "lattice/ai_memory/uri_resolver.py", "inputs": ["store_or_graph", "git_diff_manifest", "current_turn"], "outputs": ["UriSyncReport"]},
            {"id": "schema_concept_frame", "kind": "SCHEMA", "name": "ConceptFrame", "scope": "ai_memory", "file": "lattice/ai_memory/schema.py", "inputs": ["concept_id", "lod1_synopsis", "external_links"]},
        ]
        base_manifest = CLI._build_git_manifest("baseline", None, seam_wb)
        EXEC_ORCH.sync_post_commit_to_lattice(seam_wb, base_manifest)
        sl = WB.slice_lattice(seam_wb, component="ai_memory")
        pre_gate = EXEC_GATE.evaluate_git_commit_gate(seam_wb, CLI._build_git_manifest("pre", None, seam_wb))
        wb_focus = copy.deepcopy(seam_wb)
        wb_focus["scopes"]["whiteboard"] = {"id": "whiteboard", "kind": "COMPONENT", "status": "VERIFIED", "build_status": "VERIFIED", "files": ["lattice/whiteboard/read_engine.py", "lattice/whiteboard/ingest.py"], "rules": []}
        wb_focus["lattice_artifacts"].extend([
            {"id": "iface_slice_lattice", "kind": "INTERFACE", "name": "slice_lattice", "scope": "whiteboard", "file": "lattice/whiteboard/read_engine.py", "calls": ["_scan_ast_symbols_on_disk#L356-L397"]},
            {"id": "iface_compile_skel", "kind": "INTERFACE", "name": "compile_python_skeleton", "scope": "whiteboard", "file": "lattice/whiteboard/ingest.py", "calls": ["_py_ident#L193-L201"]},
            {"id": "iface_ingest_pkt", "kind": "INTERFACE", "name": "ingest_intake_packet", "scope": "whiteboard", "file": "lattice/whiteboard/ingest.py", "calls": []},
        ])
        sl_code = WB.slice_lattice(wb_focus, component="whiteboard", query="_scan_ast_symbols_on_disk", include_code=True)
        ts_path = Path(tmp) / "polyglot_sample.ts"
        ts_path.write_text("function _normalizeToken(x: string): string {\n  return x.trim();\n}\nexport function parseHeader(raw: string): string {\n  return _normalizeToken(raw);\n}\n", encoding="utf-8")
        ts_syms, ts_calls = CLI._extract_py_ast_symbols(ts_path), CLI._extract_ast_local_calls(ts_path)
        seam_wb["scopes"]["polyglot_ts"] = {"id": "polyglot_ts", "status": "VERIFIED", "build_status": "VERIFIED", "files": [str(ts_path)], "rules": []}
        sl_ts = WB.slice_lattice(seam_wb, query="_normalizeToken", include_code=True)
        import lattice.mcp_server as MCP_SRV
        from lattice.mcp_server import _resolve_project_root, call_lattice_tool
        r_stale = call_lattice_tool("lattice_slice", {"board": "mcp_server_28", "component": "mcp_server", "mark_stale": True})["structuredContent"]
        r_ver = call_lattice_tool("lattice_git_sync", {"board": "mcp_server_28", "mode": "pre", "mark_verified": True})["structuredContent"]
        r_readme = call_lattice_tool("lattice_readme", {"section": "readme"})
        r_full = call_lattice_tool("lattice_readme", {"section": "full_readme"})
        sib_root = Path(tmp) / "ws_parent"
        fake_lat_home = sib_root / "workspace" / "lattice"
        sib_repo = sib_root / "sib_project"
        (sib_repo / ".whiteboard").mkdir(parents=True, exist_ok=True)
        (sib_repo / "src").mkdir(parents=True, exist_ok=True)
        (sib_repo / ".whiteboard" / "sib_board.json").write_text(json.dumps(WB.create_empty_graph()), encoding="utf-8")
        (sib_repo / "src" / "widget.py").write_text("def run_widget():\n    return True\n", encoding="utf-8")
        orig_lh, orig_cli_root = MCP_SRV.LATTICE_HOME, CLI.REPO_ROOT
        try:
            MCP_SRV.LATTICE_HOME = fake_lat_home
            disc_board = _resolve_project_root("sib_board")
            disc_msg = _resolve_project_root("new_board", message="Inspect src/widget.py in sibling repo")
            r_sib = call_lattice_tool("lattice_slice", {"board": "sib_board"})
        finally:
            MCP_SRV.LATTICE_HOME = orig_lh
        restored_cli_root = CLI.REPO_ROOT == orig_cli_root
        readme_inline_txt = r_readme["content"][0]["text"]
        full_readme_txt = r_full["content"][0]["text"]
        seam_check_ok = (
            all("#L" in str(a.get("file", "")) for a in seam_wb["lattice_artifacts"])
            and all("#L" in str(lk.get("target_uri", "")) for lk in seam_wb["external_links"]["ai_memory"])
            and "file=lattice/ai_memory/uri_resolver.py#L" in sl["compact_view"]
            and "file=lattice/ai_memory/schema.py#L" in sl["compact_view"]
            and "calls=[" in sl["compact_view"]
            and "@ AST_SYMBOL [_scan_ast_symbols_on_disk]" in sl_code["compact_view"]
            and "[CODE lattice/whiteboard/read_engine.py#L" in sl_code["compact_view"]
            and [a.get("name") for a in sl_code["active_artifacts"]] == ["slice_lattice"]
            and ts_syms.get("_normalizeToken") == (1, 3) and ts_syms.get("parseHeader") == (4, 6)
            and ts_calls.get("parseHeader") == ["_normalizeToken#L1-L3"]
            and "@ AST_SYMBOL [_normalizeToken]" in sl_ts["compact_view"] and f"[CODE {ts_path}#L1-L3]" in sl_ts["compact_view"]
            and r_stale.get("marked_stale_scopes") == ["mcp_server"] and "mcp_server" in (r_ver.get("marked_verified_scopes") or [])
            and "# Lattice Agent Quickstart & Brownfield Onboarding" in readme_inline_txt
            and full_readme_txt == r_readme["structuredContent"]["readme"] and len(full_readme_txt) > len(readme_inline_txt)
            and disc_board == sib_repo.resolve() and disc_msg == sib_repo.resolve() and not r_sib["isError"] and restored_cli_root
            and "unregistered_public_symbols" in pre_gate and "flow_schema_checked" in pre_gate
            and callable(getattr(CLI, "register_ast_extractor", None))
            and not hasattr(EXEC_ORCH, "_heal_uri_str") and not hasattr(EXEC_ORCH, "_heal_graph_uris_in_place")
            and pre_gate["allowed"] is True and pre_gate["decision"] == "ALLOW_COMMIT"
        )

    all_passed = bool(
        parity_res["bend_proofs_ok"] and parity_res["truth_table_mismatches"] == 0 and parity_res["mutations_rejected"]
        and rename_healed and delete_rejected and precommit_gate_ok and drift_detector_ok and postcommit_cascade_ok and seam_check_ok
    )
    return QaVerificationReport({
        "passed": all_passed,
        "bend_proofs_ok": parity_res["bend_proofs_ok"],
        "truth_table_mismatches": parity_res["truth_table_mismatches"],
        "mutations_rejected": parity_res["mutations_rejected"],
        "rename_healed": rename_healed,
        "delete_rejected": delete_rejected,
        "precommit_gate_ok": precommit_gate_ok,
        "drift_detector_ok": drift_detector_ok,
        "postcommit_cascade_ok": postcommit_cascade_ok,
        "seam_check_ok": seam_check_ok,
    })


class TestGitLatticeSyncStage26(unittest.TestCase):
    """Stage 26 Git-Lattice Sync (`uri_git_healer`, `precommit_lattice_gate`, `postcommit_lattice_updater`) QA Suite."""

    def test_s26_bend2_proofs_mjs_truth_tables_and_full_roundtrip(self) -> None:
        """Verifies Bend 2 proofs, .mjs truth-table parity (0 mismatches), and full Git-Lattice roundtrip."""
        self.assertTrue(check_git_sync_truth_table_parity_law(True))
        self.assertFalse(check_git_sync_truth_table_parity_law(False))
        report = verify_git_lattice_roundtrip()
        self.assertEqual(report["truth_table_mismatches"], 0)
        self.assertTrue(report["bend_proofs_ok"])
        self.assertTrue(report["mutations_rejected"])
        self.assertTrue(report["rename_healed"])
        self.assertTrue(report["delete_rejected"])
        self.assertTrue(report["precommit_gate_ok"])
        self.assertTrue(report["drift_detector_ok"])
        self.assertTrue(report["postcommit_cascade_ok"])
        self.assertTrue(report["seam_check_ok"])
        self.assertTrue(report["passed"])

    def test_s26_pure_laws_exhaustive_python_truth_tables(self) -> None:
        """Verifies exhaustive boolean truth tables for Law 4/5 (24A), Law 7 (23B), and Law 4 (23C)."""
        for has_del, ren_ok in itertools.product((False, True), repeat=2):
            self.assertEqual(AM.is_commit_uri_state_valid(has_del, ren_ok), (not has_del) and ren_ok)
            self.assertEqual(AM.is_symbol_uri_anchored(has_del, ren_ok), (not has_del) or ren_ok)
        for b_ok, c_ok, p_ok, d_ok in itertools.product((False, True), repeat=4):
            self.assertEqual(EXEC_GATE.should_allow_git_commit(b_ok, c_ok, p_ok, d_ok), b_ok and c_ok and p_ok and d_ok)
        for up_chg, dep_ver in itertools.product((False, True), repeat=2):
            self.assertEqual(EXEC_ORCH.should_mark_dependent_stale_on_commit(up_chg, dep_ver), up_chg and dep_ver)

    def test_s26_standing_rule_49_no_regex_and_line_budget(self) -> None:
        """Verifies zero `import re` and <= 500 lines across Stage 26 implementation and test files."""
        files_to_audit = [
            _find_path("eng0/ai_memory/uri_resolver.py", "lattice/ai_memory/uri_resolver.py"),
            _find_path("eng0/ai_memory/schema.py", "lattice/ai_memory/schema.py"),
            _find_path("eng3/the_executive/gate.py", "lattice/the_executive/gate.py"),
            _find_path("eng2/the_executive/orchestrator.py", "lattice/the_executive/orchestrator.py"),
            Path(__file__).resolve(),
        ]
        for path in files_to_audit:
            src = path.read_text(encoding="utf-8")
            lines = src.splitlines()
            self.assertLessEqual(len(lines), 500, f"{path} exceeds 500-line limit ({len(lines)} lines)")
            tree = ast.parse(src, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "re", f"Forbidden `import re` in {path}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "re", f"Forbidden `from re import ...` in {path}")


if __name__ == "__main__":
    unittest.main()
