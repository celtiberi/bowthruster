# Site Y — Engineer 3 Formal Proofs (`PROOF.bend`) for Spec 23B (`the_executive` Routing Gate) Laws 1-7

import Base
import ./main.bend as EXB
import ./LAWS.bend as Laws

# Proof of LAW 1: Transactional commit bi-implication
def Laws.commit_iff_all_clear(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea):
  match has_ambiguity has_open_questions has_rule_conflict has_flawed_idea:
    case False{} False{} False{} False{}:
      {==}
    case False{} False{} False{} True{}:
      {==}
    case False{} False{} True{} False{}:
      {==}
    case False{} False{} True{} True{}:
      {==}
    case False{} True{} False{} False{}:
      {==}
    case False{} True{} False{} True{}:
      {==}
    case False{} True{} True{} False{}:
      {==}
    case False{} True{} True{} True{}:
      {==}
    case True{} False{} False{} False{}:
      {==}
    case True{} False{} False{} True{}:
      {==}
    case True{} False{} True{} False{}:
      {==}
    case True{} False{} True{} True{}:
      {==}
    case True{} True{} False{} False{}:
      {==}
    case True{} True{} False{} True{}:
      {==}
    case True{} True{} True{} False{}:
      {==}
    case True{} True{} True{} True{}:
      {==}

# Proof of LAW 2: Decision bijection with commit flag
def Laws.decision_matches_commit_flag(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea):
  match has_ambiguity has_open_questions has_rule_conflict has_flawed_idea:
    case False{} False{} False{} False{}:
      {==}
    case False{} False{} False{} True{}:
      {==}
    case False{} False{} True{} False{}:
      {==}
    case False{} False{} True{} True{}:
      {==}
    case False{} True{} False{} False{}:
      {==}
    case False{} True{} False{} True{}:
      {==}
    case False{} True{} True{} False{}:
      {==}
    case False{} True{} True{} True{}:
      {==}
    case True{} False{} False{} False{}:
      {==}
    case True{} False{} False{} True{}:
      {==}
    case True{} False{} True{} False{}:
      {==}
    case True{} False{} True{} True{}:
      {==}
    case True{} True{} False{} False{}:
      {==}
    case True{} True{} False{} True{}:
      {==}
    case True{} True{} True{} False{}:
      {==}
    case True{} True{} True{} True{}:
      {==}

# Proof of LAW 3: Rule-conflict bounce dominance
def Laws.conflict_dominates_bounce_reason(has_ambiguity, has_open_questions, has_flawed_idea):
  match has_ambiguity has_open_questions has_flawed_idea:
    case False{} False{} False{}:
      {==}
    case False{} False{} True{}:
      {==}
    case False{} True{} False{}:
      {==}
    case False{} True{} True{}:
      {==}
    case True{} False{} False{}:
      {==}
    case True{} False{} True{}:
      {==}
    case True{} True{} False{}:
      {==}
    case True{} True{} True{}:
      {==}

# Proof of LAW 4: Flawed-idea bounce dominance when conflict-free
def Laws.flawed_idea_dominates_when_no_conflict(has_ambiguity, has_open_questions):
  match has_ambiguity has_open_questions:
    case False{} False{}:
      {==}
    case False{} True{}:
      {==}
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}

# Proof of LAW 5: Clarification bounce when conflict-free and flaw-free
def Laws.ambiguity_or_question_bounces_for_clarification(has_ambiguity, has_open_questions):
  match has_ambiguity has_open_questions:
    case False{} False{}:
      {==}
    case False{} True{}:
      {==}
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}

# Proof of LAW 6: `BounceNone{}` bi-implication
def Laws.bounce_none_iff_commit(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea):
  match has_ambiguity has_open_questions has_rule_conflict has_flawed_idea:
    case False{} False{} False{} False{}:
      {==}
    case False{} False{} False{} True{}:
      {==}
    case False{} False{} True{} False{}:
      {==}
    case False{} False{} True{} True{}:
      {==}
    case False{} True{} False{} False{}:
      {==}
    case False{} True{} False{} True{}:
      {==}
    case False{} True{} True{} False{}:
      {==}
    case False{} True{} True{} True{}:
      {==}
    case True{} False{} False{} False{}:
      {==}
    case True{} False{} False{} True{}:
      {==}
    case True{} False{} True{} False{}:
      {==}
    case True{} False{} True{} True{}:
      {==}
    case True{} True{} False{} False{}:
      {==}
    case True{} True{} False{} True{}:
      {==}
    case True{} True{} True{} False{}:
      {==}
    case True{} True{} True{} True{}:
      {==}

# Proof of LAW 7: Git Pre-Commit Lattice Gate Law (`git_precommit_gate_law`, Stage 26 `precommit_lattice_gate`)
def Laws.git_precommit_gate_law(bounds_ok, contracts_ok, bend_proofs_ok, no_dangling_deletes):
  match bounds_ok contracts_ok bend_proofs_ok no_dangling_deletes:
    case False{} False{} False{} False{}:
      {==}
    case False{} False{} False{} True{}:
      {==}
    case False{} False{} True{} False{}:
      {==}
    case False{} False{} True{} True{}:
      {==}
    case False{} True{} False{} False{}:
      {==}
    case False{} True{} False{} True{}:
      {==}
    case False{} True{} True{} False{}:
      {==}
    case False{} True{} True{} True{}:
      {==}
    case True{} False{} False{} False{}:
      {==}
    case True{} False{} False{} True{}:
      {==}
    case True{} False{} True{} False{}:
      {==}
    case True{} False{} True{} True{}:
      {==}
    case True{} True{} False{} False{}:
      {==}
    case True{} True{} False{} True{}:
      {==}
    case True{} True{} True{} False{}:
      {==}
    case True{} True{} True{} True{}:
      {==}

# Proof of LAW 8: AST Call-Graph Skeleton Law (`skeleton_call_graph_law`, Stage 27 `ast_call_graph_gate`)
def Laws.skeleton_call_graph_law(interface_defined, law_fn_defined, interface_calls_law):
  match interface_defined law_fn_defined interface_calls_law:
    case False{} False{} False{}:
      {==}
    case False{} False{} True{}:
      {==}
    case False{} True{} False{}:
      {==}
    case False{} True{} True{}:
      {==}
    case True{} False{} False{}:
      {==}
    case True{} False{} True{}:
      {==}
    case True{} True{} False{}:
      {==}
    case True{} True{} True{}:
      {==}
