# Spec 20 — Bounded Context Graph & Thermodynamic Read Engine (`core/circuit/context_graph/`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-05 · **Status:** Approved & Locked (`Entity 3` — Isolated Workbench `ModelCircuits/eng5/` $\rightarrow$ Promoted `ModelCircuits/core/circuit/context_graph/`)  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md)

---

## 1. Scope, Isolation Boundary, & Module Surface

The **`context_graph`** package (`ModelCircuits/eng5/` $\rightarrow$ `ModelCircuits/core/circuit/context_graph/`, with each file $\le 500$ lines and zero `import re` or semantic keyword lists per `00a_STANDING_RULES.md`) is **Entity 3** of the 3-Entity Pipeline. It implements the persistent JSON object graph schema, invariant verifiers, write-time topology/memory operators, and the deterministic **Push-Plus-Pull** thermodynamic read/propagation engine.

### 1.1 Schema & Invariant Verification Surface (`schema.py`)
- **Constants & Types:**
  - `DEFAULT_K_CAP = 5`, `DEFAULT_MAX_RING = 10`, `MAX_META_SLOTS = 3`, `MAX_META_SLOT_WORDS = 25`, `MAX_META_TOTAL_WORDS = 75`, `MAX_META_KEY_WORDS = 5`.
  - `_TYPE_TO_FACET`: maps `"PROHIBITION"` / `"REVOCATION"` $\rightarrow$ `"prohibitions"`, `"OBLIGATION"` / `"POLICY"` $\rightarrow$ `"obligations"`, `"BOUND"` $\rightarrow$ `"Numeric_Bounds"`, `"ACTION"` / `"COMMIT"` $\rightarrow$ `"actions"`, `"FACT"` / `"GOAL"` / `"PROPOSAL"` $\rightarrow$ `"context_facts"`, `"QUESTION"` $\rightarrow$ `"open_questions"`.
  - `ContextGraphInvariantError(code: str, errors: list[str])`
  - `RuleEntry(dict[str, Any])`, `ScopeNode(dict[str, Any])`, `ContextGraph(dict[str, Any])` (dict subclasses with `.to_dict()` and attribute access).
- **Tokenization, Cost, Formatting, & Metadata Clamping:**
  - `tokenize_content_words(text: str) -> list[str]`: extracts lowercase content words/lemmas and alphanumeric identifier tokens via `spaCy` (`_get_spacy_nlp()` from `core.circuit.nlp`).
  - `clamp_rule_meta(raw_meta: dict[str, Any] | None) -> tuple[dict[str, str], list[str], list[str]]`: clamps a rule's `_meta` dictionary to $\le 3$ slots, $\le 25$ words per slot, $\le 75$ words total, and $\le 5$ words per `meta_keys` pointer, returning `(clamped_meta, meta_keys, meta_tokens)`.
  - `token_cost(obj: Any) -> int`: deterministic token cost estimator over JSON/compact views (`max(1, int(math.ceil(len(s) / 4.0)))`).
  - `format_compact_view(view: dict[str, Any], *, max_lines: int = 35, token_budget: int = 400, scope_headers: list[str] | None = None) -> str`: renders a compact multi-line view ($\le 35$ lines, $\le$ `token_budget` tokens) of active scopes, rules, tasks, and blockers, verifying `token_cost("\n".join([*lines, ln])) <= token_budget` on the full joined string before appending each line so integer-division remainders never cause the multi-line view to exceed `token_budget`. Renders conflicts, open blockers, any explicit `scope_headers` passed by `scan_graph`/`propagate_graph` (or `@ SCOPE [<node_id>]` header displaying non-`updated_turn` table-fused `attributes` and `children` when `view.get("node_type") == "SCOPE"`), and `active_tasks` (`+ TASK ...`) **before** `active_rules` (`* [<rid>] ...`) and `ambient_notes` (`~ NOTE ...`), and reserves line/token capacity for all LOD 1 `* [<rid>] ...` `active_rules` lines that fit within `max_lines` and `token_budget` before admitting optional inline `  -> meta.<k>: <v>` LOD 2 lines under each rule, ensuring active tasks, table-fused scope attributes, and later active rules are never starved by earlier rules' LOD 2 metadata lines.
- **Invariant Verifier (`validate_context_graph(graph, *, k_cap=5, max_ring=10) -> list[str]` and alias `verify_graph_invariants`):**
  1. **Scope Rule Cap:** Every scope node in `graph["scopes"]` has $\le K = 5$ direct rules (`len(scope["rules"]) <= k_cap`).
  2. **Metadata Cap:** Every rule has $\le M = 3$ `_meta` slots, $\le 25$ words per slot, $\le 75$ words total, and $\le 5$ words per `meta_keys` pointer.
  3. **Ambient Cap:** `len(graph["ambient_notes"]) <= max_ring` (`10`), with each entry containing non-empty `access_turns: list[int]`.
  4. **Bidirectional Edge Symmetry:** Every cross-scope `refs` pointer on a rule has a corresponding `in_edges` entry on the target scope (and vice versa), and every *lex specialis* `overrides` pointer has a corresponding structured `exceptions: [{"rule_id": str, "note": str}]` entry on the overridden rule (raising `ValueError` fail-closed on any bare string in `exceptions`).

### 1.2 Write-Time Topology & Memory Mutation Surface (`mutations.py`)
- `actr_base_level_activation(access_turns: list[int], current_turn: int) -> float`:
  $$B_i(t) = \ln \sum_{t_k \in \text{access\_turns}} \big(\max(1, t - t_k)\big)^{-0.5}$$
- `evict_from_ring(ring: list[dict[str, Any]], current_turn: int, max_ring: int = 10) -> list[dict[str, Any]]`: evicts lowest-activation entries tie-broken deterministically by `(B_i(t), str(id))` until `len(ring) <= max_ring`.
- `upsert_ambient_note(graph, note, current_turn=None, max_ring=10) -> dict[str, Any]`: upserts a situational note into `graph["ambient_notes"]`, updating `access_turns` and bounding via `evict_from_ring`.
- `ensure_scope_node(graph, scope_id, *, parent=None) -> dict[str, Any]`: ensures `scopes[scope_id]` exists, sets `scope["parent"] = parent` when `parent is not None`, and always appends `scope_id` to `scopes[parent]["children"]` (if `parent in scopes` and `scope_id` is not already in `scopes[parent]["children"]`, including when `scope` is newly created with `"parent": parent`).
- `wire_bidirectional_refs(graph, rule, ref_scopes) -> None`
- `wire_lex_specialis_override(graph, exception_rule, general_rule, note="conditional exception") -> None`
- `markovian_overwrite_rule(graph, scope_id, old_rule_id, new_rule_data) -> dict[str, Any]`: mutates `old_rule_id` in place with `new_rule_data` (venting the superseded scalar with zero `"previously ..."` residue, recording `incoming_meta["rationale"]` as `"transition_rationale"` in `_meta` while preserving any pre-existing `raw_meta["rationale"]` from the original rule so both original and transition rationales survive, clamping via `clamp_rule_meta`, and updating `refs`).
- `prune_dysfluency_and_upsert_rule(graph, scope_id, rule_data) -> dict[str, Any]`: upserts a clean `RuleEntry` into `scopes[scope_id]`, validating that every entry in `exceptions` is a `dict` (`ValueError` on bare string), clamping `_meta`, and wiring bidirectional `refs`.
- `graduate_task_to_fact(graph, task_id, current_turn=1) -> dict[str, Any]`: removes `task_id` from `graph["active_tasks"]` and upserts a persistent `FACT` rule (`id = f"fact_{task_id}"`, `facet = "context_facts"`) into its scope.
- `two_axis_mitosis(graph, scope_id=None, k_cap=5) -> dict[str, Any]`: recursively partitions any scope with `len(rules) > k_cap` along:
  1. **Axis 1:** sub-entity prefix before `"_"` or `"/"` on `target_prop` (when $\ge 2$ groups and `max_group_len < len(rules)`),
  2. **Axis 2:** functional `facet` (`len(facet_groups) >= 2`),
  3. **Axis 3:** non-null `target_prop` (`len(prop_groups) >= 2`),
  4. **Fallback:** balanced pages `page_1, page_2, ...` of $\le k\_cap$ rules.
  Enforces 100% lossless rule conservation (`count_after == count_before`, raising `ValueError` fail-closed on any mismatch).

### 1.3 Seed Activation & Graph-Structural Temperature Surface (`activation.py` / `frustration.py`)
- `compute_seed_activations(graph, query) -> tuple[dict[str, float], set[str]]`: computes $A^{(0)}(u \mid Q)$ by calling the shared reusable similarity primitives (`lexical_token_similarity`, `max_lexical_similarity`, `soft_token_overlap_count`, `dense_text_similarities` imported from `core.circuit.nlp`), combining exact identifier/lemma matching, `spaCy` `en_core_web_lg` 300-D word-vector cosine similarity, and `bge-small-en-v1.5` 384-D ONNX dense rule embeddings (`weights/bge_small_en_v15_int8`), with zero `qt[:6] == rt[:6]` character-slice hacks, zero English keyword lists, and **zero order-dependent loop mutation of `scope_seed_score`**:
  - **Scopes (`initial_scope_score`):** exact ID/alias substring match `0.85`, token overlap up to `0.90` (computed once per scope before evaluating `rules_list` and kept immutable while scoring individual rules via a pure helper `_score_single_rule(r, bge_sim, q_clean, q_tokens, scope_anchored, scope_meta, initial_scope_score) -> tuple[float, bool]`).
  - **Rules (Order-Independent Scoring via `_score_single_rule`):**
    1. `target_prop` or any referenced identifier in `r.get("refs", [])` match yields `0.80` (`rule_anchors_scope = True`).
    2. On `scope_anchored` scopes (`initial_scope_score > 0.0` or any rule in the scope matches `target_prop`/`refs`), a rule's schema `type`/`facet` tokens `type_facet_toks` match when `bool(q_tokens & type_facet_toks)` or `max_lexical_similarity(q_tokens, type_facet_toks) >= 0.65`, yielding `0.80` (`rule_anchors_scope = True`).
    3. Across rule content tokens `r_tokens` (`text`, `target_prop`, `conditions`, `refs`, plus `type_facet_toks` when `scope_anchored`), `overlap_r, soft_r = soft_token_overlap_count(q_tokens, r_tokens, threshold=0.70)` yields `min(0.95, 0.50 * ((len(overlap_r) + 0.85 * soft_r) / denom_r))`.
    4. On `scope_anchored` scopes, batched L2-normalized `bge-small-en-v1.5` cosine similarity `bge_sim` via `dense_text_similarities(query, [f"[{r.get('type', '')} {r.get('facet', '')}] {r.get('text', '')}" for r in rules])` with `bge_sim >= 0.72` yields `r_score = max(r_score, round(bge_sim, 6))` (`rule_anchors_scope = True`).
    5. Non-`heading_path` `meta_tokens`/`_meta` overlap (excluding tokens already present in the scope's own ID/alias token set `s_tokens`) adds `+0.25` per token up to `0.85`, and if `initial_scope_score >= 0.50`, `r_score = max(r_score, round(initial_scope_score * 0.75, 6))`. After all rules in `rules_list` are scored, `scope_seed_score = max(initial_scope_score, 0.50 if any_rule_anchored else 0.0)`.
  - **Active Tasks:** exact task ID `1.0`, token overlap up to `0.90`.
  Returns `(activations, seed_scopes)`.
- `infer_query_temperature(graph, query) -> float`: total function mapping `seed_scopes` and cross-scope topology (`_scope_cross_degree`) onto `{0.20, 0.30, 0.70}` with zero keyword lists:
  - $|S_{\text{seed}}| == 0 \Rightarrow 0.30$ (**Standard**),
  - $|S_{\text{seed}}| \ge 3 \Rightarrow 0.70$ (**Warm**),
  - $|S_{\text{seed}}| == 1 \wedge \max_{s \in S_{\text{seed}}} \text{cross\_deg}(s) == 0 \wedge \neg\text{has\_active\_task\_seed} \Rightarrow 0.20$ (**Cold**),
  - Otherwise $\Rightarrow 0.30$ (**Standard**).
- `compute_tau(h_max: float, temperature: float) -> float`: validates `temperature` $\in [0.05, 1.00]$ and `h_max >= 0.0` (`ValueError` otherwise) and evaluates the single monotone linear law:
  $$\alpha(T) = \text{clip}(0.55 - 0.50 \cdot T,\; 0.15,\; 0.50), \quad \tau_{\min}(T) = \text{clip}(0.35 - 0.50 \cdot T,\; 0.10,\; 0.25), \quad \tau(T) = \max\big(\tau_{\min}(T),\; \alpha(T) \cdot A_{\max}\big)$$

### 1.4 Thermodynamic Read & Multi-Hop Propagation Surface (`read_engine.py` / `formatter.py`)
- `_pack_active_rules_and_lod2(scopes, rule_idx, candidate_rids, activations, q_tokens, conflicts, eff_budget, max_lines) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]`: two-pass packer that (Pass 1) packs all fitting `candidate_rids` into `packed_rules` at LOD 1 in `(-A, id)` order within `eff_budget` and `max_lines`, and then (Pass 2) iterates over the packed rules in order to attach `lod2_meta[rid]` (when `q_tokens` overlaps non-`heading_path` `_meta` tokens excluding the rule's own scope ID tokens) within the remaining `eff_budget` and `max_lines`, ensuring LOD 2 metadata never starves any active rule from `packed_rules`.
- `scan_graph(graph, query, temperature=None, token_budget=400, max_lines=35, *, max_tokens=None) -> dict[str, Any]`: single-hop ($L_{\max} = 1$) index scanner packing active scopes (passing compact `@ SCOPE [<sid>] attrs: ...` `scope_headers` into `format_compact_view` for up to 3 top-activated scopes whose non-`updated_turn` `attributes` overlap `q_tokens`, without adding extra keys to `view`), rules (via `_pack_active_rules_and_lod2`), active tasks (annotated with readiness status `"READY"`, `"WAITING_ON_DEPENDENCY"`, `"BLOCKED_BY_RULE"`, or `"BLOCKED_BY_CONFLICT"`), and open blockers in single-pass `(-A, id)` order within `<= token_budget` tokens and `<= max_lines` lines.
- `propagate_graph(graph, query, temperature=None, token_budget=400, max_lines=35, gamma=0.75, max_hops=4, *, max_steps=None, max_tokens=None) -> dict[str, Any]`: multi-hop relaxation across all 5 edge families (`hierarchy`, `refs`/`in_edges`, `overrides`/`exceptions`, `depends_on`, `governed_by`) until unresolved boundary energy $E^{(t)} = 0$ or $\Delta E^{(t)} = 0$, matching `active_tasks` where `act.get(tid, 0.0) >= tau or t.get("scope") in active_scopes or any(d in active_scopes for d in t.get("depends_on", []))`, packing rules and LOD 2 `_meta` via `_pack_active_rules_and_lod2`, passing compact `@ SCOPE [<sid>] attrs: ...` `scope_headers` into `format_compact_view` for up to 3 top-activated scopes with non-empty `attributes` overlapping `q_tokens` (or when `len(active_scopes) <= 3`), failing closed with an empty frontier when $\max_v A^{(0)}(v) < \tau_{\min}(T)$ or returning `"status": "CONFLICT"` when active rules are frustrated.
- `expand_node(graph, node_id: str) -> dict[str, Any]`: on-demand **Pull** drill-down (`LOD 0 -> 1` or `LOD 1 -> 2`) for any `scope_id` or `rule_id` (raising `KeyError` if `node_id` is unknown), including `"active_tasks"` on scope expansions for any task whose `scope == node_id` or whose `depends_on` includes `node_id`.

---

## 2. Canonical Spin-Glass Frustration Contract (`detect_frustration` in `frustration.py`)

`detect_frustration(graph, active_rule_ids=None) -> list[dict[str, Any]]`:
Two active rules $r_a, r_b$ belonging to the same root scope family (`sid.split("::")[0]`) are **frustrated** (emitting a `"type": "CONFLICT"` blocker dict `{"id", "type": "CONFLICT", "scope", "target_prop", "rule_ids": [id_a, id_b], "text"}`) iff all three conditions hold:
1. **No *Lex Specialis* Link:** Neither rule lists the other in `overrides` or structured `exceptions[*]["rule_id"]` (any non-`dict` entry in `exceptions` raises `ValueError` fail-closed).
2. **Same `target_prop` & Overlapping `conditions`:** $r_a.\text{target\_prop} \text{ is not None}$ and $r_a.\text{target\_prop} == r_b.\text{target\_prop}$, and `not r_a["conditions"] or not r_b["conditions"] or bool(set(r_a["conditions"]) & set(r_b["conditions"]))`.
3. **Incompatible Structured Constraint (`_rules_clash` — Formalized in [`LAWS.bend`](LAWS.bend) Laws 8–12):**
   - **Numeric Bound Clash:** Both `bound_val`s are numeric (`int`/`float` and not `bool`) and `op_a, op_b in ("<=", "<", ">=", ">", "==", "!=")` and either (a) their intervals over $\{<, \le, ==, !=, \ge, >\}$ are disjoint (`not _intervals_satisfiable(op_a, float(va), op_b, float(vb))`), or (b) both rules have `type == "BOUND"` and specify different values for the same non-`!=` bound operator (`ta == "BOUND" and tb == "BOUND" and op_a == op_b and op_a != "!=" and float(va) != float(vb)`). If either operator is a one-sided inequality (`op_a in ("<=", "<", ">=", ">") or op_b in ("<=", "<", ">=", ">")`) and neither (a) nor (b) holds, return `False`; otherwise (`op_a, op_b in ("==", "!=", None)`), continue to Symbolic/Equality Clash and Deontic Polarity Clash so numeric and symbolic `==`/`!=` constraints obey identical deontic polarity laws (**Law 9** `rules_clash_same_num_str_parity` and **Law 10** `rules_clash_diff_num_str_parity`).
   - **Equality/Inequality Value Clash:** Both `bound_val`s are non-`None` (`same_scalar = (float(va) == float(vb)) if (a_is_num and b_is_num) else (str(va).strip().lower() == str(vb).strip().lower())`) and either both have `bound_op == "=="` with `not same_scalar`, or `{op_a, op_b} == {"==", "!="}` with `same_scalar`. If `op_a == "!=" and op_b == "!="`, return `False` (**Law 12** `mutual_neq_never_clashes`).
   - **Deontic Polarity Clash:** `not (op_a == "!=" and op_b == "!=")` and one rule has `type == "PROHIBITION"` (`Neg{}`) while the other has `type in ("OBLIGATION", "POLICY", "ACTION", "BOUND")` (`Pos{}`), and their `bound_val`s are unconstrained or equal (`va is None or vb is None or same_scalar`) (**Law 8** `rules_clash_symmetric` and **Law 11** `prohibition_clashes_with_positive_on_same_num`).

### 2.1 Formal Bend 2 Laws (`docs/specs/context_graph/LAWS.bend` & `ModelCircuits/eng3/bend_poc/`)
Every `eng3` build tag must prove all 12 universal laws in [`LAWS.bend`](LAWS.bend) via `ModelCircuits/eng3/bend_poc/PROOF.bend` (`scripts/bend_gate.py check ModelCircuits/eng3`):
- **Laws 1–7 (Signed Polarity Path Algebra & Bounded Decay):** `inv_involutive`, `comp_pos_right`, `comp_assoc`, `path_append`, `self_never_frustrated`, `neg_always_frustrated`, and `decay_zero_floor`.
- **Law 8 (`rules_clash_symmetric`):** `rules_clash(ta, op_a, tb, op_b, vrel) == rules_clash(tb, op_b, ta, op_a, vrel)` across all 1,620 `(ta: RuleType, op_a: EqOp, tb: RuleType, op_b: EqOp, vrel: ValRel)` states.
- **Laws 9 & 10 (`rules_clash_same_num_str_parity`, `rules_clash_diff_num_str_parity`):** Equal numeric values (`VSameNum{}`) behave identically to equal symbolic strings (`VSameStr{}`), and distinct numeric values (`VDiffNum{}`) behave identically to distinct symbolic strings (`VDiffStr{}`), across all `(ta, op_a, tb, op_b)`.
- **Law 11 (`prohibition_clashes_with_positive_on_same_num`):** `rules_clash(RTProhib{}, OpEq{}, tb, OpEq{}, VSameNum{}) == is_pos_pol(rule_pol(tb))` (`True{}` iff `tb` is `RTOblig{}`, `RTPolicy{}`, `RTAction{}`, or `RTBound{}`).
- **Law 12 (`mutual_neq_never_clashes`):** `rules_clash(ta, OpNeq{}, tb, OpNeq{}, vrel) == False{}` for all `(ta, tb, vrel)`.
