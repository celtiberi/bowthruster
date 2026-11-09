# Spec 21 — Shared `graph_core` & SQLite Storage Engine (`core/circuit/graph_core/`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-06 · **Assigned Engineer:** `eng3` (`ModelCircuits/eng3/graph_core/` $\rightarrow$ Promoted `ModelCircuits/core/circuit/graph_core/`)  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md) · **Formal Laws:** [`LAWS.bend`](LAWS.bend)

---

## 1. Purpose, Isolation Boundary, & Module Surface

**`graph_core`** is the shared **nD Lattice Graph Foundation Library & SQLite Storage Engine** powering all Conference Room graph products (**`the_lattice`** / `whiteboard`, **`the_table`**, and **`ai_memory`**). Every module in `graph_core` must be $\le 500$ lines with zero `import re` and zero upward imports from `whiteboard`, `the_table`, `the_executive`, or `ai_memory`.

### 1.1 SQLite Database Storage Backend (`sqlite_store.py`)
Replaces raw flat-JSON persistence with an indexed, ACID-transactional **SQLite database** (`sqlite3`, supporting both file-backed `.sqlite` paths and `:memory:` connections):
- **Relational Schema (`init_graph_db(conn)`):**
  1. `graph_meta(key TEXT PRIMARY KEY, val_json TEXT NOT NULL)`: stores graph-level scalars (`version`, `turn`, `k_cap`, `max_ring`).
  2. `scopes(id TEXT PRIMARY KEY, parent TEXT, aliases_json TEXT NOT NULL, children_json TEXT NOT NULL, in_edges_json TEXT NOT NULL, attributes_json TEXT NOT NULL)`: indexed on `parent`. Also persists component SBF metadata (`role`, `inputs`, `outputs`, `depends_on`, `owner`, `stage`, `kind`, `status`) inside `scopes` (or `attributes_json` / SBF envelope) with 100% lossless round-trip fidelity in `save_graph` / `load_graph`.
  3. `nodes(id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, node_type TEXT NOT NULL, facet TEXT NOT NULL, text TEXT NOT NULL, target_prop TEXT, bound_op TEXT, bound_val_json TEXT, conditions_json TEXT NOT NULL, refs_json TEXT NOT NULL, overrides_json TEXT NOT NULL, exceptions_json TEXT NOT NULL, meta_keys_json TEXT NOT NULL, meta_tokens_json TEXT NOT NULL, created_turn INTEGER NOT NULL, updated_turn INTEGER NOT NULL)`: indexed on `(scope_id)`, `(scope_id, target_prop)`, and `(node_type)`.
  4. `node_meta(node_id TEXT NOT NULL, scope_id TEXT NOT NULL, meta_key TEXT NOT NULL, meta_val TEXT NOT NULL, PRIMARY KEY (node_id, meta_key))`: stores `LOD 2` metadata slots (`rationale`, `transition_rationale`, `revision_note`, `heading_path`).
  5. `external_links(link_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, uri TEXT NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL, metadata_json TEXT NOT NULL)`: indexed on `node_id` for `ai_memory` `LOD 2` external source pointers.
  6. `active_tasks(id TEXT PRIMARY KEY, scope TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, depends_on_json TEXT NOT NULL, governed_by_json TEXT NOT NULL, created_turn INTEGER NOT NULL)` (preserving optional nD task metadata `owner`, `stage`, `target_prop`, `bound_op`, `bound_val`, `condition`, `completed` via `graph_meta` or task payload).
  7. `ambient_notes(id TEXT PRIMARY KEY, text TEXT NOT NULL, access_turns_json TEXT NOT NULL)`.
  8. `open_blockers(id TEXT PRIMARY KEY, type TEXT NOT NULL, scope TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL)`.
  9. `flows(id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL, payload TEXT, action TEXT, condition TEXT, step INTEGER, text TEXT NOT NULL, dimensions_json TEXT NOT NULL, turn INTEGER NOT NULL)`: stores directed nD signal/causal flow edges (`source -> target`).
  10. `lattice_artifacts(id TEXT PRIMARY KEY, kind TEXT NOT NULL, scope_id TEXT NOT NULL, name TEXT NOT NULL, spec TEXT NOT NULL, inputs_json TEXT NOT NULL, outputs_json TEXT NOT NULL, owner TEXT, stage TEXT, dimensions_json TEXT NOT NULL, text TEXT NOT NULL, turn INTEGER NOT NULL)`: stores first-class `SCHEMA`, `INTERFACE`, and `LAW` nodes indexed across `(scope_id, kind, owner, stage)`.
- **Public API of `SQLiteGraphStore` (`sqlite_store.py`):**
  - `SQLiteGraphStore(db_path: str | Path = ":memory:")`: opens/initializes the SQLite schema with `WAL` mode (on file paths) and foreign-key enforcement.
  - `save_graph(graph: dict[str, Any]) -> None`: atomically syncs a graph state into the SQLite tables inside a single transaction (`with self.conn:`).
  - `load_graph() -> dict[str, Any]`: reconstructs the canonical graph dictionary from the SQLite tables (preserving exact round-trip fidelity for `scopes` including SBF component fields, `rules`, `_meta`, `external_links`, `active_tasks`, `ambient_notes`, `open_blockers`, `flows`, and `lattice_artifacts`).
  - `upsert_external_link(node_id: str, uri: str, kind: str = "reference", summary: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]` and `get_external_links(node_id: str) -> list[dict[str, Any]]`: indexed `LOD 2` external source pointer operations.
  - `query_nodes_by_scope(scope_id: str) -> list[dict[str, Any]]` and `query_nodes_by_target_prop(scope_id: str, target_prop: str) -> list[dict[str, Any]]`: indexed SQLite lookups.

---

### 1.2 Shared Tokenization, Cost, Decay, & nD Lattice View Formatting (`formatting.py`)
- `tokenize_content_words(text: str) -> list[str]`
- `clamp_rule_meta(raw_meta: dict[str, Any] | None) -> tuple[dict[str, str], list[str], list[str]]`
- `token_cost(obj: Any) -> int`
- `actr_base_level_activation(access_turns: list[int], current_turn: int) -> float`
- `evict_from_ring(ring: list[dict[str, Any]], current_turn: int, max_ring: int = 10) -> list[dict[str, Any]]`
- `matches_nd_slice(is_unconstrained: bool, has_dim_overlap: bool) -> bool`: governed by **Bend 2 Law 10 (`nd_coordinate_slice_exact`)**—returns `True` when `is_unconstrained` is `True`, and `bool(has_dim_overlap)` otherwise.
- `_pack_active_rules_and_lod2(...)` and `format_compact_view(...)`: two-pass packer governed by **Bend 2 Law 8 (`lod1_never_starved_by_lod2`)**, packing all fitting `LOD 1` primary lines before attaching `LOD 2` `_meta` or `external_links` lines within the remaining line and token budget.
  - In addition to conflicts (`! CONFLICT`), blockers (`? BLOCKER`), scope headers (`@ SCOPE`), tasks (`+ TASK`), rules (`* [scope] id (TYPE): text`), and ambient notes (`~ NOTE`), `format_compact_view` renders active nD Lattice entries present on `view` (`component_specs`, `active_flows`, `active_artifacts`) and enriches task lines:
    - Component SBF lines (`view.get("component_specs", [])`): `@ COMPONENT [scope_id] (parent=..., children=[...], depends_on=[...], owner=..., stage=...): <role> | in=[...] -> out=[...]` (omitting empty optional tags).
    - Signal flow lines (`view.get("active_flows", [])`): `> FLOW [step] source --[payload]--> target | if: <condition>: <text>` (omitting `[step]` or `| if:` when `None`).
    - Lattice artifact lines (`view.get("active_artifacts", [])`):
      - `# SCHEMA [name] (scope=..., owner=..., stage=...): <spec_or_text>`
      - `$ INTERFACE [name] (scope=..., owner=..., stage=..., in=[...] -> out=[...]): <spec_or_text>`
      - `% LAW [name] (scope=..., owner=..., stage=...): <spec_or_text>`
    - Task lines (`view.get("active_tasks", [])`): `+ TASK <id> [<scope>] (status=<status>, owner=<owner>, stage=<stage>, deps=[<deps>]): <text>` when `status`/`owner`/`stage`/`depends_on` are present on the task dict (preserving `+ TASK <id> [<scope>]: <text>` when status/owner/stage are absent, or appending `[status=...]` cleanly).

---

### 1.3 Seed Activation, Topological Temperature, & Multi-Hop Propagation (`activation.py` / `propagation.py`)
- `compute_seed_activations(graph: dict[str, Any], query: str) -> tuple[dict[str, float], set[str]]`: order-independent seed scorer (`_score_single_rule`) combining `spaCy` `en_core_web_lg` lexical similarity and `bge-small-en-v1.5` ONNX dense embeddings, excluding `heading_path` and scope ID/alias tokens `s_tokens` from `meta_tokens` overlap. Also boosts scope seed activation when `query` tokens match a scope's SBF `role`, `inputs`, `outputs`, `owner`, `stage`, connected `flows`, or `lattice_artifacts`.
- `infer_query_temperature(graph: dict[str, Any], query: str) -> float` and `compute_tau(h_max: float, temperature: float) -> float`: governed by **Bend 2 Law 9 (`temperature_barrier_monotonic`)**.
- `_build_five_family_edges(graph: dict[str, Any]) -> dict[str, list[tuple[str, str, float]]]`: includes directed signal `flows` (`source <-> target`, weight `0.85`, governed by **Bend 2 Law 11 `flow_edge_preserves_polarity`**) and component `depends_on` edges alongside parent/child, `refs`/`in_edges`, `overrides`/`exceptions`, task `depends_on`, and `governed_by`.
- `scan_nodes(...)`, `propagate_nodes(...)`, and `expand_graph_node(...)`: reusable thermodynamic 1-hop scan, multi-hop relaxation, and `LOD 0 -> 1 -> 2` drill-down primitives parameterized by an optional `conflict_detector`. Populates `view["component_specs"]`, `view["active_flows"]`, and `view["active_artifacts"]` for activated scopes (or matching query tokens) so `format_compact_view` renders the full nD Lattice projection.

---

## 2. Formal Bend 2 Laws (`docs/specs/graph_core/LAWS.bend` & `ModelCircuits/eng3/bend_graph_core/`)

Every `eng3` build tag for `graph_core` must prove all 11 universal laws in [`LAWS.bend`](LAWS.bend) via `ModelCircuits/eng3/bend_graph_core/PROOF.bend` (`scripts/bend_gate.py check ModelCircuits/eng3`):
- **Laws 1–7 (Signed Edge-Path Polarity & Bounded Decay):** `inv_involutive`, `comp_pos_right`, `comp_assoc`, `path_append`, `self_never_frustrated`, `neg_always_frustrated`, `decay_zero_floor`.
- **Law 8 (`lod1_never_starved_by_lod2`):** `admit_lod1_count(cands, lod2_a, cap) == admit_lod1_count(cands, lod2_b, cap)` for all `(cands: Nat, lod2_a: Nat, lod2_b: Nat, cap: Nat)`.
- **Law 9 (`temperature_barrier_monotonic`):** `tau_min_scaled(Warm{}) <= tau_min_scaled(classify_temp_regime(sb, has_cross, has_task)) <= tau_min_scaled(Cold{})` across all `(sb: SeedBucket, has_cross: Bool, has_task: Bool)`.
- **Law 10 (`nd_coordinate_slice_exact`):** `matches_nd_slice(True{}, has_dim_overlap) == True{}` and `matches_nd_slice(False{}, has_dim_overlap) == has_dim_overlap`.
- **Law 11 (`flow_edge_preserves_polarity`):** `comp_pol(p, flow_edge_pol()) == p` for all `p: Pol`.
