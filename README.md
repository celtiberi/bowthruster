# Lattice

**Architecture memory, focused code lookup, and pre-commit verification for AI coding agents. The Lattice itself is the spec, eliminating manual spec writing and keeping design and code in sync automatically.**

> **Built for AI Coding Agents — Not Manual Human Bookkeeping**  
> Lattice (`lattice-mcp`) is a tool for **your AI coding agent** to operate directly, not another spec format for you to maintain by hand. Anyone (or any AI agent) can clone the repo and run it immediately with stock `python3` — **no `pip install`, no virtualenv, and no API keys required**. Connect `lattice-mcp` to your agent (Claude Code, Cursor, Antigravity, Windsurf, etc.), and your AI builds the architecture map, pulls focused code slices, and verifies its own commits automatically.

---

## Why Lattice

When you use an AI coding agent on a growing project, three things start going wrong:
1. **It burns time and tokens hunting through files:** To make a 15-line change, the agent greps across the repo and reads dozens of files—filling its context window with noise before it writes a single line of code.
2. **Fixing one thing breaks another:** It forgets a rule you explained earlier, or changes a function in one file without realizing three downstream modules depend on it.
3. **Architecture docs rot immediately:** Nobody has time to keep markdown specs in sync with code by hand.

**Lattice (`lattice-mcp`) fixes all three.** It is a fast, deterministic MCP server (zero LLMs, zero API keys) that your AI operates directly to keep your codebase's architecture, rules, and Git history in lockstep:

- **1-Shot Code Slicing Instead of Repo Grepping:** Instead of reading whole files, your agent queries Lattice for a function or feature and gets back a `< 2 KB` slice containing the exact `#L<start>-L<end>` source code, the private helpers it calls, and its upstream/downstream contracts in a single call.
- **Rules That Can't Be Silently Dropped:** Important invariants and module contracts are registered on the Lattice board instead of buried in chat history. Before a commit lands, Lattice checks your code's AST and formal invariants—blocking the commit if an invariant was bypassed or an unrelated module was edited by mistake.
- **Automatic Blast-Radius Tracking:** When your agent changes an upstream data structure or interface, Lattice automatically marks the affected downstream modules **`STALE`** so the agent knows exactly what else needs updating and which tests to run.
- **Works Across Languages & Repos:** Built-in AST/symbol extractors support Python, TypeScript/JavaScript, Go, Rust, C/C++, Java, Kotlin, C#, Swift, and Ruby—and `lattice-mcp` automatically discovers the right board across multiple repositories from a single global MCP config.

| Without Lattice | With Lattice (`lattice-mcp`) |
| :--- | :--- |
| Agent greps and reads 20+ files (`30,000+` tokens) to find the 30 lines it needs to edit. | Agent calls `lattice_slice(..., include_code=True)` once and gets the `< 2 KB` contract + exact `#L<start>-L<end>` code lines immediately. |
| A 2-line bugfix in one module accidentally edits a neighboring verified file or drops an existing rule. | Lattice's **pre-commit gate** (`lattice_git_sync`) verifies AST signatures, call-graph rules, and file boundaries before the commit can land. |
| Changing a shared type or API leaves downstream callers broken until a slow full-repo test run fails. | Lattice traces directed data flows (`> FLOW`) and marks only the impacted downstream components **`STALE`** so the agent updates them and runs only their tests. |
| Architecture diagrams and `README` specs drift out of date after a few commits. | On every commit, Lattice stamps the Git SHA, auto-heals renamed files (`git mv`), and refreshes `#L<start>-L<end>` line numbers automatically. |

---

## Quick Start (30 Seconds, Zero Dependencies)

Lattice runs on stock `python3` using only the **Python 3.11+ standard library** (`json`, `sqlite3`, `ast`, `pathlib`, `hashlib`, `subprocess`).

### 1. Clone the repository
```bash
git clone https://github.com/celtiberi/lattice.git
```
*(Optional: run `python3 -m lattice.mcp_verifier` inside `lattice/` to verify the stdio JSON-RPC 2.0 server, or `pip install -e .` if you want the `lattice` and `lattice-mcp` CLI entrypoints on your `PATH`.)*

### 2. Connect `lattice-mcp` to your AI coding agent

- **Claude Code (one command):**
  ```bash
  claude mcp add lattice -e PYTHONPATH=/ABSOLUTE/PATH/TO/lattice -- python3 -m lattice.mcp_server
  ```

- **Standard MCP JSON Config (`.mcp.json`, `mcp_config.json`, Cursor, Antigravity, Windsurf):**
  ```json
  {
    "mcpServers": {
      "lattice": {
        "command": "python3",
        "args": ["-m", "lattice.mcp_server"],
        "env": {
          "PYTHONPATH": "/ABSOLUTE/PATH/TO/lattice"
        }
      }
    }
  }
  ```
  *(Optional env var: `LATTICE_PROJECT_ROOT` to pin a specific repo. If omitted, `lattice-mcp` automatically discovers the active repository from `.whiteboard/<board>.json` or the file paths passed to `lattice_turn`.)*

### 3. Paste these two prompts to your AI
Once connected, open any new or existing repository and tell your agent:
1. **Map the codebase:**
   > *`"Call lattice_readme(section='readme') and map this repository onto a Lattice board with its Charter, component Roles/Why, Flows, Interfaces, and Laws."`*
2. **Run an architectural audit:**
   > *`"With the insight you now have from the Lattice board and AST slices, what would you change about this project's architecture (e.g., monoliths doing another component's job, hidden state mutations across flows, duplicated logic, or missing seams)?"`*

---

## Agent Reference: Tools, Workflows & Pipe-Table Schema

### The 8 MCP Tools

| MCP Tool | Step | Key Arguments & Purpose |
| :--- | :---: | :--- |
| `lattice_readme` | **Onboard** | Fetch onboarding docs or the unified `lattice` skill template (`section="readme" \| "guide" \| "skill" \| "all"`). |
| `lattice_atlas` | **Step 1 (Explore / Spec)** | Render the 1-line-per-component `LOD_0` topology (Charter, Role, Why, statuses, `> FLOW` edges, metrics, and Mermaid graph) or pass `spec=True` to compile the full Markdown specification (`board`, `load_all=True`, `mermaid=True`, `spec=True`). |
| `lattice_slice` | **Step 1 (Slice + Code)** | Pull a `< 2 KB` component/symbol slice + exact `#L<start>-L<end>` source code in one call (`board`, `component`, `query`, `include_code=True`, `mark_stale=True`). Supports windowed lookup (`query="fn_name 100-180"` or `query="fn_name keyword"`). |
| `lattice_turn` | **Step 2 (Mutate Board)** | Compile Markdown pipe tables into the Whiteboard and mark modified/downstream components `STALE` (`board`, `speaker`, `message`). |
| `lattice_packets` | **Step 3 (Build Packet)** | Emit wave-ordered `WorkPacket`s (`laws_skeleton`, `python_skeleton`, `baseline_commit_sha`) (`board`, `component`, `status="STALE"`). |
| `lattice_git_sync` | **Step 4 (Verify & Sync)** | Run pre-commit verification + auto-verify (`mode="pre"`, `mark_verified=True`), post-commit hash/line-anchor stamp (`mode="post"`), initial baseline (`mode="baseline"`), or drift check (`mode="drift"`). |
| `lattice_status` | **Step 4 (Status)** | Transition a component's build status (`board`, `component`, `new_status="VERIFIED" \| "STALE" \| "FAILED"`). |
| `lattice_logs` | **Diagnose** | Read structured Flight Recorder telemetry (`board`, `status="ERROR" \| "REJECTED"`, `limit=20`). |

### Daily Agent Workflows

#### Workflow A — Internal Fix (`0` Contract Changes, **3 Tool Calls**)
When fixing an internal bug, helper function, or threshold where no `# SCHEMA`, `$ INTERFACE`, `> FLOW`, or `% LAW` signature changes:
1. **Pull Code & Mark `STALE` in One Shot:**
   - Call `lattice_slice(board="my_board", query="verify_token", include_code=True, mark_stale=True)`
   - Returns the `< 2 KB` component contract slice, the exact `#L<start>-L<end>` code block, and transitions the owning component (`auth_service`) to `STALE` in one call. *(If exploring first without knowing whether you will edit, omit `mark_stale=True` until you pull the target function to edit.)*
2. **Edit the Code:**
   - Call `edit_file` directly using the returned `#L<start>-L<end>` lines.
3. **Pre-Commit Gate + Auto-Verify in One Shot:**
   - Call `lattice_git_sync(board="my_board", mode="pre", mark_verified=True)` (and run delta tests on the changed component).
   - Verifies AST signatures, `% LAW` call-graph reachability, `> FLOW` schemas, and Bend 2 proofs. When `allowed: true`, it **automatically transitions `STALE` components back to `VERIFIED`** and refreshes file hashes and `#L<start>-L<end>` AST anchors.

#### Workflow B — Architectural / Contract Change (**4 Tool Calls**)
When adding or modifying a `@ COMPONENT`, `> FLOW`, `$ INTERFACE`, `# SCHEMA`, `% LAW`, or `Rule`:
1. **Explore & Slice (`lattice_atlas` / `lattice_slice`):** Inspect the component slice and existing contracts.
2. **Mutate the Board First (`lattice_turn`):** Submit the updated Markdown pipe table(s), which transitions the modified component and any impacted downstream `> FLOW` consumers from `VERIFIED` to `STALE`. *(Optional for greenfield/new laws: call `lattice_packets(status="STALE")` for `laws_skeleton` and `python_skeleton`.)*
3. **Edit the Code (`edit_file`):** Implement the updated contract and `% LAW` wiring.
4. **Pre-Commit Gate + Auto-Verify (`lattice_git_sync`):** Call `lattice_git_sync(board="my_board", mode="pre", mark_verified=True)`, run delta + producer $\rightarrow$ consumer seam tests, commit, and stamp `lattice_git_sync(board="my_board", mode="post")`.

### Canonical Pipe-Table Schema (`lattice_turn`)

Pass canonical Markdown pipe tables (`Charter`, `Component`, `Flow`, `Interface`, `Law`, plus optional `Schema` and `Rule` tables) to `lattice_turn`. `lattice_atlas(board="my_board", spec=True)` renders the complete human-readable specification directly from the board at any time:

```markdown
| Charter | Summary | Principles |
| :--- | :--- | :--- |
| my_board | Edge API gateway and stateless JWT authentication service for client requests. | Stateless token validation; zero shared mutable state between gateway workers; explicit typed contracts on all internal flows. |

| Component | Role | Why | Owner | Stage | Depends_On | Files |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| auth_service | JWT token validator | Isolates cryptographic signature and expiration checks so downstream handlers only receive verified AuthTokens. | backend | 1 | none | src/auth.py:L1-L120 |
| api_gateway | HTTP request router | Terminates external HTTP requests and enforces auth_check before dispatching to internal services. | backend | 1 | auth_service | src/gateway.py:L1-L150 |

| Flow | Source | Target | Payload | Condition |
| :--- | :--- | :--- | :--- | :--- |
| auth_check | api_gateway | auth_service | AuthToken | on incoming HTTP request |

| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| verify_token | auth_service | raw_token: str | AuthToken | backend | 1 | src/auth.py:L20-L60 | Spec 1 |
| route_request | api_gateway | request: dict | HttpResponse | backend | 1 | src/gateway.py:L30-L90 | Spec 1 |

| Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| token_valid_law | auth_service | backend | 1 | laws | has_sig: Bool, not_expired: Bool | MOD.is_token_valid(has_sig, not_expired) | Bool.and(has_sig, not_expired) | Spec 1 |
| route_allow_law | api_gateway | backend | 1 | laws | token_ok: Bool, route_exists: Bool | MOD.can_route(token_ok, route_exists) | Bool.and(token_ok, route_exists) | Spec 1 |

| Schema | Component | Fields | Owner | Stage | File | Spec |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| AuthToken | auth_service | user_id: str, expires_at: int | backend | 1 | src/auth.py:L10-L18 | Spec 1 |

| Rule | Component | Type | Property | Op | Value | Condition |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| max_lines_rule | auth_service | BOUND | max_file_lines | <= | 500 | always |
| retry_cap_rule | api_gateway | BOUND | MAX_RETRIES | <= | 3 | always |
| no_requests_lib | api_gateway | PROHIBITION | requests | == | false | always |
```

- **When to use `Rule` (`*`) vs. `Law` (`%`):**
  - **`Rule` (`BOUND` / `PROHIBITION` / `OBLIGATION`):** Use for file line caps (`BOUND` on `max_file_lines`), module constant bounds (`BOUND` on an AST constant like `MAX_RETRIES <= 3`), banned imports (`PROHIBITION` on a module name like `requests` or `torch`, checked against AST `import` statements on `lattice_git_sync`), or component obligations (`OBLIGATION`, mounted in `lattice_slice`). Re-submitting the same `(Component, Property)` overwrites the old rule in place (`MARKOVIAN_OVERWRITE`), or attaches a conditional exception when `Condition` differs.
  - **`Law` (`% LAW`):** Reserve for multi-variable boolean state machines, routing tables, or conservation invariants ($k \ge 2$ boolean inputs, or a `Theorem` column `LHS == RHS`) that are proven in Bend 2 and called by the component's `$ INTERFACE` in the AST call graph. Never invent a 1-boolean identity law (`MOD.is_ok(x) == x`) just to mark a component `STALE`—use `lattice_slice(..., mark_stale=True)` or update the `$ INTERFACE`, `# SCHEMA`, or `Rule` row instead.

### Onboarding an Existing Repository & Installing the AI Skill / Git Hooks

1. **Compile & Verify the Baseline:** Submit the initial pipe tables via `lattice_turn`, mark existing files `VERIFIED` via `lattice_git_sync(board="my_board", mode="pre", mark_verified=True)`, and stamp the initial Git SHA via `lattice_git_sync(board="my_board", mode="post")`.
2. **Install the Unified `lattice` Skill:** Call `lattice_readme(section="skills")` to retrieve [`docs/skills/lattice/SKILL.md`](docs/skills/lattice/SKILL.md) and write it to `.agents/skills/lattice/SKILL.md` (or `.claude/skills/lattice/SKILL.md`), replacing `<BOARD_NAME>` and `<TEST_CMD>`.
3. **Optional Git Hooks (`.git/hooks/pre-commit` & `post-commit`):**
   ```bash
   # .git/hooks/pre-commit (chmod +x .git/hooks/pre-commit)
   #!/usr/bin/env bash
   set -euo pipefail
   LATTICE_PROJECT_ROOT="$(git rev-parse --show-toplevel)" \
     python3 -m lattice.cli --board <BOARD_NAME> git-sync --pre-commit

   # .git/hooks/post-commit (chmod +x .git/hooks/post-commit)
   #!/usr/bin/env bash
   LATTICE_PROJECT_ROOT="$(git rev-parse --show-toplevel)" \
     python3 -m lattice.cli --board <BOARD_NAME> git-sync --post-commit
   ```

---

## How It Works Under the Hood

Lattice is organized into six packages under `lattice/`:

```mermaid
flowchart LR
    User(["Developer / AI Agent"]) -- "Markdown Pipe Tables" --> Reg["1. lattice.regiment\n(Pipe-Table Compiler)"]
    Reg -- "IntakePacket (Tables + MCUs)" --> Exec["2. lattice.the_executive\n(Pre-Flight & Git Gate)"]
    Exec -- "Seed Cues" --> Mem["4. lattice.ai_memory\n(Directed Spreading Activation & URIs)"]
    Mem -- "Activated Nodes (LOD_1)" --> Table["5. lattice.the_table\n(Bounded Context Desk & Atlas)"]
    WB["3. lattice.whiteboard\n(Constraint Graph & Skeletons)"] -- "ContextView" --> Table
    Table -- "TableView (LOD_0 / LOD_1 / LOD_2)" --> Exec
    Exec -- "COMMIT_TO_WHITEBOARD" --> WB
    Exec -- "SEND_BACK_TO_USER (Conflict / Gap)" --> User
    WB -- "Committed Rules" --> Mem
    GC["6. lattice.graph_core\n(SQLite Store & Activation)"] -. "Storage" .-> WB
    GC -. "Storage" .-> Mem
    GC -. "Storage" .-> Table
```

### 1. The 4-Layer Verification Stack
Behavioral unit tests alone leave internal program structure underdetermined ([*Skeleton Coding*, `arXiv:2610.38214`](https://arxiv.org/abs/2610.38214)): an LLM can pass a unit test with a hardcoded workaround while ignoring your architecture. Lattice verifies both the architecture graph and the implementation across four layers:
1. **Graph Completeness & Interval Satisfiability (`whiteboard/frustration.py`):** A board only reaches `PROVEN_COMPLETE` when every `@ COMPONENT` has an owner, file path, typed `$ INTERFACE` contract, connected `> FLOW` edge, and at least one `% LAW` invariant, with `0` open structural gaps and `0` contradictory `BOUND`/`PROHIBITION` intervals.
2. **Formal Dependent-Type Proofs in Bend 2 (`LATTICE_PROOF.bend` & `LAWS.bend`):** Every `% LAW` (`Args`, `LHS`, `RHS`) is compiled into a formal equality theorem (`LHS == RHS` proven via `rewrite` reflexivity and inductive case analysis over `Bool`/`Nat`) and checked by the **Bend 2 proof checker (`bend check`)**.
3. **AST Signature, Schema & Call-Graph Gate (`the_executive/gate.py`):** Before every commit (`lattice_git_sync(mode="pre")`), Lattice parses staged files to verify that every declared `# SCHEMA` class (including `> FLOW` payload schemas) and `$ INTERFACE` function exists with its required fields/parameters, every `$ INTERFACE` invokes its governing `% LAW` helper function(s) in the static call graph (across single- or multi-file components), and no `VERIFIED` file was modified without marking its component `STALE`.
4. **Truth-Table & Mutation Parity (`bend_gate.py`):** Compiles Bend 2 theorems to `.mjs`, exhaustively evaluates all boolean input combinations to verify exact truth-table parity with the Python `% LAW` helpers, and runs incremental law-mutation checks (`--only-law`).

### 2. Directed Spreading Activation vs. Naive GraphRAG (`lattice.ai_memory`)
If you put a codebase into a naive dependency graph and walk 2 undirected hops from `auth_service`, hop 1 hits a shared utility (`types.py` or `graph_core`), and hop 2 bounces out to **every module in the repository that imports `types.py`** (`94.7%` hub noise). Conversely, restricting retrieval to 1 hop misses multi-stage data pipelines (`gateway -> router -> auth -> token_store`).

`lattice.ai_memory` ([`hippocampal_index.py`](lattice/ai_memory/hippocampal_index.py) and [`uri_resolver.py`](lattice/ai_memory/uri_resolver.py)) solves this with three mechanisms:
1. **Pointer-Only Storage & Git-Synchronized URIs:** `ai_memory` never copies raw source files into a vector database. It stores concept nodes holding `uri://file/<path>#L<start>-L<end>` pointers and local `_helper#L<start>-L<end>` call trees. When files are renamed (`git mv`) or line ranges shift on commit, [`sync_git_diff_uris`](lattice/ai_memory/uri_resolver.py) automatically heals the pointers and refreshes AST line anchors.
2. **Causal Edge-Type Separation (Data Flows vs. Shared Utilities):**
   - **Data-Flow Edges (`> FLOW`) & Scope Hierarchy:** Propagate activation both forward and backward along the pipeline (up to 6 hops) so multi-step data paths stay connected.
   - **Shared Utility Dependencies (`depends_on`):** Pulled **1 hop forward** (`caller -> utility`) into a **terminal contract sink**. You get the utility's interface contract without ever bouncing backward into unrelated callers (`0%` hub noise).
3. **Two-Ended Pipeline Bridge Recall:** When a task mentions two components at opposite ends of a pipeline (e.g., `web_gateway` and `cursor_engine`), `recall_concepts` propagates a forward wave from the upstream component and a backward wave from the downstream component. Intermediate pipeline stages where both waves meet automatically get boosted into the active context slice—surfacing the full connecting path across up to 5 hops without pulling in unrelated branches.

### 3. Bounded Working Context (`lattice.the_table`) & Markovian Board Updates (`lattice.whiteboard`)
- **Three Levels of Detail (`LOD_0` / `LOD_1` / `LOD_2`):** `lattice.the_table` bounds active working memory (`max_table_slots = 16`, `max_table_tokens = 1200`), mounting `LOD_0` (`lattice_atlas`) for global topology/spec queries, `LOD_1` (50-word synopses) for neighboring components, and `LOD_2` (full contracts + `#L<start>-L<end>` code slices) for active targets.
- **Markovian Overwrite & Scope Mitosis:** Re-submitting a rule on `(Component, Property)` or a flow on `(Source, Target, Payload)` updates the existing entry in place (`MARKOVIAN_OVERWRITE`) so superseded rules and duplicate edges never accumulate. When any component exceeds `k_cap` rules (default `7`), it automatically splits along metadata axes into child sub-scopes (`comp::subscope`).

---

## Configuration (`config.json`) & Limitations

### Zero-LLM Runtime & `config.json`
Every Lattice operation is **100% deterministic Python, SQLite, `ast`, and Bend 2**:
- **`0` LLM API calls & `0` API keys required**
- **`0 GB` local model weights / GPU RAM (`100%` Python 3.11+ standard library)**
- **`< 50ms` turn compilation and pre-commit verification**

All gate policies and working-memory budgets live in [`config.json`](config.json):

| Config Key | Default | What It Controls |
| :--- | :---: | :--- |
| `gate.max_file_lines` | `null` *(off)* | Optional maximum line count per file enforced by `git-sync --pre-commit` and `status ... VERIFIED` (can also be set per-component via a `Rule` table `BOUND` row). |
| `gate.verify_files_exist_on_status` | `true` | Verifies that all declared component files exist on disk (and pass `max_file_lines` if set) before allowing `lattice_status(..., new_status="VERIFIED")`. |
| `gate.require_call_graph_laws` | `true` | Requires every `$ INTERFACE` function in a staged `.py` file to invoke its component's `% LAW` helper functions in the AST call graph. |
| `lattice.k_cap` | `7` | Maximum rules per scope before automatic sub-scope mitosis (`comp::subscope`). |
| `lattice.max_table_slots` / `max_table_tokens` | `16` / `1200` | Working-memory slot and token budget for `lattice.the_table`. |
| `lattice.hub_bottleneck_min_dependents` | `3` | Minimum number of downstream dependents before a component is flagged in `hub_bottlenecks` on `lattice_atlas`. |

### Known Limitations (What Lattice Does Not Replace)
1. **Static AST & Boolean Law Proofs Complement Behavioral Tests:**
   - The pre-commit gate (`evaluate_git_commit_gate` + `verify_ast_call_graph_skeleton`) and Bend 2 (`bend check`) prove that no `VERIFIED` component was edited without marking it `STALE`, declared `# SCHEMA` classes/fields and `$ INTERFACE` functions/parameters exist in the AST, every `$ INTERFACE` calls its governing `% LAW` helper without discarding the return value, and every `% LAW` formula (`LHS == RHS`) is mathematically sound.
   - However, static checks cannot prove that an algorithm computes the right intermediate values before passing booleans to a `% LAW` helper, or that SQL queries, I/O, or runtime state mutations behave correctly. Component-scoped unit and producer $\rightarrow$ consumer seam tests (`pytest` on `changed_scopes | stale_dependents`) remain essential.
2. **Enforcement Is Bounded by What Is Declared on the Board:**
   - `evaluate_git_commit_gate` verifies schemas, interfaces, laws, bounds, and import prohibitions registered on the board (and flags `unregistered_public_symbols` on modified files). If an internal helper introduces an undeclared side effect not governed by a board rule, promote a new `Rule`, `# SCHEMA`, or `% LAW` onto the board via `lattice_turn`.

---

## License

Licensed under the **Functional Source License, Version 1.1, Apache 2.0 Future License (`FSL-1.1-Apache-2.0`)**. See [LICENSE](LICENSE) for full terms.
