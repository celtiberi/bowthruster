"""Board schema versioning, forward migrations (`v0 -> v1 -> v2`), and bolt-on session tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from bowthruster import open_board_session
from bowthruster.graph_core import SQLiteGraphStore
from bowthruster.mcp_server import call_lattice_tool
from bowthruster.regiment import parse_cond_atoms
from bowthruster.whiteboard import (
    BOARD_SCHEMA_VERSION,
    BoardSchemaVersionError,
    ContextGraph,
    detect_board_schema_version,
    migrate_board_data,
)


class TestBoardMigrationsAndSession(unittest.TestCase):
    """Tests persistent board schema migrations, forward-compatibility guards, and bolt-on sessions."""

    def test_01_bolt_on_board_session_and_parse_cond_atoms(self) -> None:
        atoms, raw = parse_cond_atoms(["tier == vip and amount >= 100", "is_active"])
        self.assertEqual(atoms["tier"], [("==", "vip")])
        self.assertEqual(atoms["amount"], [(">=", 100)])
        self.assertEqual(raw, {"is_active"})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".whiteboard").mkdir()
            with open_board_session("ext_b", project_root=root, save_on_exit=True) as (g, r):
                self.assertEqual(r, root.resolve())
                g.setdefault("scopes", {})["demo"] = {"status": "STALE"}
            with open_board_session("ext_b", project_root=root, save_on_exit=False) as (g2, _):
                self.assertEqual(g2["scopes"]["demo"]["status"], "STALE")

    def test_02_board_schema_versioning_migrations_and_forward_guard(self) -> None:
        v0_raw = {
            "scopes": {
                "auth": {
                    "build_status": "verified",
                    "claimed_by": "stale_agent",
                    "files": ["src/auth.py", "tests:tests/test_auth.py"],
                    "test_files": ["tests/test_extra.py"],
                    "attributes": {"test_files": ["tests/test_attr.py"]},
                }
            },
            "external_links": [{"node_id": "auth", "uri": "file://auth", "kind": "doc"}],
        }
        self.assertEqual(detect_board_schema_version(v0_raw), 0)
        migrated, report = migrate_board_data(v0_raw, board_name="v0_demo")
        self.assertTrue(report.migrated)
        self.assertEqual((report.from_version, report.to_version), (0, BOARD_SCHEMA_VERSION))
        self.assertEqual(len(report.steps_applied), 2)
        self.assertEqual(migrated["schema_version"], BOARD_SCHEMA_VERSION)
        auth_sc = migrated["scopes"]["auth"]
        self.assertEqual(auth_sc["files"], ["src/auth.py"])
        self.assertEqual(auth_sc["tests"], ["tests/test_auth.py", "tests/test_extra.py"])
        self.assertNotIn("test_files", auth_sc)
        self.assertNotIn("test_files", auth_sc["attributes"])
        self.assertIsNone(auth_sc["claimed_by"])
        self.assertIn("auth", migrated["external_links"])

        with SQLiteGraphStore(":memory:") as mem_db:
            cg = ContextGraph(migrated, store=mem_db)
            mem_db.save_graph(cg)
            reloaded = mem_db.load_graph()
            self.assertEqual(reloaded["schema_version"], BOARD_SCHEMA_VERSION)
            self.assertEqual(len(reloaded["schema_migrations"]), 2)

        for bad_ver in (-1, "2", True):
            with self.assertRaises(BoardSchemaVersionError):
                detect_board_schema_version({"schema_version": bad_ver})
        with self.assertRaises(BoardSchemaVersionError):
            migrate_board_data({"schema_version": BOARD_SCHEMA_VERSION + 5}, board_name="future_b")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wb_dir = root / ".whiteboard"
            wb_dir.mkdir()
            leg_p = wb_dir / "leg_b.json"
            leg_p.write_text(
                json.dumps({
                    "version": 1,
                    "charter": {"name": "leg_b", "summary": "Legacy board", "principles": "Clean"},
                    "scopes": {"core": {"status": "VERIFIED", "files": ["a.py"], "test_files": ["t.py"]}},
                }),
                encoding="utf-8",
            )
            at_pre = call_lattice_tool("atlas", {"board": "leg_b", "project_root": str(root)})["structuredContent"]
            self.assertTrue(at_pre["atlas_metrics"]["board_schema_versions"]["leg_b"]["needs_migration"])
            self.assertIn("NEEDS_MIGRATION", at_pre["atlas_schematic"])

            dry = call_lattice_tool(
                "migrate", {"board": "leg_b", "project_root": str(root), "dry_run": True}
            )["structuredContent"]
            self.assertEqual(dry["migrated_count"], 1)
            self.assertNotIn("schema_version", json.loads(leg_p.read_text(encoding="utf-8")))

            app = call_lattice_tool("migrate", {"migrate_all": True, "project_root": str(root)})["structuredContent"]
            self.assertEqual(app["migrated_count"], 1)
            on_disk = json.loads(leg_p.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["schema_version"], BOARD_SCHEMA_VERSION)
            self.assertEqual(on_disk["scopes"]["core"]["tests"], ["t.py"])

            idem = call_lattice_tool("migrate", {"board": "leg_b", "project_root": str(root)})["structuredContent"]
            self.assertEqual(idem["migrated_count"], 0)
            at_post = call_lattice_tool("atlas", {"board": "leg_b", "project_root": str(root)})["structuredContent"]
            self.assertFalse(at_post["atlas_metrics"]["board_schema_versions"]["leg_b"]["needs_migration"])
            self.assertIn("[schema=v2]", at_post["atlas_schematic"])

            fut_p = wb_dir / "fut_b.json"
            fut_raw = json.dumps({"schema_version": 99, "scopes": {}})
            fut_p.write_text(fut_raw, encoding="utf-8")
            fut_res = call_lattice_tool("atlas", {"board": "fut_b", "project_root": str(root)})
            self.assertTrue(fut_res["isError"])
            self.assertIn("newer than supported", fut_res["structuredContent"]["error"])
            self.assertEqual(fut_p.read_text(encoding="utf-8"), fut_raw)


if __name__ == "__main__":
    unittest.main()
