"""Spec 22B: `whiteboard` Spin-Glass Conflict & Parent-Hierarchy Engine (`frustration.py`, `bend_wb_22b`) — Unit & Bend 2 QA Suite.

Covers all Spec 22B (§1–§3) and `docs/specs/whiteboard_22b/LAWS.bend` (Laws 1–6) contracts:
  1. Bend 2 Proof Gate (`bend_wb_22b`), exhaustive `.mjs` Execution Parity across all `1,620` `rules_clash` states and
     `3,240` `inherited_rules_clash` states against Python `_rules_clash` / `_inherited_rules_clash`, and Mutation Rejection
     across Laws 1–6 (`rules_clash_symmetric`, `rules_clash_same_num_str_parity`, `rules_clash_diff_num_str_parity`,
     `prohibition_clashes_with_positive_on_same_num`, `mutual_neq_never_clashes`, `lex_specialis_shields_clash`).
  2. `whiteboard.frustration`: `_numeric_interval`, `_intervals_satisfiable`, `_rules_clash`, `_inherited_rules_clash`,
     and `detect_frustration` across:
     - (a) intra-scope and `::` mitosis sibling scopes,
     - (b) ancestor-descendant scope chains via `parent` links (with cycle protection up to `MAX_ANCESTOR_HOPS = 16`),
     - (c) *Lex Specialis* shielding via `overrides` / structured `exceptions` and Law 6 `is_exception` on ancestor-descendant pairs,
     - (d) disjoint vs overlapping `conditions`, and
     - (e) fail-closed `ValueError` on bare string or non-list in `exceptions`.
  3. Standing Rule 49 & Spec 22B Isolation Gate (`<= 500` lines, zero `import re`, zero forbidden 22A/22C files in `eng3/whiteboard`).
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any

WORKSPACE_LATTICE = Path(__file__).resolve().parents[3]
REPO_ROOT = Path(__file__).resolve().parents[4]
BEND_GATE = WORKSPACE_LATTICE / "laws" / "bend_gate.py" if (WORKSPACE_LATTICE / "laws" / "bend_gate.py").is_file() else REPO_ROOT / "scripts" / "bend_gate.py"
SPEC_LAWS_BEND = WORKSPACE_LATTICE / "laws" / "bend_wb_22b" / "LAWS.bend"


def _resolve_spec_laws_bend() -> Path:
    if SPEC_LAWS_BEND.is_file():
        return SPEC_LAWS_BEND
    for entry in sys.path:
        for rel in ("laws/bend_wb_22b/LAWS.bend", "docs/specs/whiteboard_22b/LAWS.bend"):
            if (cand := Path(entry) / rel).is_file():
                return cand
    return SPEC_LAWS_BEND


def _import_wb_22b() -> Any:
    for pkg in ("lattice.whiteboard", "whiteboard", "eng3.whiteboard", "core.circuit.whiteboard"):
        if pkg == "eng3.whiteboard" and not any("/qa-" in p for p in sys.path):
            continue
        try:
            return importlib.import_module(f"{pkg}.frustration")
        except ModuleNotFoundError:
            continue
    raise unittest.SkipTest("whiteboard.frustration not found on PYTHONPATH")


def _load_bend_gate_module() -> Any:
    spec = importlib.util.spec_from_file_location("site_y_bend_gate", BEND_GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _locate_bend_wb_22b_dir(frust_mod: Any) -> Path:
    laws_cand = WORKSPACE_LATTICE / "laws" / "bend_wb_22b"
    if (laws_cand / "PROOF.bend").is_file() and (laws_cand / "main.bend").is_file():
        return laws_cand
    f_file = Path(frust_mod.__file__).resolve()
    for parent in (f_file.parent.parent, f_file.parent):
        cand = parent / "bend_wb_22b"
        if (cand / "PROOF.bend").is_file() and (cand / "main.bend").is_file():
            return cand
    eng3_cand = REPO_ROOT / "ModelCircuits" / "eng3" / "bend_wb_22b"
    if (eng3_cand / "PROOF.bend").is_file():
        return eng3_cand
    raise AssertionError(f"Could not locate bend_wb_22b directory relative to {f_file}")


class TestSpec22BWhiteboardFrustrationAndHierarchy(unittest.TestCase):
    """Spec 22B §1–§3 unit, parent-hierarchy, Bend 2 parity, and mutation QA suite."""

    def test_bend_gate_check_and_1620_3240_state_python_bend2_mjs_parity(self) -> None:
        frust_mod = _import_wb_22b()
        bend_gate = _load_bend_gate_module()
        bend_dir = _locate_bend_wb_22b_dir(frust_mod)

        spec_laws = _resolve_spec_laws_bend().read_text(encoding="utf-8")
        self.assertTrue(spec_laws.startswith((bend_dir / "LAWS.bend").read_text(encoding="utf-8")))

        bend_exe = bend_gate.find_bend_bin()
        node_bin = shutil.which("node") or "/opt/homebrew/bin/node"
        env = {**os.environ, "BEND_NO_TELEMETRY": "1"}

        with tempfile.TemporaryDirectory(prefix="qa_bend_22b_") as tmp:
            staged_dir = Path(tmp) / "bend_wb_22b"
            staged_dir.mkdir(parents=True, exist_ok=True)
            for bf in bend_dir.glob("*.bend"):
                (staged_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
            (staged_dir / "LAWS.bend").write_text(spec_laws, encoding="utf-8")
            ok, msg = bend_gate.check_bend_dir(staged_dir, check_canonical=False)
            self.assertTrue(ok, msg)
            self.assertIn("ALL PROOFS CHECK", msg)

            mjs_path = Path(tmp) / "wb_22b.mjs"
            subprocess.run(
                [str(bend_exe), str(staged_dir / "main.bend"), "-o", str(mjs_path)],
                cwd=str(staged_dir),
                env=env,
                check=True,
                capture_output=True,
            )
            js_probe = f"""
import WBMod from {json.dumps(mjs_path.as_posix())};
const B = WBMod.default || WBMod;
const rtypes = [
  ["PROHIBITION", {{$: "RTProhib"}}],
  ["OBLIGATION", {{$: "RTOblig"}}],
  ["POLICY", {{$: "RTPolicy"}}],
  ["ACTION", {{$: "RTAction"}}],
  ["BOUND", {{$: "RTBound"}}],
  ["CONTEXT", {{$: "RTOther"}}],
];
const ops = [
  ["==", {{$: "OpEq"}}],
  ["!=", {{$: "OpNeq"}}],
  ["NONE", {{$: "OpNone"}}],
];
const vrels = [
  ["VSameNum", {{$: "VSameNum"}}],
  ["VDiffNum", {{$: "VDiffNum"}}],
  ["VSameStr", {{$: "VSameStr"}}],
  ["VDiffStr", {{$: "VDiffStr"}}],
  ["VUnconstrained", {{$: "VUnconstrained"}}],
];
const out = {{ clash: {{}}, inherited: {{}}, law7: [], law8: [], law9: [] }};
for (const [taName, taVal] of rtypes) {{
  for (const [opaName, opaVal] of ops) {{
    for (const [tbName, tbVal] of rtypes) {{
      for (const [opbName, opbVal] of ops) {{
        for (const [vrName, vrVal] of vrels) {{
          const key = `${{taName}}:${{opaName}}:${{tbName}}:${{opbName}}:${{vrName}}`;
          out.clash[key] = Boolean(B.rules_clash(taVal, opaVal, tbVal, opbVal, vrVal));
          out.inherited[`${{key}}:0`] = Boolean(B.inherited_rules_clash(taVal, opaVal, tbVal, opbVal, vrVal, false));
          out.inherited[`${{key}}:1`] = Boolean(B.inherited_rules_clash(taVal, opaVal, tbVal, opbVal, vrVal, true));
        }}
      }}
    }}
  }}
}}
for (const a of [false, true]) for (const b of [false, true]) for (const c of [false, true]) for (const d of [false, true]) {{
  out.law7.push({{ a, b, c, d, res: Boolean(B.dependency_or_flow_clashes(a, b, c, d)) }});
  out.law8.push({{ a, b, c, d, res: Boolean(B.flow_payload_clashes(a, b, c, d)) }});
  const st = B.classify_lattice_proof_status(a, b, c, d);
  out.law9.push({{ a, b, c, d, tag: st.$, proven: Boolean(B.is_lp_proven(st)), broken: Boolean(B.is_lp_broken(st)) }});
}}
console.log(JSON.stringify(out));
"""
            probe_path = Path(tmp) / "probe.mjs"
            probe_path.write_text(js_probe, encoding="utf-8")
            node_res = subprocess.run(
                [node_bin, str(probe_path)],
                cwd=str(tmp),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            bend_out = json.loads(node_res.stdout)

        self.assertEqual(len(bend_out["clash"]), 1620)
        self.assertEqual(len(bend_out["inherited"]), 3240)
        for row in bend_out["law7"]:
            py_dep = frust_mod.dependency_or_flow_clashes(row["a"], row["b"], row["c"], row["d"])
            self.assertEqual(row["res"], py_dep)
            self.assertEqual(py_dep, False if row["d"] else (row["a"] or row["b"] or row["c"]))
        for row in bend_out["law8"]:
            py_pay = frust_mod.flow_payload_clashes(row["a"], row["b"], row["c"], row["d"])
            self.assertEqual(row["res"], py_pay)
            self.assertEqual(py_pay, (row["a"] and not row["b"]) or (row["c"] and not row["d"]))
        lp_map = {"LPProven": "PROVEN_COMPLETE", "LPBroken": "BROKEN_INVARIANT", "LPDraft": "INCOMPLETE_DRAFT"}
        for row in bend_out["law9"]:
            py_lp = frust_mod.classify_lattice_proof_status(row["a"], row["b"], row["c"], row["d"])
            self.assertEqual(lp_map[row["tag"]], py_lp)
            self.assertEqual(row["broken"], row["a"])
            self.assertEqual(row["proven"], (not row["a"]) and row["b"] and row["c"] and row["d"])

        vrel_pairs = {
            "VSameNum": (8080, 8080.0),
            "VDiffNum": (8080, 8443),
            "VSameStr": ("tls_1_3", "tls_1_3"),
            "VDiffStr": ("tls_1_3", "ssl_v3"),
            "VUnconstrained": (None, "tls_1_3"),
        }
        for key, b_clash in bend_out["clash"].items():
            ta, opa_s, tb, opb_s, vr_s = key.split(":")
            opa = None if opa_s == "NONE" else opa_s
            opb = None if opb_s == "NONE" else opb_s
            va, vb = vrel_pairs[vr_s]
            ra = {"id": "ra", "type": ta, "target_prop": "p", "bound_op": opa, "bound_val": va}
            rb = {"id": "rb", "type": tb, "target_prop": "p", "bound_op": opb, "bound_val": vb}

            py_clash = frust_mod._rules_clash(ra, rb)
            self.assertEqual(py_clash, b_clash, f"Mismatch on _rules_clash for {key}")
            self.assertEqual(
                frust_mod._inherited_rules_clash(ra, rb, False),
                bend_out["inherited"][f"{key}:0"],
                f"Mismatch on _inherited_rules_clash(False) for {key}",
            )
            self.assertFalse(frust_mod._inherited_rules_clash(ra, rb, True))
            self.assertFalse(bend_out["inherited"][f"{key}:1"])

    def test_bend_wb_22b_mutation_rejection_across_laws_1_to_6(self) -> None:
        frust_mod = _import_wb_22b()
        bend_dir = _locate_bend_wb_22b_dir(frust_mod)
        orig_main = (bend_dir / "main.bend").read_text(encoding="utf-8")
        spec_laws = _resolve_spec_laws_bend().read_text(encoding="utf-8")

        mutations = [
            ("Law 1 (rules_clash_symmetric)", "    case Pos{} Neg{}:\n      True{}", "    case Pos{} Neg{}:\n      False{}"),
            ("Law 2 (rules_clash_same_num_str_parity)", "    case VSameStr{}:\n      VRSame{}", "    case VSameStr{}:\n      VRDiff{}"),
            ("Law 3 (rules_clash_diff_num_str_parity)", "    case VDiffStr{}:\n      VRDiff{}", "    case VDiffStr{}:\n      VRSame{}"),
            ("Law 4 (prohibition_clashes_with_positive_on_same_num)", "    case OpEq{} OpEq{} VRSame{}:\n      is_deontic_opposing(pa, pb)", "    case OpEq{} OpEq{} VRSame{}:\n      False{}"),
            ("Law 5 (mutual_neq_never_clashes)", "    case OpNeq{} OpNeq{} vr:\n      False{}", "    case OpNeq{} OpNeq{} vr:\n      True{}"),
            ("Law 6 (lex_specialis_shields_clash)", "    case False{}:\n      rules_clash(ta, op_a, tb, op_b, vrel)", "    case False{}:\n      True{}"),
            ("Law 7 (prohibited_dependency_or_flow_clashes)", "    case False{}:\n      Bool.or(has_prohib, Bool.or(is_upward, is_cycle))", "    case False{}:\n      False{}"),
            ("Law 8 (flow_interface_payload_compatibility)", "    Bool.and(src_decl, Bool.not(in_src)),", "    Bool.and(src_decl, in_src),"),
        ]
        bend_gate = _load_bend_gate_module()
        for label, target, replacement in mutations:
            self.assertIn(target, orig_main, f"Mutation target missing for {label}")
            with tempfile.TemporaryDirectory(prefix="qa_mut_22b_") as tmp:
                tmp_dir = Path(tmp) / "bend_wb_22b"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                for bf in bend_dir.glob("*.bend"):
                    (tmp_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
                (tmp_dir / "LAWS.bend").write_text(spec_laws, encoding="utf-8")
                (tmp_dir / "main.bend").write_text(orig_main.replace(target, replacement, 1), encoding="utf-8")
                ok, _ = bend_gate.check_bend_dir(tmp_dir, check_canonical=False)
                self.assertFalse(ok, f"bend_gate check unexpectedly passed on mutated {label}")

    def test_detect_frustration_intra_scope_mitosis_siblings_and_parent_hierarchy(self) -> None:
        frust_mod = _import_wb_22b()

        # 1. Interval satisfiability unit checks
        self.assertTrue(frust_mod._intervals_satisfiable(">=", 100.0, "<=", 250.0))
        self.assertFalse(frust_mod._intervals_satisfiable(">=", 300.0, "<=", 250.0))
        self.assertFalse(frust_mod._intervals_satisfiable("<", 100.0, ">=", 100.0))
        self.assertFalse(frust_mod._intervals_satisfiable("==", 100.0, "!=", 100.0))
        self.assertTrue(frust_mod._intervals_satisfiable("!=", 100.0, "!=", 100.0))

        # 2. Intra-scope, :: mitosis sibling scopes, and multi-hop parent hierarchy with cycle protection
        graph: dict[str, Any] = {
            "scopes": {
                "global_defaults": {
                    "id": "global_defaults",
                    "parent": None,
                    "rules": [
                        {
                            "id": "global.r_tls",
                            "type": "PROHIBITION",
                            "target_prop": "tls_version",
                            "bound_op": "==",
                            "bound_val": "tls_1_0",
                            "conditions": [],
                            "overrides": [],
                            "exceptions": [],
                        }
                    ],
                },
                "region_east": {
                    "id": "region_east",
                    "parent": "global_defaults",
                    "rules": [],
                },
                "ingress_gateway": {
                    "id": "ingress_gateway",
                    "parent": "region_east",
                    "rules": [
                        {
                            "id": "ingress.r_tls_bad",
                            "type": "OBLIGATION",
                            "target_prop": "tls_version",
                            "bound_op": "==",
                            "bound_val": "tls_1_0",
                            "conditions": [],
                            "is_exception": False,
                            "overrides": [],
                            "exceptions": [],
                        }
                    ],
                },
                "svc::page_1": {
                    "id": "svc::page_1",
                    "parent": "svc",
                    "rules": [
                        {
                            "id": "svc.r_cap_1",
                            "type": "BOUND",
                            "target_prop": "max_conn",
                            "bound_op": "<=",
                            "bound_val": 100,
                            "conditions": [],
                            "overrides": [],
                            "exceptions": [],
                        }
                    ],
                },
                "svc::page_2": {
                    "id": "svc::page_2",
                    "parent": "svc",
                    "rules": [
                        {
                            "id": "svc.r_cap_2",
                            "type": "BOUND",
                            "target_prop": "max_conn",
                            "bound_op": ">=",
                            "bound_val": 250,
                            "conditions": [],
                            "overrides": [],
                            "exceptions": [],
                        }
                    ],
                },
                "unrelated_scope": {
                    "id": "unrelated_scope",
                    "parent": None,
                    "rules": [
                        {
                            "id": "unrel.r1",
                            "type": "OBLIGATION",
                            "target_prop": "tls_version",
                            "bound_op": "==",
                            "bound_val": "tls_1_0",
                            "conditions": [],
                            "overrides": [],
                            "exceptions": [],
                        }
                    ],
                },
                "cycle_a": {"id": "cycle_a", "parent": "cycle_b", "rules": []},
                "cycle_b": {"id": "cycle_b", "parent": "cycle_a", "rules": []},
            }
        }

        conflicts = frust_mod.detect_frustration(graph)
        conflict_ids = {c["id"] for c in conflicts}
        conflict_pairs = {tuple(c["rule_ids"]) for c in conflicts}
        # Parent cycle detection (cycle_a <-> cycle_b)
        self.assertTrue({"conflict_dep_cycle_a_cycle_b", "conflict_dep_cycle_b_cycle_a"}.issubset(conflict_ids))
        # Ancestor-descendant clash (global_defaults vs ingress_gateway via region_east)
        self.assertIn(("global.r_tls", "ingress.r_tls_bad"), conflict_pairs)
        # Mitosis sibling clash (svc::page_1 vs svc::page_2)
        self.assertIn(("svc.r_cap_1", "svc.r_cap_2"), conflict_pairs)
        # Unrelated scope without shared root or ancestor link does NOT clash with global_defaults
        self.assertNotIn(("global.r_tls", "unrel.r1"), conflict_pairs)

        del graph["scopes"]["cycle_a"]
        del graph["scopes"]["cycle_b"]

        # 3. Law 6: is_exception=True on descendant rule shields ancestor-descendant clash
        graph["scopes"]["ingress_gateway"]["rules"][0]["is_exception"] = True
        conflicts_shielded = frust_mod.detect_frustration(graph)
        self.assertNotIn(
            ("global.r_tls", "ingress.r_tls_bad"),
            {tuple(c["rule_ids"]) for c in conflicts_shielded},
        )

        # 4. Explicit overrides / structured exceptions shields mitosis sibling clash
        graph["scopes"]["svc::page_2"]["rules"][0]["overrides"] = ["svc.r_cap_1"]
        self.assertEqual(frust_mod.detect_frustration(graph), [])

        # 5. Disjoint vs overlapping conditions
        graph["scopes"]["svc::page_2"]["rules"][0]["overrides"] = []
        graph["scopes"]["svc::page_1"]["rules"][0]["conditions"] = ["when mode is prod"]
        graph["scopes"]["svc::page_2"]["rules"][0]["conditions"] = ["when mode is staging"]
        self.assertEqual(frust_mod.detect_frustration(graph), [])

        graph["scopes"]["svc::page_2"]["rules"][0]["conditions"] = ["when mode is prod", "when region is east"]
        self.assertEqual(len(frust_mod.detect_frustration(graph)), 1)

        # 6. Fail-closed ValueError on bare string or non-list in exceptions
        graph["scopes"]["svc::page_1"]["rules"][0]["exceptions"] = ["svc.r_cap_2"]
        with self.assertRaises(ValueError):
            frust_mod.detect_frustration(graph)
        graph["scopes"]["svc::page_1"]["rules"][0]["exceptions"] = "not_a_list"
        with self.assertRaises(ValueError):
            frust_mod.detect_frustration(graph)

        # 7. Laws 7-8: SBF dependency prohibition/cycle, flow prohibition/self-loop, and payload interface mismatch
        sbf_graph: dict[str, Any] = {
            "scopes": {
                "svc_a": {
                    "id": "svc_a", "parent": None, "depends_on": ["svc_b"], "outputs": ["AuthToken"],
                    "rules": [{"id": "svc_a.no_b", "type": "PROHIBITION", "target_prop": "depends_on", "bound_op": "!=", "bound_val": "svc_b", "exceptions": []}],
                },
                "svc_b": {"id": "svc_b", "parent": None, "depends_on": ["svc_a"], "inputs": ["SessionTicket"], "rules": []},
            },
            "flows": [
                {"id": "f_ab", "source": "svc_a", "target": "svc_b", "payload": "BadPayload"},
            ],
        }
        sbf_conflicts = {c["id"]: c for c in frust_mod.detect_frustration(sbf_graph)}
        self.assertIn("conflict_dep_svc_a_svc_b", sbf_conflicts)
        self.assertIn("conflict_dep_svc_b_svc_a", sbf_conflicts)
        self.assertIn("conflict_flow_f_ab", sbf_conflicts)
        self.assertIn("conflict_payload_f_ab", sbf_conflicts)

        # 8. Law 9 & evaluate_lattice_proof across INCOMPLETE_DRAFT, BROKEN_INVARIANT, and PROVEN_COMPLETE
        broken_proof = frust_mod.evaluate_lattice_proof(sbf_graph)
        self.assertEqual((broken_proof["status"], broken_proof["is_proven"]), ("BROKEN_INVARIANT", False))

        incomp_graph: dict[str, Any] = {
            "scopes": {
                "comp_unowned": {"id": "comp_unowned", "kind": "COMPONENT", "role": "Unowned SBF component", "owner": None, "laws": [], "inputs": ["Req"], "outputs": [], "rules": []},
                "comp_unwired": {"id": "comp_unwired", "kind": "COMPONENT", "owner": "eng1", "laws": [], "inputs": [], "outputs": [], "rules": []},
            },
            "flows": [],
            "lattice_artifacts": [],
        }
        incomp_proof = frust_mod.evaluate_lattice_proof(incomp_graph)
        self.assertEqual((incomp_proof["status"], incomp_proof["is_proven"]), ("INCOMPLETE_DRAFT", False))
        gap_kinds = {g["kind"] for g in incomp_proof["open_gaps"]}
        self.assertEqual(gap_kinds, {"MISSING_OWNER", "MISSING_LAW", "MISSING_CONTRACT", "UNWIRED_COMPONENT"})

        proven_graph: dict[str, Any] = {
            "scopes": {
                "svc_in": {"id": "svc_in", "kind": "COMPONENT", "owner": "eng1", "laws": ["law_in"], "inputs": ["RawReq"], "outputs": ["CleanReq"], "depends_on": [], "rules": []},
                "svc_out": {"id": "svc_out", "kind": "COMPONENT", "owner": "eng2", "laws": ["law_out"], "inputs": ["CleanReq"], "outputs": ["Resp"], "depends_on": ["svc_in"], "rules": []},
            },
            "flows": [{"id": "f_io", "source": "svc_in", "target": "svc_out", "payload": "CleanReq"}],
            "lattice_artifacts": [
                {"id": "s_req", "kind": "SCHEMA", "name": "CleanReq", "scope": "svc_in", "owner": "eng1"},
                {"id": "l_in", "kind": "LAW", "name": "law_in", "scope": "svc_in", "owner": "eng1"},
                {"id": "l_out", "kind": "LAW", "name": "law_out", "scope": "svc_out", "owner": "eng2"},
            ],
        }
        proven_proof = frust_mod.evaluate_lattice_proof(proven_graph)
        self.assertEqual((proven_proof["status"], proven_proof["is_proven"], proven_proof["open_gaps"]), ("PROVEN_COMPLETE", True, []))
        self.assertTrue(bool(proven_proof.get("bend_proof_source", "").strip()))

    def test_standing_rule_49_zero_regex_file_size_and_22b_isolation_gate(self) -> None:
        frust_mod = _import_wb_22b()
        py_file = Path(frust_mod.__file__).resolve()
        wb_dir = py_file.parent
        source = py_file.read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 500, f"{py_file.name} exceeds 500 lines")
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "re", f"Forbidden regex import in {py_file.name}")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "re", f"Forbidden regex import in {py_file.name}")

        if "eng3" in wb_dir.parts:
            forbidden_22a_22c = {
                "schema.py", "mutations.py", "linker.py",
                "rewriter.py", "ingest.py", "read_engine.py", "__init__.py",
            }
            present = {p.name for p in wb_dir.glob("*.py")}
            self.assertEqual(
                present & forbidden_22a_22c,
                set(),
                f"Forbidden 22A/22C files in eng3/whiteboard: {present & forbidden_22a_22c}",
            )


if __name__ == "__main__":
    unittest.main()
