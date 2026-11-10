"""Spec 23A: Unit & Bend 2 QA Suite for `the_executive` Stage 23A (`preflight.py` & `bend_exec_23a`).

Verifies:
  1. Bend 2 formal verification gate (`scripts/bend_gate.py check`) on `bend_exec_23a` (`LAWS.bend`, `main.bend`, `PROOF.bend`).
  2. Exhaustive truth-table parity between compiled `main.mjs` and Python `classify_mcu_status` / `is_preflight_clean`
     across all 16 boolean input states for Laws 1–4 and all 16 boolean input states for Law 5 (32 states total).
  3. Negative mutation testing of `PROOF.bend` confirming `bend2` rejects broken implementations of Laws 1–5.
  4. Non-destructive Whiteboard cloning (`clone_whiteboard_for_trial` detaches `_sqlite_store` and leaves live `whiteboard_graph`
     and SQLite storage bit-for-bit untouched) and `normalize_intake_packet` fail-closed error handling.
  5. Detection of `RULE_CONFLICT` across intra-turn/cross-turn Spin-Glass Frustration and Action/Rule clashes against standing
     `PROHIBITION`, `BOUND`, `OBLIGATION`, and `POLICY` rules (plus `is_exception` and `superseded_literals` exemptions).
  6. Detection of `FLAWED_IDEA` across all three flaw kinds (`FACT_CONTRADICTION` on `FACT` rules and fused table attributes,
     `GOAL_SABOTAGE` on `GOAL` rules, and `CRITIQUE_FLAGGED` from `flawed_reason` and `flawed_idea_critiques`), plus
     `NEEDS_CLARIFICATION` (`unclear_spans`) and `HAS_OPEN_QUESTION` (`QUESTION` MCUs).
  7. Standing Rule 49 zero-regex gate, file-size gate (`<= 450` lines), and Spec 23A module isolation in `eng1/the_executive`.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import itertools
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
SPEC_23A_LAWS = Path(__file__).resolve().parents[3] / "laws" / "bend_exec_23a" / "LAWS.bend"
BEND2_BIN = shutil.which("bend2") or str(Path.home() / ".cargo" / "bin" / "bend2")
NODE_BIN = shutil.which("node") or "node"
MAX_23A_LINES = 800


def _resolve_bend_exec_23a_dir() -> Path:
    for p in sys.path:
        cand = Path(p) / "eng1" / "bend_exec_23a"
        if cand.is_dir():
            return cand
    cand_ws = MODEL_CIRCUITS / "eng1" / "bend_exec_23a"
    return cand_ws if cand_ws.is_dir() else MODEL_CIRCUITS / "core" / "circuit" / "the_executive" / "bend_exec_23a"


def _resolve_spec_23a_laws() -> Path:
    for p in sys.path:
        cand = Path(p) / "docs" / "specs" / "executive_23a" / "LAWS.bend"
        if cand.is_file():
            return cand
    return SPEC_23A_LAWS


def _load_pkg_from_dir(pkg_name: str, pkg_dir: Path) -> Any:
    search_locs = [str(pkg_dir)]
    for p in sys.path:
        if "/qa-" in p:
            for e in ("eng1", "eng2", "eng3"):
                d = Path(p) / e / "the_executive"
                if d.is_dir() and str(d) not in search_locs:
                    search_locs.append(str(d))
            cd = Path(p) / "core" / "circuit" / "the_executive"
            if cd.is_dir() and str(cd) not in search_locs:
                search_locs.append(str(cd))
    ws_cd = MODEL_CIRCUITS / "core" / "circuit" / "the_executive"
    if ws_cd.is_dir() and str(ws_cd) not in search_locs:
        search_locs.append(str(ws_cd))
    spec = importlib.util.spec_from_file_location(
        pkg_name,
        str(pkg_dir / "__init__.py"),
        submodule_search_locations=search_locs,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_exec_23a() -> tuple[Any, Any]:
    for p in sys.path:
        if "/qa-" in p:
            cand_dir = Path(p) / "eng1" / "the_executive"
            if (cand_dir / "preflight.py").is_file():
                pkg = _load_pkg_from_dir("core.circuit.the_executive", cand_dir)
                import bowthruster.the_executive.preflight as preflight
                return pkg, preflight
    import bowthruster.the_executive as pkg
    import bowthruster.the_executive.preflight as preflight

    return pkg, preflight


class TestSpec23AExecutivePreflightAndTrial(unittest.TestCase):
    """Unit & Bend 2 QA suite for Spec 23A (`the_executive/preflight.py` and `bend_exec_23a`)."""

    def test_bend_gate_check_and_laws_1_to_5_python_bend2_mjs_parity(self) -> None:
        _, preflight = _import_exec_23a()
        bend_dir = _resolve_bend_exec_23a_dir()
        self.assertTrue(bend_dir.is_dir(), f"Missing bend_exec_23a directory: {bend_dir}")
        for fname in ("LAWS.bend", "main.bend", "PROOF.bend", "main.mjs"):
            self.assertTrue((bend_dir / fname).is_file(), f"Missing {fname} in {bend_dir}")

        spec_laws = _resolve_spec_23a_laws()
        if spec_laws.is_file():
            self.assertEqual(
                (bend_dir / "LAWS.bend").read_text(encoding="utf-8"),
                spec_laws.read_text(encoding="utf-8"),
                "LAWS.bend in bend_exec_23a drifted from canonical docs/specs/executive_23a/LAWS.bend",
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
const mcuRows = [];
for (const u of bools) {{
  for (const q of bools) {{
    for (const c of bools) {{
      for (const f of bools) {{
        const st = m.classify_mcu_status(u, q, c, f);
        mcuRows.push({{
          u, q, c, f,
          tag: st.$,
          is_rc: m.is_rule_conflict_status(st),
          is_fi: m.is_flawed_idea_status(st),
          is_nc: m.is_needs_clarification_status(st),
          is_cl: m.is_clean_status(st),
        }});
      }}
    }}
  }}
}}
const cleanRows = [];
for (const a of bools) {{
  for (const o of bools) {{
    for (const r of bools) {{
      for (const f of bools) {{
        cleanRows.push({{a, o, r, f, clean: m.is_preflight_clean(a, o, r, f)}});
      }}
    }}
  }}
}}
console.log(JSON.stringify({{main: m.main(), mcuRows, cleanRows}}));
"""
        node_proc = subprocess.run([NODE_BIN, "--input-type=module", "-e", node_script], capture_output=True, text=True, timeout=30)
        self.assertEqual(node_proc.returncode, 0, f"node parity script failed:\n{node_proc.stderr}")
        payload = json.loads(node_proc.stdout.strip())
        self.assertTrue(payload["main"])
        self.assertEqual(len(payload["mcuRows"]), 16)
        self.assertEqual(len(payload["cleanRows"]), 16)

        tag_to_status = {
            "StatusRuleConflict": preflight.MCU_STATUS_RULE_CONFLICT,
            "StatusFlawedIdea": preflight.MCU_STATUS_FLAWED_IDEA,
            "StatusNeedsClarification": preflight.MCU_STATUS_NEEDS_CLARIFICATION,
            "StatusHasOpenQuestion": preflight.MCU_STATUS_HAS_OPEN_QUESTION,
            "StatusClean": preflight.MCU_STATUS_CLEAN,
        }
        for row in payload["mcuRows"]:
            py_status = preflight.classify_mcu_status(row["u"], row["q"], row["c"], row["f"])
            self.assertEqual(tag_to_status[row["tag"]], py_status, f"MCU status mismatch on {row}")
            self.assertEqual(row["is_rc"], py_status == preflight.MCU_STATUS_RULE_CONFLICT)
            self.assertEqual(row["is_fi"], py_status == preflight.MCU_STATUS_FLAWED_IDEA)
            self.assertEqual(row["is_nc"], py_status == preflight.MCU_STATUS_NEEDS_CLARIFICATION)
            self.assertEqual(row["is_cl"], py_status == preflight.MCU_STATUS_CLEAN)

        for row in payload["cleanRows"]:
            py_clean = preflight.is_preflight_clean(row["a"], row["o"], row["r"], row["f"])
            self.assertEqual(row["clean"], py_clean, f"is_preflight_clean mismatch on {row}")

    def test_bend_exec_23a_mutation_rejection_across_laws_1_to_5(self) -> None:
        bend_dir = _resolve_bend_exec_23a_dir()
        orig_main = (bend_dir / "main.bend").read_text(encoding="utf-8")
        mutations = [
            ("Law 1 clash_dominates_mcu_status", "match has_clash:\n    case True{}:\n      StatusRuleConflict{}", "match has_clash:\n    case True{}:\n      StatusClean{}"),
            ("Law 2 flawed_idea_dominates_when_no_clash", "match has_flawed_idea:\n        case True{}:\n          StatusFlawedIdea{}", "match has_flawed_idea:\n        case True{}:\n          StatusClean{}"),
            ("Law 3 unclear_dominates_when_no_clash", "match has_unclear:\n            case True{}:\n              StatusNeedsClarification{}", "match has_unclear:\n            case True{}:\n              StatusClean{}"),
            ("Law 4 mcu_clean_iff_all_clear", "def is_clean_status(s: McuStatus) -> Bool:\n  match s:\n    case StatusClean{}:\n      True{}", "def is_clean_status(s: McuStatus) -> Bool:\n  match s:\n    case StatusClean{}:\n      False{}"),
            ("Law 5 preflight_clean_iff_no_blockers", "Bool.and(Bool.not(has_rule_conflict), Bool.not(has_flawed_idea))", "Bool.not(has_rule_conflict)"),
        ]
        spec_bg = importlib.util.spec_from_file_location("site_y_bend_gate_23a", BEND_GATE_PY)
        assert spec_bg is not None and spec_bg.loader is not None
        bend_gate = importlib.util.module_from_spec(spec_bg)
        spec_bg.loader.exec_module(bend_gate)
        for label, target, replacement in mutations:
            self.assertIn(target, orig_main, f"Mutation target missing for {label}")
            with tempfile.TemporaryDirectory(prefix="qa_bend23a_mut_") as tmp:
                tmp_dir = Path(tmp) / "bend_exec_23a"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                for bf in bend_dir.glob("*.bend"):
                    (tmp_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
                (tmp_dir / "main.bend").write_text(orig_main.replace(target, replacement, 1), encoding="utf-8")
                ok, _ = bend_gate.check_bend_dir(tmp_dir, check_canonical=False)
                self.assertFalse(ok, f"bend_gate.py check failed to reject mutation for {label}")

    def test_clone_whiteboard_for_trial_and_normalize_intake_packet_fail_closed(self) -> None:
        exec_pkg, preflight = _import_exec_23a()
        from bowthruster import whiteboard as WB
        from bowthruster.regiment.intake_packet import compile_intake_packet

        self.assertTrue(set(exec_pkg.__all__).issuperset({
            "ExecutivePreflightError", "McuPreflightStatus", "classify_mcu_status",
            "is_preflight_clean", "normalize_intake_packet", "clone_whiteboard_for_trial", "inspect_turn_preflight",
        }))

        # 1. clone_whiteboard_for_trial(None) -> detached empty Whiteboard
        empty_trial = preflight.clone_whiteboard_for_trial(None)
        self.assertIsNone(getattr(empty_trial, "_sqlite_store", None))
        self.assertNotIn("_sqlite_store", empty_trial)

        # 2. Live SQLite-backed Whiteboard remains bit-for-bit untouched during trial inspection
        with tempfile.TemporaryDirectory(prefix="qa_exec23a_sqlite_") as tmp:
            db_path = Path(tmp) / "live_wb.sqlite"
            live_wb = WB.create_empty_context_graph(db_path=db_path)
            WB.ingest_intake_packet(live_wb, {
                "turn_index": 1,
                "grounding_status": "GROUNDED",
                "parsed_mcus": [{
                    "id": "r_live_1", "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds",
                    "primary_scope": "worker_pool", "text": "Keep max_threads <= 16.",
                    "target_prop": "max_threads", "bound_op": "<=", "bound_val": 16,
                }],
            })
            live_store = getattr(live_wb, "_sqlite_store", None)
            self.assertIsNotNone(live_store)
            live_wb["_sqlite_store"] = live_store
            preflight._detach_sqlite_store(empty_trial)
            snapshot_before = copy.deepcopy({k: v for k, v in live_wb.items() if k != "_sqlite_store"})
            db_bytes_before = db_path.read_bytes()

            cloned = preflight.clone_whiteboard_for_trial(live_wb)
            self.assertIsNone(getattr(cloned, "_sqlite_store", None))
            self.assertNotIn("_sqlite_store", cloned)
            self.assertIs(getattr(live_wb, "_sqlite_store", None), live_store)
            self.assertIs(live_wb.get("_sqlite_store"), live_store)

            rep = preflight.inspect_turn_preflight({
                "turn_index": 2,
                "parsed_mcus": [{
                    "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds",
                    "primary_scope": "worker_pool", "text": "Set max_threads >= 32.",
                    "target_prop": "max_threads", "bound_op": ">=", "bound_val": 32,
                }],
            }, live_wb)
            self.assertFalse(rep["is_clean"])
            self.assertTrue(rep["has_rule_conflict"])
            snapshot_after = {k: v for k, v in live_wb.items() if k != "_sqlite_store"}
            self.assertEqual(snapshot_before, snapshot_after, "Live Whiteboard mutated during pre-flight trial!")
            self.assertEqual(db_bytes_before, db_path.read_bytes(), "Live SQLite DB mutated during pre-flight trial!")
            live_store.close()

        # 3. normalize_intake_packet from IntakePacket, Phase3Result["intake_packet"], and raw Phase3Result ("mcus" + "propositions")
        raw_p3_no_pkt = {
            "raw_text": "| Rule | Scope | Type | Target_Prop | Bound_Op | Bound_Val |\n| :--- | :--- | :--- | :--- | :--- | :--- |\n| r1 | worker_pool | BOUND | max_threads | <= | 16 |",
            "grounding_status": "GROUNDED", "is_locked": True,
            "mcus": [{"mcu_id": 1, "head_id": 1, "source_chunk_ids": [1], "text": "Keep `max_threads` <= 16 on `worker_pool`.", "canonical_category": "RULE", "primary_scope": "worker_pool", "target_prop": "max_threads", "bound_op": "<=", "bound_val": 16, "required_literals": [{"kind": "NUM", "raw": "16", "canonical_value": 16}]}],
            "propositions": [{"mcu_id": 1, "canonical_text": "Keep `max_threads` <= 16 on `worker_pool`.", "canonical_category": "RULE", "unclear_spans": []}],
        }
        p3_r = {**raw_p3_no_pkt, "intake_packet": compile_intake_packet(raw_p3_no_pkt, turn_index=3)}
        norm_from_p3 = preflight.normalize_intake_packet(p3_r, turn_index=3)
        norm_from_raw_p3 = preflight.normalize_intake_packet(raw_p3_no_pkt, turn_index=3)
        self.assertEqual(norm_from_p3["parsed_mcus"], norm_from_raw_p3["parsed_mcus"])
        self.assertEqual(norm_from_raw_p3["turn_index"], 3)

        # 4. Fail-closed validation on invalid inputs
        for bad_pkt in (None, "not_a_dict", [], {}, {"parsed_mcus": "not_a_list"}, {"parsed_mcus": [123]}, {"parsed_mcus": [], "turn_index": 0}, {"parsed_mcus": [], "turn_index": True}):
            with self.assertRaises(preflight.ExecutivePreflightError) as ctx:
                preflight.normalize_intake_packet(bad_pkt)  # type: ignore[arg-type]
            self.assertEqual(ctx.exception.code, "preflight_invalid_packet")

        for bad_turn in (0, -1, True, "2"):
            with self.assertRaises(preflight.ExecutivePreflightError) as ctx_t:
                preflight.normalize_intake_packet({"parsed_mcus": []}, turn_index=bad_turn)  # type: ignore[arg-type]
            self.assertEqual(ctx_t.exception.code, "preflight_invalid_packet")

        for bad_wb in ("invalid_wb", 123, [1, 2]):
            with self.assertRaises(preflight.ExecutivePreflightError) as ctx_w:
                preflight.clone_whiteboard_for_trial(bad_wb)  # type: ignore[arg-type]
            self.assertEqual(ctx_w.exception.code, "preflight_invalid_whiteboard")
            with self.assertRaises(preflight.ExecutivePreflightError) as ctx_iw:
                preflight.inspect_turn_preflight({"parsed_mcus": []}, bad_wb)  # type: ignore[arg-type]
            self.assertEqual(ctx_iw.exception.code, "preflight_invalid_whiteboard")

        with self.assertRaises(preflight.ExecutivePreflightError) as ctx_c:
            preflight.inspect_turn_preflight({"parsed_mcus": []}, None, flawed_idea_critiques="bad")  # type: ignore[arg-type]
        self.assertEqual(ctx_c.exception.code, "preflight_invalid_critiques")

    def test_inspect_turn_preflight_rule_conflict_frustration_and_standing_clashes(self) -> None:
        _, preflight = _import_exec_23a()
        from bowthruster import whiteboard as WB

        # 1. Intra-turn Spin-Glass Frustration between two new rules in the same turn
        intra_rep = preflight.inspect_turn_preflight({
            "turn_index": 1,
            "parsed_mcus": [
                {"id": "r_low", "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "api_gateway", "text": "Keep timeout <= 10.", "target_prop": "timeout", "bound_op": "<=", "bound_val": 10},
                {"id": "r_high", "mcu_id": 2, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "api_gateway", "text": "Keep timeout >= 30.", "target_prop": "timeout", "bound_op": ">=", "bound_val": 30},
            ],
        }, None)
        self.assertFalse(intra_rep["is_clean"])
        self.assertTrue(intra_rep["has_rule_conflict"])
        self.assertGreaterEqual(len(intra_rep["new_frustrations"]), 1)
        self.assertEqual([m["status"] for m in intra_rep["mcu_findings"]], [preflight.MCU_STATUS_RULE_CONFLICT, preflight.MCU_STATUS_RULE_CONFLICT])

        # 2. Standing PROHIBITION, BOUND, OBLIGATION, and POLICY clashes vs exceptions and self-repairs
        wb = WB.create_empty_context_graph()
        WB.ensure_scope_node(wb, "org_sec")
        WB.ensure_scope_node(wb, "edge_proxy", parent="org_sec")
        wb["scopes"]["org_sec"]["children"].append("edge_proxy")
        WB.ingest_intake_packet(wb, {
            "turn_index": 1,
            "grounding_status": "GROUNDED",
            "parsed_mcus": [
                {"id": "r_prohib_root", "mcu_id": 1, "type": "PROHIBITION", "facet": "prohibitions", "primary_scope": "org_sec", "text": "Never use ssl_v3.", "target_prop": "cipher", "bound_op": "!=", "bound_val": "ssl_v3"},
                {"id": "r_bound_pool", "mcu_id": 2, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "edge_proxy", "text": "Keep drain_sec <= 60.", "target_prop": "drain_sec", "bound_op": "<=", "bound_val": 60},
                {"id": "r_oblig_mode", "mcu_id": 3, "type": "OBLIGATION", "facet": "requirements", "primary_scope": "edge_proxy", "text": "Sync mode must be quorum.", "target_prop": "sync_mode", "bound_op": "==", "bound_val": "quorum"},
                {"id": "r_policy_tls", "mcu_id": 4, "type": "POLICY", "facet": "requirements", "primary_scope": "edge_proxy", "text": "Protocol must be tls_1_3.", "target_prop": "protocol", "bound_op": "==", "bound_val": "tls_1_3"},
            ],
        })

        clash_rep = preflight.inspect_turn_preflight({
            "turn_index": 2,
            "parsed_mcus": [
                # MCU 1: ACTION clashing with ancestor standing PROHIBITION
                {"mcu_id": 1, "type": "ACTION", "facet": "actions", "primary_scope": "edge_proxy", "text": "Enable ssl_v3 on edge_proxy.", "target_prop": "cipher", "bound_op": "==", "bound_val": "ssl_v3"},
                # MCU 2: ACTION whose numeric parameter violates standing BOUND (90 vs <= 60)
                {"mcu_id": 2, "type": "ACTION", "facet": "actions", "primary_scope": "edge_proxy", "text": "Set drain_sec to 90 on edge_proxy.", "target_prop": "drain_sec", "bound_op": "==", "bound_val": 90},
                # MCU 3: Un-excepted OBLIGATION attempting silent overwrite of standing OBLIGATION
                {"mcu_id": 3, "type": "OBLIGATION", "facet": "requirements", "primary_scope": "edge_proxy", "text": "Sync mode must be async.", "target_prop": "sync_mode", "bound_op": "==", "bound_val": "async"},
                # MCU 4: Un-excepted POLICY clashing with standing POLICY
                {"mcu_id": 4, "type": "POLICY", "facet": "requirements", "primary_scope": "edge_proxy", "text": "Protocol must be tls_1_2.", "target_prop": "protocol", "bound_op": "==", "bound_val": "tls_1_2"},
                # MCU 5: Explicit exception (`is_exception=True`) is allowed (CLEAN)
                {"mcu_id": 5, "type": "POLICY", "facet": "requirements", "primary_scope": "edge_proxy", "text": "Unless legacy_test is active, protocol is tls_1_2.", "target_prop": "protocol", "bound_op": "==", "bound_val": "tls_1_2", "conditions": ["unless legacy_test is active"], "is_exception": True},
                # MCU 6: Explicit self-repair (`superseded_literals` non-empty) is allowed (CLEAN)
                {"mcu_id": 6, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "edge_proxy", "text": "Not 60, keep drain_sec <= 45.", "target_prop": "drain_sec", "bound_op": "<=", "bound_val": 45, "superseded_literals": [{"kind": "NUM", "raw": "60", "canonical_value": 60}]},
            ],
        }, wb)
        self.assertFalse(clash_rep["is_clean"])
        self.assertTrue(clash_rep["has_rule_conflict"])
        statuses = {m["mcu_id"]: m["status"] for m in clash_rep["mcu_findings"]}
        self.assertEqual(statuses, {
            1: preflight.MCU_STATUS_RULE_CONFLICT,
            2: preflight.MCU_STATUS_RULE_CONFLICT,
            3: preflight.MCU_STATUS_RULE_CONFLICT,
            4: preflight.MCU_STATUS_RULE_CONFLICT,
            5: preflight.MCU_STATUS_CLEAN,
            6: preflight.MCU_STATUS_CLEAN,
        })

        # 3. Structural & flow frustration blockers (`conflict_dep_*`, `conflict_flow_*`, `conflict_payload_*`) mapped onto mcu_conflicts and conflicts
        wb_sbf = WB.create_empty_context_graph()
        WB.ingest_intake_packet(wb_sbf, {
            "turn_index": 1,
            "grounding_status": "GROUNDED",
            "parsed_mcus": [
                {"id": "c_b", "mcu_id": 1, "type": "COMPONENT", "lattice_kind": "COMPONENT", "primary_scope": "svc_b", "depends_on": ["svc_a"], "inputs": ["CleanTok"], "outputs": ["Resp"], "owner": "eng2", "text": "`svc_b` owned by `eng2` depends on `svc_a`."},
                {"id": "i_b", "mcu_id": 2, "type": "INTERFACE", "lattice_kind": "INTERFACE", "primary_scope": "svc_b", "name": "handle_b", "lattice_payload": {"name": "handle_b", "inputs": ["CleanTok"], "outputs": ["Resp"]}, "text": "`handle_b(CleanTok) -> Resp`."},
            ],
        })
        sbf_rep = preflight.inspect_turn_preflight({
            "turn_index": 2,
            "parsed_mcus": [
                {"id": "c_a", "mcu_id": 1, "type": "COMPONENT", "lattice_kind": "COMPONENT", "primary_scope": "svc_a", "depends_on": ["svc_b"], "inputs": ["Req"], "outputs": ["AuthTok"], "owner": "eng1", "text": "`svc_a` owned by `eng1` depends on `svc_b`."},
                {"id": "f_aa", "mcu_id": 2, "type": "FLOW", "lattice_kind": "FLOW", "primary_scope": "svc_a", "source": "svc_a", "target": "svc_a", "payload": "AuthTok", "text": "`svc_a` emits `AuthTok` to `svc_a`."},
                {"id": "f_ab", "mcu_id": 3, "type": "FLOW", "lattice_kind": "FLOW", "primary_scope": "svc_a", "source": "svc_a", "target": "svc_b", "payload": "WrongPayload", "text": "`svc_a` emits `WrongPayload` to `svc_b`."},
            ],
        }, wb_sbf)
        self.assertFalse(sbf_rep["is_clean"])
        self.assertTrue(sbf_rep["has_rule_conflict"])
        self.assertEqual([m["status"] for m in sbf_rep["mcu_findings"]], [preflight.MCU_STATUS_RULE_CONFLICT] * 3)
        for m in sbf_rep["mcu_findings"]:
            self.assertGreaterEqual(len(m["conflicts"]), 1)
        all_conf_ids = {c.get("conflict_id") or c.get("id") for c in sbf_rep["conflicts"]} | {c.get("id") for c in sbf_rep["new_frustrations"]}
        self.assertTrue(any(str(cid).startswith("conflict_dep_") for cid in all_conf_ids))
        self.assertTrue(any(str(cid).startswith("conflict_flow_") for cid in all_conf_ids))
        self.assertTrue(any(str(cid).startswith("conflict_payload_") for cid in all_conf_ids))

    def test_inspect_turn_preflight_flawed_idea_all_three_kinds_and_ambiguity_questions(self) -> None:
        _, preflight = _import_exec_23a()
        from bowthruster import whiteboard as WB

        wb = WB.create_empty_context_graph()
        WB.ingest_intake_packet(wb, {
            "turn_index": 1,
            "grounding_status": "GROUNDED",
            "doc_tables": [{
                "table_index": 1,
                "heading_path": "Cache Tiers",
                "headers": ["Service", "max_memory_gb"],
                "rows": [{"Service": "redis_cache", "max_memory_gb": "16"}],
            }],
            "parsed_mcus": [
                {"id": "f_os", "mcu_id": 1, "type": "FACT", "facet": "context_facts", "primary_scope": "redis_cache", "text": "The host OS is linux.", "target_prop": "host_os", "bound_op": "==", "bound_val": "linux"},
                {"id": "g_lat", "mcu_id": 2, "type": "GOAL", "facet": "context_facts", "primary_scope": "redis_cache", "text": "The goal is latency_ms <= 5.", "target_prop": "latency_ms", "bound_op": "<=", "bound_val": 5},
            ],
        })

        rep = preflight.inspect_turn_preflight(
            {
                "turn_index": 2,
                "parsed_mcus": [
                    # MCU 1: Contradicts established FACT rule (`host_os == 'linux'`)
                    {"mcu_id": 1, "type": "PROPOSAL", "facet": "context_facts", "primary_scope": "redis_cache", "text": "The user proposes setting host_os to windows.", "target_prop": "host_os", "bound_op": "==", "bound_val": "windows"},
                    # MCU 2: Contradicts fused table attribute (`redis_cache.max_memory_gb == 16`)
                    {"mcu_id": 2, "type": "ACTION", "facet": "actions", "primary_scope": "redis_cache", "text": "Allocate 64 GB for max_memory_gb on redis_cache.", "target_prop": "max_memory_gb", "bound_op": "==", "bound_val": 64},
                    # MCU 3: Sabotages active GOAL rule (`latency_ms <= 5`)
                    {"mcu_id": 3, "type": "PROPOSAL", "facet": "context_facts", "primary_scope": "redis_cache", "text": "The user proposes setting latency_ms to 50.", "target_prop": "latency_ms", "bound_op": "==", "bound_val": 50},
                    # MCU 4: Explicit `flawed_reason` on MCU (`CRITIQUE_FLAGGED`)
                    {"mcu_id": 4, "type": "PROPOSAL", "facet": "context_facts", "primary_scope": "redis_cache", "text": "Disable all persistence backups.", "flawed_reason": "Disabling persistence violates durability requirements."},
                    # MCU 5: External `flawed_idea_critiques` entry matching mcu_id=5 (`CRITIQUE_FLAGGED`)
                    {"mcu_id": 5, "type": "ACTION", "facet": "actions", "primary_scope": "redis_cache", "text": "Flush production keys now."},
                    # MCU 6: Ambiguous MCU (`unclear_spans` -> `NEEDS_CLARIFICATION`)
                    {"mcu_id": 6, "type": "ACTION", "facet": "actions", "primary_scope": "redis_cache", "text": "Restart.", "unclear_spans": ["which shard?"]},
                    # MCU 7: Open Question MCU (`type == "QUESTION"` -> `HAS_OPEN_QUESTION`)
                    {"mcu_id": 7, "type": "QUESTION", "facet": "open_questions", "primary_scope": "redis_cache", "text": "Which availability zone hosts the replica?"},
                    # MCU 8: Clean MCU (`CLEAN`)
                    {"mcu_id": 8, "type": "ACTION", "facet": "actions", "primary_scope": "redis_cache", "text": "Monitor cache hit ratio."},
                ],
            },
            wb,
            flawed_idea_critiques=[{
                "mcu_id": 5,
                "flaw_kind": preflight.FLAW_KIND_CRITIQUE_FLAGGED,
                "basis_id": "crit_flush_prod",
                "description": "Flushing production keys causes immediate outage.",
            }],
        )
        self.assertFalse(rep["is_clean"])
        self.assertTrue(rep["has_flawed_idea"])
        self.assertTrue(rep["has_ambiguity"])
        self.assertTrue(rep["has_open_questions"])
        self.assertFalse(rep["has_rule_conflict"])

        by_id = {m["mcu_id"]: m for m in rep["mcu_findings"]}
        self.assertEqual(by_id[1]["status"], preflight.MCU_STATUS_FLAWED_IDEA)
        self.assertEqual(by_id[1]["flawed_ideas"][0]["flaw_kind"], preflight.FLAW_KIND_FACT_CONTRADICTION)
        self.assertEqual(by_id[2]["status"], preflight.MCU_STATUS_FLAWED_IDEA)
        self.assertEqual(by_id[2]["flawed_ideas"][0]["flaw_kind"], preflight.FLAW_KIND_FACT_CONTRADICTION)
        self.assertIn("attr:redis_cache.max_memory_gb", by_id[2]["flawed_ideas"][0]["basis_id"])
        self.assertEqual(by_id[3]["status"], preflight.MCU_STATUS_FLAWED_IDEA)
        self.assertEqual(by_id[3]["flawed_ideas"][0]["flaw_kind"], preflight.FLAW_KIND_GOAL_SABOTAGE)
        self.assertEqual(by_id[4]["status"], preflight.MCU_STATUS_FLAWED_IDEA)
        self.assertEqual(by_id[4]["flawed_ideas"][0]["flaw_kind"], preflight.FLAW_KIND_CRITIQUE_FLAGGED)
        self.assertEqual(by_id[5]["status"], preflight.MCU_STATUS_FLAWED_IDEA)
        self.assertEqual(by_id[5]["flawed_ideas"][0]["flaw_kind"], preflight.FLAW_KIND_CRITIQUE_FLAGGED)
        self.assertEqual(by_id[6]["status"], preflight.MCU_STATUS_NEEDS_CLARIFICATION)
        self.assertEqual(by_id[7]["status"], preflight.MCU_STATUS_HAS_OPEN_QUESTION)
        self.assertEqual(by_id[8]["status"], preflight.MCU_STATUS_CLEAN)
        self.assertEqual(rep["clarification_questions"], ["which shard?"])
        self.assertEqual(rep["open_questions"], ["Which availability zone hosts the replica?"])

        # Verify a purely clean turn returns is_clean=True
        clean_rep = preflight.inspect_turn_preflight({
            "turn_index": 3,
            "parsed_mcus": [{"mcu_id": 1, "type": "ACTION", "facet": "actions", "primary_scope": "redis_cache", "text": "Record telemetry snapshot."}],
        }, wb)
        self.assertTrue(clean_rep["is_clean"])
        self.assertEqual([m["status"] for m in clean_rep["mcu_findings"]], [preflight.MCU_STATUS_CLEAN])

    def test_standing_rule_49_zero_regex_file_size_and_23a_isolation_gate(self) -> None:
        _, preflight = _import_exec_23a()
        exec_dir = Path(preflight.__file__).resolve().parent
        for fname in ("__init__.py", "preflight.py"):
            fpath = exec_dir / fname
            self.assertTrue(fpath.is_file(), f"Missing {fname} in {exec_dir}")
            src = fpath.read_text(encoding="utf-8")
            lines = src.splitlines()
            self.assertLessEqual(len(lines), MAX_23A_LINES, f"{fname} has {len(lines)} lines (> {MAX_23A_LINES})")
            tree = ast.parse(src, filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "re", f"{fname} imports forbidden `re`")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "re", f"{fname} imports from forbidden `re`")

        if "eng1" in exec_dir.parts:
            py_files = {p.name for p in exec_dir.glob("*.py")}
            self.assertEqual(py_files, {"__init__.py", "preflight.py"}, f"eng1/the_executive must contain only Spec 23A modules, found {py_files}")


if __name__ == "__main__":
    unittest.main()
