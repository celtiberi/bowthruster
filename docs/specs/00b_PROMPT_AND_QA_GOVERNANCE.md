# Spec 00b: Generality of Prompts, Specs & Blind-QA Anti-Overfitting Protocol

**Author:** Chief Theorist Hans Bethe (`T-Division`)  
**Authority:** Director Order (2026-11-01) — Standing Rule 48 (`00a_STANDING_RULES.md`)  
**Applies to:** `T-Division` (`Bethe`), `G-Division` (`Fermi`), `Q-Division` (`Bobo`), `W-Division` (`Mike`)

---

## 1. Why This Protocol Exists (Root-Cause Postmortem of Phase 0 & Phase 1 Drift)

During the initial build of Regimentation Phase 0 (`Spec 15`) and the draft of Phase 1 (`Spec 16`), test-specific phrases from the QA suite (`M01`–`M13`) leaked into the specifications and into `ModelCircuits/core/circuit/prompts.json`. That drift happened through a four-step feedback loop:

1. **Over-rigid character-exact QA grading on open-ended prose rewriting:** In Stage 0A (`regiment.phase0_copy_edit`), the QA oracle (`phase0_qa_messages.md`) demanded 100% character-for-character equality against a single hand-written reference string on a multi-paragraph slang and code-switched passage (`M13`). When Claude Haiku produced valid English that differed only stylistically (contracted vs. uncontracted copula, or splitting a non-operative venting sentence at an inline expletive), the test reported a failure.
2. **QA bug tickets and spec updates quoted the failing test constructions:** To explain the mismatch to `G-Division`, bug tickets and spec revisions quoted exact surface constructions from the QA suite (inline foreign clauses, phonetic malapropisms, slang retraction idioms, parenthetical evaluative asides, and dialectal double-negatives).
3. **Spec worked examples (`§4`) were written as vocabulary-swapped clones of `M01`–`M12`:** Even where nouns and numbers were changed, the sentence skeletons in `§4` mirrored the held-out QA set 1-for-1.
4. **Engineering tuned `prompts.json` by inserting those examples and templates into the prompt text:** To make the character-exact assertion pass, `G-Division` added specific phrases and `<noun>`-masked templates directly into `prompts.json`.

**Result:** A prompt that quotes, paraphrases, or templates test sentences is a lookup table in prose form (Standing Rule 4 & 36 / Archetype 14). It inflates test scores on the QA suite while teaching the model nothing general for unseen inputs.

---

## 2. The Five Mandatory Rules

### Rule 1 — Prompts in `prompts.json` Must Be 100% General (Zero Example Sentences, Zero Masked Templates, Zero Worked Parentheticals)
Every prompt in `ModelCircuits/core/circuit/prompts.json` must be written in terms of **general linguistic, grammatical, or pragmatic rules** that apply to any English input.

- **Strictly Forbidden in `prompts.json`:**
  1. **No quoted example sentences, clauses, or parenthetical domain illustrations:** Never include full or partial example sentences, clauses, idioms, domain phrases, or parenthetical worked illustrations (`"such as ..."`, `"e.g., ..."`) that echo the vocabulary or scenarios of any spec illustration (`§4`) or QA message (`docs/specs/qa/**`).
  2. **No slot-masked templates of test sentences:** Replacing nouns or numbers with `<N>`, `<M>`, `<file>`, or `<component>` inside a sentence skeleton from a spec or test is still test-fitting and is forbidden.
  3. **No stylistic micromanagement aimed at matching a single reference string:** Never instruct a model to prefer one valid English contraction or stylistic choice over another just to force character-for-character agreement with a single reference string.
- **What IS Allowed in `prompts.json`:**
  1. **General grammatical, syntactic, and pragmatic definitions** of the task, roles, precedence rules, and boundary criteria.
  2. **Closed-class grammatical function words and standard orthographic categories:** Naming closed-class English conjunctions (`if, unless, when, because, though, before, after, and, but, or, so`), closed-class discourse markers (`First, Second, Otherwise, Anyway, Also`), closed-class negation/modal words (`not, n't, never, no, without, unless, except, instead, forget, must, only`), and standard orthographic abbreviations (`Dr., Mr., Ms., Prof., i.e., e.g., etc., vs.`).

---

### Rule 2 — Specifications (`docs/specs/*.md`) Must Be General and Non-Isomorphic to `docs/specs/qa/**`
- **Normative Sections (`§§1–3`):** Must state rules in terms of general syntax, semantics, pragmatics, and deterministic invariants—never by enumerating sentences from `docs/specs/qa/**`.
- **Illustrative Contract Section (`§4`):** Exists only to show the **data contract and JSON schema shape** on 2–3 short, minimal (1–2 sentence) examples. Worked examples in `§4` must **never** be structural 1-to-1 clones (same clause sequence with swapped nouns/numbers) of the QA suite in `docs/specs/qa/**`.

---

### Rule 3 — Blind-QA Tickets Must Describe Failure Categories, Never QA Sentences
When `Q-Division` (`Bobo`) or `T-Division` (`Bethe`) opens or comments on a ticket readable by `G-Division` (`Fermi`), `C-Division` (`Raymand`), or `W-Division` (`Mike`):
- **Never quote, paraphrase, or template a sentence or phrase from `docs/specs/qa/**`.**
- Describe only:
  1. **The invariant or general grammatical/pragmatic rule that failed** (e.g., *"Stage 0A simplified a double negative into a single negative, reversing polarity"*, or *"Stage 0C failed to split before an un-commaed finite `because <subj> <verb>` clause in the first half of a compound sentence"*).
  2. **Which stage or function failed** and the error code or invariant name.
- Detailed per-message outputs (`M01`–`M13`) belong exclusively in `docs/specs/qa/**` or QA gate audit files that Engineering only sees at the structural level.

---

### Rule 4 — How QA Grades LLM Stages: Strict Invariants + Structural/Semantic Equivalence (Never Arbitrary Wording)
To prevent QA tests from forcing Engineering to overfit prompts to arbitrary phrasing choices:
1. **Deterministic & Invariant Checks Are 100% Strict:**
   - Every mechanical invariant (`check_phase0_invariants`, `check_phase1_invariants`, literal survival, negation/retraction token conservation, dot purification `·`/`․`, prosody/emoticon removal, per-sentence partition `" ".join(clauses) == sentence`, chunk bijection, workbench/lookback partition) must pass **100% strictly with zero tolerance**.
2. **Open-Ended Prose Copy-Editing (`Stage 0A` on Slang/Noisy Text) Is Graded by Invariants + Targeted Repair Checks, Not Arbitrary Character Identity:**
   - On **already-clean standard ASCII inputs** (containing no spelling errors, slang malapropisms, bare comparator symbols, or inline foreign phrases), Stage 0A must preserve the clean text (modulo whitespace normalization).
   - On **heavily corrupted/slang/multi-paragraph inputs**, there are multiple valid English copy-edits of non-operative slang/venting sentences (contracted vs. uncontracted forms, comma vs. period after removing an inline expletive, etc.). Grading Stage 0A on such inputs must therefore verify:
     a. All 4 Phase 0 mechanical invariants pass (`0` invariant errors).
     b. Every specific spelling typo, phonetic malapropism, inline foreign phrase, bare comparator symbol (`>`/`<`), unpunctuated contraction, and missing sentence boundary in the corrupted input has been repaired into clean standard ASCII English.
     c. Every operative directive, condition, constraint, self-repair, retraction, and question is preserved with its exact technical literals and polarity intact.
3. **Clause Segmentation (`Stage 0C`) on Multi-Paragraph Inputs Is Graded by Partition Invariant + Operative Clause Boundaries:**
   - Every sentence must pass the **Per-Sentence Partition Guard** (`" ".join(clauses) == sentence`, preserving every comma and word).
   - Every **operative clause** (directives, conditions, constraints, self-repairs, retractions, questions) must be cleanly separated at its syntactic boundary without merging distinct directives or splitting inside a coordinated noun phrase.

---

### Rule 5 — Prompt Complexity & Instruction-Count Caps (Automated QA Guard Against Mega-Prompts)
A core tenet of the architecture is that LLMs fail when given too many rules or instructions in a single prompt. Every prompt in `ModelCircuits/core/circuit/prompts/*.json` must perform **one focused cognitive job** and obey strict mechanical complexity caps enforced by automated QA (`ModelCircuits/tests/core/circuit/test_prompts.py`):
1. **Phase 0 Stage 0A Micro-Pass Prompts (`regiment.pass0a1_disfluency_repair`, `regiment.pass0a2_orthography_idiom`, `regiment.pass0a3_sentence_punctuation`):**
   - **Word count:** `len(text.split()) <= 200` words per pass prompt.
   - **Instruction count:** At most `4` top-level bulleted/numbered instructions (`- ` or `1.`) and at most `4` inline numbered rules (`(1)`, `(2)`, ...).
2. **Phase 0 Stage 0C Clause-Split Prompt (`regiment.phase0_clause_split`):**
   - **Word count:** `len(text.split()) <= 350` words.
   - **Instruction count:** At most `5` top-level bulleted/numbered instructions (`- ` or `1.`).
3. **Phase 1 & Phase 2 `Jev` Prompts (`jev.pass1*`, `jev.pass2*`):**
   - **Word count:** `len(text.split()) <= 250` words for `*_instructions` keys and `len(text.split()) <= 200` words for criterion keys (`jev.pass1*.*`, `jev.pass2*.*`).
   - **Instruction count:** At most `6` top-level bulleted/numbered instructions or inline numbered boundary rules (`(1)`..`(6)`).
4. **Global Ceiling Across All Shipped Prompts (`ModelCircuits/core/circuit/prompts/*.json`):**
   - **Word count:** Every prompt in `prompts/*.json` must satisfy `len(text.split()) <= 600` words.
   - **Top-level instruction count:** Every prompt in `prompts/*.json` must have `<= 8` top-level unindented bulleted/numbered instruction lines (`<= 6` for all `phase0.json`, `phase1_passes.json`, `phase2.json`, and `phase3.json` prompts).

---

## 3. Pre-Tag Audit Checklist for Every Regimentation Phase

Before `G-Division` tags any build (`s15-phase0-*`, `s16-phase1-*`, etc.) or `T-Division` approves a prompt change:
1. **Prompt Generality & Complexity Check:** Read the diff in `ModelCircuits/core/circuit/prompts/*.json`. Does it contain *any* quoted example sentence, *any* `<N>`/`<file>` masked sentence template, or *any* vocabulary from `M01`–`M13` or `§4`, or does any prompt exceed the Rule 5 word-count / instruction-count caps? If yes, **reject and decompose or rewrite as a general linguistic rule**.
2. **Python Zero-Word-List Check:** Read the diff in `ModelCircuits/core/circuit/`. Does Python contain any open-ended English semantic word list or phrase regex tailored to test examples? If yes, **reject**.
3. **Spec Generality Check:** Read `ModelCircuits/docs/specs/`. Do `§§1–3` state general rules without leaking `docs/specs/qa/**` strings? If not, **clean the spec first**.
