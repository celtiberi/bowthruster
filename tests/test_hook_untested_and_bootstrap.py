"""Integration tests for Git pre-commit hooks, bootstrap law wiring, untested status, and gate config."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bowthruster.cli import main as run_cli
from bowthruster.mcp_server import call_lattice_tool

_VENV_BIN = str(Path(sys.executable).parent)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    merged_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "qa",
        "GIT_AUTHOR_EMAIL": "qa@example.com",
        "GIT_COMMITTER_NAME": "qa",
        "GIT_COMMITTER_EMAIL": "qa@example.com",
        **(env or {}),
    }
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        env=merged_env,
        text=True,
        capture_output=True,
        check=check,
    )


class TestHookUntestedAndBootstrap(unittest.TestCase):
    """Covers Fable review findings #1–#3, #5–#7, and #10 end-to-end."""

    def test_git_precommit_hook_rejects_broken_verified_and_missing_board(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bt_hook_e2e_") as tmp:
            repo = Path(tmp)
            _git(repo, "init")
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "tests").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "calc.py").write_text(
                "def add_pos(x: int, y: int) -> int:\n    return x + y\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_calc.py").write_text(
                "from src.calc import add_pos\n\ndef test_add_pos() -> None:\n    assert add_pos(2, 3) == 5\n",
                encoding="utf-8",
            )
            turn_md = (
                "| Charter | Summary | Principles |\n"
                "| :--- | :--- | :--- |\n"
                "| calc_board | Positive adder | Explicit contracts |\n\n"
                "| Component | Role | Why | Owner | Stage | Depends_On | Files | Tests |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                "| calc | Adder module | Computes sums | dev | 1 | none | src/calc.py | tests/test_calc.py |\n\n"
                "| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                "| add_pos | calc | x: int, y: int | int | dev | 1 | src/calc.py | Return x + y |\n"
            )
            call_lattice_tool(
                "turn",
                {"board": "calc_board", "project_root": str(repo), "message": turn_md},
            )
            pre0 = call_lattice_tool(
                "git_sync",
                {"board": "calc_board", "project_root": str(repo), "mode": "pre", "mark_verified": True},
            )["structuredContent"]
            self.assertTrue(pre0["allowed"])
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "initial verified baseline")
            call_lattice_tool(
                "git_sync",
                {"board": "calc_board", "project_root": str(repo), "mode": "post"},
            )
            _git(repo, "add", ".whiteboard/calc_board.json")
            _git(repo, "commit", "-m", "stamp baseline sha")

            call_lattice_tool(
                "git_sync",
                {"board": "calc_board", "project_root": str(repo), "mode": "install_hook"},
            )
            hook_path = repo / ".git" / "hooks" / "pre-commit"
            hook_txt = hook_path.read_text(encoding="utf-8")
            self.assertIn("BOWTHRUSTER_PROJECT_ROOT", hook_txt)
            self.assertIn("--project-root", hook_txt)

            head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
            (repo / "src" / "calc.py").write_text(
                "def add_pos(x: int, y: int) -> int:\n    return x - y\n",
                encoding="utf-8",
            )
            _git(repo, "add", "src/calc.py")
            hook_env = {"PATH": f"{_VENV_BIN}:/usr/bin:/bin"}
            bad_commit = subprocess.run(
                ["git", "commit", "-m", "break verified file without stale"],
                cwd=str(repo / "src"),
                env={
                    **os.environ,
                    "GIT_AUTHOR_NAME": "qa",
                    "GIT_AUTHOR_EMAIL": "qa@example.com",
                    "GIT_COMMITTER_NAME": "qa",
                    "GIT_COMMITTER_EMAIL": "qa@example.com",
                    **hook_env,
                },
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(bad_commit.returncode, 0)
            self.assertIn("REJECT_COMMIT", bad_commit.stdout + bad_commit.stderr)
            self.assertEqual(_git(repo, "rev-parse", "HEAD").stdout.strip(), head_before)

            # Repo .venv/bin/python without bowthruster falls through to bowthruster on PATH
            (repo / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            bare_py = repo / ".venv" / "bin" / "python"
            bare_py.write_text(
                f'#!/usr/bin/env sh\nexec "{sys.executable}" -S "$@"\n',
                encoding="utf-8",
            )
            bare_py.chmod(0o755)
            fallback_commit = _git(
                repo,
                "commit",
                "-m",
                "still rejected via PATH bowthruster",
                env=hook_env,
                check=False,
            )
            self.assertNotEqual(fallback_commit.returncode, 0)
            self.assertIn("REJECT_COMMIT", fallback_commit.stdout + fallback_commit.stderr)

            # Missing bowthruster everywhere fails open (exit 0) instead of wedging unrelated repos
            fail_open = subprocess.run(
                [str(hook_path)],
                cwd=str(repo),
                env={"PATH": "/usr/bin:/bin"},
                text=True,
                capture_output=True,
            )
            self.assertEqual(fail_open.returncode, 0)
            self.assertIn("failing open", fail_open.stderr)
            bare_py.unlink()

            # Hook bound to a non-existent board refuses commit with MISSING_BOARD
            call_lattice_tool(
                "git_sync",
                {"board": "ghost_board", "project_root": str(repo), "mode": "install_hook"},
            )
            missing_board_commit = _git(
                repo,
                "commit",
                "-m",
                "commit against missing board",
                env=hook_env,
                check=False,
            )
            self.assertNotEqual(missing_board_commit.returncode, 0)
            self.assertIn("MISSING_BOARD", missing_board_commit.stdout + missing_board_commit.stderr)

            # Restore calc_board hook, mark STALE, fix code, auto-verify, and pass real git commit
            call_lattice_tool(
                "git_sync",
                {"board": "calc_board", "project_root": str(repo), "mode": "install_hook"},
            )
            call_lattice_tool(
                "slice",
                {"board": "calc_board", "project_root": str(repo), "component": "calc", "mark_stale": True},
            )
            (repo / "src" / "calc.py").write_text(
                "def add_pos(x: int, y: int) -> int:\n    return int(x) + int(y)\n",
                encoding="utf-8",
            )
            pre_ok = call_lattice_tool(
                "git_sync",
                {"board": "calc_board", "project_root": str(repo), "mode": "pre", "mark_verified": True},
            )["structuredContent"]
            self.assertTrue(pre_ok["allowed"])
            _git(repo, "add", "src/calc.py", ".whiteboard/calc_board.json")
            good_commit = _git(repo, "commit", "-m", "verified update", env=hook_env, check=False)
            self.assertEqual(good_commit.returncode, 0, msg=good_commit.stdout + good_commit.stderr)
            self.assertNotEqual(_git(repo, "rev-parse", "HEAD").stdout.strip(), head_before)

    def test_bootstrap_preserves_law_wiring_checks_against_cheats(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bt_boot_law_") as tmp:
            repo = Path(tmp)
            _git(repo, "init")
            # Empty repo edge case
            empty_boot = call_lattice_tool(
                "bootstrap",
                {"board": "empty_b", "project_root": str(repo)},
            )["structuredContent"]
            self.assertEqual(empty_boot["candidate_component_count"], 0)

            # Syntax-error resilience on a throwaway board
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "broken_syntax.py").write_text("def oops(:\n    pass\n", encoding="utf-8")
            (repo / "src" / "engine.py").write_text(
                "def is_safe_dispatch(has_auth: bool, within_quota: bool) -> bool:\n"
                "    return bool(has_auth and within_quota)\n\n"
                "def dispatch_job(user: str, quota_left: int) -> bool:\n"
                "    return is_safe_dispatch(bool(user), quota_left > 0)\n",
                encoding="utf-8",
            )
            syn_boot = call_lattice_tool(
                "bootstrap",
                {"board": "syntax_b", "project_root": str(repo)},
            )["structuredContent"]
            self.assertGreaterEqual(syn_boot["candidate_component_count"], 1)
            (repo / "src" / "broken_syntax.py").unlink()

            boot_all = call_lattice_tool(
                "bootstrap",
                {"board": "boot_law", "project_root": str(repo)},
            )["structuredContent"]
            comp_id = boot_all["onboarding_packets"][0]["component"]
            boot = call_lattice_tool(
                "bootstrap",
                {"board": "boot_law", "project_root": str(repo), "component": comp_id},
            )["structuredContent"]
            self.assertEqual(len(boot["onboarding_packets"]), 1)

            law_turn = (
                "| Charter | Summary | Principles |\n"
                "| :--- | :--- | :--- |\n"
                "| boot_law | Job dispatcher | Gate dispatches with safety law |\n\n"
                "| Component | Role | Why | Owner | Stage | Depends_On | Files |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                f"| {comp_id} | Job dispatch engine | Enforces auth and quota before dispatch | dev | 1 | none | src/engine.py |\n\n"
                "| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                f"| dispatch_job | {comp_id} | user: str, quota_left: int | bool | dev | 1 | src/engine.py | Check is_safe_dispatch and return allow flag |\n\n"
                "| Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                f"| safe_dispatch_law | {comp_id} | dev | 1 | laws | has_auth: Bool, within_quota: Bool | MOD.is_safe_dispatch(has_auth, within_quota) | Bool.and(has_auth, within_quota) | Allow iff authenticated and within quota |\n"
            )
            call_lattice_tool(
                "turn",
                {"board": "boot_law", "project_root": str(repo), "message": law_turn},
            )

            cheats = {
                "never_called": "    return bool(user) and quota_left > 0\n",
                "discarded_result": "    _ = is_safe_dispatch(bool(user), quota_left > 0)\n    return True\n",
                "duplicate_args": "    return is_safe_dispatch(bool(user), bool(user))\n",
                "all_constants": "    return is_safe_dispatch(True, False)\n",
            }
            header = (
                "def is_safe_dispatch(has_auth: bool, within_quota: bool) -> bool:\n"
                "    return bool(has_auth and within_quota)\n\n"
                "def dispatch_job(user: str, quota_left: int) -> bool:\n"
            )
            for cheat_name, body in cheats.items():
                (repo / "src" / "engine.py").write_text(header + body, encoding="utf-8")
                res = call_lattice_tool(
                    "git_sync",
                    {"board": "boot_law", "project_root": str(repo), "mode": "pre"},
                )["structuredContent"]
                self.assertFalse(res["allowed"], f"Bootstrapped cheat {cheat_name} must be rejected")
                self.assertFalse(res["call_graph_skeleton_ok"], f"Cheat {cheat_name} passed call graph")

            (repo / "src" / "engine.py").write_text(
                header + "    return is_safe_dispatch(bool(user), quota_left > 0)\n",
                encoding="utf-8",
            )
            clean_res = call_lattice_tool(
                "git_sync",
                {"board": "boot_law", "project_root": str(repo), "mode": "pre"},
            )["structuredContent"]
            self.assertTrue(clean_res["allowed"])
            self.assertTrue(clean_res["call_graph_skeleton_ok"])

    def test_untested_reporting_consistency_and_undeclared_test_warnings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bt_untested_") as tmp:
            repo = Path(tmp)
            _git(repo, "init")
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "tests").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "tested_mod.py").write_text(
                "def run_tested(x: int) -> int:\n    return x * 2\n",
                encoding="utf-8",
            )
            (repo / "src" / "untested_mod.py").write_text(
                "from src.tested_mod import run_tested\n\n"
                "def run_untested(x: int) -> int:\n    return run_tested(x) + 1\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_comp_tested.py").write_text(
                "from src.tested_mod import run_tested\n\ndef test_ok() -> None:\n    assert run_tested(3) == 6\n",
                encoding="utf-8",
            )
            # Disk test file matching comp_untested that was NOT declared on the board
            (repo / "tests" / "test_comp_untested.py").write_text(
                "def test_undeclared() -> None:\n    assert False\n",
                encoding="utf-8",
            )
            md = (
                "| Charter | Summary | Principles |\n"
                "| :--- | :--- | :--- |\n"
                "| mix_b | Mixed test coverage board | Honest test reporting |\n\n"
                "| Component | Role | Why | Owner | Stage | Depends_On | Files | Tests |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                "| comp_tested | Tested worker | Has declared pytest file | dev | 1 | none | src/tested_mod.py | tests/test_comp_tested.py |\n"
                "| comp_untested | Untested worker | No declared pytest file | dev | 1 | comp_tested | src/untested_mod.py | |\n\n"
                "| Flow | Source | Target | Payload | Condition |\n"
                "| :--- | :--- | :--- | :--- | :--- |\n"
                "| feed | comp_tested | comp_untested | int | always |\n\n"
                "| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                "| run_tested | comp_tested | x: int | int | dev | 1 | src/tested_mod.py | Double x |\n"
                "| run_untested | comp_untested | x: int | int | dev | 1 | src/untested_mod.py | Increment x |\n"
            )
            call_lattice_tool("turn", {"board": "mix_b", "project_root": str(repo), "message": md})
            pre = call_lattice_tool(
                "git_sync",
                {"board": "mix_b", "project_root": str(repo), "mode": "pre", "mark_verified": True},
            )["structuredContent"]
            self.assertTrue(pre["allowed"])
            self.assertEqual(pre["delta_tests_status"], "PARTIAL_UNTESTED")
            self.assertEqual(pre["untested_scopes"], ["comp_untested"])
            self.assertEqual(
                pre["undeclared_matching_tests"],
                {"comp_untested": ["tests/test_comp_untested.py"]},
            )
            self.assertTrue(any("test_comp_untested.py" in w for w in pre["warnings"]))

            atlas_res = call_lattice_tool(
                "atlas",
                {"board": "mix_b", "project_root": str(repo)},
            )["structuredContent"]
            self.assertEqual(atlas_res["verified_components"], 1)
            self.assertEqual(atlas_res["verified_untested_components"], 1)
            self.assertIn("verified=1/2", atlas_res["atlas_schematic"])
            self.assertIn("untested_verified=1", atlas_res["atlas_schematic"])
            self.assertIn("status=VERIFIED (untested)", atlas_res["atlas_schematic"])

            # Verify MCP and CLI status surface print VERIFIED (untested) + warning
            call_lattice_tool(
                "status",
                {
                    "board": "mix_b",
                    "project_root": str(repo),
                    "component": "comp_untested",
                    "new_status": "STALE",
                },
            )
            st_mcp = call_lattice_tool(
                "status",
                {
                    "board": "mix_b",
                    "project_root": str(repo),
                    "component": "comp_untested",
                    "new_status": "VERIFIED",
                },
            )
            st_txt = st_mcp["content"][0]["text"]
            self.assertIn("status=VERIFIED (untested)", st_txt)
            self.assertIn("test_comp_untested.py", st_txt)

            rc = run_cli(
                ["--project-root", str(repo), "--board", "mix_b", "status", "comp_untested", "STALE"]
            )
            self.assertEqual(rc, 0)
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc2 = run_cli(
                    ["--project-root", str(repo), "--board", "mix_b", "status", "comp_untested", "VERIFIED"]
                )
            self.assertEqual(rc2, 0)
            self.assertIn("status=VERIFIED (untested)", buf.getvalue())
            self.assertIn("test_comp_untested.py", buf.getvalue())

    def test_require_call_graph_laws_config_toggle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bt_cfg_laws_") as tmp:
            repo = Path(tmp)
            _git(repo, "init")
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "guard.py").write_text(
                "def can_proceed(a: bool, b: bool) -> bool:\n"
                "    return bool(a and b)\n\n"
                "def execute_step(a: bool, b: bool) -> bool:\n"
                "    return bool(a and b)\n",
                encoding="utf-8",
            )
            md = (
                "| Charter | Summary | Principles |\n"
                "| :--- | :--- | :--- |\n"
                "| cfg_b | Config law toggle test | Explicit config wiring |\n\n"
                "| Component | Role | Why | Owner | Stage | Depends_On | Files |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                "| guard | Step guard | Tests gate.require_call_graph_laws | dev | 1 | none | src/guard.py |\n\n"
                "| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                "| execute_step | guard | a: bool, b: bool | bool | dev | 1 | src/guard.py | Execute guarded step |\n\n"
                "| Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                "| proceed_law | guard | dev | 1 | laws | a: Bool, b: Bool | MOD.can_proceed(a, b) | Bool.and(a, b) | Both flags required |\n"
            )
            call_lattice_tool("turn", {"board": "cfg_b", "project_root": str(repo), "message": md})

            res_default = call_lattice_tool(
                "git_sync",
                {"board": "cfg_b", "project_root": str(repo), "mode": "pre"},
            )["structuredContent"]
            self.assertFalse(res_default["allowed"])
            self.assertTrue(res_default["call_graph_laws_enforced"])

            (repo / "config.json").write_text(
                json.dumps({"gate": {"require_call_graph_laws": False}}),
                encoding="utf-8",
            )
            res_disabled = call_lattice_tool(
                "git_sync",
                {"board": "cfg_b", "project_root": str(repo), "mode": "pre"},
            )["structuredContent"]
            self.assertTrue(res_disabled["allowed"])
            self.assertFalse(res_disabled["call_graph_laws_enforced"])


if __name__ == "__main__":
    unittest.main()
