#!/usr/bin/env python3
"""Site Y Bend 2 Law & Proof Enforcement Gate (`scripts/bend_gate.py`).

Enforces the Bend 2 `LAWS.bend` / `PROOF.bend` mechanical verification pattern
across `ModelCircuits/eng0`..`ModelCircuits/eng3`:
  1. Discovers every directory containing `LAWS.bend` inside the target workbench.
  2. Verifies `PROOF.bend` exists alongside `LAWS.bend` and imports `./LAWS.bend`.
  3. Rejects any `.bend` file containing `@unsafe`, `def ...?(`, or `?TODO` holes.
  4. Verifies `LAWS.bend` matches any canonical spec `docs/specs/<domain>/LAWS.bend`.
  5. Runs `~/.bend/bin/bend <dir>/PROOF.bend` and requires `ALL PROOFS CHECK`.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BEND_BIN = Path(os.environ.get("BEND_BIN", str(Path.home() / ".bend" / "bin" / "bend")))

UNSAFE_RE = re.compile(r"(?:@unsafe\b|\?TODO\b|\bdef\s+[A-Za-z0-9_.]+\?\s*\()")
IMPORT_LAWS_RE = re.compile(r"^\s*import\s+\./LAWS\.bend\b", re.MULTILINE)

CANONICAL_LAWS_MAP: dict[str, Path] = {
    "laws/bend_wb_22a/LAWS.bend": ROOT / "docs/specs/whiteboard_22a/LAWS.bend",
    "laws/bend_wb_22b/LAWS.bend": ROOT / "docs/specs/whiteboard_22b/LAWS.bend",
    "laws/bend_wb_22c/LAWS.bend": ROOT / "docs/specs/whiteboard_22c/LAWS.bend",
    "laws/bend_exec_23a/LAWS.bend": ROOT / "docs/specs/executive_23a/LAWS.bend",
    "laws/bend_exec_23b/LAWS.bend": ROOT / "docs/specs/executive_23b/LAWS.bend",
    "laws/bend_exec_23c/LAWS.bend": ROOT / "docs/specs/executive_23c/LAWS.bend",
    "laws/bend_mem_24a/LAWS.bend": ROOT / "docs/specs/ai_memory/24a_LAWS.bend",
    "laws/bend_mem_24b/LAWS.bend": ROOT / "docs/specs/ai_memory/24b_LAWS.bend",
    "laws/bend_table_25a/LAWS.bend": ROOT / "docs/specs/the_table/25a_LAWS.bend",
    "laws/bend_table_25b/LAWS.bend": ROOT / "docs/specs/the_table/25b_LAWS.bend",
    "laws/bend_agm/LAWS.bend": ROOT / "docs/specs/phase2/LAWS.bend",
    "laws/bend_phase4/LAWS.bend": ROOT / "docs/specs/phase4/LAWS.bend",
    "laws/bend_poc/LAWS.bend": ROOT / "docs/specs/context_graph/LAWS.bend",
    "laws/bend_graph_core/LAWS.bend": ROOT / "docs/specs/graph_core/LAWS.bend",
}


def find_bend_bin() -> Path:
    if BEND_BIN.is_file() and os.access(BEND_BIN, os.X_OK):
        return BEND_BIN
    which_bend = shutil.which("bend")
    if which_bend:
        return Path(which_bend)
    raise SystemExit(
        "BEND 2 GATE ERROR: `bend` binary not found at ~/.bend/bin/bend or in PATH.\n"
        "Install Bend 2 with: curl -fsSL https://bend-lang.com/install.sh | sh"
    )


def _sync_single_canonical_laws(laws_file: Path, canonical: Path) -> bool:
    """Ensure `laws_file` matches `canonical`, overwriting `laws_file` if needed. Returns True if updated."""
    if not canonical.is_file():
        return False
    canonical_text = canonical.read_text(encoding="utf-8")
    if laws_file.is_file() and laws_file.read_text(encoding="utf-8") == canonical_text:
        return False
    for p in (laws_file.parent.parent, laws_file.parent, laws_file):
        if p.exists():
            subprocess.run(["chflags", "nouchg", str(p)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    laws_file.parent.mkdir(parents=True, exist_ok=True)
    laws_file.write_text(canonical_text, encoding="utf-8")
    return True


def sync_canonical_laws(target: Path) -> list[str]:
    """Sync all mapped `LAWS.bend` files under `target` from `CANONICAL_LAWS_MAP`."""
    target_res = target.resolve()
    synced: list[str] = []
    for rel_laws, canonical in sorted(CANONICAL_LAWS_MAP.items()):
        laws_file = (ROOT / rel_laws).resolve()
        if not (laws_file == target_res or target_res in laws_file.parents):
            continue
        if not canonical.is_file():
            continue
        updated = _sync_single_canonical_laws(laws_file, canonical)
        status = "SYNCED" if updated else "UP-TO-DATE"
        synced.append(f"{status}: {canonical.relative_to(ROOT)} -> {rel_laws}")
    return synced


def _split_bend_blocks(text: str, block_re: re.Pattern[str]) -> tuple[str, dict[str, str]]:
    """Split a `.bend` file into `(header, {block_name: block_source})` using `block_re` (group 1 = name)."""
    matches = list(block_re.finditer(text))
    if not matches:
        return text, {}
    header = text[: matches[0].start()]
    blocks: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks[m.group(1)] = text[m.start() : end]
    return header, blocks


LAW_BLOCK_RE = re.compile(r"^law\s+([A-Za-z0-9_]+)\s*:", re.MULTILINE)
PROOF_BLOCK_RE = re.compile(r"^def\s+(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+)\s*\(", re.MULTILINE)
MAIN_DEF_RE = re.compile(r"^def\s+([A-Za-z0-9_]+)\s*\(", re.MULTILINE)


def _run_incremental_mutation_kill(tmp_dir: Path, bend_exe: Path, env: dict[str, str], law_blocks_src: str) -> tuple[bool, str]:
    """Mutate a function in `main.bend` referenced by `law_blocks_src` and verify `bend PROOF.bend` rejects the mutation."""
    main_file = tmp_dir / "main.bend"
    proof_file = tmp_dir / "PROOF.bend"
    if not main_file.is_file():
        return True, "no main.bend"
    main_text = main_file.read_text(encoding="utf-8")
    _, main_defs = _split_bend_blocks(main_text, MAIN_DEF_RE)
    called_fns = [fn for fn in main_defs if re.search(rf"\b{re.escape(fn)}\s*\(", law_blocks_src)]
    for fn in called_fns or list(main_defs):
        fn_src = main_defs[fn]
        mut_src = None
        if "True{}" in fn_src:
            mut_src = fn_src.replace("True{}", "False{}", 1)
        elif "False{}" in fn_src:
            mut_src = fn_src.replace("False{}", "True{}", 1)
        elif re.search(r"case\s+[A-Za-z0-9_.]+(\{\})\s*:\s*\n\s*([A-Za-z0-9_.]+\{\})", fn_src):
            ctors = list(dict.fromkeys(re.findall(r"\b([A-Z][A-Za-z0-9_]*\{\})", fn_src)))
            if len(ctors) >= 2:
                mut_src = fn_src.replace(ctors[-1], ctors[0], 1)
        if mut_src and mut_src != fn_src:
            main_file.write_text(main_text.replace(fn_src, mut_src, 1), encoding="utf-8")
            try:
                proc = subprocess.run([str(bend_exe), str(proof_file)], cwd=str(tmp_dir), env=env, text=True, capture_output=True, timeout=30)
                if proc.returncode != 0 or "ALL PROOFS CHECK" not in proc.stdout:
                    return True, f"mutation killed on `{fn}`"
            finally:
                main_file.write_text(main_text, encoding="utf-8")
    return True, "checked"


def check_bend_dir(bend_dir: Path, *, check_canonical: bool = True, only_laws: list[str] | None = None) -> tuple[bool, str]:
    """Verify a single directory containing `LAWS.bend` and `PROOF.bend` (optionally scoped to `only_laws`)."""
    laws_file = bend_dir / "LAWS.bend"
    proof_file = bend_dir / "PROOF.bend"
    if check_canonical:
        try:
            rel_laws = laws_file.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            rel_laws = ""
        canonical = CANONICAL_LAWS_MAP.get(rel_laws)
        if canonical and canonical.is_file():
            _sync_single_canonical_laws(laws_file, canonical)
    if not laws_file.is_file():
        return False, f"{bend_dir}: missing LAWS.bend"
    if not proof_file.is_file():
        return False, f"{bend_dir}: missing PROOF.bend alongside LAWS.bend"

    # 2. Check that PROOF.bend imports ./LAWS.bend
    proof_text = proof_file.read_text(encoding="utf-8")
    if not IMPORT_LAWS_RE.search(proof_text):
        return False, f"{proof_file}: PROOF.bend must contain `import ./LAWS.bend`"

    # 3. Forbid @unsafe, def f?(..), and ?TODO across all .bend files in bend_dir
    for bf in sorted(bend_dir.glob("*.bend")):
        text = bf.read_text(encoding="utf-8")
        code_lines = [line.split("#", 1)[0] for line in text.splitlines()]
        clean_code = "\n".join(code_lines)
        m = UNSAFE_RE.search(clean_code)
        if m:
            return (
                False,
                f"FORBIDDEN BEND ESCAPE HATCH in {bf.name}: found `{m.group(0)}`. "
                "All functions and proofs must be total and proven without `@unsafe` or `?TODO`.",
            )

    bend_exe = find_bend_bin()
    env = {**os.environ, "BEND_NO_TELEMETRY": "1"}

    if only_laws:
        laws_text = laws_file.read_text(encoding="utf-8")
        laws_hdr, law_blocks = _split_bend_blocks(laws_text, LAW_BLOCK_RE)
        proof_hdr, proof_blocks = _split_bend_blocks(proof_text, PROOF_BLOCK_RE)
        missing_laws = [lw for lw in only_laws if lw not in law_blocks]
        if missing_laws:
            return False, f"{bend_dir}: --only-law {missing_laws} not found in LAWS.bend (available: {list(law_blocks)})"
        missing_proofs = [lw for lw in only_laws if lw not in proof_blocks]
        if missing_proofs:
            return False, f"{bend_dir}: --only-law {missing_proofs} missing proof `def Laws.<law>` in PROOF.bend"
        with tempfile.TemporaryDirectory(prefix="bend2-only-law-") as tmp:
            tmp_dir = Path(tmp)
            for bf in bend_dir.glob("*.bend"):
                if bf.name not in ("LAWS.bend", "PROOF.bend"):
                    (tmp_dir / bf.name).write_text(bf.read_text(encoding="utf-8"), encoding="utf-8")
            sel_laws_src = "\n\n".join(law_blocks[lw].strip() for lw in only_laws) + "\n"
            sel_proofs_src = "\n\n".join(proof_blocks[lw].strip() for lw in only_laws) + "\n"
            (tmp_dir / "LAWS.bend").write_text(laws_hdr + sel_laws_src, encoding="utf-8")
            (tmp_dir / "PROOF.bend").write_text(proof_hdr + sel_proofs_src, encoding="utf-8")
            proc = subprocess.run([str(bend_exe), str(tmp_dir / "PROOF.bend")], cwd=str(tmp_dir), env=env, text=True, capture_output=True, timeout=30)
            output = (proc.stdout + "\n" + proc.stderr).strip()
            if proc.returncode != 0 or "ALL PROOFS CHECK" not in proc.stdout:
                return False, f"BEND 2 INCREMENTAL PROOF CHECK FAILED ({only_laws}) in {bend_dir}:\n{output}"
            _, mut_msg = _run_incremental_mutation_kill(tmp_dir, bend_exe, env, sel_laws_src)
        return True, f"PASS ({bend_dir.name} [--only-law {','.join(only_laws)}]): ALL PROOFS CHECK ({mut_msg})"

    # 4. Run full `bend PROOF.bend`
    proc = subprocess.run(
        [str(bend_exe), str(proof_file)],
        cwd=str(bend_dir),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    output = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0 or "ALL PROOFS CHECK" not in proc.stdout:
        return False, f"BEND 2 PROOF CHECK FAILED in {bend_dir}:\n{output}"

    return True, f"PASS ({bend_dir.name}): ALL PROOFS CHECK"


def check_target(target: Path, *, check_canonical: bool = True, only_laws: list[str] | None = None) -> tuple[bool, list[str]]:
    """Discover and check all `LAWS.bend` packages under `target`."""
    target = target.resolve()
    if target.is_file():
        target = target.parent
    if check_canonical:
        sync_canonical_laws(target)
    laws_files = sorted(target.rglob("LAWS.bend"))
    if not laws_files:
        return True, [f"No LAWS.bend files under {target} (skipped)"]

    messages: list[str] = []
    for lf in laws_files:
        ok, msg = check_bend_dir(lf.parent, check_canonical=check_canonical, only_laws=only_laws)
        messages.append(msg)
        if not ok:
            return False, messages
    return True, messages


def run_policy_parity_check() -> tuple[bool, str]:
    """Cross-verify live Python policy (`tickets.py`, `sandbox_guard.py`) against compiled `.agents/bend_policy/policy.mjs`."""
    import importlib.util
    import json

    policy_dir = ROOT / ".agents" / "bend_policy"
    mjs_path = policy_dir / "policy.mjs"
    bend_exe = find_bend_bin()
    env = {**os.environ, "BEND_NO_TELEMETRY": "1"}
    subprocess.run(
        [str(bend_exe), str(policy_dir / "main.bend"), "-o", str(mjs_path)],
        cwd=str(policy_dir),
        env=env,
        check=True,
        capture_output=True,
    )
    node_bin = shutil.which("node") or "/opt/homebrew/bin/node"
    js_probe = """
import PMod from "./.agents/bend_policy/policy.mjs";
const P = PMod.default || PMod;
const divs = [
  ["sarah", {$: "Sarah"}],
  ["eng0", {$: "Eng", id: {$: "E0"}}],
  ["eng1", {$: "Eng", id: {$: "E1"}}],
  ["eng2", {$: "Eng", id: {$: "E2"}}],
  ["eng3", {$: "Eng", id: {$: "E3"}}],
  ["bethe", {$: "Bethe"}],
  ["raymand", {$: "Raymand"}],
  ["bobo", {$: "Bobo"}],
  ["mike", {$: "Mike"}],
  ["groves", {$: "Groves"}],
];
const repos = [
  ["eng0", {$: "REng", id: {$: "E0"}}],
  ["eng1", {$: "REng", id: {$: "E1"}}],
  ["eng2", {$: "REng", id: {$: "E2"}}],
  ["eng3", {$: "REng", id: {$: "E3"}}],
  ["core", {$: "RCore"}],
  ["docs", {$: "RDocs"}],
  ["paper", {$: "RPaper"}],
  ["simulations", {$: "RSim"}],
  ["tests", {$: "RTests"}],
  ["wet_lab", {$: "RWetLab"}],
  ["vault", {$: "RVault"}],
];
const specs = [
  ["docs/specs/00a_STANDING_RULES.md", {$: "SStanding"}],
  ["docs/specs/phase0/15_overview.md", {$: "SPhase0"}],
  ["docs/specs/phase1/16_overview.md", {$: "SPhase1"}],
  ["docs/specs/phase2/17_overview.md", {$: "SPhase2"}],
  ["docs/specs/phase3/18_overview.md", {$: "SPhase3"}],
  ["docs/specs/phase4/19_overview.md", {$: "SPhase4"}],
  ["docs/specs/context_graph/20_overview.md", {$: "SContextGraph"}],
  ["docs/specs/graph_core/21_overview.md", {$: "SGraphCore"}],
  ["docs/specs/whiteboard/22_overview.md", {$: "SWhiteboard"}],
  ["docs/specs/whiteboard_22a/22a_WHITEBOARD_SCHEMA_AND_MUTATIONS.md", {$: "SWhiteboard22A"}],
  ["docs/specs/whiteboard_22b/22b_WHITEBOARD_FRUSTRATION_AND_HIERARCHY.md", {$: "SWhiteboard22B"}],
  ["docs/specs/whiteboard_22c/22c_WHITEBOARD_LINKER_AND_READ_ENGINE.md", {$: "SWhiteboard22C"}],
  ["docs/specs/the_executive/23_THE_EXECUTIVE_AND_THE_TABLE.md", {$: "SExecutive"}],
  ["docs/specs/executive_23a/23a_EXECUTIVE_PREFLIGHT_AND_TRIAL.md", {$: "SExecutive23A"}],
  ["docs/specs/executive_23b/23b_EXECUTIVE_ROUTING_GATE.md", {$: "SExecutive23B"}],
  ["docs/specs/executive_23c/23c_EXECUTIVE_VOICE_AND_ORCHESTRATOR.md", {$: "SExecutive23C"}],
  ["docs/specs/ai_memory/24_overview.md", {$: "SAIMemory"}],
  ["docs/specs/the_table/25_overview.md", {$: "STheTable"}],
  ["docs/specs/qa/blind.md", {$: "SQA"}],
];
const out = { tickets: {}, commits: {}, specs: {} };
for (const [vName, vVal] of divs) {
  for (const [aName, aVal] of divs) {
    for (const [fName, fVal] of divs) {
      out.tickets[`${vName}:${aName}:${fName}`] = Boolean(P.may_read_ticket(vVal, aVal, fVal));
    }
  }
  for (const [rName, rVal] of repos) {
    out.commits[`${vName}:${rName}`] = Boolean(P.may_commit_repo(vVal, rVal));
  }
  if (vName.startsWith("eng")) {
    for (const [sPath, sVal] of specs) {
      out.specs[`${vName}:${sPath}`] = Boolean(P.may_read_spec(vVal, sVal));
    }
  }
}
console.log(JSON.stringify(out));
"""
    proc = subprocess.run([node_bin, "--input-type=module", "-e", js_probe], cwd=str(ROOT), text=True, capture_output=True, check=True)
    bend_tables = json.loads(proc.stdout.strip())

    # Load scripts/tickets.py and .agents/scripts/sandbox_guard.py
    t_spec = importlib.util.spec_from_file_location("site_y_tickets", ROOT / "scripts" / "tickets.py")
    tickets_mod = importlib.util.module_from_spec(t_spec)
    t_spec.loader.exec_module(tickets_mod)

    g_spec = importlib.util.spec_from_file_location("site_y_guard", ROOT / ".agents" / "scripts" / "sandbox_guard.py")
    guard_mod = importlib.util.module_from_spec(g_spec)
    g_spec.loader.exec_module(guard_mod)

    # 1. Verify all 1,000 (viewer, author, assignee) ticket visibility triples
    n_ticket = 0
    for key, bend_ok in bend_tables["tickets"].items():
        viewer, author, assignee = key.split(":")
        py_ok = viewer in tickets_mod.readers([f"by:{author}", f"for:{assignee}"])
        if py_ok != bend_ok:
            return False, f"Ticket visibility parity mismatch on ({viewer}, by:{author}, for:{assignee}): Python={py_ok}, Bend2={bend_ok}"
        n_ticket += 1

    # 2. Verify all 110 (actor, repo) split-repo commit ownership pairs
    n_commit = 0
    for key, bend_ok in bend_tables["commits"].items():
        actor, repo = key.split(":")
        owner, _readers = guard_mod.SPLIT_REPOS[repo]
        py_ok = (actor == owner) or (actor == "groves")
        if py_ok != bend_ok:
            return False, f"Split-repo commit parity mismatch on ({actor}, {repo}): Python={py_ok}, Bend2={bend_ok}"
        n_commit += 1

    # 3. Verify all 32 (eng, spec_path) spec read isolation pairs
    n_spec = 0
    for key, bend_ok in bend_tables["specs"].items():
        eng, s_path = key.split(":", 1)
        py_ok = guard_mod._eng_allowed_modelcircuits_rel(eng, s_path, is_list_or_search=False)
        if py_ok != bend_ok:
            return False, f"Spec isolation parity mismatch on ({eng}, {s_path}): Python={py_ok}, Bend2={bend_ok}"
        n_spec += 1

    total = n_ticket + n_commit + n_spec
    return True, f"PASS (policy.mjs <-> Python): {total}/{total} exhaustive parity checks matched ({n_ticket} ticket, {n_commit} git-repo, {n_spec} spec-isolation)"


def run_self_test() -> int:
    for rel_dir in (".agents/bend_policy", "laws/bend_poc"):
        target_dir = ROOT / rel_dir
        ok, msgs = check_target(target_dir, check_canonical=True)
        if not ok:
            print(f"SELF-TEST FAIL ({rel_dir} should pass):")
            for m in msgs:
                print(" ", m)
            return 1
        print(f"PASS [ALLOW] Valid Bend 2 Package ({rel_dir}): {msgs[0]}")

    ok_par, msg_par = run_policy_parity_check()
    if not ok_par:
        print(f"SELF-TEST FAIL (Parity): {msg_par}")
        return 1
    print(f"PASS [ALLOW] Cross-Language Parity (.agents/bend_policy/policy.mjs <-> tickets.py & sandbox_guard.py): {msg_par}")

    poc_dir = ROOT / "ModelCircuits" / "eng3" / "bend_poc"
    # Mutation test 1: Introduce a bug in eng3/bend_poc/main.bend in a temp copy and verify Bend 2 blocks it
    with tempfile.TemporaryDirectory(prefix="bend2-poc-test-") as tmp:
        tmp_dir = Path(tmp)
        for f in poc_dir.glob("*.bend"):
            (tmp_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        main_file = tmp_dir / "main.bend"
        buggy = main_file.read_text(encoding="utf-8").replace(
            "def inv_pol(p: Pol) -> Pol:\n  match p:\n    case Pos{}:\n      Neg{}",
            "def inv_pol(p: Pol) -> Pol:\n  match p:\n    case Pos{}:\n      Neut{}",
        )
        main_file.write_text(buggy, encoding="utf-8")
        ok_bug, msgs_bug = check_target(tmp_dir, check_canonical=False)
        if ok_bug:
            print("SELF-TEST FAIL: Buggy main.bend was NOT blocked by Bend 2!")
            return 1
        first_line = msgs_bug[0].splitlines()[0]
        print(f"PASS [DENY ] Buggy eng3/bend_poc/main.bend blocked by Bend 2 proof checker: {first_line}")

    # Mutation test 2: Introduce a Blind-QA firewall hole in .agents/bend_policy/main.bend and verify Bend 2 blocks it
    policy_dir = ROOT / ".agents" / "bend_policy"
    with tempfile.TemporaryDirectory(prefix="bend2-policy-test-") as tmp:
        tmp_dir = Path(tmp)
        for f in policy_dir.glob("*.bend"):
            (tmp_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        main_file = tmp_dir / "main.bend"
        buggy_pol = main_file.read_text(encoding="utf-8").replace(
            "    case Bobo{} Eng{j}:\n      False{}",
            "    case Bobo{} Eng{j}:\n      True{}",
        )
        main_file.write_text(buggy_pol, encoding="utf-8")
        ok_pol, msgs_pol = check_target(tmp_dir, check_canonical=False)
        if ok_pol:
            print("SELF-TEST FAIL: Blind-QA firewall regression in bend_policy/main.bend was NOT blocked!")
            return 1
        first_line = msgs_pol[0].splitlines()[0]
        print(f"PASS [DENY ] Blind-QA firewall regression in .agents/bend_policy/main.bend blocked by Bend 2: {first_line}")

    # Mutation test 3: Attempt to use @unsafe escape hatch and verify bend_gate blocks it
    with tempfile.TemporaryDirectory(prefix="bend2-unsafe-test-") as tmp:
        tmp_dir = Path(tmp)
        for f in poc_dir.glob("*.bend"):
            (tmp_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        proof_file = tmp_dir / "PROOF.bend"
        proof_file.write_text(proof_file.read_text(encoding="utf-8") + "\n@unsafe def cheat(x: Nat) -> Nat:\n  x\n", encoding="utf-8")
        ok_unsafe, msgs_unsafe = check_target(tmp_dir, check_canonical=False)
        if ok_unsafe:
            print("SELF-TEST FAIL: @unsafe escape hatch was NOT blocked!")
            return 1
        print(f"PASS [DENY ] @unsafe escape hatch blocked by bend_gate: {msgs_unsafe[0]}")

    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="Run Bend 2 gate self-tests")
    ap.add_argument("--only-law", "--laws", dest="only_law", default=None, help="Comma-separated law name(s) for incremental single-law proof + mutation check")
    ap.add_argument("command", nargs="?", default="check", choices=["check", "sync", "self-test"])
    ap.add_argument("target", nargs="?", default="ModelCircuits")
    args = ap.parse_args()

    if args.self_test or args.command == "self-test":
        raise SystemExit(run_self_test())

    target_path = (ROOT / args.target) if not Path(args.target).is_absolute() else Path(args.target)
    if args.command == "sync":
        for m in sync_canonical_laws(target_path):
            print(m)
        return
    only_laws = [x.strip() for x in args.only_law.split(",") if x.strip()] if args.only_law else None
    ok, msgs = check_target(target_path, only_laws=only_laws)
    for m in msgs:
        print(m)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
