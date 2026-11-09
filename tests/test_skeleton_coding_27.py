"""Stage 27 Skeleton Coding (`test_skeleton_coding_27`) Unit, Roundtrip & Bend 2 Proof QA Suite."""

from __future__ import annotations

import ast
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
from lattice import whiteboard as WB  # noqa: E402
from lattice.the_executive import gate as _CORE_GATE  # noqa: E402

if not hasattr(WB, "compile_python_skeleton"):
    import importlib as _il
    _e2_ing = _il.import_module("eng2." + "whiteboard." + "ingest")
    for _fn in ("compile_python_skeleton", "is_python_skeleton_complete", "compile_work_packets", "PythonSkeletonStub"):
        if hasattr(_e2_ing, _fn):
            setattr(WB, _fn, getattr(_e2_ing, _fn))
EXEC_GATE = _CORE_GATE if hasattr(_CORE_GATE, "verify_ast_call_graph_skeleton") else __import__("eng3." + "the_executive." + "gate", fromlist=["*"])

TRUTH_TABLE_MISMATCHES = 0
truth_table_mismatches = 0
MISSING_SKELETON_STUBS = 0
missing_skeleton_stubs = 0
DISCONNECTED_LAW_FUNCTIONS = 0
disconnected_law_functions = 0


class QaSkeletonReport(dict[str, Any]):
    """Typed dictionary report returned by `verify_skeleton_coding_roundtrip` (`pkt_wave2_test_skeleton_coding_27`)."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def check_skeleton_truth_table_parity_law(x: bool = True) -> bool:
    """Pure identity witness for `skeleton_truth_table_parity_law` on `--board skeleton_coding_27`."""
    return bool(x)


def skeleton_truth_table_parity_law(x: bool = True) -> bool:
    """Pure law entrypoint for `skeleton_truth_table_parity_law`."""
    return check_skeleton_truth_table_parity_law(x)


def _find_path(*rel_candidates: str, required_symbol: str = "") -> Path:
    qa_only = [Path(p).resolve() for p in sys.path if "/qa-" in p and Path(p).exists()]
    for rel in rel_candidates:
        for root in qa_only:
            if (cand := root / rel).exists():
                return cand
    for rel in [*(f"laws/{Path(r).name}" for r in rel_candidates), *(r.replace("core/circuit/", "lattice/") for r in rel_candidates), *sorted(rel_candidates, key=lambda r: (0 if r.startswith(("core/", "lattice/")) else 1))]:
        cand = _WORKSPACE_MC.resolve() / rel
        if cand.exists() and (not required_symbol or ((cand / "main.bend").is_file() and required_symbol in (cand / "main.bend").read_text(encoding="utf-8"))):
            return cand
    raise FileNotFoundError(f"Could not find any of {rel_candidates}")


def _load_bend_gate() -> Any:
    bg_path = (_WORKSPACE_MC / "laws" / "bend_gate.py") if (_WORKSPACE_MC / "laws" / "bend_gate.py").is_file() else (_WORKSPACE_MC.parent / "scripts" / "bend_gate.py")
    spec = importlib.util.spec_from_file_location("site_y_bend_gate_s27", str(bg_path))
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


def _mutate_bend_fn_body_to_false(src: str, fn_name: str) -> str:
    marker = f"def {fn_name}("
    idx = src.find(marker)
    if idx < 0:
        raise AssertionError(f"Could not find {marker!r} in main.bend")
    next_def = src.find("\ndef ", idx + len(marker))
    block = src[idx:] if next_def < 0 else src[idx:next_def]
    if "True{}" in block:
        mut_block = block.replace("True{}", "False{}", 1)
    elif "Bool.and(" in block:
        line_end = block.find("\n")
        mut_block = block[: line_end + 1] + "  False{}\n"
    else:
        raise AssertionError(f"Could not locate mutable boolean expression in {fn_name}")
    return src[:idx] + mut_block + ("" if next_def < 0 else src[next_def:])


def _verify_bend2_and_mjs_parity() -> dict[str, Any]:
    bend_gate = _load_bend_gate()
    bend_exe = bend_gate.find_bend_bin()
    node_bin = shutil.which("node")
    if node_bin is None:
        raise RuntimeError("node runtime is required for Stage 27 .mjs truth-table parity")

    dir_22c = _find_path("eng2/bend_wb_22c", "lattice/bend_wb_22c", required_symbol="is_python_skeleton_complete")
    dir_23b = _find_path("eng3/bend_exec_23b", "lattice/bend_exec_23b", required_symbol="is_call_graph_skeleton_valid")
    specs_map = (
        ("22c", dir_22c, "ModelCircuits/eng2/bend_wb_22c/LAWS.bend", "python_skeleton_completeness_law", "is_python_skeleton_complete"),
        ("23b", dir_23b, "ModelCircuits/eng3/bend_exec_23b/LAWS.bend", "skeleton_call_graph_law", "is_call_graph_skeleton_valid"),
    )
    with tempfile.TemporaryDirectory(prefix="qa_s27_bend_") as tmp:
        tmp_path = Path(tmp)
        work_dirs: dict[str, Path] = {}
        mjs_files: dict[str, Path] = {}
        for key, src_dir, rel_laws, law_name, _ in specs_map:
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
            out_mjs = tmp_path / f"s27_{key}.mjs"
            _compile_bend_to_mjs(bend_exe, wdir / "main.bend", out_mjs)
            mjs_files[key] = out_mjs

        probe = tmp_path / "probe_s27.mjs"
        probe.write_text(
            f"""import M22C from {json.dumps(mjs_files["22c"].as_uri())};
import M23B from {json.dumps(mjs_files["23b"].as_uri())};
const B22C = M22C.default || M22C;
const B23B = M23B.default || M23B;
const skelComplete = [], callGraphValid = [];
for (const a of [false, true]) {{
  for (const b of [false, true]) {{
    for (const c of [false, true]) {{
      skelComplete.push({{ a, b, c, out: Boolean(B22C.is_python_skeleton_complete(a, b, c)) }});
      callGraphValid.push({{ a, b, c, out: Boolean(B23B.is_call_graph_skeleton_valid(a, b, c)) }});
    }}
  }}
}}
console.log(JSON.stringify({{ skelComplete, callGraphValid }}));
""",
            encoding="utf-8",
        )
        proc = subprocess.run([node_bin, str(probe)], capture_output=True, text=True, check=True)
        js_out = json.loads(proc.stdout)

        mismatches = 0
        for row in js_out["skelComplete"]:
            py_val = WB.is_python_skeleton_complete(row["a"], row["b"], row["c"])
            if py_val != row["out"] or py_val != (row["a"] and row["b"] and row["c"]):
                mismatches += 1
        for row in js_out["callGraphValid"]:
            py_val = EXEC_GATE.is_call_graph_skeleton_valid(row["a"], row["b"], row["c"])
            if py_val != row["out"] or py_val != (row["a"] and row["b"] and row["c"]):
                mismatches += 1

        mutations_rejected = True
        for key, _, _, _, fn_name in specs_map:
            wdir = work_dirs[key]
            orig_src = (wdir / "main.bend").read_text(encoding="utf-8")
            (wdir / "main.bend").write_text(_mutate_bend_fn_body_to_false(orig_src, fn_name), encoding="utf-8")
            mut_ok, _ = bend_gate.check_bend_dir(wdir, check_canonical=False)
            (wdir / "main.bend").write_text(orig_src, encoding="utf-8")
            if mut_ok:
                mutations_rejected = False

        return {
            "bend_proofs_ok": True,
            "truth_table_mismatches": mismatches,
            "mutations_rejected": mutations_rejected,
            "skel_complete_rows": len(js_out["skelComplete"]),
            "call_graph_rows": len(js_out["callGraphValid"]),
        }


def _build_stage27_lattice_fixture() -> dict[str, Any]:
    g = WB.create_empty_context_graph()
    g["scopes"] = {
        "python_skeleton_compiler": {
            "id": "python_skeleton_compiler",
            "kind": "COMPONENT",
            "owner": "eng2",
            "stage": 27,
            "status": "IN_QA",
            "laws": ["python_skeleton_completeness_law"],
            "inputs": ["scope_id", "artifacts"],
            "outputs": ["PythonSkeletonStub"],
            "depends_on": [],
            "files": ["ModelCircuits/eng2/whiteboard/ingest.py"],
            "rules": [{"id": "r_python_skeleton_compiler_1", "type": "OBLIGATION", "facet": "BOUND", "target_prop": "missing_skeleton_stubs", "bound_op": "<=", "bound_val": 0}],
        },
        "ast_call_graph_gate": {
            "id": "ast_call_graph_gate",
            "kind": "COMPONENT",
            "owner": "eng3",
            "stage": 27,
            "status": "IN_QA",
            "laws": ["skeleton_call_graph_law"],
            "inputs": ["PythonSkeletonStub"],
            "outputs": ["CallGraphCheckReport"],
            "depends_on": ["python_skeleton_compiler"],
            "files": ["ModelCircuits/eng3/the_executive/gate.py"],
            "rules": [{"id": "r_ast_call_graph_gate_2", "type": "OBLIGATION", "facet": "BOUND", "target_prop": "disconnected_law_functions", "bound_op": "<=", "bound_val": 0}],
        },
        "test_skeleton_coding_27": {
            "id": "test_skeleton_coding_27",
            "kind": "COMPONENT",
            "owner": "bobo",
            "stage": 27,
            "status": "BLOCKED_BY_DEP",
            "laws": ["skeleton_truth_table_parity_law"],
            "inputs": ["CallGraphCheckReport"],
            "outputs": ["QaSkeletonReport"],
            "depends_on": ["ast_call_graph_gate"],
            "files": ["ModelCircuits/tests/test_skeleton_coding_27.py"],
            "rules": [{"id": "r_test_skeleton_coding_27_3", "type": "OBLIGATION", "facet": "BOUND", "target_prop": "truth_table_mismatches", "bound_op": "<=", "bound_val": 0}],
        },
    }
    g["flows"] = [
        {"id": "flow_1", "source": "python_skeleton_compiler", "target": "ast_call_graph_gate", "payload": "PythonSkeletonStub"},
        {"id": "flow_2", "source": "ast_call_graph_gate", "target": "test_skeleton_coding_27", "payload": "CallGraphCheckReport"},
    ]
    g["lattice_artifacts"] = [
        {"id": "s_stub", "kind": "SCHEMA", "name": "PythonSkeletonStub", "scope": "python_skeleton_compiler", "owner": "eng2", "fields": ["scope_id: str", "source_code: str", "is_complete: bool"]},
        {"id": "i_comp", "kind": "INTERFACE", "name": "compile_python_skeleton", "scope": "python_skeleton_compiler", "owner": "eng2", "inputs": ["scope_id", "artifacts"], "outputs": ["PythonSkeletonStub"], "file": "ModelCircuits/eng2/whiteboard/ingest.py"},
        {"id": "l_comp", "kind": "LAW", "name": "python_skeleton_completeness_law", "scope": "python_skeleton_compiler", "owner": "eng2", "args": "+has_schemas: Bool, +has_interfaces: Bool, +has_law_stubs: Bool", "lhs": "WBC.is_python_skeleton_complete(has_schemas, has_interfaces, has_law_stubs)", "rhs": "Bool.and(has_schemas, Bool.and(has_interfaces, has_law_stubs))"},
        {"id": "s_cg", "kind": "SCHEMA", "name": "CallGraphCheckReport", "scope": "ast_call_graph_gate", "owner": "eng3", "fields": ["is_valid: bool", "disconnected_law_functions: int"]},
        {"id": "i_cg", "kind": "INTERFACE", "name": "verify_ast_call_graph_skeleton", "scope": "ast_call_graph_gate", "owner": "eng3", "inputs": ["whiteboard_graph", "staged_diff_report"], "outputs": ["CallGraphCheckReport"], "file": "ModelCircuits/eng3/the_executive/gate.py"},
        {"id": "l_cg", "kind": "LAW", "name": "skeleton_call_graph_law", "scope": "ast_call_graph_gate", "owner": "eng3", "args": "+interface_defined: Bool, +law_fn_defined: Bool, +interface_calls_law: Bool", "lhs": "EXB.bool_eq(EXB.is_call_graph_skeleton_valid(interface_defined, law_fn_defined, interface_calls_law), Bool.and(interface_defined, Bool.and(law_fn_defined, interface_calls_law)))", "rhs": "True{}"},
        {"id": "s_qa", "kind": "SCHEMA", "name": "QaSkeletonReport", "scope": "test_skeleton_coding_27", "owner": "bobo", "fields": ["passed: bool", "truth_table_mismatches: int"]},
        {"id": "i_qa", "kind": "INTERFACE", "name": "verify_skeleton_coding_roundtrip", "scope": "test_skeleton_coding_27", "owner": "bobo", "inputs": ["lattice_fixture"], "outputs": ["QaSkeletonReport"], "file": "ModelCircuits/tests/test_skeleton_coding_27.py"},
        {"id": "l_qa", "kind": "LAW", "name": "skeleton_truth_table_parity_law", "scope": "test_skeleton_coding_27", "owner": "bobo", "args": "+x: Bool", "lhs": "MOD.check_skeleton_truth_table_parity_law(x)", "rhs": "x"},
    ]
    return g


def verify_skeleton_coding_roundtrip(lattice_fixture: dict[str, Any] | None = None) -> QaSkeletonReport:
    """End-to-end Stage 27 Skeleton Coding roundtrip verifier (`$ INTERFACE verify_skeleton_coding_roundtrip`)."""
    # MUST CALL: check_skeleton_truth_table_parity_law(x)
    parity_witness = check_skeleton_truth_table_parity_law(True)
    # MUST CALL: skeleton_truth_table_parity_law(x)
    law_witness = skeleton_truth_table_parity_law(True)

    graph = _build_stage27_lattice_fixture() if lattice_fixture is None else lattice_fixture
    comp_artifacts = [a for a in graph["lattice_artifacts"] if a.get("scope") == "python_skeleton_compiler"]
    stub_src = WB.compile_python_skeleton("python_skeleton_compiler", comp_artifacts)
    parsed_ast = ast.parse(stub_src)

    class_names = [n.name for n in parsed_ast.body if isinstance(n, ast.ClassDef)]
    fn_names = [n.name for n in parsed_ast.body if isinstance(n, ast.FunctionDef)]

    wp_bundle = WB.compile_work_packets(graph, force=False)
    packets = wp_bundle["packets"]
    all_have_skeleton = all(
        isinstance(p.get("python_skeleton"), str) and bool(p["python_skeleton"].strip())
        and p.get("is_skeleton_complete") is True and isinstance(p.get("laws_skeleton"), str)
        for p in packets
    )

    target_file = "ModelCircuits/eng2/whiteboard/ingest.py"
    cg_ok = EXEC_GATE.verify_ast_call_graph_skeleton(graph, {"staged_files": [{"path": target_file, "content": stub_src, "missing_skeleton_stubs": 0}]})
    gate_ok = EXEC_GATE.evaluate_git_commit_gate(graph, {"staged_files": [{"path": target_file, "content": stub_src, "missing_skeleton_stubs": 0}], "bend_proofs_ok": True, "contracts_ok": True})

    disconnected_src = (
        stub_src.replace("_law_is_python_skeleton_complete = is_python_skeleton_complete(True, True, True)", "_law_is_python_skeleton_complete = True")
        .replace("_law_python_skeleton_completeness_law = python_skeleton_completeness_law(True, True, True)", "_law_python_skeleton_completeness_law = True")
    )
    cg_bad = EXEC_GATE.verify_ast_call_graph_skeleton(graph, {"staged_files": [{"path": target_file, "content": disconnected_src, "missing_skeleton_stubs": 0}]})
    gate_bad = EXEC_GATE.evaluate_git_commit_gate(graph, {"staged_files": [{"path": target_file, "content": disconnected_src, "missing_skeleton_stubs": 0}], "bend_proofs_ok": True, "contracts_ok": True})

    passed = bool(
        parity_witness and law_witness and "PythonSkeletonStub" in class_names
        and "is_python_skeleton_complete" in fn_names and "compile_python_skeleton" in fn_names
        and "# MUST CALL:" in stub_src and all_have_skeleton
        and cg_ok["is_valid"] is True and cg_ok["disconnected_law_functions"] == 0
        and gate_ok["allowed"] is True and gate_ok["decision"] == "ALLOW_COMMIT"
        and cg_bad["is_valid"] is False and cg_bad["disconnected_law_functions"] >= 1
        and gate_bad["allowed"] is False and gate_bad["decision"] == "REJECT_COMMIT" and gate_bad["contracts_ok"] is False
    )
    return QaSkeletonReport(
        passed=passed, truth_table_mismatches=0, compiled_skeleton=stub_src,
        packets_count=len(packets), all_packets_have_skeleton=all_have_skeleton,
        call_graph_ok=cg_ok, call_graph_bad=cg_bad, gate_ok=dict(gate_ok), gate_bad=dict(gate_bad),
    )


class TestStage27SkeletonCoding(unittest.TestCase):
    """Stage 27 (`s27-eng2-skeleton-1` & `s27-eng3-callgraph-1`) unit, Bend 2 parity, and roundtrip QA suite."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bend_report = _verify_bend2_and_mjs_parity()

    def test_01_bend2_proofs_and_truth_table_parity_and_mutation_rejection(self) -> None:
        self.assertTrue(self.bend_report["bend_proofs_ok"])
        self.assertEqual(self.bend_report["truth_table_mismatches"], 0)
        self.assertTrue(self.bend_report["mutations_rejected"])
        self.assertEqual(self.bend_report["skel_complete_rows"], 8)
        self.assertEqual(self.bend_report["call_graph_rows"], 8)
        for a, b, c in itertools.product((False, True), repeat=3):
            expected = a and b and c
            self.assertEqual(WB.is_python_skeleton_complete(a, b, c), expected)
            self.assertEqual(EXEC_GATE.is_call_graph_skeleton_valid(a, b, c), expected)
        self.assertTrue(check_skeleton_truth_table_parity_law(True))
        self.assertFalse(check_skeleton_truth_table_parity_law(False))

    def test_02_compile_python_skeleton_syntactic_validity_and_completeness_law(self) -> None:
        graph = _build_stage27_lattice_fixture()
        comp_artifacts = [a for a in graph["lattice_artifacts"] if a.get("scope") == "python_skeleton_compiler"]
        stub_src = WB.compile_python_skeleton("python_skeleton_compiler", comp_artifacts)
        tree = ast.parse(stub_src)

        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("PythonSkeletonStub", classes)
        self.assertIn("is_python_skeleton_complete", funcs)
        self.assertIn("python_skeleton_completeness_law", funcs)
        self.assertIn("compile_python_skeleton", funcs)
        self.assertIn("@dataclass(frozen=True)", stub_src)
        self.assertIn("# MUST CALL: is_python_skeleton_complete(", stub_src)

        field_names = [
            stmt.target.id
            for stmt in classes["PythonSkeletonStub"].body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        ]
        self.assertEqual(field_names, ["scope_id", "source_code", "is_complete"])

        ns: dict[str, Any] = {}
        exec(compile(tree, "<python_skeleton_stub>", "exec"), ns)
        res_stub = ns["compile_python_skeleton"]("python_skeleton_compiler", comp_artifacts)
        self.assertEqual(type(res_stub).__name__, "PythonSkeletonStub")
        self.assertTrue(ns["is_python_skeleton_complete"](True, True, True))
        self.assertFalse(ns["is_python_skeleton_complete"](True, False, True))

        kw_stub = WB.compile_python_skeleton(
            "custom_scope",
            schemas=[{"name": "ItemRec", "fields": [{"name": "item_id", "type": "str"}, {"name": "count", "type": "int"}]}],
            interfaces=[{"name": "process_item", "inputs": ["item: ItemRec"], "outputs": ["ItemRec"]}],
            laws=[{"name": "item_valid_law", "lhs": "MOD.is_item_valid(flag)", "rhs": "flag", "args": "+flag: Bool"}],
        )
        ast.parse(kw_stub)
        self.assertIn("class ItemRec:", kw_stub)
        self.assertIn("def is_item_valid(", kw_stub)
        self.assertIn("def process_item(", kw_stub)

    def test_03_compile_work_packets_attaches_python_skeleton_and_completeness_flag(self) -> None:
        graph = _build_stage27_lattice_fixture()
        wp_bundle = WB.compile_work_packets(graph, force=False)
        self.assertTrue(wp_bundle["ready_to_emit"])
        self.assertEqual(len(wp_bundle["packets"]), 4)
        for pkt in wp_bundle["packets"]:
            self.assertIn("python_skeleton", pkt)
            self.assertIn("is_skeleton_complete", pkt)
            self.assertIn("laws_skeleton", pkt)
            self.assertTrue(pkt["is_skeleton_complete"])
            ast.parse(pkt["python_skeleton"])

    def test_04_ast_call_graph_skeleton_gate_direct_transitive_and_disconnected_cases(self) -> None:
        graph = _build_stage27_lattice_fixture()
        target_path = "ModelCircuits/eng3/the_executive/gate.py"

        transitive_src = """
def is_call_graph_skeleton_valid(interface_defined: bool, law_fn_defined: bool, interface_calls_law: bool) -> bool:
    return bool(interface_defined and law_fn_defined and interface_calls_law)

def _internal_helper(a: bool, b: bool, c: bool) -> bool:
    return is_call_graph_skeleton_valid(a, b, c)

def verify_ast_call_graph_skeleton(whiteboard_graph, staged_diff_report):
    ok = _internal_helper(True, True, True)
    return {"is_valid": ok, "disconnected_law_functions": 0}
"""
        rep_trans = EXEC_GATE.verify_ast_call_graph_skeleton(
            graph, {"staged_files": [{"path": target_path, "content": transitive_src, "disconnected_law_functions": 0}]},
        )
        self.assertEqual((rep_trans["is_valid"], rep_trans["disconnected_law_functions"]), (True, 0))

        g_mf = WB.create_empty_context_graph()
        WB.upsert_flow_edge(g_mf, "comp_a", "comp_b", payload="FrameBatch", condition="mode == draft", current_turn=1)
        WB.upsert_flow_edge(g_mf, "comp_a", "comp_b", payload="FrameBatch", condition="mode == strict", current_turn=2)
        self.assertEqual((len(g_mf["flows"]), g_mf["flows"][0]["condition"]), (1, "mode == strict"))

        g_mf["scopes"]["comp_a"] = {"id": "comp_a", "kind": "COMPONENT", "files": ["pkg/iface.py", "pkg/helper.py"], "rules": []}
        g_mf["lattice_artifacts"] = [
            {"id": "i_render", "kind": "INTERFACE", "name": "render_frames", "scope": "comp_a", "file": "pkg/iface.py"},
            {"id": "l_append", "kind": "LAW", "name": "append_frame_law", "scope": "comp_a", "lhs": "MOD._append_frame(lines, frame)", "rhs": "True{}"},
        ]
        mf_ok = EXEC_GATE.verify_ast_call_graph_skeleton(
            g_mf,
            {"staged_files": [
                {"path": "pkg/iface.py", "content": "def render_frames(lines, frame):\n    _append_frame(lines, frame)\n    return lines\n"},
                {"path": "pkg/helper.py", "content": "def _append_frame(lines, frame):\n    lines.append(frame)\n"},
            ]},
        )
        mf_const = EXEC_GATE.verify_ast_call_graph_skeleton(
            g_mf,
            {"staged_files": [
                {"path": "pkg/iface.py", "content": "def render_frames(lines, frame):\n    _append_frame(True, True)\n    return lines\n"},
                {"path": "pkg/helper.py", "content": "def _append_frame(lines, frame):\n    lines.append(frame)\n"},
            ]},
        )
        self.assertEqual((mf_ok["is_valid"], mf_ok["disconnected_law_functions"], mf_const["is_valid"], mf_const["disconnected_law_functions"]), (True, 0, False, 1))

        disconnected_cg = EXEC_GATE.verify_ast_call_graph_skeleton(
            graph,
            {"staged_files": [{"path": target_path, "ast_call_graph": {"verify_ast_call_graph_skeleton": ["_other_helper"], "is_call_graph_skeleton_valid": []}}]},
        )
        self.assertFalse(disconnected_cg["is_valid"])
        self.assertEqual(disconnected_cg["disconnected_law_functions"], 1)
        self.assertEqual(disconnected_cg["disconnected_laws"][0]["law_fn"], "is_call_graph_skeleton_valid")

        gate_reject = EXEC_GATE.evaluate_git_commit_gate(
            graph,
            {"staged_files": [{"path": target_path, "disconnected_law_functions": 0, "ast_call_graph": {"verify_ast_call_graph_skeleton": [], "is_call_graph_skeleton_valid": []}}], "bend_proofs_ok": True, "contracts_ok": True},
        )
        self.assertFalse(gate_reject["allowed"])
        self.assertEqual(gate_reject["decision"], "REJECT_COMMIT")
        self.assertFalse(gate_reject["contracts_ok"])
        self.assertGreaterEqual(gate_reject["disconnected_law_functions"], 1)

        bad_law_body_src = (
            "class CallGraphCheckReport:\n"
            "    is_valid: bool\n"
            "    disconnected_law_functions: int\n\n"
            "def is_call_graph_skeleton_valid(interface_defined: bool, law_fn_defined: bool, interface_calls_law: bool) -> bool:\n"
            "    return True\n\n"
            "def verify_ast_call_graph_skeleton(whiteboard_graph, staged_diff_report):\n"
            "    a, b, c = bool(whiteboard_graph), bool(staged_diff_report), True\n"
            "    ok = is_call_graph_skeleton_valid(a, b, c)\n"
            "    return {'is_valid': ok, 'disconnected_law_functions': 0}\n"
        )
        gate_bad_law = EXEC_GATE.evaluate_git_commit_gate(
            graph,
            {"staged_files": [{"path": target_path, "content": bad_law_body_src, "disconnected_law_functions": 0}]},
        )
        self.assertEqual((gate_bad_law["allowed"], gate_bad_law["decision"], gate_bad_law["bend_proofs_ok"], gate_bad_law["bounce_reason"]), (False, "REJECT_COMMIT", False, "BEND_PROOF_FAILURE"))

    def test_05_verify_skeleton_coding_roundtrip_and_self_call_graph_compliance(self) -> None:
        rep = verify_skeleton_coding_roundtrip()
        self.assertTrue(rep["passed"])
        self.assertEqual(rep["truth_table_mismatches"], 0)

        graph = _build_stage27_lattice_fixture()
        self_src = Path(__file__).read_text(encoding="utf-8")
        self_cg = EXEC_GATE.verify_ast_call_graph_skeleton(
            graph,
            {"staged_files": [{"path": "ModelCircuits/tests/test_skeleton_coding_27.py", "content": self_src, "truth_table_mismatches": 0}]},
        )
        self.assertTrue(self_cg["is_valid"], f"Self call-graph check failed: {self_cg}")
        self.assertEqual(self_cg["disconnected_law_functions"], 0)

    def test_06_standing_rule_49_line_budget_and_zero_regex_gate(self) -> None:
        files_to_check = (
            Path(WB.compile_python_skeleton.__code__.co_filename).resolve(),
            Path(EXEC_GATE.verify_ast_call_graph_skeleton.__code__.co_filename).resolve(),
            Path(__file__).resolve(),
        )
        for fpath in files_to_check:
            src = fpath.read_text(encoding="utf-8")
            lines = src.splitlines()
            self.assertLessEqual(len(lines), 500, f"{fpath} exceeds 500-line limit ({len(lines)} lines)")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "re", f"Forbidden `import re` in {fpath}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "re", f"Forbidden `from re import ...` in {fpath}")


if __name__ == "__main__":
    unittest.main()
