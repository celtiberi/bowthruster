# Spec 23B — `the_executive` Stage 23B: Executive Routing Gate & Transactional Turn Policy (`gate.py`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-07 · **Assigned Engineer:** `eng3` (`ModelCircuits/eng3/the_executive/gate.py` $\rightarrow$ Promoted `ModelCircuits/core/circuit/the_executive/gate.py`)  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md) · **Formal Laws:** [`LAWS.bend`](LAWS.bend) · **Dependencies:** `core.circuit.whiteboard`, `core.circuit.the_executive.preflight` (`Spec 23A`)

---

## 1. Purpose, Isolation Boundary, & Module Surface

**Stage 23B (`gate.py`)** implements the Bend 2-verified **Executive Routing Gate** and **Transactional Whiteboard Commit/Bounce Policy**. Given a `PreflightReport` from Stage 23A (`preflight.py`) and the live `whiteboard_graph`:
- When `should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)` is `True`, the gate commits the turn's `IntakePacket` onto the live `whiteboard_graph` via `ingest_intake_packet`.
- When `should_commit_whiteboard(...)` is `False` (`decision == "SEND_BACK_TO_USER"`, `bounce_reason` $\in$ `{"RULE_CONFLICT", "FLAWED_IDEA", "NEEDS_CLARIFICATION"}`), the gate **caps the marker**: it leaves the live `whiteboard_graph` bit-for-bit untouched and packages the exact blocked items, rule conflicts, flawed ideas, clarification questions, and open questions for Stage 23C (`voice.py`).

### Isolation & File-Size Constraints
- `eng3` writes **only** `ModelCircuits/eng3/the_executive/{__init__.py,gate.py}` ($\le 350$ lines each, zero `import re`) and `ModelCircuits/eng3/bend_exec_23b/{main.bend,PROOF.bend,LAWS.bend}`.
- `eng3` may read `core/circuit/whiteboard/*.py`, `core/circuit/graph_core/*.py`, and `core/circuit/the_executive/preflight.py`.

---

## 2. Public Constants, Pure Functions, & Data Contracts (`gate.py`)

### 2.1 Constants & Error Class
- `DECISION_COMMIT_TO_WHITEBOARD = "COMMIT_TO_WHITEBOARD"`
- `DECISION_SEND_BACK_TO_USER = "SEND_BACK_TO_USER"`
- `VALID_EXECUTIVE_DECISIONS = frozenset({DECISION_COMMIT_TO_WHITEBOARD, DECISION_SEND_BACK_TO_USER})`
- `BOUNCE_NONE = "NONE"`
- `BOUNCE_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"`
- `BOUNCE_FLAWED_IDEA = "FLAWED_IDEA"`
- `BOUNCE_RULE_CONFLICT = "RULE_CONFLICT"`
- `VALID_BOUNCE_REASONS = frozenset({BOUNCE_NONE, BOUNCE_NEEDS_CLARIFICATION, BOUNCE_FLAWED_IDEA, BOUNCE_RULE_CONFLICT})`
- `class ExecutiveGateError(ValueError)`:
  - `__init__(self, code: str, errors: list[str]) -> None` setting `self.code = code`, `self.errors = list(errors)`, and `super().__init__(f"{code}: {'; '.join(self.errors)}")`.

### 2.2 Bend 2-Backed Pure Routing Functions
1. `should_commit_whiteboard(has_ambiguity: bool, has_open_questions: bool, has_rule_conflict: bool, has_flawed_idea: bool = False) -> bool`:
   - Returns `not (bool(has_ambiguity) or bool(has_open_questions) or bool(has_rule_conflict) or bool(has_flawed_idea))` (**Law 1 `commit_iff_all_clear`**).
2. `route_executive_decision(has_ambiguity: bool, has_open_questions: bool, has_rule_conflict: bool, has_flawed_idea: bool = False) -> str`:
   - Returns `DECISION_COMMIT_TO_WHITEBOARD` if `should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)` else `DECISION_SEND_BACK_TO_USER` (**Law 2 `decision_matches_commit_flag`**).
3. `resolve_bounce_reason(has_ambiguity: bool, has_open_questions: bool, has_rule_conflict: bool, has_flawed_idea: bool = False) -> str`:
   - If `has_rule_conflict`: return `BOUNCE_RULE_CONFLICT` (**Law 3 `conflict_dominates_bounce_reason`**).
   - Else if `has_flawed_idea`: return `BOUNCE_FLAWED_IDEA` (**Law 4 `flawed_idea_dominates_when_no_conflict`**).
   - Else if `has_ambiguity or has_open_questions`: return `BOUNCE_NEEDS_CLARIFICATION` (**Law 5 `ambiguity_or_question_bounces_for_clarification`**).
   - Else: return `BOUNCE_NONE` (**Law 6 `bounce_none_iff_commit`**).

### 2.3 `verify_preflight_report(preflight_report: dict[str, Any]) -> list[str]`
Deterministic validator returning a list of error strings (empty when valid):
- Verifies `preflight_report` is a `dict` containing required keys `("turn_index", "intake_packet", "is_clean", "has_ambiguity", "has_open_questions", "has_rule_conflict", "has_flawed_idea", "mcu_findings", "conflicts", "flawed_ideas", "clarification_questions", "open_questions")`.
- Verifies `has_ambiguity`, `has_open_questions`, `has_rule_conflict`, `has_flawed_idea`, and `is_clean` are `bool`, and that `is_clean == should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)`.
- Verifies `has_rule_conflict == (len(preflight_report["conflicts"]) > 0)` and `has_flawed_idea == (len(preflight_report["flawed_ideas"]) > 0)`.

### 2.4 `execute_gate_decision(preflight_report: dict[str, Any], whiteboard_graph: dict[str, Any] | None = None, *, k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING, store: SQLiteGraphStore | None = None, db_path: str | Path | None = None) -> dict[str, Any]`
Executes the transactional Whiteboard gate decision:
1. Validates `preflight_report` via `verify_preflight_report(preflight_report)` and raises `ExecutiveGateError("gate_invalid_preflight_report", errors)` if non-empty.
2. If `whiteboard_graph is not None` and not `isinstance(whiteboard_graph, dict)`, raises `ExecutiveGateError("gate_invalid_whiteboard", [...])`.
3. Extracts `has_ambiguity = preflight_report["has_ambiguity"]`, `has_open_questions = preflight_report["has_open_questions"]`, `has_rule_conflict = preflight_report["has_rule_conflict"]`, and `has_flawed_idea = preflight_report["has_flawed_idea"]`.
4. Computes:
   - `should_commit = should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)`
   - `decision = route_executive_decision(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)`
   - `bounce_reason = resolve_bounce_reason(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea)`
5. **Branch A — `should_commit is True` (`DECISION_COMMIT_TO_WHITEBOARD`):**
   - Calls `ingestion_res = ingest_intake_packet(whiteboard_graph, preflight_report["intake_packet"], k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)`.
   - Sets `active_graph = ingestion_res["graph"]`, `whiteboard_mutated = True`, `committed_items = copy.deepcopy(preflight_report["intake_packet"].get("parsed_mcus", []))`, and `blocked_items = []`.
6. **Branch B — `should_commit is False` (`DECISION_SEND_BACK_TO_USER`):**
   - **Never mutates `whiteboard_graph`:** if `whiteboard_graph is None`, creates an empty whiteboard `active_graph = create_empty_context_graph(k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)` without ingesting any MCU from the turn; if `whiteboard_graph` was passed in, `active_graph = whiteboard_graph` remains bit-for-bit identical to its pre-turn state.
   - Sets `ingestion_res = None`, `whiteboard_mutated = False`, `committed_items = []`, and `blocked_items = copy.deepcopy(preflight_report["intake_packet"].get("parsed_mcus", []))`.
7. **Continuous Lattice Proof Attachment (`lattice_proof`):**
   - Evaluates `lattice_proof = evaluate_lattice_proof(active_graph)` (imported from `core.circuit.whiteboard.frustration`). When `should_commit is True`, also stores `active_graph["lattice_proof"] = lattice_proof`.
8. Returns the `GateDecisionResult` dict:
   ```python
   {
       "turn_index": int(preflight_report["turn_index"]),
       "decision": decision,
       "bounce_reason": bounce_reason,
       "should_commit": should_commit,
       "whiteboard_mutated": whiteboard_mutated,
       "whiteboard_graph": active_graph,
       "ingestion_result": ingestion_res,
       "committed_items": committed_items,
       "blocked_items": blocked_items,
       "conflicts": copy.deepcopy(preflight_report["conflicts"]),
       "flawed_ideas": copy.deepcopy(preflight_report["flawed_ideas"]),
       "clarification_questions": list(preflight_report["clarification_questions"]),
       "open_questions": list(preflight_report["open_questions"]),
       "preflight_report": preflight_report,
       "lattice_proof": lattice_proof,
   }
   ```

---

## 3. Formal Bend 2 Laws (`docs/specs/executive_23b/LAWS.bend` & `ModelCircuits/eng3/bend_exec_23b/`)

Every `eng3` build tag for `Spec 23B` must prove Laws 1–6 in [`LAWS.bend`](LAWS.bend) via `ModelCircuits/eng3/bend_exec_23b/PROOF.bend` (`scripts/bend_gate.py check ModelCircuits/eng3`).
