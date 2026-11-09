// Site Y — Stage 24B (`ai_memory` — `hippocampal_index.py`) Compiled Bend 2 Kernel (`main.mjs`)

function run_loop(r) {
  while (r !== null && typeof r === "object" && r.$ === "$JMP") {
    r = r.f(r.x);
  }
  return r;
}

function run_lib(f, n) {
  return (...a) => a.length < n ? run_lib((...b) => f(...a, ...b), n - a.length)
    : run_loop(f(...a));
}

function $bool_eq(_a_0, _b_0) {
  return Boolean(_a_0) === Boolean(_b_0);
}

function $should_include_recalled_concept(_hop_bucket_0, _budget_within_400_0, _words_within_50_0) {
  if (_hop_bucket_0.$ === "Hop0Seed" || _hop_bucket_0.$ === "Hop1Neighbor" || _hop_bucket_0.$ === "Hop2Second") {
    return Boolean(_budget_within_400_0 && _words_within_50_0);
  } else {
    return false;
  }
}

function $can_propagate_from_node(_reached_via_depends_on_sink_0, _step_energy_above_epsilon_0, _within_max_hops_0) {
  if (_reached_via_depends_on_sink_0) {
    return false;
  } else {
    return Boolean(_step_energy_above_epsilon_0 && _within_max_hops_0);
  }
}

function $main() {
  return Boolean($should_include_recalled_concept({$: "Hop0Seed"}, true, true) && $can_propagate_from_node(false, true, true));
}

export default {
  "bool_eq": run_lib((a0, a1) => run_loop($bool_eq(a0, a1)), 2),
  "should_include_recalled_concept": run_lib((a0, a1, a2) => run_loop($should_include_recalled_concept(a0, a1, a2)), 3),
  "can_propagate_from_node": run_lib((a0, a1, a2) => run_loop($can_propagate_from_node(a0, a1, a2)), 3),
  "main": run_lib(() => run_loop($main()), 0),
};
