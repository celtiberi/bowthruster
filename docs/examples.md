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
    Packets -- "Wave 0 Packet\n(cursor_engine)" --> AgentA["Subagent A\n(cursor.py)"]
    Packets -- "Wave 1 Packet\n(mix_play)" --> AgentB["Subagent B\n(mix_play.py)"]
    Packets -- "Wave 1 Packet\n(turn_brief)" --> AgentC["Subagent C\n(turn_brief.py)"]
    AgentA & AgentB & AgentC -- "3. lattice_git_sync(mode='pre', mark_verified=True)" --> Gate["Pre-Commit Gate\n(VERIFIED)"]
```

### Step-by-Step Multi-Agent Flow

1. **Lead Agent Updates the Contract on the Board (`lattice_turn`):**
   Suppose `cursor_engine` adds a new field (`review_due: list[str]`) to the `TaughtSet` schema that flows into `mix_play` and `turn_brief`:
   ```markdown
   | Schema | Component | Fields | Owner | Stage | File | Spec |
   | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
   | TaughtSet | cursor_engine | taught_ids: list, review_due: list | tutor | 1 | tutor/cursor.py | Spec 1 |

   | Flow | Source | Target | Payload | Condition |
   | :--- | :--- | :--- | :--- | :--- |
   | cursor_to_mix | cursor_engine | mix_play | TaughtSet | on candidate selection |
   | cursor_to_brief | cursor_engine | turn_brief | TaughtSet | on prompt assembly |
   ```
   - `lattice_turn` updates `TaughtSet` in place and **automatically transitions `cursor_engine` (producer) and `mix_play` + `turn_brief` (downstream consumers) from `VERIFIED` to `STALE`**, while leaving all other components `VERIFIED`.

2. **Lead Agent Pulls Wave-Ordered `WorkPacket`s (`lattice_packets`):**
   - Calling `lattice_packets(board="my_board", status="STALE")` returns self-contained packets ordered by dependency wave:
     - **Wave 0 (`cursor_engine`):** Contains `cursor_engine`'s contract slice, declared `Files` (`tutor/cursor.py`), and `TaughtSet` schema requirements.
     - **Wave 1 (`mix_play`, `turn_brief`):** Contains each consumer's contract slice, upstream `> FLOW` payload (`TaughtSet`), and declared `Files`.

3. **Subagents Execute in Parallel Without Collisions:**
   - The Lead Agent dispatches a subagent for each `STALE` component with its `WorkPacket`.
   - **Scope Isolation (`verified_scope_edit_guard_law`):** If Subagent B (`mix_play`) accidentally edits a `VERIFIED` file belonging to another component, `lattice_git_sync(mode="pre")` blocks the commit and names the offending file.
   - **Concurrency Safety:** `lattice-mcp` guards board reads/writes with cross-process file locks (`flock`), so parallel subagents can call `lattice_slice` and `lattice_git_sync` simultaneously.

---

## 2. Targeted QA & Producer $\rightarrow$ Consumer Seam Testing

Standard unit test suites fail in two ways with AI agents:
1. **Mock Drift:** Agent A updates `cursor_engine` to return `{"taught_ids": [...], "review_due": [...]}`. Agent B's unit test for `mix_play` still mocks the old return value, so all unit tests pass while the real pipeline crashes.
2. **Slow Full-Repo Test Runs:** Running the entire test suite after every small edit wastes minutes.

### How Lattice Changes the QA Loop

1. **Blast-Radius Test Selection (`changed_scopes | stale_dependents`):**
   When an agent runs `lattice_git_sync(board="my_board", mode="pre")`, Lattice identifies the exact modified components and their downstream `> FLOW` dependents. Instead of running the entire test suite, the QA agent runs only the affected component tests:
   ```bash
   pytest -k "cursor_engine or mix_play or turn_brief"
   ```

2. **Static `> FLOW` Payload Schema Verification:**
   Before tests even run, `lattice_git_sync(mode="pre")` checks `flow_schema_checked`: for every `> FLOW Source --[Payload]--> Target` where `Payload` is a `# SCHEMA`, Lattice parses the AST to verify the `# SCHEMA` class exists with all declared fields.

3. **Unmocked Producer $\rightarrow$ Consumer Seam Tests:**
   Every `> FLOW Source --[Payload]--> Target` edge on the board defines a required **unmocked integration seam**. When a QA agent (or coding agent) sees:
   ```text
   @ FLOW cursor_engine --[TaughtSet]--> mix_play
   ```
   it writes or runs a seam test that calls the real `cursor_engine` interface and feeds its actual output directly into `mix_play` with zero mocks:
   ```python
   def test_seam_cursor_engine_to_mix_play():
       taught_set = compute_taught_set(session_state)  # Producer ($ INTERFACE)
       mix_plan = select_mix_candidates(taught_set)    # Consumer ($ INTERFACE)
       assert isinstance(taught_set, TaughtSet)
       assert mix_plan.valid
   ```

---

## 3. Architectural Audit & Mechanical Pre-Commit Gate Catches

### A. Auditing a Codebase Using `Charter` + Component `Why`

Once a repository is mapped onto a Lattice board with a project `Charter` and component `Why` rationales, your AI agent can evaluate whether the code actually respects its intended boundaries.

Prompt your agent:
> *`"Inspect lattice_atlas(board='my_board', spec=True) and the component slices. Where does the current implementation violate a component's Why rationale or the Charter principles?"`*

Typical issues the agent catches immediately from the board + AST slices:
- **Cross-Component State Mutation:** A grading component (`jev_judge`) directly mutating `session.sheet` instead of returning a `GradeMutation` payload along a `> FLOW` edge to `cursor_engine`.
- **Boundary Leakage:** A prompt-rendering stage reaching into private `_brief_fields` helpers owned by another component instead of consuming the declared `$ INTERFACE`.

### B. What the Pre-Commit Gate Catches Automatically (`lattice_git_sync(mode="pre")`)

Even if an agent forgets a rule during coding, `lattice_git_sync(board="my_board", mode="pre", mark_verified=True)` blocks the commit when any of the following occur:

| Gate Violation | What the Agent Did Wrong | How Lattice Blocks It |
| :--- | :--- | :--- |
| **`VERIFIED_SCOPE_EDIT_VIOLATION`** | Edited a file in a `VERIFIED` component without marking it `STALE` first (`mark_stale=True` or `lattice_turn`). | Rejects the commit and lists the modified `VERIFIED` file path. |
| **`CONTRACT_VIOLATION`** | Renamed/deleted a declared `$ INTERFACE` function or `# SCHEMA` class, or dropped a required parameter. | Parses the staged file's AST and reports the missing symbol or parameter. |
| **`CALL_GRAPH_LAW_VIOLATION`** | Wrote a `% LAW` helper function (`is_token_valid`) but never called it inside `verify_token`, or discarded its result (`_ = is_token_valid(...)`). | Walks the static AST call graph across the component's files and rejects disconnected or discarded law calls. |
| **`BOUND_VIOLATION`** | Exceeded `max_file_lines` (e.g., `<= 500`) or set a module constant `MAX_RETRIES = 10` when a `Rule` requires `MAX_RETRIES <= 3`. | Checks file line counts and top-level AST numeric constants against active `BOUND` rules. |
| **`PROHIBITION_VIOLATION`** | Added `import requests` or `import torch` in a component with a `PROHIBITION` rule on that library. | Parses AST `import` / `from ... import` nodes and blocks prohibited modules. |

---

## 4. Generating a Living Specification & Mermaid Diagram (`lattice_atlas(spec=True)`)

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
