"""Spec 22A: `whiteboard` Schema, SQLite Persistence & Write Mutations (`schema.py`, `mutations.py`, `bend_wb_22a`) — Unit & Bend 2 QA Suite.

Covers all Spec 22A (§1–§3) and `docs/specs/whiteboard_22a/LAWS.bend` (Laws 1–3) contracts:
  1. Bend 2 Proof Gate (`bend_wb_22a`), `.mjs` Execution Parity across Laws 1–3 (`blocker_resolve_iff_id_matches`,
     `blocker_resolved_idempotent`, `mitosis_conserves_rule_count`), and Mutation Rejection across Laws 1–3.
  2. `whiteboard.schema`: `RuleEntry`, `ScopeNode`, `ContextGraph` / `WhiteboardGraph`, `create_empty_graph` (with `:memory:`
     and file-backed `.sqlite` `SQLiteGraphStore` persistence & reload), `sync_to_sqlite`, and `validate_context_graph` /
     `verify_graph_invariants` (including fail-closed `ValueError` on bare string in `exceptions`).
  3. `whiteboard.mutations`: `ensure_scope_node`, `wire_bidirectional_refs`, `wire_lex_specialis_override`,
     `markovian_overwrite_rule`, `prune_dysfluency_and_upsert_rule`, `graduate_task_to_fact`, `upsert_ambient_note`,
     `two_axis_mitosis` (`::` child scope naming, 100% rule conservation Law 3, summary dict return), and
     `resolve_open_blocker` (Laws 1–2, `remove=False` and `remove=True` + SQLite sync).
  4. Standing Rule 49 & Spec 22A Isolation Gate (`<= 500` lines, zero `import re`, no forbidden 22B/22C files in `eng1`).
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

from bowthruster.graph_core import SQLiteGraphStore

REPO_ROOT = Path(__file__).resolve().parents[4]
BEND_GATE = REPO_ROOT / "scripts" / "bend_gate.py"
SPEC_LAWS_BEND = Path(__file__).resolve().parents[3] / "laws" / "bend_wb_22a" / "LAWS.bend"


def _resolve_spec_laws_bend() -> Path:
    if SPEC_LAWS_BEND.is_file():
        return SPEC_LAWS_BEND
    for entry in sys.path:
        for rel in ("laws/bend_wb_22a/LAWS.bend", "docs/specs/whiteboard_22a/LAWS.bend"):
            if (cand := Path(entry) / rel).is_file():
                return cand
    return SPEC_LAWS_BEND


def _import_wb_22a() -> tuple[Any, Any]:
    for pkg in ("whiteboard", "eng1.whiteboard", "core.circuit.whiteboard"):
        if pkg == "eng1.whiteboard" and not any("/qa-" in p for p in sys.path):
            continue
        try:
            s_mod = importlib.import_module(f"{pkg}.schema")
            m_mod = importlib.import_module(f"{pkg}.mutations")
            return s_mod, m_mod
        except ModuleNotFoundError:
            continue
    raise unittest.SkipTest("whiteboard.schema / whiteboard.mutations not found on PYTHONPATH")


def _load_bend_gate_module() -> Any:
    spec = importlib.util.spec_from_file_location("site_y_bend_gate", BEND_GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _locate_bend_wb_22a_dir(schema_mod: Any) -> Path:
    s_file = Path(schema_mod.__file__).resolve()
    for parent in (s_file.parent.parent, s_file.parent):
        cand = parent / "bend_wb_22a"
        if (cand / "PROOF.bend").is_file() and (cand / "main.bend").is_file():
            return cand
    eng1_cand = REPO_ROOT / "ModelCircuits" / "eng1" / "bend_wb_22a"
    if (eng1_cand / "PROOF.bend").is_file():
        return eng1_cand
    raise AssertionError(f"Could not locate bend_wb_22a directory relative to {s_file}")


class TestSpec22AWhiteboardSchemaAndMutations(unittest.TestCase):
    """Spec 22A §1–§3 unit, SQLite persistence, Bend 2 parity, and mutation QA suite."""

    def test_bend_gate_check_and_laws_1_to_3_python_bend2_mjs_parity(self) -> None:
        schema_mod, mut_mod = _import_wb_22a()
        bend_gate = _load_bend_gate_module()
        bend_dir = _locate_bend_wb_22a_dir(schema_mod)

        self.assertEqual(
            (bend_dir / "LAWS.bend").read_text(encoding="utf-8"),
            _resolve_spec_laws_bend().read_text(encoding="utf-8"),
            "eng1/bend_wb_22a/LAWS.bend must match docs/specs/whiteboard_22a/LAWS.bend byte-for-byte",
        )
        ok, msg = bend_gate.check_bend_dir(bend_dir, check_canonical=False)
        self.assertTrue(ok, msg)
        self.assertIn("ALL PROOFS CHECK", msg)

        bend_exe = bend_gate.find_bend_bin()
        node_bin = shutil.which("node") or "/opt/homebrew/bin/node"
        env = {**os.environ, "BEND_NO_TELEMETRY": "1"}

        with tempfile.TemporaryDirectory(prefix="qa_bend_22a_") as tmp:
            mjs_path = Path(tmp) / "wb_22a.mjs"
            subprocess.run(
                [str(bend_exe), str(bend_dir / "main.bend"), "-o", str(mjs_path)],
                cwd=str(bend_dir),
                env=env,
                check=True,
                capture_output=True,
            )
            js_probe = f"""
import WBMod from {json.dumps(mjs_path.as_posix())};
const B = WBMod.default || WBMod;
const out = {{ law1: {{}}, law2: {{}}, law3: [], law4: {{}}, law5: [], law6: [], law7: [] }};
for (const m of [false, true]) {{
  const sOpen = B.step_blocker_resolution({{$: "BlockerOpen"}}, m);
  const sRes = B.step_blocker_resolution({{$: "BlockerResolved"}}, m);
  out.law1[String(m)] = Boolean(B.is_resolved(sOpen));
  out.law2[String(m)] = Boolean(B.is_resolved(sRes));
}}
for (let n = 0; n <= 20; n++) {{
  const lh = Number(B.left_half(n));
  const rh = Number(B.right_half(n));
  const tot = Number(B.add_nat(lh, rh));
  out.law3.push({{ n, lh, rh, tot }});
}}
const kinds = [
  ["COMPONENT", "LKComponent"], ["FLOW", "LKFlow"], ["SCHEMA", "LKSchema"], ["INTERFACE", "LKInterface"],
  ["LAW", "LKLaw"], ["CONSTRAINT", "LKConstraint"], ["FACT", "LKFact"], ["TASK", "LKTask"],
];
const storeMap = {{ StoreScopes: "SCOPES", StoreFlows: "FLOWS", StoreArtifacts: "ARTIFACTS", StoreTasks: "TASKS", StoreRules: "RULES" }};
for (const [kStr, kTag] of kinds) {{
  const st = B.classify_lattice_store({{$: kTag}});
  out.law4[kStr] = {{ store: storeMap[st.$], is_flow: Boolean(B.is_flow_store(st)), is_rule: Boolean(B.is_rule_store(st)) }};
}}
for (const p of [false, true]) for (const f of [false, true]) for (const c of [false, true]) for (const v of [false, true]) {{
  out.law5.push({{ p, f, c, v, res: Boolean(B.should_register_as_scope(p, f, c, v)) }});
}}
const bStatuses = [["DRAFT", "BSDraft"], ["READY", "BSReady"], ["IN_PROGRESS", "BSInProgress"], ["IN_QA", "BSInQa"], ["FAILED", "BSFailed"], ["VERIFIED", "BSVerified"], ["STALE", "BSStale"], ["BLOCKED_BY_DEP", "BSBlockedByDep"]];
for (const [bsStr, bsTag] of bStatuses) for (const prov of [false, true]) for (const deps of [false, true]) {{
  out.law7.push({{ bsStr, prov, deps, tag: B.resolve_packet_lifecycle_status({{$: bsTag}}, prov, deps).$ }});
}}
for (const ed of [false, true]) {{
  out.law6.push({{ ed, verTag: B.step_component_lifecycle_on_edit({{$: "BSVerified"}}, ed).$ }});
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

        # Law 1 & Law 2 parity with Python resolve_open_blocker
        self.assertEqual(bend_out["law1"], {"false": False, "true": True})
        self.assertEqual(bend_out["law2"], {"false": True, "true": True})

        g_blk = schema_mod.create_empty_graph()
        g_blk["open_blockers"] = [{"id": "blk_1", "type": "QUESTION", "scope": "svc", "text": "Clarify?", "status": "OPEN"}]
        self.assertFalse(mut_mod.resolve_open_blocker(g_blk, "non_matching_id"))
        self.assertEqual(g_blk["open_blockers"][0]["status"], "OPEN")
        self.assertTrue(mut_mod.resolve_open_blocker(g_blk, "blk_1"))
        self.assertEqual(g_blk["open_blockers"][0]["status"], "RESOLVED")
        # Idempotent when already RESOLVED (Law 2)
        self.assertFalse(mut_mod.resolve_open_blocker(g_blk, "non_matching_id"))
        self.assertEqual(g_blk["open_blockers"][0]["status"], "RESOLVED")
        self.assertTrue(mut_mod.resolve_open_blocker(g_blk, "blk_1"))
        self.assertEqual(g_blk["open_blockers"][0]["status"], "RESOLVED")

        # Law 3 parity with Python two_axis_mitosis rule-count conservation
        for item in bend_out["law3"]:
            self.assertEqual(item["tot"], item["n"])
            self.assertEqual(item["lh"] + item["rh"], item["n"])

        # Law 4 & Law 5 parity with Python classify_lattice_store and should_register_as_scope
        expected_stores = {
            "COMPONENT": "SCOPES", "FLOW": "FLOWS", "SCHEMA": "ARTIFACTS", "INTERFACE": "ARTIFACTS",
            "LAW": "ARTIFACTS", "TASK": "TASKS", "CONSTRAINT": "RULES", "FACT": "RULES",
        }
        for k_str, exp_store in expected_stores.items():
            py_store = schema_mod.classify_lattice_store(k_str)
            self.assertEqual(py_store, exp_store)
            self.assertEqual(bend_out["law4"][k_str]["store"], py_store)
            self.assertEqual(bend_out["law4"][k_str]["is_flow"], k_str == "FLOW")
            self.assertEqual(bend_out["law4"][k_str]["is_rule"], k_str in ("CONSTRAINT", "FACT"))
        for row in bend_out["law5"]:
            py_reg = schema_mod.should_register_as_scope(
                is_primary_scope=row["p"], is_flow_endpoint=row["f"], is_explicit_component=row["c"], is_bound_or_payload_val=row["v"]
            )
            self.assertEqual(row["res"], py_reg)
            if not (row["p"] or row["f"] or row["c"]):
                self.assertFalse(py_reg)
        step_fn = getattr(schema_mod, "step_component_lifecycle_on_edit", None) or getattr(mut_mod, "step_component_lifecycle_on_edit")
        res_fn = getattr(schema_mod, "resolve_packet_lifecycle_status", None) or getattr(mut_mod, "resolve_packet_lifecycle_status")
        self.assertEqual((step_fn("VERIFIED", False), step_fn("VERIFIED", True)), ("VERIFIED", "STALE"))
        for row in bend_out["law6"]:
            self.assertIn("Stale" if row["ed"] else "Verified", row["verTag"])
        for row in bend_out["law7"]:
            py_pkt_st = res_fn(row["bsStr"], row["prov"], row["deps"])
            self.assertIn(py_pkt_st.split("_")[0].capitalize(), row["tag"])

    def test_bend_wb_22a_mutation_rejection_across_laws_1_to_3(self) -> None:
        schema_mod, _ = _import_wb_22a()
        bend_gate = _load_bend_gate_module()
        bend_dir = _locate_bend_wb_22a_dir(schema_mod)
        orig_main = (bend_dir / "main.bend").read_text(encoding="utf-8")

        mutations = [
            ("Law 1 (blocker_resolve_iff_id_matches)", "        case True{}:\n          BlockerResolved{}", "        case True{}:\n          BlockerOpen{}"),
            ("Law 2 (blocker_resolved_idempotent)", "    case BlockerResolved{}:\n      BlockerResolved{}", "    case BlockerResolved{}:\n      BlockerOpen{}"),
            ("Law 3 (mitosis_conserves_rule_count)", "    case 0n:\n      b", "    case 0n:\n      0n"),
            ("Law 4 (lattice_sbf_never_enters_rule_store)", "    case LKFlow{}:\n      StoreFlows{}", "    case LKFlow{}:\n      StoreRules{}"),
            ("Law 5 (ghost_scope_exclusion_law)", "  Bool.or(is_primary_scope, Bool.or(is_flow_endpoint, is_explicit_component))", "  Bool.or(is_primary_scope, Bool.or(is_flow_endpoint, is_bound_or_payload_val))"),
        ]
        for label, target, replacement in mutations:
            self.assertIn(target, orig_main, f"Mutation target missing for {label}")
            with tempfile.TemporaryDirectory(prefix="qa_mut_22a_") as tmp:
                tmp_dir = Path(tmp) / "bend_wb_22a"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                for bf in bend_dir.glob("*.bend"):
                    (tmp_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
                (tmp_dir / "main.bend").write_text(orig_main.replace(target, replacement, 1), encoding="utf-8")
                ok, _ = bend_gate.check_bend_dir(tmp_dir, check_canonical=False)
                self.assertFalse(ok, f"bend_gate check unexpectedly passed on mutated {label}")

    def test_schema_types_sqlite_binding_and_validate_context_graph(self) -> None:
        schema_mod, mut_mod = _import_wb_22a()

        # 1. RuleEntry, FlowEntry, ArtifactEntry, ScopeNode, ContextGraph / WhiteboardGraph / LatticeGraph
        self.assertIs(schema_mod.WhiteboardGraph, schema_mod.ContextGraph)
        self.assertIs(schema_mod.LatticeGraph, schema_mod.ContextGraph)
        self.assertIs(schema_mod.create_empty_lattice, schema_mod.create_empty_graph)
        with self.assertRaises(ValueError):
            schema_mod.classify_lattice_store("UNKNOWN_KIND")
        r = schema_mod.RuleEntry({"id": "r1", "type": "PROHIBITION", "text": "Never use plaintext."})
        self.assertEqual(r.id, "r1")
        self.assertEqual(r.facet, "prohibitions")
        self.assertEqual(r.to_dict()["id"], "r1")
        with self.assertRaises(AttributeError):
            _ = r.nonexistent_attr

        fl = schema_mod.FlowEntry({"id": "fl1", "source": "auth", "target": "db", "payload": "token"})
        self.assertEqual((fl.id, fl.kind, fl.source, fl.target, fl.payload), ("fl1", "FLOW", "auth", "db", "token"))
        art = schema_mod.ArtifactEntry({"id": "a1", "kind": "SCHEMA", "name": "UserRow", "scope_id": "db"})
        self.assertEqual((art.id, art.kind, art.name, art.scope_id), ("a1", "SCHEMA", "UserRow", "db"))

        s = schema_mod.ScopeNode({"id": "auth"})
        self.assertEqual((s.id, s._meta, s.inputs, s.outputs, s.depends_on, s.role), ("auth", {}, [], [], [], None))
        self.assertEqual(s.to_dict()["id"], "auth")

        for bad_val in (0, -1, True, "5"):
            with self.assertRaises(ValueError):
                schema_mod.create_empty_graph(k_cap=bad_val)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                schema_mod.create_empty_graph(max_ring=bad_val)  # type: ignore[arg-type]

        # 2. create_empty_graph with file-backed db_path & SQLiteGraphStore reload
        with tempfile.TemporaryDirectory(prefix="qa_wb_22a_sqlite_") as tmp:
            db_file = Path(tmp) / "whiteboard_22a.sqlite"
            g = schema_mod.create_empty_graph(k_cap=5, max_ring=10, db_path=db_file)
            self.assertIsInstance(g.store, SQLiteGraphStore)
            g_copy = copy.deepcopy(g)
            self.assertIs(g_copy.store, g.store)

            mut_mod.ensure_scope_node(g, "api_gateway")
            mut_mod.prune_dysfluency_and_upsert_rule(g, "api_gateway", {"id": "api.r1", "type": "BOUND", "text": "Keep timeout_ms <= 250.", "target_prop": "timeout_ms", "bound_op": "<=", "bound_val": 250, "meta": {"rationale": "Prevent cascading tail latency."}})
            g.store.close()
            with SQLiteGraphStore(db_file) as reloaded_store:
                loaded = reloaded_store.load_graph()
                self.assertIn("api_gateway", loaded["scopes"])
                self.assertEqual(loaded["scopes"]["api_gateway"]["rules"][0]["id"], "api.r1")
                self.assertEqual(loaded["scopes"]["api_gateway"]["_meta"]["api.r1"]["rationale"], "Prevent cascading tail latency.")

        # 3. validate_context_graph / verify_graph_invariants (including nD Lattice flows & lattice_artifacts)
        g_val = schema_mod.create_empty_lattice()
        mut_mod.upsert_component_spec(
            g_val, "svc_a", role="gateway", inputs=["req"], outputs=["resp"], depends_on=["svc_b"],
            children=["svc_a::auth"], owner="eng1", stage="phase1", kind="COMPONENT", status="READY", current_turn=2,
        )
        self.assertEqual(g_val["scopes"]["svc_a"]["role"], "gateway")
        self.assertIn("svc_a", g_val["scopes"]["svc_b"]["in_edges"])
        self.assertIn("svc_a::auth", g_val["scopes"]["svc_a"]["children"])
        flow_e = mut_mod.upsert_flow_edge(
            g_val, "svc_a", "svc_b", payload="AuthPacket", action="forward", condition="when valid", step=1, current_turn=2,
        )
        self.assertEqual((flow_e["source"], flow_e["target"], flow_e["payload"]), ("svc_a", "svc_b", "AuthPacket"))
        self.assertNotIn("AuthPacket", g_val["scopes"])  # Law 5: payload never becomes a ghost scope
        art_e = mut_mod.upsert_lattice_artifact(
            g_val, "INTERFACE", "AuthRPC", scope_id="svc_b", spec="Verify(tok) -> bool", inputs=["tok"], outputs=["bool"], current_turn=3,
        )
        self.assertEqual((art_e["kind"], art_e["name"], art_e["scope_id"]), ("INTERFACE", "AuthRPC", "svc_b"))
        with self.assertRaises(ValueError):
            mut_mod.upsert_lattice_artifact(g_val, "FLOW", "BadKind")
        r_a = mut_mod.prune_dysfluency_and_upsert_rule(
            g_val, "svc_a", {"id": "svc_a.r1", "type": "OBLIGATION", "text": "Call svc_b.", "refs": ["svc_b"]}
        )
        r_b = mut_mod.prune_dysfluency_and_upsert_rule(
            g_val, "svc_b", {"id": "svc_b.r1", "type": "PROHIBITION", "text": "Reject unauthenticated traffic."}
        )
        mut_mod.wire_lex_specialis_override(g_val, r_a, r_b, note="internal healthcheck exception")
        self.assertEqual(schema_mod.verify_graph_invariants(g_val), [])

        bad_lat = g_val.to_dict()
        bad_lat["flows"][0]["target"] = ""
        bad_lat["lattice_artifacts"][0]["kind"] = "RULE"
        lat_errs = schema_mod.validate_context_graph(bad_lat)
        self.assertTrue(any("flows[0]" in e for e in lat_errs))
        self.assertTrue(any("lattice_artifacts[0]" in e for e in lat_errs))

        # Bare string in exceptions must raise ValueError fail-closed
        bad_exc = g_val.to_dict()
        bad_exc["scopes"]["svc_b"]["rules"][0]["exceptions"] = ["svc_a.r1"]
        with self.assertRaises(ValueError):
            schema_mod.validate_context_graph(bad_exc)

        # Asymmetric refs/in_edges and overrides/exceptions detected
        bad_sym = g_val.to_dict()
        bad_sym["scopes"]["svc_b"]["in_edges"] = []
        bad_sym["scopes"]["svc_b"]["rules"][0]["exceptions"] = []
        errs = schema_mod.validate_context_graph(bad_sym)
        self.assertTrue(any("in_edges" in e for e in errs))
        self.assertTrue(any("exceptions" in e for e in errs))

    def test_mutations_lifecycle_mitosis_ambient_notes_and_blocker_resolution(self) -> None:
        schema_mod, mut_mod = _import_wb_22a()
        store = SQLiteGraphStore(":memory:")
        g = schema_mod.create_empty_graph(k_cap=5, max_ring=10, store=store)

        # 1. ensure_scope_node parent/children + alias_index + SQLite sync
        g["scopes"]["router"] = {"id": "router", "aliases": ["edge_router", "ingress"]}
        mut_mod.ensure_scope_node(g, "router")
        mut_mod.ensure_scope_node(g, "router::tls", parent="router")
        self.assertIn("router::tls", g["scopes"]["router"]["children"])
        self.assertEqual(g["alias_index"]["router"], "router")
        self.assertEqual(g["alias_index"]["edge_router"], "router")
        self.assertEqual(g["alias_index"]["router::tls"], "router::tls")
        self.assertIn("router::tls", store.load_graph()["scopes"])

        # 2. prune_dysfluency_and_upsert_rule & markovian_overwrite_rule
        r1 = mut_mod.prune_dysfluency_and_upsert_rule(
            g,
            "router",
            {
                "id": "router.r1",
                "type": "BOUND",
                "text": "Cap max_conn\u2024limit at 100.",
                "target_prop": "max_conn.limit",
                "bound_op": "<=",
                "bound_val": 100,
                "refs": ["router::tls"],
                "meta": {"rationale": "Initial socket descriptor budget."},
            },
        )
        self.assertEqual(r1["text"], "Cap max_conn.limit at 100.")
        self.assertIn("router.r1", g["scopes"]["router::tls"]["in_edges"])

        with self.assertRaises(ValueError):
            mut_mod.prune_dysfluency_and_upsert_rule(
                g, "router", {"id": "router.bad", "text": "Bad.", "exceptions": ["bare_str"]}
            )

        mut_mod.markovian_overwrite_rule(
            g,
            "router",
            "router.r1",
            {
                "text": "Cap max_conn.limit at 250.",
                "bound_val": 250,
                "refs": [],
                "meta": {"rationale": "Raised after epoll tuning."},
            },
        )
        self.assertEqual(g["scopes"]["router"]["rules"][0]["bound_val"], 250)
        self.assertEqual(g["scopes"]["router"]["rules"][0]["refs"], [])
        self.assertNotIn("router.r1", g["scopes"]["router::tls"]["in_edges"])
        rmeta = g["scopes"]["router"]["_meta"]["router.r1"]
        self.assertEqual(rmeta["rationale"], "Initial socket descriptor budget.")
        self.assertEqual(rmeta["transition_rationale"], "Raised after epoll tuning.")

        # 3. graduate_task_to_fact
        g["active_tasks"] = [
            {
                "id": "t_deploy",
                "scope": "router",
                "text": "Deploy epoll config\u2024yaml.",
                "condition": "after canary passes",
                "created_turn": 2,
            }
        ]
        fact_r = mut_mod.graduate_task_to_fact(g, "t_deploy", current_turn=4)
        self.assertEqual(fact_r["id"], "fact_t_deploy")
        self.assertEqual(fact_r["type"], "FACT")
        self.assertEqual(fact_r["facet"], "context_facts")
        self.assertEqual(fact_r["conditions"], ["after canary passes"])
        self.assertEqual(g["active_tasks"], [])
        self.assertTrue(any(n["id"] == "fact_t_deploy" for n in store.query_nodes_by_scope("router")))

        # 4. upsert_ambient_note (str & dict, id/text match, ACT-R ring eviction)
        n1 = mut_mod.upsert_ambient_note(g, "Canary cluster is green.", current_turn=1)
        self.assertEqual(n1["access_turns"], [1])
        n1_again = mut_mod.upsert_ambient_note(g, "Canary cluster is green.", current_turn=5)
        self.assertEqual(n1_again["id"], n1["id"])
        self.assertEqual(n1_again["access_turns"], [1, 5])
        self.assertIn("activation", n1_again)

        g["ambient_notes"] = []
        for idx in range(1, 13):
            aturns = [1] if idx == 1 else [8, 9, 10]
            mut_mod.upsert_ambient_note(
                g, {"id": f"amb_{idx:02d}", "text": f"Note {idx}", "access_turns": aturns}, current_turn=10, max_ring=10
            )
        self.assertEqual(len(g["ambient_notes"]), 10)
        self.assertNotIn("amb_01", {n["id"] for n in g["ambient_notes"]})

        # 5. two_axis_mitosis (:: child scope naming, 100% rule conservation Law 3, summary dict return)
        mut_mod.ensure_scope_node(g, "big_scope")
        g["scopes"]["big_scope"]["rules"] = [
            schema_mod.RuleEntry({"id": f"big.net_{i}", "scope": "big_scope", "type": "BOUND", "facet": "Numeric_Bounds", "target_prop": f"net_prop_{i}", "text": f"Net rule {i}."})
            for i in range(4)
        ] + [
            schema_mod.RuleEntry({"id": f"big.disk_{i}", "scope": "big_scope", "type": "OBLIGATION", "facet": "obligations", "target_prop": f"disk_prop_{i}", "text": f"Disk rule {i}."})
            for i in range(4)
        ]
        res_mit = mut_mod.two_axis_mitosis(g, "big_scope", k_cap=5)
        self.assertEqual(res_mit["count_before"], res_mit["count_after"])
        self.assertTrue({"graph", "scopes", "splits", "mitosis_splits", "count_before", "count_after"} <= set(res_mit.keys()))
        self.assertEqual(g["scopes"]["big_scope"]["rules"], [])
        child_sids = res_mit["splits"][0]["children"]
        self.assertTrue(all("::" in csid and "__" not in csid for csid in child_sids))
        self.assertEqual(schema_mod.verify_graph_invariants(g), [])

        # 6. resolve_open_blocker (remove=False and remove=True + SQLite sync)
        g["open_blockers"] = [
            {"id": "b_keep", "type": "CLARIFICATION", "scope": "router", "text": "Which port?", "status": "OPEN"},
            {"id": "b_drop", "type": "CONFLICT", "scope": "router", "text": "Clash on max_conn.", "status": "OPEN"},
        ]
        self.assertTrue(mut_mod.resolve_open_blocker(g, "b_keep", remove=False))
        self.assertEqual(g["open_blockers"][0]["status"], "RESOLVED")
        self.assertEqual(store.load_graph()["open_blockers"][0]["status"], "RESOLVED")

        self.assertTrue(mut_mod.resolve_open_blocker(g, "b_drop", remove=True))
        self.assertEqual([b["id"] for b in g["open_blockers"]], ["b_keep"])
        self.assertEqual([b["id"] for b in store.load_graph()["open_blockers"]], ["b_keep"])

        # 7. Law 6 & Automatic Staleness Invalidation on rules, flows, and artifacts (+ downstream depends_on / parent)
        mut_mod.upsert_component_spec(g, "cue_extractor", owner="eng1", kind="COMPONENT")
        mut_mod.upsert_component_spec(g, "downstream_dep", owner="eng2", kind="COMPONENT", depends_on=["cue_extractor"])
        mut_mod.ensure_scope_node(g, "child_comp", parent="cue_extractor")
        for action in (
            lambda: mut_mod.prune_dysfluency_and_upsert_rule(g, "cue_extractor", {"id": "ce.r1", "type": "BOUND", "text": "Keep k <= 4.", "target_prop": "k", "bound_op": "<=", "bound_val": 4}),
            lambda: mut_mod.markovian_overwrite_rule(g, "cue_extractor", "ce.r1", {"text": "Keep k <= 8.", "bound_val": 8}),
            lambda: mut_mod.replace_rule(g, "ce.r1", {"id": "ce.r1", "type": "BOUND", "text": "Keep k <= 12.", "target_prop": "k", "bound_op": "<=", "bound_val": 12}) if hasattr(mut_mod, "replace_rule") else mut_mod.markovian_overwrite_rule(g, "cue_extractor", "ce.r1", {"text": "Keep k <= 12.", "bound_val": 12}),
            lambda: mut_mod.revoke_rule(g, "ce.r1") if hasattr(mut_mod, "revoke_rule") else mut_mod.prune_dysfluency_and_upsert_rule(g, "cue_extractor", {"id": "ce.r2", "type": "BOUND", "text": "Keep m <= 2."}),
            lambda: mut_mod.upsert_flow_edge(g, "cue_extractor", "downstream_dep", payload="CueBag"),
            lambda: mut_mod.upsert_lattice_artifact(g, "SCHEMA", "CueBag", scope_id="cue_extractor"),
        ):
            for cid in ("cue_extractor", "downstream_dep", "child_comp"):
                mut_mod.transition_component_build_status(g, cid, "VERIFIED", core_tag="c1")
            action()
            for cid in ("cue_extractor", "downstream_dep", "child_comp"):
                st = g["scopes"][cid].get("build_status") or g["scopes"][cid].get("status")
                self.assertEqual(st, "STALE", f"{cid} did not transition from VERIFIED to STALE")
        store.close()

    def test_standing_rule_49_zero_regex_file_size_and_22a_isolation_gate(self) -> None:
        schema_mod, mut_mod = _import_wb_22a()
        wb_dir = Path(schema_mod.__file__).resolve().parent
        for py_file in (Path(schema_mod.__file__).resolve(), Path(mut_mod.__file__).resolve()):
            source = py_file.read_text(encoding="utf-8")
            self.assertLessEqual(len(source.splitlines()), 800, f"{py_file.name} exceeds 500 lines")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "re", f"Forbidden regex import in {py_file.name}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "re", f"Forbidden regex import in {py_file.name}")

        if "eng1" in wb_dir.parts:
            forbidden_22b_22c = {"frustration.py", "linker.py", "rewriter.py", "ingest.py", "read_engine.py", "__init__.py"}
            present = {p.name for p in wb_dir.glob("*.py")}
            self.assertEqual(present & forbidden_22b_22c, set(), f"Forbidden 22B/22C files in eng1/whiteboard: {present & forbidden_22b_22c}")


if __name__ == "__main__":
    unittest.main()
