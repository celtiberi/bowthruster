"""Stage 24A & 24B AI Memory (`ai_memory`) Unit, SQLite, and Bend 2 Proof QA Suite."""

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


def _resolve_roots() -> list[Path]:
    qa_roots = [Path(p).resolve() for p in sys.path if "/qa-" in p and Path(p).exists()]
    if _WORKSPACE_MC.resolve() not in qa_roots:
        qa_roots.append(_WORKSPACE_MC.resolve())
    return qa_roots


def _load_ai_memory_module():
    qa_roots = _resolve_roots()
    for eng_pkg in ("eng3/graph_core", "lattice/graph_core"):
        for root in qa_roots:
            gc_dir = root / eng_pkg
            if (gc_dir / "__init__.py").is_file():
                spec = importlib.util.spec_from_file_location(
                    "core.circuit.graph_core", str(gc_dir / "__init__.py"), submodule_search_locations=[str(gc_dir)]
                )
                if spec and spec.loader and "core.circuit.graph_core" not in sys.modules:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules["core.circuit.graph_core"] = mod
                    spec.loader.exec_module(mod)
                break

    search_dirs: list[str] = []
    for rel in ("eng3/ai_memory", "eng0/ai_memory", "lattice/ai_memory"):
        for root in qa_roots:
            d = root / rel
            if d.is_dir() and str(d) not in search_dirs:
                search_dirs.append(str(d))
    if not search_dirs:
        raise RuntimeError("Could not locate ai_memory package in QA or workspace roots.")

    init_py = next((Path(d) / "__init__.py" for d in search_dirs if (Path(d) / "__init__.py").is_file()), None)
    assert init_py is not None
    target_mod = "core.circuit.ai_memory"
    for mod_name in list(sys.modules):
        if mod_name == target_mod or mod_name.startswith(target_mod + "."):
            del sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(target_mod, str(init_py), submodule_search_locations=search_dirs)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[target_mod] = mod
    sys.modules["ai_memory"] = mod
    spec.loader.exec_module(mod)
    for sub_name in ("schema", "cue_extractor", "uri_resolver", "hippocampal_index"):
        sub_mod = importlib.import_module(f"{target_mod}.{sub_name}")
        for attr in dir(sub_mod):
            if not attr.startswith("__") and not hasattr(mod, attr):
                setattr(mod, attr, getattr(sub_mod, attr))
    return mod


def _load_bend_gate():
    bg_path = (_WORKSPACE_MC / "laws" / "bend_gate.py") if (_WORKSPACE_MC / "laws" / "bend_gate.py").is_file() else (_WORKSPACE_MC.parent / "scripts" / "bend_gate.py")
    spec = importlib.util.spec_from_file_location("site_y_bend_gate", str(bg_path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AM = _load_ai_memory_module()
from lattice.graph_core import SQLiteGraphStore  # noqa: E402


def _find_path(*rel_candidates: str) -> Path:
    for rel in rel_candidates:
        for root in _resolve_roots():
            candidate = root / rel
            if candidate.exists():
                return candidate
    raise FileNotFoundError(f"Could not find any of {rel_candidates} in {_resolve_roots()}")


def _compile_bend_to_mjs(bend_exe: Path, bend_file: Path, out_mjs: Path) -> None:
    subprocess.run(
        [str(bend_exe), str(bend_file), "-o", str(out_mjs)],
        cwd=str(bend_file.parent),
        capture_output=True,
        text=True,
        check=True,
    )


class TestAIMemoryStage24(unittest.TestCase):
    """Comprehensive Stage 24A (`eng0`) & Stage 24B (`eng3`) AI Memory QA Suite."""

    def test_s24a_and_s24b_bend2_proofs_mjs_truth_tables_and_mutation_rejection(self) -> None:
        """Verifies Bend 2 proofs, .mjs truth-table parity, and single-law mutation rejection for 24A & 24B."""
        bend_gate = _load_bend_gate()
        bend_exe = bend_gate.find_bend_bin()
        bend_24a_dir = _find_path("laws/bend_mem_24a", "eng0/bend_mem_24a", "lattice/bend_mem_24a")
        bend_24b_dir = _find_path("laws/bend_mem_24b", "eng3/bend_mem_24b", "lattice/bend_mem_24b")
        laws_24a = _find_path("laws/bend_mem_24a/LAWS.bend", "docs/specs/ai_memory/24a_LAWS.bend")
        laws_24b = _find_path("laws/bend_mem_24b/LAWS.bend", "docs/specs/ai_memory/24b_LAWS.bend")

        node_bin = shutil.which("node")
        self.assertIsNotNone(node_bin, "node runtime is required for .mjs truth-table parity")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            work_24a, work_24b = tmp_path / "work_24a", tmp_path / "work_24b"
            for src_dir, work_dir, spec_laws in ((bend_24a_dir, work_24a, laws_24a), (bend_24b_dir, work_24b, laws_24b)):
                work_dir.mkdir(parents=True, exist_ok=True)
                for bf in src_dir.glob("*"):
                    if bf.is_file():
                        (work_dir / bf.name).write_bytes(bf.read_bytes())
                laws_dst = work_dir / "LAWS.bend"
                if laws_dst.is_file():
                    self.assertEqual(laws_dst.read_text(encoding="utf-8"), spec_laws.read_text(encoding="utf-8"))
                else:
                    laws_dst.write_bytes(spec_laws.read_bytes())

            ok_24a, msg_24a = bend_gate.check_bend_dir(work_24a, check_canonical=False)
            self.assertTrue(ok_24a, f"24A Bend proof check failed: {msg_24a}")
            ok_24b, msg_24b = bend_gate.check_bend_dir(work_24b, check_canonical=False)
            self.assertTrue(ok_24b, f"24B Bend proof check failed: {msg_24b}")

            mjs_24a, mjs_24b = tmp_path / "mem24a.mjs", tmp_path / "mem24b.mjs"
            _compile_bend_to_mjs(bend_exe, work_24a / "main.bend", mjs_24a)
            _compile_bend_to_mjs(bend_exe, work_24b / "main.bend", mjs_24b)

            probe = tmp_path / "probe.mjs"
            probe.write_text(
                f"""import M24A from {json.dumps(mjs_24a.as_uri())};
import M24B from {json.dumps(mjs_24b.as_uri())};
const B24A = M24A.default || M24A;
const B24B = M24B.default || M24B;
const storage = [], cues = [], uriLinks = [], recall = [], propNode = [];
for (const a of [false, true]) {{
  for (const c of [false, true]) {{
    for (const d of [false, true]) {{
      storage.push({{ a, c, d, out: Boolean(B24A.is_valid_concept_storage(a, c, d)) }});
      cues.push({{ a, c, d, out: Boolean(B24A.should_accept_seed_cue(a, c, d)) }});
      propNode.push({{ a, c, d, out: Boolean(B24B.can_propagate_from_node(a, c, d)) }});
    }}
  }}
}}
for (const bucket of ["LBZero", "LB1To7", "LBAtCap8"]) {{
  for (const dup of [false, true]) {{
    const nextB = B24A.step_uri_link_registration({{ $: bucket }}, dup).$;
    const capOk = Boolean(B24A.is_within_8_cap({{ $: nextB }}));
    uriLinks.push({{ bucket, dup, nextB, capOk }});
  }}
}}
for (const hop of ["Hop0Seed", "Hop1Neighbor", "Hop2Second", "HopExceeds2"]) {{
  for (const b400 of [false, true]) {{
    for (const w50 of [false, true]) {{
      const inc = Boolean(B24B.should_include_recalled_concept({{ $: hop }}, b400, w50));
      recall.push({{ hop, b400, w50, inc }});
    }}
  }}
}}
console.log(JSON.stringify({{ storage, cues, uriLinks, recall, propNode }}));
""",
                encoding="utf-8",
            )
            proc = subprocess.run([node_bin, str(probe)], capture_output=True, text=True, check=True)
            js_out = json.loads(proc.stdout)

            mismatches = 0
            for row in js_out["storage"]:
                if AM.is_valid_concept_storage(row["a"], row["c"], row["d"]) != row["out"]:
                    mismatches += 1
            for row in js_out["cues"]:
                if AM.should_accept_seed_cue(row["a"], row["c"], row["d"]) != row["out"]:
                    mismatches += 1
            for row in js_out["uriLinks"]:
                py_next = AM.step_uri_link_registration(row["bucket"], row["dup"])
                if py_next.value != row["nextB"] or AM.is_within_8_cap(py_next) != row["capOk"]:
                    mismatches += 1
            for row in js_out["recall"]:
                if AM.should_include_recalled_concept(row["hop"], row["b400"], row["w50"]) != row["inc"]:
                    mismatches += 1
            for row in js_out["propNode"]:
                if AM.can_propagate_from_node(row["a"], row["c"], row["d"]) != row["out"]:
                    mismatches += 1
            self.assertEqual(mismatches, 0, "bend_mjs_truth_table_parity_law: truth_table_mismatch_count must be 0")

            src_24a = (work_24a / "main.bend").read_text(encoding="utf-8")
            for target_snippet, mutated_snippet in (
                ("Bool.and(words_within_50, links_within_8)", "True{}"),
                ("Bool.and(within_12_cap, Bool.not(is_duplicate_seed))", "True{}"),
                ("case LBZero{} True{}:\n      LBZero{}", "case LBZero{} True{}:\n      LB1To7{}"),
            ):
                self.assertIn(target_snippet, src_24a)
                (work_24a / "main.bend").write_text(src_24a.replace(target_snippet, mutated_snippet, 1), encoding="utf-8")
                mut_ok, _ = bend_gate.check_bend_dir(work_24a, check_canonical=False)
                self.assertFalse(mut_ok, f"Mutated 24A law must be rejected by bend_gate: {target_snippet}")

            src_24b = (work_24b / "main.bend").read_text(encoding="utf-8")
            for target_24b, mutated_24b in (
                ("case HopExceeds2{}:\n      False{}", "case HopExceeds2{}:\n      True{}"),
                ("case True{}:\n      False{}", "case True{}:\n      True{}"),
            ):
                self.assertIn(target_24b, src_24b)
                (work_24b / "main.bend").write_text(src_24b.replace(target_24b, mutated_24b, 1), encoding="utf-8")
                mut_ok_b, _ = bend_gate.check_bend_dir(work_24b, check_canonical=False)
                self.assertFalse(mut_ok_b, f"Mutated 24B law must be rejected by bend_gate: {target_24b}")

    def test_s24a_concept_frame_schema_pointer_only_storage_and_sqlite_roundtrip(self) -> None:
        """Verifies ConceptFrame schema bounds, pointer-only storage law, and real SQLiteGraphStore persistence."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "concept_schema.sqlite"
            store = SQLiteGraphStore(db_path)

            ptr1 = AM.ExternalLinkPointer(
                None, "https://docs.site-y.org/specs/24a#L1-L20", "spec_section", "LOD2", concept_id="concept_pointer_law"
            )
            frame = AM.ConceptFrame(
                "concept_pointer_law",
                "Store only compact LOD 1 synopses and URI pointers without raw documents.",
                [ptr1],
                updated_turn=3,
            )
            validated = AM.validate_concept_frame(frame)
            self.assertEqual(validated.external_uris, ["https://docs.site-y.org/specs/24a#L1-L20"])
            self.assertEqual(validated.source_links, validated.external_uris)

            persisted = AM.upsert_concept_frame(
                store,
                frame,
                related_concepts=["concept_hippocampus", "concept_pointer_law", "concept_hippocampus"],
                aliases=["pointer_law"],
            )
            self.assertEqual(persisted.concept_id, "concept_pointer_law")

            store_reopened = SQLiteGraphStore(db_path)
            loaded = AM.get_concept_frame(store_reopened, "concept_pointer_law")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.lod1_synopsis, "Store only compact LOD 1 synopses and URI pointers without raw documents.")
            self.assertEqual(loaded.external_uris, ["https://docs.site-y.org/specs/24a#L1-L20"])
            self.assertEqual(loaded["refs"], ["concept_hippocampus"])

            AM.upsert_concept_frame(
                store_reopened, "concept_hippocampus", "Bounded two hop associative recall index over concept frames.", [], updated_turn=4
            )
            AM.link_concepts(store_reopened, "concept_pointer_law", "concept_hippocampus", turn=5)

            frames_all = AM.list_concept_frames(store_reopened)
            self.assertEqual({f.concept_id for f in frames_all}, {"concept_hippocampus", "concept_pointer_law"})
            reloaded_b = AM.get_concept_frame(store_reopened, "concept_hippocampus")
            assert reloaded_b is not None
            self.assertIn("concept_pointer_law", reloaded_b.get("refs", []))

            for forbidden_key in AM._FORBIDDEN_RAW_DOC_KEYS:
                with self.assertRaises(ValueError):
                    AM.upsert_concept_frame(
                        store_reopened,
                        {"concept_id": "bad_raw", "lod1_synopsis": "Short synopsis.", "external_links": [], forbidden_key: "Forbidden raw doc"},
                    )
                with self.assertRaises(ValueError):
                    AM.normalize_external_link({"target_uri": "https://example.org/a", forbidden_key: "forbidden"})

            fifty_one_words = " ".join(f"word{i}" for i in range(51))
            with self.assertRaises(ValueError):
                AM.validate_concept_frame(AM.ConceptFrame("too_wordy", fifty_one_words, []))

            nine_links = [f"https://example.org/doc/{i}" for i in range(9)]
            with self.assertRaises(ValueError):
                AM.validate_concept_frame(AM.ConceptFrame("too_many_links", "Compact synopsis.", nine_links))

            with self.assertRaises(ValueError):
                AM.validate_concept_frame(
                    {
                        "concept_id": "mismatch_links",
                        "lod1_synopsis": "Compact synopsis.",
                        "external_links": ["https://example.org/doc/1"],
                        "source_links": ["https://example.org/doc/1"],
                        "external_uris": ["https://example.org/doc/2"],
                    }
                )

    def test_s24a_cue_extractor_determinism_and_12_seed_cap(self) -> None:
        """Verifies deterministic cue extraction, lowercase deduplication, punctuation trimming, and <= 12 cap."""
        raw_query = (
            "  [Hippocampus], ('Pointer-Only'), \"URI_Resolver\"! "
            "hippocampus; pointer-only? Cue04, Cue05, Cue06, Cue07, "
            "Cue08, Cue09, Cue10, Cue11, Cue12, Cue13, Cue14, Cue15  "
        )
        cues_first = AM.extract_concept_cues(raw_query)
        cues_second = AM.extract_concept_cues(raw_query)
        self.assertEqual(cues_first, cues_second)
        self.assertEqual(len(cues_first), 12)
        self.assertEqual(cues_first[:3], ["hippocampus", "pointer-only", "uri_resolver"])
        self.assertNotIn("cue13", cues_first)

        self.assertEqual(AM.extract_concept_cues(None), [])
        self.assertEqual(AM.extract_concept_cues("   ... !!!   "), [])
        self.assertEqual(AM.extract_concept_cues(["Alpha Beta", "beta, GAMMA!", "  "]), ["alpha", "beta", "gamma"])
        self.assertEqual(AM.extract_concept_cues({"zeta", "alpha"}), ["alpha", "zeta"])

        with self.assertRaises(ValueError):
            AM.extract_concept_cues("alpha beta", max_seed_cues=-1)
        with self.assertRaises(ValueError):
            AM.extract_concept_cues("alpha beta", max_seed_cues=True)

    def test_s24a_uri_resolver_idempotence_8_link_cap_and_lod_tiers(self) -> None:
        """Verifies URI link idempotence, LinkCountBucket 8-link cap saturation, and LOD tier filtering."""
        self.assertEqual(AM.bucket_for_link_count(0), AM.LBZero)
        self.assertEqual(AM.bucket_for_link_count(4), AM.LB1To7)
        self.assertEqual(AM.bucket_for_link_count(8), AM.LBAtCap8)

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteGraphStore(Path(tmp) / "uri_resolver.sqlite")
            AM.upsert_concept_frame(store, "c_uri_node", "Concept node for testing URI registration idempotence.", [])

            ptr_1 = store.register_external_link("c_uri_node", "https://docs.site-y.org/spec/24a#L10", artifact_kind="spec_anchor", lod_tier="LOD2")
            self.assertEqual(ptr_1.target_uri, "https://docs.site-y.org/spec/24a#L10")
            loaded_1 = AM.get_concept_frame(store, "c_uri_node")
            assert loaded_1 is not None
            self.assertEqual(len(loaded_1.external_links), 1)
            self.assertTrue(AM.verify_uri_resolver_entry(loaded_1))

            ptr_dup = store.register_external_link("c_uri_node", "https://docs.site-y.org/spec/24a#L10", artifact_kind="spec_anchor", lod_tier="LOD2")
            self.assertEqual(ptr_dup.target_uri, "https://docs.site-y.org/spec/24a#L10")
            loaded_dup = AM.get_concept_frame(store, "c_uri_node")
            assert loaded_dup is not None
            self.assertEqual(len(loaded_dup.external_links), 1)

            for idx in range(2, 9):
                store.register_external_link(
                    "c_uri_node", f"https://docs.site-y.org/spec/24a#L{idx * 10}", artifact_kind="spec_anchor", lod_tier="LOD1" if idx <= 3 else "LOD2"
                )
            loaded_8 = AM.get_concept_frame(store, "c_uri_node")
            assert loaded_8 is not None
            self.assertEqual(len(loaded_8.external_links), 8)
            self.assertTrue(AM.verify_uri_resolver_entry(loaded_8))

            store.register_external_link("c_uri_node", "https://docs.site-y.org/spec/24a#L90")
            loaded_sat = AM.get_concept_frame(store, "c_uri_node")
            assert loaded_sat is not None
            self.assertEqual(len(loaded_sat.external_links), 8)
            self.assertNotIn("https://docs.site-y.org/spec/24a#L90", loaded_sat.external_uris)

            with self.assertRaises(ValueError):
                AM.verify_uri_resolver_entry({"external_links": [{"target_uri": f"u{i}"} for i in range(9)]})
            with self.assertRaises(ValueError):
                AM.verify_uri_resolver_entry({"source_links": ["u1"], "external_uris": ["u2"]})

            self.assertEqual(len(store.resolve_external_links("c_uri_node", "LOD1")), 2)
            self.assertEqual(len(store.resolve_external_links("c_uri_node", "LOD2")), 6)
            self.assertEqual(len(store.resolve_external_links("c_uri_node", "*")), 8)

    def test_s24b_hippocampal_index_2_hop_recall_and_ai_memory_store_sqlite(self) -> None:
        """Verifies Stage 24B 2-hop radial recall, multi-cue Steiner bridges, depends_on sink isolation, and expand_concept_neighbors."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "hippocampal_24b.sqlite"
            store = SQLiteGraphStore(db_path)

            committed_rules = [
                {"concept_id": "c_hop0", "text": "Root seedzero concept for testing two hop spreading activation.", "refs": ["c_hop1"], "external_links": ["https://docs.site-y.org/mem/hop0"]},
                {"concept_id": "c_hop1", "text": "First hop neighbor linked directly from seed concept zero.", "refs": ["c_hop2"], "external_links": ["https://docs.site-y.org/mem/hop1"]},
                {"concept_id": "c_hop2", "text": "Second hop concept reachable in two associative steps.", "refs": ["c_hop3"], "external_links": ["https://docs.site-y.org/mem/hop2"]},
                {"concept_id": "c_hop3", "text": "Third hop concept beyond the strict two hop recall bound.", "refs": [], "external_links": ["https://docs.site-y.org/mem/hop3"]},
            ]
            indexed = AM.index_committed_rules(store, committed_rules, current_turn=1)
            self.assertEqual(indexed, ["c_hop0", "c_hop1", "c_hop2", "c_hop3"])

            recalled = AM.recall_concepts(store, "c_hop0")
            self.assertEqual([rc["concept_id"] for rc in recalled], ["c_hop0", "c_hop1", "c_hop2"])
            self.assertEqual([rc["hop"] for rc in recalled], [0, 1, 2])
            self.assertEqual([rc["hop_bucket"] for rc in recalled], ["Hop0Seed", "Hop1Neighbor", "Hop2Second"])
            self.assertLessEqual(sum(rc["token_cost"] for rc in recalled), AM.MAX_LOD1_TOKEN_BUDGET)

            tight_recalled = AM.recall_concepts(store, "c_hop0", token_budget=recalled[0]["token_cost"])
            self.assertEqual([rc["concept_id"] for rc in tight_recalled], ["c_hop0"])

            with self.assertRaises(ValueError):
                AM.recall_concepts(store, "c_hop0", max_recall_hops=7)
            with self.assertRaises(ValueError):
                AM.recall_concepts(store, "c_hop0", token_budget=401)
            with self.assertRaises(ValueError):
                AM.recall_concepts(store, "c_hop0", lod1_synopsis_words=51)

            # Multi-cue Steiner bridge (4-hop > FLOW chain c_src -> c_mid1 -> c_mid2 -> c_mid3 -> c_dst) + depends_on sink isolation
            bridge_rules = [
                {"concept_id": "c_src", "text": "Source module depends on graph_core and flows to c_mid1.", "refs": ["c_mid1", "graph_core"]},
                {"concept_id": "c_mid1", "text": "Bridge stage one transforming source packet.", "refs": ["c_mid2"]},
                {"concept_id": "c_mid2", "text": "Bridge stage two routing intermediate state.", "refs": ["c_mid3"]},
                {"concept_id": "c_mid3", "text": "Bridge stage three delivering to destination.", "refs": ["c_dst"]},
                {"concept_id": "c_dst", "text": "Destination module receiving multi-hop flow.", "refs": []},
                {"concept_id": "graph_core", "text": "Shared foundation utility sink.", "refs": []},
                {"concept_id": "c_unrel", "text": "Unrelated module that depends on graph_core.", "refs": ["graph_core"]},
            ]
            AM.index_committed_rules(store, bridge_rules, current_turn=2)
            self.assertFalse(AM.can_propagate_from_node(True, True, True))
            self.assertTrue(AM.can_propagate_from_node(False, True, True))

            multi_recalled = AM.recall_concepts(store, ["c_src", "c_dst"])
            multi_ids = [rc["concept_id"] for rc in multi_recalled]
            for expected_id in ("c_src", "c_dst", "c_mid1", "c_mid2", "c_mid3", "graph_core"):
                self.assertIn(expected_id, multi_ids)
            self.assertNotIn("c_unrel", multi_ids, "depends_on sink isolation must prevent backward bounce into c_unrel")
            mid2_frame = next(rc for rc in multi_recalled if rc["concept_id"] == "c_mid2")
            self.assertEqual(mid2_frame["hop_bucket"], "Hop2Second")

            mem = AM.AIMemoryStore(store)
            neighbors = mem.expand_concept_neighbors("c_src", max_hops=1)
            self.assertEqual({nb["concept_id"] for nb in neighbors}, {"c_mid1", "graph_core"})
            self.assertEqual([nb["activation"] for nb in neighbors], sorted((nb["activation"] for nb in neighbors), reverse=True))
            self.assertLessEqual(sum(nb["token_cost"] for nb in neighbors), AM.MAX_LOD1_TOKEN_BUDGET)
            self.assertTrue(all(len(nb["lod1_synopsis"].split()) <= AM.MAX_LOD1_SYNOPSIS_WORDS for nb in neighbors))

            ep_node = mem.record_episode("ep_turn_7", "Recorded executive episode linked to c_hop0.", related_frames=["c_hop0"], turn=7)
            self.assertEqual(ep_node["id"], "ep_turn_7")
            wm_lod1 = mem.recall_into_working_memory("c_hop0", deep_detail_needed=False, external_fetcher=lambda uri: f"fetched:{uri}")
            self.assertEqual(wm_lod1["lod_level"], "LOD1")
            self.assertEqual(wm_lod1["dereferenced_sources"], {})
            wm_lod2 = mem.recall_into_working_memory("c_hop0", deep_detail_needed=True, external_fetcher=lambda uri: f"fetched:{uri}")
            self.assertEqual(wm_lod2["lod_level"], "LOD2")
            self.assertIn("https://docs.site-y.org/mem/hop0", wm_lod2["dereferenced_sources"])

    def test_s24_standing_rule_49_no_regex_and_line_budget(self) -> None:
        """Verifies zero `import re` and <= 500 lines across all Stage 24A/24B and test files."""
        files_to_audit = [
            _find_path("eng0/ai_memory/schema.py", "lattice/ai_memory/schema.py"),
            _find_path("eng0/ai_memory/cue_extractor.py", "lattice/ai_memory/cue_extractor.py"),
            _find_path("eng0/ai_memory/uri_resolver.py", "lattice/ai_memory/uri_resolver.py"),
            _find_path("eng0/ai_memory/__init__.py", "lattice/ai_memory/__init__.py"),
            _find_path("eng3/ai_memory/hippocampal_index.py", "lattice/ai_memory/hippocampal_index.py"),
            _find_path("eng3/ai_memory/__init__.py", "lattice/ai_memory/__init__.py"),
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
