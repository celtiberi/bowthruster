---
name: bowthruster
description: Unified BowThruster workflow for exploring code (`atlas` & `slice`), mutating architecture/contracts (`turn`), implementing `STALE` WorkPackets (`packets`), and running `< 5s` incremental QA & Git verification (`git_sync` & `status`). Activate this skill before reading/editing source files or running tests in a BowThruster-tracked repository.
---

# `bowthruster`: Unified Slice-First Navigation, Change & Verification Workflow

> **Project Configuration (Customize on Onboarding):**
> - **Default Board:** `<BOARD_NAME>` (e.g., `lattice_core`)
> - **Test Command:** `<TEST_CMD>` (e.g., `.venv/bin/pytest -k`)
> - **Onboarding a Repo:** Call `bootstrap(board="<BOARD_NAME>")` (CLI: `bowthruster --board <BOARD_NAME> bootstrap [--component <comp>]`) to deterministically extract candidate components, public `Schema`/`Interface` signatures with `#L<start>-L<end>` anchors, `Depends_On` imports, and matched test files, then fill in `Charter`, `Role`, `Why`, `Spec`, and `Flow`s one onboarding packet at a time via `turn`. If `atlas` reports `NEEDS_MIGRATION` on an older board file, run `migrate(board="<BOARD_NAME>")` (CLI: `bowthruster migrate --all`).

## Step 1 — Explore & Pull Code Slices (`atlas` $\rightarrow$ `slice`)
Never grep blindly across the repository or read whole files out of curiosity:
1. **Topology & Living Spec (`LOD_0` / `spec=True`):** Call `atlas(board="<BOARD_NAME>")` (or `load_all=True`) for the 1-line-per-component map (Charter, Role, Why/Rationale, verification statuses, and `> FLOW` edges), or pass `spec=True` to render the complete human-readable Markdown specification.
2. **Component + Code Slice in One Shot (`LOD_2`):** Call `slice(board="<BOARD_NAME>", query="<function_or_helper>", include_code=True)` (or `component="<comp>"`).
   - Returns the owning `@ COMPONENT`, `# SCHEMA` and `$ INTERFACE` `file=<path>#L<start>-L<end>` spans, intra-file helper call trees (`calls=[_helper#L<start>-L<end>, ...]`), on-the-fly `@ AST_SYMBOL` resolution (including private `_helper` functions), and the exact numbered `[CODE <path>#L<start>-L<end>]` block so you can pass lines directly to `edit_file` without `read_file`.
   - **Internal Fixes (3-Call Fast Path):** If fixing internal code where no contract/schema/law changes, pass `mark_stale=True` (and optional `claim_by="<agent>"` in multi-agent runs) to `slice(..., include_code=True, mark_stale=True)` to transition the matched component to `STALE` in the same call and skip directly to `edit_file` $\rightarrow$ `git_sync(mode="pre", mark_verified=True)`.

## Step 2 — Mutate the Board First When Contracts Change (`turn` $\rightarrow$ `STALE`)
Never edit a `VERIFIED` component's files directly (`verified_scope_edit_guard_law` blocks the commit). When adding or changing a component, flow, interface, schema, law, or rule, call `turn(board="<BOARD_NAME>", speaker="agent", message="...")` with canonical Markdown pipe tables (`< 50ms`, zero LLMs):
- `| Charter | Summary | Principles |` *(project/board purpose, why it exists, and core design principles)*
- `| Component | Role | Why | Owner | Stage | Depends_On | Files | Tests |` *(`Why` and `Tests` columns are optional; declare component `pytest` files in `Tests` so `git_sync` runs them and counts the component in `verified=X/Y` rather than `VERIFIED (untested)`)*
- `| Flow | Source | Target | Payload | Condition |`
- `| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |`
- `| Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |` *(or `Theorem` column `LHS == RHS`)*
- `| Schema | Component | Fields | Owner | Stage | File | Spec |` *(optional data/payload models)*
- `| Rule | Component | Type | Property | Op | Value | Condition |` *(optional: `BOUND` on `max_file_lines` or AST module constants, `PROHIBITION` on banned imports, or `OBLIGATION`; re-submitting `(Component, Property)` updates in place)*
- **Complete Specification on the Board (`Spec` & `Rule`):** The `Spec` column on `Interface`, `Schema`, and `Law` rows defines the symbol's behavioral contract as a `;`-separated sequence of operations, invariants, and edge-case rules. Because the board is the persistent specification across sessions and agents, record all behavioral requirements in `Spec` and all numeric or import constraints in `Rule` during `turn` before writing code.
- **Anti-Tautology `% LAW` Rule:** Reserve `% LAW` for multi-variable boolean state machines, routing tables, or conservation invariants ($k \ge 2$ boolean inputs using `Bool.and`, `Bool.or`, `Bool.not`). Never invent a 1-boolean identity wrapper (`MOD.is_ok(x) == x`) just to mark a component `STALE` for a bug fix — use `slice(..., mark_stale=True)` or update the `$ INTERFACE`/`# SCHEMA`/`Rule` row instead.

## Step 3 — Implement the `STALE` WorkPacket (`packets` / `edit_file`)
- Call `packets(board="<BOARD_NAME>", status="STALE")` (or `component="<comp>"`; in multi-agent runs pass `unclaimed_only=True, claim_by="<agent>"` so two agents never implement the same packet, and `status(..., release_claim=True)` if handing off early; claims clear automatically on `VERIFIED`) to compile each `STALE` component's `WorkPacket` (`python_skeletons_by_file`, `python_skeleton`, `laws_skeleton`, `prompt_slice`, and `baseline_commit_sha`).
- Drive implementation strictly from the compiled `WorkPacket`—whether implementing the component directly or assigning the component ID to a subagent to pull via `packets`. If any requirement needed to write the code is absent from the `WorkPacket`, return to Step 2 and update the board via `turn` first.
- Implement every `STALE` component in the dependency wave within its declared `files`, keeping every file within any active `max_file_lines` bound and ensuring non-law `$ INTERFACE` functions reach governing `% LAW` helpers structurally in the call graph with dynamic non-duplicate arguments (static AST checks are structural; semantic behavior is verified by delta `pytest`), before running Step 4 verification.

## Step 4 — `< 5s` Pre-Commit Gate, Auto-Verify & Post-Commit Stamp (`git_sync`)
1. **50ms Pre-Commit Gate + Auto-Verify:** Call `git_sync(board="<BOARD_NAME>", mode="pre", mark_verified=True)` and confirm `allowed: true` (`bounds_ok`, `contracts_ok`, `bend_proofs_ok`, `no_dangling_deletes`, `flow_schema_checked`, `delta_tests_status` in `PASSED`/`PARTIAL_UNTESTED`/`NOT_CHECKED`, and check `unregistered_public_symbols` + `warnings` for any `undeclared_matching_tests`). When `allowed: true`, `mark_verified=True` automatically transitions `STALE` components to `VERIFIED` (`VERIFIED (untested)` if `Tests` is empty) and refreshes their file hashes and `#L<start>-L<end>` anchors.
2. **Delta & Seam Tests:** Run `<TEST_CMD> <changed_interface_or_component>` on `changed_scopes | stale_dependents` only. Whenever a change touches a producer or consumer along a `> FLOW Source --[Payload]--> Target` edge, include an **unmocked producer $\rightarrow$ consumer seam test** feeding the real output of `Source` directly into `Target`.
3. **Commit & Stamp Post-Commit:** Commit and call `git_sync(board="<BOARD_NAME>", mode="post")` (or let `.git/hooks/post-commit` run it). On any gate rejection or error, inspect `logs(board="<BOARD_NAME>", limit=10)`.
