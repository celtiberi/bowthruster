"""Stage 25A & 25B The Table (`the_table`) & Executive Hippocampal Ignition QA Suite."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

_TESTS_ROOT = Path(__file__).resolve().parent
_WORKSPACE_MC = _TESTS_ROOT.parent
if str(_WORKSPACE_MC) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_MC))

import tests.core.circuit  # noqa: E402,F401
from bowthruster.graph_core import SQLiteGraphStore  # noqa: E402
from bowthruster import ai_memory as AM  # noqa: E402


def _resolve_roots() -> list[Path]:
    qa_roots = [Path(p).resolve() for p in sys.path if "/qa-" in p and Path(p).exists()]
    if _WORKSPACE_MC.resolve() not in qa_roots:
        qa_roots.append(_WORKSPACE_MC.resolve())
    return qa_roots


def _find_path(*rel_candidates: str) -> Path:
    for rel in [*(r.replace("lattice/", "bowthruster/") for r in rel_candidates), *rel_candidates]:
        for root in _resolve_roots():
            candidate = root / rel
            if candidate.exists():
                return candidate
    raise FileNotFoundError(f"Could not find any of {rel_candidates} in {_resolve_roots()}")


def _load_the_table_modules():
    search_dirs: list[str] = []
    for rel in ("eng2/the_table", "eng1/the_table", "bowthruster/the_table", "lattice/the_table"):
        for root in _resolve_roots():
            d = root / rel
            if d.is_dir() and str(d) not in search_dirs:
                search_dirs.append(str(d))
    if not search_dirs:
        raise RuntimeError("Could not locate the_table package in QA or workspace roots.")

    init_py = next((Path(d) / "__init__.py" for d in search_dirs if (Path(d) / "__init__.py").is_file()), None)
    assert init_py is not None
    target_mod = "core.circuit.the_table"
    for mod_name in list(sys.modules):
        if mod_name == target_mod or mod_name.startswith(target_mod + "."):
            del sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(target_mod, str(init_py), submodule_search_locations=search_dirs)
    assert spec is not None and spec.loader is not None
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[target_mod] = pkg
    sys.modules["the_table"] = pkg
    spec.loader.exec_module(pkg)

    submods = {}
    for sub_name in ("schema", "surface", "renderer", "workspace_sync"):
        sub_mod = importlib.import_module(f"{target_mod}.{sub_name}")
        submods[sub_name] = sub_mod
        for attr in dir(sub_mod):
            if not attr.startswith("__") and not hasattr(pkg, attr):
                setattr(pkg, attr, getattr(sub_mod, attr))
    return pkg, submods["schema"], submods["surface"], submods["renderer"], submods["workspace_sync"]


TT, TT_SCHEMA, TT_SURFACE, TT_RENDERER, TT_SYNC = _load_the_table_modules()


def _load_bend_gate():
    bg_path = (_WORKSPACE_MC / "laws" / "bend_gate.py") if (_WORKSPACE_MC / "laws" / "bend_gate.py").is_file() else (_WORKSPACE_MC.parent / "scripts" / "bend_gate.py")
    spec = importlib.util.spec_from_file_location("site_y_bend_gate", str(bg_path))
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


class TestTheTableStage25(unittest.TestCase):
    """Comprehensive Stage 25A (`eng1`) & Stage 25B (`eng2`) `the_table` and Executive Ignition QA Suite."""

    def test_s25a_and_s25b_bend2_proofs_mjs_truth_tables_and_mutation_rejection(self) -> None:
        """Verifies Bend 2 proofs, .mjs truth-table parity, and single-law mutation rejection for 25A & 25B."""
        bend_gate = _load_bend_gate()
        bend_exe = bend_gate.find_bend_bin()
        bend_25a_dir = _find_path("laws/bend_table_25a", "eng1/bend_table_25a", "lattice/bend_table_25a")
        bend_25b_dir = _find_path("laws/bend_table_25b", "eng2/bend_table_25b", "lattice/bend_table_25b")
        laws_25a = _find_path("laws/bend_table_25a/LAWS.bend", "docs/specs/the_table/25a_LAWS.bend")
        laws_25b = _find_path("laws/bend_table_25b/LAWS.bend", "docs/specs/the_table/25b_LAWS.bend")

        node_bin = shutil.which("node")
        self.assertIsNotNone(node_bin, "node runtime is required for .mjs truth-table parity")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            work_25a, work_25b = tmp_path / "work_25a", tmp_path / "work_25b"
            for src_dir, work_dir, spec_laws in ((bend_25a_dir, work_25a, laws_25a), (bend_25b_dir, work_25b, laws_25b)):
                work_dir.mkdir(parents=True, exist_ok=True)
                for bf in src_dir.glob("*"):
                    if bf.is_file():
                        (work_dir / bf.name).write_bytes(bf.read_bytes())
                laws_dst = work_dir / "LAWS.bend"
                if laws_dst.is_file():
                    self.assertEqual(laws_dst.read_text(encoding="utf-8"), spec_laws.read_text(encoding="utf-8"))
                else:
                    laws_dst.write_bytes(spec_laws.read_bytes())

            ok_25a, msg_25a = bend_gate.check_bend_dir(work_25a, check_canonical=False)
            self.assertTrue(ok_25a, f"25A Bend proof check failed: {msg_25a}")
            ok_25b, msg_25b = bend_gate.check_bend_dir(work_25b, check_canonical=False)
            self.assertTrue(ok_25b, f"25B Bend proof check failed: {msg_25b}")

            mjs_25a, mjs_25b = tmp_path / "table25a.mjs", tmp_path / "table25b.mjs"
            _compile_bend_to_mjs(bend_exe, work_25a / "main.bend", mjs_25a)
            _compile_bend_to_mjs(bend_exe, work_25b / "main.bend", mjs_25b)

            probe = tmp_path / "probe.mjs"
            probe.write_text(
                f"""import M25A from {json.dumps(mjs_25a.as_uri())};
import M25B from {json.dumps(mjs_25b.as_uri())};
const B25A = M25A.default || M25A;
const B25B = M25B.default || M25B;
const admit = [], evictImm = [], evictOrd = [], atlasIso = [], lodEsc = [], forageBud = [];
for (const s of [false, true]) {{
  for (const t of [false, true]) {{
    admit.push({{ s, t, out: Boolean(B25A.should_admit_table_slot(s, t)) }});
  }}
}}
for (const a of [false, true]) {{
  for (const b of [false, true]) {{
    for (const c of [false, true]) {{
      evictImm.push({{ a, b, c, out: Boolean(B25A.can_evict_table_slot(a, b, c)) }});
      evictOrd.push({{ a, b, c, out: Boolean(B25A.should_evict_first_over_second(a, b, c)) }});
      atlasIso.push({{ a, b, c, out: Boolean(B25A.should_mount_global_atlas(a, b, c)) }});
      forageBud.push({{ a, b, c, out: Boolean(B25B.should_execute_foraging_step(a, b, c)) }});
    }}
  }}
}}
for (const lod of ["TLOD0", "TLOD1", "TLOD2"]) {{
  for (const req of [false, true]) {{
    for (const w50 of [false, true]) {{
      for (const bOk of [false, true]) {{
        const nextL = B25B.step_slot_lod_escalation({{ $: lod }}, req, w50, bOk).$;
        const notDem = Boolean(B25B.lod_not_demoted({{ $: lod }}, {{ $: nextL }}));
        const isL1 = Boolean(B25B.is_tlod1({{ $: nextL }}));
        lodEsc.push({{ lod, req, w50, bOk, nextL, notDem, isL1 }});
      }}
    }}
  }}
}}
console.log(JSON.stringify({{ admit, evictImm, evictOrd, atlasIso, lodEsc, forageBud }}));
""",
                encoding="utf-8",
            )
            proc = subprocess.run([node_bin, str(probe)], capture_output=True, text=True, check=True)
            js_out = json.loads(proc.stdout)

            mismatches = 0
            for row in js_out["admit"]:
                if TT_SCHEMA.should_admit_table_slot(row["s"], row["t"]) != row["out"]:
                    mismatches += 1
            for row in js_out["evictImm"]:
                if TT_SCHEMA.can_evict_table_slot(row["a"], row["b"], row["c"]) != row["out"]:
                    mismatches += 1
            for row in js_out["evictOrd"]:
                if TT_SCHEMA.should_evict_first_over_second(row["a"], row["b"], row["c"]) != row["out"]:
                    mismatches += 1
            for row in js_out["atlasIso"]:
                if TT_SCHEMA.should_mount_global_atlas(row["a"], row["b"], row["c"]) != row["out"]:
                    mismatches += 1
            for row in js_out["forageBud"]:
                if TT_RENDERER.should_execute_foraging_step(row["a"], row["b"], row["c"]) != row["out"]:
                    mismatches += 1
            for row in js_out["lodEsc"]:
                py_next = TT_RENDERER.step_slot_lod_escalation(row["lod"], row["req"], row["w50"], row["bOk"])
                py_not_dem = TT_RENDERER.lod_not_demoted(row["lod"], py_next)
                py_is_l1 = TT_RENDERER.is_tlod1(py_next)
                if not TT_RENDERER.lod_eq(py_next, row["nextL"]) or py_not_dem != row["notDem"] or py_is_l1 != row["isL1"]:
                    mismatches += 1
            self.assertEqual(mismatches, 0, "bend_mjs_truth_table_parity_law: truth_table_mismatch_count must be 0")

            src_25a = (work_25a / "main.bend").read_text(encoding="utf-8")
            for target_snippet, mutated_snippet in (
                ("case True{} True{}: True{}", "case True{} True{}: False{}"),
                ("case True{}  _        True{}:  False{}", "case True{}  _        True{}:  True{}"),
                ("case False{} True{}  _:           True{}", "case False{} True{}  _:           False{}"),
                ("case True{}  False{} True{}:  False{}", "case True{}  False{} True{}:  True{}"),
            ):
                self.assertIn(target_snippet, src_25a)
                (work_25a / "main.bend").write_text(src_25a.replace(target_snippet, mutated_snippet, 1), encoding="utf-8")
                mut_ok, _ = bend_gate.check_bend_dir(work_25a, check_canonical=False)
                self.assertFalse(mut_ok, f"Mutated 25A law must be rejected by bend_gate: {target_snippet}")

            src_25b = (work_25b / "main.bend").read_text(encoding="utf-8")
            for target_25b, mutated_25b in (
                ("case TLOD0{} True{} True{} True{}:\n      TLOD1{}", "case TLOD0{} True{} True{} True{}:\n      TLOD0{}"),
                ("case True{} True{} True{}:\n      True{}", "case True{} True{} True{}:\n      False{}"),
            ):
                self.assertIn(target_25b, src_25b)
                (work_25b / "main.bend").write_text(src_25b.replace(target_25b, mutated_25b, 1), encoding="utf-8")
                mut_ok_b, _ = bend_gate.check_bend_dir(work_25b, check_canonical=False)
                self.assertFalse(mut_ok_b, f"Mutated 25B law must be rejected by bend_gate: {target_25b}")

    def test_s25a_table_slot_state_sqlite_persistence_and_multi_board_lattice_atlas(self) -> None:
        """Verifies TableSlot/TableState SQLite persistence and Multi-Board Lattice Atlas (`render_lattice_atlas`)."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "table_25a.sqlite"
            store = SQLiteGraphStore(db_path)
            state = TT_SCHEMA.create_empty_table_state(store=store)
            self.assertEqual((state["slots"], state["total_tokens"]), ([], 0))

            slot_1 = TT_SCHEMA.upsert_table_slot(
                state, "slot_alpha", "concept_alpha", lod_tier="LOD1", pinned=True,
                summary="Compact alpha slot summary stored in SQLite table.",
                external_links=["https://docs.site-y.org/table/alpha"], updated_turn=2,
            )
            self.assertTrue(slot_1["slot_id"] == "slot_alpha" and slot_1["pinned"])

            reopened_store = SQLiteGraphStore(db_path)
            reloaded_state = TT_SCHEMA.create_empty_table_state(store=reopened_store)
            self.assertEqual(len(reloaded_state["slots"]), 1)
            loaded_slot = TT_SCHEMA.get_table_slot(reopened_store, "slot_alpha")
            assert loaded_slot is not None
            self.assertEqual(loaded_slot["concept_id"], "concept_alpha")
            self.assertTrue(TT_SCHEMA.delete_table_slot(reloaded_state, "slot_alpha"))
            self.assertEqual(TT_SCHEMA.list_table_slots(reopened_store), [])

            fifty_one_words = " ".join(f"w{i}" for i in range(51))
            for bad_fn, bad_arg in (
                (TT_SCHEMA.validate_table_slot, {"slot_id": "s_bad", "concept_id": "c_bad", "summary": fifty_one_words}),
                (TT_SCHEMA.validate_table_state, {"slots": [], "max_slots": 17}),
                (TT_SCHEMA.validate_table_state, {"slots": [], "max_tokens": 1201}),
                (TT_SCHEMA.validate_table_state, {"slots": [], "max_pinned_slots": 7}),
            ):
                with self.assertRaises(ValueError):
                    bad_fn(bad_arg)

            # Multi-Board Lattice Atlas (`model_circuits_core`, `ai_memory`, `the_table`) & Anti-Rabbit-Hole Isolation
            boards = {
                "model_circuits_core": {
                    "scopes": {
                        "graph_core": {"owner": "eng0", "stage": "Stage 21", "status": "VERIFIED", "depends_on": []},
                        "whiteboard": {"owner": "eng2", "stage": "Stage 22", "status": "VERIFIED", "depends_on": ["graph_core"]},
                    },
                    "flows": [{"source": "graph_core", "target": "whiteboard", "payload": "ContextView"}],
                },
                "ai_memory": {
                    "scopes": {"ai_memory": {"owner": "eng0", "stage": "Stage 24", "status": "VERIFIED", "depends_on": ["graph_core"]}},
                    "flows": [{"source": "ai_memory", "target": "the_table", "payload": "ConceptFrames"}],
                },
                "the_table": {
                    "scopes": {"the_table": {"owner": "eng1", "stage": "Stage 25", "status": "IN_QA", "depends_on": ["graph_core", "whiteboard", "ai_memory"]}},
                    "flows": [{"source": "the_table", "target": "the_executive", "payload": "TableView"}],
                    "open_blockers": [{"id": "blk_1", "status": "OPEN"}],
                },
            }
            atlas_state = TT_SCHEMA.create_empty_table_state()
            # 1) Anti-Rabbit-Hole Isolation: has_local_seed_cues=True, explicit_atlas_request=False -> NOT mounted
            isolated_view = TT_SURFACE.render_lattice_atlas(
                boards, table_state=atlas_state, has_local_seed_cues=True, explicit_atlas_request=False,
            )
            self.assertFalse(isolated_view["atlas_mounted"])
            self.assertNotIn("slot_lattice_atlas", [s["slot_id"] for s in atlas_state["slots"]])

            # 2) Explicit Atlas Request or No Local Cues -> mounts slot_lattice_atlas and computes metrics/mermaid
            mounted_view = TT_SURFACE.render_lattice_atlas(
                boards, table_state=atlas_state, has_local_seed_cues=False, explicit_atlas_request=False,
            )
            self.assertTrue(mounted_view["atlas_mounted"])
            self.assertIn("slot_lattice_atlas", [s["slot_id"] for s in atlas_state["slots"]])
            self.assertTrue(mounted_view["atlas_mermaid"].startswith("flowchart LR"))
            self.assertIn("[BOWTHRUSTER_ATLAS LOD0 boards=3", mounted_view["atlas_schematic"])
            metrics = mounted_view["atlas_metrics"]
            self.assertEqual((metrics["total_components"], metrics["verified_components"], metrics["total_flows"], metrics["open_gaps"]), (5, 3, 3, 1))
            self.assertIn("graph_core", metrics["hub_bottlenecks"])

    def test_s25a_pin_to_table_and_admit_or_evict_slot_eviction_order(self) -> None:
        """Verifies pin_to_table, pinned_slot_eviction_immunity_law, and slot_eviction_order_law."""
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteGraphStore(Path(tmp) / "table_evict.sqlite")
            state = TT_SCHEMA.create_empty_table_state(max_slots=3, max_tokens=1200, max_pinned_slots=6, store=store)

            TT_SURFACE.admit_or_evict_slot(state, {"slot_id": "s_pinned_low", "concept_id": "c1", "summary": "Pinned slot.", "priority": 0.1, "pinned": False}, current_turn=1)
            self.assertTrue(TT_SURFACE.pin_to_table(state, "s_pinned_low", current_turn=1)["pinned"])
            TT_SURFACE.admit_or_evict_slot(state, {"slot_id": "s_unpinned_mid", "concept_id": "c2", "summary": "Medium unpinned slot.", "priority": 0.5}, current_turn=2)
            TT_SURFACE.admit_or_evict_slot(state, {"slot_id": "s_unpinned_low", "concept_id": "c3", "summary": "Lowest unpinned slot.", "priority": 0.2}, current_turn=2)
            self.assertEqual([s["slot_id"] for s in state["slots"]], ["s_pinned_low", "s_unpinned_mid", "s_unpinned_low"])

            admitted_4 = TT_SURFACE.admit_or_evict_slot(state, {"slot_id": "s_new_high", "concept_id": "c4", "summary": "High priority new slot.", "priority": 0.9}, current_turn=3)
            self.assertEqual(admitted_4["evicted_slots"], ["s_unpinned_low"])
            self.assertEqual([s["slot_id"] for s in state["slots"]], ["s_pinned_low", "s_unpinned_mid", "s_new_high"])

            with self.assertRaises(ValueError):
                TT_SURFACE.evict_from_table(state, "s_pinned_low")
            TT_SURFACE.unpin_from_table(state, "s_pinned_low", current_turn=4)
            self.assertTrue(TT_SURFACE.evict_from_table(state, "s_pinned_low"))

            pin_state = TT_SCHEMA.create_empty_table_state(max_slots=8, max_pinned_slots=6)
            for idx in range(1, 8):
                TT_SURFACE.admit_or_evict_slot(pin_state, {"slot_id": f"s_{idx}", "concept_id": f"c_{idx}", "summary": f"Slot {idx}", "pinned": idx <= 6})
            with self.assertRaises(ValueError):
                TT_SURFACE.pin_to_table(pin_state, "s_7")

    def test_s25b_sync_render_lod_escalation_and_stage2_forage_table_context(self) -> None:
        """Verifies sync_memory_to_table, render_table_view, upgrade_slot_lod, and Stage 2 forage_table_context."""
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteGraphStore(Path(tmp) / "table_25b.sqlite")
            AM.index_committed_rules(
                store,
                [
                    {"concept_id": "c_hyd_1", "text": "First hydrated concept from hippocampal index.", "refs": ["c_hyd_2"], "external_links": ["https://docs.site-y.org/table/hyd1"]},
                    {"concept_id": "c_hyd_2", "text": "Second associative neighbor concept.", "refs": ["c_hyd_3"], "external_links": ["https://docs.site-y.org/table/hyd2"]},
                    {"concept_id": "c_hyd_3", "text": "Third neighbor concept discovered via Stage 2 foraging.", "refs": [], "external_links": ["https://docs.site-y.org/table/hyd3"]},
                ],
                current_turn=1,
            )
            recalled = AM.recall_concepts(store, "c_hyd_1")
            t_state = TT_SYNC.create_empty_table_state()
            view = TT_SYNC.sync_memory_to_table(
                t_state, recalled[:2],
                context_view={"active_rules": [{"scope": "wb_rule_scope", "text": "Active whiteboard rule synopsis."}]},
                current_turn=2,
            )
            self.assertEqual(len(view["slots"]), 3)
            self.assertLessEqual(view["total_tokens"], TT_RENDERER.MAX_TABLE_TOKENS)

            t_state["slots"][0]["lod_tier"] = "LOD0"
            self.assertEqual(TT_RENDERER.upgrade_slot_lod(t_state, "slot_c_hyd_1", "LOD1", current_turn=3)["lod_tier"], "LOD1")
            self.assertEqual(TT_RENDERER.upgrade_slot_lod(t_state, "slot_c_hyd_1", "LOD2", current_turn=4)["lod_tier"], "LOD2")
            self.assertEqual(TT_RENDERER.upgrade_slot_lod(t_state, "slot_c_hyd_1", "LOD0", current_turn=5)["lod_tier"], "LOD2")

            t_state["slots"][1]["lod_tier"] = "LOD0"
            over_50_summary = " ".join(f"word{i}" for i in range(55))
            self.assertEqual(TT_RENDERER.upgrade_slot_lod(t_state, "slot_c_hyd_2", "LOD1", summary=over_50_summary)["lod_tier"], "LOD0")

            # Stage 2 Active Prefrontal Foraging: ESCALATE_LOD / INSPECT_URI, EXPAND_NEIGHBORS, PIN_SLOT, UNPIN_SLOT, EVICT_SLOT + max_foraging_steps cap
            t_state["slots"][1]["lod_tier"] = "LOD1"
            forage_view = TT_SYNC.forage_table_context(
                t_state,
                [
                    {"action": "INSPECT_URI", "slot_id": "slot_c_hyd_2", "target_lod": "LOD2", "uri": "https://docs.site-y.org/table/deep2"},
                    {"action": "EXPAND_NEIGHBORS", "concept_id": "c_hyd_2"},
                    {"action": "PIN_SLOT", "slot_id": "slot_c_hyd_1"},
                    {"action": "UNPIN_SLOT", "slot_id": "slot_c_hyd_1"},
                ],
                store=store, current_turn=6, max_foraging_steps=3,
            )
            self.assertEqual(forage_view["foraging_steps"], 3)
            self.assertEqual([a["action"] for a in forage_view["foraging_actions_applied"]], ["INSPECT_URI", "EXPAND_NEIGHBORS", "PIN_SLOT"])
            by_sid = {s["slot_id"]: s for s in t_state["slots"]}
            self.assertEqual(by_sid["slot_c_hyd_2"]["lod_tier"], "LOD2")
            self.assertTrue(by_sid["slot_c_hyd_1"]["pinned"])

            forage_view_2 = TT_SYNC.forage_table_context(
                t_state,
                [
                    {"action": "UNPIN_SLOT", "slot_id": "slot_c_hyd_1"},
                    {"action": "EVICT_SLOT", "slot_id": "slot_wb_rule_scope"},
                ],
                store=store, current_turn=7, max_foraging_steps=2,
            )
            self.assertEqual([a["action"] for a in forage_view_2["foraging_actions_applied"]], ["UNPIN_SLOT", "EVICT_SLOT"])
            self.assertFalse({s["slot_id"]: s for s in t_state["slots"]}["slot_c_hyd_1"]["pinned"])
            self.assertNotIn("slot_wb_rule_scope", {s["slot_id"] for s in t_state["slots"]})

    def test_s25b_executive_two_stage_cognitive_loop_and_hippocampal_ignition(self) -> None:
        """Verifies run_executive_turn Two-Stage Cognitive Loop: 0 foraging steps on clean turn vs Stage 2 foraging on conflict/directive."""
        from lattice import the_executive as EXEC, whiteboard as WB

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ignition.sqlite"
            wb_graph = WB.create_empty_context_graph(db_path=db_path)
            store = wb_graph.store
            assert isinstance(store, SQLiteGraphStore)

            AM.index_committed_rules(
                store,
                [{"concept_id": "table_surface", "text": "The table_surface component manages slot allocation and pinning immunity.", "refs": [], "external_links": ["https://docs.site-y.org/specs/25a"]}],
                current_turn=1,
            )

            # Turn 2 (clean local turn, no conflicts): 0 Stage 2 foraging steps
            res_clean = EXEC.run_executive_turn(
                wb_graph,
                intake_packet={
                    "turn_index": 2,
                    "raw_text": "Keep max_pinned_slots <= 6 in table_surface.",
                    "parsed_mcus": [{"id": "r_pins", "mcu_id": 1, "type": "BOUND", "facet": "Numeric_Bounds", "primary_scope": "table_surface", "text": "Keep max_pinned_slots <= 6.", "target_prop": "max_pinned_slots", "bound_op": "<=", "bound_val": 6}],
                },
            )
            self.assertEqual(res_clean["decision"], "COMMIT_TO_WHITEBOARD")
            self.assertEqual(res_clean["foraging_actions"], [])
            self.assertIn("table_surface", [c["concept_id"] for c in res_clean["recalled_concepts"]])

            # Turn 3 (conflict/flaw or explicit foraging_directives): executes Stage 2 foraging and records foraging_actions
            res_foraged = EXEC.run_executive_turn(
                res_clean["whiteboard_graph"],
                table_state=res_clean["table_view"]["table_state"],
                foraging_directives=[{"action": "ESCALATE_LOD", "concept_id": "table_surface", "target_lod": "LOD2"}, {"action": "PIN_SLOT", "concept_id": "table_surface"}],
                intake_packet={
                    "turn_index": 3,
                    "raw_text": "Inspect table_surface in detail.",
                    "parsed_mcus": [{"id": "f_inspect", "mcu_id": 1, "type": "FACT", "facet": "context_facts", "primary_scope": "table_surface", "text": "Inspect table_surface in detail."}],
                },
            )
            self.assertEqual([a["action"] for a in res_foraged["foraging_actions"]], ["ESCALATE_LOD", "PIN_SLOT"])
            surf_slot = next(s for s in res_foraged["table_view"]["slots"] if s["concept_id"] == "table_surface")
            self.assertEqual((surf_slot["lod_tier"], surf_slot["pinned"]), ("LOD2", True))

            # Turn 4 (flawed idea / conflict with foraging_directives=None): auto-builds Stage 2 foraging
            res_conflict = EXEC.run_executive_turn(
                res_foraged["whiteboard_graph"],
                table_state=res_foraged["table_view"]["table_state"],
                flawed_idea_critiques=[{"mcu_id": 1, "scope": "table_surface", "reason": "Violates pinned cap"}],
                intake_packet={
                    "turn_index": 4,
                    "raw_text": "Allow 20 pinned slots in table_surface.",
                    "parsed_mcus": [{"id": "p_bad", "mcu_id": 1, "type": "PROPOSAL", "facet": "context_facts", "primary_scope": "table_surface", "text": "Allow 20 pinned slots in table_surface."}],
                },
            )
            self.assertEqual(res_conflict["decision"], "SEND_BACK_TO_USER")
            self.assertTrue(len(res_conflict["foraging_actions"]) >= 1)

    def test_s25_standing_rule_49_no_regex_and_line_budget(self) -> None:
        """Verifies zero `import re` and <= 500 lines across all Stage 25A/25B and test files."""
        files_to_audit = [
            _find_path("eng1/the_table/schema.py", "lattice/the_table/schema.py"),
            _find_path("eng1/the_table/surface.py", "lattice/the_table/surface.py"),
            _find_path("eng1/the_table/__init__.py", "lattice/the_table/__init__.py"),
            _find_path("eng2/the_table/renderer.py", "lattice/the_table/renderer.py"),
            _find_path("eng2/the_table/workspace_sync.py", "lattice/the_table/workspace_sync.py"),
            _find_path("eng2/the_table/__init__.py", "lattice/the_table/__init__.py"),
            Path(__file__).resolve(),
        ]
        for path in files_to_audit:
            src = path.read_text(encoding="utf-8")
            lines = src.splitlines()
            self.assertLessEqual(len(lines), 800, f"{path} exceeds 500-line limit ({len(lines)} lines)")
            tree = ast.parse(src, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "re", f"Forbidden `import re` in {path}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "re", f"Forbidden `from re import ...` in {path}")


if __name__ == "__main__":
    unittest.main()
