# Spec 00c: Human Psychological Pragmatics & AI Language Specification

**Author:** Bethe (T-Division) · **Date:** 2026-11-01 · **Status:** Active Reference & Standing Design Specification  
**Parent Plan:** [`docs/REGIMENTATION_MASTER_PLAN.md`](../REGIMENTATION_MASTER_PLAN.md) · **Companion Rules:** [`00a_STANDING_RULES.md`](./00a_STANDING_RULES.md), [`00b_PROMPT_AND_QA_GOVERNANCE.md`](./00b_PROMPT_AND_QA_GOVERNANCE.md)

*(Written in canonical **AI Language** — noise-free, monotonic, cohesively grouped, decontextualized, and explicitly role-typed into `[CONTEXT]`, `[RULE]`, and `[ACTION]`.)*

---

## 1. Core Premises & Target Definition (`[CONTEXT]`)

- **`[CONTEXT 1.1 — The Pragmatic Mismatch]`** Human conversational language evolved for cooperative social interaction between humans, where speakers routinely disguise rules, goals, proposals, and critiques behind feelings, politeness rituals, past anecdotes, hedges, and rhetorical questions. AI models process surface tokens literally or mirror social tone sycophantically, causing them to misinterpret indirect human intent.
- **`[CONTEXT 1.2 — Purpose of the Translator]`** Phases 0–3 of the Regimentation Loop translate unstructured **Human Language** into canonical **AI Language** *(formally: Canonical Intent Language / CIL)* so that any downstream consumer—whether an LLM, a small local model, or the Phase 4+ Circuit Node Compiler (and even human-to-human pragmatic decoding)—receives explicit, unambiguous intent without losing psychological subtext.
- **`[CONTEXT 1.3 — The 5 Defining Properties of AI Language]`** A document in **AI Language** satisfies five strict properties:
  1. **Noise-Free (`SHELVE` removed):** Zero off-topic anecdotes, pure cathartic venting, greetings, pre-announcements, pause idioms, insults, or persuasive pep-talk.
  2. **Monotonic (Belief-Revised):** All intra-message self-repairs, retractions, and self-answered questions are already resolved; superseded drafts do not appear in the output.
  3. **Cohesively Grouped:** Every condition, scoped rule, and target context is bundled directly with the item it governs.
  4. **Fully Decontextualized (Self-Contained):** Every pronoun (*"it"*), demonstrative (*"that file"*, *"this framing"*), ellipsis (*"if so"*, *"if it does"*), speaker reference (*"me"* $\rightarrow$ *"the user"*), and context-dependent colloquialism (*"screw us up"*) is replaced with its explicit referent.
  5. **Explicitly Role-Typed & Pragmatically Decoded (`[CONTEXT]`, `[RULE]`, `[ACTION]`, `[QUESTION]`, plus inline `[UNCLEAR: <question>]` spans in Phase 3):** Every statement carries its explicit computational role (from Phase 1's 7-role sieve: `SHELVE`, `ACTION`, `RULE`, `MODIFIER`, `CONTEXT`, `QUESTION`, `COMMIT`), with indirect human psychological signals decoded into explicit machine-readable form, and with any genuinely unresolvable reference annotated inline in Phase 3 as `[UNCLEAR: <clarification question>]`.

---

## 2. The 6 Human Psychological Patterns & Their Translation Rules (`[CONTEXT]` & `[RULE]`)

### Pattern 1: Politeness Mitigation vs. Epistemic Hedging (Brown & Levinson 1987; Biber 1989)
- **`[CONTEXT 2.1a — Politeness Mitigation on Firm Directives]`** Humans automatically apply negative-politeness softeners (*"could you maybe..."*, *"when you get a chance"*, *"if it's not too much trouble"*, *"please"*) to firm commands to avoid sounding domineering (the *Computers Are Social Actors* reflex). The speaker's commitment to executing the task is 100%.
- **`[RULE 2.1a]`** When hedging or politeness markers modify a concrete task request, Phase 1 must classify the clause as **`ACTION`**, and Phase 3 must strip the politeness mitigation and emit a direct imperative `[ACTION]`.
- **`[CONTEXT 2.1b — Epistemic Hedging on Proposals & Hypotheses]`** When a human hedges a design idea, architectural framing, or hypothesis (*"I kind of feel like maybe..."*, *"should probably..."*, *"I think..."*, *"my gut is..."*), the hedge is **not** social filler—it is an epistemic signal of tentativeness (`commitment < 100%`) intended to elicit critique or a better alternative from the listener.
- **`[RULE 2.1b]`** Phase 0 must never strip epistemic hedges (`maybe`, `probably`, `I think`, `I feel like`); Phase 1 must classify hedged theses/proposals as **`CONTEXT`** (never `SHELVE`); and Phase 3 must **never** flatten a hedged proposal into a settled fact or blind command. Instead, Phase 3 must translate the hedge into explicit AI Language:
  > `[CONTEXT] The user tentatively proposes that <Proposition>, and invites critique or a better alternative if one exists.`

---

### Pattern 2: Cathartic Venting vs. Aversive Implicature (Grice 1975; Drew & Holt 1988)
- **`[CONTEXT 2.2a — Pure Cathartic Venting]`** Humans frequently express frustration about external facts outside the assistant's control (coworkers' habits, recurring Friday build failures, office facilities). These clauses carry zero operative instruction or constraint for the assistant, though they may introduce entity names (such as filenames) referenced later.
- **`[RULE 2.2a]`** Phase 1 must classify pure cathartic venting and its causal/temporal explanations (`because...`, `when...`) as **`SHELVE`**, retaining them in `lookback_buffer` strictly for downstream noun-phrase lookback.
- **`[CONTEXT 2.2b — Aversive Implicature (Fear / Past-Disaster Warnings as Rules)]`** Humans often state a feared outcome, personal stakes, or a past failure (*"I don't want to screw us up"*, *"The client will freak out if internal notes are included"*, *"Last time we ran that script, it wiped the production cache"*) without using explicit negative keywords (`do not`, `must not`, `never`). Psychologically, stating an undesired consequence is how humans issue a high-priority guardrail.
- **`[RULE 2.2b]`** If a clause states an undesired consequence, risk, or past failure of an action, target, or resource under discussion in the message, Phase 1 must classify it as **`RULE`** (not `SHELVE`), and Phase 3 must translate the feared consequence into an explicit negative guardrail:
  > `[RULE] <Subject/Action> must not <Undesired Outcome>.`
- **`[RULE 2.2c — Precedence Between Aversive Implicature (`RULE`) and Venting / Pep-Talk (`SHELVE`)]`** A past-failure or undesired-consequence clause is **`RULE`** iff it describes or constrains an operation, target, or resource under discussion in the message (even when introduced by a temporal or causal subordinator such as `when`, `before`, `because`, or `last time`). Pure workplace venting about people or schedules (`[RULE 2.2a]`) and general motivational truisms (`[RULE 2.3a]`) that name no operation, target, or resource guardrail in the message are **`SHELVE`**.

---

### Pattern 3: Persuasive Pep-Talk vs. Teleological Intent ("Commander's Intent" — Bratman 1987; Klein 1998)
- **`[CONTEXT 2.3a — Persuasive / Moralizing Pep-Talk]`** Policy and SOP documents written for humans often include motivational truisms or philosophical rationale (*"Failing tests are valid data that expose real system problems"*, *"Clean code is a sign of craftsmanship"*) to persuade reluctant humans to obey a rule that is already explicitly stated alongside it.
- **`[RULE 2.3a]`** When a declarative clause in a policy document is purely motivational or philosophical cheerleading for an adjacent rule and defines no scope, parameter, or rule of its own, Phase 1 must classify it as **`SHELVE`**.
- **`[CONTEXT 2.3b — Teleological Goal / Commander's Intent]`** When a user explains the operational goal or diagnostic purpose behind a request (*"I'm trying to figure out why memory spiked after Tuesday's deploy"*, *"getting the user message into AI language gives us something we can work with"*), that statement defines the **success criterion and interpretive frame** for the task.
- **`[RULE 2.3b]`** Phase 1 must classify statements of operational purpose, diagnostic goal, or Commander's Intent as **`CONTEXT`** (never `SHELVE`), and Phase 3 must preserve them in `[CONTEXT]` so downstream models know *why* the task is being performed and what to prioritize.

---

### Pattern 4: Externalized Working Memory ("Thinking Out Loud & Self-Answering" — Vygotsky; Levelt 1983)
- **`[CONTEXT 2.4 — Self-Dialogue & In-Turn Resolution]`** Humans frequently use messages as external working memory: they pose a question or dilemma out loud (*"How do we translate this since we are not mind readers?"*), weigh alternatives (*"Either in our parser or by the model at a later stage."*), and state their conclusion inside the same turn (*"Our parser seems like the more appropriate place to do it."*).
- **`[RULE 2.4]`** In Phase 1, the initial question is tagged `QUESTION` and the subsequent reasoning/conclusion clauses are tagged `CONTEXT`. In **Phase 2 (Cohesive Attraction & Belief Revision)**, if a subsequent `CONTEXT` clause in the same turn answers or resolves the user's own earlier `QUESTION`, Phase 2 links them and marks the question as **self-answered** so Phase 3 emits only the consolidated premise/proposal (`[CONTEXT]`) rather than asking or answering a question the user already settled.

---

### Pattern 5: Indirect Directives, Proposals, & Rules Disguised as Questions (Searle 1975; Heritage 2002)
- **`[CONTEXT 2.5 — Four Distinct Psychological Acts Behind `?`]`** Surface interrogative syntax (`?`) encodes four distinct pragmatic acts in human speech:
  1. **Polite Pre-Announcement (`"Could you do two things for me?"`):** Announces upcoming requests without naming the action verb.
  2. **Genuine Exploratory Question (`"What happens if status.json is empty?"`, `"Does this framing make sense?"`):** Requests information, evaluation, or analysis from the assistant.
  3. **Indirect Action / Proposal Question (`"Why don't we split Stage 0C into two files?"`, `"Could you run the linter on src/parser.py?"`):** Proposes or requests an action using interrogative politeness.
  4. **Rhetorical Complaint Question (`"Why on earth are you printing 10 pages of logs?!"`):** Expresses dissatisfaction with a behavior to imply a constraint.
- **`[RULE 2.5]`** Phase 1 must classify interrogatives by their **illocutionary force**, not by the presence of `?`:
  - Cataphoric pre-announcements without the concrete task verb $\rightarrow$ **`SHELVE`**.
  - Genuine requests for information, verification, or architectural evaluation $\rightarrow$ **`QUESTION`**.
  - Polite action requests (*"Could you run..."*) $\rightarrow$ **`ACTION`**; indirect design suggestions (*"Why don't we..."*) $\rightarrow$ **`ACTION`** or **`CONTEXT` (Tentative Proposal)**.
  - Rhetorical complaint questions that bound output or forbid behavior $\rightarrow$ **`RULE`** (or **`SHELVE`** if pure insult with no extractable bound).

---

### Pattern 6: Contrastive Foil Framing & Humor vs. Substantive Realization (Clark 1996; Horn 1989)
- **`[CONTEXT 2.6a — Contrastive Definition]`** Humans frequently sharpen an abstract goal or target set by contrasting it against an undesired foil (*"Something concrete compared to 'get this human garbage in a form that can create nodes'"*, *"Check the timeout errors, not the auth warnings"*).
- **`[RULE 2.6a]`** When a clause defines a target or concept by contrasting it with a rejected alternative:
  - If the contrast defines a conceptual thesis or target scope (*"compared to..."*, *"rather than..."*), keep it as **`CONTEXT`**.
  - If a separate clause explicitly excludes items from an action (*"not the auth warnings"*, *"Do not include expired contracts"*), classify the exclusion as **`RULE`** so Phase 2 attaches it as a negative filter on the target `ACTION`.
- **`[CONTEXT 2.6b — Substantive Realizations Braided with Playful Humor/Levity]`** Humans often pair a genuine product or domain insight (*"I just realized what we are creating could be used as a tool by autistic people"*) immediately with a playful joke or self-annotated banter (*"Hahahaha.. AI is autistic :). AAI - Autistic Artificial Intelligence (this is a joke)"*).
- **`[RULE 2.6b]`** Phase 1 must separate the two at the clause/sentence boundary: keep the substantive product or architectural realization as **`CONTEXT`**, and classify the playful joke, laughter, or banter as **`SHELVE`**.

---

## 3. Summary Translation Matrix Across Phases 0–3 (`[ACTION]`)

- **`[ACTION 3.1]`** Apply the following mapping across active specifications ([`15_REGIMENTATION_PHASE0.md`](./15_REGIMENTATION_PHASE0.md), [`16_REGIMENTATION_PHASE1.md`](./16_REGIMENTATION_PHASE1.md)) and upcoming Phase 2–3 specifications (`Specs 17–18`, defined in [`REGIMENTATION_MASTER_PLAN.md`](../REGIMENTATION_MASTER_PLAN.md)) when translating Human Language into AI Language:

| Human Psychological Pattern | Surface Example | Phase 0 (Surface Copy-Editor) | Phase 1 (Role Sieve) | Phase 2 (Attraction & Revision) | Phase 3 (Canonical AI Language Output) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1A. Politeness on Firm Task** | *"Could you maybe run the linter when you get a chance?"* | Preserve words; fix typos | **`ACTION`** | Bundle with any modifiers | `[ACTION] Run the linter.` *(politeness stripped)* |
| **1B. Epistemic Hedge on Idea** | *"I kind of feel like maybe this should be Phase 0."* | Preserve hedge words intact | **`CONTEXT`** | Bundle with related questions/actions | `[CONTEXT] The user tentatively proposes making this Phase 0, and invites critique or a better alternative.` |
| **2A. Pure Cathartic Venting** | *"I'm so tired of the build breaking every Friday."* | Fix typos; strip expletive interjections | **`SHELVE`** | Kept in `lookback_buffer` only | *(Omitted from AI Language; literals available for lookback)* |
| **2B. Aversive Implicature (Fear / Past Disaster)** | *"I don't want to screw us up."* / *"The last migration dropped the staging table."* | Preserve intact | **`RULE`** | Attach to governed task or standing rules | `[RULE] Must not break downstream node generation.` / `[RULE] Must not drop the staging table.` |
| **3A. Persuasive Pep-Talk** | *"Failing tests are valid data that expose real problems."* | Preserve intact | **`SHELVE`** | Kept in `lookback_buffer` only | *(Omitted from AI Language; adjacent rule is kept)* |
| **3B. Teleological Goal ("Commander's Intent")** | *"Our objective is to isolate the latency regression from yesterday's release."* | Preserve intact | **`CONTEXT`** | Attach to target diagnostic `ACTION` | `[CONTEXT] The goal of the task is to isolate the latency regression from yesterday's release.` |
| **4. Externalized Working Memory (Self-Answered `?`)** | *"How do we translate this? Either in the parser or later. The parser is better."* | Split clauses cleanly | `QUESTION` + `CONTEXT` | Link conclusion to question; mark question self-resolved | `[CONTEXT] Translation must occur inside the parser rather than in a downstream model.` |
| **5A. Genuine Exploratory `?`** | *"Does this framing make sense?"* | Preserve `?` | **`QUESTION`** | Link `QUESTION` to its `CONTEXT` thesis | `[QUESTION] Is the proposed framing in [CONTEXT 1] accurate?` |
| **5B. Polite Pre-Announcement `?`** | *"Could you help me with two quick tasks?"* | Preserve `?` | **`SHELVE`** | Kept in `lookback_buffer` only | *(Omitted from AI Language; subsequent task clauses carry the actions)* |
| **6. Substantive Realization vs. Playful Humor** | *"This translator could help neurodivergent users. Haha, AAI!"* | Split sentences; strip emoticons | `CONTEXT` + `SHELVE` | Keep realization on Workbench; shelve joke | `[CONTEXT] The translator could serve as a pragmatic decoding aid for neurodivergent users.` *(joke omitted)* |
| **7. Unresolvable Reference (Mind-Reading Boundary)** | *"Audit the usual contracts from last quarter."* | Preserve intact | **`ACTION`** *(or **`CONTEXT`** if declarative)* | Lookback search finds no antecedent | `[ACTION] Audit [UNCLEAR: which contracts are "the usual contracts"?] from last quarter.` |

---

## 4. Two-Level `Jev` Pragmatic Classification Architecture (`Coarse Role` $\rightarrow$ `Spec 00c` `subkind` $\rightarrow$ Phase 2/3 Execution)

- **`[CONTEXT 4.1 — Why Coarse Roles Alone Lose Pragmatic Information Needed by Phases 2 & 3]`**
  If Phase 1 outputs only a coarse `role` (`SHELVE`, `ACTION`, `RULE`, `MODIFIER`, `CONTEXT`, `QUESTION`, `COMMIT`) with `subkind: null` on non-`MODIFIER` roles, downstream Phases 2 and 3 lose the psychological distinction that Phase 1 already had to compute:
  1. **Inside `CONTEXT`:** Phase 3 must perform four distinct operations depending on the `Spec 00c` pattern:
     - `HEDGED_PROPOSAL` (Pattern 1B) $\rightarrow$ emit `[CONTEXT] The user tentatively proposes <X>, and invites critique or a better alternative if one exists.`
     - `TELEOLOGICAL_GOAL` (Pattern 3B) $\rightarrow$ attach in Phase 2 to the diagnostic/operational `ACTION` and emit `[CONTEXT] The goal of the task is <X>.`
     - `SELF_ANSWER` (Pattern 4) $\rightarrow$ link in Phase 2 to the user's own preceding `QUESTION` in the same turn and mark that question self-resolved so the AI does not ask it back.
     - `DOMAIN_FACT_SCOPE` $\rightarrow$ emit as a settled fact, workspace scope, or target search/filter criterion.
  2. **Inside `RULE`:** Phase 2 and Phase 3 must perform four distinct operations depending on the `Spec 00c` pattern:
     - `AVERSIVE_IMPLICATURE` (Pattern 2B) $\rightarrow$ the surface clause is an affirmative past-tense failure story or feared outcome; Phase 3 must **invert its polarity** into a negative guardrail (`[RULE] Must not <Undesired Outcome>.`).
     - `EXPLICIT_PROHIBITION` $\rightarrow$ surface clause is already negative (`do not / must not / never`); preserve negative polarity.
     - `QUANTITY_BOUND` $\rightarrow$ Phase 2 attaches the count/length/duration bound directly to the target `ACTION`.
     - `STANDING_POLICY` $\rightarrow$ surface clause uses an imperative verb, but Phase 3 emits it as a standing `[RULE]`, never a one-shot `[ACTION]`.
  3. **Inside `SHELVE`, `ACTION`, `QUESTION`, and `COMMIT`:** Each of these four coarse roles has a single uniform downstream operation in Phase 2 and Phase 3 (`SHELVE` moves to `lookback_buffer`; `ACTION` anchors an episodic action cluster rewritten to a direct `[ACTION]` imperative stripping any politeness mitigation; `QUESTION` emits `[QUESTION]` unless resolved by a `SELF_ANSWER`; `COMMIT` locks $\mathcal{S}_t$), so `subkind` is `None` (`null`) with zero extra `Jev` calls after Step 1A-1.

- **`[CONTEXT 4.2 — Why `Jev` (`JevGateway.choice` & `JevGateway.noul`) Is the Right Engine for Phase 1 & Phase 2]`**
  Once Phase 0 (`Layer A`) has produced clean `ClauseChunk`s, `spaCy` `root_pos`, and sentence context, **Phase 1 and Phase 2 do not generate a single word of English—they are pure classification and linking problems.**
  In `core/circuit/http_backend.py`, [`JevGateway`](../../core/circuit/http_backend.py) provides two single-pass, non-autoregressive primitives over structured `state` (which carries both the target chunk and its surrounding discourse context):
  1. **`jev.choice(state, instructions, criteria: dict[str, str]) -> ({choice, confidence, probabilities}, meta)`:** Evaluates a 1-of-$N$ classification against named `criteria` in a single pass and returns the winning `choice`, `confidence` $\in [0, 1]$, and the full probability distribution `{criterion: P}`.
  2. **`jev.noul(state, instructions) -> (p, meta)`:** Evaluates a binary yes/no question over `state` and returns probability $p \in [0, 1]$ (`p >= THETA`).

- **`[RULE 4.2 — Four-Pass `Jev` Funnel in Phase 1 (`Pass 1A` $\rightarrow$ `Pass 1B` $\rightarrow$ `Pass 1C` $\rightarrow$ `Pass 1D`)]`**
  Instead of forcing a model to solve noise filtering, relational modifiers, speech-act roles, and pragmatic subkinds in a single pass, Phase 1 ([`16_REGIMENTATION_PHASE1.md`](./16_REGIMENTATION_PHASE1.md)) decomposes classification into **four orthogonal `jev.choice` passes**:
  1. **Pass 1A — Noise Sieve ([`16a_PHASE1_PASS1A_NOISE_SIEVE.md`](./16a_PHASE1_PASS1A_NOISE_SIEVE.md), 2 choices):**
     - Separates `"OPERATIVE"` clauses from `"SHELVE"` (`role="SHELVE", subkind=None`).
  2. **Pass 1B — Revision & Condition Sieve ([`16b_PHASE1_PASS1B_MODIFIER_SIEVE.md`](./16b_PHASE1_PASS1B_MODIFIER_SIEVE.md), 4 choices on `operative_ids`):**
     - Separates `"STANDALONE"` clauses from dependent modifiers `"CONDITION"`, `"SELF_REPAIR"`, and `"RETRACTION"` (`role="MODIFIER", subkind=choice`).
  3. **Pass 1C — Standalone Speech-Act Role ([`16c_PHASE1_PASS1C_SPEECH_ACT_ROLE.md`](./16c_PHASE1_PASS1C_SPEECH_ACT_ROLE.md), 5 choices on `standalone_ids`):**
     - Selects `role` $\in \{\text{\texttt{"ACTION"}}, \text{\texttt{"RULE"}}, \text{\texttt{"CONTEXT"}}, \text{\texttt{"QUESTION"}}, \text{\texttt{"COMMIT"}}\}$. Chunks with `role in ("ACTION", "QUESTION", "COMMIT")` receive `subkind=None` directly.
  4. **Pass 1D — `Spec 00c` Pragmatic Subkinds ([`16d_PHASE1_PASS1D_PRAGMATIC_SUBKINDS.md`](./16d_PHASE1_PASS1D_PRAGMATIC_SUBKINDS.md), 4 choices on `"RULE"` and `"CONTEXT"`):**
     - If `role == "RULE"` $\rightarrow$ `jev.choice` over **4 criteria**: `{"EXPLICIT_PROHIBITION", "AVERSIVE_IMPLICATURE", "QUANTITY_BOUND", "STANDING_POLICY"}`.
     - If `role == "CONTEXT"` $\rightarrow$ `jev.choice` over **4 criteria**: `{"DOMAIN_FACT_SCOPE", "HEDGED_PROPOSAL", "TELEOLOGICAL_GOAL", "SELF_ANSWER"}`.

| Coarse `role` | `Spec 00c` `subkind` | Decided In Pass | Phase 2 Operation (`Jev` + `Code`) | Phase 3 Canonical AI Language Operation (`gen` + `Code`) |
| :--- | :--- | :--- | :--- | :--- |
| **`SHELVE`** | `null` | **Pass 1A** | Move to `lookback_buffer` + `lookback_literals` | Omitted from AI Language; available for lazy noun-phrase lookback |
| **`MODIFIER`** | `CONDITION` | **Pass 1B** | Attach via `jev.choice` to governed `ACTION`, `RULE`, `CONTEXT`, or `QUESTION` | Front `[If/Unless/When <Condition>,]` on the governed sentence |
| **`MODIFIER`** | `SELF_REPAIR` | **Pass 1B** | Attach via `jev.choice` to earlier `ACTION`, `RULE`, or `CONTEXT`; overwrite parameter | Emit only the revised parameter value (`AGM Belief Revision`) |
| **`MODIFIER`** | `RETRACTION` | **Pass 1B** | Attach via `jev.choice` to earlier `ACTION`, `RULE`, or `CONTEXT`; cancel item | Omit the retracted cluster from $\mathcal{S}_t$ |
| **`ACTION`** | `null` | **Pass 1C** | Anchor of an episodic action cluster | Strip any politeness mitigation; emit `[ACTION] <Imperative>.` |
| **`QUESTION`** | `null` | **Pass 1C** | Keep open unless resolved by a `SELF_ANSWER` (`jev.noul`) | Emit `[QUESTION] <Self-Contained Question>?` |
| **`COMMIT`** | `null` | **Pass 1C** | Lock $\mathcal{S}_t$ for execution | Advance locked $\mathcal{S}_t$ to Phase 4+ |
| **`RULE`** | `EXPLICIT_PROHIBITION` | **Pass 1C (role) $\rightarrow$ Pass 1D (subkind)** | Attach via `jev.choice` to target `ACTION` or keep as standing rule | Emit `[RULE] <Subject> must not <Action>.` |
| **`RULE`** | `AVERSIVE_IMPLICATURE` | **Pass 1C (role) $\rightarrow$ Pass 1D (subkind)** | Attach via `jev.choice` to governed `ACTION` or standing rules | **Invert feared/past failure** into `[RULE] Must not <Undesired Outcome>.` |
| **`RULE`** | `QUANTITY_BOUND` | **Pass 1C (role) $\rightarrow$ Pass 1D (subkind)** | Attach via `jev.choice` to target `ACTION` | Emit count/length/duration cap on the governed `[ACTION]` / `[RULE]` |
| **`RULE`** | `STANDING_POLICY` | **Pass 1C (role) $\rightarrow$ Pass 1D (subkind)** | Anchor of a standing policy cluster (receives `CONDITION` modifiers) | Emit `[RULE] [If/When <Condition>,] <Subject> must <Policy>.` |
| **`CONTEXT`** | `DOMAIN_FACT_SCOPE` | **Pass 1C (role) $\rightarrow$ Pass 1D (subkind)** | Attach to relevant cluster or keep in standing `[CONTEXT]` | Emit `[CONTEXT] <Self-Contained Declarative Fact / Scope / Filter>.` |
| **`CONTEXT`** | `HEDGED_PROPOSAL` | **Pass 1C (role) $\rightarrow$ Pass 1D (subkind)** | Bundle with related `QUESTION`s / `ACTION`s | Emit `[CONTEXT] The user tentatively proposes <X>, and invites critique or a better alternative.` |
| **`CONTEXT`** | `TELEOLOGICAL_GOAL` | **Pass 1C (role) $\rightarrow$ Pass 1D (subkind)** | Attach via `jev.choice` to diagnostic/operational `ACTION` | Emit `[CONTEXT] The goal of the task is <X>.` |
| **`CONTEXT`** | `SELF_ANSWER` | **Pass 1C (role) $\rightarrow$ Pass 1D (subkind)** | Verify via `jev.noul` against preceding `QUESTION`; mark question self-resolved | Consolidate into `[CONTEXT] <Resolved Conclusion>.` (question omitted) |

- **`[RULE 4.3 — Clean 3-Layer Division of Labor Across Phases 0–3]`**
  1. **Layer A — Phase 0 (`gen` Small LLM + `spaCy` + Deterministic Code):** Restores surface text (`Stage 0A`), purifies non-sentence dots and extracts literals/quantities (`Stage 0B`), and splits sentences into syntactic `ClauseChunk`s with `spaCy` `root_pos` and cohesion locks (`Stage 0C`).
  2. **Layer B — Phase 1 & Phase 2 (`Jev` Single-Pass Classifier/Judge + Deterministic Code, Zero Text Generation):**
     - **Phase 1 (`Layer B1`):** Four-pass `jev.choice` funnel (`Pass 1A Noise Sieve` $\rightarrow$ `Pass 1B Modifier Sieve` $\rightarrow$ `Pass 1C Speech-Act Role` $\rightarrow$ `Pass 1D Pragmatic Subkind`) + deterministic Workbench/Lookback partitioning.
     - **Phase 2 (`Layer B2`):** `jev.choice` and `jev.noul` link modifiers, scoped rules, teleological goals, and self-answers to their target anchors in the message (or in $\mathcal{S}_{t-1}$), and deterministic code applies belief revision.
  3. **Layer C — Phase 3 (`gen` Small LLM + Deterministic Invariant Guards):** Rewrites each consolidated cluster into canonical **AI Language** (`[CONTEXT]`, `[RULE]`, `[ACTION]`, `[QUESTION]`, plus inline `[UNCLEAR: ...]` spans) using the explicit `Spec 00c` `subkind` transformation rules.
