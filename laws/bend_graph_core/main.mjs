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

function $inv_pol(_p_0) {
  if (_p_0.$ === "Pos") {
    return {$: "Neg"};
  } else if (_p_0.$ === "Neg") {
    return {$: "Pos"};
  } else {
    return {$: "Neut"};
  }
}

function $comp_pol(_a_0, _b_0) {
  if (_a_0.$ === "Pos") {
    return _b_0;
  } else if (_a_0.$ === "Neg") {
    return $inv_pol(_b_0);
  } else {
    return {$: "Neut"};
  }
}

function $cat(_xs_0, _ys_0) {
  if (_xs_0.$ === "Nil") {
    return _ys_0;
  } else {
    const _h_0 = _xs_0["head"];
    const _t_0 = _xs_0["tail"];
    return {$: "Con", "head": _h_0, "tail": $$cat(_t_0, _ys_0)};
  }
}

function $path_pol($0, $1) {
  for (;;) {
    {
      const _edges_0 = $0;
      const _p_0 = $1;
      if (_edges_0.$ === "Nil") {
        return _p_0;
      } else {
        const _h_0 = _edges_0["head"];
        const _t_0 = _edges_0["tail"];
        $0 = _t_0;
        $1 = $$comp_pol(_p_0, _h_0);
        continue;
      }
    }
  }
}

function $is_frustrated(_a_0, _b_0) {
  if (_a_0) {
    if (_b_0) {
      return false;
    } else {
      return true;
    }
  } else {
    if (!_b_0) {
      return false;
    } else {
      return true;
    }
  }
}

function $dec_nat(_x_0) {
  if (_x_0 == 0) {
    return 0;
  } else {
    const _p_0 = (_x_0 - 1);
    return _p_0;
  }
}

function $decay($0, $1) {
  for (;;) {
    {
      const _steps_0 = $0;
      const _act_0 = $1;
      if (_steps_0 == 0) {
        return _act_0;
      } else {
        const _rest_0 = (_steps_0 - 1);
        $0 = _rest_0;
        $1 = $$dec_nat(_act_0);
        continue;
      }
    }
  }
}

function $nat_min(_a_0, _b_0) {
  if (_a_0 == 0) {
    return 0;
  } else {
    const _ap_0 = (_a_0 - 1);
    if (_b_0 == 0) {
      return 0;
    } else {
      const _bp_0 = (_b_0 - 1);
      return nat_chk($$nat_min(_ap_0, _bp_0) + 1);
    }
  }
}

function $admit_lod1_count(_cands_0, _lod2_req_0, _cap_0) {
  return $nat_min(_cands_0, _cap_0);
}

function $classify_temp_regime(_sb_0, _has_cross_0, _has_task_0) {
  if (_sb_0.$ === "SBZero") {
    return {$: "Standard"};
  } else if (_sb_0.$ === "SBMany") {
    return {$: "Warm"};
  } else if (_sb_0.$ === "SBTwo") {
    return {$: "Standard"};
  } else {
    if (!_has_cross_0) {
      if (!_has_task_0) {
        return {$: "Cold"};
      } else {
        return {$: "Standard"};
      }
    } else {
      if (!_has_task_0) {
        return {$: "Standard"};
      } else {
        return {$: "Standard"};
      }
    }
  }
}

function $tau_min_scaled(_r_0) {
  if (_r_0.$ === "Warm") {
    return 2;
  } else if (_r_0.$ === "Standard") {
    return 4;
  } else {
    return 5;
  }
}

function $nat_lte($0, $1) {
  for (;;) {
    {
      const _a_0 = $0;
      const _b_0 = $1;
      if (_a_0 == 0) {
        return true;
      } else {
        const _ap_0 = (_a_0 - 1);
        if (_b_0 == 0) {
          return false;
        } else {
          const _bp_0 = (_b_0 - 1);
          $0 = _ap_0;
          $1 = _bp_0;
          continue;
        }
      }
    }
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
    if (_b_0) {
      return false;
    } else {
      return true;
    }
  }
}

function $matches_nd_slice(_is_unconstrained_0, _has_dim_overlap_0) {
  if (_is_unconstrained_0) {
    return true;
  } else {
    if (_has_dim_overlap_0) {
      return true;
    } else {
      return false;
    }
  }
}

function $flow_edge_pol() {
  return {$: "Pos"};
}

function $main() {
  return $path_pol({$: "Con", "head": {$: "Neg"}, "tail": {$: "Con", "head": {$: "Neg"}, "tail": {$: "Con", "head": {$: "Pos"}, "tail": {$: "Nil"}}}}, {$: "Pos"});
}

const $$cat = $cat;
const $$comp_pol = $comp_pol;
const $$dec_nat = $dec_nat;
const $$nat_min = $nat_min;export default {
  "inv_pol": run_lib((a0) => run_loop($inv_pol(a0)), 1),
  "comp_pol": run_lib((a0,a1) => run_loop($comp_pol(a0,a1)), 2),
  "cat": run_lib((a0,a1) => run_loop($cat(a0,a1)), 2),
  "path_pol": run_lib((a0,a1) => run_loop($path_pol(a0,a1)), 2),
  "is_frustrated": run_lib((a0,a1) => run_loop($is_frustrated(a0,a1)), 2),
  "dec_nat": run_lib((a0) => run_loop($dec_nat(nat_in(a0))), 1),
  "decay": run_lib((a0,a1) => run_loop($decay(nat_in(a0),nat_in(a1))), 2),
  "nat_min": run_lib((a0,a1) => run_loop($nat_min(nat_in(a0),nat_in(a1))), 2),
  "admit_lod1_count": run_lib((a0,a1,a2) => run_loop($admit_lod1_count(nat_in(a0),nat_in(a1),nat_in(a2))), 3),
  "classify_temp_regime": run_lib((a0,a1,a2) => run_loop($classify_temp_regime(a0,a1,a2)), 3),
  "tau_min_scaled": run_lib((a0) => run_loop($tau_min_scaled(a0)), 1),
  "nat_lte": run_lib((a0,a1) => run_loop($nat_lte(nat_in(a0),nat_in(a1))), 2),
  "bool_eq": run_lib((a0,a1) => run_loop($bool_eq(a0,a1)), 2),
  "matches_nd_slice": run_lib((a0,a1) => run_loop($matches_nd_slice(a0,a1)), 2),
  "flow_edge_pol": run_lib(() => run_loop($flow_edge_pol()), 0),
  "main": run_lib(() => run_loop($main()), 0),
};
