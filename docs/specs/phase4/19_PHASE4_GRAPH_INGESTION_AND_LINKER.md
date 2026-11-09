# Spec 19 — Phase 4: Graph Ingestion & Linker Layer (`core/circuit/phase4/`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-05 · **Status:** Approved & Locked (`Entity 2` — Isolated Workbench `ModelCircuits/eng4/` $\rightarrow$ Promoted `ModelCircuits/core/circuit/phase4/`)  
**Standing Rules:** [`00a_STANDING_RULES.md`](./00a_STANDING_RULES.md)

---

## 1. Scope, Isolation Boundary, & Contract Imports

**Phase 4 (`ModelCircuits/eng4/` $\rightarrow$ `ModelCircuits/core/circuit/phase4/`)** is **Entity 2** of the 3-Entity Pipeline: the stateful graph ingestion and linking layer that sits strictly **between** the stateless Intake Parser (**Entity 1**, `Phases 0–3`, which produces an `IntakePacket`) and the persistent **`ContextGraph`** (**Entity 3**, `core/circuit/context_graph/`).

- **Strict Isolation & Imports (`00a_STANDING_RULES.md`):**
  - Every module in `ModelCircuits/eng4/` (`__init__.py`, `linker.py`, `ingest.py`) is $\le 500$ lines, contains **zero regular expressions (`import re` is forbidden)**, and contains **zero open-ended English semantic header/domain word lists** (`_PRIMARY_ENTITY_HEADERS` and column-name keyword lists are strictly forbidden).
  - Shared foundation and contract imports use absolute package paths (`from core.circuit.nlp import restore_ascii_dots`, `from core.circuit.context_graph.frustration import _intervals_satisfiable, detect_frustration`, `from core.circuit.context_graph.mutations import ensure_scope_node, evict_from_ring, graduate_task_to_fact, markovian_overwrite_rule, prune_dysfluency_and_upsert_rule, two_axis_mitosis, upsert_ambient_note, wire_lex_specialis_override`, `from core.circuit.context_graph.schema import ContextGraph, DEFAULT_K_CAP, DEFAULT_MAX_RING, validate_context_graph`) rather than relative `..context_graph` imports, so `eng4` imports cleanly both as standalone top-level package `eng4` and when promoted to `core.circuit.phase4`.
- **Inputs:** `(graph: dict[str, Any] | None, intake_packet: dict[str, Any], *, k_cap: int = 5, max_ring: int = 10)`
- **Output:** `IngestionResult` (dict-compatible object with `.to_dict()` and attribute access) containing `"graph"`, `"status"` $\in \{\text{\texttt{"COMMITTED"}}, \text{\texttt{"CONFLICT"}}, \text{\texttt{"NEEDS\_CLARIFICATION"}}\}$, `"delta_log"`, `"conflicts"`, `"mitosis_splits"`, `"ring_size"`, `"max_rules_per_scope"`, `"fused_scopes"`, and `"turn_index"`, satisfying all structural and thermodynamic write-time invariants:
  1. Every scope node has $\le K = 5$ direct rules (`max_rules_per_scope <= k_cap`).
  2. Every rule has $\le M = 3$ `_meta` slots ($\le 25$ words/slot, $\le 75$ words total) and $\le 5$ words per `meta_keys` pointer.
  3. `len(graph["ambient_notes"]) <= max_ring` (`10`).
  4. $100\%$ of non-superseded rules are conserved across B-Tree mitosis (`count_after == count_before`, raising `ValueError` fail-closed on any violation).
  5. Every cross-scope `refs` pointer has a corresponding `in_edges` entry on the target scope, and every *lex specialis* `overrides` pointer has a corresponding structured `exceptions: [{"rule_id": ..., "note": ...}]` entry on the overridden rule.

---

## 2. Three Deterministic Ingestion Stages (`Stage 4A -> Stage 4B -> Stage 4C`)

### 2.1 Stage 4A — Entity Resolution & Table-Attribute Fusion (`linker.py`)
1. **Slug Normalization & Canonical/Alias Extraction (`_slugify_scope_id`, `_extract_canonical_and_aliases`):**
   - `_slugify_scope_id(raw: str) -> str`: Normalizes a surface entity/scope string into a canonical `snake_case`/path scope ID using character-by-character classification (`ch.isalnum() or ch in ("_", "/", ".")` kept, `ch in ("-", " ", ":")` mapped to `"_"`, consecutive `"__"` collapsed, outer `"_/"` stripped; zero `import re`).
   - `_extract_canonical_and_aliases(raw_name: str) -> tuple[str, list[str]]`: Extracts `(canonical_slug, surface_aliases)` from `raw_name`. If `raw_name` contains a backticked single-token identifier `` `slug_id` `` or a trailing parenthetical single-token identifier `(slug_id)`, uses that token as `canonical_cand` and collects the full surface string, the prefix before `(`, and any segments split on `"—"`, `"–"`, or `" - "` into `aliases`.
2. **Global Alias Index Lookup (`_register_scope_aliases`, `_resolve_or_register_scope`):**
   - Maintains `graph["alias_index"]` mapping lowercase surface strings and slugified identifiers to their canonical `scope_id` in `graph["scopes"]` (also matching mitosis base scope IDs `sid.split("::")[0]` and path suffixes `sid.endswith(f"/{canonical_slug}")`).
3. **Table-to-Attribute Fusion (`fuse_table_attributes(graph, doc_tables, turn_index=1) -> list[str]` — Zero Header Word Lists):**
   - Validates `graph` (`dict`) and `doc_tables` (`list` of dicts each with non-empty `headers: list` and `rows: list`), raising `ValueError` fail-closed if any header normalizes via `_slugify_scope_id` to an empty string `""`.
   - **Structural Row-Key Rule (Zero Keyword Lists):** Column `0` (`primary_col_idx = 0`, `primary_hdr = str(headers[0])`) is the primary entity column for each row.
   - For each row dict in `rows`:
     - Resolves `row.get(primary_hdr, "")` via `_resolve_or_register_scope(graph, raw_entity, create=True)` to `scope_id`, appending `scope_id` to `fused_scopes`.
     - Populates `scope["attributes"][col_key] = _parse_scalar_cell(str(row[raw_hdr]))` for each remaining column `col_idx >= 1` (`col_key = norm_headers[col_idx]`), recording `heading_path` (when present) and `"updated_turn": int(turn_index)`.
     - Whenever a non-primary cell raw string contains a backticked identifier (``"`" in str(row[raw_hdr])``) or a path separator (`"/" in str(cell_val)`), registers its extracted aliases on `scope_id` via `_register_scope_aliases`.
4. **MCU Scope & Reference Resolution (`resolve_mcu_scopes_and_refs(graph, parsed_mcu) -> tuple[str, list[str]]`):**
   - Resolves `parsed_mcu.get("primary_scope") or "global_defaults"` via `_resolve_or_register_scope(graph, raw_primary, create=True)` to `primary_scope`.
   - For each `ref_str` in `parsed_mcu.get("secondary_refs", [])`:
     - If `ref_str` is already registered in `graph["scopes"]` or `graph["alias_index"]`, resolves it via `_resolve_or_register_scope(graph, ref_str, create=False)`.
     - Otherwise, only allows `create=True` when `ref_str` is a standalone entity identifier: `ref_str not in (target_prop, bound_val_str)`, `len(ref_str) >= 3`, contains none of `(".", "*", "<", ">", ":", " ", "-")`, does not start with `"-"`, and is not a hex commit/ticket digest (`not (len(ref_str) >= 6 and any(c.isdigit() for c in ref_str) and all(c in "0123456789abcdef" for c in ref_str.lower()))`). This preserves cross-scope creation for named services/personas in tableless turns while preventing empty dummy scopes for filenames, extensions, globs, CLI flags, placeholders, build tags, hex hashes, or bound values.

### 2.2 Stage 4B — Delta Classification, Markovian Upsert & Bidirectional Edge Wiring (`classify_and_apply_delta`)
Given `(graph, parsed_mcu, turn_index=1)`:
1. **`NEEDS_CLARIFICATION` / `unclear_spans`:** Appends a `"type": "CLARIFICATION"` blocker (`id = f"clarify_t{t_turn}_m{mcu_id}"`) to `graph["open_blockers"]` and returns `{"operation": "BLOCKER_CLARIFICATION", ...}`.
2. **`QUESTION` / `HAS_OPEN_QUESTIONS`:** Appends a `"type": "QUESTION"` blocker (`id = f"question_t{t_turn}_m{mcu_id}"`) to `graph["open_blockers"]` and returns `{"operation": "BLOCKER_QUESTION", ...}`.
3. **`COMMIT`:** Upserts a commit note into `graph["ambient_notes"]` via `upsert_ambient_note` and returns `{"operation": "COMMIT_NOTE", ...}`.
4. **`ACTION` (Imperative Task & Task Graduation — Unconditional Task Routing):**
   - If `completed is True` and `task_id` is supplied (or after upserting an `ACTION` into `graph["active_tasks"]` when `completed is True`), executes `graduate_task_to_fact(graph, task_id, current_turn=t_turn)` and returns `{"operation": "GRADUATE_TASK_TO_FACT", ...}`.
   - Otherwise, whenever `node_type == "ACTION"`, unconditionally upserts `{id, text, scope, condition, depends_on, governed_by, completed, turn}` into `graph["active_tasks"]` and returns `{"operation": "UPSERT_TASK", ...}` (`ACTION` nodes are never routed into `scope["rules"]`).
5. **Standing Rule (`PROHIBITION`, `OBLIGATION`, `POLICY`, `BOUND`, `FACT`, `GOAL`, `PROPOSAL`, `REVOCATION`):**
   - Collects existing rules in `scope_id` (including mitosis child scopes `f"{base}::*"`) via `_collect_root_scope_rules(graph, scope_id)`.
   - **Same-Turn Attached Causal Rationale Deduplication:** If `node_type == "FACT"` and `target_prop is None`, and an existing rule `r_prev` in `existing_rules` from the same turn (`int(r_prev.get("turn", 0)) == t_turn`) already has a rationale in `scope["_meta"].get(r_prev["id"], {})` (`"rationale"` or `"transition_rationale"`) whose content words are a superset of (or match) the incoming causal clause's content words (`bool(inc_toks) and inc_toks.issubset(rat_toks)`), skips inserting a duplicate `FACT` rule into `scope["rules"]` and returns `{"operation": "ATTACH_RATIONALE", "rule_id": r_prev["id"], "scope": r_sid, "mcu_id": mcu_id}`.
   - **Prohibition Guard (`_is_prohibited_by_active_rule` — Formalized in [`phase4/LAWS.bend`](phase4/LAWS.bend) Laws 3–5):** Checks whether an active un-overridden rule `r` and the incoming rule have opposing prohibition polarity (`(r_type == "PROHIBITION" or r_op == "!=") != (incoming_type == "PROHIBITION" or bound_op == "!=")`) on the same `target_prop` and overlapping `conditions`, and either share the same scalar/unconstrained value (`r_val is None or bound_val is None or (float(r_val) == float(bound_val) if (r_is_num and inc_is_num) else str(r_val).strip().lower() == str(bound_val).strip().lower())`, where `r_is_num = isinstance(r_val, (int, float)) and not isinstance(r_val, bool)`) or have disjoint numeric intervals (`r_is_num and inc_is_num and not _intervals_satisfiable(r_op, float(r_val), bound_op, float(bound_val))`). Notice that `r_is_prohib != inc_is_prohib` is strictly symmetric between the existing rule and the incoming rule (**Law 4** `prohibition_guard_symmetric` and **Law 5** `prohibition_guard_disjoint_intervals`); if prohibited (and `not is_exception`), skips Markovian overwrite (**Law 3** `prohibition_guard_blocks_overwrite`) so the rule is inserted and caught by Stage 4C's Spin-Glass Frustration detector.
   - **Case 1 — `MARKOVIAN_OVERWRITE`:** If `target_prop is not None`, `not prohibited`, and an existing rule `r_old` in `existing_rules` has the exact same `target_prop`, exact same condition set (`set(r_old["conditions"]) == cond_set`), and matching prohibition polarity (`(r_old["type"] == "PROHIBITION" or r_old["bound_op"] == "!=") == incoming_is_prohib`), and is not a same-turn disjoint-interval conflict without `superseded_literals`, mutates `r_old` in place via `markovian_overwrite_rule(graph, r_sid, str(r_old["id"]), rule_payload)` and returns `{"operation": "MARKOVIAN_OVERWRITE", ...}`.
   - **Case 2 — `LEX_SPECIALIS_EXCEPTION`:** Upserts the rule via `prune_dysfluency_and_upsert_rule(graph, scope_id, rule_payload)`. If `conditions` is non-empty or `is_exception is True` (or explicit `overrides` IDs are provided) and broader rules exist in `existing_rules` (`r_conds < cond_set` on the same `target_prop`, or unconditional `not r_conds` when `is_exception is True`), wires bidirectional `overrides` and structured `exceptions` via `wire_lex_specialis_override(graph, new_rule, gen_r, note=note_str)` and returns `{"operation": "LEX_SPECIALIS_EXCEPTION", ...}`.
   - **Case 3 — `ORTHOGONAL_INSERT`:** Otherwise returns `{"operation": "ORTHOGONAL_INSERT", ...}`.

### 2.3 Stage 4C — Recursive Mitosis, ACT-R Ring Eviction, & Frustration Check (`ingest.py`)
`ingest_intake_packet(graph, intake_packet, *, k_cap=DEFAULT_K_CAP, max_ring=DEFAULT_MAX_RING) -> IngestionResult`:
1. Initializes `graph = create_empty_context_graph()` if `graph is None`; validates `graph` and `intake_packet` (`dict`), raising `Phase4InvariantError` on invalid input.
2. Runs Stage 4A (`fuse_table_attributes`) on `intake_packet.get("doc_tables", [])`, and pre-registers every non-empty `pmcu.get("primary_scope")` across `intake_packet.get("parsed_mcus", intake_packet.get("items", []))` via `_resolve_or_register_scope(graph, raw_primary, create=True)` before running Stage 4B so cross-scope `secondary_refs` between MCUs in the same turn resolve cleanly with `create=False`.
3. Runs Stage 4B (`classify_and_apply_delta`) on each `pmcu` in `intake_packet.get("parsed_mcus", intake_packet.get("items", []))` (also upserting unattached `"global_defaults"` `FACT`/`GOAL`/`PROPOSAL` items with `target_prop is None` into `ambient_notes`), and records any top-level `intake_packet.get("clarification_questions", [])` in `graph["open_blockers"]`.
4. Executes recursive lossless B-Tree mitosis `two_axis_mitosis(graph, k_cap=k_cap)` and ACT-R ring eviction `evict_from_ring(list(graph.get("ambient_notes", [])), turn_index, max_ring=max_ring)`.
5. Runs `conflicts = detect_frustration(graph)`, synchronizes `"CONFLICT"` entries at the front of `graph["open_blockers"]`, determines `status` (`"NEEDS_CLARIFICATION"` if clarification blockers exist, else `"CONFLICT"` if `conflicts` is non-empty, else `"COMMITTED"`), verifies `check_phase4_invariants(res, k_cap=k_cap, max_ring=max_ring)` (raising `Phase4InvariantError("phase4_invariant_violation", inv_errors)` on any error), and returns `IngestionResult`.

### 2.4 Unified 3-Entity Runtime Turn & Readout Orchestration (`run_turn` & `query_context_graph` in `ingest.py`)
To wire the 3-Entity Pipeline (**Entity 1:** `Phases 0–3` $\rightarrow$ **Entity 2:** `Phase 4` $\rightarrow$ **Entity 3:** `ContextGraph`) into a single runtime turn and query surface using only allowed contract imports (`from core.circuit.regiment import run_phase0, run_phase1, run_phase2, run_phase3`, `from core.circuit.phase3.intake_packet import compile_intake_packet`, and `from core.circuit.context_graph.read_engine import expand_node, propagate_graph, scan_graph`):

1. **`query_context_graph(graph, query="", *, mode="propagate", node_id=None, temperature=None, token_budget=400, max_lines=35) -> dict[str, Any]`:**
   - Validates `isinstance(graph, dict)` (raising `Phase4InvariantError("phase4_invalid_graph", [f"graph must be a dict, got {type(graph).__name__}"])` if not) and `mode in ("propagate", "scan", "expand")` (raising `ValueError(f"invalid query mode={mode!r}, expected 'propagate', 'scan', or 'expand'")` if not).
   - When `mode == "expand"`: resolves `target_id = node_id if (isinstance(node_id, str) and node_id.strip()) else str(query).strip()` and returns `expand_node(graph, target_id, query=str(query or ""), token_budget=token_budget, max_lines=max_lines)`.
   - When `mode == "scan"`: returns `scan_graph(graph, str(query or ""), token_budget=token_budget, max_lines=max_lines, temperature=temperature)`.
   - When `mode == "propagate"`: if `not isinstance(query, str) or not query.strip()`, returns `scan_graph(graph, "", token_budget=token_budget, max_lines=max_lines, temperature=temperature)`; otherwise returns `propagate_graph(graph, query, temperature=temperature, token_budget=token_budget, max_lines=max_lines)`.
2. **`run_turn(graph, raw_text="", gen=None, jev=None, limits=None, *, turn_index=None, query=None, k_cap=DEFAULT_K_CAP, max_ring=DEFAULT_MAX_RING, token_budget=400, max_lines=35, phase3_result=None, intake_packet=None) -> IngestionResult`:**
   - Initializes `graph = create_empty_context_graph()` if `graph is None`; validates `isinstance(graph, dict)` (raising `Phase4InvariantError("phase4_invalid_graph", ...)` if not).
   - Computes `eff_turn = int(turn_index) if turn_index is not None else max(1, int(graph.get("turn_index", 0)) + 1)`.
   - If `intake_packet is not None`:
     - Copies `packet = dict(intake_packet)` (raising `Phase4InvariantError("phase4_invalid_intake_packet", ...)` if not a `dict`), sets `packet["turn_index"] = eff_turn`, and sets `p3 = phase3_result`.
   - Else:
     - If `phase3_result is not None`: uses `p3 = phase3_result`.
     - Else: validates `isinstance(raw_text, str)` (raising `Phase4InvariantError("phase4_invalid_raw_text", ...)` if not) and runs Entity 1 via `core.circuit.regiment`:
       1. `p0 = run_phase0(raw_text, gen, limits)`
       2. `p1 = run_phase1(p0, jev, limits)`
       3. `p2 = run_phase2(p1, jev, limits)`
       4. `p3 = run_phase3(p2, gen, limits, jev=jev)`
     - Compiles `packet = compile_intake_packet(p3, turn_index=eff_turn)`.
   - Executes Entity 2 via `res = ingest_intake_packet(graph, packet, k_cap=k_cap, max_ring=max_ring)`.
   - Executes Entity 3 readout using `eff_query = query if query is not None else (raw_text if isinstance(raw_text, str) else "")`:
     - `scan_view = query_context_graph(res["graph"], eff_query, mode="scan", token_budget=token_budget, max_lines=max_lines)`
     - `readout = query_context_graph(res["graph"], eff_query, mode="propagate", token_budget=token_budget, max_lines=max_lines)`
   - Attaches `"intake_packet": packet`, `"phase3_result": p3`, `"scan_view": scan_view`, and `"readout": readout` onto `res` and returns `res`.

### 2.5 Formal Bend 2 Laws (`docs/specs/phase4/LAWS.bend` & `ModelCircuits/eng2/bend_phase4/`)
Every Phase 4 build tag must prove the 6 universal laws in [`phase4/LAWS.bend`](phase4/LAWS.bend) via `PROOF.bend`:
- **Law 1 (`clarification_dominates_all_deltas`):** Any MCU with `GNeedsClarification{}` or `has_unclear == True{}` transitions unconditionally to `OpBlockerClarification{}`.
- **Law 2 (`grounded_action_never_enters_rule_table`):** A grounded `NKAction{}` with no unclear spans routes exclusively to task operations (`OpUpsertTask{}` or `OpGraduateTaskToFact{}`) and never mutates standing rule tables (`is_rule_table_op == False{}`).
- **Law 3 (`prohibition_guard_blocks_overwrite`):** Whenever `is_prohibited == True{}`, `classify_delta` never returns `OpMarkovianOverwrite{}`.
- **Law 4 (`prohibition_guard_symmetric`):** `prohibition_clashes(a_is_prohib, b_is_prohib, same_val, intervals_disjoint) == prohibition_clashes(b_is_prohib, a_is_prohib, same_val, intervals_disjoint)`.
- **Law 5 (`prohibition_guard_disjoint_intervals`):** Whenever one rule is a prohibition and the other is positive, `intervals_disjoint == True{}` always triggers `prohibition_clashes == True{}` regardless of rule arrival order.
- **Law 6 (`ingestion_status_commitment_iff_clean`):** `resolve_ingestion_status(has_clarification, has_conflicts)` returns `StatusCommitted{}` iff `has_clarification == False{}` and `has_conflicts == False{}`.

---

## 3. Public Interface (`ModelCircuits/eng4/` & `ModelCircuits/core/circuit/phase4/`)

- `VALID_INGESTION_STATUSES = frozenset({"COMMITTED", "CONFLICT", "NEEDS_CLARIFICATION"})`
- `Phase4InvariantError(code: str, errors: list[str])`
- `IngestionResult(dict[str, Any])` (with `.to_dict()` and attribute access)
- `create_empty_context_graph() -> ContextGraph`
- `fuse_table_attributes(graph, doc_tables, turn_index=1) -> list[str]`
- `resolve_mcu_scopes_and_refs(graph, parsed_mcu) -> tuple[str, list[str]]`
- `classify_and_apply_delta(graph, parsed_mcu, turn_index=1) -> dict[str, Any]`
- `ingest_intake_packet(graph, intake_packet, *, k_cap=5, max_ring=10) -> IngestionResult`
- `query_context_graph(graph, query="", *, mode="propagate", node_id=None, temperature=None, token_budget=400, max_lines=35) -> dict[str, Any]`
- `run_turn(graph, raw_text="", gen=None, jev=None, limits=None, *, turn_index=None, query=None, k_cap=5, max_ring=10, token_budget=400, max_lines=35, phase3_result=None, intake_packet=None) -> IngestionResult`
- `check_phase4_invariants(result, *, k_cap=5, max_ring=10) -> list[str]`
