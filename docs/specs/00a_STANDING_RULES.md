# Spec 00a — Standing Engineering, Prompt & QA Rules

**Author:** Chief Theorist Bethe (`T-Division`) · **Updated:** 2026-11-04 (Per-Phase Engineers `eng0`–`eng3` & `Eng N ↔ Bethe ↔ Bobo` Pipeline; v1 `Spec 11/12` rules archived in [`archive_v1/00a_STANDING_RULES_v1.md`](../archive_v1/00a_STANDING_RULES_v1.md)).

---

## 1. General Case Only & Anti-Overfitting (`Core Rule 3`)

1. **General case only:** No code, prompt text, threshold, or limit may be written or tuned so that a specific worked example in `docs/specs/**` or a specific QA/wet-lab test message passes. A failure is valid data; fixes must state and implement the general linguistic or mathematical rule.
2. **Nothing specific to a test or example** (names, filenames, topics, dates, example typos, or phrases) may appear in `ModelCircuits/eng0..3/`, `ModelCircuits/core/`, or `prompts.json`.
3. **Zero example sentences or `<...>` masked templates in `prompts.json` (`00b_PROMPT_AND_QA_GOVERNANCE.md`):**
   - Every prompt in `ModelCircuits/core/circuit/prompts.json` must state **only general linguistic, grammatical, or structural rules**.
   - Never include quoted example clauses, `<target>`/`<verb>`/`<N>` angle-bracket sentence templates, or stylistic micromanagement (`"using 'I am' rather than 'I'm'"`). Only closed-class function words (conjunctions, discourse adverbs, negation/modal words, standard abbreviations) may be named.
   - Every `prompts.json` change bumps the `"version"` label (`p-regiment-N`) and is owned exclusively by `T-Division` (`Bethe`).
4. **Blind-QA isolation:**
   - Per-phase engineers (`eng0`–`eng3`) never read `docs/specs/qa/**`, `tests/**`, or `wet_lab/**`.
   - Tickets opened for `eng0`–`eng3` must describe only the general linguistic/structural failure category and invariant, never quoting sentences from `docs/specs/qa/**` or `wet_lab/**`.

---

## 2. Zero Regexes, Zero Heuristics & Fail-Closed Invariants in `phase0`–`phase3`

5. **Zero regular expressions (`import re`) in `phase0/`–`phase3/` (`SI-1` / `SI-2`):**
   - Regular expressions (`import re`, `from re import`, `re.compile`, `re.match`, `re.search`, `re.findall`, `re.sub`, `re.finditer`, `compile_infix_regex`, `compile_prefix_regex`) are **strictly forbidden** across `ModelCircuits/eng0..3/` and `ModelCircuits/core/circuit/phase0..3/`.
   - All text normalization and segmentation use narrow LLM calls, `spaCy` (`en_core_web_sm`), and simple pure-Python character/string checks on standard ASCII text (no Unicode dot substitutions `U+2024` / `U+00B7`, and no `.replace("(),", "/,")` BPE patches).
6. **Zero open-class English word lists or undocumented Python pre-filters:**
   - Never add open-class English noun/verb lists or undocumented Python pre-filters/prompt hints (`unanchored_presuppositions`, `_local_lookback_for_mcu`, `_find_unanchored_presuppositions`) to pre-solve semantic tasks for a model or force an invariant check to pass. Only closed-class function words explicitly specified in the governing spec may appear in deterministic code.
7. **No silent fallbacks (`Core Rule 3`):**
   - No silent retries, no substitute model/provider, no default standing in for a missing value, and no `"do something else"` recovery path.
   - Every required reply field, finish-reason allowlist check, `completion_tokens >= max_tokens` cut-off check, and phase invariant check (`Phase0InvariantError`–`Phase3InvariantError`, `ContextOverflow`, `BackendError`) fails closed with a structured error.
   - **The one allowed retry:** Logged transient transport retry inside `http_backend.py` (1 retry after `retry_delay_s` on connection `OSError` before status line for Ollama/gateway; standard exponential backoff up to `jev_max_retries = 3` on `JevGateway` for connection `OSError` or HTTP `429/502/503/504`), recorded in `attempt_log` and `raw_replies`.

---

## 3. File Size (`<= 500` Lines) & Module Ownership (`Core Rule 4`)

8. **Specs define behavior and interfaces, never file layout:**
   - Specs in `ModelCircuits/docs/specs/**` define **what** a phase does—its stages, data contracts/schemas, public entrypoints, and invariants—**not** which internal `.py` files or private helper functions must exist.
9. **One logical component or stage per file (`<= 500` lines):**
   - Each implementing engineer (`eng0`–`eng3`) owns the module layout in `ModelCircuits/engN/` and must keep every `.py` file `<= 500` lines by separating stages, schemas, and helpers into focused modules and re-exporting public entrypoints in `__init__.py`.

---

## 4. The `Eng N ↔ Bethe ↔ Bobo` Build, Tag & Promotion Pipeline (`Core Rule 2`)

10. **Strict isolation & command chain:**
    - `eng0` (Phase 0, `Spec 15`), `eng1` (Phase 1, `Spec 16`), `eng2` (Phase 2, `Spec 17`), and `eng3` (Phase 3, `Spec 18`) are completely isolated from each other, `QA Bobo`, and `Mike`. Each engineer reads only `ModelCircuits/engN/**`, `00a_STANDING_RULES.md`, `docs/specs/phaseN/**`, and shared foundation files in `ModelCircuits/core/` (`types.py`, `gateway.py`, `nlp.py`, `prompts.py`, `prompts.json`, `regiment.py`, `onnx_backend.py`, `http_backend.py`, `models/**` — never `core/circuit/phase0..3/`).
    - Engineers communicate **only** with `Bethe` (`--for bethe`). `QA Bobo` and Engineering never communicate directly.
    - **`Bethe` never edits `.py` code in `ModelCircuits/core/**` or runs tests directly.**
11. **Mandatory spec-flaw escalation (`Eng N / Bobo -> Bethe`):**
    - If a spec or requirement cannot be satisfied cleanly without heuristics, regex workarounds, or `prompts.json` changes, the engineer **must stop and escalate to `Bethe`** (`--for bethe`) rather than patching code to force a pass.
    - If a QA check fails because of a flaw or ambiguity in a spec, prompt, or QA fixture, `Bobo` escalates via `--kind bug --for bethe` (never prescribing code workarounds).
12. **Step-by-step release pipeline:**
    - **Step 1 — Spec Tag (`Bethe -> Eng N`):** `Bethe` updates the spec in `ModelCircuits/docs/specs/phaseN/` (and `prompts.json` if needed), runs `scripts/red_queue.py now docs`, tags `<spec-tag>` via `scripts/build_tag.py`, and wakes `engN` (`--for engN --priority P0` citing `<spec-tag>`).
    - **Step 2 — Engineer Build & Unit QA (`Eng N -> Bethe -> Bobo`):** `engN` implements in `ModelCircuits/engN/`, commits/pushes `git -C ModelCircuits/engN`, runs `scripts/red_queue.py now engN`, tags `<build-tag>`, and closes the ticket to wake `Bethe`. `Bethe` audits the diff and opens a `P0` ticket for `bobo` citing `<build-tag>` to run unit tests against the extracted tag (`scripts/build_tag.py extract <build-tag> /tmp/qa-<build-tag>`).
    - **Step 3 — Promotion to `/core` & Integration / Wet-Lab QA (`Bobo -> Bethe -> /core -> Bobo / Mike`):** Once `Bobo` reports unit tests pass on `<build-tag>`, `Bethe` copies the verified `.py` files from `ModelCircuits/engN/` into `ModelCircuits/core/circuit/phaseN/`, commits/pushes `ModelCircuits/core`, runs `scripts/red_queue.py now core`, tags `<core-tag>`, and wakes `bobo` (integration QA) or `mike` (live user-message testing on `qwen3.5:9b`) citing `<core-tag>`.
