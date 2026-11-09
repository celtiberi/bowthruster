# Spec 22B — `whiteboard` Spin-Glass Conflict & Parent-Hierarchy Engine (`frustration.py`)

**Author:** Chief Theorist Hans Bethe (`T-Division`) · **Date:** 2026-11-06 · **Assigned Engineer:** `eng3` (`ModelCircuits/eng3/whiteboard/frustration.py` $\rightarrow$ Promoted `ModelCircuits/core/circuit/whiteboard/frustration.py`)  
**Standing Rules:** [`../00a_STANDING_RULES.md`](../00a_STANDING_RULES.md) · **Formal Laws:** [`LAWS.bend`](LAWS.bend)

---

## 1. Strict Isolation Boundary for `eng3` (`Spec 22B`)

`eng3` owns **only** the spin-glass conflict and parent-hierarchy frustration detector of `whiteboard`:
- **Allowed Writable Files in `ModelCircuits/eng3/`:**
  - `ModelCircuits/eng3/whiteboard/frustration.py` ($\le 500$ lines, zero `import re`)
  - `ModelCircuits/eng3/bend_wb_22b/{LAWS.bend,main.bend,PROOF.bend,main.mjs}`
- **Forbidden to `eng3`:** `eng3` may **not** create or edit `schema.py`, `mutations.py` (`Spec 22A`, owned by `eng1`) or `linker.py`, `rewriter.py`, `ingest.py`, `read_engine.py`, `__init__.py` (`Spec 22C`, owned by `eng2`).

---

## 2. Module Contract (`frustration.py`)

### 2.1 `_rules_clash`, `_inherited_rules_clash`, `dependency_or_flow_clashes`, & `flow_payload_clashes` (Governed by Bend 2 Laws 1–8)
- `_intervals_satisfiable(op_a: str | None, va: float, op_b: str | None, vb: float) -> bool`: exact numeric interval satisfiability over $\{<, \le, ==, !=, \ge, >\}$.
- `_rules_clash(ra: dict[str, Any], rb: dict[str, Any]) -> bool`: governed by **Laws 1–5** (`rules_clash_symmetric`, `rules_clash_same_num_str_parity`, `rules_clash_diff_num_str_parity`, `prohibition_clashes_with_positive_on_same_num`, `mutual_neq_never_clashes`).
- `_inherited_rules_clash(ra: dict[str, Any], rb: dict[str, Any], is_exception: bool) -> bool`: governed by **Law 6 (`lex_specialis_shields_clash`)**—returns `False` when `is_exception` is `True`, and `_rules_clash(ra, rb)` when `is_exception` is `False`.
- `dependency_or_flow_clashes(has_prohibition_on_target: bool, is_upward_leaf_import: bool, is_self_or_cycle: bool, is_exception: bool) -> bool`: governed by **Law 7 (`prohibited_dependency_or_flow_clashes`)**—returns `False` when `is_exception` is `True`, and `bool(has_prohibition_on_target or is_upward_leaf_import or is_self_or_cycle)` when `is_exception` is `False`.
- `flow_payload_clashes(source_declares_outputs: bool, payload_in_source_outputs: bool, target_declares_inputs: bool, payload_in_target_inputs: bool) -> bool`: governed by **Law 8 (`flow_interface_payload_compatibility`)**—returns `bool((source_declares_outputs and not payload_in_source_outputs) or (target_declares_inputs and not payload_in_target_inputs))`.
- `classify_lattice_proof_status(has_hard_violation: bool, has_sbf_components: bool, all_components_complete: bool, all_flows_connected: bool) -> str`: governed by **Law 9 (`lattice_proof_proven_iff_complete_and_conflict_free`)**:
  - When `has_hard_violation` is `True`: returns `"BROKEN_INVARIANT"`.
  - When `not has_hard_violation and has_sbf_components and all_components_complete and all_flows_connected`: returns `"PROVEN_COMPLETE"`.
  - Otherwise: returns `"INCOMPLETE_DRAFT"`.

### 2.2 `detect_frustration(graph: dict[str, Any], active_rule_ids: set[str] | list[str] | None = None) -> list[dict[str, Any]]`
Evaluates pairs of active rules $(r_a, r_b)$ across `graph["scopes"]` as well as SBF structural dependencies (`scopes[sid].get("depends_on", [])`), parent-child hierarchy links (`scopes[sid].get("parent")`), and signal flows (`graph.get("flows", [])`):
1. **Scope Relationship Check (Same Root Scope OR Ancestor-Descendant Scope Chain):**
   - Two scopes $s_a, s_b$ are comparable iff:
     - **Same Root Family:** `s_a.split("::")[0] == s_b.split("::")[0]`, **OR**
     - **Parent/Ancestor Chain:** $s_a$ is an ancestor of $s_b$ (or $s_b$ is an ancestor of $s_a$) via `scopes[sid].get("parent")` (walking up at most 16 hops with cycle protection).
2. **Lex Specialis & Exception Shielding (**Law 6**):**
   - Validates that every entry in `r_a.get("exceptions", [])` and `r_b.get("exceptions", [])` is a `dict` (raising `ValueError` fail-closed on any non-`dict` entry).
   - `is_shielded = (id_b in set(r_a.get("overrides", []))) or (id_a in set(r_b.get("overrides", []))) or (id_b in {e.get("rule_id") for e in r_a.get("exceptions", [])}) or (id_a in {e.get("rule_id") for e in r_b.get("exceptions", [])}) or (is_ancestor_pair and (bool(r_a.get("is_exception")) or bool(r_b.get("is_exception"))))`.
   - Calls `_inherited_rules_clash(r_a, r_b, is_shielded)` when `target_prop` matches and `conditions` are non-disjoint (`not cond_a or not cond_b or bool(cond_a & cond_b)`).
3. **SBF Structural Dependency, Hierarchy Cycle, & Signal-Flow Conflict Checks (**Laws 7–8**):**
   - **Dependency Prohibition & Cycle Check (`Law 7`):** For each scope `sid` and each `dep` in `scopes[sid].get("depends_on", [])` (plus `parent = scopes[sid].get("parent")` for cycle checking):
     - Let `has_prohib` be `True` iff any active `PROHIBITION` rule in `sid` (or its ancestor scopes) either has `str(r.get("value") or "").strip().lower() in (dep.lower(), f"{dep.lower()}_imports", "upward_imports")` or has `str(r.get("target_prop") or "").strip().lower() in ("depends_on", "module_dependencies", "imports")` with `str(r.get("value") or "").strip().lower() == dep.lower()` (storing the matching rule ID `prohib_rid`).
     - Let `is_cycle` be `True` iff `dep == sid` or `sid` is reachable from `dep` via `depends_on` edges (`<= 16` hops) or `sid` is reachable from `parent` via `parent` edges (`<= 16` hops).
     - When `dependency_or_flow_clashes(has_prohib, False, is_cycle, False)` is `True`, append a `"type": "CONFLICT"` blocker `{"id": f"conflict_dep_{sid}_{dep}", "type": "CONFLICT", "scope": sid.split("::")[0], "target_prop": "depends_on", "rule_ids": [prohib_rid] if prohib_rid else [f"dep:{sid}->{dep}"], "text": f"Structural dependency conflict: {sid} depends_on {dep}."}`.
   - **Signal-Flow Prohibition & Interface Payload Compatibility Check (`Laws 7–8`):** For each flow `fl` in `graph.get("flows", [])` (`fid = fl["id"]`, `src = fl.get("source", "")`, `tgt = fl.get("target", "")`, `payload = str(fl.get("payload") or "").strip()`):
     - Let `has_prohib` be `True` iff any active `PROHIBITION` rule in `src` (or its ancestor scopes) prohibits `tgt` (`str(r.get("value") or "").strip().lower() in (tgt.lower(), f"{tgt.lower()}_imports")`).
     - Let `is_self` be `bool(src and src == tgt)`.
     - If `dependency_or_flow_clashes(has_prohib, False, is_self, False)` is `True`, append a `"type": "CONFLICT"` blocker `{"id": f"conflict_flow_{fid}", "type": "CONFLICT", "scope": src.split("::")[0] if src else "global_defaults", "target_prop": "flow", "rule_ids": [fid], "text": f"Signal flow conflict on {fid}: {src} -> {tgt}."}`.
     - If `payload`: let `src_outs = [str(x).strip() for x in scopes.get(src, {}).get("outputs", []) if str(x).strip()]` and `tgt_ins = [str(x).strip() for x in scopes.get(tgt, {}).get("inputs", []) if str(x).strip()]`. When `flow_payload_clashes(bool(src_outs), payload in src_outs, bool(tgt_ins), payload in tgt_ins)` is `True`, append a `"type": "CONFLICT"` blocker `{"id": f"conflict_payload_{fid}", "type": "CONFLICT", "scope": src.split("::")[0] if src else "global_defaults", "target_prop": "payload", "rule_ids": [fid], "text": f"Flow payload mismatch on {fid}: {src} --[{payload}]--> {tgt}."}`.
4. **Output Contract:**
   - Returns a list of `"type": "CONFLICT"` blocker dicts in deterministic order.

### 2.3 Continuous Architectural Proof Engine (`evaluate_lattice_proof(graph: dict[str, Any]) -> dict[str, Any]`)
Evaluates the whole-system architectural consistency and completeness of `graph` (governed by **Law 9 `lattice_proof_proven_iff_complete_and_conflict_free`**):
1. **Hard Violations (`hard_violations`):** Runs `conflicts = detect_frustration(graph)`. Any conflict in `conflicts` is recorded in `hard_violations`.
2. **Owned SBF Target Components (`owned_components`):** Collects all scopes `sid` in `graph.get("scopes", {})` (excluding `"global_defaults"`) that have `sc.get("kind") == "COMPONENT"` or non-empty `owner`, `role`, `depends_on`, `children`, or `parent` **and** have at least one design anchor (`bool(sc.get("owner") or sc.get("role") or sc.get("depends_on") or sc.get("children") or sc.get("parent"))`, excluding bare external flow-endpoint scopes such as `the_executive` or `whiteboard` that carry no `owner`/`role`/`depends_on`/`children`/`parent`).
3. **Open Architectural Gaps (`open_gaps`):** For each `sid` in `owned_components`:
   - **Owner Check (`MISSING_OWNER`):** `sid` (or an ancestor scope via `parent`) must have a non-empty `owner`. Otherwise append `{"kind": "MISSING_OWNER", "scope": sid, "description": f"Component '{sid}' has no assigned division owner."}`.
   - **Formal Law Check (`MISSING_LAW`):** `sid` (or an ancestor scope via `parent` or a child scope whose `parent == sid`) must have at least one `LAW` artifact in `graph.get("lattice_artifacts", {})`. Otherwise append `{"kind": "MISSING_LAW", "scope": sid, "description": f"Component '{sid}' has no Bend 2 formal law (% LAW)."}`.
   - **Contract Check (`MISSING_CONTRACT`):** `sid` (or an ancestor/child scope) must have at least one `SCHEMA` or `INTERFACE` artifact in `graph.get("lattice_artifacts", {})`, or non-empty `inputs`/`outputs`, or participate in a `> FLOW` carrying a non-empty `payload`, or (for QA components with `sid.startswith("test_")` or `owner == "bobo"`) at least one rule or task. Otherwise append `{"kind": "MISSING_CONTRACT", "scope": sid, "description": f"Component '{sid}' has no schema, interface, or typed flow payload."}`.
   - **Flow Connectivity Check (`UNWIRED_COMPONENT`):** For non-QA components (`not sid.startswith("test_")` and `owner != "bobo"`), `sid` (or at least one child scope in `sc.get("children", [])` or scope whose `parent == sid`) must appear as `source` or `target` in at least one `> FLOW` in `graph.get("flows", [])`. Otherwise append `{"kind": "UNWIRED_COMPONENT", "scope": sid, "description": f"Component '{sid}' is not connected to any > FLOW edge."}`.
4. **Verdict & Self-Contained Bend 2 Proof Synthesis:**
   - Computes `all_components_complete = not any(g["kind"] in ("MISSING_OWNER", "MISSING_LAW", "MISSING_CONTRACT") for g in open_gaps)` and `all_flows_connected = not any(g["kind"] == "UNWIRED_COMPONENT" for g in open_gaps)`.
   - Computes `status = classify_lattice_proof_status(bool(hard_violations), bool(owned_components), all_components_complete, all_flows_connected)`.
   - Renders `bend_proof_source: str` — a valid, self-contained Bend 2 module defining `LPStatus`, `classify_lattice_proof_status`, and `board_proof_verdict : LPStatus` for the current board's boolean flags — and returns:
     ```python
     {
         "status": status,               # "PROVEN_COMPLETE" | "INCOMPLETE_DRAFT" | "BROKEN_INVARIANT"
         "is_proven": status == "PROVEN_COMPLETE",
         "component_count": len(owned_components),
         "flow_count": len(graph.get("flows", [])),
         "law_count": sum(1 for a in (graph.get("lattice_artifacts") or {}).values() if isinstance(a, dict) and a.get("kind") == "LAW"),
         "contract_count": sum(1 for a in (graph.get("lattice_artifacts") or {}).values() if isinstance(a, dict) and a.get("kind") in ("SCHEMA", "INTERFACE")),
         "hard_violations": hard_violations,
         "open_gaps": open_gaps,
         "bend_proof_source": bend_proof_source,
     }
     ```

---

## 3. Formal Bend 2 Laws (`docs/specs/whiteboard_22b/LAWS.bend` & `ModelCircuits/eng3/bend_wb_22b/`)

Every `eng3` build tag for `Spec 22B` must prove Laws 1–9 in [`LAWS.bend`](LAWS.bend) via `ModelCircuits/eng3/bend_wb_22b/PROOF.bend` (`scripts/bend_gate.py check ModelCircuits/eng3`).
