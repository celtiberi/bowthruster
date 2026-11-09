# Spec 22A — `whiteboard` Schema, SQLite Persistence & Write Mutations (`schema.py`, `mutations.py`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-06 · **Assigned Engineer:** `eng1` (`ModelCircuits/eng1/whiteboard/{schema.py,mutations.py}` $\rightarrow$ Promoted `ModelCircuits/core/circuit/whiteboard/{schema.py,mutations.py}`)  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md) · **Formal Laws:** [`LAWS.bend`](LAWS.bend) · **Foundation Dependency:** [`../graph_core/21_GRAPH_CORE_AND_SQLITE_ENGINE.md`](../graph_core/21_GRAPH_CORE_AND_SQLITE_ENGINE.md)

---

## 1. Strict Isolation Boundary for `eng1` (`Spec 22A`)

`eng1` owns **only** the schema validation, SQLite persistence binding, and write-time topology/memory mutation layer of `whiteboard`:
- **Allowed Writable Files in `ModelCircuits/eng1/`:**
  - `ModelCircuits/eng1/whiteboard/schema.py` ($\le 500$ lines, zero `import re`)
  - `ModelCircuits/eng1/whiteboard/mutations.py` ($\le 500$ lines, zero `import re`)
  - `ModelCircuits/eng1/bend_wb_22a/{LAWS.bend,main.bend,PROOF.bend,main.mjs}`
- **Forbidden to `eng1`:** `eng1` may **not** create or edit `frustration.py` (`Spec 22B`, owned by `eng3`) or `linker.py`, `rewriter.py`, `ingest.py`, `read_engine.py`, `__init__.py` (`Spec 22C`, owned by `eng2`).

---

## 2. Module Contracts (`schema.py` & `mutations.py`)

### 2.1 `schema.py` (`the_lattice` / Whiteboard Types, Invariant Verification, & `SQLiteGraphStore` Binding)
Imports shared primitives (`SQLiteGraphStore`, `tokenize_content_words`, `clamp_rule_meta`, `token_cost`, `format_compact_view`) from `core.circuit.graph_core`:
- **Constants & Types:**
  - `DEFAULT_K_CAP = 5`, `DEFAULT_MAX_RING = 10`, `MAX_META_SLOTS = 3`, `MAX_META_SLOT_WORDS = 25`, `MAX_META_TOTAL_WORDS = 75`, `MAX_META_KEY_WORDS = 5`.
  - `_TYPE_TO_FACET`: maps `"PROHIBITION"` / `"REVOCATION"` $\rightarrow$ `"prohibitions"`, `"OBLIGATION"` / `"POLICY"` $\rightarrow$ `"obligations"`, `"BOUND"` $\rightarrow$ `"Numeric_Bounds"`, `"ACTION"` / `"COMMIT"` $\rightarrow$ `"actions"`, `"FACT"` / `"GOAL"` / `"PROPOSAL"` $\rightarrow$ `"context_facts"`, `"QUESTION"` $\rightarrow$ `"open_questions"`.
  - `LATTICE_KINDS = ("COMPONENT", "FLOW", "SCHEMA", "INTERFACE", "LAW", "CONSTRAINT", "FACT", "TASK")`
  - `BUILD_STATUSES = ("DRAFT", "BLOCKED_BY_DEP", "READY", "IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE")`
  - `ContextGraphInvariantError(code: str, errors: list[str])`
  - `RuleEntry(dict[str, Any])`, `FlowEntry(dict[str, Any])`, `ArtifactEntry(dict[str, Any])`, `ScopeNode(dict[str, Any])` (supporting SBF & lifecycle fields `role: str | None`, `inputs: list[str]`, `outputs: list[str]`, `depends_on: list[str]`, `owner: str | None`, `stage: str | None`, `kind: str | None`, `status: str | None`, `build_tag: str | None`, `core_tag: str | None`, `failure_diagnostic: str | None`), `ContextGraph(dict[str, Any])` (and aliases `WhiteboardGraph = ContextGraph`, `LatticeGraph = ContextGraph`), supporting `.to_dict()`, attribute access, and an optional attached `_sqlite_store: SQLiteGraphStore | None` with `sync_to_sqlite(graph) -> None`.
- **Bend 2-Backed Pure Lattice Partition, Ghost-Scope, & Build Lifecycle Guards (`schema.py`):**
  - `classify_lattice_store(lattice_kind: str) -> str`: governed by **Bend 2 Law 4 (`lattice_sbf_never_enters_rule_store`)**:
    - `"COMPONENT"` $\rightarrow$ `"SCOPES"`
    - `"FLOW"` $\rightarrow$ `"FLOWS"`
    - `"SCHEMA" | "INTERFACE" | "LAW"` $\rightarrow$ `"ARTIFACTS"`
    - `"TASK"` $\rightarrow$ `"TASKS"`
    - `"CONSTRAINT" | "FACT"` (and rule types `"BOUND" | "PROHIBITION" | "OBLIGATION" | "POLICY" | "GOAL" | "PROPOSAL"`) $\rightarrow$ `"RULES"`.
  - `should_register_as_scope(is_primary_scope: bool, is_flow_endpoint: bool, is_explicit_component: bool, is_bound_or_payload_val: bool = False) -> bool`: governed by **Bend 2 Law 5 (`ghost_scope_exclusion_law`)**—returns `bool(is_primary_scope or is_flow_endpoint or is_explicit_component)`, guaranteeing that a secondary token that is merely a `bound_val`, `target_prop`, or `payload` schema (`False, False, False, True`) never spawns an empty ghost scope in `graph["scopes"]`.
  - `step_component_lifecycle_on_edit(cur_status: str | None, is_spec_mutated: bool) -> str`: governed by **Bend 2 Law 6 (`verified_component_becomes_stale_on_spec_edit`)**—normalizes `None`/`""` to `"DRAFT"`; when `is_spec_mutated` is `True` and `cur_status in ("VERIFIED", "IN_QA")`, returns `"STALE"`; otherwise returns `cur_status`.
  - `resolve_packet_lifecycle_status(cur_status: str | None, is_lattice_proven: bool, all_deps_verified: bool) -> str`: governed by **Bend 2 Law 7 (`resolve_packet_lifecycle_status_law`)**—normalizes `None`/`""` to `"DRAFT"`; when `cur_status in ("IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE")`, returns `cur_status`; when `cur_status in ("DRAFT", "BLOCKED_BY_DEP", "READY")`, returns `"DRAFT"` if `not is_lattice_proven`, `"BLOCKED_BY_DEP"` if `is_lattice_proven and not all_deps_verified`, and `"READY"` if `is_lattice_proven and all_deps_verified`.
- **Factory & Validation Functions:**
  - `create_empty_graph(*, k_cap: int = 5, max_ring: int = 10, store: SQLiteGraphStore | None = None, db_path: str | Path | None = None) -> ContextGraph` (also exported as `create_empty_lattice`): initializes a clean `ContextGraph` with `"flows": []` and `"lattice_artifacts": []` alongside `"scopes"`, `"active_tasks"`, `"open_blockers"`, `"ambient_notes"`, and `"alias_index"`.
  - `validate_context_graph(graph: dict[str, Any], *, k_cap: int = 5, max_ring: int = 10) -> list[str]` and alias `verify_graph_invariants`: enforces Scope Rule Cap ($\le K$), Metadata Cap ($\le M$), Ambient Cap ($\le \text{max\_ring}$), Bidirectional Edge Symmetry (`refs`/`in_edges` and `overrides`/structured `exceptions`), and validates that every entry in `graph.get("flows", [])` has non-empty `source` and `target` strings and every entry in `graph.get("lattice_artifacts", [])` has `kind in ("SCHEMA", "INTERFACE", "LAW")` and non-empty `name`.

---

### 2.2 `mutations.py` (Write-Time Topology, nD Lattice SBF Upserts, Mitosis, & Blocker Resolution Operators)
Imports `actr_base_level_activation` and `evict_from_ring` from `core.circuit.graph_core` and `sync_to_sqlite` from `.schema`. Every mutation function automatically calls `sync_to_sqlite(graph)` before returning if `graph` has an attached `SQLiteGraphStore`:
- `ensure_scope_node(graph, scope_id, *, parent=None) -> dict[str, Any]`: ensures `scopes[scope_id]` exists as a `ScopeNode` (initializing SBF defaults `"role": None, "inputs": [], "outputs": [], "depends_on": [], "owner": None, "stage": None, "kind": None, "status": None`), wires `parent`/`children` links, registers `graph.setdefault("alias_index", {})[scope_id.lower()] = scope_id`, and syncs to SQLite.
- `upsert_component_spec(graph: dict[str, Any], scope_id: str, *, role: str | None = None, inputs: list[str] | None = None, outputs: list[str] | None = None, depends_on: list[str] | None = None, children: list[str] | None = None, parent: str | None = None, owner: str | None = None, stage: str | None = None, kind: str | None = None, status: str | None = None, current_turn: int = 1) -> dict[str, Any]`:
  - Ensures `scope_id` exists via `ensure_scope_node(graph, scope_id, parent=parent)`.
  - Updates any non-`None` SBF fields in place (Markovian overwrite on `role`, `inputs`, `outputs`, `depends_on`, `owner`, `stage`, `kind`, `status`), ensures any declared `children` exist with `parent=scope_id`, ensures any declared `depends_on` component scopes exist in `graph["scopes"]` (wiring `in_edges`), and syncs to SQLite.
- `upsert_flow_edge(graph: dict[str, Any], source: str, target: str, *, payload: str | None = None, action: str | None = None, condition: str | None = None, step: int | None = None, text: str = "", dimensions: dict[str, Any] | None = None, current_turn: int = 1) -> dict[str, Any]`:
  - Ensures both endpoint component scopes `source` and `target` exist via `ensure_scope_node` (never creating a scope for `payload`!).
  - Matches an existing flow in `graph.setdefault("flows", [])` if `(f["source"], f["target"], f.get("condition")) == (source, target, condition)` (Markovian update in place) or appends a new `FlowEntry` (`id = f"flow_{len(flows) + 1}"`), wires `target` into `scopes[source]["in_edges"]` / `scopes[target]["in_edges"]`, and syncs to SQLite.
- `upsert_lattice_artifact(graph: dict[str, Any], kind: str, name: str, *, scope_id: str = "global_defaults", spec: str = "", inputs: list[str] | None = None, outputs: list[str] | None = None, owner: str | None = None, stage: str | None = None, dimensions: dict[str, Any] | None = None, text: str = "", current_turn: int = 1) -> dict[str, Any]`:
  - Validates `kind in ("SCHEMA", "INTERFACE", "LAW")` and non-empty `name`.
  - Ensures `scope_id` exists via `ensure_scope_node(graph, scope_id)`.
  - Upserts in place in `graph.setdefault("lattice_artifacts", [])` when an artifact with `(a["kind"], a["scope_id"], a["name"].lower()) == (kind, scope_id, name.lower())` already exists, or appends a new `ArtifactEntry` (`id = f"{kind.lower()}_{scope_id}_{name.lower()}"`), and syncs to SQLite.
- `wire_bidirectional_refs(graph, rule, ref_scopes) -> None`
- `wire_lex_specialis_override(graph, exception_rule, general_rule, note="conditional exception") -> None`
- `markovian_overwrite_rule(graph, scope_id, old_rule_id, new_rule_data) -> dict[str, Any]`: mutates `old_rule_id` in place, venting superseded scalar while preserving original `rationale` and recording incoming `rationale` as `transition_rationale` in `_meta`.
- `prune_dysfluency_and_upsert_rule(graph, scope_id, rule_data) -> dict[str, Any]`
- `graduate_task_to_fact(graph, task_id, current_turn=1) -> dict[str, Any]`
- `upsert_ambient_note(graph, note, current_turn=None, max_ring=10) -> dict[str, Any]`
- `_partition_rules_for_mitosis(rules: list[dict[str, Any]], k_cap: int = 5) -> dict[str, list[dict[str, Any]]]` and `two_axis_mitosis(graph: dict[str, Any], scope_id: str | int | None = None, k_cap: int = 5) -> dict[str, Any]`: governed by **Bend 2 Law 3 (`mitosis_conserves_rule_count`)**.
- `resolve_open_blocker(graph: dict[str, Any], blocker_id: str, *, remove: bool = False) -> bool`: governed by **Bend 2 Laws 1–2 (`blocker_resolve_iff_id_matches`, `blocker_resolved_idempotent`)**.
- **Automatic Staleness Invalidation & Build Lifecycle Transitions (`mutations.py`, Laws 6–7):**
  - Whenever `upsert_component_spec` (when `status is None`), `upsert_flow_edge`, `upsert_lattice_artifact`, `markovian_overwrite_rule`, `prune_dysfluency_and_upsert_rule`, `replace_rule`, or `revoke_rule` mutates a scope `sid` (or a flow's `source`/`target`), it transitions `scopes[sid]["status"] = step_component_lifecycle_on_edit(scopes[sid].get("status"), True)` **and** also transitions any downstream scope `dep_sid` that lists `sid` in `scopes[dep_sid].get("depends_on", [])` or has `scopes[dep_sid].get("parent") == sid` via `step_component_lifecycle_on_edit(scopes[dep_sid].get("status"), True)` (so modifying a rule, law, flow, or schema on a `VERIFIED` component automatically marks that component and its dependent QA/child scopes as `"STALE"`!).
  - `transition_component_build_status(graph: dict[str, Any], scope_id: str, new_status: str, *, build_tag: str | None = None, core_tag: str | None = None, failure_diagnostic: str | None = None, current_turn: int = 1) -> dict[str, Any]`:
    - Validates `new_status.upper() in BUILD_STATUSES` and ensures `scope_id` exists in `graph["scopes"]` (resolving via `alias_index` if needed, or raising `KeyError` if unknown).
    - Sets `scopes[scope_id]["status"] = new_status.upper()`, updates `build_tag` / `core_tag` when provided, sets `failure_diagnostic` when `new_status.upper() == "FAILED"` (and clears `failure_diagnostic = None` when transitioning to `"READY"`, `"IN_PROGRESS"`, `"IN_QA"`, or `"VERIFIED"`), syncs to SQLite, and returns `scopes[scope_id]`.

---

## 3. Formal Bend 2 Laws (`docs/specs/whiteboard_22a/LAWS.bend` & `ModelCircuits/eng1/bend_wb_22a/`)

Every `eng1` build tag for `Spec 22A` must prove Laws 1–7 in [`LAWS.bend`](LAWS.bend) via `ModelCircuits/eng1/bend_wb_22a/PROOF.bend` (`scripts/bend_gate.py check ModelCircuits/eng1`).
