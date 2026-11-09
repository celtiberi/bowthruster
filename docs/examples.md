# Lattice Workflow Examples

Practical examples of how AI coding agents use `lattice-mcp` for multi-agent coordination, targeted QA, architectural audits, and living documentation.

---

## 1. Multi-Agent Parallel Dispatch (`lattice_turn` $\rightarrow$ `STALE` Cascade $\rightarrow$ Subagents)

When an architectural change updates a shared data structure or API, a single agent editing everything sequentially is slow—and multiple agents editing files without boundaries step on each other's work.

Lattice coordinates multi-agent work using **`STALE` cascades** and **wave-ordered `WorkPacket`s**:

```mermaid
flowchart LR
    Lead["Lead / Architect Agent"] -- "1. lattice_turn\n(Update Schema/Flow)" --> Board[("Lattice Board\n(.whiteboard)")]
    Board -- "2. Auto-Cascade STALE\n(Wave 0: Producer, Wave 1: Consumers)" --> Packets["lattice_packets(status='STALE')"]
    Packets -- "Wave 0 Packet\n(auth_service)" --> AgentA["Subagent A\n(src/auth.py)"]
    Packets -- "Wave 1 Packet\n(api_gateway)" --> AgentB["Subagent B\n(src/gateway.py)"]
    Packets -- "Wave 1 Packet\n(billing_service)" --> AgentC["Subagent C\n(src/billing.py)"]
    AgentA & AgentB & AgentC -- "3. lattice_git_sync(mode='pre', mark_verified=True)" --> Gate["Pre-Commit Gate\n(VERIFIED)"]
```

### Step-by-Step Multi-Agent Flow

1. **Lead Agent Updates the Contract on the Board (`lattice_turn`):**
   Suppose `auth_service` adds a new field (`tenant_id: str`) to the `AuthToken` schema that flows into `api_gateway` and `billing_service`:
   ```markdown
   | Schema | Component | Fields | Owner | Stage | File | Spec |
   | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
   | AuthToken | auth_service | user_id: str, tenant_id: str, expires_at: int | backend | 1 | src/auth.py | Spec 1 |

   | Flow | Source | Target | Payload | Condition |
   | :--- | :--- | :--- | :--- | :--- |
   | auth_to_gateway | auth_service | api_gateway | AuthToken | on authenticated request |
   | auth_to_billing | auth_service | billing_service | AuthToken | on metered API call |
   ```
   - `lattice_turn` updates `AuthToken` in place and **automatically transitions `auth_service` (producer) and `api_gateway` + `billing_service` (downstream consumers) from `VERIFIED` to `STALE`**, while leaving all other components `VERIFIED`.

2. **Lead Agent Pulls Wave-Ordered `WorkPacket`s (`lattice_packets`):**
   - Calling `lattice_packets(board="my_board", status="STALE")` returns self-contained packets ordered by dependency wave:
     - **Wave 0 (`auth_service`):** Contains `auth_service`'s contract slice, declared `Files` (`src/auth.py`), and `AuthToken` schema requirements.
     - **Wave 1 (`api_gateway`, `billing_service`):** Contains each consumer's contract slice, upstream `> FLOW` payload (`AuthToken`), and declared `Files`.

3. **Subagents Execute in Parallel Without Collisions:**
   - The Lead Agent dispatches a subagent for each `STALE` component with its `WorkPacket`.
   - **Scope Isolation (`verified_scope_edit_guard_law`):** If Subagent B (`api_gateway`) accidentally edits a `VERIFIED` file belonging to another component, `lattice_git_sync(mode="pre")` blocks the commit and names the offending file.
   - **Concurrency Safety:** `lattice-mcp` guards board reads/writes with cross-process file locks (`flock`), so parallel subagents can call `lattice_slice` and `lattice_git_sync` simultaneously.

---

## 2. Targeted QA & Producer $\rightarrow$ Consumer Seam Testing

Standard unit test suites fail in two ways with AI agents:
1. **Mock Drift:** Agent A updates `auth_service` to return `AuthToken(user_id=..., tenant_id=..., expires_at=...)`. Agent B's unit test for `billing_service` still mocks the old return value, so all unit tests pass while the real pipeline crashes.
2. **Slow Full-Repo Test Runs:** Running the entire test suite after every small edit wastes minutes.

### How Lattice Changes the QA Loop

1. **Blast-Radius Test Selection (`changed_scopes | stale_dependents`):**
   When an agent runs `lattice_git_sync(board="my_board", mode="pre")`, Lattice identifies the exact modified components and their downstream `> FLOW` dependents. Instead of running the entire test suite, the QA agent runs only the affected component tests:
   ```bash
   pytest -k "auth_service or api_gateway or billing_service"
   ```

2. **Static `> FLOW` Payload Schema Verification:**
   Before tests even run, `lattice_git_sync(mode="pre")` checks `flow_schema_checked`: for every `> FLOW Source --[Payload]--> Target` where `Payload` is a `# SCHEMA`, Lattice parses the AST to verify the `# SCHEMA` class exists with all declared fields.

3. **Unmocked Producer $\rightarrow$ Consumer Seam Tests:**
   Every `> FLOW Source --[Payload]--> Target` edge on the board defines a required **unmocked integration seam**. When a QA agent (or coding agent) sees:
   ```text
   @ FLOW auth_service --[AuthToken]--> billing_service
   ```
   it writes or runs a seam test that calls the real `auth_service` interface and feeds its actual output directly into `billing_service` with zero mocks:
   ```python
   def test_seam_auth_service_to_billing_service():
       token = verify_token(raw_jwt)          # Producer ($ INTERFACE on auth_service)
       receipt = record_usage(token, units=1) # Consumer ($ INTERFACE on billing_service)
       assert isinstance(token, AuthToken)
       assert receipt.tenant_id == token.tenant_id
   ```

---

## 3. Architectural Audit & Mechanical Pre-Commit Gate Catches

### A. Auditing a Codebase Using `Charter` + Component `Why`

Once a repository is mapped onto a Lattice board with a project `Charter` and component `Why` rationales, your AI agent can evaluate whether the code actually respects its intended boundaries.

Prompt your agent:
> *`"Inspect lattice_atlas(board='my_board', spec=True) and the component slices. Where does the current implementation violate a component's Why rationale or the Charter principles?"`*

Typical issues the agent catches from the board + AST slices:
- **Cross-Component State Mutation:** A request handler (`api_gateway`) directly mutating billing ledger state instead of passing an event payload along a `> FLOW` edge to `billing_service`.
- **Boundary Leakage:** A downstream service reaching into private `_parse_claims` helpers owned by `auth_service` instead of consuming the declared `$ INTERFACE` (`verify_token`).

### B. What the Pre-Commit Gate Catches Automatically (`lattice_git_sync(mode="pre")`)

Even if an agent forgets a structural rule during coding, `lattice_git_sync(board="my_board", mode="pre", mark_verified=True)` blocks the commit when any of the following occur:

| Gate Violation | What the Agent Did Wrong | How Lattice Blocks It |
| :--- | :--- | :--- |
| **`VERIFIED_SCOPE_EDIT_VIOLATION`** | Edited a file in a `VERIFIED` component without marking it `STALE` first (`mark_stale=True` or `lattice_turn`). | Rejects the commit and lists the modified `VERIFIED` file path. |
| **`CONTRACT_VIOLATION`** | Renamed/deleted a declared `$ INTERFACE` function or `# SCHEMA` class, or dropped a required parameter. | Parses the staged file's AST and reports the missing symbol or parameter. |
| **`CALL_GRAPH_LAW_VIOLATION`** | Wrote a `% LAW` helper function (`is_token_valid`) but never called it inside `verify_token`, or discarded its result (`_ = is_token_valid(...)`). | Walks the static AST call graph across the component's files and rejects disconnected or discarded law calls. |
| **`BEND_PROOF_FAILURE`** | Changed a Python `% LAW` helper (`is_token_valid`) to `return True` or `not exp or sig` instead of `not exp and sig`. | Transpiles the staged Python function's AST into Bend 2, checks `LHS == RHS` across all boolean assignments and via `bend`, and rejects the commit. |
| **`BOUND_VIOLATION`** | Exceeded `max_file_lines` (e.g., `<= 500`) or set a module constant `MAX_RETRIES = 10` when a `Rule` requires `MAX_RETRIES <= 3`. | Checks file line counts and top-level AST numeric constants against active `BOUND` rules. |
| **`PROHIBITION_VIOLATION`** | Added `import requests` or `import torch` in a component with a `PROHIBITION` rule on that library. | Parses AST `import` / `from ... import` nodes and blocks prohibited modules. |

---

## 4. Setting Rules & Policies via `lattice_turn` (`BOUND` vs. `PROHIBITION` vs. `POLICY`)

Every rule or policy in Lattice is submitted using the same `| Rule | Component | Type | Property | Op | Value | Condition | Spec |` pipe table via `lattice_turn`.

The **`Type`** column tells Lattice whether the rule is **mechanically enforced by the pre-commit AST gate** or is a **semantic standing instruction** surfaced to coding and QA agents in slices, `WorkPacket`s, and living specs:

| Rule `Type` | What It Is | Enforced by Pre-Commit AST Gate? | When to Use |
| :--- | :--- | :--- | :--- |
| **`BOUND`** | A numeric threshold on file length (`max_file_lines`) or a module-level Python constant (`MAX_RETRIES <= 3`). | **Yes** — blocks commits when a file exceeds `max_file_lines` or a top-level constant violates `Op Value`. | File size limits, timeouts, retry caps, batch sizes, latency/budget constants. |
| **`PROHIBITION`** | A banned Python import or module dependency (`Property = requests`, `Op = !=`, `Value = imported`). | **Yes** — blocks commits if any `.py` file imports the prohibited package. | Layer boundaries (e.g., keeping `sqlalchemy` or `requests` out of pure domain logic). |
| **`POLICY`** *(or `OBLIGATION`)* | A semantic engineering rule or architectural convention that requires human/LLM judgment rather than a brittle AST regex. | **No** — stored on the board and surfaced in `lattice_atlas` (for `global_defaults`) or `lattice_slice` / `lattice_packets` (for component-scoped rules). | Architectural principles (e.g., no silent output truncation, UTC-only timestamps, idempotent retries). |

### Scope: Repo-Wide (`global_defaults`) vs. Single Component

- **Repo-Wide (`Component = global_defaults`):** Displayed **once** at the top of `lattice_atlas` (under `@ CHARTER` as `* GLOBAL_RULE`) and in `lattice_atlas(spec=True)`, so calling `lattice_slice` 10 times during a session never repeats global rules 10 times in the agent's context window.
- **Single Component (`Component = <component_name>`):** Displayed **only** when slicing or pulling a `WorkPacket` for that specific component (`lattice_slice(component="...")` or `lattice_packets`), keeping rules scoped to where they actually apply.

### Example: Submitting `BOUND`, `PROHIBITION`, and `POLICY` Rules (`lattice_turn`)

```markdown
| Rule | Component | Type | Property | Op | Value | Condition | Spec |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| max_file_lines | global_defaults | BOUND | max_file_lines | <= | 500 | — | Every source file must be at most 500 lines |
| no_silent_truncation | global_defaults | POLICY | no_silent_truncation | == | true | — | Never silently truncate strings, lists, or diagnostic outputs ([:N]) to hide data |
| max_retries | billing_service | BOUND | MAX_RETRIES | <= | 3 | — | Billing webhook delivery must cap retries at 3 attempts |
| no_orm_in_auth | auth_service | PROHIBITION | sqlalchemy | != | imported | — | Auth token verification must stay stateless and never import sqlalchemy |
| utc_timestamps | billing_service | POLICY | utc_timestamps | == | true | — | All billing ledger timestamps must be stored and compared in UTC |
```

### Updating or Overwriting an Existing Rule

To change a rule later (for example, tightening `max_file_lines` from `500` to `400` or updating a `POLICY`), submit a `Rule` table with the **same `Component`, `Property`, and `Condition`**. Lattice performs a **Markovian overwrite** in place rather than accumulating conflicting duplicate rules.

---

## 5. Generating a Living Specification & Mermaid Diagram (`lattice_atlas(spec=True)`)

Because the Lattice board stores the project `Charter`, component `Role` and `Why`, directed `> FLOW` edges, `#L<start>-L<end>` `Interfaces`, `Schemas`, `Laws`, and `Rules`, you never have to write or update `ARCHITECTURE.md` by hand.

### Via Your AI Agent (MCP)
> *`"Call lattice_atlas(board='my_board', spec=True) and write the output to ARCHITECTURE.md."`*

### Via the Command Line (CLI)
```bash
PYTHONPATH=/path/to/lattice python3 -m lattice.cli --board my_board atlas --spec > ARCHITECTURE.md
```

This compiles a complete Markdown document containing:
1. **Project Charter & Standing Global Rules** (`Summary`, `Principles`, global `OBLIGATION` / `PROHIBITION` rules).
2. **System Topology Mermaid Diagram (`flowchart LR`) & Flow Table** showing every component, its verification status and owner, and every typed `> FLOW` edge.
3. **Per-Component Reference** with `Purpose (Role)`, `Why It Exists (Rationale)`, `Depends On`, `Files`, `#L<start>-L<end>` `Interfaces`, `Schemas`, `Formal Laws (Bend 2)`, and `Bounds`.
