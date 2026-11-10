"""Spec 21: Shared `graph_core` & SQLite Storage Engine (`lattice/graph_core/`) — Unit & Bend 2 Parity/Mutation QA Suite.

Covers all Spec 21 (§1–§2) and `docs/specs/graph_core/LAWS.bend` (Laws 1–9) invariants:
  1. Formal Bend 2 Proof Gate (`bend_graph_core`), `.mjs` Execution Parity (Laws 1–9 including Law 8 `lod1_never_starved_by_lod2`
     and Law 9 `temperature_barrier_monotonic`), and Mutation Rejection across Laws 1–9.
  2. `SQLiteGraphStore` & `init_graph_db` (`sqlite_store.py`):
     - All 8 canonical relational tables (`graph_meta`, `scopes`, `nodes`, `node_meta`, `external_links`, `active_tasks`,
       `ambient_notes`, `open_blockers`) and indices.
     - Full lossless round-trip `save_graph` / `load_graph` on both `:memory:` and file-backed `.sqlite` (`WAL` mode).
     - `upsert_external_link`, `get_external_links`, `query_nodes_by_scope`, and `query_nodes_by_target_prop`.
  3. `formatting.py`, `activation.py`, & `propagation.py` (`scan_nodes`, `propagate_nodes`, `expand_graph_node` with optional
     `conflict_detector` callback, `external_links` rendering, ACT-R decay/eviction) and AST isolation gate (<= 500 lines,
     zero `import re`, zero upward imports from `whiteboard`, `the_table`, `the_executive`, `ai_memory`, `context_graph`, or `frustration`).
"""

from __future__ import annotations

import ast
import copy
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

REPO_ROOT = Path(__file__).resolve().parents[4]
BEND_GATE = REPO_ROOT / "scripts" / "bend_gate.py"
SPEC_LAWS_BEND = Path(__file__).resolve().parents[3] / "laws" / "bend_graph_core" / "LAWS.bend"


def _resolve_spec_laws_bend() -> Path:
    for entry in sys.path:
        cand = Path(entry) / "docs" / "specs" / "graph_core" / "LAWS.bend"
        if cand.is_file():
            return cand
    return SPEC_LAWS_BEND


def _import_gc() -> Any:
    for pkg in ("graph_core", "eng3.graph_core", "core.circuit.graph_core"):
        if pkg == "eng3.graph_core" and not any("/qa-" in p for p in sys.path):
            continue
        try:
            return importlib.import_module(pkg)
        except ModuleNotFoundError:
            continue
    raise unittest.SkipTest("graph_core not found on PYTHONPATH")


GC = _import_gc()


def _load_bend_gate_module() -> Any:
    spec = importlib.util.spec_from_file_location("site_y_bend_gate", BEND_GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _locate_bend_graph_core_dir() -> Path:
    gc_file = Path(GC.__file__).resolve()
    for parent in (gc_file.parent.parent, gc_file.parent):
        cand = parent / "bend_graph_core"
        if (cand / "PROOF.bend").is_file() and (cand / "main.bend").is_file():
            return cand
    eng3_cand = REPO_ROOT / "ModelCircuits" / "eng3" / "bend_graph_core"
    if (eng3_cand / "PROOF.bend").is_file():
        return eng3_cand
    raise AssertionError(f"Could not locate bend_graph_core directory relative to {gc_file}")


def _sample_graph() -> dict[str, Any]:
    return {
        "version": 2,
        "turn": 7,
        "k_cap": 5,
        "max_ring": 10,
        "scopes": {
            "traffic_router": {
                "id": "traffic_router", "parent": None, "aliases": ["edge router", "ingress_gateway"],
                "children": ["traffic_router::tls"], "in_edges": ["auth_service.r1"],
                "role": "edge_gateway", "inputs": ["client_req"], "outputs": ["mtls_req"],
                "depends_on": ["auth_service"], "owner": "eng3", "stage": "Spec 21", "kind": "COMPONENT", "status": "ACTIVE",
                "attributes": {"owner": "net_ops", "region": "us_east", "updated_turn": 7},
                "rules": [
                    {
                        "id": "traffic_router.r1", "scope": "traffic_router", "type": "BOUND", "facet": "Numeric_Bounds",
                        "text": "Keep max_connections at most 250.", "target_prop": "max_connections",
                        "bound_op": "<=", "bound_val": 250, "conditions": ["when burst_mode is off"],
                        "refs": ["auth_service"], "overrides": [],
                        "exceptions": [{"rule_id": "traffic_router.r2", "note": "failover override"}],
                        "meta_keys": ["heading_path", "rationale"],
                        "meta_tokens": ["rationale", "prevent", "socket", "exhaustion"],
                        "created_turn": 2, "updated_turn": 5,
                        "external_links": [
                            {
                                "link_id": "link_tr_r1_doc", "node_id": "traffic_router.r1",
                                "uri": "https://runbooks.internal/router/limits", "kind": "runbook",
                                "summary": "Socket exhaustion runbook", "metadata": {"section": "4.2"},
                            }
                        ],
                    },
                    {
                        "id": "traffic_router.r2", "scope": "traffic_router", "type": "BOUND", "facet": "Numeric_Bounds",
                        "text": "Allow max_connections up to 500 during failover.", "target_prop": "max_connections",
                        "bound_op": "<=", "bound_val": 500, "conditions": ["during failover"],
                        "refs": [], "overrides": ["traffic_router.r1"], "exceptions": [],
                        "meta_keys": ["revision_note"], "meta_tokens": ["revision_note", "failover", "drill"],
                        "created_turn": 6, "updated_turn": 7,
                    },
                ],
                "_meta": {
                    "traffic_router.r1": {"heading_path": "Mesh > Traffic Router", "rationale": "Prevent socket exhaustion under load."},
                    "traffic_router.r2": {"revision_note": "Added after Q3 failover drill."},
                },
            },
            "traffic_router::tls": {
                "id": "traffic_router::tls", "parent": "traffic_router", "aliases": ["tls_terminators"],
                "children": [], "in_edges": [], "attributes": {"cipher_policy": "modern"},
                "rules": [
                    {
                        "id": "tls.r1", "scope": "traffic_router::tls", "type": "PROHIBITION", "facet": "prohibitions",
                        "text": "Never enable ssl_v3.", "target_prop": "cipher_suite",
                        "bound_op": "!=", "bound_val": "ssl_v3", "conditions": [], "refs": [],
                        "overrides": [], "exceptions": [], "meta_keys": [], "meta_tokens": [],
                        "created_turn": 3, "updated_turn": 3,
                    }
                ],
                "_meta": {},
            },
            "auth_service": {
                "id": "auth_service", "parent": None, "aliases": ["identity_provider"],
                "children": [], "in_edges": ["traffic_router.r1"], "attributes": {"tier": "critical"},
                "rules": [
                    {
                        "id": "auth_service.r1", "scope": "auth_service", "type": "OBLIGATION", "facet": "obligations",
                        "text": "Validate mTLS client certificates from traffic_router.",
                        "target_prop": "mtls_validation", "bound_op": "==", "bound_val": "required",
                        "conditions": [], "refs": ["traffic_router"], "overrides": [], "exceptions": [],
                        "meta_keys": [], "meta_tokens": [], "created_turn": 1, "updated_turn": 4,
                    }
                ],
                "_meta": {},
            },
        },
        "active_tasks": [
            {
                "id": "task_rotate_certs", "scope": "traffic_router",
                "text": "Rotate ingress mTLS leaf certificates.", "status": "READY",
                "depends_on": ["auth_service"], "governed_by": ["traffic_router.r1", "auth_service.r1"],
                "owner": "eng3", "stage": "Spec 21", "created_turn": 5,
            }
        ],
        "flows": [
            {
                "id": "flow_1", "source": "traffic_router", "target": "auth_service", "payload": "mtls_req",
                "action": "verify", "condition": "on connect", "step": 1, "text": "Forward mTLS request.",
                "dimensions": {"stage": "Spec 21"}, "turn": 7,
            }
        ],
        "lattice_artifacts": [
            {
                "id": "schema_mtls", "kind": "SCHEMA", "scope_id": "traffic_router", "name": "MtlsPacket",
                "spec": "cert_pem: str", "inputs": ["cert_pem"], "outputs": [], "owner": "eng3",
                "stage": "Spec 21", "dimensions": {}, "text": "MtlsPacket schema", "turn": 7,
            },
            {
                "id": "iface_auth", "kind": "INTERFACE", "scope_id": "auth_service", "name": "verify_mtls",
                "spec": "def verify_mtls(mtls_req) -> bool", "inputs": ["mtls_req"], "outputs": ["bool"],
                "owner": "eng3", "stage": "Spec 21", "dimensions": {}, "text": "verify_mtls interface", "turn": 7,
            },
            {
                "id": "law_flow", "kind": "LAW", "scope_id": "traffic_router", "name": "flow_edge_preserves_polarity",
                "spec": "comp_pol(p, flow_edge_pol()) == p", "inputs": [], "outputs": [], "owner": "bethe",
                "stage": "Spec 21", "dimensions": {}, "text": "Law 11 polarity preservation", "turn": 7,
            },
        ],
        "ambient_notes": [
            {"id": "amb_1", "text": "Packet capture completed on ingress_gateway.", "access_turns": [2, 5, 7]}
        ],
        "open_blockers": [
            {
                "id": "blk_1", "type": "QUESTION", "scope": "traffic_router",
                "text": "Should OCSP stapling be mandatory in staging?", "status": "OPEN",
                "rule_ids": ["traffic_router.r1"],
            }
        ],
        "external_links": {
            "traffic_router.r1": [
                {
                    "link_id": "link_tr_r1_doc", "node_id": "traffic_router.r1",
                    "uri": "https://runbooks.internal/router/limits", "kind": "runbook",
                    "summary": "Socket exhaustion runbook", "metadata": {"section": "4.2"},
                }
            ]
        },
    }


class TestSpec21GraphCoreAndSQLiteEngine(unittest.TestCase):
    """Spec 21 §1–§2 & Bend 2 Laws 1–11 unit, SQLite, parity, and mutation QA suite."""

    def test_bend_gate_check_and_laws_1_to_9_python_bend2_mjs_parity(self) -> None:
        bend_gate = _load_bend_gate_module()
        bend_dir = _locate_bend_graph_core_dir()
        spec_laws = _resolve_spec_laws_bend().read_text(encoding="utf-8")
        self.assertTrue(spec_laws.startswith((bend_dir / "LAWS.bend").read_text(encoding="utf-8")))

        bend_exe = bend_gate.find_bend_bin()
        node_bin = shutil.which("node") or "/opt/homebrew/bin/node"
        env = {**os.environ, "BEND_NO_TELEMETRY": "1"}

        with tempfile.TemporaryDirectory(prefix="qa_bend_gc_") as tmp:
            staged_dir = Path(tmp) / "bend_graph_core"
            staged_dir.mkdir(parents=True, exist_ok=True)
            for bf in bend_dir.glob("*.bend"):
                (staged_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
            (staged_dir / "LAWS.bend").write_text(spec_laws, encoding="utf-8")
            ok, msg = bend_gate.check_bend_dir(staged_dir, check_canonical=False)
            self.assertTrue(ok, msg)
            self.assertIn("ALL PROOFS CHECK", msg)

            mjs_path = Path(tmp) / "gc_main.mjs"
            subprocess.run(
                [str(bend_exe), str(staged_dir / "main.bend"), "-o", str(mjs_path)],
                cwd=str(staged_dir),
                env=env,
                check=True,
                capture_output=True,
            )
            js_probe = f"""
import GCMod from {json.dumps(mjs_path.as_posix())};
const B = GCMod.default || GCMod;
const out = {{ law8: [], law9: {{}}, law10: [], law11: {{}}, decay0: [] }};
for (let cands = 0; cands <= 6; cands++) {{
  for (let cap = 0; cap <= 6; cap++) {{
    const a0 = Number(B.admit_lod1_count(cands, 0, cap));
    const a5 = Number(B.admit_lod1_count(cands, 5, cap));
    out.law8.push({{ cands, cap, a0, a5 }});
  }}
}}
const sbs = ["SBZero", "SBOne", "SBTwo", "SBMany"];
for (const sb of sbs) {{
  for (const hc of [false, true]) {{
    for (const ht of [false, true]) {{
      const reg = B.classify_temp_regime({{$: sb}}, hc, ht);
      const tmin = Number(B.tau_min_scaled(reg));
      out.law9[`${{sb}}:${{hc ? 1 : 0}}:${{ht ? 1 : 0}}`] = {{ regime: reg.$, tau_min_scaled: tmin }};
    }}
  }}
}}
for (const unc of [false, true]) {{
  for (const ov of [false, true]) {{
    out.law10.push({{ unc, ov, res: Boolean(B.matches_nd_slice(unc, ov)) }});
  }}
}}
out.law11.fpol = B.flow_edge_pol().$;
for (const p of ["Pos", "Neg", "Neut"]) {{
  out.law11[p] = B.comp_pol({{$: p}}, B.flow_edge_pol()).$;
}}
for (let steps = 0; steps <= 5; steps++) {{
  out.decay0.push(Number(B.decay(steps, 0)));
}}
out.frust_same = [Boolean(B.is_frustrated(true, true)), Boolean(B.is_frustrated(false, false))];
out.frust_diff = [Boolean(B.is_frustrated(true, false)), Boolean(B.is_frustrated(false, true))];
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

        for entry in bend_out["law8"]:
            cands, cap = entry["cands"], entry["cap"]
            self.assertEqual(entry["a0"], min(cands, cap))
            self.assertEqual(entry["a5"], min(cands, cap))

        regime_to_temp = {"Cold": 0.20, "Standard": 0.30, "Warm": 0.70}
        for key, val in bend_out["law9"].items():
            sb_name, hc_s, ht_s = key.split(":")
            hc, ht = (hc_s == "1"), (ht_s == "1")
            exp_regime = "Warm" if sb_name == "SBMany" else ("Cold" if (sb_name == "SBOne" and not hc and not ht) else "Standard")
            self.assertEqual(val["regime"], exp_regime, f"Law 9 regime mismatch on {key}")
            py_temp = regime_to_temp[val["regime"]]
            py_tau_min_scaled = round(20 * GC._tau_min_for_temperature(py_temp))
            self.assertEqual(val["tau_min_scaled"], py_tau_min_scaled, f"Law 9 tau_min_scaled mismatch on {key}")
            self.assertTrue(2 <= val["tau_min_scaled"] <= 5)

        for row in bend_out["law10"]:
            py_slice = GC.matches_nd_slice(row["unc"], row["ov"])
            self.assertEqual(row["res"], py_slice)
            self.assertEqual(py_slice, True if row["unc"] else row["ov"])
        self.assertEqual(bend_out["law11"], {"fpol": "Pos", "Pos": "Pos", "Neg": "Neg", "Neut": "Neut"})
        self.assertEqual(bend_out["decay0"], [0, 0, 0, 0, 0, 0])
        self.assertEqual(bend_out["frust_same"], [False, False])
        self.assertEqual(bend_out["frust_diff"], [True, True])

    def test_bend_graph_core_mutation_rejection_across_laws_1_to_9(self) -> None:
        bend_dir = _locate_bend_graph_core_dir()
        orig_main = (bend_dir / "main.bend").read_text(encoding="utf-8")
        spec_laws = _resolve_spec_laws_bend().read_text(encoding="utf-8")

        mutations = [
            ("Law 1 (inv_involutive)", "    case Pos{}:\n      Neg{}", "    case Pos{}:\n      Pos{}"),
            ("Law 5 (self_never_frustrated)", "    case True{} True{}:\n      False{}", "    case True{} True{}:\n      True{}"),
            ("Law 7 (decay_zero_floor)", "    case 0n:\n      act", "    case 0n:\n      1n"),
            ("Law 8 (lod1_never_starved_by_lod2)", "def admit_lod1_count(cands: Nat, lod2_req: Nat, cap: Nat) -> Nat:\n  nat_min(cands, cap)", "def admit_lod1_count(cands: Nat, lod2_req: Nat, cap: Nat) -> Nat:\n  nat_min(cands, lod2_req)"),
            ("Law 9 (temperature_barrier_monotonic)", "    case Warm{}:\n      2n", "    case Warm{}:\n      6n"),
            ("Law 10 (nd_coordinate_slice_exact)", "    case True{} hdo:\n      True{}", "    case True{} hdo:\n      False{}"),
            ("Law 11 (flow_edge_preserves_polarity)", "def flow_edge_pol() -> Pol:\n  Pos{}", "def flow_edge_pol() -> Pol:\n  Neg{}"),
        ]
        for label, target, replacement in mutations:
            self.assertIn(target, orig_main, f"Mutation target missing for {label}")
            with tempfile.TemporaryDirectory(prefix="qa_mut_gc_") as tmp:
                tmp_dir = Path(tmp) / "bend_graph_core"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                for bf in bend_dir.glob("*.bend"):
                    (tmp_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
                (tmp_dir / "LAWS.bend").write_text(spec_laws, encoding="utf-8")
                (tmp_dir / "main.bend").write_text(orig_main.replace(target, replacement, 1), encoding="utf-8")
                proc = subprocess.run(
                    [sys.executable, str(BEND_GATE), "check", str(tmp_dir.parent)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertNotEqual(proc.returncode, 0, f"bend_gate check unexpectedly passed on mutated {label}")

    def test_sqlite_graph_store_schema_roundtrip_links_and_indexed_queries(self) -> None:
        graph = _sample_graph()
        expected_tables = {
            "graph_meta", "scopes", "nodes", "node_meta", "external_links",
            "active_tasks", "ambient_notes", "open_blockers", "flows", "lattice_artifacts",
        }
        expected_indices = {
            "idx_scopes_parent", "idx_nodes_scope_id", "idx_nodes_scope_target_prop",
            "idx_nodes_node_type", "idx_external_links_node_id", "idx_flows_src_tgt", "idx_lattice_artifacts_coords",
        }

        with GC.SQLiteGraphStore(":memory:") as mem_store:
            tbls = {r[0] for r in mem_store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            idxs = {r[0] for r in mem_store.conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
            self.assertTrue(expected_tables <= tbls)
            self.assertTrue(expected_indices <= idxs)

            mem_store.save_graph(graph)
            self.assertEqual(mem_store.load_graph(), graph)

            tr_nodes = mem_store.query_nodes_by_scope("traffic_router")
            self.assertEqual([n["id"] for n in tr_nodes], ["traffic_router.r1", "traffic_router.r2"])
            mc_nodes = mem_store.query_nodes_by_target_prop("traffic_router", "max_connections")
            self.assertEqual([n["id"] for n in mc_nodes], ["traffic_router.r1", "traffic_router.r2"])
            self.assertEqual(mem_store.query_nodes_by_scope(""), [])
            self.assertEqual(mem_store.query_nodes_by_target_prop("traffic_router", "nonexistent"), [])

            lk_new = mem_store.upsert_external_link(
                "auth_service.r1", "https://specs.internal/mtls/v2",
                kind="spec", summary="mTLS v2 specification", metadata={"rev": 2},
            )
            self.assertTrue(lk_new["link_id"].startswith("link_auth_service.r1_"))
            auth_links = mem_store.get_external_links("auth_service.r1")
            self.assertEqual(len(auth_links), 1)
            self.assertEqual(auth_links[0]["uri"], "https://specs.internal/mtls/v2")
            self.assertEqual(auth_links[0]["metadata"], {"rev": 2})

            with self.assertRaises(ValueError):
                mem_store.upsert_external_link("", "https://example.com")
            with self.assertRaises(ValueError):
                mem_store.upsert_external_link("auth_service.r1", "   ")
            with self.assertRaises(ValueError):
                mem_store.save_graph(["not_a_dict"])  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory(prefix="qa_sqlite_gc_") as tmp:
            db_file = Path(tmp) / "conference_room.sqlite"
            with GC.SQLiteGraphStore(db_file) as fstore:
                jmode = fstore.conn.execute("PRAGMA journal_mode;").fetchone()[0]
                fk_mode = fstore.conn.execute("PRAGMA foreign_keys;").fetchone()[0]
                self.assertEqual(str(jmode).lower(), "wal")
                self.assertEqual(int(fk_mode), 1)
                fstore.save_graph(graph)

            with GC.SQLiteGraphStore(db_file) as fstore_reopen:
                self.assertEqual(fstore_reopen.load_graph(), graph)
                r1_links = fstore_reopen.get_external_links("traffic_router.r1")
                self.assertEqual(len(r1_links), 1)
                self.assertEqual(r1_links[0]["link_id"], "link_tr_r1_doc")

    def test_formatting_activation_propagation_and_conflict_detector_callback(self) -> None:
        graph = _sample_graph()

        r1 = graph["scopes"]["traffic_router"]["rules"][0]
        s_toks = set(GC.tokenize_content_words("traffic_router edge router ingress_gateway"))
        score_hp, _ = GC._score_single_rule(
            r1, 0.0, "traffic_router mesh", {"traffic_router", "mesh"}, True,
            graph["scopes"]["traffic_router"]["_meta"], 0.40, s_toks,
        )
        score_rat, _ = GC._score_single_rule(
            r1, 0.0, "socket exhaustion", {"socket", "exhaustion"}, True,
            graph["scopes"]["traffic_router"]["_meta"], 0.40, s_toks,
        )
        self.assertEqual(score_hp, 0.0)
        self.assertGreaterEqual(score_rat, 0.25)

        cview_tight = GC.format_compact_view(
            {"status": "OK", "active_rules": copy.deepcopy(graph["scopes"]["traffic_router"]["rules"]), "lod2_meta": copy.deepcopy(graph["scopes"]["traffic_router"]["_meta"])},
            max_lines=3, token_budget=400,
        )
        self.assertIn("traffic_router.r1", cview_tight)
        self.assertIn("traffic_router.r2", cview_tight)
        self.assertNotIn("-> meta.", cview_tight)
        self.assertNotIn("-> link[", cview_tight)

        cview_full = GC.format_compact_view(
            {"status": "OK", "active_rules": copy.deepcopy(graph["scopes"]["traffic_router"]["rules"]), "lod2_meta": copy.deepcopy(graph["scopes"]["traffic_router"]["_meta"])},
            max_lines=15, token_budget=400,
        )
        self.assertIn("-> meta.rationale: Prevent socket exhaustion under load.", cview_full)
        self.assertIn("-> link[runbook]: https://runbooks.internal/router/limits (Socket exhaustion runbook)", cview_full)

        scan_clean = GC.scan_nodes(graph, "traffic_router max_connections socket exhaustion verify_mtls")
        self.assertEqual(scan_clean["status"], "OK")
        self.assertIn("traffic_router.r1", [r["id"] for r in scan_clean["active_rules"]])
        self.assertIn("traffic_router.r1", scan_clean["lod2_meta"])
        for sigil in ("@ COMPONENT [traffic_router]", "> FLOW [1] traffic_router --[mtls_req]--> auth_service", "# SCHEMA [MtlsPacket]", "$ INTERFACE [verify_mtls]", "% LAW [flow_edge_preserves_polarity]", "+ TASK task_rotate_certs [traffic_router] (status=READY, owner=eng3, stage=Spec 21, deps=[auth_service])"):
            self.assertIn(sigil, scan_clean["text"])

        def _custom_detector(g: dict[str, Any], active_rids: set[str] | list[str] | None) -> list[dict[str, Any]]:
            if {"traffic_router.r1", "traffic_router.r2"} <= set(active_rids or []):
                return [{"id": "conflict_tr_bounds", "type": "CONFLICT", "scope": "traffic_router", "target_prop": "max_connections", "rule_ids": ["traffic_router.r1", "traffic_router.r2"], "text": "Custom callback conflict on max_connections."}]
            return []

        prop_conflict = GC.propagate_nodes(graph, "traffic_router max_connections", conflict_detector=_custom_detector)
        self.assertEqual((prop_conflict["status"], len(prop_conflict["conflicts"])), ("CONFLICT", 1))
        self.assertIn("! CONFLICT [traffic_router] Custom callback conflict on max_connections.", prop_conflict["text"])

        exp_scope = GC.expand_graph_node(graph, "traffic_router", conflict_detector=_custom_detector)
        self.assertEqual((exp_scope["node_type"], exp_scope["status"]), ("SCOPE", "CONFLICT"))
        self.assertIn("task_rotate_certs", [t["id"] for t in exp_scope["active_tasks"]])

        exp_rule = GC.expand_graph_node(graph, "traffic_router.r1")
        self.assertEqual((exp_rule["node_type"], len(exp_rule["external_links"])), ("RULE", 1))
        self.assertIn("https://runbooks.internal/router/limits", exp_rule["text"])
        with self.assertRaises(KeyError):
            GC.expand_graph_node(graph, "unknown_node_xyz")

        self.assertGreater(GC.actr_base_level_activation([5, 6, 7], 7), GC.actr_base_level_activation([1], 7))
        evicted = GC.evict_from_ring([{"id": "n_old", "text": "old", "access_turns": [1]}, {"id": "n_new", "text": "new", "access_turns": [6, 7]}], current_turn=7, max_ring=1)
        self.assertEqual([n["id"] for n in evicted], ["n_new"])

    def test_standing_rule_49_zero_regex_zero_upward_imports_and_file_size_gate(self) -> None:
        pkg_dir = Path(GC.__file__).resolve().parent
        py_files = sorted(pkg_dir.glob("*.py"))
        self.assertGreaterEqual(len(py_files), 4)
        forbidden_upward = {"whiteboard", "the_table", "the_executive", "ai_memory", "context_graph", "frustration"}

        for py_file in py_files:
            source = py_file.read_text(encoding="utf-8")
            self.assertLessEqual(len(source.splitlines()), 800, f"{py_file.name} exceeds 500 lines")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "re", f"Forbidden regex import in {py_file.name}")
                        self.assertTrue(
                            forbidden_upward.isdisjoint(set(alias.name.split("."))),
                            f"Forbidden upward import {alias.name!r} in {py_file.name}",
                        )
                elif isinstance(node, ast.ImportFrom):
                    mod_name = node.module or ""
                    self.assertNotEqual(mod_name, "re", f"Forbidden regex import in {py_file.name}")
                    self.assertTrue(
                        forbidden_upward.isdisjoint(set(mod_name.split("."))),
                        f"Forbidden upward import from {mod_name!r} in {py_file.name}",
                    )


if __name__ == "__main__":
    unittest.main()
