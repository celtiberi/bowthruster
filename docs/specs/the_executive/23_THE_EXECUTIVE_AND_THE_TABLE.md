# Spec 23 — `the_executive` v1 (Prefrontal Whiteboard Gate, Pre-Flight Trial, & Executive Voice)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-07 · **Product Scope:** `ModelCircuits/core/circuit/the_executive/`  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md) · [`../00b_PROMPT_AND_QA_GOVERNANCE.md`](../00b_PROMPT_AND_QA_GOVERNANCE.md) · [`../00d_BEND2_LAW_DRIVEN_DEVELOPMENT_AND_MIGRATION.md`](../00d_BEND2_LAW_DRIVEN_DEVELOPMENT_AND_MIGRATION.md)  
**Stage Specs:**
- **Stage `23A` (`eng1`):** [`../executive_23a/23a_EXECUTIVE_PREFLIGHT_AND_TRIAL.md`](../executive_23a/23a_EXECUTIVE_PREFLIGHT_AND_TRIAL.md) · [`../executive_23a/LAWS.bend`](../executive_23a/LAWS.bend)
- **Stage `23B` (`eng3`):** [`../executive_23b/23b_EXECUTIVE_ROUTING_GATE.md`](../executive_23b/23b_EXECUTIVE_ROUTING_GATE.md) · [`../executive_23b/LAWS.bend`](../executive_23b/LAWS.bend)
- **Stage `23C` (`eng2`):** [`../executive_23c/23c_EXECUTIVE_VOICE_AND_ORCHESTRATOR.md`](../executive_23c/23c_EXECUTIVE_VOICE_AND_ORCHESTRATOR.md) · [`../executive_23c/LAWS.bend`](../executive_23c/LAWS.bend)

---

## 1. Architectural Purpose & Vertical Slice (`regiment` $\rightarrow$ `the_executive` v1 $\rightarrow$ `whiteboard`)

**`the_executive` v1** ("The Boss at the Head of the Table") sits directly between **`regiment`** (`Phase 0–3`, which compiles raw human text into a clean, bracket-free `IntakePacket`) and **`whiteboard`** (`Spec 22A–22C`, the persistent bounded Context Graph).

Before any turn is allowed to mutate the live `Whiteboard`, `the_executive` v1 performs a non-destructive pre-flight inspection and makes one binary transactional decision:
1. **`COMMIT_TO_WHITEBOARD`** (`bounce_reason == "NONE"`) — Every proposition in the turn is grounded (`clarification_questions == []` and `open_questions == []`), introduces zero rule/constraint collisions (`conflicts == []`), and contains zero flawed/self-defeating premises (`flawed_ideas == []`). The Executive commits the turn to the Whiteboard and confirms the update.
2. **`SEND_BACK_TO_USER`** (`bounce_reason` $\in$ `{"RULE_CONFLICT", "FLAWED_IDEA", "NEEDS_CLARIFICATION"}`) — One or more propositions collide with a standing rule (`RULE_CONFLICT`), rest on a false premise that contradicts established `FACT`s/table attributes or sabotages a stated `GOAL` (`FLAWED_IDEA`), or contain unresolved ambiguity/open questions (`NEEDS_CLARIFICATION`). The Executive **keeps the marker capped** (leaving the live `Whiteboard` bit-for-bit untouched) and sends a concise, story-calibrated inquiry back to the user naming the exact rule collision, flawed premise, or missing target.

---

## 2. Three-Engineer Modular Decomposition (`Spec 23A`, `Spec 23B`, `Spec 23C`)

| Stage | Assigned Engineer | Promoted Target (`core/circuit/the_executive/`) | Bend 2 Proof Directory | Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| **`Spec 23A`** | **`eng1`** | `preflight.py` | `ModelCircuits/eng1/bend_exec_23a/` | Non-destructive dry-run Whiteboard trial (`copy.deepcopy` with `_sqlite_store=None`), per-MCU ambiguity/question/flawed-idea/rule-conflict inspection (`classify_mcu_status`), and `PreflightReport` compilation (`inspect_turn_preflight`). |
| **`Spec 23B`** | **`eng3`** | `gate.py` | `ModelCircuits/eng3/bend_exec_23b/` | Bend 2-verified Executive Routing Gate (`route_executive_decision`, `resolve_bounce_reason`, `should_commit_whiteboard`) and transactional Whiteboard commit/bounce execution (`execute_gate_decision`) guaranteeing bit-for-bit Whiteboard invariance on bounce. |
| **`Spec 23C`** | **`eng2`** | `voice.py`, `orchestrator.py`, `__init__.py` | `ModelCircuits/eng2/bend_exec_23c/` | Story-calibrated Executive Voice (`prompts/executive/executive.json` with the Four Conference Room Parables), fail-closed reply guard (`verify_executive_reply`), and end-to-end pipeline orchestrator (`run_executive_turn`). |
