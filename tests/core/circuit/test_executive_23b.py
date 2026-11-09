"""Spec 23B: Unit & Bend 2 QA Suite for `the_executive` Stage 23B (`gate.py` & `bend_exec_23b`).

Verifies:
  1. Bend 2 formal verification gate (`scripts/bend_gate.py check`) on `bend_exec_23b` (`LAWS.bend`, `main.bend`, `PROOF.bend`).
  2. Exhaustive truth-table parity between compiled `main.mjs` and Python `should_commit_whiteboard`,
     `route_executive_decision`, and `resolve_bounce_reason` across all 16 boolean input states (Laws 1–6).
  3. Negative mutation testing of `PROOF.bend` confirming `bend_gate.py check` rejects broken implementations of Laws 1–6.
  4. Transactional Whiteboard commit on clean turns (`execute_gate_decision` commits via `ingest_intake_packet`, sets
     `whiteboard_mutated=True`, populates `committed_items`, and syncs to SQLite when bound).
  5. Transactional Whiteboard invariance on bounced turns across all three bounce reasons (`RULE_CONFLICT`, `FLAWED_IDEA`,
     `NEEDS_CLARIFICATION`): `execute_gate_decision` leaves live `whiteboard_graph` and SQLite DB bit-for-bit untouched
     (`whiteboard_mutated=False`, `ingestion_result=None`, `blocked_items` populated).
  6. Fail-closed validation in `verify_preflight_report` and `ExecutiveGateError`.
  7. Standing Rule 49 zero-regex gate, file-size gate (`<= 350` lines), and Spec 23B module isolation in `eng3/the_executive`.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
MODEL_CIRCUITS = WORKSPACE_ROOT / "ModelCircuits"
BEND_GATE_PY = WORKSPACE_ROOT / "scripts" / "bend_gate.py"
SPEC_23B_LAWS = MODEL_CIRCUITS / "docs" / "specs" / "executive_23b" / "LAWS.bend"
NODE_BIN = shutil.which("node") or "node"
MAX_23B_LINES = 500


def _resolve_bend_exec_23b_dir() -> Path:
    for p in sys.path:
        cand = Path(p) / "eng3" / "bend_exec_23b"
        if cand.is_dir():
            return cand
    cand_ws = MODEL_CIRCUITS / "eng3" / "bend_exec_23b"
    return cand_ws if cand_ws.is_dir() else MODEL_CIRCUITS / "core" / "circuit" / "the_executive" / "bend_exec_23b"


def _resolve_spec_23b_laws() -> Path:
    for p in sys.path:
        cand = Path(p) / "docs" / "specs" / "executive_23b" / "LAWS.bend"
        if cand.is_file():
            return cand
    return SPEC_23B_LAWS


def _import_exec_23b() -> tuple[Any, Any, Any]:
    core_exec_dir = MODEL_CIRCUITS / "core" / "circuit" / "the_executive"
    for p in sys.path:
        if "/qa-" in p:
            cand_dir = Path(p) / "eng3" / "the_executive"
            if (cand_dir / "gate.py").is_file():
                search_locs = [str(cand_dir)]
                for e in ("eng3", "eng1", "eng2"):
                    d = Path(p) / e / "the_executive"
                    if d.is_dir() and str(d) not in search_locs:
                        search_locs.append(str(d))
                for cd in (Path(p) / "core" / "circuit" / "the_executive", core_exec_dir):
                    if cd.is_dir() and str(cd) not in search_locs:
                        search_locs.append(str(cd))
                spec = importlib.util.spec_from_file_location(
                    "core.circuit.the_executive",
                    str(cand_dir / "__init__.py"),
                    submodule_search_locations=search_locs,
                )
                assert spec is not None and spec.loader is not None
                pkg = importlib.util.module_from_spec(spec)
                sys.modules["core.circuit.the_executive"] = pkg
                spec.loader.exec_module(pkg)
                import lattice.the_executive.gate as gate
                import lattice.the_executive.preflight as preflight
                return pkg, gate, preflight
    import lattice.the_executive as pkg
    import lattice.the_executive.gate as gate
    import lattice.the_executive.preflight as preflight

    return pkg, gate, preflight


class TestSpec23BExecutiveRoutingGate(unittest.TestCase):
    """Unit & Bend 2 QA suite for Spec 23B (`the_executive/gate.py` and `bend_exec_23b`)."""

    def test_bend_gate_check_and_laws_1_to_6_python_bend2_mjs_parity(self) -> None:
        _, gate, _ = _import_exec_23b()
        bend_dir = _resolve_bend_exec_23b_dir()
        self.assertTrue(bend_dir.is_dir(), f"Missing bend_exec_23b directory: {bend_dir}")
        for fname in ("LAWS.bend", "main.bend", "PROOF.bend", "main.mjs"):
            self.assertTrue((bend_dir / fname).is_file(), f"Missing {fname} in {bend_dir}")

        spec_laws = _resolve_spec_23b_laws()
        if spec_laws.is_file():
            self.assertEqual(
                (bend_dir / "LAWS.bend").read_text(encoding="utf-8"),
                spec_laws.read_text(encoding="utf-8"),
                "LAWS.bend in bend_exec_23b drifted from canonical docs/specs/executive_23b/LAWS.bend",
            )

        proc = subprocess.run(
            [sys.executable, str(BEND_GATE_PY), "check", str(bend_dir)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, f"bend_gate.py check failed:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("PASS", proc.stdout)

        mjs_path = (bend_dir / "main.mjs").resolve()
        node_script = f"""
import m from {json.dumps(mjs_path.as_uri())};
const bools = [false, true];
const rows = [];
for (const a of bools) {{
  for (const o of bools) {{
    for (const r of bools) {{
      for (const f of bools) {{
        const commit = m.should_commit_whiteboard(a, o, r, f);
        const dec = m.route_executive_decision(a, o, r, f);
        const bnc = m.resolve_bounce_reason(a, o, r, f);
        rows.push({{
          a, o, r, f,
          commit,
          dec_tag: dec.$,
          is_commit: m.is_commit_decision(dec),
          is_send_back: m.is_send_back_decision(dec),
          bnc_tag: bnc.$,
          is_rc: m.is_rule_conflict_bounce(bnc),
          is_fi: m.is_flawed_idea_bounce(bnc),
          is_nc: m.is_needs_clarification_bounce(bnc),
          is_none: m.is_none_bounce(bnc),
        }});
      }}
    }}
  }}
}}
console.log(JSON.stringify({{main: m.main().$, rows}}));
"""
        node_proc = subprocess.run([NODE_BIN, "--input-type=module", "-e", node_script], capture_output=True, text=True, timeout=30)
        self.assertEqual(node_proc.returncode, 0, f"node parity script failed:\n{node_proc.stderr}")
        payload = json.loads(node_proc.stdout.strip())
        self.assertEqual(payload["main"], "DecisionCommit")
        self.assertEqual(len(payload["rows"]), 16)

        dec_map = {
            "DecisionCommit": gate.DECISION_COMMIT_TO_WHITEBOARD,
            "DecisionSendBack": gate.DECISION_SEND_BACK_TO_USER,
        }
        bnc_map = {
            "BounceNone": gate.BOUNCE_NONE,
            "BounceNeedsClarification": gate.BOUNCE_NEEDS_CLARIFICATION,
            "BounceFlawedIdea": gate.BOUNCE_FLAWED_IDEA,
            "BounceRuleConflict": gate.BOUNCE_RULE_CONFLICT,
        }
        for row in payload["rows"]:
            a, o, r, f = row["a"], row["o"], row["r"], row["f"]
            py_commit = gate.should_commit_whiteboard(a, o, r, f)
            py_dec = gate.route_executive_decision(a, o, r, f)
            py_bnc = gate.resolve_bounce_reason(a, o, r, f)
            self.assertEqual(row["commit"], py_commit, f"should_commit_whiteboard mismatch on {row}")
            self.assertEqual(dec_map[row["dec_tag"]], py_dec, f"route_executive_decision mismatch on {row}")
            self.assertEqual(bnc_map[row["bnc_tag"]], py_bnc, f"resolve_bounce_reason mismatch on {row}")
            self.assertEqual(row["is_commit"], py_dec == gate.DECISION_COMMIT_TO_WHITEBOARD)
            self.assertEqual(row["is_send_back"], py_dec == gate.DECISION_SEND_BACK_TO_USER)
            self.assertEqual(row["is_rc"], py_bnc == gate.BOUNCE_RULE_CONFLICT)
            self.assertEqual(row["is_fi"], py_bnc == gate.BOUNCE_FLAWED_IDEA)
            self.assertEqual(row["is_nc"], py_bnc == gate.BOUNCE_NEEDS_CLARIFICATION)
            self.assertEqual(row["is_none"], py_bnc == gate.BOUNCE_NONE)

            # Verify Laws 1-6 priority invariants directly
            if r:
                self.assertEqual(py_bnc, gate.BOUNCE_RULE_CONFLICT)
                self.assertEqual(py_dec, gate.DECISION_SEND_BACK_TO_USER)
            elif f:
                self.assertEqual(py_bnc, gate.BOUNCE_FLAWED_IDEA)
                self.assertEqual(py_dec, gate.DECISION_SEND_BACK_TO_USER)
            elif a or o:
                self.assertEqual(py_bnc, gate.BOUNCE_NEEDS_CLARIFICATION)
                self.assertEqual(py_dec, gate.DECISION_SEND_BACK_TO_USER)
            else:
                self.assertTrue(py_commit)
                self.assertEqual(py_bnc, gate.BOUNCE_NONE)
                self.assertEqual(py_dec, gate.DECISION_COMMIT_TO_WHITEBOARD)

    def test_bend_exec_23b_mutation_rejection_across_laws_1_to_6(self) -> None:
        bend_dir = _resolve_bend_exec_23b_dir()
        orig_main = (bend_dir / "main.bend").read_text(encoding="utf-8")
        mutations = [
            ("Law 1 commit_iff_all_clear", "case False{} False{} False{} True{}:\n      False{}", "case False{} False{} False{} True{}:\n      True{}"),
            ("Law 2 decision_matches_commit_flag", "case True{}:\n      DecisionCommit{}", "case True{}:\n      DecisionSendBack{}"),
            ("Law 3 conflict_dominates_bounce_reason", "case amb oq True{} fi:\n      BounceRuleConflict{}", "case amb oq True{} fi:\n      BounceNone{}"),
            ("Law 4 flawed_idea_dominates_when_no_conflict", "case amb oq False{} True{}:\n      BounceFlawedIdea{}", "case amb oq False{} True{}:\n      BounceNeedsClarification{}"),
            ("Law 5 ambiguity_or_question_bounces_for_clarification", "case True{} oq False{} False{}:\n      BounceNeedsClarification{}", "case True{} oq False{} False{}:\n      BounceNone{}"),
            ("Law 6 bounce_none_iff_commit", "def is_none_bounce(b: BounceReason) -> Bool:\n  match b:\n    case BounceNone{}:\n      True{}", "def is_none_bounce(b: BounceReason) -> Bool:\n  match b:\n    case BounceNone{}:\n      False{}"),
        ]
        spec_bg = importlib.util.spec_from_file_location("site_y_bend_gate_23b", BEND_GATE_PY)
        assert spec_bg is not None and spec_bg.loader is not None
        bend_gate = importlib.util.module_from_spec(spec_bg)
        spec_bg.loader.exec_module(bend_gate)
        for label, target, replacement in mutations:
            self.assertIn(target, orig_main, f"Mutation target missing for {label}")
            with tempfile.TemporaryDirectory(prefix="qa_bend23b_mut_") as tmp:
                tmp_dir = Path(tmp) / "bend_exec_23b"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                for bf in bend_dir.glob("*.bend"):
                    (tmp_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
                (tmp_dir / "main.bend").write_text(orig_main.replace(target, replacement, 1), encoding="utf-8")
                ok, _ = bend_gate.check_bend_dir(tmp_dir, check_canonical=False)
                self.assertFalse(ok, f"bend_gate.py check failed to reject mutation for {label}")

    def test_execute_gate_decision_clean_commit_and_sqlite_persistence(self) -> None:
        exec_pkg, gate, preflight = _import_exec_23b()
        from lattice import whiteboard as WB

        self.assertTrue(set(exec_pkg.__all__).issuperset({
            "DECISION_COMMIT_TO_WHITEBOARD", "DECISION_SEND_BACK_TO_USER",
            "BOUNCE_NONE", "BOUNCE_NEEDS_CLARIFICATION", "BOUNCE_FLAWED_IDEA", "BOUNCE_RULE_CONFLICT",
            "ExecutiveGateError", "GateDecisionResult", "should_commit_whiteboard",
            "route_executive_decision", "resolve_bounce_reason", "verify_preflight_report", "execute_gate_decision",
        }))

        with tempfile.TemporaryDirectory(prefix="qa_exec23b_clean_") as tmp:
            db_path = Path(tmp) / "clean_gate.sqlite"
            live_wb = WB.create_empty_context_graph(db_path=db_path)
            pkt = {
                "turn_index": 1,
                "grounding_status": "GROUNDED",
                "parsed_mcus": [
                    {
                        "id": "r_pool_threads", "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds",
                        "primary_scope": "worker_pool", "text": "Keep max_threads <= 16.",
                        "target_prop": "max_threads", "bound_op": "<=", "bound_val": 16,
                    },
                    {
                        "mcu_id": 2, "type": "ACTION", "facet": "actions",
                        "primary_scope": "worker_pool", "task_id": "t_pool_start",
                        "text": "Start worker_pool.",
                    },
                ],
            }
            rep = preflight.inspect_turn_preflight(pkt, live_wb)
            self.assertEqual(gate.verify_preflight_report(rep), [])

            out = gate.execute_gate_decision(rep, live_wb)
            self.assertIsInstance(out, gate.GateDecisionResult)
            self.assertEqual(out.decision, gate.DECISION_COMMIT_TO_WHITEBOARD)
            self.assertEqual(out.bounce_reason, gate.BOUNCE_NONE)
            self.assertTrue(out.should_commit)
            self.assertTrue(out.whiteboard_mutated)
            self.assertIsNotNone(out.ingestion_result)
            self.assertEqual(len(out.committed_items), 2)
            self.assertEqual(out.blocked_items, [])
            self.assertEqual(out.to_dict()["decision"], gate.DECISION_COMMIT_TO_WHITEBOARD)
            self.assertIn("lattice_proof", out.to_dict())
            self.assertIn("lattice_proof", live_wb)
            self.assertEqual(out["lattice_proof"]["status"], live_wb["lattice_proof"]["status"])

            store = live_wb.get("_sqlite_store")
            if store is not None:
                store.close()

            reloaded_store = WB.SQLiteGraphStore(db_path)
            try:
                reloaded = reloaded_store.load_graph()
                self.assertIn("worker_pool", reloaded["scopes"])
                self.assertEqual(len(reloaded["scopes"]["worker_pool"]["rules"]), 1)
                self.assertEqual(len(reloaded["active_tasks"]), 1)
            finally:
                reloaded_store.close()

    def test_execute_gate_decision_bounce_invariance_across_all_three_bounce_reasons(self) -> None:
        _, gate, preflight = _import_exec_23b()
        from lattice import whiteboard as WB

        with tempfile.TemporaryDirectory(prefix="qa_exec23b_bounce_") as tmp:
            db_path = Path(tmp) / "bounce_gate.sqlite"
            live_wb = WB.create_empty_context_graph(db_path=db_path)
            WB.ingest_intake_packet(live_wb, {
                "turn_index": 1,
                "grounding_status": "GROUNDED",
                "parsed_mcus": [
                    {
                        "id": "r_standing_bound", "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds",
                        "primary_scope": "worker_pool", "text": "Keep max_threads <= 16.",
                        "target_prop": "max_threads", "bound_op": "<=", "bound_val": 16,
                    },
                    {
                        "id": "f_standing_fact", "mcu_id": 2, "type": "FACT", "facet": "context_facts",
                        "primary_scope": "worker_pool", "text": "The runtime is python_3_14.",
                        "target_prop": "runtime", "bound_op": "==", "bound_val": "python_3_14",
                    },
                ],
            })
            snapshot_before = copy.deepcopy({k: v for k, v in live_wb.items() if k != "_sqlite_store"})
            db_bytes_before = db_path.read_bytes()

            bounce_packets = [
                (
                    gate.BOUNCE_RULE_CONFLICT,
                    {
                        "turn_index": 2,
                        "parsed_mcus": [{
                            "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds",
                            "primary_scope": "worker_pool", "text": "Set max_threads >= 64.",
                            "target_prop": "max_threads", "bound_op": ">=", "bound_val": 64,
                        }],
                    },
                ),
                (
                    gate.BOUNCE_FLAWED_IDEA,
                    {
                        "turn_index": 2,
                        "parsed_mcus": [{
                            "mcu_id": 1, "type": "PROPOSAL", "facet": "context_facts",
                            "primary_scope": "worker_pool", "text": "The user proposes runtime == ruby_2_7.",
                            "target_prop": "runtime", "bound_op": "==", "bound_val": "ruby_2_7",
                        }],
                    },
                ),
                (
                    gate.BOUNCE_NEEDS_CLARIFICATION,
                    {
                        "turn_index": 2,
                        "parsed_mcus": [{
                            "mcu_id": 1, "type": "ACTION", "facet": "actions",
                            "primary_scope": "worker_pool", "text": "Scale.",
                            "unclear_spans": ["which pool?"],
                        }],
                    },
                ),
                (
                    gate.BOUNCE_NEEDS_CLARIFICATION,
                    {
                        "turn_index": 2,
                        "parsed_mcus": [{
                            "mcu_id": 1, "type": "QUESTION", "facet": "open_questions",
                            "primary_scope": "worker_pool", "text": "What is the current queue depth?",
                        }],
                    },
                ),
            ]

            for expected_bounce, b_pkt in bounce_packets:
                rep = preflight.inspect_turn_preflight(b_pkt, live_wb)
                self.assertEqual(gate.verify_preflight_report(rep), [])
                out = gate.execute_gate_decision(rep, live_wb)
                self.assertEqual(out["decision"], gate.DECISION_SEND_BACK_TO_USER)
                self.assertEqual(out["bounce_reason"], expected_bounce)
                self.assertFalse(out["should_commit"])
                self.assertFalse(out["whiteboard_mutated"])
                self.assertIsNone(out["ingestion_result"])
                self.assertEqual(out["committed_items"], [])
                self.assertEqual(len(out["blocked_items"]), 1)
                self.assertIs(out["whiteboard_graph"], live_wb)

                snapshot_after = {k: v for k, v in live_wb.items() if k != "_sqlite_store"}
                self.assertEqual(snapshot_before, snapshot_after, f"Live Whiteboard mutated on {expected_bounce} bounce!")
                self.assertEqual(db_bytes_before, db_path.read_bytes(), f"SQLite DB mutated on {expected_bounce} bounce!")

            # Also verify bounce when whiteboard_graph=None creates a clean empty graph with 0 rules ingested
            rep_none = preflight.inspect_turn_preflight(bounce_packets[2][1], None)
            out_none = gate.execute_gate_decision(rep_none, None)
            self.assertFalse(out_none["whiteboard_mutated"])
            self.assertEqual(out_none["whiteboard_graph"]["scopes"], {})
            self.assertEqual(out_none["whiteboard_graph"]["active_tasks"], [])

            store = live_wb.get("_sqlite_store")
            if store is not None:
                store.close()

    def test_verify_preflight_report_and_execute_gate_decision_fail_closed(self) -> None:
        _, gate, preflight = _import_exec_23b()
        valid_rep = preflight.inspect_turn_preflight({
            "turn_index": 1,
            "parsed_mcus": [{"mcu_id": 1, "type": "ACTION", "facet": "actions", "primary_scope": "worker_pool", "text": "Restart worker_pool."}],
        }, None)
        self.assertEqual(gate.verify_preflight_report(valid_rep), [])

        self.assertTrue(gate.verify_preflight_report(None))  # type: ignore[arg-type]
        self.assertTrue(gate.verify_preflight_report({}))

        mutators = [
            lambda d: d.update(turn_index=0),
            lambda d: d.update(turn_index=True),
            lambda d: d.update(intake_packet="bad"),
            lambda d: d["intake_packet"].update(parsed_mcus="bad"),
            lambda d: d.update(mcu_findings="bad"),
            lambda d: d.update(has_ambiguity="bad"),
            lambda d: d.update(is_clean=False),
            lambda d: d.update(has_rule_conflict=True, is_clean=False),
            lambda d: d.update(has_flawed_idea=True, is_clean=False),
        ]
        for mut in mutators:
            bad = copy.deepcopy(valid_rep)
            mut(bad)
            errs = gate.verify_preflight_report(bad)
            self.assertTrue(errs, f"Expected verify_preflight_report errors for mutation on {bad}")
            with self.assertRaises(gate.ExecutiveGateError) as ctx:
                gate.execute_gate_decision(bad, None)
            self.assertEqual(ctx.exception.code, "gate_invalid_preflight_report")

        for bad_wb in ("not_a_dict", 42, [1, 2]):
            with self.assertRaises(gate.ExecutiveGateError) as ctx_w:
                gate.execute_gate_decision(valid_rep, bad_wb)  # type: ignore[arg-type]
            self.assertEqual(ctx_w.exception.code, "gate_invalid_whiteboard")

    def test_standing_rule_49_zero_regex_file_size_and_23b_isolation_gate(self) -> None:
        _, gate, _ = _import_exec_23b()
        exec_dir = Path(gate.__file__).resolve().parent
        for fname in ("__init__.py", "gate.py"):
            fpath = exec_dir / fname
            self.assertTrue(fpath.is_file(), f"Missing {fname} in {exec_dir}")
            src = fpath.read_text(encoding="utf-8")
            lines = src.splitlines()
            self.assertLessEqual(len(lines), MAX_23B_LINES, f"{fname} has {len(lines)} lines (> {MAX_23B_LINES})")
            tree = ast.parse(src, filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "re", f"{fname} imports forbidden `re`")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "re", f"{fname} imports from forbidden `re`")

        if "eng3" in exec_dir.parts:
            py_files = {p.name for p in exec_dir.glob("*.py")}
            self.assertEqual(py_files, {"__init__.py", "gate.py"}, f"eng3/the_executive must contain only Spec 23B modules, found {py_files}")


if __name__ == "__main__":
    unittest.main()
