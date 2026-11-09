# Spec 22C — `whiteboard` Intake Linker, Task Readiness & Read Engine (`linker.py`, `rewriter.py`, `ingest.py`, `read_engine.py`, `__init__.py`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-06 · **Assigned Engineer:** `eng2` (`ModelCircuits/eng2/whiteboard/` $\rightarrow$ Promoted `ModelCircuits/core/circuit/whiteboard/`)  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md) · **Formal Laws:** [`LAWS.bend`](LAWS.bend)

---

## 1. Strict Isolation Boundary for `eng2` (`Spec 22C`)

`eng2` owns **only** the `IntakePacket` linker, task readiness classifier, read engine wrapper, and package `__init__.py` of `whiteboard`:
- **Allowed Writable Files in `ModelCircuits/eng2/`:**
  - `ModelCircuits/eng2/whiteboard/linker.py` ($\le 500$ lines, zero `import re`)
  - `ModelCircuits/eng2/whiteboard/rewriter.py` ($\le 500$ lines, zero `import re`)
  - `ModelCircuits/eng2/whiteboard/ingest.py` ($\le 500$ lines, zero `import re`)
  - `ModelCircuits/eng2/whiteboard/read_engine.py` ($\le 500$ lines, zero `import re`)
  - `ModelCircuits/eng2/whiteboard/__init__.py` ($\le 500$ lines, zero `import re`)
  - `ModelCircuits/eng2/bend_wb_22c/{LAWS.bend,main.bend,PROOF.bend,main.mjs}`
- **Forbidden to `eng2`:** `eng2` may **not** create or edit `schema.py`, `mutations.py` (`Spec 22A`, owned by `eng1`) or `frustration.py` (`Spec 22B`, owned by `eng3`). `eng2`'s modules **must import** `schema`, `mutations`, and `frustration` from `core.circuit.whiteboard` (promoted from `eng1` and `eng3`).

---

## 2. Module Contracts (`read_engine.py`, `linker.py`, `rewriter.py`, `ingest.py`, `__init__.py`)

### 2.1 `read_engine.py` (Whiteboard & nD Lattice Read Engine + Bend 2-Verified Task Readiness)
Imports `scan_nodes`, `propagate_nodes`, `expand_graph_node`, `matches_nd_slice`, `format_compact_view` from `core.circuit.graph_core` and `detect_frustration` from `core.circuit.whiteboard.frustration`:
- `prohibition_blocks_task(rule_has_prop: bool, task_has_prop: bool, props_match: bool, mentions_prohibited_val: bool) -> bool`: governed by **Bend 2 Laws 9–10 (`property_specific_prohibition_spares_untargeted_task`, `scope_wide_or_matching_prohibition_blocks_task`)**:
  - When `rule_has_prop == False` (scope-wide prohibition with `r.get("target_prop") is None`): returns `True`.
  - When `rule_has_prop == True` (property-specific prohibition with `r.get("target_prop") is not None`): returns `(task_has_prop and props_match) or mentions_prohibited_val`. In `_evaluate_task_flags`, `mentions_prohibited_val` is `True` iff the prohibition rule has a non-empty string `value` (`r_val = str(r.get("value") or "").strip().lower()`) that appears as a token/substring in the task's `summary` or `bound_value`. A task with `target_prop is None` (`task_has_prop == False`) is **never** blocked by a property-specific `PROHIBITION` unless `mentions_prohibited_val == True`.
- `classify_task_status(has_conflict: bool, has_prohibition: bool, has_unmet_dep: bool) -> str`: governed by **Bend 2 Law 7 (`task_ready_iff_all_clear`)**:
  - `has_conflict == True` $\rightarrow$ `"BLOCKED_BY_CONFLICT"`
  - `has_prohibition == True` $\rightarrow$ `"BLOCKED_BY_RULE"`
  - `has_unmet_dep == True` $\rightarrow$ `"WAITING_ON_DEPENDENCY"`
  - Otherwise $\rightarrow$ `"READY"`.
- `scan_graph(graph, query, temperature=None, token_budget=400, max_lines=35, *, max_tokens=None) -> dict[str, Any]`: delegates to `scan_nodes(..., conflict_detector=detect_frustration)` and annotates `active_tasks` via `classify_task_status`.
- `propagate_graph(graph, query, temperature=None, token_budget=400, max_lines=35, gamma=0.75, max_hops=4, *, max_steps=None, max_tokens=None) -> dict[str, Any]`: delegates to `propagate_nodes(..., conflict_detector=detect_frustration)` and annotates `active_tasks` via `classify_task_status`.
- `slice_lattice(graph: dict[str, Any], query: str = "", *, component: str | None = None, division: str | None = None, stage: str | None = None, schema: str | None = None, kind: str | None = None, token_budget: int = 400, max_lines: int = 35) -> dict[str, Any]`:
  - Computes an nD Hyperplane Slice over `graph` governed by **`Spec 21` Law 10 (`nd_coordinate_slice_exact` / `matches_nd_slice(is_unconstrained: bool, has_dim_overlap: bool) -> bool`)**:
    1. Evaluates `detect_frustration(graph)` and annotates `active_tasks` via `classify_task_status`.
    2. Filters `scopes` (`component_specs`), `flows` (`active_flows`), `lattice_artifacts` (`active_artifacts`), `rules` (`active_rules`), and `active_tasks` to the exact subset where `matches_nd_slice(is_unconstrained, has_dim_overlap)` returns `True` across every dimension `(component, division, stage, schema, kind)` (and query token overlap when `query.strip()` is non-empty):
       - `component`: `is_unconstrained = not bool(component)`; `has_dim_overlap` holds if `component` matches the node's `scope` / `scope_id` / `id` / `parent` / `source` / `target` or is in `depends_on` / `children`.
       - `division`: `is_unconstrained = not bool(division)`; `has_dim_overlap` holds if `division` matches the node's `owner` (or its parent scope's `owner`).
       - `stage`: `is_unconstrained = not bool(stage)`; `has_dim_overlap` holds if `stage` matches the node's `stage` (or its parent scope's `stage`).
       - `schema`: `is_unconstrained = not bool(schema)`; `has_dim_overlap` holds if `schema` matches `payload`, `name`, `target_prop`, or is in `inputs` / `outputs`.
       - `kind`: `is_unconstrained = not bool(kind)`; `has_dim_overlap` holds if `kind.upper()` matches the node's ontological kind (`COMPONENT`, `FLOW`, `SCHEMA`, `INTERFACE`, `LAW`, `CONSTRAINT`, `FACT`, `TASK`, or specific rule type `BOUND`/`PROHIBITION`/`OBLIGATION`). For `scopes` (`component_specs`), a scope matches `kind == "COMPONENT"` only if it is an SBF component (`s.get("kind") == "COMPONENT"` or `bool(s.get("role") or s.get("owner") or s.get("stage") or s.get("depends_on") or s.get("children") or s.get("parent"))`), excluding bare rule-bucket scopes (`goal`, property names, etc.).
    3. Renders `"compact_view"` via `format_compact_view(view, max_lines=max_lines, token_budget=token_budget)` on the sliced view (**without** passing duplicate `scope_headers`, since `format_compact_view` natively renders `component_specs`, `active_flows`, and `active_artifacts` once) and returns a `ContextView` dict containing `"status"`, `"component_specs"`, `"active_flows"`, `"active_artifacts"`, `"active_rules"`, `"active_tasks"`, `"blockers"`, and `"compact_view"`.
- `expand_node(graph, node_id: str) -> dict[str, Any]`: delegates to `expand_graph_node(graph, node_id)`.

### 2.2 `linker.py`, `rewriter.py`, `ingest.py` (Stage 4A–4C Whiteboard & nD Lattice Linker)
Imports `schema` (`should_register_as_scope`, `classify_lattice_store`) and `mutations` (`upsert_component_spec`, `upsert_flow_edge`, `upsert_lattice_artifact`) from `core.circuit.whiteboard` and `detect_frustration` from `core.circuit.whiteboard.frustration`:
- Governed by **Bend 2 Laws 1–6, Law 8, & Law 11** (`clarification_dominates_all_deltas`, `grounded_action_never_enters_rule_table`, `prohibition_guard_blocks_overwrite`, `prohibition_guard_symmetric`, `prohibition_guard_disjoint_intervals`, `ingestion_status_commitment_iff_clean`, `epistemic_partition_blocks_cross_kind_overwrite`, `lattice_delta_routes_sbf_kinds`).
- **Monotonic Rule ID Generation Across `replace_rule` / `revoke_rule` (`linker.py`):** When allocating a new `rule_id` (`f"r_{scope_id}_{next_num}"`) in `linker.py`, compute `next_num = 1 + max((int(rid.rsplit("_", 1)[-1]) for rid in graph.get("rules", {}) if rid.rsplit("_", 1)[-1].isdigit()), default=len(graph.get("rules", {})))` instead of `len(graph.get("rules", {})) + 1`, so deleting a rule via `replace_rule` or `revoke_rule` never reuses an existing numeric suffix or overwrites an unrelated rule on the same scope.
- **Ghost-Scope Prevention & Markdown Component Table Promotion in `rewriter.py` (`Spec 22A` Law 5 — `should_register_as_scope`):**
  - In `resolve_mcu_scopes_and_refs(graph, mcu)`: when iterating over `secondary_refs`, compute `lpayload = dict(mcu.get("lattice_payload") or {})` and `lattice_kind = str(mcu.get("lattice_kind") or "").upper()`, `artifact_ids = {str(x).strip().lower() for x in (lpayload.get("ids") or ([lpayload.get("id")] if lpayload.get("id") else [])) if str(x).strip()}`, `interface_io = {str(x).strip().lower() for x in list(lpayload.get("inputs") or []) + list(lpayload.get("outputs") or []) if str(x).strip()}`, and `division_owners = {"eng0", "eng1", "eng2", "eng3", "bethe", "bobo", "mike", "raymand", "sarah", "groves"}`. Compute `is_bound_or_payload_val = (ref.lower() in ({str(mcu.get("bound_val") or "").strip().lower(), str(mcu.get("target_prop") or "").strip().lower(), str(lpayload.get("payload") or "").strip().lower(), str(lpayload.get("owner") or "").strip().lower(), "laws.bend"} | artifact_ids | interface_io | division_owners) - {""})` and `is_flow_endpoint = (ref.lower() in {str(lpayload.get("source") or "").strip().lower(), str(lpayload.get("target") or "").strip().lower()} - {""})`. Call `_resolve_or_register_scope(graph, ref)` **only** when `should_register_as_scope(False, is_flow_endpoint, ref in graph.get("scopes", {}), is_bound_or_payload_val)` is `True`. Property names, bound values, law/schema/interface names, interface I/O arguments, division names, and flow payload names (`IntakePacket`) are **never** registered as empty ghost scopes in `graph["scopes"]`.
  - In `fuse_table_attributes(graph, mcu)`: when a Markdown table row (`table_row`) is fused into `scope`, in addition to updating `scope["attributes"]`, check for recognized SBF component columns (`role` / `purpose` / `responsibility`, `owner` / `division`, `stage` / `phase`, `parent`, `depends_on` / `dependencies`, `children`, `inputs`, `outputs`). Whenever any SBF column (`role`, `owner`/`division`, `stage`, `depends_on`, `children`, `inputs`, `outputs`) is present in `table_row`, sync those fields onto the top-level `ScopeNode` via `upsert_component_spec` (setting `scope["kind"] = "COMPONENT"`, `scope["role"]`, `scope["owner"]`, `scope["stage"]`, etc., where comma-separated cells for `depends_on`/`children`/`inputs`/`outputs` are split into clean token lists) so Markdown component tables automatically populate `@ COMPONENT` nodes in the Lattice.
- **SBF / Lattice Delta Routing in `linker.py` (`classify_lattice_delta`, Bend 2 Law 11):**
  - `classify_lattice_delta(lattice_kind: str, grounding_status: str, has_unclear: bool) -> str`:
    - If `grounding_status == "NEEDS_CLARIFICATION"` or `has_unclear`: returns `"BLOCKER_INSERT_CLARIFICATION"`.
    - If `lattice_kind == "COMPONENT"`: returns `"UPSERT_COMPONENT"`.
    - If `lattice_kind == "FLOW"`: returns `"UPSERT_FLOW"`.
    - If `lattice_kind == "SCHEMA"`: returns `"UPSERT_SCHEMA"`.
    - If `lattice_kind == "INTERFACE"`: returns `"UPSERT_INTERFACE"`.
    - If `lattice_kind == "LAW"`: returns `"UPSERT_LAW"`.
    - Otherwise (`lattice_kind in ("CONSTRAINT", "FACT", "TASK", "")`): delegates to `classify_delta_transition`.
  - When `classify_and_apply_delta` receives an `mcu` with `lattice_kind in ("COMPONENT", "FLOW", "SCHEMA", "INTERFACE", "LAW")` and `classify_lattice_delta` returns an `"UPSERT_*"` operation, it invokes `upsert_component_spec`, `upsert_flow_edge`, or `upsert_lattice_artifact` from `core.circuit.whiteboard.mutations` using `mcu.get("lattice_payload", {})` (never inserting SBF structural/behavioral/artifact nodes into `scopes[sid]["rules"]`). When `op in ("UPSERT_LAW", "UPSERT_SCHEMA", "UPSERT_INTERFACE")` and `lpayload.get("ids")` is a non-empty list of artifact identifiers (e.g., from a compound sentence like `"LAWS.bend defines law_a, law_b, and law_c"`), `classify_and_apply_delta` iterates over each `art_id` in `lpayload["ids"]` and calls `upsert_lattice_artifact(graph, art_id, kind=art_kind, ...)` for every listed artifact. When `mcu` is an `ACTION` task (`TASK`), `classify_and_apply_delta` also propagates `owner` and `stage` from `mcu.get("lattice_payload", {})` onto the `ActiveTask` dict.
- **Epistemic Partition on `MARKOVIAN_OVERWRITE` (`can_overwrite_node_kind` & `classify_delta_transition` in `linker.py`, Bend 2 Law 8):** `can_overwrite_node_kind(existing_type: str, incoming_type: str) -> bool` returns `True` iff either both are identical descriptive node types (`existing_type == incoming_type` for `FACT`/`GOAL`/`PROPOSAL`) or both are normative rule types (`existing_type not in ("FACT", "GOAL", "PROPOSAL", "QUESTION", "COMMIT", "ACTION")` and `incoming_type not in ("FACT", "GOAL", "PROPOSAL", "QUESTION", "COMMIT", "ACTION")`). A descriptive `FACT` (e.g. `file_lines == 388`) and a compatible normative `BOUND` (e.g. `file_lines <= 500`) on the same `target_prop` in the same scope never overwrite each other (`can_overwrite_node_kind == False`) and coexist via `ORTHOGONAL_INSERT`.
- **Continuous Proof Attachment & Parallel Work Packet Compiler in `ingest.py` (`compile_work_packets`):**
  - At the end of `ingest_into_context_graph`, attaches `graph["lattice_proof"] = evaluate_lattice_proof(graph)` (imported from `core.circuit.whiteboard.frustration`) and includes `"lattice_proof"` in the returned ingestion result dict.
  - `compile_work_packets(graph: dict[str, Any], *, force: bool = False, status_filter: str | None = None) -> dict[str, Any]` (in `ingest.py` and re-exported in `whiteboard/__init__.py` alongside `evaluate_lattice_proof`, `classify_lattice_proof_status`, `BUILD_STATUSES`, `step_component_lifecycle_on_edit`, `resolve_packet_lifecycle_status`, and `transition_component_build_status`):
    1. Evaluates `proof = evaluate_lattice_proof(graph)`.
    2. Resolves each component's **Lattice-Native Build Lifecycle Status** (`build_status` $\in$ `("DRAFT", "BLOCKED_BY_DEP", "READY", "IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE")`) via `resolve_packet_lifecycle_status(sc.get("status"), proof["is_proven"], all_deps_verified)`:
       - For Wave 0 (`schemas`): `all_deps_verified = True`.
       - For Wave 1 (`sid`): `all_deps_verified = all(scopes.get(d, {}).get("status") == "VERIFIED" for d in sc.get("depends_on", []) if d in owned_non_qa_set and bool(scopes.get(d, {}).get("rules") or scopes.get(d, {}).get("role") or scopes.get(d, {}).get("owner")))` (so if a component depends on another unverified component being built on the same board, it starts as `"BLOCKED_BY_DEP"` and automatically transitions to `"READY"` when that dependency reaches `"VERIFIED"`, whereas leaf components with no unverified board dependencies start as `"READY"` immediately once `proof["is_proven"]` is `True`).
       - For Wave 2 (`test_*` / `bobo` QA components): `all_deps_verified = all(scopes.get(d, {}).get("status") == "VERIFIED" for d in sc.get("depends_on", []) if d in owned_non_qa_set)` (or if `depends_on` is empty, `all(scopes.get(c, {}).get("status") == "VERIFIED" for c in owned_non_qa_set)`), so Wave 2 QA packets start as `"BLOCKED_BY_DEP"` and automatically unlock to `"READY"` when the Wave 1 components they test reach `"VERIFIED"`!
       - Includes `"build_status"`, `"build_tag": sc.get("build_tag")`, `"core_tag": sc.get("core_tag")`, and `"failure_diagnostic": sc.get("failure_diagnostic")` on every packet dict. When `sc.get("failure_diagnostic")` is non-empty, appends `f"\n! BUILD_FAILURE [{sid}]: {sc['failure_diagnostic']}"` to the packet's `"prompt_slice"`.
       - If `status_filter` is provided (e.g. `status_filter="READY"` or `"FAILED"` or `"STALE"`), filters `packets` to those matching `pkt["build_status"] == status_filter.upper()`.
    3. Constructs dependency-ordered parallel **Work Packets** across 3 waves:
       - **Wave 0 (`wave=0`, `kind="SCHEMA_CONTRACTS"`):** Shared `# SCHEMA` artifacts and root/parent `% LAW` invariants (`packet_id="pkt_wave0_schemas"`, `component="schemas"`, `owner`, `stage`, `build_status`, `schemas`, `laws`, `prompt_slice`).
       - **Wave 1 (`wave=1`, `kind="COMPONENT_SLICE"`):** One self-contained micro-slice packet per non-QA owned SBF component `sid` (`packet_id=f"pkt_wave1_{sid}"`, `component=sid`, `owner=sc.get("owner")`, `stage=sc.get("stage")`, `build_status`, `build_tag`, `core_tag`, `failure_diagnostic`, `depends_on=list(sc.get("depends_on", []))`, `laws`, `contracts`, `flows`, `rules`, `tasks`, and `"prompt_slice"`).
       - **Wave 2 (`wave=2`, `kind="QA_VERIFICATION"`):** One QA verification packet per `test_*` or `bobo`-owned component `sid` (`packet_id=f"pkt_wave2_{sid}"`, `component=sid`, `owner=sc.get("owner") or "bobo"`, `stage=sc.get("stage")`, `build_status`, `build_tag`, `core_tag`, `failure_diagnostic`, `"prompt_slice"`).
    4. Sets `ready_to_emit = bool(proof["is_proven"])`. Returns:
       ```python
       {
           "status": proof["status"],
           "is_proven": proof["is_proven"],
           "ready_to_emit": ready_to_emit,
           "open_gaps": proof["open_gaps"],
           "hard_violations": proof["hard_violations"],
           "lattice_proof": proof,
           "packets": filtered_packets if (ready_to_emit or force) else [],
           "waves": {
               "wave_0_schemas": wave0 if (ready_to_emit or force) else [],
               "wave_1_components": wave1 if (ready_to_emit or force) else [],
               "wave_2_qa": wave2 if (ready_to_emit or force) else [],
           },
       }
       ```
- Automatically persists any attached `SQLiteGraphStore` via `sync_to_sqlite(graph)` at the end of `ingest_into_context_graph` / `run_phase4` (and re-exports `slice_lattice`, `compile_work_packets`, `evaluate_lattice_proof`, `classify_lattice_proof_status`, `BUILD_STATUSES`, `step_component_lifecycle_on_edit`, `resolve_packet_lifecycle_status`, and `transition_component_build_status` in `whiteboard/__init__.py`).

---

## 3. Formal Bend 2 Laws (`docs/specs/whiteboard_22c/LAWS.bend` & `ModelCircuits/eng2/bend_wb_22c/`)

Every `eng2` build tag for `Spec 22C` must prove Laws 1–11 in [`LAWS.bend`](LAWS.bend) via `ModelCircuits/eng2/bend_wb_22c/PROOF.bend` (`scripts/bend_gate.py check ModelCircuits/eng2`).
