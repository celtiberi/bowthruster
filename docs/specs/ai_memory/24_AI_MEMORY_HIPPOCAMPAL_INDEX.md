# Spec 24 — `ai_memory` Hippocampal Concept Index & External Source Links (`core/circuit/ai_memory/`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-06 · **Assigned Engineer:** `eng0` (`ModelCircuits/eng0/ai_memory/` $\rightarrow$ Promoted `ModelCircuits/core/circuit/ai_memory/`)  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md) · **Formal Laws:** [`LAWS.bend`](LAWS.bend) · **Foundation Dependency:** [`../graph_core/21_GRAPH_CORE_AND_SQLITE_ENGINE.md`](../graph_core/21_GRAPH_CORE_AND_SQLITE_ENGINE.md)

---

## 1. Purpose, Isolation Boundary, & Module Surface

**`ai_memory`** (`core/circuit/ai_memory/`, with each module $\le 500$ lines and zero `import re`) is **Product 3** of the Conference Room Architecture: the Hippocampal Concept & Episodic Index built on `graph_core` (`SQLiteGraphStore`).

Instead of dumping entire books or documents into graph nodes, `ai_memory` stores a 3-Level-of-Detail (`LOD 0/1/2`) associative index governed by **Bend 2 Laws 1–2 (`LAWS.bend`)**:
- **`LOD 0` (Concept / Entity Header):** Node ID, aliases, semantic category, and `bge-small-en-v1.5` embedding.
- **`LOD 1` (Distilled Synopsis & Relational Edges):** 1–2 sentence core synopsis (`text`), key facts, and typed relational edges (`refs`, `attributes`), returned immediately during fast associative recall (`recall_into_working_memory`).
- **`LOD 2` (External Source Links):** Structured `external_links` (`link_id`, `node_id`, `uri`, `kind`, `summary`, `metadata`) stored in `SQLiteGraphStore`'s `external_links` table, dereferenced only when `LOD 2` deep detail is requested (**Law 2 `external_deref_only_at_lod2`**).

`ai_memory` depends **only** on `graph_core` (`core.circuit.graph_core`). It has **zero dependency** on `whiteboard`, `the_table`, or `the_executive`.

---

## 2. Public API & Module Surface (`core/circuit/ai_memory/`)

### 2.1 `lod_state.py` (Bend 2-Verified LOD Progression & External Dereference Gate)
- `LOD_0 = "LOD0"`, `LOD_1 = "LOD1"`, `LOD_2 = "LOD2"`.
- `step_lod_level(level: str) -> str`: maps `"LOD0"` $\rightarrow$ `"LOD1"`, `"LOD1"` $\rightarrow$ `"LOD2"`, `"LOD2"` $\rightarrow$ `"LOD2"` (**Law 1 `lod_step_idempotent_at_lod2`**).
- `should_dereference_external(level: str, has_links: bool, deep_detail_needed: bool) -> bool`: returns `True` iff `level == "LOD2" and has_links and deep_detail_needed` (**Law 2 `external_deref_only_at_lod2`**).

### 2.2 `hippocampal_index.py` (Concept Frame Indexing, Associative Recall, & External Link Binding)
- `AIMemoryStore`: wraps a `SQLiteGraphStore` instance (from `core.circuit.graph_core`) to manage concept frames, episodic traces, and external source links:
  - `upsert_concept_frame(frame_id: str, synopsis: str, *, category: str = "CONCEPT", aliases: list[str] | None = None, attributes: dict[str, Any] | None = None, related_frames: list[str] | None = None, external_links: list[dict[str, Any]] | None = None, current_turn: int = 1) -> dict[str, Any]`:
    - Ensures a scope/frame node `frame_id` exists in the underlying graph with `aliases` and `attributes`.
    - Upserts a primary `FACT` synopsis node (`id = f"{frame_id}.synopsis"`, `type = "FACT"`, `facet = "context_facts"`, `text = synopsis`, `refs = related_frames or []`) and wires bidirectional `refs` across related concept frames.
    - Persists any `external_links` into `SQLiteGraphStore.upsert_external_link(...)` and syncs the graph to SQLite via `store.save_graph(graph)`.
  - `record_episode(episode_id: str, summary: str, *, related_frames: list[str] | None = None, turn: int = 1) -> dict[str, Any]`:
    - Records an episodic memory node under `episodes` (or linked `related_frames`) and updates ACT-R `access_turns`.
  - `recall_into_working_memory(query: str, *, deep_detail_needed: bool = False, token_budget: int = 300, max_lines: int = 25, external_fetcher: Callable[[str], str] | None = None) -> dict[str, Any]`:
    - Runs `propagate_nodes` from `core.circuit.graph_core` over the `ai_memory` graph for `query` (Kintsch's **Construction-Integration** associative recall).
    - Returns a structured recall packet:
      - `"activated_frames"`: `list[str]` of activated concept frame IDs (`active_scopes`),
      - `"synopses"`: `list[dict[str, Any]]` of activated `LOD 1` concept/fact nodes (`active_rules`),
      - `"lod_level"`: `"LOD2"` if `deep_detail_needed` else `"LOD1"`,
      - `"external_links"`: `dict[str, list[dict[str, Any]]]` mapping activated node/frame IDs to their `SQLiteGraphStore.get_external_links(...)` entries,
      - `"dereferenced_sources"`: `dict[str, str]` populated **only** when `should_dereference_external(lod_level, bool(links), deep_detail_needed)` is `True` (calling `external_fetcher(link["uri"])` when provided, or returning `link["summary"]`),
      - `"text"`: compact formatted view from `graph_core`.

---

## 3. Formal Bend 2 Laws (`docs/specs/ai_memory/LAWS.bend` & `ModelCircuits/eng0/bend_ai_memory/`)

Every `eng0` build tag for `ai_memory` must prove Laws 1–2 in [`LAWS.bend`](LAWS.bend) via `ModelCircuits/eng0/bend_ai_memory/PROOF.bend` (`scripts/bend_gate.py check ModelCircuits/eng0`):
- **Law 1 (`lod_step_idempotent_at_lod2`):** `step_lod(step_lod(step_lod(lvl))) == LOD2{}` for all `lvl: LODLevel`.
- **Law 2 (`external_deref_only_at_lod2`):** `should_dereference_external(lvl, has_links, deep_detail_needed) == (lvl == LOD2{} && has_links && deep_detail_needed)`.
