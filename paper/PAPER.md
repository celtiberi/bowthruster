# Lattice: Guarding a Generator with Single-Pass Rule Neurons & a Contradiction-Gated Constraint Lattice

**Authors:** Lattice Contributors  
**Status:** Working Paper — Phase 0 Hypothesis & Experimental Design

---

## Abstract

When a language model is handed one monolithic prompt containing $N$ tools and $K$ simultaneous domain rules, it fails in three predictable ways: **Attention Dilution** (picking the wrong tool), **Premature Stopping** (running a read/search step and stopping before the action step), and **Action Bluffing / Rule Drops** (claiming an action that never happened, or silently dropping one of the $K$ rules).

**`Lattice`** keeps a generator model but removes the rules from its prompt. The rules move into a circuit of fast, non-autoregressive, **single-pass rule neurons** that watch the generator's output, backed by a deterministic **Regimentation, Contradiction-Gated Whiteboard, Hippocampal AI Memory, and Bounded Table Pipeline (`Specs 15–28`)** that compiles raw multi-turn prose and Markdown pipe tables into a bounded, formally proven object graph ($K \le 5$ rules per scope node, $\le 400$ readout tokens, $\le 35$ lines).

---

## 1. The Problem

1. **Attention Dilution:** With $N \ge 16$ overlapping tool schemas in one prompt, models pick the wrong tool.
2. **Premature Stopping:** In *Search $\rightarrow$ Act* workflows, the model reads results and stops instead of acting.
3. **Action Bluffing & Rule Drops:** The model claims a state change without calling the tool, or drops rules as $K$ grows because all $K$ rules compete for attention in one context window.

---

## 2. The Core Hypothesis

> A generator with a short prompt and a small tool set, wrapped in $K$ isolated single-pass rule neurons and deterministic receipt gates, completes rule-heavy tasks more reliably than the same generator (or a much larger one) given all $N$ tools and $K$ rules in one prompt — at far lower verification latency than LLM-based judges.

### 2.1 Circuit Components

| Component | What It Does |
| :--- | :--- |
| **Generator Node** | Any capable model (small local or cloud). Sees one Floor: $k \le 6$ tools and a short role prompt. No rule text in its prompt. |
| **Router (Gating Neuron)** | Single-pass classifier that picks the Floor for a request, or abstains as out-of-scope. |
| **Receipt Gate** | Deterministic check: any claimed state-mutating action must have a matching tool execution receipt in the trace. Not ML. |
| **Excitatory Neuron (green node)** | Fires when an action workflow stopped after its read step without obtaining the action receipt; pushes the generator to finish. |
| **Inhibitory Rule Neurons (red nodes)** | One single-pass decision per rule $r_k$. Evaluates `(rule_k, draft)` in isolation. Silent below threshold $\theta_k$; fires above it and returns only rule $r_k$ and its `error_span`. |

### 2.2 Closed-Loop Reliability

For an active rule $r_k$: $p_k^{(0)}$ is the probability that the generator satisfies $r_k$ on its first draft, $\alpha_k$ the neuron's catch rate on violations, $\text{fpr}_k$ its false-alarm rate on compliant drafts, $\beta_k$ the generator's fix rate given a 1-rule bounce, $p_{\text{corrupt}}$ the chance that a false bounce damages a good draft, and $B$ the maximum number of bounces:

$$\varepsilon_k(B) = \big(1 - p_k^{(0)}\big)\big(1 - \alpha_k \beta_k\big)^B + p_k^{(0)} \cdot \text{fpr}_k \cdot p_{\text{corrupt}}$$

---

## 3. The Six-Subsystem Lattice Architecture (`lattice/`)

$$\text{raw\_text} \xrightarrow{\text{`lattice.regiment`}} \text{IntakePacket} \xrightarrow{\text{`lattice.the\_executive`}} \text{`lattice.whiteboard`} \xleftrightarrow{\text{`lattice.ai\_memory` + `lattice.the\_table`}} \text{Bounded TableView & WorkPackets}$$

1. **`lattice.regiment` (`phase0`..`phase3`, Specs 15–18):** Stateless linguistic & discourse intake parser with deterministic Markdown pipe-table extraction (`extract_markdown_tables`), cohesion-locked Stage 0A windowing (`bge_small_en_v15_int8`), 4-pass `/v1/systemone` speech-act sieve (`Phase 1`), AGM belief revision (`Phase 2`), and EARS decontextualization (`Phase 3`).
2. **`lattice.whiteboard` (`Stage 22`):** n-Dimensional SBF constraint lattice (`@ COMPONENT`, `> FLOW`, `# SCHEMA`, `$ INTERFACE`, `% LAW`, `* BOUND`), Spin-Glass frustration detector, `LATTICE_PROOF.bend` synthesizer, and `WorkPacket` + `python_skeleton` compiler.
3. **`lattice.the_executive` (`Stage 23`, `26`, `27`):** Preflight trial router, conflict gate, Story-calibrated Executive Voice, AST call-graph verifier (`verify_ast_call_graph_skeleton`), and Git pre-commit/post-commit gate (`evaluate_git_commit_gate`, `sync_post_commit_to_lattice`).
4. **`lattice.ai_memory` (`Stage 24`):** Deterministic cue extractor, directed multi-cue Steiner push Hippocampal index (`recall_concepts`, $F_1 = 0.989$), and Git-synchronized URI resolver (`uri_resolver`).
5. **`lattice.the_table` (`Stage 25`):** Capacity-bounded working-memory surface (`<= 16` slots, `<= 1,200` tokens), Stage 2 conflict-triggered prefrontal foraging (`forage_table_context`), and `LOD_0` Multi-Board Lattice Atlas (`render_lattice_atlas`).
6. **`lattice.graph_core` (`Stage 21`):** Shared SQLite persistence (`SQLiteGraphStore`), nD coordinate slicing, and ACT-R base-level activation physics.
