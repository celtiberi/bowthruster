function word_to_u32(w) {
  let x = 0;
  for (let i = 0; w.$ === "WCon"; i++) {
    x |= Number(w.head) << i;
    w = w.tail;
  }
  return x >>> 0;
}

function u32_to_word(x) {
  let w = {$: "WNil"};
  for (let i = 31; i >= 0; i--) {
    w = {$: "WCon", head: ((x >>> i) & 1) === 1, tail: w};
  }
  return w;
}

function cmp_new(a, b) {
  return {$: a < b ? "LT" : a === b ? "EQ" : "GT"};
}

function tup_new(a, b) {
  return {$: "Tuple", fst: a, snd: b};
}

function nat_divmod(a, b) {
  return tup_new(b && Math.trunc(a / b), b ? a % b : a);
}

function nat_chk(n) {
  if (n > 281474976710655) {
    throw "bend: a Nat past the largest immediate 2^48-1";
  }
  return n;
}

function nat_in(n) {
  if (!Number.isInteger(n) || n < 0 || n > 2 ** 53) {
    throw "bend: expected a Nat host number: an integer from 0 to 2^53";
  }
  return n;
}

function f32_show(x) {
  let s = "nan";
  for (let p = 1; x === x && p <= 9 && f32_round(s) !== x; p += 1) {
    s = String(Number(x.toExponential(p - 1)));
  }
  return (Object.is(x, -0) ? "-0" : s).replace("Infinity", "inf");
}

function f32_bits(x) {
  return new Uint32Array(new Float32Array([x]).buffer)[0];
}

function f32_from_bits(u) {
  return new Float32Array(new Uint32Array([u]).buffer)[0];
}

function f32_read(s) {
  const re = /^[\t\n\v\f\r ]*[+-]?((\d+(?:\.\d*)?|\.\d+)(e[+-]?\d+)?|inf(inity)?|nan)$/i;
  return re.test(s) ? {$: "Some", value: f32_round(
    s.replace(/inf\w*/i, "Infinity"))} : {$: "None"};
}

const f32_round = function f32_round(s) {
  const d = Number(s);
  const a = Math.abs(d);
  const f = Math.fround(a);
  const g = 2 * a - Math.min(f, 2 ** 128);
  if (g === f || Math.fround(g) !== g || g === Infinity) {
    return Math.sign(d) * f;
  }
  let k = 0;
  while (a * 2 ** k % 1 !== 0) {
    k += 1;
  }
  const [, i, r, e] = /(\d*)\.?(\d*)(?:e([+-]?\d+))?$/i.exec(s);
  const n = Number(e ?? 0) - r.length;
  const x = BigInt(i + r) * 2n ** BigInt(k) * 10n ** BigInt(Math.max(n, 0));
  const y = BigInt(a * 2 ** k) * 10n ** BigInt(Math.max(-n, 0));
  return Math.sign(d) * (x === y || x > y !== g > f ? f : g);
};

const char_new = function char_new(c) {
  return c < 55296 || c >= 57344 && c <= 1114111 ? String.fromCodePoint(c) : String.fromCharCode(56320, c >>> 22, c >>> 11 & 2047, c & 2047);
};

function char_len(s, at = 0) {
  return s[at] === "\uDC00" ? 4 : s.codePointAt(at) > 0xFFFF ? 2 : 1;
}

function char_code(s) {
  return s.length < 4 ? s.codePointAt(0)
    : (s.charCodeAt(1) << 22 | s.charCodeAt(2) << 11 | s.charCodeAt(3)) >>> 0;
}

function string_contains(s, p) {
  // A match starts at a Char, not inside a packed U32 code.
  for (let at = 0; at <= s.length; at += char_len(s, at)) {
    if (s.startsWith(p, at)) {
      return true;
    }
  }
  return false;
}

function str_out(s) {
  return s.replace(/\uDC00[^]{3}/gu, "\uFFFD");
}

function str_in(s) {
  return s.replace(/\p{Cs}/gu, "\uFFFD");
}

// Array
// =====

function array_new(d, v, lgs) {
  if (d > 31 - lgs) {
    throw "bend: an array past the deepest block class 31 (depth + log2 words per cell)";
  }
  return Array(2 ** d).fill(v);
}

function array_node(a, b) {
  if (a.length !== b.length) {
    throw "bend: runtime fail-stop";
  }
  return a.concat(b);
}

function array_rmw(a, i, f) {
  const old = a[i %= a.length];
  a[i] = f(old);
  return tup_new(a, old);
}

// Run
// ===

function run_tail(f, x) {
  return {$: "$JMP", f: f.j?.f === f ? f.j : f, x};
}

function run_clo(j) {
  const f = (x) => run_loop(j(x));
  f.j = j;
  j.f = f;
  return f;
}

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

// Effect
// ======

const $0eff = Object.create(null);
const io_file_append = new Set();

function io_eff(k, run) {
  if (arguments.length > 2) {
    throw new Error("bend: " + k + " takes no need: an effect that waits parks itself");
  }
  if (k in $0eff) {
    throw new Error("bend: two effects register " + k);
  }
  $0eff[k] = run;
}
// Program
// =======

function $is_clarification_op(_op_0) {
  if (_op_0.$ === "OpBlockerClarification") {
    return true;
  } else if (_op_0.$ === "OpBlockerQuestion") {
    return false;
  } else if (_op_0.$ === "OpCommitNote") {
    return false;
  } else if (_op_0.$ === "OpGraduateTaskToFact") {
    return false;
  } else if (_op_0.$ === "OpUpsertTask") {
    return false;
  } else if (_op_0.$ === "OpAttachRationale") {
    return false;
  } else if (_op_0.$ === "OpMarkovianOverwrite") {
    return false;
  } else if (_op_0.$ === "OpLexSpecialisException") {
    return false;
  } else {
    return false;
  }
}

function $is_rule_table_op(_op_0) {
  if (_op_0.$ === "OpBlockerClarification") {
    return false;
  } else if (_op_0.$ === "OpBlockerQuestion") {
    return false;
  } else if (_op_0.$ === "OpCommitNote") {
    return false;
  } else if (_op_0.$ === "OpGraduateTaskToFact") {
    return false;
  } else if (_op_0.$ === "OpUpsertTask") {
    return false;
  } else if (_op_0.$ === "OpAttachRationale") {
    return true;
  } else if (_op_0.$ === "OpMarkovianOverwrite") {
    return true;
  } else if (_op_0.$ === "OpLexSpecialisException") {
    return true;
  } else {
    return true;
  }
}

function $is_markovian_overwrite(_op_0) {
  if (_op_0.$ === "OpBlockerClarification") {
    return false;
  } else if (_op_0.$ === "OpBlockerQuestion") {
    return false;
  } else if (_op_0.$ === "OpCommitNote") {
    return false;
  } else if (_op_0.$ === "OpGraduateTaskToFact") {
    return false;
  } else if (_op_0.$ === "OpUpsertTask") {
    return false;
  } else if (_op_0.$ === "OpAttachRationale") {
    return false;
  } else if (_op_0.$ === "OpMarkovianOverwrite") {
    return true;
  } else if (_op_0.$ === "OpLexSpecialisException") {
    return false;
  } else {
    return false;
  }
}

function $classify_delta(_nt_0, _grounding_0, _has_unclear_0, _completed_0, _has_target_prop_0, _is_prohibited_0, _has_overwrite_match_0, _has_broader_rules_0, _has_rationale_match_0) {
  if (_nt_0.$ === "NKQuestion") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "OpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        return {$: "OpBlockerQuestion"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        return {$: "OpBlockerQuestion"};
      }
    }
  } else if (_nt_0.$ === "NKCommit") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "OpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        return {$: "OpBlockerQuestion"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        return {$: "OpCommitNote"};
      }
    }
  } else if (_nt_0.$ === "NKAction") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "OpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        return {$: "OpBlockerQuestion"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        if (_completed_0) {
          return {$: "OpGraduateTaskToFact"};
        } else {
          return {$: "OpUpsertTask"};
        }
      }
    }
  } else if (_nt_0.$ === "NKFact") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "OpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        return {$: "OpBlockerQuestion"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        if (!_has_target_prop_0) {
          if (_has_broader_rules_0) {
            if (_has_rationale_match_0) {
              return {$: "OpAttachRationale"};
            } else {
              return {$: "OpLexSpecialisException"};
            }
          } else {
            if (_has_rationale_match_0) {
              return {$: "OpAttachRationale"};
            } else {
              return {$: "OpOrthogonalInsert"};
            }
          }
        } else {
          if (!_is_prohibited_0) {
            if (_has_overwrite_match_0) {
              return {$: "OpMarkovianOverwrite"};
            } else {
              if (_has_broader_rules_0) {
                return {$: "OpLexSpecialisException"};
              } else {
                return {$: "OpOrthogonalInsert"};
              }
            }
          } else {
            if (_has_broader_rules_0) {
              return {$: "OpLexSpecialisException"};
            } else {
              return {$: "OpOrthogonalInsert"};
            }
          }
        }
      }
    }
  } else {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "OpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        return {$: "OpBlockerQuestion"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "OpBlockerClarification"};
      } else {
        if (!_has_target_prop_0) {
          if (_has_broader_rules_0) {
            return {$: "OpLexSpecialisException"};
          } else {
            return {$: "OpOrthogonalInsert"};
          }
        } else {
          if (!_is_prohibited_0) {
            if (_has_overwrite_match_0) {
              return {$: "OpMarkovianOverwrite"};
            } else {
              if (_has_broader_rules_0) {
                return {$: "OpLexSpecialisException"};
              } else {
                return {$: "OpOrthogonalInsert"};
              }
            }
          } else {
            if (_has_broader_rules_0) {
              return {$: "OpLexSpecialisException"};
            } else {
              return {$: "OpOrthogonalInsert"};
            }
          }
        }
      }
    }
  }
}

function $prohibition_clashes(_a_is_prohib_0, _b_is_prohib_0, _same_val_0, _intervals_disjoint_0) {
  if (_a_is_prohib_0) {
    if (_b_is_prohib_0) {
      return false;
    } else {
      if (_same_val_0) {
        return true;
      } else {
        if (_intervals_disjoint_0) {
          return true;
        } else {
          return false;
        }
      }
    }
  } else {
    if (!_b_is_prohib_0) {
      return false;
    } else {
      if (_same_val_0) {
        return true;
      } else {
        if (_intervals_disjoint_0) {
          return true;
        } else {
          return false;
        }
      }
    }
  }
}

function $resolve_ingestion_status(_has_clarification_0, _has_conflicts_0) {
  if (_has_clarification_0) {
    return {$: "StatusNeedsClarification"};
  } else {
    if (_has_conflicts_0) {
      return {$: "StatusConflict"};
    } else {
      return {$: "StatusCommitted"};
    }
  }
}

function $is_committed_status(_s_0) {
  if (_s_0.$ === "StatusNeedsClarification") {
    return false;
  } else if (_s_0.$ === "StatusConflict") {
    return false;
  } else {
    return true;
  }
}

function $classify_task_readiness(_has_conflict_0, _has_prohibition_0, _has_unmet_dep_0) {
  if (_has_conflict_0) {
    return {$: "TaskBlockedByConflict"};
  } else {
    if (_has_prohibition_0) {
      return {$: "TaskBlockedByRule"};
    } else {
      if (_has_unmet_dep_0) {
        return {$: "TaskWaitingOnDep"};
      } else {
        return {$: "TaskReady"};
      }
    }
  }
}

function $is_task_ready(_r_0) {
  if (_r_0.$ === "TaskBlockedByConflict") {
    return false;
  } else if (_r_0.$ === "TaskBlockedByRule") {
    return false;
  } else if (_r_0.$ === "TaskWaitingOnDep") {
    return false;
  } else {
    return true;
  }
}

function $can_overwrite_node_kind(_existing_kind_0, _incoming_kind_0) {
  if (_existing_kind_0.$ === "NKFact") {
    if (_incoming_kind_0.$ === "NKFact") {
      return true;
    } else {
      return false;
    }
  } else if (_existing_kind_0.$ === "NKRule") {
    if (_incoming_kind_0.$ === "NKRule") {
      return true;
    } else {
      return false;
    }
  } else {
    return false;
  }
}

function $prohibition_blocks_task(_rule_has_prop_0, _task_has_prop_0, _props_match_0, _mentions_prohibited_val_0) {
  if (!_rule_has_prop_0) {
    return true;
  } else {
    if (_task_has_prop_0) {
      if (_props_match_0) {
        if (_mentions_prohibited_val_0) {
          return true;
        } else {
          return true;
        }
      } else {
        if (_mentions_prohibited_val_0) {
          return true;
        } else {
          return false;
        }
      }
    } else {
      if (_mentions_prohibited_val_0) {
        return true;
      } else {
        return false;
      }
    }
  }
}

function $classify_lattice_delta(_lkind_0, _grounding_0, _has_unclear_0) {
  if (_lkind_0.$ === "LKComponent") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "LOpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertComponent"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertComponent"};
      }
    }
  } else if (_lkind_0.$ === "LKFlow") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "LOpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertFlow"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertFlow"};
      }
    }
  } else if (_lkind_0.$ === "LKSchema") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "LOpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertSchema"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertSchema"};
      }
    }
  } else if (_lkind_0.$ === "LKInterface") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "LOpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertInterface"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertInterface"};
      }
    }
  } else if (_lkind_0.$ === "LKLaw") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "LOpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertLaw"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpUpsertLaw"};
      }
    }
  } else if (_lkind_0.$ === "LKConstraint") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "LOpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpDelegateRuleOrTask"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpDelegateRuleOrTask"};
      }
    }
  } else if (_lkind_0.$ === "LKFact") {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "LOpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpDelegateRuleOrTask"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpDelegateRuleOrTask"};
      }
    }
  } else {
    if (_grounding_0.$ === "GNeedsClarification") {
      return {$: "LOpBlockerClarification"};
    } else if (_grounding_0.$ === "GHasOpenQuestions") {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpDelegateRuleOrTask"};
      }
    } else {
      if (_has_unclear_0) {
        return {$: "LOpBlockerClarification"};
      } else {
        return {$: "LOpDelegateRuleOrTask"};
      }
    }
  }
}

function $is_lattice_clarification(_op_0) {
  if (_op_0.$ === "LOpBlockerClarification") {
    return true;
  } else if (_op_0.$ === "LOpUpsertComponent") {
    return false;
  } else if (_op_0.$ === "LOpUpsertFlow") {
    return false;
  } else if (_op_0.$ === "LOpUpsertSchema") {
    return false;
  } else if (_op_0.$ === "LOpUpsertInterface") {
    return false;
  } else if (_op_0.$ === "LOpUpsertLaw") {
    return false;
  } else {
    return false;
  }
}

function $is_sbf_store_op(_op_0) {
  if (_op_0.$ === "LOpBlockerClarification") {
    return false;
  } else if (_op_0.$ === "LOpUpsertComponent") {
    return true;
  } else if (_op_0.$ === "LOpUpsertFlow") {
    return true;
  } else if (_op_0.$ === "LOpUpsertSchema") {
    return true;
  } else if (_op_0.$ === "LOpUpsertInterface") {
    return true;
  } else if (_op_0.$ === "LOpUpsertLaw") {
    return true;
  } else {
    return false;
  }
}

function $is_python_skeleton_complete(_has_schemas_0, _has_interfaces_0, _has_law_stubs_0) {
  if (_has_schemas_0) {
    if (_has_interfaces_0) {
      if (_has_law_stubs_0) {
        return true;
      } else {
        return false;
      }
    } else {
      return false;
    }
  } else {
    return false;
  }
}
export default {
  "is_clarification_op": run_lib((a0) => run_loop($is_clarification_op(a0)), 1),
  "is_rule_table_op": run_lib((a0) => run_loop($is_rule_table_op(a0)), 1),
  "is_markovian_overwrite": run_lib((a0) => run_loop($is_markovian_overwrite(a0)), 1),
  "classify_delta": run_lib((a0,a1,a2,a3,a4,a5,a6,a7,a8) => run_loop($classify_delta(a0,a1,a2,a3,a4,a5,a6,a7,a8)), 9),
  "prohibition_clashes": run_lib((a0,a1,a2,a3) => run_loop($prohibition_clashes(a0,a1,a2,a3)), 4),
  "resolve_ingestion_status": run_lib((a0,a1) => run_loop($resolve_ingestion_status(a0,a1)), 2),
  "is_committed_status": run_lib((a0) => run_loop($is_committed_status(a0)), 1),
  "classify_task_readiness": run_lib((a0,a1,a2) => run_loop($classify_task_readiness(a0,a1,a2)), 3),
  "is_task_ready": run_lib((a0) => run_loop($is_task_ready(a0)), 1),
  "can_overwrite_node_kind": run_lib((a0,a1) => run_loop($can_overwrite_node_kind(a0,a1)), 2),
  "prohibition_blocks_task": run_lib((a0,a1,a2,a3) => run_loop($prohibition_blocks_task(a0,a1,a2,a3)), 4),
  "classify_lattice_delta": run_lib((a0,a1,a2) => run_loop($classify_lattice_delta(a0,a1,a2)), 3),
  "is_lattice_clarification": run_lib((a0) => run_loop($is_lattice_clarification(a0)), 1),
  "is_sbf_store_op": run_lib((a0) => run_loop($is_sbf_store_op(a0)), 1),
  "is_python_skeleton_complete": run_lib((a0,a1,a2) => run_loop($is_python_skeleton_complete(a0,a1,a2)), 3),
};
