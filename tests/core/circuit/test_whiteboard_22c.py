"""Spec 22C: Whiteboard Intake Linker, Task Readiness & Read Engine — Unit & Bend 2 Laws 1-11 QA Suite."""

from __future__ import annotations

import ast
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

WORKSPACE_MODEL_CIRCUITS = Path(__file__).resolve().parents[3]
REPO_ROOT = WORKSPACE_MODEL_CIRCUITS.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))
from scripts import bend_gate  # noqa: E402

CANONICAL_LAWS_22C = WORKSPACE_MODEL_CIRCUITS / "laws" / "bend_wb_22c" / "LAWS.bend"


def _resolve_spec_laws_bend() -> Path:
    if CANONICAL_LAWS_22C.is_file():
        return CANONICAL_LAWS_22C
    for p in sys.path:
        for rel in ("laws/bend_wb_22c/LAWS.bend", "docs/specs/whiteboard_22c/LAWS.bend"):
            if (cand := Path(p) / rel).is_file():
                return cand
    return CANONICAL_LAWS_22C


def _import_wb_22c() -> tuple[Any, Any, Any, Any, Any]:
    """Imports `(wb_pkg, read_engine, rewriter, linker, ingest)` from `eng2` PYTHONPATH or `core.circuit.whiteboard`."""
    for prefix in ("whiteboard", "eng2.whiteboard", "core.circuit.whiteboard"):
        if prefix == "eng2.whiteboard" and not any("/qa-" in p for p in sys.path):
            continue
        try:
            mods = tuple(importlib.import_module(f"{prefix}{s}") for s in ("", ".read_engine", ".rewriter", ".linker", ".ingest"))
            if hasattr(mods[1], "classify_task_status") and hasattr(mods[4], "ingest_into_whiteboard"):
                return mods  # type: ignore[return-value]
        except ImportError:
            pass
    raise ImportError("Could not import whiteboard 22C modules")


def _locate_bend_wb_22c_dir(read_engine_mod: Any) -> Path:
    mod_path = Path(read_engine_mod.__file__).resolve()
    for cand in (WORKSPACE_MODEL_CIRCUITS / "laws" / "bend_wb_22c", mod_path.parents[1] / "bend_wb_22c", WORKSPACE_MODEL_CIRCUITS / "eng2" / "bend_wb_22c"):
        if cand.is_dir():
            return cand
    raise FileNotFoundError(f"Could not locate bend_wb_22c directory from {mod_path}")


class TestSpec22CWhiteboardLinkerAndReadEngine(unittest.TestCase):
    """Verifies Spec 22C `whiteboard/{read_engine,rewriter,linker,ingest,__init__}.py` and `bend_wb_22c` Laws 1-11."""

    def test_bend_gate_check_and_1920_16_4_8_48_state_python_bend2_mjs_parity(self) -> None:
        _, read_engine, _, linker, ingest = _import_wb_22c()
        bend_dir = _locate_bend_wb_22c_dir(read_engine)
        spec_laws = _resolve_spec_laws_bend()
        self.assertTrue(spec_laws.is_file())
        with tempfile.TemporaryDirectory(prefix="qa_check_22c_") as tmp:
            staged_dir = Path(tmp) / "bend_wb_22c"
            staged_dir.mkdir(parents=True, exist_ok=True)
            for bf in bend_dir.glob("*.bend"):
                (staged_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
            (staged_dir / "LAWS.bend").write_text(spec_laws.read_text(encoding="utf-8"), encoding="utf-8")
            passed, msg = bend_gate.check_bend_dir(staged_dir, check_canonical=False)
            self.assertTrue(passed, f"bend_gate check failed: {msg}")

        mjs_path = bend_dir / "main.mjs"
        self.assertTrue(mjs_path.is_file(), f"Missing compiled {mjs_path}")
        node_bin = shutil.which("node") or "/opt/homebrew/bin/node"
        js_probe = f"""
import Mod from {json.dumps(mjs_path.as_posix())};
const M = Mod.default ?? Mod;
const nts = [["QUESTION", {{$: "NKQuestion"}}], ["COMMIT", {{$: "NKCommit"}}], ["ACTION", {{$: "NKAction"}}], ["FACT", {{$: "NKFact"}}], ["OBLIGATION", {{$: "NKRule"}}]];
const lks = [["COMPONENT", {{$: "LKComponent"}}], ["FLOW", {{$: "LKFlow"}}], ["SCHEMA", {{$: "LKSchema"}}], ["INTERFACE", {{$: "LKInterface"}}], ["LAW", {{$: "LKLaw"}}], ["CONSTRAINT", {{$: "LKConstraint"}}], ["FACT", {{$: "LKFact"}}], ["TASK", {{$: "LKTask"}}]];
const groundings = [["GROUNDED", {{$: "GGrounded"}}], ["HAS_OPEN_QUESTIONS", {{$: "GHasOpenQuestions"}}], ["NEEDS_CLARIFICATION", {{$: "GNeedsClarification"}}]];
const bools = [false, true];
const out = {{ delta: {{}}, prohib: {{}}, status: {{}}, task: {{}}, overwrite: {{}}, pblock: {{}}, ldelta: {{}} }};
for (const [lkName, lkVal] of lks) {{
  for (const [gName, gVal] of groundings) {{
    for (const unc of bools) {{
      const lop = M.classify_lattice_delta(lkVal, gVal, unc);
      out.ldelta[`${{lkName}}:${{gName}}:${{unc?1:0}}`] = {{ op: lop.$, is_clar: Boolean(M.is_lattice_clarification(lop)), is_sbf: Boolean(M.is_sbf_store_op(lop)) }};
    }}
  }}
}}
for (const [ntName, ntVal] of nts) {{
  for (const [ntName2, ntVal2] of nts) {{
    out.overwrite[`${{ntName}}:${{ntName2}}`] = Boolean(M.can_overwrite_node_kind(ntVal, ntVal2));
  }}
  for (const [gName, gVal] of groundings) {{
    for (const unc of bools) for (const comp of bools) for (const tp of bools) for (const pr of bools) for (const ov of bools) for (const br of bools) for (const rat of bools) {{
      const k = `${{ntName}}:${{gName}}:${{unc?1:0}}:${{comp?1:0}}:${{tp?1:0}}:${{pr?1:0}}:${{ov?1:0}}:${{br?1:0}}:${{rat?1:0}}`;
      const op = M.classify_delta(ntVal, gVal, unc, comp, tp, pr, ov, br, rat);
      out.delta[k] = {{ op: op.$, is_clar: Boolean(M.is_clarification_op(op)), is_rule_op: Boolean(M.is_rule_table_op(op)), is_overwrite: Boolean(M.is_markovian_overwrite(op)) }};
    }}
  }}
}}
for (const pa of bools) for (const pb of bools) for (const sv of bools) for (const dj of bools) {{
  out.prohib[`${{pa?1:0}}:${{pb?1:0}}:${{sv?1:0}}:${{dj?1:0}}`] = Boolean(M.prohibition_clashes(pa, pb, sv, dj));
  out.pblock[`${{pa?1:0}}:${{pb?1:0}}:${{sv?1:0}}:${{dj?1:0}}`] = Boolean(M.prohibition_blocks_task(pa, pb, sv, dj));
}}
for (const cl of bools) for (const cf of bools) {{
  const st = M.resolve_ingestion_status(cl, cf);
  out.status[`${{cl?1:0}}:${{cf?1:0}}`] = {{ status: st.$, is_committed: Boolean(M.is_committed_status(st)) }};
}}
for (const hc of bools) for (const hp of bools) for (const hd of bools) {{
  const tr = M.classify_task_readiness(hc, hp, hd);
  out.task[`${{hc?1:0}}:${{hp?1:0}}:${{hd?1:0}}`] = {{ readiness: tr.$, is_ready: Boolean(M.is_task_ready(tr)) }};
}}
console.log(JSON.stringify(out));
"""
        node_proc = subprocess.run([node_bin, "--input-type=module", "-e", js_probe], capture_output=True, text=True, timeout=20, check=True)
        bend_out = json.loads(node_proc.stdout.strip())

        op_map = {
            "OpBlockerClarification": linker.DeltaOperation.BLOCKER_CLARIFICATION,
            "OpBlockerQuestion": linker.DeltaOperation.BLOCKER_QUESTION,
            "OpCommitNote": linker.DeltaOperation.COMMIT_NOTE,
            "OpGraduateTaskToFact": linker.DeltaOperation.GRADUATE_TASK_TO_FACT,
            "OpUpsertTask": linker.DeltaOperation.UPSERT_TASK,
            "OpAttachRationale": linker.DeltaOperation.ATTACH_RATIONALE,
            "OpMarkovianOverwrite": linker.DeltaOperation.MARKOVIAN_OVERWRITE,
            "OpLexSpecialisException": linker.DeltaOperation.LEX_SPECIALIS_EXCEPTION,
            "OpOrthogonalInsert": linker.DeltaOperation.ORTHOGONAL_INSERT,
        }
        lop_map = {
            "LOpBlockerClarification": "BLOCKER_INSERT_CLARIFICATION",
            "LOpUpsertComponent": "UPSERT_COMPONENT",
            "LOpUpsertFlow": "UPSERT_FLOW",
            "LOpUpsertSchema": "UPSERT_SCHEMA",
            "LOpUpsertInterface": "UPSERT_INTERFACE",
            "LOpUpsertLaw": "UPSERT_LAW",
            "LOpDelegateRuleOrTask": "DELEGATE_RULE_OR_TASK",
        }
        status_map = {
            "StatusNeedsClarification": ingest.IngestionStatus.NEEDS_CLARIFICATION,
            "StatusConflict": ingest.IngestionStatus.CONFLICT,
            "StatusCommitted": ingest.IngestionStatus.COMMITTED,
        }
        task_map = {
            "TaskBlockedByConflict": "BLOCKED_BY_CONFLICT",
            "TaskBlockedByRule": "BLOCKED_BY_RULE",
            "TaskWaitingOnDep": "WAITING_ON_DEPENDENCY",
            "TaskReady": "READY",
        }

        self.assertEqual(len(bend_out["ldelta"]), 48)
        for key, b_ld in bend_out["ldelta"].items():
            lk_name, g_name, unc_s = key.split(":")
            py_lop = linker.classify_lattice_delta(lk_name, g_name, unc_s == "1")
            self.assertEqual(lop_map[b_ld["op"]], py_lop, key)
            self.assertEqual(b_ld["is_clar"], py_lop == "BLOCKER_INSERT_CLARIFICATION", key)
            self.assertEqual(b_ld["is_sbf"], py_lop in {"UPSERT_COMPONENT", "UPSERT_FLOW", "UPSERT_SCHEMA", "UPSERT_INTERFACE", "UPSERT_LAW"}, key)

        self.assertEqual(len(bend_out["delta"]), 1920)
        for key, b_entry in bend_out["delta"].items():
            parts = key.split(":")
            nt_name, g_name = parts[0], parts[1]
            unc, comp, tp, pr, ov, br, rat = (x == "1" for x in parts[2:])
            graph = {"scopes": {"svc": {"rules": [], "_meta": {}}}}
            existing_rules: list[tuple[str, dict[str, Any]]] = []
            if rat:
                r_rat = {"id": "r_rat", "type": "OBLIGATION", "target_prop": "other_p", "conditions": ["c_rat"], "turn": 1}
                graph["scopes"]["svc"]["_meta"]["r_rat"] = {"rationale": "Connection pool ceiling rationale."}
                existing_rules.append(("svc", r_rat))
            if pr and tp:
                existing_rules.append(("svc", {"id": "r_prohib", "type": "PROHIBITION", "target_prop": "max_conn", "bound_op": "==", "bound_val": 100, "conditions": [], "turn": 0}))
            if ov and tp:
                existing_rules.append(("svc", {"id": "r_ov", "type": nt_name, "target_prop": "max_conn", "bound_op": "==", "bound_val": 50, "conditions": [], "turn": 0}))
            if br:
                existing_rules.append(("svc", {"id": "r_broad", "type": "OBLIGATION", "target_prop": "broad_p", "conditions": ["c_broad"], "turn": 0}))
            parsed_mcu: dict[str, Any] = {
                "mcu_id": 1, "type": nt_name, "grounding_status": g_name, "text": "Pool ceiling.",
                "unclear_spans": ["unclear"] if unc else [], "completed": comp,
                "target_prop": "max_conn" if tp else None, "bound_op": "==" if tp else None, "bound_val": 100 if tp else None,
                "conditions": [], "is_exception": False, "overrides": ["r_broad"] if br else [],
            }
            py_tr = linker.classify_delta_transition(graph, parsed_mcu, scope_id="svc", ref_scopes=[], existing_rules=existing_rules, turn_index=1)
            self.assertEqual(op_map[b_entry["op"]], py_tr.operation, key)
            self.assertEqual(b_entry["is_clar"], py_tr.operation == linker.DeltaOperation.BLOCKER_CLARIFICATION, key)
            self.assertEqual(b_entry["is_rule_op"], py_tr.operation in (linker.DeltaOperation.ATTACH_RATIONALE, linker.DeltaOperation.MARKOVIAN_OVERWRITE, linker.DeltaOperation.LEX_SPECIALIS_EXCEPTION, linker.DeltaOperation.ORTHOGONAL_INSERT), key)
            self.assertEqual(b_entry["is_overwrite"], py_tr.operation == linker.DeltaOperation.MARKOVIAN_OVERWRITE, key)

        self.assertEqual(len(bend_out["prohib"]), 16)
        for key, b_clash in bend_out["prohib"].items():
            pa, pb, sv, dj = (x == "1" for x in key.split(":"))
            r_type, inc_type = ("PROHIBITION" if pa else "OBLIGATION"), ("PROHIBITION" if pb else "OBLIGATION")
            if sv:
                r_op, r_val, inc_op, inc_val = ("<", 8080, ">", 8080.0) if dj else ("==", 8080, "==", 8080.0)
            else:
                r_op, r_val, inc_op, inc_val = ("<=", 8080, ">=", 9000) if dj else (">=", 8000, "<=", 9000)
            r_existing = {"id": "r1", "type": r_type, "target_prop": "port", "bound_op": r_op, "bound_val": r_val, "conditions": []}
            py_clash = linker._is_prohibited_by_active_rule([("svc", r_existing)], "port", inc_op, inc_val, set(), inc_type, False)
            self.assertEqual(py_clash, b_clash, f"prohibition_clashes mismatch on {key}")

        self.assertEqual(len(bend_out["status"]), 4)
        for key, b_st in bend_out["status"].items():
            has_clar, has_conf = (x == "1" for x in key.split(":"))
            pkt = {"grounding_status": "NEEDS_CLARIFICATION" if has_clar else "GROUNDED"}
            confs = [{"id": "c1", "type": "CONFLICT"}] if has_conf else []
            py_st = ingest.resolve_ingestion_status(pkt, [], confs)
            self.assertEqual(status_map[b_st["status"]], py_st, key)
            self.assertEqual(b_st["is_committed"], py_st == ingest.IngestionStatus.COMMITTED, key)

        self.assertEqual(len(bend_out["task"]), 8)
        for key, b_tk in bend_out["task"].items():
            hc, hp, hd = (x == "1" for x in key.split(":"))
            py_tk = read_engine.classify_task_status(hc, hp, hd)
            self.assertEqual(task_map[b_tk["readiness"]], py_tk, key)
            self.assertEqual(b_tk["is_ready"], py_tk == "READY", key)

        self.assertEqual(len(bend_out["overwrite"]), 25)
        for key, b_ov in bend_out["overwrite"].items():
            ex_k, in_k = key.split(":")
            self.assertEqual(linker.can_overwrite_node_kind(ex_k, in_k), b_ov, key)

        self.assertEqual(len(bend_out["pblock"]), 16)
        for key, b_pb in bend_out["pblock"].items():
            rp, tp, pm, mv = (x == "1" for x in key.split(":"))
            self.assertEqual(read_engine.prohibition_blocks_task(rp, tp, pm, mv), b_pb, key)

    def test_bend_wb_22c_mutation_rejection_across_laws_1_to_11(self) -> None:
        _, read_engine, _, _, _ = _import_wb_22c()
        bend_dir = _locate_bend_wb_22c_dir(read_engine)
        orig_main = (bend_dir / "main.bend").read_text(encoding="utf-8")
        laws_text = _resolve_spec_laws_bend().read_text(encoding="utf-8")

        mutations = [
            ("Law 1 (clarification_dominates_all_deltas)", "    case _ GNeedsClarification{} _ _ _ _ _ _ _:\n      OpBlockerClarification{}", "    case _ GNeedsClarification{} _ _ _ _ _ _ _:\n      OpOrthogonalInsert{}"),
            ("Law 2 (grounded_action_never_enters_rule_table)", "    case NKAction{} GGrounded{} False{} False{} _ _ _ _ _:\n      OpUpsertTask{}", "    case NKAction{} GGrounded{} False{} False{} _ _ _ _ _:\n      OpOrthogonalInsert{}"),
            ("Law 3 (prohibition_guard_blocks_overwrite)", "    case NKRule{} GGrounded{} False{} _ True{} True{} _ False{} _:\n      OpOrthogonalInsert{}", "    case NKRule{} GGrounded{} False{} _ True{} True{} _ False{} _:\n      OpMarkovianOverwrite{}"),
            ("Law 4 (prohibition_guard_symmetric)", "    case False{} True{} True{} _:\n      True{}", "    case False{} True{} True{} _:\n      False{}"),
            ("Law 5 (prohibition_guard_disjoint_intervals)", "    case False{} True{} False{} True{}:\n      True{}", "    case False{} True{} False{} True{}:\n      False{}"),
            ("Law 6 (ingestion_status_commitment_iff_clean)", "    case False{} True{}:\n      StatusConflict{}", "    case False{} True{}:\n      StatusCommitted{}"),
            ("Law 7 (task_ready_iff_all_clear)", "    case False{} False{} True{}:\n      TaskWaitingOnDep{}", "    case False{} False{} True{}:\n      TaskReady{}"),
            ("Law 8 (epistemic_partition_blocks_cross_kind_overwrite)", "    case _ _:\n      False{}", "    case _ _:\n      True{}"),
            ("Law 9 (property_specific_prohibition_spares_untargeted_task)", "    case True{} False{} _ False{}:\n      False{}", "    case True{} False{} _ False{}:\n      True{}"),
            ("Law 10 (scope_wide_or_matching_prohibition_blocks_task)", "    case False{} _ _ _:\n      True{}", "    case False{} _ _ _:\n      False{}"),
            ("Law 11 (lattice_delta_routes_sbf_kinds)", "    case LKFlow{} GGrounded{} False{}:\n      LOpUpsertFlow{}", "    case LKFlow{} GGrounded{} False{}:\n      LOpDelegateRuleOrTask{}"),
        ]
        for label, target, replacement in mutations:
            self.assertIn(target, orig_main, f"Mutation target missing for {label}")
            with tempfile.TemporaryDirectory(prefix="qa_mut_22c_") as tmp:
                tmp_dir = Path(tmp) / "bend_wb_22c"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                for bf in bend_dir.glob("*.bend"):
                    (tmp_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
                (tmp_dir / "LAWS.bend").write_text(laws_text, encoding="utf-8")
                (tmp_dir / "main.bend").write_text(orig_main.replace(target, replacement, 1), encoding="utf-8")
                passed, _ = bend_gate.check_bend_dir(tmp_dir, check_canonical=False)
                self.assertFalse(passed, f"bend_gate check unexpectedly passed on mutated {label}")

    def test_read_engine_classify_task_status_annotate_scan_propagate_and_expand(self) -> None:
        _, read_engine, rewriter, _, _ = _import_wb_22c()
        graph = rewriter.create_empty_context_graph()
        graph["scopes"]["svc_conflict"] = {
            "id": "svc_conflict", "parent": None, "aliases": [], "attributes": {},
            "rules": [
                {"id": "rc1", "type": "OBLIGATION", "facet": "BOUND", "text": "port == 8080", "target_prop": "port", "bound_op": "==", "bound_val": 8080, "conditions": [], "refs": [], "overrides": [], "exceptions": [], "turn": 1},
                {"id": "rc2", "type": "PROHIBITION", "facet": "PROHIBITION", "text": "port != 8080", "target_prop": "port", "bound_op": "==", "bound_val": 8080, "conditions": [], "refs": [], "overrides": [], "exceptions": [], "turn": 1},
            ],
            "history": [], "_meta": {},
        }
        graph["scopes"]["svc_prohib"] = {
            "id": "svc_prohib", "parent": None, "aliases": [], "attributes": {},
            "rules": [{"id": "rp1", "type": "PROHIBITION", "facet": "PROHIBITION", "text": "Never deploy direct SQL.", "target_prop": "direct_sql", "bound_op": "==", "bound_val": "enabled", "conditions": [], "refs": [], "overrides": [], "exceptions": [], "turn": 1}],
            "history": [], "_meta": {},
        }
        graph["scopes"]["svc_ready"] = {
            "id": "svc_ready", "parent": None, "aliases": [], "attributes": {},
            "rules": [{"id": "rr1", "type": "OBLIGATION", "facet": "OBLIGATION", "text": "Enable mTLS.", "target_prop": "mtls", "bound_op": "==", "bound_val": "enabled", "conditions": [], "refs": [], "overrides": [], "exceptions": [], "turn": 1}],
            "history": [], "_meta": {},
        }
        graph["active_tasks"] = [
            {"id": "t_conf", "text": "Bind conflict port", "scope": "svc_conflict", "depends_on": [], "governed_by": ["rc1"], "completed": False, "turn": 1},
            {"id": "t_prohib", "text": "Run direct SQL", "scope": "svc_prohib", "target_prop": "direct_sql", "depends_on": [], "governed_by": ["rp1"], "completed": False, "turn": 1},
            {"id": "t_wait", "text": "Wait on t_conf", "scope": "svc_ready", "depends_on": ["t_conf"], "governed_by": ["rr1"], "completed": False, "turn": 1},
            {"id": "t_ready", "text": "Roll out mTLS", "scope": "svc_ready", "depends_on": ["rr1"], "governed_by": ["rr1"], "completed": False, "turn": 1},
        ]

        annotated = read_engine.annotate_active_tasks(graph)
        status_by_id = {t["id"]: t["status"] for t in annotated}
        self.assertEqual(status_by_id["t_conf"], "BLOCKED_BY_CONFLICT")
        self.assertEqual(status_by_id["t_prohib"], "BLOCKED_BY_RULE")
        self.assertEqual(status_by_id["t_wait"], "WAITING_ON_DEPENDENCY")
        self.assertEqual(status_by_id["t_ready"], "READY")

        scan_view = read_engine.scan_graph(graph, "mTLS port")
        self.assertGreaterEqual(len(scan_view["conflicts"]), 1)
        self.assertEqual({t["id"]: t["status"] for t in scan_view["active_tasks"]}["t_ready"], "READY")

        prop_view = read_engine.propagate_graph(graph, "mTLS port")
        self.assertGreaterEqual(len(prop_view["conflicts"]), 1)
        self.assertEqual({t["id"]: t["status"] for t in prop_view["active_tasks"]}["t_conf"], "BLOCKED_BY_CONFLICT")

        exp_view = read_engine.expand_node(graph, "svc_ready", query="mTLS")
        self.assertIn("svc_ready", exp_view["active_scopes"])

    def test_stage_4a_4b_4c_ingestion_sqlite_persistence_and_unified_exports(self) -> None:
        wb_pkg, _, _, linker, ingest = _import_wb_22c()

        for sym in ("ingest_into_whiteboard", "query_whiteboard", "classify_task_status", "slice_lattice", "classify_lattice_delta", "detect_frustration", "two_axis_mitosis", "SQLiteGraphStore"):
            self.assertTrue(hasattr(wb_pkg, sym), f"Missing export {sym} on whiteboard package")

        with tempfile.TemporaryDirectory(prefix="qa_wb_22c_sqlite_") as tmp:
            db_path = Path(tmp) / "wb_22c.sqlite"
            pkt1 = {
                "turn_index": 1, "grounding_status": "GROUNDED",
                "doc_tables": [{"heading_path": "Architecture > Services", "headers": ["Service", "Max_Workers", "Tier"], "rows": [{"Service": "Payment Worker (`pay_svc`)", "Max_Workers": "16", "Tier": "critical"}]}],
                "parsed_mcus": [
                    {"mcu_id": 1, "type": "OBLIGATION", "facet": "BOUND", "primary_scope": "pay_svc", "secondary_refs": ["audit_log"], "depends_on": ["audit_log"], "text": "pay_svc timeout must be 30.", "target_prop": "timeout", "bound_op": "==", "bound_val": 30, "conditions": []},
                    {"mcu_id": 2, "type": "ACTION", "primary_scope": "pay_svc", "text": "Provision pay_svc workers.", "task_id": "task_prov", "depends_on": ["audit_log"], "completed": False},
                    {"mcu_id": 3, "type": "COMMIT", "primary_scope": "global_defaults", "text": "Freeze schema before release."},
                ],
            }
            res1 = ingest.ingest_into_whiteboard(None, pkt1, db_path=db_path)
            self.assertEqual((res1.status, res1.graph["scopes"]["pay_svc"]["attributes"]["max_workers"], res1.graph["active_tasks"][0]["status"]), ("COMMITTED", 16, "READY"))
            self.assertIn("pay_svc", res1.fused_scopes)

            pkt2 = {
                "turn_index": 2, "grounding_status": "GROUNDED",
                "parsed_mcus": [
                    {"mcu_id": 1, "type": "OBLIGATION", "facet": "BOUND", "primary_scope": "pay_svc", "text": "Update pay_svc timeout to 45.", "target_prop": "timeout", "bound_op": "==", "bound_val": 45, "conditions": []},
                    {"mcu_id": 2, "type": "PROHIBITION", "facet": "PROHIBITION", "primary_scope": "pay_svc", "text": "In failover mode, timeout must not equal 45.", "target_prop": "timeout", "bound_op": "!=", "bound_val": 45, "conditions": ["mode == failover"], "is_exception": True},
                    {"mcu_id": 3, "type": "ACTION", "primary_scope": "pay_svc", "text": "Provision pay_svc workers completed.", "task_id": "task_prov", "completed": True},
                ],
            }
            res2 = ingest.ingest_into_whiteboard(res1.graph, pkt2)
            self.assertEqual((res2.status, len(res2.graph["active_tasks"]), [d["operation"] for d in res2.delta_log]), ("COMMITTED", 0, [linker.DeltaOperation.MARKOVIAN_OVERWRITE.value, linker.DeltaOperation.LEX_SPECIALIS_EXCEPTION.value, linker.DeltaOperation.GRADUATE_TASK_TO_FACT.value]))

            q_scan = ingest.query_whiteboard(res2.graph, "pay_svc timeout", mode="scan")
            q_prop = ingest.query_whiteboard(res2.graph, "pay_svc timeout", mode="propagate")
            q_exp = ingest.query_whiteboard(res2.graph, mode="expand", node_id="pay_svc")
            self.assertTrue("pay_svc" in q_scan["active_scopes"] and "pay_svc" in q_prop["active_scopes"] and "pay_svc" in q_exp["active_scopes"])

            if getattr(res2.graph, "store", None) is not None:
                res2.graph.store.close()
            store2 = wb_pkg.SQLiteGraphStore(db_path)
            try:
                reloaded = store2.load_graph()
            finally:
                store2.close()
            self.assertIn("pay_svc", reloaded["scopes"])
            self.assertEqual(reloaded["scopes"]["pay_svc"]["attributes"]["max_workers"], 16)
            self.assertGreaterEqual(len(reloaded["scopes"]["pay_svc"]["rules"]), 2)

    def test_standing_rule_49_zero_regex_file_size_and_22c_isolation_gate(self) -> None:
        wb_pkg, read_engine, rewriter, linker, ingest = _import_wb_22c()
        modules = {
            "read_engine.py": Path(read_engine.__file__).resolve(),
            "rewriter.py": Path(rewriter.__file__).resolve(),
            "linker.py": Path(linker.__file__).resolve(),
            "ingest.py": Path(ingest.__file__).resolve(),
            "__init__.py": Path(wb_pkg.__file__).resolve(),
        }
        for name, fpath in modules.items():
            src = fpath.read_text(encoding="utf-8")
            lines = src.splitlines()
            self.assertLessEqual(len(lines), 500, f"{name} exceeds 500 lines ({len(lines)})")
            tree = ast.parse(src, filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "re", f"Forbidden import re in {name}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "re", f"Forbidden from re import in {name}")

        wb_dir = Path(read_engine.__file__).resolve().parent
        if "eng2" in wb_dir.parts:
            py_files = {p.name for p in wb_dir.glob("*.py")}
            self.assertEqual(py_files, {"read_engine.py", "rewriter.py", "linker.py", "ingest.py", "__init__.py"})

    def test_multi_turn_e2e_whiteboard_22a_22b_22c_and_nd_lattice_sbf_slicing(self) -> None:
        wb_pkg, read_engine, rewriter, _, ingest = _import_wb_22c()
        with tempfile.TemporaryDirectory(prefix="qa_wb_e2e_") as tmp:
            db_path = Path(tmp) / "wb_e2e.sqlite"
            g1 = rewriter.create_empty_context_graph(k_cap=2, db_path=db_path)
            wb_pkg.ensure_scope_node(g1, "org_policy")
            wb_pkg.ensure_scope_node(g1, "checkout_svc", parent="org_policy")
            g1["scopes"]["org_policy"]["children"].append("checkout_svc")

            pkt1 = {"turn_index": 1, "grounding_status": "GROUNDED", "parsed_mcus": [
                {"id": "r_org_tls", "mcu_id": 1, "type": "PROHIBITION", "facet": "PROHIBITION", "primary_scope": "org_policy", "text": "Services must not use TLS 1.2.", "target_prop": "tls_min", "bound_op": "==", "bound_val": 1.2},
                {"id": "r_chk_tls", "mcu_id": 2, "type": "OBLIGATION", "facet": "BOUND", "primary_scope": "checkout_svc", "text": "Checkout legacy gateway uses TLS 1.2.", "target_prop": "tls_min", "bound_op": "==", "bound_val": 1.2},
                {"id": "r_chk_timeout", "mcu_id": 3, "type": "OBLIGATION", "facet": "BOUND", "primary_scope": "checkout_svc", "text": "Checkout timeout is 30.", "target_prop": "timeout", "bound_op": "==", "bound_val": 30},
                {"id": "r_chk_workers", "mcu_id": 4, "type": "OBLIGATION", "facet": "BOUND", "primary_scope": "checkout_svc", "text": "Checkout max workers is 8.", "target_prop": "max_workers", "bound_op": "==", "bound_val": 8, "meta": {"rationale": "Scale checkout workers during flash sale."}},
                {"id": "r_chk_http", "mcu_id": 5, "type": "PROHIBITION", "facet": "PROHIBITION", "primary_scope": "http_gateway", "text": "Plain HTTP is prohibited.", "target_prop": "plain_http", "bound_op": "==", "bound_val": "enabled"},
                {"mcu_id": 6, "type": "QUESTION", "primary_scope": "vault_svc", "text": "Which region hosts the primary vault?"},
                {"mcu_id": 7, "type": "ACTION", "primary_scope": "checkout_svc", "task_id": "t_tls", "text": "Configure checkout TLS", "target_prop": "max_workers", "depends_on": ["r_chk_tls"], "completed": False},
                {"mcu_id": 8, "type": "ACTION", "primary_scope": "http_gateway", "task_id": "t_http", "text": "Enable plain HTTP fallback", "target_prop": "plain_http", "depends_on": [], "completed": False},
                {"mcu_id": 9, "type": "ACTION", "primary_scope": "vault_svc", "task_id": "t_vault", "text": "Configure primary vault", "depends_on": [], "completed": False},
            ]}
            res1 = ingest.ingest_into_whiteboard(g1, pkt1, k_cap=2)
            self.assertEqual(res1.status, "CONFLICT")
            self.assertGreaterEqual(len(res1.mitosis_splits), 1)
            self.assertTrue(any(c["rule_ids"] == ["r_chk_tls", "r_org_tls"] for c in res1.conflicts))
            st1 = {t["id"]: t["status"] for t in res1.graph["active_tasks"]}
            self.assertEqual((st1["t_tls"], st1["t_http"], st1["t_vault"]), ("BLOCKED_BY_CONFLICT", "BLOCKED_BY_RULE", "WAITING_ON_DEPENDENCY"))

            if getattr(res1.graph, "store", None) is not None:
                res1.graph.store.close()
            store2 = wb_pkg.SQLiteGraphStore(db_path)
            try:
                g2 = wb_pkg.ContextGraph(store2.load_graph(), store=store2)
                wb_pkg.resolve_open_blocker(g2, "question_t1_m6")
                wb_pkg.resolve_open_blocker(g2, "question_t1_m6", remove=True)
                for snode in g2["scopes"].values():
                    for r in snode.get("rules", []):
                        if r.get("id") == "r_chk_tls":
                            r["is_exception"] = True
                            wb_pkg.wire_lex_specialis_override(g2, r, g2["scopes"]["org_policy"]["rules"][0], note="legacy gateway exception")

                res2 = ingest.ingest_into_whiteboard(g2, {"turn_index": 2, "grounding_status": "GROUNDED", "parsed_mcus": [{"id": "r_http_loopback", "mcu_id": 1, "type": "OBLIGATION", "facet": "BOUND", "primary_scope": "http_gateway", "text": "Loopback plain HTTP is allowed.", "target_prop": "plain_http", "bound_op": "==", "bound_val": "enabled", "conditions": ["mode == loopback"], "is_exception": True, "overrides": ["r_chk_http"]}]}, k_cap=2, store=store2)
                self.assertEqual((res2.status, res2.conflicts), ("COMMITTED", []))
                self.assertEqual({t["id"]: t["status"] for t in res2.graph["active_tasks"]}, {"t_tls": "READY", "t_http": "READY", "t_vault": "READY"})

                prop = read_engine.propagate_graph(res2.graph, "checkout flash sale workers tls", token_budget=400)
                scan = read_engine.scan_graph(res2.graph, "checkout workers", token_budget=400)
                exp = read_engine.expand_node(res2.graph, "org_policy", query="tls")
                self.assertIn("r_chk_workers", prop["lod2_meta"])
                self.assertEqual({t["id"]: t["status"] for t in prop["active_tasks"]}, {"t_tls": "READY"})
                self.assertTrue(bool(scan["text"].strip()))
                self.assertIn("org_policy", exp["active_scopes"])
            finally:
                store2.close()

        # Spec 22C §2.1-2.4 (s22c-eng2-lattice-2): Table COMPONENT promotion, multi-law ids upsert, ghost-scope exclusion, and slice_lattice dedup
        g_lat = rewriter.create_empty_context_graph()
        res_lat = ingest.ingest_into_whiteboard(g_lat, {"turn_index": 1, "grounding_status": "GROUNDED", "doc_tables": [{
            "heading_path": "Architecture > Components",
            "headers": ["Component", "Role", "Owner", "Stage", "Depends_On"],
            "rows": [{"Component": "ai_memory", "Role": "Hippocampal index & URI hydration", "Owner": "eng1", "Stage": "Stage 25", "Depends_On": "graph_core, phase3"}],
        }], "parsed_mcus": [
            {"id": "c_p3", "mcu_id": 1, "type": "COMPONENT", "lattice_kind": "COMPONENT", "primary_scope": "phase3", "secondary_refs": ["phase2"], "depends_on": ["phase2"], "owner": "eng3", "stage": "Phase 3", "text": "`phase3` owned by `eng3` in `Phase 3` depends on `phase2`."},
            {"id": "f_p3_wb", "mcu_id": 2, "type": "FLOW", "lattice_kind": "FLOW", "primary_scope": "phase3", "secondary_refs": ["whiteboard", "IntakePacket"], "source": "phase3", "target": "whiteboard", "payload": "IntakePacket", "stage": "Phase 3", "text": "`phase3` emits `IntakePacket` to `whiteboard`."},
            {"id": "s_ip", "mcu_id": 3, "type": "SCHEMA", "lattice_kind": "SCHEMA", "primary_scope": "phase3", "name": "IntakePacket", "lattice_payload": {"name": "IntakePacket"}, "owner": "eng3", "stage": "Phase 3", "text": "`IntakePacket` schema in `phase3` has fields `parsed_mcus` and `grounding_status`."},
            {"id": "i_ing", "mcu_id": 4, "type": "INTERFACE", "lattice_kind": "INTERFACE", "primary_scope": "whiteboard", "name": "ingest_into_whiteboard", "secondary_refs": ["graph_arg", "pkt_arg", "eng2"], "lattice_payload": {"name": "ingest_into_whiteboard", "inputs": ["graph_arg", "pkt_arg"], "outputs": ["IngestResult"], "owner": "eng2", "stage": "Phase 4"}, "owner": "eng2", "stage": "Phase 4", "text": "`ingest_into_whiteboard(graph_arg, pkt_arg)` ingests `IntakePacket`."},
            {"id": "l_laws", "mcu_id": 5, "type": "LAW", "lattice_kind": "LAW", "primary_scope": "whiteboard", "secondary_refs": ["LAWS.bend", "law_a", "law_b", "law_c", "eng2"], "lattice_payload": {"ids": ["law_a", "law_b", "law_c"], "owner": "eng2", "stage": "Phase 4"}, "owner": "eng2", "stage": "Phase 4", "text": "In `LAWS.bend`, formal laws `law_a`, `law_b`, and `law_c` govern `whiteboard` for `eng2` in `Phase 4`."},
            {"id": "r_no_up", "mcu_id": 6, "type": "PROHIBITION", "lattice_kind": "CONSTRAINT", "primary_scope": "phase3", "secondary_refs": ["upward_imports", "table_imports", "external_uris", "bend2_laws"], "target_prop": "upward_imports", "bound_op": "==", "bound_val": "forbidden", "text": "Never allow `upward_imports`, `table_imports`, `external_uris`, or `bend2_laws` violations in `phase3`."},
            {"id": "t_mm", "task_id": "t_mm", "mcu_id": 7, "type": "ACTION", "lattice_kind": "TASK", "primary_scope": "whiteboard", "secondary_refs": ["mind_map", "eng2"], "owner": "eng2", "stage": "Phase 4", "text": "Build `mind_map` layout in `whiteboard`.", "completed": False},
        ]})
        self.assertEqual([d["operation"] for d in res_lat.delta_log], ["UPSERT_COMPONENT", "UPSERT_FLOW", "UPSERT_SCHEMA", "UPSERT_INTERFACE", "UPSERT_LAW", "ORTHOGONAL_INSERT", "UPSERT_TASK"])
        self.assertEqual((g_lat["scopes"]["ai_memory"]["kind"], g_lat["scopes"]["ai_memory"]["owner"], g_lat["scopes"]["ai_memory"]["stage"], g_lat["scopes"]["ai_memory"]["depends_on"]), ("COMPONENT", "eng1", "Stage 25", ["graph_core", "phase3"]))
        for ghost in ("upward_imports", "table_imports", "external_uris", "bend2_laws", "IntakePacket", "mind_map", "LAWS.bend", "laws_bend", "law_a", "law_b", "law_c", "graph_arg", "pkt_arg", "IngestResult", "eng0", "eng1", "eng2", "eng3"):
            self.assertNotIn(ghost, g_lat["scopes"], f"Ghost scope {ghost!r} must not be created in graph['scopes']")
        self.assertTrue({"ai_memory", "phase3", "phase2", "whiteboard"}.issubset(g_lat["scopes"].keys()))
        self.assertEqual((g_lat["active_tasks"][0]["owner"], g_lat["active_tasks"][0]["stage"]), ("eng2", "Phase 4"))

        sl_p3 = read_engine.slice_lattice(g_lat, component="phase3")
        self.assertEqual(([c["id"] for c in sl_p3["component_specs"]], [f["payload"] for f in sl_p3["active_flows"]], [a["name"] for a in sl_p3["active_artifacts"]]), (["ai_memory", "phase3"], ["IntakePacket"], ["IntakePacket"]))
        self.assertEqual((sl_p3["compact_view"].count("@ COMPONENT [phase3]"), sl_p3["compact_view"].count("> FLOW phase3"), sl_p3["compact_view"].count("SCHEMA [IntakePacket]")), (1, 1, 1))
        sl_eng2 = read_engine.slice_lattice(g_lat, division="eng2", stage="Phase 4")
        self.assertEqual(([a["name"] for a in sl_eng2["active_artifacts"]], [t["id"] for t in sl_eng2["active_tasks"]]), (["ingest_into_whiteboard", "law_a", "law_b", "law_c"], ["t_mm"]))
        self.assertEqual((sl_eng2["compact_view"].count("LAW [law_a]"), sl_eng2["compact_view"].count("LAW [law_b]"), sl_eng2["compact_view"].count("LAW [law_c]")), (1, 1, 1))
        sl_schema = read_engine.slice_lattice(g_lat, schema="IntakePacket", kind="FLOW")
        self.assertEqual((len(sl_schema["active_flows"]), len(sl_schema["active_artifacts"]), len(sl_schema["active_rules"])), (1, 0, 0))
        # Spec 22C (s22c-eng2-lattice-3): monotonic rule ID allocation after deletion + kind="COMPONENT" slice excludes bare rule buckets
        wb_pkg.ensure_scope_node(g_lat, "goal")
        g_lat["scopes"]["goal"]["rules"] = [{"id": "r_goal_9", "type": "GOAL", "text": "Keep latency low."}, {"id": "r_goal_10", "type": "GOAL", "text": "Keep memory bounded."}]
        g_lat["scopes"]["goal"]["rules"] = [r for r in g_lat["scopes"]["goal"]["rules"] if r["id"] != "r_goal_9"]
        res_mono = ingest.ingest_into_whiteboard(g_lat, {"turn_index": 2, "grounding_status": "GROUNDED", "parsed_mcus": [{"mcu_id": 1, "type": "GOAL", "primary_scope": "goal", "text": "Keep recall hops <= 2."}]})
        self.assertEqual((res_mono.delta_log[0]["rule_id"], [c["id"] for c in read_engine.slice_lattice(g_lat, kind="COMPONENT")["component_specs"]]), ("r_goal_11", ["ai_memory", "phase3"]))

        # Spec 22C (s22c-eng2-proof-1): compile_work_packets locks when != PROVEN_COMPLETE (unless force=True) and emits Waves 0/1/2 when PROVEN_COMPLETE
        wp_locked = wb_pkg.compile_work_packets(g_lat, force=False)
        self.assertEqual((wp_locked["ready_to_emit"], wp_locked["packets"]), (False, []))
        self.assertGreaterEqual(len(wb_pkg.compile_work_packets(g_lat, force=True)["packets"]), 1)
        g_prov = rewriter.create_empty_context_graph()
        g_prov["scopes"] = {
            "svc_in": {"id": "svc_in", "kind": "COMPONENT", "owner": "eng1", "laws": ["law_in"], "inputs": ["RawReq"], "outputs": ["CleanReq"], "depends_on": [], "rules": []},
            "svc_out": {"id": "svc_out", "kind": "COMPONENT", "owner": "eng2", "laws": ["law_out"], "inputs": ["CleanReq"], "outputs": ["Resp"], "depends_on": ["svc_in"], "rules": []},
            "qa_suite": {"id": "qa_suite", "kind": "COMPONENT", "owner": "bobo", "laws": ["law_qa"], "inputs": ["Resp"], "outputs": ["Verdict"], "depends_on": ["svc_out"], "rules": []},
        }
        g_prov["flows"] = [{"id": "f_io", "source": "svc_in", "target": "svc_out", "payload": "CleanReq"}, {"id": "f_qa", "source": "svc_out", "target": "qa_suite", "payload": "Resp"}]
        g_prov["lattice_artifacts"] = [{"id": "s_req", "kind": "SCHEMA", "name": "CleanReq", "scope": "svc_in", "owner": "eng1"}, {"id": "s_resp", "kind": "SCHEMA", "name": "Resp", "scope": "svc_out", "owner": "eng2"}, {"id": "l_in", "kind": "LAW", "name": "law_in", "scope": "svc_in", "owner": "eng1"}, {"id": "l_out", "kind": "LAW", "name": "law_out", "scope": "svc_out", "owner": "eng2"}, {"id": "l_qa", "kind": "LAW", "name": "law_qa", "scope": "qa_suite", "owner": "bobo"}]
        wp_prov = ingest.compile_work_packets(g_prov, force=False)
        _pst = lambda p: p.get("build_status") or p.get("lifecycle_status") or p.get("status")
        by_comp = {(p["wave"], p.get("component") or p.get("scope")): p for p in wp_prov["packets"]}
        self.assertEqual((wp_prov["ready_to_emit"], {(p["wave"], p["kind"]) for p in wp_prov["packets"]}), (True, {(0, "SCHEMA_CONTRACTS"), (1, "COMPONENT_SLICE"), (2, "QA_VERIFICATION")}))
        self.assertEqual((_pst(by_comp[(1, "svc_in")]), _pst(by_comp[(1, "svc_out")]), _pst(next(p for p in wp_prov["packets"] if p["wave"] == 2))), ("READY", "BLOCKED_BY_DEP", "BLOCKED_BY_DEP"))
        wb_pkg.transition_component_build_status(g_prov, "svc_in", "VERIFIED", core_tag="c_in")
        self.assertEqual(_pst({(p["wave"], p.get("component") or p.get("scope")): p for p in ingest.compile_work_packets(g_prov, force=False)["packets"]}[(1, "svc_out")]), "READY")
        wb_pkg.transition_component_build_status(g_prov, "svc_out", "VERIFIED", core_tag="c_out")
        self.assertEqual(_pst(next(p for p in ingest.compile_work_packets(g_prov, force=False)["packets"] if p["wave"] == 2)), "READY")
        wb_pkg.transition_component_build_status(g_prov, "svc_in", "FAILED", failure_diagnostic="assertion failed in seed hash")
        wp_fail = ingest.compile_work_packets(g_prov, status_filter="FAILED")
        self.assertTrue(all(_pst(p) == "FAILED" for p in wp_fail["packets"]) and len(wp_fail["packets"]) >= 1)
        self.assertIn("! BUILD_FAILURE [svc_in]: assertion failed in seed hash", wp_fail["packets"][0]["prompt_slice"])


if __name__ == "__main__":
    unittest.main()
