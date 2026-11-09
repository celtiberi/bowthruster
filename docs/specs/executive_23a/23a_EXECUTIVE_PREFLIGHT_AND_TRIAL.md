# Spec 23A — `the_executive` Stage 23A: Pre-Flight Whiteboard Trial & Collision Inspector (`preflight.py`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-07 · **Assigned Engineer:** `eng1` (`ModelCircuits/eng1/the_executive/preflight.py` $\rightarrow$ Promoted `ModelCircuits/core/circuit/the_executive/preflight.py`)  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md) · **Formal Laws:** [`LAWS.bend`](LAWS.bend) · **Dependencies:** `core.circuit.whiteboard`, `core.circuit.phase3.intake_packet`

---

## 1. Purpose, Isolation Boundary, & Module Surface

**Stage 23A (`preflight.py`)** is the first stage of `the_executive` v1. Given an incoming `IntakePacket` (or `Phase3Result`) from `regiment` and the current `whiteboard_graph`, `preflight.py` performs a **non-destructive dry-run trial** on a deep-copied scratch Whiteboard (with SQLite persistence detached so the live database and live graph are never mutated during pre-flight) and inspects every incoming `ParsedMCU` for:
1. **Rule & Constraint Collisions (`RULE_CONFLICT`):**
   - **Rule-vs-Rule Spin-Glass Frustration:** Newly introduced frustration edges in `detect_frustration(trial_graph)` that were not present in the pre-turn `whiteboard_graph` (when neither rule in the pair is a `FACT` or `GOAL`).
   - **Action/Rule-vs-Standing-Rule Clash:** Any incoming `ParsedMCU` whose action or rule clashes with a pre-existing standing `PROHIBITION`, `BOUND`, `OBLIGATION`, or `POLICY` rule on the Whiteboard (preventing silent `MARKOVIAN_OVERWRITE` of a prior turn's standing rule without an explicit `is_exception` or `superseded_literals`).
2. **Flawed / Self-Defeating Ideas (`FLAWED_IDEA`):**
   - **Fact / Table-Attribute Contradiction (`FACT_CONTRADICTION`):** An incoming `PROPOSAL`, `ACTION`, or `RULE` assumes or targets a state (`target_prop`, `bound_op`, `bound_val`) that contradicts an established `FACT` node (`r_old.get("type") == "FACT"`) or fused table attribute (`scopes[sid]["attributes"][target_prop]`) on the Whiteboard (or an earlier `FACT` in the same turn).
   - **Goal / Teleological Self-Sabotage (`GOAL_SABOTAGE`):** An incoming `PROPOSAL`, `ACTION`, or `RULE` clashes via `_inherited_rules_clash` with an active `GOAL` node (`r_old.get("type") == "GOAL"`) in the same scope family or `"global_defaults"`, or carries an explicit self-contradicting rationale.
   - **Explicit Critique Flag (`CRITIQUE_FLAGGED`):** Any `pmcu` carrying a non-empty `pmcu.get("flawed_reason")` string, or any entry in the optional `flawed_idea_critiques: list[dict[str, Any]] | None = None` passed to `inspect_turn_preflight(...)`.
3. **Ambiguity (`NEEDS_CLARIFICATION`):** Unresolved `unclear_spans` or `grounding_status == "NEEDS_CLARIFICATION"`.
4. **Open Questions (`HAS_OPEN_QUESTION`):** User questions (`type == "QUESTION"` or `grounding_status == "HAS_OPEN_QUESTIONS"`).

### Isolation & File-Size Constraints
- `eng1` writes **only** `ModelCircuits/eng1/the_executive/{__init__.py,preflight.py}` ($\le 450$ lines each, zero `import re`) and `ModelCircuits/eng1/bend_exec_23a/{main.bend,PROOF.bend,LAWS.bend}`.
- `eng1` may read `core/circuit/whiteboard/*.py`, `core/circuit/graph_core/*.py`, and `core/circuit/phase3/intake_packet.py`.

---

## 2. Public Constants, Pure Functions, & Data Contracts (`preflight.py`)

### 2.1 Constants & Error Class
- `MCU_STATUS_CLEAN = "CLEAN"`
- `MCU_STATUS_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"`
- `MCU_STATUS_HAS_OPEN_QUESTION = "HAS_OPEN_QUESTION"`
- `MCU_STATUS_FLAWED_IDEA = "FLAWED_IDEA"`
- `MCU_STATUS_RULE_CONFLICT = "RULE_CONFLICT"`
- `VALID_MCU_PREFLIGHT_STATUSES = frozenset({MCU_STATUS_CLEAN, MCU_STATUS_NEEDS_CLARIFICATION, MCU_STATUS_HAS_OPEN_QUESTION, MCU_STATUS_FLAWED_IDEA, MCU_STATUS_RULE_CONFLICT})`
- `FLAW_KIND_FACT_CONTRADICTION = "FACT_CONTRADICTION"`
- `FLAW_KIND_GOAL_SABOTAGE = "GOAL_SABOTAGE"`
- `FLAW_KIND_CRITIQUE_FLAGGED = "CRITIQUE_FLAGGED"`
- `class ExecutivePreflightError(ValueError)`:
  - `__init__(self, code: str, errors: list[str]) -> None` setting `self.code = code`, `self.errors = list(errors)`, and `super().__init__(f"{code}: {'; '.join(self.errors)}")`.

### 2.2 Bend 2-Backed Pure Classification Functions
1. `classify_mcu_status(has_unclear: bool, is_question: bool, has_clash: bool, has_flawed_idea: bool = False) -> str`:
   - Exact discrete truth-table proven in [`LAWS.bend`](LAWS.bend):
     - If `has_clash`: return `MCU_STATUS_RULE_CONFLICT` (**Law 1 `clash_dominates_mcu_status`**).
     - Else if `has_flawed_idea`: return `MCU_STATUS_FLAWED_IDEA` (**Law 2 `flawed_idea_dominates_when_no_clash`**).
     - Else if `has_unclear`: return `MCU_STATUS_NEEDS_CLARIFICATION` (**Law 3 `unclear_dominates_when_no_clash`**).
     - Else if `is_question`: return `MCU_STATUS_HAS_OPEN_QUESTION`.
     - Else: return `MCU_STATUS_CLEAN` (**Law 4 `mcu_clean_iff_all_clear`**).
2. `is_preflight_clean(has_ambiguity: bool, has_open_questions: bool, has_rule_conflict: bool, has_flawed_idea: bool = False) -> bool`:
   - Returns `not (has_ambiguity or has_open_questions or has_rule_conflict or has_flawed_idea)` (**Law 5 `preflight_clean_iff_no_blockers`**).

### 2.3 `normalize_intake_packet(packet_or_phase3: dict[str, Any], *, turn_index: int = 1) -> dict[str, Any]`
- Accepts either:
  - A compiled `IntakePacket` dict containing `"parsed_mcus"`, OR
  - A `Phase3Result` dict containing `"intake_packet"` (or `"mcus"` and `"propositions"`, compiled via `compile_intake_packet(packet_or_phase3, turn_index=turn_index)`).
- Fails closed by raising `ExecutivePreflightError("preflight_invalid_packet", [...])` if `packet_or_phase3` is not a `dict`, if `turn_index` is not a positive `int` (`not isinstance(turn_index, bool)`), or if the resulting packet lacks a `list` under `"parsed_mcus"`.
- Returns a deep-copied dict with keys `("turn_index", "doc_tables", "parsed_mcus", "grounding_status", "is_locked", "clarification_questions", "open_questions")`.

### 2.4 `clone_whiteboard_for_trial(whiteboard_graph: dict[str, Any] | None) -> dict[str, Any]`
- If `whiteboard_graph is None`, returns `create_empty_context_graph()`.
- If `whiteboard_graph` is not a `dict`, raises `ExecutivePreflightError("preflight_invalid_whiteboard", [...])`.
- Temporarily detaches any `_sqlite_store` attribute/key before calling `copy.deepcopy` so copying never duplicates open SQLite handles and trial mutations never touch disk, then restores `_sqlite_store` on the original `whiteboard_graph` and ensures `trial_graph._sqlite_store = None` (and removes `"_sqlite_store"` key if present in `trial_graph`).

### 2.5 `inspect_turn_preflight(packet_or_phase3: dict[str, Any], whiteboard_graph: dict[str, Any] | None = None, *, turn_index: int | None = None, flawed_idea_critiques: list[dict[str, Any]] | None = None) -> dict[str, Any]`
Executes the non-destructive pre-flight inspection of a turn against `whiteboard_graph`:
1. **Determine `eff_turn` & Normalize Packet:**
   - Computes `prev_turn = max(int(whiteboard_graph.get("turn", 0)), int(whiteboard_graph.get("turn_index", 0)))` when `isinstance(whiteboard_graph, dict)` else `0`.
   - Sets `eff_turn = int(turn_index) if turn_index is not None else int(packet_or_phase3.get("turn_index", max(1, prev_turn + 1)))` (validating that `eff_turn >= 1` and not `bool`).
   - Normalizes `packet = normalize_intake_packet(packet_or_phase3, turn_index=eff_turn)` and sets `packet["turn_index"] = eff_turn`.
2. **Snapshot Pre-Turn Whiteboard & Existing Frustrations:**
   - Creates `base_graph = clone_whiteboard_for_trial(whiteboard_graph)` and `trial_graph = clone_whiteboard_for_trial(base_graph)`.
   - Computes `pre_existing_conflicts = detect_frustration(base_graph)` and `pre_conflict_ids = {c["id"] for c in pre_existing_conflicts}`.
   - Builds `base_rules_by_id: dict[str, tuple[str, dict[str, Any]]]` mapping `rule["id"] -> (scope_id, rule)` across all scopes in `base_graph`.
3. **Run Dry-Run Trial Ingestion on `trial_graph`:**
   - Calls `trial_res = ingest_intake_packet(trial_graph, packet)` on `trial_graph`.
   - Extracts `new_frustrations = [c for c in trial_res["conflicts"] if c["id"] not in pre_conflict_ids]`.
4. **Map Collisions, Flawed Ideas, & Ambiguities per `ParsedMCU`:**
   - Matches each `pmcu` in `packet["parsed_mcus"]` with its corresponding delta entry in `trial_res["delta_log"]` (by `mcu_id`).
   - Resolves `scope_id` for `pmcu` (from its delta entry or `resolve_mcu_scopes_and_refs(base_graph, pmcu)[0]`).
   - Collects candidate standing rules in `base_graph` across the resolved `scope_id`, its root scope family (`scope_id.split("::")[0]`), its ancestor scopes (`_ancestor_scopes(base_graph["scopes"], scope_id)`), and `"global_defaults"`.
   - **Rule Conflict Check (`mcu_conflicts`):**
     - Any new frustration edge in `new_frustrations` involving `pmcu`'s rule where the opposing rule has `type` in `("PROHIBITION", "BOUND", "OBLIGATION", "POLICY")`, **or** any structural/flow conflict in `new_frustrations` (`str(frust.get("id", "")).startswith(("conflict_dep_", "conflict_flow_", "conflict_payload_"))`) whose `scope == root_sid` or whose `flow_id` matches `delta_entry.get("flow_id")` when `pmcu.get("lattice_kind") in ("COMPONENT", "FLOW")` (so introducing a dependency/hierarchy cycle, prohibited import, or flow payload mismatch bounces immediately at Pre-Flight with `RULE_CONFLICT`).
     - Any standing rule `r_old` in `base_graph` with `r_old.get("type") in ("PROHIBITION", "BOUND", "OBLIGATION", "POLICY")` where:
       - `pmcu.get("type") == "ACTION"` and either `r_old` is an unshielded `PROHIBITION` in the same scope family (`r_old.get("target_prop") is None or r_old.get("target_prop") == pmcu.get("target_prop")`) or `pmcu` shares `target_prop` (`target_prop is not None`) and clashes via `_inherited_rules_clash(pmcu_rule_view, r_old, bool(pmcu.get("is_exception")))`; OR
       - `pmcu.get("type") in ("PROHIBITION", "BOUND", "OBLIGATION", "POLICY")` shares `target_prop` (`target_prop is not None`) and clashes with `r_old` via `_inherited_rules_clash(pmcu_rule_view, r_old, bool(pmcu.get("is_exception")))` when `not pmcu.get("is_exception")` and `not pmcu.get("superseded_literals")`.
     - Note: When construirng `pmcu_rule_view` for an `ACTION` that has `target_prop` and `bound_val` (e.g. `bound_op="=="`), if `r_old.get("type") == "BOUND"`, also check `_rules_clash({"type": "BOUND", "target_prop": pmcu.get("target_prop"), "bound_op": pmcu.get("bound_op") or "==", "bound_val": pmcu.get("bound_val")}, r_old)` so an `ACTION` whose parameter violates a numeric `BOUND` (`e.g. bound_val=20` vs `<= 10`) is caught!
   - **Flawed Idea Check (`mcu_flaws`):**
     - **(a) Fact / Attribute Contradiction (`FLAW_KIND_FACT_CONTRADICTION`):**
       - Any `r_fact` in `base_graph` (or earlier in `trial_graph`) with `r_fact.get("type") == "FACT"` sharing `target_prop` (`target_prop is not None`) that clashes with `pmcu` (`pmcu.get("type") in ("PROPOSAL", "ACTION", "OBLIGATION", "BOUND", "PROHIBITION")`) via `_inherited_rules_clash` (or where `r_fact.get("bound_val")` and `pmcu.get("bound_val")` are incompatible).
       - Or any fused table attribute `attr_val = base_graph.get("scopes", {}).get(scope_id, {}).get("attributes", {}).get(target_prop)` where `target_prop is not None` and `pmcu.get("type") in ("PROPOSAL", "ACTION")` clashes with `{"type": "FACT", "target_prop": target_prop, "bound_op": "==", "bound_val": attr_val}` via `_rules_clash`.
     - **(b) Goal Self-Sabotage (`FLAW_KIND_GOAL_SABOTAGE`):**
       - Any `r_goal` in `base_graph` (or earlier in `trial_graph`) with `r_goal.get("type") == "GOAL"` sharing `target_prop` (`target_prop is not None`) that clashes with `pmcu` via `_inherited_rules_clash` (or where `r_goal` and `pmcu` have opposing polarity / disjoint numeric intervals on `target_prop`).
     - **(c) Explicit Premise / Idea Critique (`FLAW_KIND_CRITIQUE_FLAGGED`):**
       - If `isinstance(pmcu.get("flawed_reason"), str) and pmcu["flawed_reason"].strip()`, or if `flawed_idea_critiques` contains a critique dict matching `mcu_id` (`c.get("mcu_id") == mcu_id`).
     - Each entry in `flawed_ideas` / `mcu_flaws` has keys:
       ```python
       {
           "flaw_id": str,
           "mcu_id": int,
           "flaw_kind": str,       # "FACT_CONTRADICTION" | "GOAL_SABOTAGE" | "CRITIQUE_FLAGGED"
           "scope": str,
           "target_prop": str | None,
           "incoming_text": str,
           "basis_id": str,
           "basis_text": str,
           "description": str,
       }
       ```
   - Computes for each `pmcu`:
     - `has_unclear = bool(pmcu.get("unclear_spans")) or pmcu.get("grounding_status") == "NEEDS_CLARIFICATION"`
     - `is_question = pmcu.get("type") == "QUESTION" or pmcu.get("grounding_status") == "HAS_OPEN_QUESTIONS"`
     - `has_clash = bool(mcu_conflicts)`
     - `has_flaw = bool(mcu_flaws)`
     - `status = classify_mcu_status(has_unclear, is_question, has_clash, has_flaw)`
5. **Compile and Return `PreflightReport` Dict:**
   ```python
   {
       "turn_index": eff_turn,
       "intake_packet": packet,
       "is_clean": is_preflight_clean(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea),
       "has_ambiguity": has_ambiguity,
       "has_open_questions": has_open_questions,
       "has_rule_conflict": has_rule_conflict,
       "has_flawed_idea": has_flawed_idea,
       "mcu_findings": mcu_findings,             # list[dict]: {"mcu_id", "status", "text", "scope", "target_prop", "unclear_spans", "conflicts", "flawed_ideas"}
       "clean_mcus": clean_mcus,
       "ambiguous_mcus": ambiguous_mcus,
       "question_mcus": question_mcus,
       "conflicting_mcus": conflicting_mcus,
       "flawed_mcus": flawed_mcus,
       "conflicts": conflicts,
       "flawed_ideas": flawed_ideas,
       "new_frustrations": new_frustrations,
       "clarification_questions": list(packet.get("clarification_questions", [])),
       "open_questions": list(packet.get("open_questions", [])),
       "trial_ingestion": trial_res,
   }
   ```

---

## 3. Formal Bend 2 Laws (`docs/specs/executive_23a/LAWS.bend` & `ModelCircuits/eng1/bend_exec_23a/`)

Every `eng1` build tag for `Spec 23A` must prove Laws 1–5 in [`LAWS.bend`](LAWS.bend) via `ModelCircuits/eng1/bend_exec_23a/PROOF.bend` (`scripts/bend_gate.py check ModelCircuits/eng1`).
