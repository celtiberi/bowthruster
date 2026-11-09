# Spec 00d: Bend 2 Law-Driven Development (`LAWS.bend` / `PROOF.bend`) & Gradual Migration Plan

* **Document ID:** `00d_BEND2_LAW_DRIVEN_DEVELOPMENT_AND_MIGRATION.md`
* **Status:** Active Architectural Standard & Gradual Migration Roadmap (Approved POC `1d8ace21`)
* **Rollback Reference Tag:** `pre-bend2-poc` (tagged across top-level repo and all `ModelCircuits/*` split repos)
* **Maintained By:** Lattice Contributors
* **Local Toolchain:** `~/.bend/bin/bend` (`Bend 2.0.40`, `BEND_NO_TELEMETRY=1`) · Gate Script: [`scripts/bend_gate.py`](scripts/bend_gate.py)

---

## 1. Executive Summary & Core Thesis

**Bend 2** (`github.com/bendlang/bend` / `bend-lang.com`) is a dependently-typed, affine functional language and proof checker created by Victor Taelin / HigherOrderCO specifically for AI-driven software engineering. Unlike Bend 1 (which was dynamically typed and focused solely on HVM GPU parallelism), **Bend 2 is a single-pass bidirectional proof checker and compiler (C, Metal, CUDA, JS) that verifies universal mathematical laws in ~10–50 milliseconds.**

### Why Bend 2 Matters for Site Y (`ModelCircuits/`)
In a multi-agent engineering organization, the single biggest failure mode is the **"AI Doom Loop" (or "Test-Fitting Trap")**:
1. When specs are written only in English prose (`docs/specs/*.md`) and verified only by finite unit tests (`ModelCircuits/tests/`), coding agents (`eng0`–`eng3`) are tempted to pass failing tests by adding special-case branches, fallback defaults, or magic constants.
2. Catching those workarounds currently requires an LLM Red Elf review (`scripts/red_queue.py`, ~20–40s) plus a multi-division ticket handoff (`engN -> Bethe -> Bobo`).
3. **Bend 2 eliminates test-fitting at the compiler level:**
   - **Bethe (`T-Division`)** states universal invariants in **`LAWS.bend`** (`for x: T ... {lhs == rhs : Type}`).
   - **Engineers (`eng0`–`eng3`)** write the implementation in **`main.bend`** and the inductive mathematical proof in **`PROOF.bend`**, and are **hardware-blocked by [`sandbox_guard.py`](.agents/scripts/sandbox_guard.py) from editing `LAWS.bend`**.
   - Every branch or heuristic hack an engineer adds to `main.bend` creates a proof obligation in `PROOF.bend`. Special-case test-fitting is **mathematically impossible** to compile past `bend PROOF.bend --verdict`.

---

## 2. Empirical Findings from Our Site Y Proof of Concept (`ModelCircuits/eng3/bend_poc/`)

We tested Bend `2.0.40` live in [`ModelCircuits/eng3/bend_poc/`](ModelCircuits/eng3/bend_poc/main.bend) by formalizing and proving a 7-law **Spec 20 Context Graph Polarity, Path Homomorphism & Frustration Kernel**:

| Metric / Question | Empirical Result in Site Y POC |
| :--- | :--- |
| **Can an LLM not pre-trained on Bend 2 write & prove code?** | **Yes, immediately.** The entire language specification (`bend guide`) is 678 lines (<8k tokens) with zero implicit type inference and no opaque tactic DSL. 6 of 7 laws checked on the 1st pass; the 7th checked on the 2nd pass in <15s total. |
| **Checker Speed (`bend PROOF.bend`)** | **~40 milliseconds** for 7 laws (including structural induction over arbitrary-length paths `List<&2, Pol>` and `Nat` decay steps). |
| **Diagnostic Quality on Proof Failure** | Prints exact normalized equations (`expected: {c == CG.inv_pol(CG.inv_pol(c))}` vs `observed: {CG.inv_pol(CG.inv_pol(c)) == c}`) with exact line/column spans. |
| **Mutation Catch Rate (`scripts/bend_gate.py --self-test`)** | **100%.** Mutating `Pos{} -> Neut{}` in `inv_pol` or adding `@unsafe` is blocked in ~40ms before `build_tag.py` can mint a tag. |

---

## 3. Architectural Pattern: Robert Penner’s "Host Primitives Outside, Proven Operators Inside"

A critical insight from early Bend 2 adopters (notably Robert Penner’s work combining TypeScript 64-bit easing curves with Bend 2 operator laws, and the `nohzafk/bend-ldd` Law-Driven Development methodology) resolves how Bend 2 handles floating-point (`F32`/`F64`), NLP models, and I/O:

> *"Put in Bend the part that is **small, pure, decides something, and is costly when wrong**: matching, scheduling, allocation, dependency resolution, a protocol's state machine. Keep in the host what the checker cannot reach or the ecosystem lacks: IO, HTTP, parsing, external libraries... So my core easing functions still live outside Bend, in TypeScript. But in Bend I can prove laws about how I transform easing functions with operators."*

### How This Divides `ModelCircuits/` into Two Clean Layers

```
┌────────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: PYTHON HOST (`ModelCircuits/core/` & `eng0..3/*.py`)              │
│   • Neural / Continuous Primitives: spaCy (`en_core_web_trf`/`lg`),        │
│     `bge_small_en_v15_int8` ONNX embeddings, LLM Gateway (`http_backend`)  │
│   • I/O, JSON serialization, SQLite/Graph persistence, String slicing      │
│   • Quantizes/packages raw observations into discrete typed events (`Ev`)  │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ Pure data boundary (FFI / C / JS / Basis-Point Nat)
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ LAYER 2: BEND 2 PROVEN OPERATOR & STATE-MACHINE CORES (`*.bend`)           │
│   • Algebraic combinators (`inv_pol`, `comp_pol`, interval algebra)        │
│   • Total state machines: `step(State, Ev) -> Out`                         │
│   • Guaranteed by `LAWS.bend` (Bethe) + `PROOF.bend` (`eng0`–`eng3`)       │
│   • Checked in ~40ms by `scripts/bend_gate.py` before `build_tag.py`       │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. High-Value Target Areas Across `ModelCircuits/` (Where We Should Use Bend 2)

Below are the specific subsystems in `ModelCircuits/` ranked by ROI for Bend 2 formal verification:

### Tier 1 — Immediate High-ROI Targets (Pure Discrete Algebra & State Machines)

1. **Context Graph (`Spec 20` — `eng3/context_graph/`):**
   - **Why:** Context Graph frustration detection (`frustration.py`), mitosis (`mitosis.py`), and sign propagation (`propagation_state.py`) are pure algebraic transformations over graph nodes and signed edges.
   - **Laws to Prove (`LAWS.bend`):**
     - *Sign & Polarity Algebra:* Involution, associativity, and path concatenation homomorphism *(already proven in `ModelCircuits/eng3/bend_poc/`)*.
     - *Mitosis Conservation:* Splitting a frustrated node into twin sibling contexts preserves all incident provenance IDs, never creates self-loops, and strictly reduces local frustration count.
     - *Fixed-Point Activation Decay:* Representing activation weights in integer basis points (`0n..10000n` instead of raw floats) lets us prove monotonicity (`decay(t+1, a) <= decay(t, a)`), floor clamping (`>= 0n`), and ceiling clamping (`<= 10000n`).

2. **Phase 3 Deterministic CNL Verifier (`Spec 18` — `eng3/`):**
   - **Why:** The Phase 3 verifier is the judge that accepts or rejects LLM-generated Controlled Natural Language (CNL) and Minimal Clause Units (MCUs). Currently, the verifier is written in Python—meaning a bug in the verifier itself can silently accept invalid logic.
   - **Laws to Prove (`LAWS.bend`):**
     - *Soundness & Completeness of Clause Verification:* `verify(mcu) == True{}` iff every quantifier scope is closed, every variable is bound before use, and polarity is conserved.
     - *Idempotence of Canonicalization:* `canonicalize(canonicalize(expr)) == canonicalize(expr)`.

3. **Phase 2 AGM Belief Revision & QUD Stack Machine (`Spec 17` — `eng2/`):**
   - **Why:** Phase 2 implements AGM belief revision (expansion, contraction, revision) and Question Under Discussion (QUD) stack transitions (`step(QUDState, ClauseEvent) -> QUDState`). AGM belief revision is literally defined in mathematical logic by the **AGM Postulates**!
   - **Laws to Prove (`LAWS.bend`):**
     - *AGM Success Postulate:* Revising belief state `B` with proposition `p` always results in a state where `p` holds.
     - *AGM Vacuity & Inclusion:* If `not(p)` is not in `B`, revision `revise(B, p)` equals simple expansion `expand(B, p)`.
     - *AGM Consistency:* Revising a consistent belief state with a consistent clause never produces a contradiction.
     - *QUD Stack Balance:* Every QUD push/pop transition preserves stack well-formedness.

### Tier 2 — Medium-Term Targets (Interval & Tree Combinators)

4. **Phase 0 Occupied Interval & Span Sieve Algebra (`Spec 15` — `eng0/`):**
   - **Why:** Keep `spaCy` and regex-free character scanning in Python, but move the **`OccupiedIntervals` interval-tree algebra** (merging, non-overlapping invariant, containment checks) into a proven Bend 2 core.
   - **Laws to Prove (`LAWS.bend`):**
     - *Disjointness & Sortedness Invariant:* After any sequence of `insert_span(lo, hi)` operations, intervals are strictly non-overlapping and sorted.
     - *Monotonic Coverage:* Once a character index `i` is marked occupied, no subsequent span insertion can unmark `i`.

5. **Phase 1 Modifier Sieve & Tree Reconstruction (`Spec 16` — `eng1/`):**
   - **Why:** Proving that splitting a compound sentence tree into atomic kernel clauses + modifier attachments and reconstructing it is **lossless** (every token ID from the input appears in at least one output kernel or modifier, and no phantom token IDs are invented).

6. **Phase 4 Entity Linker & Scope Resolver (`Spec 19` — `eng2/phase4/` & `eng3/phase4/`):**
   - **Why:** Proving scope-shadowing laws (inner quantifier scope shadows outer scope of the same variable, and exiting a scope restores the outer binding).

### Tier 3 — Where We Should NOT Use Bend 2 (Keep in Python)
- **`ModelCircuits/core/circuit/http_backend.py` & `gateway.py`**: Network I/O, retries, JSON schema formatting, and API routing.
- **`ModelCircuits/core/circuit/onnx_backend.py` & `phase0/spacy_nlp.py`**: Neural model loading, SHA-256 weight verification, tokenization, and dense 384-D/300-D vector dot products.

---

## 5. Five Core Design Patterns for Bend 2 in Site Y

### Pattern 1: The 3-Layer State Machine Pattern (`State`, `Ev`, `step`)
Whenever a circuit stage has state across clauses or turns (e.g., Phase 2 QUD stack, Phase 4 Scope Resolver, Spec 20 Context Graph), structure it as a total step function:
```python
# main.bend
import Base

type State is Data:
  State{active: Nat, halted: Bool}

type Ev is Data:
  Tick{delta: Nat}
  Halt{}

def step(s: State, e: Ev) -> State:
  match s e:
    case State{a, True{}} _:
      State{a, True{}}
    case State{a, False{}} Halt{}:
      State{a, True{}}
    case State{a, False{}} Tick{d}:
      State{Nat.add(a, d), False{}}
```
And structure `LAWS.bend` in **three layers** (from `bend-ldd`):
1. **Layer 1 (State Invariant `inv`):** `inv(init()) == True{}` and `inv(s) == True{} -> inv(step(s, e)) == True{}`.
2. **Layer 2 (Single-Step Transition Laws):** One law per spec rule (e.g., `law halt_is_absorbing: for a: Nat, for e: Ev: {step(State{a, True{}}, e) == State{a, True{}} : State}`).
3. **Layer 3 (Multi-Step Run Homomorphisms):** Properties over arbitrary event sequences `for es: List<&2, Ev>` folded via `run(es, s)`.

### Pattern 2: Fixed-Point Basis Points (`0n..10000n`) Instead of `F32` for Proven Thresholds
Because `F32` is axiomatic in Bend 2 (IEEE-754 NaN/rounding breaks algebraic associativity), **never use `F32` inside a Bend core that requires proof**.
- Instead, Python converts cosine similarities `sim in [0.0, 1.0]` into integer basis points `bp = round(sim * 10000)` (`Nat` or `U32` in `0..10000`) before crossing into Bend 2.
- Inside Bend 2, threshold comparisons (`bp >= 7000`), weighted averages, and monotonic decay are 100% exact and provable over `Nat`!

### Pattern 3: The "Ford Trick" (Intrinsically Typed ASTs via Equality Witnesses)
To enforce cross-phase AST invariants at the type level (see `demos/proof_typed_eval/main.bend`), embed an equality proof field `{ExpectedTag{} == tag : Tag}` inside AST constructors:
```python
type ClauseKind is Data:
  Kernel{}
  Modifier{}

type Clause<-k: ClauseKind> is Data:
  MkKernel{id: Nat, eq: {Kernel{} == k : ClauseKind}}
  MkMod{id: Nat, target_id: Nat, eq: {Modifier{} == k : ClauseKind}}
```
With this pattern, a function `def attach_mod(k: Clause<Kernel{}>, m: Clause<Modifier{}>)` **cannot even be called** on two modifiers—mismatched clause kinds are compile-time type errors!

### Pattern 4: Falsify Before Proving, Mutate After Proving (`Bend-LDD` Loop)
Never spend tokens proving a law until you have:
1. **Falsified / sanity-checked the law** on concrete boundary instances (to ensure the law itself isn't misstated).
2. **Proved the law** (`bend PROOF.bend` -> `ALL PROOFS CHECK`).
3. **Mutated `main.bend`** (flipping a branch or operator in a temp copy via `scripts/bend_gate.py`) to confirm that at least one law in `LAWS.bend` breaks when that line of `main.bend` is corrupted. If a mutation survives all laws, **a law is missing**.

### Pattern 5: Zero-Copy C / ES Module Bridge (`bend -o core.c` / `bend -o core.mjs`)
Once `bend PROOF.bend --verdict` passes, compile `main.bend` into a standalone C shared library (`bend main.bend -o core.c` -> `clang -O3 -shared -fPIC core.c -o libcore.dylib`) or JS module (`bend main.bend -o core.mjs`) so Python (`ctypes`) can call the proven functions directly in microseconds.

---

## 6. Site Y Division Roles under Law-Driven Development ("Many Agents, One Judge")

Our existing Site Y division boundaries map directly onto the `bend-ldd` multi-agent verification pipeline:

| Site Y Division | Role in Bend 2 Law-Driven Pipeline | Owned Files | Gate / Tool |
| :--- | :--- | :--- | :--- |
| **Director** | Approves plain-language behavioral decisions & invariants. | High-level intent | Plain-language decision review |
| **T-Division — Bethe (`bethe`)** | Translates spec invariants into `LAWS.bend` (and shared lemmas). Copies `LAWS.bend` into `engN` workbenches. | `ModelCircuits/docs/specs/**/LAWS.bend`, `ModelCircuits/eng0..3/**/LAWS.bend` | `bend LAWS.bend` (typechecks open claims) |
| **Engineers (`eng0`–`eng3`)** | Implements pure core in `main.bend` and proves all laws in `PROOF.bend`. **Blocked from editing `LAWS.bend`.** | `ModelCircuits/engN/**/main.bend`, `ModelCircuits/engN/**/PROOF.bend` | `scripts/bend_gate.py check ModelCircuits/engN` & `scripts/build_tag.py` |
| **Q-Division — QA Bobo (`bobo`)** | **Mutator & Integration QA:** Mutates `main.bend` in temp sandboxes to find unconstrained law gaps; tests Python↔Bend bridge in `ModelCircuits/tests/`. | `ModelCircuits/tests/**` | Mutation survival analysis + `pytest`/`unittest` |
| **Post Commander — Groves (`groves`)** | Maintains `scripts/bend_gate.py`, `scripts/build_tag.py`, and `sandbox_guard.py` `LAWS.bend` firewall. | `scripts/**`, `.agents/**` | `sandbox_guard.py --self-test` & `bend_gate.py --self-test` |

---

## 7. Slow, Zero-Disruption Migration Plan (4 Phases)

To avoid disrupting active Python work in `ModelCircuits/`, we adopt a **4-Phase Gradual Migration ("Shadow -> Gate -> Bridge -> Parallel")** where every step is additive and reversible via git tags:

| Phase | Scope & Deliverable | Risk to Existing Python Pipeline | Exit Criterion |
| :--- | :--- | :--- | :--- |
| **Phase A: Infrastructure & Spec 20 POC** *(DONE — Commit `1d8ace21`)* | • Install Bend `2.0.40` (`~/.bend/bin/bend`).<br>• Enforce `LAWS.bend` firewall in `sandbox_guard.py`.<br>• Wire `scripts/bend_gate.py` into `scripts/build_tag.py`.<br>• Prove 7 Context Graph laws in `ModelCircuits/eng3/bend_poc/`. | **Zero** (runs alongside existing Python code; rollback tag `pre-bend2-poc` in place). | `sandbox_guard.py --self-test` & `bend_gate.py --self-test` pass 100%. |
| **Phase B: Shadow Spec Verification ("Spec Proving" by Bethe & `eng2`/`eng3`)** | • Before Bethe finalizes tricky discrete specs in **Spec 20 (Context Graph Mitosis/Frustration)**, **Spec 18 (Phase 3 Verifier)**, or **Spec 17 (Phase 2 AGM Revision)**, Bethe writes `LAWS.bend` in `docs/specs/<phase>/LAWS.bend`.<br>• `eng2`/`eng3` prove the discrete transition rules in `ModelCircuits/engN/<phase>_bend/PROOF.bend` *before* or *alongside* writing the Python implementation. | **Zero** (Python remains the runtime caller; Bend 2 acts as an executable specification & theorem prover that guarantees the spec and algorithm have zero logical contradictions). | Zero spec-contradiction remands between `eng2`/`eng3`, Bethe, and Bobo on covered modules. |
| **Phase C: Python ↔ Bend C/JS Bridge on 1 Pure Module** | • Compile `ModelCircuits/eng3/bend_poc/main.bend` (or the Phase 3 deterministic verifier kernel) via `bend main.bend -o kernel.c` into a thin `ctypes` / C extension wrapper callable from Python.<br>• QA Bobo runs equivalence + mutation tests in `ModelCircuits/tests/` comparing the Python implementation against the proven Bend 2 kernel. | **Low** (gated behind a single pure function boundary with zero I/O or global state). | Bit-identical parity between Python and compiled Bend 2 kernel + zero surviving mutants. |
| **Phase D: GPU / Multi-Core Parallel Acceleration (`!`)** | • Once Context Graph activation propagation or batch MCU verification scales to thousands of nodes, use Bend 2's parallel fork-join call (`!`) compiled to Apple Metal / CUDA. | **Low** (same proven `main.bend` source compiles to Metal/CPU threads). | Measured throughput speedup on Apple M-series CPU/GPU over Python loop. |

---

## 8. Quick-Reference Commands & Curated Resources

### Local CLI Commands (Site Y)
```bash
# 1. Read the official 678-line Bend 2 language & proof guide (always read first!)
BEND_NO_TELEMETRY=1 ~/.bend/bin/bend guide

# 2. Inspect Base library types and lemmas (Equal.sym, Equal.trans, Equal.cong, Nat, List, Map)
BEND_NO_TELEMETRY=1 ~/.bend/bin/bend base

# 3. Check proofs in a workbench directory (~40ms)
BEND_NO_TELEMETRY=1 ~/.bend/bin/bend ModelCircuits/eng3/bend_poc/PROOF.bend

# 4. Run the Site Y Bend 2 Gate (checks canonical LAWS.bend match, no @unsafe/?TODO, ALL PROOFS CHECK)
.venv/bin/python scripts/bend_gate.py check ModelCircuits/eng3

# 5. Run the Site Y Bend 2 Gate Self-Test (includes mutation & @unsafe blocking tests)
.venv/bin/python scripts/bend_gate.py --self-test
```

### Key External Repositories & References
1. **Official Bend 2 Compiler, Guide & Demos:**
   - Repo: https://github.com/bendlang/bend (`guide/GUIDE.md`, `bend2/base.bend`, `demos/proof_typed_eval`, `demos/proof_insertion_sort`)
   - Papers: *BendTT: An Affine Dependent Type Theory* (`paper/BendTT.pdf`) & *BendRT: A Parallel Runtime for CPUs and GPUs* (`paper/BendRT.pdf`)
2. **Law-Driven Development & Multi-Agent Proof Workflows (`bend-ldd`):**
   - Repo: https://github.com/nohzafk/bend-ldd (`SKILL.md`, `references/method.md`, `references/proofs.md`, `references/multi-agent.md`)
   - Counterexample & Mutation Harness: https://github.com/nohzafk/bend-falsify
3. **Robert Penner’s Bend 2 Tooling & Host-Interop Ecosystem:**
   - **Toph** (fast counterexample checker & concise `.law` compiler before proving): https://github.com/robertpenner/toph (upstream: `oxiglade/toph`)
   - **bend-emit** (compile a pure Bend 2 core into a typed ES module): https://github.com/robertpenner/bend-emit (upstream: `nohzafk/bend-emit`)
   - **The Refl Game** (interactive equality/rewrite proof training for Bend 2 & Lean 4): https://github.com/robertpenner/refl
   - **Thread on Host Primitives vs. Bend 2 Operator Laws:** https://x.com/robertpenner/status/2118625742296678466
4. **Machine Learning & Standard Lemmas in Bend 2:**
   - **bend-ml** (dependently-typed tensors, BPE tokenizer roundtrip proofs, autograd `reverse_eq_forward` proofs): https://github.com/nuxyel/bend-ml
   - **bendlib / bend-mathlib** (machine-checked `Nat` and `List` lemmas): https://github.com/bendlib/bendlib

---

## 9. Empirical Results from Phase B & Phase C Rollout (`s17d-s20-core-bend2-clean-32`)

Applying Bend 2 selectively to the three highest-leverage discrete state machines and decision algebras in `ModelCircuits/` (**25 universal laws proven across `Spec 17D`, `Spec 19`, and `Spec 20`**) uncovered **6 real latent edge-case bugs** in production Python modules that had previously passed all finite unit tests:

| Target Subsystem & Canonical Laws | Workbench & QA Parity Suite | Latent Bugs Exposed by Proving Universal Laws |
| :--- | :--- | :--- |
| **1. Phase 2 AGM & QUD State Machine (`Spec 17D`)**<br>[`docs/specs/phase2/LAWS.bend`](phase2/LAWS.bend) *(7 laws)* | [`ModelCircuits/eng2/bend_agm/`](../../eng2/bend_agm/PROOF.bend)<br>`test_phase2_bend_agm_s17d.py`<br>*(48 root + 6 dependent states, 0 surviving mutants)* | **1.** In [`agm_state.py`](../../core/circuit/phase2/agm_state.py), an unresolved `QUESTION` (`qud_status == "OPEN"`) with non-empty `revisions` transitioned to `REVISED` instead of `ACTIVE`, violating Invariant 5 (`open_qud_ids \| resolved_qud_ids == non_contracted_questions`, **Laws 6 & 7**). |
| **2. Context Graph Polarity & Rule-Clash Algebra (`Spec 20`)**<br>[`docs/specs/context_graph/LAWS.bend`](context_graph/LAWS.bend) *(12 laws)* | [`ModelCircuits/eng3/bend_poc/`](../../eng3/bend_poc/PROOF.bend)<br>`test_context_graph_bend_clash_s20.py`<br>*(1,620 `rules_clash` states, 0 surviving mutants)* | **2.** Premature `return False` at the end of the numeric block in [`frustration.py`](../../core/circuit/context_graph/frustration.py) prevented numeric `PROHIBITION` (`== 8080`) vs `OBLIGATION` (`== 8080`) from reaching Deontic Polarity Clash (**Laws 9 & 11**).<br>**3.** `{"PROHIBITION", "BOUND"}` was omitted from Deontic Polarity Clash despite Spec 20 §2 (**Law 11**).<br>**4.** `str(8080) != str(8080.0)` treated equal numeric `int` and `float` values as distinct when `bound_op is None` (**Law 9**).<br>**5.** Two `BOUND` rules with `!=` on distinct numbers clashed because `op_a != "!="` was omitted, and mutual `!=` on `PROHIBITION` vs `OBLIGATION` clashed (**Law 12**). |
| **3. Phase 4 Delta Classification & Prohibition Guard (`Spec 19`)**<br>[`docs/specs/phase4/LAWS.bend`](phase4/LAWS.bend) *(6 laws)* | [`ModelCircuits/eng2/bend_phase4/`](../../eng2/bend_phase4/PROOF.bend)<br>`test_phase4_bend_s19.py`<br>*(1,920 `classify_delta` + 16 `prohibition_clashes` + 4 status states)* | **6.** In [`linker.py`](../../core/circuit/phase4/linker.py) (`_is_prohibited_by_active_rule`), when an existing rule was a prohibition and an incoming rule was positive, disjoint numeric intervals triggered the prohibition guard; in the reverse arrival order (`elif`), `_intervals_satisfiable` was omitted (**Laws 4 & 5**). |

### Key Practical Engineering Lessons for Bend 2 in Site Y
1. **Where Bend 2 Has Maximum Leverage:** Use Bend 2 on **multi-factor discrete state machines, partition invariants, and relational clash/override decision tables** where the input space is a Cartesian product ($50$ to $2{,}000+$ states) that hand-written unit tests only sparsely sample.
2. **Factoring Through Canonical Quotient Types Keeps Proofs Short (`<= 200` lines):** Instead of brute-force matching across $6 \times 3 \times 6 \times 3 \times 5 = 1{,}620$ combinations in `PROOF.bend`, project inputs onto canonical quotient types first (e.g., `rule_pol(rt: RuleType) -> Pol` and `norm_vrel(vrel: ValRel) -> VRelNorm` in `bend_poc/main.bend`). Equivalence laws between numeric and string representations (`Law 9` & `Law 10`) then reduce definitionally to `{==}` in a single line!
3. **Binder-Order Rule in `match`:** In Bend 2, `match a b c:` must match variables in the exact left-to-right declaration order of the enclosing `def` parameters.
4. **Dual Verification (`bend PROOF.bend` + `.mjs` Exhaustive Parity in QA):** Compiling each proven `main.bend` kernel via `bend main.bend -o main.mjs` inside `ModelCircuits/tests/` allows QA Bobo to exhaustively assert 100% bit-identical parity between the proven Bend 2 kernel and the production Python module across all state combinations alongside mutation survival checks.
