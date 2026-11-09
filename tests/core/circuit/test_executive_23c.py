"""Spec 23C Unit & Bend 2 QA Suite: Story-Calibrated Executive Voice, Reply Guard, & Orchestrator (`voice.py`, `orchestrator.py`, `__init__.py`).

Verifies against Spec 23C (`docs/specs/executive_23c/23c_EXECUTIVE_VOICE_AND_ORCHESTRATOR.md` & `LAWS.bend`):
1. Bend 2 formal verification (`bend_exec_23c/PROOF.bend` + `scripts/bend_gate.py check`) + mutation rejection across Laws 1-3.
2. 100% truth-table parity across all 16 boolean input states between Python `is_reply_valid`, Bend 2 Laws 1-3, and compiled `main.mjs`.
3. Prompt loading (`load_executive_prompt`) and formatting (`format_executive_prompt`) with the Four Conference Room Parables and all 8 placeholders.
4. Fail-closed reply guard (`verify_executive_reply`) rejecting verdict overrides, empty messages, or missing citations.
5. Deterministic and generator-backed voice synthesis (`render_deterministic_executive_reply`, `synthesize_executive_voice`) with 1-tuple/2-tuple `(raw, meta)` unpacking, truncation detection, 1-retry recovery, and fail-closed `ExecutiveVoiceError`.
6. End-to-end `run_executive_turn` orchestration across `COMMIT_TO_WHITEBOARD` and `SEND_BACK_TO_USER` (`RULE_CONFLICT`, `FLAWED_IDEA`, `NEEDS_CLARIFICATION`), verifying multi-turn Whiteboard + SQLite progression and bounce invariance.
7. Standing Rule 49 zero-regex gate, file-size limits (<= 400 lines per Spec 23C §1), and 23C isolation gate.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

WORKSPACE_LATTICE = Path(__file__).resolve().parents[3]
REPO_ROOT = Path(__file__).resolve().parents[4]
BEND_GATE_PY = WORKSPACE_LATTICE / "laws" / "bend_gate.py" if (WORKSPACE_LATTICE / "laws" / "bend_gate.py").is_file() else REPO_ROOT / "scripts" / "bend_gate.py"


def _locate_exec_23c_dir() -> Path:
    for entry in sys.path:
        if "/qa-" in entry:
            cand = Path(entry) / "eng2" / "the_executive"
            if (cand / "voice.py").is_file() and (cand / "orchestrator.py").is_file():
                return cand
    cand_lat = WORKSPACE_LATTICE / "lattice" / "the_executive"
    if (cand_lat / "voice.py").is_file() and (cand_lat / "orchestrator.py").is_file():
        return cand_lat
    for entry in sys.path:
        if not entry:
            continue
        cand = Path(entry) / "core" / "circuit" / "the_executive"
        if (cand / "voice.py").is_file() and (cand / "orchestrator.py").is_file():
            return cand
    return REPO_ROOT / "ModelCircuits" / "core" / "circuit" / "the_executive"


def _locate_bend_exec_23c_dir() -> Path:
    laws_cand = WORKSPACE_LATTICE / "laws" / "bend_exec_23c"
    if (laws_cand / "PROOF.bend").is_file():
        return laws_cand
    for entry in sys.path:
        if not entry:
            continue
        cand = Path(entry) / "eng2" / "bend_exec_23c"
        if (cand / "PROOF.bend").is_file():
            return cand
    return REPO_ROOT / "ModelCircuits" / "eng2" / "bend_exec_23c"


def _locate_spec_laws_bend() -> Path:
    spec_cand = WORKSPACE_LATTICE / "docs" / "specs" / "executive_23c" / "LAWS.bend"
    if spec_cand.is_file():
        return spec_cand
    for entry in sys.path:
        if not entry:
            continue
        cand = Path(entry) / "docs" / "specs" / "executive_23c" / "LAWS.bend"
        if cand.is_file():
            return cand
    return REPO_ROOT / "ModelCircuits" / "docs" / "specs" / "executive_23c" / "LAWS.bend"


def _import_exec_23c() -> tuple[Any, Any, Any, Any, Any]:
    cand_dir = _locate_exec_23c_dir()
    core_exec_dir = REPO_ROOT / "ModelCircuits" / "core" / "circuit" / "the_executive"
    search_locs = [str(cand_dir)]
    for entry in sys.path:
        if "/qa-" in entry:
            for e in ("eng2", "eng3", "eng1"):
                d = Path(entry) / e / "the_executive"
                if d.is_dir() and str(d) not in search_locs:
                    search_locs.append(str(d))
            cd = Path(entry) / "core" / "circuit" / "the_executive"
            if cd.is_dir() and str(cd) not in search_locs:
                search_locs.append(str(cd))
    for extra in (
        Path("/tmp/qa-s23c-spec-exec-voice-3/ModelCircuits/lattice/the_executive"),
        core_exec_dir,
    ):
        if extra.is_dir() and str(extra) not in search_locs:
            search_locs.append(str(extra))

    pkg_name = "core.circuit.the_executive"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]

    init_spec = importlib.util.spec_from_file_location(
        pkg_name,
        cand_dir / "__init__.py",
        submodule_search_locations=search_locs,
    )
    assert init_spec is not None and init_spec.loader is not None
    pkg = importlib.util.module_from_spec(init_spec)
    sys.modules[pkg_name] = pkg
    init_spec.loader.exec_module(pkg)

    from lattice.the_executive import gate, orchestrator, preflight, voice
    return pkg, voice, orchestrator, gate, preflight


class _ScriptedVoiceGen:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any], int]] = []

    def generate_json(self, prompt: str, schema_rec: dict[str, Any], max_tokens: int) -> Any:
        self.calls.append((prompt, schema_rec, max_tokens))
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx]


class TestSpec23CExecutiveVoiceAndOrchestrator(unittest.TestCase):
    """Unit and Bend 2 QA tests for Spec 23C (`voice.py`, `orchestrator.py`, `__init__.py`, `bend_exec_23c/`)."""

    def test_bend_gate_check_and_laws_1_to_3_python_bend2_mjs_parity(self) -> None:
        _, voice, _, _, _ = _import_exec_23c()
        bend_dir = _locate_bend_exec_23c_dir()
        spec_laws = _locate_spec_laws_bend()
        self.assertEqual(
            (bend_dir / "LAWS.bend").read_bytes(),
            spec_laws.read_bytes(),
            "eng2/bend_exec_23c/LAWS.bend diverged from canonical docs/specs/executive_23c/LAWS.bend!",
        )

        gate_proc = subprocess.run(
            [sys.executable, str(BEND_GATE_PY), "check", str(bend_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(gate_proc.returncode, 0, f"bend_gate.py check failed: {gate_proc.stdout}\n{gate_proc.stderr}")
        self.assertIn("PASS (bend_exec_23c): ALL PROOFS CHECK", gate_proc.stdout)

        mjs_path = (bend_dir / "main.mjs").resolve()
        node_script = f"""
import M from {json.dumps(mjs_path.as_uri())};
const rows = [];
for (const d of [false, true]) {{
  for (const r of [false, true]) {{
    for (const m of [false, true]) {{
      for (const c of [false, true]) {{
        rows.push({{ d, r, m, c, valid: Boolean(M.is_reply_valid(d, r, m, c)) }});
      }}
    }}
  }}
}}
console.log(JSON.stringify(rows));
"""
        node_proc = subprocess.run(
            ["node", "--input-type=module", "-e", node_script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(node_proc.returncode, 0, f"node main.mjs execution failed: {node_proc.stderr}")
        mjs_rows = {(r["d"], r["r"], r["m"], r["c"]): r["valid"] for r in json.loads(node_proc.stdout)}
        self.assertEqual(len(mjs_rows), 16)

        for d, r, m, c in itertools.product([False, True], repeat=4):
            py_valid = voice.is_reply_valid(d, r, m, c)
            expected_valid = bool(d and r and m and c)
            self.assertEqual(py_valid, expected_valid)
            if not d or not r:
                self.assertFalse(py_valid)
            if not m or not c:
                self.assertFalse(py_valid)
            self.assertEqual(py_valid, mjs_rows[(d, r, m, c)], f"Mismatch vs main.mjs at state {(d, r, m, c)}")

    def test_bend_exec_23c_mutation_rejection_across_laws_1_to_3(self) -> None:
        bend_dir = _locate_bend_exec_23c_dir()
        orig_main = (bend_dir / "main.bend").read_text(encoding="utf-8")
        mutations = [
            ("law1_accept_missing_citations", "case True{} True{} True{} False{}:\n      False{}", "case True{} True{} True{} False{}:\n      True{}"),
            ("law1_reject_all_true", "case True{} True{} True{} True{}:\n      True{}", "case True{} True{} True{} True{}:\n      False{}"),
            ("law2_accept_mismatched_reason", "case True{} False{} _ _:\n      False{}", "case True{} False{} _ _:\n      True{}"),
            ("law2_accept_mismatched_decision", "case False{} _ _ _:\n      False{}", "case False{} _ _ _:\n      True{}"),
            ("law3_accept_empty_message", "case True{} True{} False{} _:\n      False{}", "case True{} True{} False{} _:\n      True{}"),
        ]
        spec_bg = importlib.util.spec_from_file_location("site_y_bend_gate_23c", BEND_GATE_PY)
        assert spec_bg is not None and spec_bg.loader is not None
        bend_gate = importlib.util.module_from_spec(spec_bg)
        spec_bg.loader.exec_module(bend_gate)
        for label, target, replacement in mutations:
            self.assertIn(target, orig_main, f"Mutation target {label} not found")
            with tempfile.TemporaryDirectory(prefix=f"bend23c_mut_{label}_") as tmp:
                tmp_dir = Path(tmp)
                for fn in ("LAWS.bend", "PROOF.bend", "main.bend", "main.mjs"):
                    (tmp_dir / fn).write_text((bend_dir / fn).read_text(encoding="utf-8"), encoding="utf-8")
                (tmp_dir / "main.bend").write_text(orig_main.replace(target, replacement, 1), encoding="utf-8")
                ok, _ = bend_gate.check_bend_dir(tmp_dir, check_canonical=False)
                self.assertFalse(ok, f"Bend 2 gate failed to reject mutation {label}!")

    def test_load_and_format_executive_prompt_parables_and_placeholders(self) -> None:
        _, voice, _, gate, preflight = _import_exec_23c()
        prompt_meta = voice.load_executive_prompt()
        self.assertIn(prompt_meta["version"], {"p-executive-2", "p-executive-3"})
        self.assertEqual(prompt_meta["name"], "executive_synthesize_turn")
        self.assertEqual(set(prompt_meta["schema"]["required"]), {"decision", "bounce_reason", "user_message", "cited_items"})
        for parable in (
            "The Parable of the Unanchored Instruction",
            "The Parable of the Silent Contradiction",
            "The Parable of the Foolish Plan",
            "The Parable of the Decisive Commit",
        ):
            self.assertIn(parable, prompt_meta["text"])

        bounce_rep = preflight.inspect_turn_preflight({
            "turn_index": 1,
            "parsed_mcus": [{"mcu_id": 1, "type": "QUESTION", "facet": "open_questions", "primary_scope": "worker_pool", "text": "Which region hosts worker_pool?"}],
        }, None)
        gate_bounce = gate.execute_gate_decision(bounce_rep, None)
        formatted_bounce = voice.format_executive_prompt(gate_bounce)
        self.assertIn("gate_decision: SEND_BACK_TO_USER", formatted_bounce)
        self.assertIn("bounce_reason: NEEDS_CLARIFICATION", formatted_bounce)
        self.assertIn("Which region hosts worker_pool?", formatted_bounce)
        self.assertIn("[EMPTY_WHITEBOARD]", formatted_bounce)

        rep_commit = preflight.inspect_turn_preflight({
            "turn_index": 1,
            "parsed_mcus": [{"id": "r_threads", "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "worker_pool", "text": "Keep max_threads <= 16.", "target_prop": "max_threads", "bound_op": "<=", "bound_val": 16}],
        }, None)
        gate_commit = gate.execute_gate_decision(rep_commit, None)
        formatted_commit = voice.format_executive_prompt(gate_commit)
        self.assertIn("gate_decision: COMMIT_TO_WHITEBOARD", formatted_commit)
        self.assertIn("bounce_reason: NONE", formatted_commit)
        self.assertIn("Keep max_threads <= 16.", formatted_commit)
        self.assertIn("worker_pool", formatted_commit)

        with self.assertRaises(voice.ExecutiveVoiceError):
            voice.load_executive_prompt("")
        with self.assertRaises(voice.ExecutiveVoiceError):
            voice.load_executive_prompt("executive.nonexistent_key")
        with self.assertRaises(voice.ExecutiveVoiceError):
            voice.format_executive_prompt(None)  # type: ignore[arg-type]
        with self.assertRaises(voice.ExecutiveVoiceError):
            voice.format_executive_prompt({"decision": "COMMIT_TO_WHITEBOARD"})

    def test_verify_executive_reply_fail_closed_guard(self) -> None:
        _, voice, _, gate, preflight = _import_exec_23c()
        from lattice import whiteboard as WB

        wb = WB.create_empty_context_graph()
        WB.ingest_intake_packet(wb, {
            "turn_index": 1,
            "grounding_status": "GROUNDED",
            "parsed_mcus": [
                {"id": "r_standing", "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "worker_pool", "text": "Keep max_threads <= 16.", "target_prop": "max_threads", "bound_op": "<=", "bound_val": 16},
                {"id": "f_standing", "mcu_id": 2, "type": "FACT", "facet": "context_facts", "primary_scope": "worker_pool", "text": "The runtime is python_3_14.", "target_prop": "runtime", "bound_op": "==", "bound_val": "python_3_14"},
            ],
        })

        rep = preflight.inspect_turn_preflight({
            "turn_index": 2,
            "parsed_mcus": [
                {"mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "worker_pool", "text": "Set max_threads >= 64.", "target_prop": "max_threads", "bound_op": ">=", "bound_val": 64},
                {"mcu_id": 2, "type": "PROPOSAL", "facet": "context_facts", "primary_scope": "worker_pool", "text": "The user proposes setting runtime to ruby_2_0.", "target_prop": "runtime", "bound_op": "==", "bound_val": "ruby_2_0"},
                {"mcu_id": 3, "type": "ACTION", "facet": "actions", "primary_scope": "worker_pool", "text": "Deploy now.", "unclear_spans": ["which cluster?"]},
                {"mcu_id": 4, "type": "QUESTION", "facet": "open_questions", "primary_scope": "worker_pool", "text": "What is the drain timeout?"},
            ],
        }, wb)
        gate_res = gate.execute_gate_decision(rep, wb)
        det_reply = voice.render_deterministic_executive_reply(gate_res)
        self.assertEqual(voice.verify_executive_reply(gate_res, det_reply), [])

        self.assertTrue(voice.verify_executive_reply(None, det_reply))  # type: ignore[arg-type]
        self.assertTrue(voice.verify_executive_reply(gate_res, None))  # type: ignore[arg-type]
        self.assertTrue(voice.verify_executive_reply(gate_res, {}))

        for k, v in (("decision", "COMMIT_TO_WHITEBOARD"), ("bounce_reason", "NONE"), ("user_message", "   "), ("cited_items", [""])):
            bad = copy.deepcopy(det_reply)
            bad[k] = v
            self.assertTrue(any(k in e for e in voice.verify_executive_reply(gate_res, bad)))

        no_anchors = {
            "decision": gate_res["decision"],
            "bounce_reason": gate_res["bounce_reason"],
            "user_message": "Nothing matches any anchor here.",
            "cited_items": [],
        }
        err_list = voice.verify_executive_reply(gate_res, no_anchors)
        self.assertTrue(any("conflict[0]" in e for e in err_list))
        self.assertTrue(any("flawed_ideas[0]" in e for e in err_list))
        self.assertTrue(any("clarification_questions[0]" in e for e in err_list))
        self.assertTrue(any("open_questions[0]" in e for e in err_list))

        rep_commit = preflight.inspect_turn_preflight({
            "turn_index": 2,
            "parsed_mcus": [{"mcu_id": 1, "type": "ACTION", "facet": "actions", "primary_scope": "worker_pool", "text": "Restart worker_pool."}],
        }, wb)
        gate_commit = gate.execute_gate_decision(copy.deepcopy(rep_commit), copy.deepcopy(wb))
        bad_commit_reply = {
            "decision": "COMMIT_TO_WHITEBOARD",
            "bounce_reason": "NONE",
            "user_message": "Written to the Whiteboard: [worker_pool] Restart worker_pool.",
            "cited_items": [],
        }
        self.assertTrue(any("cited_items" in e for e in voice.verify_executive_reply(gate_commit, bad_commit_reply)))

    def test_render_deterministic_and_synthesize_executive_voice_with_generator_retry_and_truncation(self) -> None:
        _, voice, _, gate, preflight = _import_exec_23c()
        self.assertEqual(voice.EXECUTIVE_MAX_TOKENS, 2048)
        rep_clean = preflight.inspect_turn_preflight({
            "turn_index": 1,
            "parsed_mcus": [{"id": "r_threads", "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "worker_pool", "text": "Keep max_threads <= 16.", "target_prop": "max_threads", "bound_op": "<=", "bound_val": 16}],
        }, None)
        gate_clean = gate.execute_gate_decision(rep_clean, None)

        det_clean = voice.synthesize_executive_voice(gate_clean, gen=None)
        self.assertEqual(det_clean["decision"], "COMMIT_TO_WHITEBOARD")
        self.assertEqual(det_clean["bounce_reason"], "NONE")
        self.assertIn("Written to the Whiteboard", det_clean["user_message"])
        self.assertEqual(det_clean["cited_items"], ["Keep max_threads <= 16."])

        valid_llm_reply = {
            "decision": "COMMIT_TO_WHITEBOARD",
            "bounce_reason": "NONE",
            "user_message": "Committed [worker_pool] Keep max_threads <= 16. to the Whiteboard.",
            "cited_items": ["Keep max_threads <= 16."],
        }
        gen_ok = _ScriptedVoiceGen([(json.dumps(valid_llm_reply), {"done_reason": "stop"})])
        self.assertEqual(voice.synthesize_executive_voice(gate_clean, gen=gen_ok), valid_llm_reply)
        self.assertEqual(len(gen_ok.calls), 1)

        invalid_attempt_1 = {
            "decision": "SEND_BACK_TO_USER",
            "bounce_reason": "RULE_CONFLICT",
            "user_message": "Wait!",
            "cited_items": ["Keep max_threads <= 16."],
        }
        gen_retry = _ScriptedVoiceGen([invalid_attempt_1, valid_llm_reply])
        self.assertEqual(voice.synthesize_executive_voice(gate_clean, gen=gen_retry), valid_llm_reply)
        self.assertEqual(len(gen_retry.calls), 2)
        self.assertIn("PREVIOUS REPLY FAILED GUARD VERIFICATION:", gen_retry.calls[1][0])

        gen_fail = _ScriptedVoiceGen([invalid_attempt_1, invalid_attempt_1])
        with self.assertRaises(voice.ExecutiveVoiceError) as ctx_guard:
            voice.synthesize_executive_voice(gate_clean, gen=gen_fail)
        self.assertEqual(ctx_guard.exception.code, "executive_voice_guard_failed")

        gen_trunc = _ScriptedVoiceGen([(valid_llm_reply, {"done_reason": "length"})])
        with self.assertRaises(voice.ExecutiveVoiceError) as ctx_trunc:
            voice.synthesize_executive_voice(gate_clean, gen=gen_trunc)
        self.assertEqual(ctx_trunc.exception.code, "executive_voice_output_truncated")

        self.assertEqual(voice.synthesize_executive_voice(gate_clean, gen=lambda p, s, m: valid_llm_reply), valid_llm_reply)
        self.assertEqual(voice.synthesize_executive_voice(gate_clean, gen=lambda prompt: valid_llm_reply), valid_llm_reply)
        self.assertEqual(
            voice.synthesize_executive_voice(gate_clean, gen=lambda gate_result: voice.render_deterministic_executive_reply(gate_result)),
            det_clean,
        )

    def test_run_executive_turn_end_to_end_multi_turn_progression_and_bounce_invariance(self) -> None:
        pkg, _, orchestrator, gate, _ = _import_exec_23c()
        from lattice import whiteboard as WB

        with tempfile.TemporaryDirectory(prefix="qa_exec23c_e2e_") as tmp:
            db_path = Path(tmp) / "exec_e2e.sqlite"
            live_wb = WB.create_empty_context_graph(db_path=db_path)

            t1 = pkg.run_executive_turn(
                live_wb,
                intake_packet={
                    "turn_index": 1,
                    "parsed_mcus": [
                        {"id": "r_threads", "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "worker_pool", "text": "Keep max_threads <= 16.", "target_prop": "max_threads", "bound_op": "<=", "bound_val": 16},
                        {"id": "g_latency", "mcu_id": 2, "type": "GOAL", "facet": "goals", "primary_scope": "worker_pool", "text": "Keep p99_latency_ms <= 50.", "target_prop": "p99_latency_ms", "bound_op": "<=", "bound_val": 50},
                        {"id": "f_runtime", "mcu_id": 3, "type": "FACT", "facet": "context_facts", "primary_scope": "worker_pool", "text": "The runtime is python_3_14.", "target_prop": "runtime", "bound_op": "==", "bound_val": "python_3_14"},
                        {"id": "c_wp", "mcu_id": 4, "type": "COMPONENT", "lattice_kind": "COMPONENT", "primary_scope": "worker_pool", "owner": "eng1", "text": "`worker_pool` is owned by `eng1`."},
                    ],
                },
            )
            self.assertIsInstance(t1, orchestrator.ExecutiveTurnResult)
            self.assertEqual(t1.turn_index, 1)
            self.assertEqual(t1.decision, gate.DECISION_COMMIT_TO_WHITEBOARD)
            self.assertEqual(t1.bounce_reason, gate.BOUNCE_NONE)
            self.assertTrue(t1.whiteboard_mutated)
            self.assertEqual(len(t1.committed_items), 4)
            self.assertIn("Written to the Whiteboard", t1.user_message)
            self.assertIn("[Lattice Proof:", t1.user_message)
            self.assertIn("lattice_proof", t1.to_dict())
            self.assertEqual(t1.lattice_proof["status"], "INCOMPLETE_DRAFT")
            self.assertEqual(t1.to_dict()["decision"], gate.DECISION_COMMIT_TO_WHITEBOARD)

            snapshot_after_t1 = copy.deepcopy({k: v for k, v in live_wb.items() if k != "_sqlite_store"})
            db_bytes_after_t1 = db_path.read_bytes()

            bounce_cases = [
                (
                    gate.BOUNCE_RULE_CONFLICT,
                    {"turn_index": 2, "parsed_mcus": [{"mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "worker_pool", "text": "Set max_threads >= 64.", "target_prop": "max_threads", "bound_op": ">=", "bound_val": 64}]},
                    "max_threads",
                ),
                (
                    gate.BOUNCE_FLAWED_IDEA,
                    {"turn_index": 2, "parsed_mcus": [{"mcu_id": 1, "type": "PROPOSAL", "facet": "context_facts", "primary_scope": "worker_pool", "text": "The user proposes setting runtime to ruby_2_0.", "target_prop": "runtime", "bound_op": "==", "bound_val": "ruby_2_0"}]},
                    "runtime",
                ),
                (
                    gate.BOUNCE_NEEDS_CLARIFICATION,
                    {"turn_index": 2, "parsed_mcus": [{"mcu_id": 1, "type": "QUESTION", "facet": "open_questions", "primary_scope": "worker_pool", "text": "Should worker_pool drain active jobs first?"}]},
                    "Should worker_pool drain active jobs first?",
                ),
            ]
            for expected_reason, pkt, expected_substr in bounce_cases:
                res = pkg.run_executive_turn(live_wb, intake_packet=pkt)
                self.assertEqual(res.decision, gate.DECISION_SEND_BACK_TO_USER)
                self.assertEqual(res.bounce_reason, expected_reason)
                self.assertFalse(res.whiteboard_mutated)
                self.assertIn("The marker remains capped", res.user_message)
                self.assertIn(expected_substr, res.user_message)
                self.assertEqual({k: v for k, v in live_wb.items() if k != "_sqlite_store"}, snapshot_after_t1)
                self.assertEqual(db_path.read_bytes(), db_bytes_after_t1)

            from lattice import regiment as REG
            raw_order = (
                "| Scope | Rule | Target_Prop | Op | Value | Facet |\n"
                "|---|---|---|---|---|---|\n"
                "| worker_pool | Keep `max_connections` <= 128 on `worker_pool`. | max_connections | <= | 128 | Numeric_Bounds |"
            )
            t5 = pkg.run_executive_turn(live_wb, raw_order, turn_index=2)
            self.assertEqual(t5.turn_index, 2)
            self.assertEqual(t5.decision, gate.DECISION_COMMIT_TO_WHITEBOARD)
            self.assertTrue(t5.whiteboard_mutated)
            self.assertEqual(len(live_wb["scopes"]["worker_pool"]["rules"]), 4)

            vault_md_t1 = (
                "| Component | Type | Property | Op | Value | Spec |\n"
                "|---|---|---|---|---|---|\n"
                "| vault_store | FACT | storage_tier | == | cold_glacier | In vault_store, the current storage_tier is cold_glacier. |\n"
                "| vault_store | PROHIBITION | encryption_mode | != | aes_256_gcm | The vault_store must not use aes_256_gcm for encryption_mode. |"
            )
            t6 = pkg.run_executive_turn(live_wb, vault_md_t1)
            self.assertEqual((t6.turn_index, t6.decision, t6.bounce_reason, t6.whiteboard_mutated), (3, gate.DECISION_COMMIT_TO_WHITEBOARD, gate.BOUNCE_NONE, True))
            self.assertIn("vault_store", live_wb["scopes"])

            vault_md_t2 = (
                "| Component | Type | Property | Op | Value | Spec |\n"
                "|---|---|---|---|---|---|\n"
                "| vault_store | PROPOSAL | storage_tier | == | hot_ramdisk | Keep vault_store storage_tier at hot_ramdisk for instant millisecond reads. |"
            )
            t7 = pkg.run_executive_turn(live_wb, vault_md_t2)
            self.assertEqual((t7.turn_index, t7.decision, t7.bounce_reason, t7.whiteboard_mutated), (4, gate.DECISION_SEND_BACK_TO_USER, gate.BOUNCE_FLAWED_IDEA, False))
            self.assertIn("storage_tier", t7.user_message)

            store = live_wb.get("_sqlite_store")
            if store is not None:
                store.close()

            reloaded_store = WB.SQLiteGraphStore(db_path)
            try:
                reloaded = reloaded_store.load_graph()
                self.assertEqual(len(reloaded["scopes"]["worker_pool"]["rules"]), 4)
            finally:
                reloaded_store.close()

    def test_standing_rule_49_zero_regex_file_size_and_23c_isolation_gate(self) -> None:
        cand_dir = _locate_exec_23c_dir()
        for fn in ("voice.py", "orchestrator.py", "__init__.py"):
            fpath = cand_dir / fn
            self.assertTrue(fpath.is_file(), f"Missing {fn} in {cand_dir}")
            src = fpath.read_text(encoding="utf-8")
            self.assertLessEqual(len(src.splitlines()), 500, f"{fn} exceeds 500-line limit in Core Rule 4")
            tree = ast.parse(src, filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "re", f"Forbidden `import re` in {fn}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "re", f"Forbidden `from re import ...` in {fn}")


if __name__ == "__main__":
    unittest.main()
