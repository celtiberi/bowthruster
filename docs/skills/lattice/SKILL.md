---
name: lattice
description: Unified Lattice-first workflow for exploring code (`lattice_atlas` & `lattice_slice`), mutating architecture/contracts (`lattice_turn`), implementing `STALE` WorkPackets (`lattice_packets`), and running `< 5s` incremental QA & Git verification (`lattice_git_sync` & `lattice_status`). Activate this skill before reading/editing source files or running tests in a Lattice-tracked repository.
---

# `lattice`: Unified Slice-First Navigation, Change & Verification Workflow

> **Project Configuration (Customize on Onboarding):**
> - **Default Board:** `<BOARD_NAME>` (e.g., `lattice_core`)
> - **Test Command:** `<TEST_CMD>` (e.g., `.venv/bin/pytest -k`)

## Step 1 — Explore & Pull Code Slices (`lattice_atlas` $\rightarrow$ `lattice_slice`)
Never grep blindly across the repository or read whole files out of curiosity:
1. **Topology (`LOD_0`):** Call `lattice_atlas(board="<BOARD_NAME>")` (or `load_all=True`) for the 1-line-per-component map, verification statuses, and `> FLOW` edges.
2. **Component + Code Slice in One Shot (`LOD_2`):** Call `lattice_slice(board="<BOARD_NAME>", query="<function_or_helper>", include_code=True)` (or `component="<comp>"`).
   - Returns the owning `@ COMPONENT`, `# SCHEMA` and `$ INTERFACE` `file=<path>#L<start>-L<end>` spans, intra-file helper call trees (`calls=[_helper#L<start>-L<end>, ...]`), on-the-fly `@ AST_SYMBOL` resolution (including private `_helper` functions), and the exact numbered `[CODE <path>#L<start>-L<end>]` block so you can pass lines directly to `edit_file` without `read_file`.
   - **Internal Fixes (3-Call Fast Path):** If fixing internal code where no contract/schema/law changes, pass `mark_stale=True` to `lattice_slice(..., include_code=True, mark_stale=True)` to transition the matched component to `STALE` in the same call and skip directly to `edit_file` $\rightarrow$ `lattice_git_sync(mode="pre", mark_verified=True)`.

## Step 2 — Mutate the Board First When Contracts Change (`lattice_turn` $\rightarrow$ `STALE`)
Never edit a `VERIFIED` component's files directly (`verified_scope_edit_guard_law` blocks the commit). When adding or changing a component, flow, interface, schema, law, or rule, call `lattice_turn(board="<BOARD_NAME>", speaker="agent", message="...")` with canonical Markdown pipe tables (`< 50ms`, zero LLMs):
- `| Component | Role | Owner | Stage | Depends_On | Files |`
- `| Flow | Source | Target | Payload | Condition |`
- `| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |`
- `| Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |` *(or `Theorem` column `LHS == RHS`)*
- `| Schema | Component | Fields | Owner | Stage | File | Spec |` *(optional)*
- `| Rule | Component | Type | Property | Op | Value | Condition |` *(optional)*
- **Anti-Tautology `% LAW` Rule:** Reserve `% LAW` for multi-variable boolean state machines, routing tables, or conservation invariants ($k \ge 2$ boolean inputs using `Bool.and`, `Bool.or`, `Bool.not`). Never invent a 1-boolean identity wrapper (`MOD.is_ok(x) == x`) just to mark a component `STALE` for a bug fix — use `lattice_slice(..., mark_stale=True)` or update the `$ INTERFACE`/`# SCHEMA`/`Rule` row instead.

## Step 3 — Implement the `STALE` WorkPacket (`lattice_packets` / `edit_file`)
- For new components or `% LAW` changes, call `lattice_packets(board="<BOARD_NAME>", status="STALE")` (or `component="<comp>"`) to retrieve `laws_skeleton` (`LAWS.bend`), `python_skeleton` (typed Python stub with `Spec` docstrings and `# MUST CALL` anchors), and `baseline_commit_sha` (use `git diff <baseline_commit_sha>` when refactoring or moving files).
- Implement changes in the component's declared `files`, keeping every file within any active `max_file_lines` bound and ensuring every `$ INTERFACE` calls its governing `% LAW` helper in the live call graph (never discard return values with `_ = law(...)`).

## Step 4 — `< 5s` Pre-Commit Gate, Auto-Verify & Post-Commit Stamp (`lattice_git_sync`)
1. **50ms Pre-Commit Gate + Auto-Verify:** Call `lattice_git_sync(board="<BOARD_NAME>", mode="pre", mark_verified=True)` and confirm `allowed: true` (`bounds_ok`, `contracts_ok`, `bend_proofs_ok`, `no_dangling_deletes`, `flow_schema_checked`, and check `unregistered_public_symbols`). When `allowed: true`, `mark_verified=True` automatically transitions `STALE` components to `VERIFIED` and refreshes their file hashes and `#L<start>-L<end>` anchors.
2. **Delta & Seam Tests:** Run `<TEST_CMD> <changed_interface_or_component>` on `changed_scopes | stale_dependents` only. Whenever a change touches a producer or consumer along a `> FLOW Source --[Payload]--> Target` edge, include an **unmocked producer $\rightarrow$ consumer seam test** feeding the real output of `Source` directly into `Target`.
3. **Commit & Stamp Post-Commit:** Commit and call `lattice_git_sync(board="<BOARD_NAME>", mode="post")` (or let `.git/hooks/post-commit` run it). On any gate rejection or error, inspect `lattice_logs(board="<BOARD_NAME>", limit=10)`.
