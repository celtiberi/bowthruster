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

function $rule_pol(_t_0) {
  if (_t_0.$ === "RTProhib") {
    return {$: "Neg"};
  } else if (_t_0.$ === "RTOblig") {
    return {$: "Pos"};
  } else if (_t_0.$ === "RTPolicy") {
    return {$: "Pos"};
  } else if (_t_0.$ === "RTAction") {
    return {$: "Pos"};
  } else if (_t_0.$ === "RTBound") {
    return {$: "Pos"};
  } else {
    return {$: "Neut"};
  }
}

function $is_pos_pol(_p_0) {
  if (_p_0.$ === "Pos") {
    return true;
  } else if (_p_0.$ === "Neg") {
    return false;
  } else {
    return false;
  }
}

function $bool_eq(_a_0, _b_0) {
  if (_a_0) {
    if (_b_0) {
      return true;
    } else {
      return false;
    }
  } else {
    if (!_b_0) {
      return true;
    } else {
      return false;
    }
  }
}

function $norm_vrel(_vrel_0) {
  if (_vrel_0.$ === "VSameNum") {
    return {$: "VRSame"};
  } else if (_vrel_0.$ === "VDiffNum") {
    return {$: "VRDiff"};
  } else if (_vrel_0.$ === "VSameStr") {
    return {$: "VRSame"};
  } else if (_vrel_0.$ === "VDiffStr") {
    return {$: "VRDiff"};
  } else {
    return {$: "VRUnconstrained"};
  }
}

function $is_deontic_opposing(_pa_0, _pb_0) {
  if (_pa_0.$ === "Neg") {
    if (_pb_0.$ === "Pos") {
      return true;
    } else if (_pb_0.$ === "Neg") {
      return false;
    } else {
      return false;
    }
  } else if (_pa_0.$ === "Pos") {
    if (_pb_0.$ === "Neg") {
      return true;
    } else if (_pb_0.$ === "Pos") {
      return false;
    } else {
      return false;
    }
  } else {
    if (_pb_0.$ === "Pos") {
      return false;
    } else if (_pb_0.$ === "Neg") {
      return false;
    } else {
      return false;
    }
  }
}

function $clash_core(_pa_0, _op_a_0, _pb_0, _op_b_0, _vr_0) {
  if (_op_a_0.$ === "OpNeq") {
    if (_op_b_0.$ === "OpNeq") {
      return false;
    } else if (_op_b_0.$ === "OpEq") {
      if (_vr_0.$ === "VRDiff") {
        return false;
      } else if (_vr_0.$ === "VRSame") {
        return true;
      } else {
        return $is_deontic_opposing(_pa_0, _pb_0);
      }
    } else {
      if (_vr_0.$ === "VRDiff") {
        return false;
      } else if (_vr_0.$ === "VRSame") {
        return $is_deontic_opposing(_pa_0, _pb_0);
      } else {
        return $is_deontic_opposing(_pa_0, _pb_0);
      }
    }
  } else if (_op_a_0.$ === "OpEq") {
    if (_op_b_0.$ === "OpEq") {
      if (_vr_0.$ === "VRDiff") {
        return true;
      } else if (_vr_0.$ === "VRSame") {
        return $is_deontic_opposing(_pa_0, _pb_0);
      } else {
        return $is_deontic_opposing(_pa_0, _pb_0);
      }
    } else if (_op_b_0.$ === "OpNeq") {
      if (_vr_0.$ === "VRDiff") {
        return false;
      } else if (_vr_0.$ === "VRSame") {
        return true;
      } else {
        return $is_deontic_opposing(_pa_0, _pb_0);
      }
    } else {
      if (_vr_0.$ === "VRDiff") {
        return false;
      } else if (_vr_0.$ === "VRSame") {
        return $is_deontic_opposing(_pa_0, _pb_0);
      } else {
        return $is_deontic_opposing(_pa_0, _pb_0);
      }
    }
  } else {
    if (_op_b_0.$ === "OpEq") {
      if (_vr_0.$ === "VRDiff") {
        return false;
      } else if (_vr_0.$ === "VRSame") {
        return $is_deontic_opposing(_pa_0, _pb_0);
      } else {
        return $is_deontic_opposing(_pa_0, _pb_0);
      }
    } else if (_op_b_0.$ === "OpNeq") {
      if (_vr_0.$ === "VRDiff") {
        return false;
      } else if (_vr_0.$ === "VRSame") {
        return $is_deontic_opposing(_pa_0, _pb_0);
      } else {
        return $is_deontic_opposing(_pa_0, _pb_0);
      }
    } else {
      if (_vr_0.$ === "VRDiff") {
        return false;
      } else if (_vr_0.$ === "VRSame") {
        return $is_deontic_opposing(_pa_0, _pb_0);
      } else {
        return $is_deontic_opposing(_pa_0, _pb_0);
      }
    }
  }
}

function $rules_clash(_ta_0, _op_a_0, _tb_0, _op_b_0, _vrel_0) {
  return $clash_core($$rule_pol(_ta_0), _op_a_0, $$rule_pol(_tb_0), _op_b_0, $$norm_vrel(_vrel_0));
}

function $inherited_rules_clash(_ta_0, _op_a_0, _tb_0, _op_b_0, _vrel_0, _is_exception_0) {
  if (_is_exception_0) {
    return false;
  } else {
    return $rules_clash(_ta_0, _op_a_0, _tb_0, _op_b_0, _vrel_0);
  }
}

function $dependency_or_flow_clashes(_has_prohib_0, _is_upward_0, _is_cycle_0, _is_exception_0) {
  if (_is_exception_0) {
    return false;
  } else {
    return Boolean(_has_prohib_0 || _is_upward_0 || _is_cycle_0);
  }
}

function $flow_payload_clashes(_src_decl_0, _in_src_0, _tgt_decl_0, _in_tgt_0) {
  return Boolean((_src_decl_0 && !_in_src_0) || (_tgt_decl_0 && !_in_tgt_0));
}

function $is_lp_broken(_s_0) {
  return _s_0.$ === "LPBroken";
}

function $is_lp_proven(_s_0) {
  return _s_0.$ === "LPProven";
}

function $classify_lattice_proof_status(_has_viol_0, _has_comp_0, _comp_ok_0, _flow_ok_0) {
  if (_has_viol_0) {
    return {$: "LPBroken"};
  } else if (_has_comp_0 && _comp_ok_0 && _flow_ok_0) {
    return {$: "LPProven"};
  } else {
    return {$: "LPDraft"};
  }
}

function $main() {
  return $inherited_rules_clash({$: "RTProhib"}, {$: "OpEq"}, {$: "RTOblig"}, {$: "OpEq"}, {$: "VSameNum"}, false);
}

const $$rule_pol = $rule_pol;
const $$norm_vrel = $norm_vrel;export default {
  "rule_pol": run_lib((a0) => run_loop($rule_pol(a0)), 1),
  "is_pos_pol": run_lib((a0) => run_loop($is_pos_pol(a0)), 1),
  "bool_eq": run_lib((a0,a1) => run_loop($bool_eq(a0,a1)), 2),
  "norm_vrel": run_lib((a0) => run_loop($norm_vrel(a0)), 1),
  "is_deontic_opposing": run_lib((a0,a1) => run_loop($is_deontic_opposing(a0,a1)), 2),
  "clash_core": run_lib((a0,a1,a2,a3,a4) => run_loop($clash_core(a0,a1,a2,a3,a4)), 5),
  "rules_clash": run_lib((a0,a1,a2,a3,a4) => run_loop($rules_clash(a0,a1,a2,a3,a4)), 5),
  "inherited_rules_clash": run_lib((a0,a1,a2,a3,a4,a5) => run_loop($inherited_rules_clash(a0,a1,a2,a3,a4,a5)), 6),
  "dependency_or_flow_clashes": run_lib((a0,a1,a2,a3) => run_loop($dependency_or_flow_clashes(a0,a1,a2,a3)), 4),
  "flow_payload_clashes": run_lib((a0,a1,a2,a3) => run_loop($flow_payload_clashes(a0,a1,a2,a3)), 4),
  "is_lp_broken": run_lib((a0) => run_loop($is_lp_broken(a0)), 1),
  "is_lp_proven": run_lib((a0) => run_loop($is_lp_proven(a0)), 1),
  "classify_lattice_proof_status": run_lib((a0,a1,a2,a3) => run_loop($classify_lattice_proof_status(a0,a1,a2,a3)), 4),
  "main": run_lib(() => run_loop($main()), 0),
};
