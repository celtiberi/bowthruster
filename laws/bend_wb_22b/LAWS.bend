# Site Y — Canonical Spec 22B (`whiteboard` Spin-Glass Conflict & Parent-Hierarchy Engine) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `whiteboard_22b` build tag in `ModelCircuits/eng3` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as WBB

# LAW 1: Rule-clash symmetry — swapping rule A `(ta, op_a)` and rule B `(tb, op_b)`
# yields the exact same clash verdict across all 1,620 `(ta, op_a, tb, op_b, vrel)` states.
law rules_clash_symmetric:
  for +ta: WBB.RuleType
  for +op_a: WBB.EqOp
  for +tb: WBB.RuleType
  for +op_b: WBB.EqOp
  for +vrel: WBB.ValRel
  {WBB.rules_clash(ta, op_a, tb, op_b, vrel) == WBB.rules_clash(tb, op_b, ta, op_a, vrel) : Bool}

# LAW 2: Numeric-symbolic equality parity — equal numeric values (`VSameNum{}`) and equal
# symbolic strings (`VSameStr{}`) produce the exact same clash verdict for every rule pair.
law rules_clash_same_num_str_parity:
  for +ta: WBB.RuleType
  for +op_a: WBB.EqOp
  for +tb: WBB.RuleType
  for +op_b: WBB.EqOp
  {WBB.rules_clash(ta, op_a, tb, op_b, WBB.VSameNum{}) == WBB.rules_clash(ta, op_a, tb, op_b, WBB.VSameStr{}) : Bool}

# LAW 3: Numeric-symbolic inequality parity — distinct numeric values (`VDiffNum{}`) and
# distinct symbolic strings (`VDiffStr{}`) produce the exact same clash verdict for every rule pair.
law rules_clash_diff_num_str_parity:
  for +ta: WBB.RuleType
  for +op_a: WBB.EqOp
  for +tb: WBB.RuleType
  for +op_b: WBB.EqOp
  {WBB.rules_clash(ta, op_a, tb, op_b, WBB.VDiffNum{}) == WBB.rules_clash(ta, op_a, tb, op_b, WBB.VDiffStr{}) : Bool}

# LAW 4: Deontic polarity clash on equal numeric values — a `PROHIBITION` (`RTProhib{}`)
# with `OpEq{}` clashes (`True{}`) with `tb` (`OpEq{}`) on `VSameNum{}` iff `tb` has positive
# deontic polarity (`RTOblig{}`, `RTPolicy{}`, `RTAction{}`, `RTBound{}`).
law prohibition_clashes_with_positive_on_same_num:
  for +tb: WBB.RuleType
  {WBB.rules_clash(WBB.RTProhib{}, WBB.OpEq{}, tb, WBB.OpEq{}, WBB.VSameNum{}) == WBB.is_pos_pol(WBB.rule_pol(tb)) : Bool}

# LAW 5: Mutual inequality (`OpNeq{}` vs `OpNeq{}`) is always satisfiable and never clashes
# regardless of rule deontic types `(ta, tb)` or value relation `vrel`.
law mutual_neq_never_clashes:
  for +ta: WBB.RuleType
  for +tb: WBB.RuleType
  for +vrel: WBB.ValRel
  {WBB.rules_clash(ta, WBB.OpNeq{}, tb, WBB.OpNeq{}, vrel) == False{} : Bool}

# LAW 6: Lex specialis shields inherited parent-scope clash — when evaluating an inherited
# parent/global rule against a child scope rule (`inherited_rules_clash`), an explicit
# lex specialis exception (`is_exception == True{}`) always shields the pair (`False{}`),
# and when `is_exception == False{}`, it equals `rules_clash(ta, op_a, tb, op_b, vrel)`.
law lex_specialis_shields_clash:
  for +ta: WBB.RuleType
  for +op_a: WBB.EqOp
  for +tb: WBB.RuleType
  for +op_b: WBB.EqOp
  for +vrel: WBB.ValRel
  {Bool.and(
    Bool.not(WBB.inherited_rules_clash(ta, op_a, tb, op_b, vrel, True{})),
    WBB.bool_eq(
      WBB.inherited_rules_clash(ta, op_a, tb, op_b, vrel, False{}),
      WBB.rules_clash(ta, op_a, tb, op_b, vrel)
    )
  ) == True{} : Bool}

# LAW 7: Structural dependency or signal-flow prohibition clash — evaluating a directed
# structural edge (`depends_on` or `FLOW`) via `dependency_or_flow_clashes(has_prohibition_on_target, is_upward_leaf_import, is_self_or_cycle, is_exception)`
# returns `False{}` when shielded by an explicit exception (`is_exception == True{}`), and
# returns `Bool.or(has_prohibition_on_target, Bool.or(is_upward_leaf_import, is_self_or_cycle))`
# when `is_exception == False{}`.
law prohibited_dependency_or_flow_clashes:
  for +has_prohib: Bool
  for +is_upward: Bool
  for +is_cycle: Bool
  {Bool.and(
    Bool.not(WBB.dependency_or_flow_clashes(has_prohib, is_upward, is_cycle, True{})),
    WBB.bool_eq(
      WBB.dependency_or_flow_clashes(has_prohib, is_upward, is_cycle, False{}),
      Bool.or(has_prohib, Bool.or(is_upward, is_cycle))
    )
  ) == True{} : Bool}

# LAW 8: Signal-flow interface payload compatibility — when a `FLOW` edge carries a non-empty
# `payload` from `source` to `target`, `flow_payload_clashes(source_declares_outputs, payload_in_source_outputs, target_declares_inputs, payload_in_target_inputs)`
# returns `True{}` iff `source` explicitly declares outputs (`source_declares_outputs == True{}`)
# that omit `payload` (`payload_in_source_outputs == False{}`) OR `target` explicitly declares
# inputs (`target_declares_inputs == True{}`) that omit `payload` (`payload_in_target_inputs == False{}`).
law flow_interface_payload_compatibility:
  for +src_decl: Bool
  for +in_src: Bool
  for +tgt_decl: Bool
  for +in_tgt: Bool
  {WBB.flow_payload_clashes(src_decl, in_src, tgt_decl, in_tgt) ==
   Bool.or(
     Bool.and(src_decl, Bool.not(in_src)),
     Bool.and(tgt_decl, Bool.not(in_tgt))
   ) : Bool}

# LAW 9: Continuous Lattice Proof Readiness (`classify_lattice_proof_status`) —
# (1) Any hard violation (`has_hard_violation == True{}`) forces `LPBroken{}` (`"BROKEN_INVARIANT"`),
# dominating all completeness flags;
# (2) `LPProven{}` (`"PROVEN_COMPLETE"`) holds iff `has_hard_violation == False{}` AND
# `has_sbf_components == True{}` AND `all_components_complete == True{}` AND `all_flows_connected == True{}`.
law lattice_proof_proven_iff_complete_and_conflict_free:
  for +has_viol: Bool
  for +has_comp: Bool
  for +comp_ok: Bool
  for +flow_ok: Bool
  {Bool.and(
    WBB.bool_eq(
      WBB.is_lp_broken(WBB.classify_lattice_proof_status(has_viol, has_comp, comp_ok, flow_ok)),
      has_viol
    ),
    WBB.bool_eq(
      WBB.is_lp_proven(WBB.classify_lattice_proof_status(has_viol, has_comp, comp_ok, flow_ok)),
      Bool.and(Bool.not(has_viol), Bool.and(has_comp, Bool.and(comp_ok, flow_ok)))
    )
  ) == True{} : Bool}
