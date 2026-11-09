# Spec 23C — `the_executive` Stage 23C: Story-Calibrated Executive Voice, Reply Guard, & End-to-End Orchestrator (`voice.py`, `orchestrator.py`, `__init__.py`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-07 · **Assigned Engineer:** `eng2` (`ModelCircuits/eng2/the_executive/{voice,orchestrator,__init__}.py` $\rightarrow$ Promoted `ModelCircuits/core/circuit/the_executive/{voice,orchestrator,__init__}.py`)  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md) · [`../00b_PROMPT_AND_QA_GOVERNANCE.md`](../00b_PROMPT_AND_QA_GOVERNANCE.md) · **Formal Laws:** [`LAWS.bend`](LAWS.bend)  
**Prompt File (Read-Only for `eng2`):** [`../../core/circuit/prompts/executive/executive.json`](../../core/circuit/prompts/executive/executive.json)

---

## 1. Purpose, Isolation Boundary, & Module Surface

**Stage 23C (`voice.py`, `orchestrator.py`, `__init__.py`)** completes `the_executive` v1 by pairing the Bend 2-verified Routing Gate (`Spec 23A` + `Spec 23B`) with:
1. **Story-Calibrated Executive Voice (`voice.py`):** Loads `executive.synthesize_turn` from `ModelCircuits/core/circuit/prompts/executive/executive.json`—which teaches executive judgment posture through **Four Conference Room Parables** (*The Parable of the Unanchored Instruction*, *The Parable of the Silent Contradiction*, *The Parable of the Foolish Plan*, and *The Parable of the Decisive Commit*) plus strict operating rules—and verifies every LLM reply with a Bend 2-backed fail-closed guard (`verify_executive_reply`) so the model can never override the gate verdict or drop a colliding rule, flawed idea, or clarification question.
2. **End-to-End Pipeline Orchestrator (`orchestrator.py` & `__init__.py`):** Connects **`regiment` (`Phase 0–3`) $\rightarrow$ `the_executive` (`23A` Pre-Flight $\rightarrow$ `23B` Routing Gate $\rightarrow$ `23C` Voice) $\rightarrow$ `whiteboard` (`Spec 22A–22C`)** in `run_executive_turn(...)`.

### Isolation & File-Size Constraints
- `eng2` writes **only** `ModelCircuits/eng2/the_executive/{voice.py,orchestrator.py,__init__.py}` (each $\le 400$ lines, zero `import re`) and `ModelCircuits/eng2/bend_exec_23c/{main.bend,PROOF.bend,LAWS.bend}`.
- `eng2` may read `core/circuit/prompts/executive/executive.json`, `core/circuit/the_executive/{preflight.py,gate.py}`, `core/circuit/whiteboard/*.py`, `core/circuit/graph_core/*.py`, and `core/circuit/phase3/intake_packet.py`.

---

## 2. Public API & Module Contracts

### 2.1 `voice.py` — Prompt Loader, Reply Guard, & Voice Synthesizer

#### A. Prompt Loading (`load_executive_prompt`)
- `EXECUTIVE_PROMPT_KEY = "executive.synthesize_turn"`
- `EXECUTIVE_MAX_TOKENS = 2048`
- Locates `executive.json` at `Path(__file__).resolve().parents[1] / "prompts" / "executive" / "executive.json"` when installed in `core/circuit/the_executive/`, or `Path(__file__).resolve().parents[2] / "core" / "circuit" / "prompts" / "executive" / "executive.json"` when running in `ModelCircuits/eng2/the_executive/`.
- `load_executive_prompt(key: str = EXECUTIVE_PROMPT_KEY) -> dict[str, Any]`: returns `{"version": data["version"], "text": entry["text"], "name": entry["name"], "schema": copy.deepcopy(entry["schema"])}`.
- `format_executive_prompt(gate_result: dict[str, Any]) -> str`: formats `entry["text"]` with the 8 required placeholders `gate_decision`, `bounce_reason`, `committed_items`, `conflicts`, `flawed_ideas`, `clarification_questions`, `open_questions`, and `whiteboard_summary`.

#### B. Bend 2-Backed Reply Guard (`is_reply_valid` & `verify_executive_reply`)
1. `is_reply_valid(decision_matches_gate: bool, reason_matches_gate: bool, message_non_empty: bool, all_required_citations_present: bool) -> bool`:
   - Returns `bool(decision_matches_gate and reason_matches_gate and message_non_empty and all_required_citations_present)` (**Laws 1–3 in [`LAWS.bend`](LAWS.bend)**).
2. `verify_executive_reply(gate_result: dict[str, Any], reply: dict[str, Any]) -> list[str]`:
   - Validates that `reply` is a `dict` with keys `("decision", "bounce_reason", "user_message", "cited_items")`:
     - `decision_matches_gate`: `reply.get("decision") == gate_result["decision"]`
     - `reason_matches_gate`: `reply.get("bounce_reason") == gate_result["bounce_reason"]`
     - `message_non_empty`: `isinstance(reply.get("user_message"), str) and bool(reply["user_message"].strip())`
     - `all_required_citations_present`:
       - `cited_items` must be a `list` of strings (`all(isinstance(x, str) and x.strip() for x in cited_items)`).
       - Combined lowercased text `combined_low = (reply["user_message"] + " " + " ".join(cited_items)).lower()`:
         - For every `c` in `gate_result.get("conflicts", [])`: at least one identifying anchor from `c` (`str(c.get("target_prop") or "").lower()`, `str(c.get("existing_rule_id") or "").lower()`, or `str(c.get("scope") or "").lower()`) must appear in `combined_low`, and `len(cited_items) >= 1`.
         - For every `f` in `gate_result.get("flawed_ideas", [])`: at least one identifying anchor from `f` (`str(f.get("target_prop") or "").lower()`, `str(f.get("basis_id") or "").lower()`, `str(f.get("scope") or "").lower()`, or `str(f.get("description") or "").lower()`) must appear in `combined_low`, and `len(cited_items) >= 1`.
         - For every `cq` in `gate_result.get("clarification_questions", [])`: `cq.strip().lower() in combined_low` or `len(cited_items) >= 1` with `user_message` ending in or containing `"?"`.
         - For every `oq` in `gate_result.get("open_questions", [])`: `oq.strip().lower() in combined_low` or `len(cited_items) >= 1`.
         - When `gate_result["decision"] == "COMMIT_TO_WHITEBOARD"` and `gate_result.get("committed_items")` is non-empty: `len(cited_items) >= 1`.
   - Calls `is_reply_valid(decision_matches_gate, reason_matches_gate, message_non_empty, all_required_citations_present)` and returns a list of descriptive error strings (empty iff `is_reply_valid(...) is True`).

#### C. Deterministic & LLM Voice Synthesis (`render_deterministic_executive_reply` & `synthesize_executive_voice`)
1. `render_deterministic_executive_reply(gate_result: dict[str, Any]) -> dict[str, Any]`:
   - Synthesizes a clean, executive-posture reply directly from `gate_result` (used when `gen is None`):
     - When `decision == "SEND_BACK_TO_USER"`:
       - Starts with `"The marker remains capped; nothing from this turn has been written to the Whiteboard."`
       - Lists each rule conflict in `gate_result.get("conflicts", [])` (citing `scope`, `target_prop`, `existing_rule_id`, `existing_rule_text`, and `incoming_text`), each flawed idea in `gate_result.get("flawed_ideas", [])` (citing `flaw_kind`, `scope`, `target_prop`, `basis_id`, `basis_text`, and `description`), each clarification question in `gate_result.get("clarification_questions", [])`, and each open question in `gate_result.get("open_questions", [])`, and closes by asking how the user wants to resolve them.
       - Populates `cited_items` with every conflict `description`, every flawed idea `description`, and every question in `clarification_questions` + `open_questions`.
     - When `decision == "COMMIT_TO_WHITEBOARD"`:
       - Summarizes the committed items written to the Whiteboard (citing their `primary_scope` and `text`) and populates `cited_items` with `[str(m.get("text") or m.get("canonical_text")) for m in gate_result.get("committed_items", [])]`.
2. `synthesize_executive_voice(gate_result: dict[str, Any], *, gen: Any = None, limits: SizeLimits | None = None) -> dict[str, Any]`:
   - If `gen is None`: returns `render_deterministic_executive_reply(gate_result)` after verifying `verify_executive_reply(gate_result, reply) == []`.
   - If `gen is not None`: formats `prompt = format_executive_prompt(gate_result)`, invokes `gen.generate_json(prompt, schema_rec, EXECUTIVE_MAX_TOKENS)` (unpacking `(raw, meta)` when `generate_json` returns a 2-tuple and failing closed with `ExecutiveVoiceError("executive_voice_output_truncated", [...])` if `meta.get("done_reason") in ("length", "max_tokens")`; or for a plain callable `gen`, binding `(prompt, schema_rec, max_tokens)`, `(prompt,)`, or `(gate_result,)` via `inspect.signature`), and validates via `verify_executive_reply(gate_result, reply)` (retrying once if verification fails, and raising `ExecutiveVoiceError("executive_voice_guard_failed", errors)` if still invalid).

---

### 2.2 `orchestrator.py` & `__init__.py` — End-to-End Pipeline (`run_executive_turn`)

- `class ExecutiveTurnResult(dict[str, Any])`: dict subclass with `.to_dict()` and attribute access (`__getattr__`).
- `run_executive_turn(whiteboard_graph: dict[str, Any] | None = None, raw_text: str = "", *, gen: Any = None, jev: Any = None, executive_gen: Any = None, limits: SizeLimits | None = None, turn_index: int | None = None, phase3_result: dict[str, Any] | None = None, intake_packet: dict[str, Any] | None = None, flawed_idea_critiques: list[dict[str, Any]] | None = None, k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING, store: SQLiteGraphStore | None = None, db_path: str | Path | None = None) -> ExecutiveTurnResult`:
  1. **Intake (`regiment`):**
     - If `intake_packet is None`:
       - If `phase3_result is None`: runs `p0 = run_phase0(raw_text, gen, limits)`, `p1 = run_phase1(p0, jev, limits)`, `p2 = run_phase2(p1, jev, limits)`, `phase3_result = run_phase3(p2, gen, limits, jev=jev)`.
       - Extracts `intake_packet = copy.deepcopy(phase3_result["intake_packet"])` (setting `intake_packet["turn_index"] = eff_turn` whenever `turn_index is not None` or `not had_precompiled_intake` or `prev_turn > 0`) or `compile_intake_packet(phase3_result, turn_index=eff_turn)`.
  2. **Stage 23A Pre-Flight:**
     - `preflight_report = inspect_turn_preflight(intake_packet, whiteboard_graph, turn_index=turn_index, flawed_idea_critiques=flawed_idea_critiques)`
  3. **Stage 23B Routing Gate:**
     - `gate_result = execute_gate_decision(preflight_report, whiteboard_graph, k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)`
  4. **Stage 23C Executive Voice & Lattice Proof Radar:**
     - `voice_gen = executive_gen if executive_gen is not None else (gen if (intake_packet is not None or phase3_result is not None) else None)`
     - `voice_reply = synthesize_executive_voice(gate_result, gen=voice_gen, limits=limits)`
     - Extracts `lattice_proof = gate_result.get("lattice_proof") or evaluate_lattice_proof(gate_result["whiteboard_graph"])`. When `gate_result["decision"] == "COMMIT_TO_WHITEBOARD"` and `isinstance(lattice_proof, dict)`: appends a concise `[Lattice Proof: ...]` radar footer to `user_message` (and `voice_reply["user_message"]`) if `"[Lattice Proof:"` is not already in `user_message`:
       - When `lattice_proof.get("is_proven")` is `True`: `"\n[Lattice Proof: PROVEN_COMPLETE — all components, flows, contracts, and Bend 2 laws verified; parallel Work Packets unlocked.]"`
       - When `lattice_proof.get("status") == "INCOMPLETE_DRAFT"` and `lattice_proof.get("open_gaps")` is non-empty: `f"\n[Lattice Proof: INCOMPLETE_DRAFT ({len(gaps)} gap(s) remaining before Work Packets unlock: {'; '.join(g['description'] for g in gaps[:3])})]"`
  5. Returns `ExecutiveTurnResult` containing:
     `("turn_index", "decision", "bounce_reason", "should_commit", "whiteboard_mutated", "user_message", "cited_items", "whiteboard_graph", "committed_items", "blocked_items", "conflicts", "flawed_ideas", "clarification_questions", "open_questions", "intake_packet", "phase3_result", "preflight_report", "gate_result", "voice_reply", "lattice_proof")`.

---

## 3. Formal Bend 2 Laws (`docs/specs/executive_23c/LAWS.bend` & `ModelCircuits/eng2/bend_exec_23c/`)

Every `eng2` build tag for `Spec 23C` must prove Laws 1–3 in [`LAWS.bend`](LAWS.bend) via `ModelCircuits/eng2/bend_exec_23c/PROOF.bend` (`scripts/bend_gate.py check ModelCircuits/eng2`).
