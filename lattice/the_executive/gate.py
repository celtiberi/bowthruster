"""Spec 23B, Stage 26 & Stage 27: `the_executive` Routing Gate, Pre-Commit Lattice Gate & AST Call-Graph Skeleton Gate (`gate.py`)."""

from __future__ import annotations

import ast, copy, functools, os
from enum import Enum
from pathlib import Path
from typing import Any

from lattice.graph_core import DEFAULT_K_CAP, DEFAULT_MAX_RING, SQLiteGraphStore

DECISION_COMMIT_TO_WHITEBOARD, DECISION_SEND_BACK_TO_USER = "COMMIT_TO_WHITEBOARD", "SEND_BACK_TO_USER"
DECISION_ALLOW_COMMIT, DECISION_REJECT_COMMIT = "ALLOW_COMMIT", "REJECT_COMMIT"
VALID_EXECUTIVE_DECISIONS = frozenset({DECISION_COMMIT_TO_WHITEBOARD, DECISION_SEND_BACK_TO_USER})

BOUNCE_NONE, BOUNCE_NEEDS_CLARIFICATION, BOUNCE_FLAWED_IDEA, BOUNCE_RULE_CONFLICT = "NONE", "NEEDS_CLARIFICATION", "FLAWED_IDEA", "RULE_CONFLICT"
BOUNCE_BEND_PROOF_FAILURE, BOUNCE_DANGLING_DELETED_URI = "BEND_PROOF_FAILURE", "DANGLING_DELETED_URI"
DEFAULT_MAX_FILE_LINES = 500
VALID_BOUNCE_REASONS = frozenset({BOUNCE_NONE, BOUNCE_NEEDS_CLARIFICATION, BOUNCE_FLAWED_IDEA, BOUNCE_RULE_CONFLICT, BOUNCE_BEND_PROOF_FAILURE, BOUNCE_DANGLING_DELETED_URI})
REQUIRED_PREFLIGHT_KEYS: tuple[str, ...] = ("turn_index", "intake_packet", "is_clean", "has_ambiguity", "has_open_questions", "has_rule_conflict", "has_flawed_idea", "mcu_findings", "conflicts", "flawed_ideas", "clarification_questions", "open_questions")
_BEND_WRAPPER_FNS = frozenset({"bool_eq", "bool_imp", "and", "or", "not", "true", "false", "eq"})

class ExecutiveDecision(str, Enum):
    COMMIT_TO_WHITEBOARD, SEND_BACK_TO_USER = DECISION_COMMIT_TO_WHITEBOARD, DECISION_SEND_BACK_TO_USER

class BounceReason(str, Enum):
    NONE, NEEDS_CLARIFICATION, FLAWED_IDEA, RULE_CONFLICT = BOUNCE_NONE, BOUNCE_NEEDS_CLARIFICATION, BOUNCE_FLAWED_IDEA, BOUNCE_RULE_CONFLICT

class ExecutiveGateError(ValueError):
    def __init__(self, code: str, errors: list[str]) -> None:
        self.code, self.errors = code, list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")

class GateDecisionResult(dict[str, Any]):
    def to_dict(self) -> dict[str, Any]: return copy.deepcopy(dict(self))
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name) or name not in self: raise AttributeError(name)
        return self[name]

def should_commit_whiteboard(has_ambiguity: bool, has_open_questions: bool, has_rule_conflict: bool, has_flawed_idea: bool = False) -> bool:
    return not (bool(has_ambiguity) or bool(has_open_questions) or bool(has_rule_conflict) or bool(has_flawed_idea))

def route_executive_decision(has_ambiguity: bool, has_open_questions: bool, has_rule_conflict: bool, has_flawed_idea: bool = False) -> str:
    return ExecutiveDecision.COMMIT_TO_WHITEBOARD.value if should_commit_whiteboard(has_ambiguity, has_open_questions, has_rule_conflict, has_flawed_idea) else ExecutiveDecision.SEND_BACK_TO_USER.value

def resolve_bounce_reason(has_ambiguity: bool, has_open_questions: bool, has_rule_conflict: bool, has_flawed_idea: bool = False) -> str:
    return BounceReason.RULE_CONFLICT.value if bool(has_rule_conflict) else (BounceReason.FLAWED_IDEA.value if bool(has_flawed_idea) else (BounceReason.NEEDS_CLARIFICATION.value if (bool(has_ambiguity) or bool(has_open_questions)) else BounceReason.NONE.value))

def verify_preflight_report(preflight_report: dict[str, Any]) -> list[str]:
    if not isinstance(preflight_report, dict): return [f"preflight_report must be a dict, got {type(preflight_report).__name__}"]
    if errors := [f"preflight_report missing required key {k!r}" for k in REQUIRED_PREFLIGHT_KEYS if k not in preflight_report]: return errors
    turn_idx, intake_pkt = preflight_report.get("turn_index"), preflight_report.get("intake_packet")
    if isinstance(turn_idx, bool) or not isinstance(turn_idx, int) or turn_idx < 1: errors.append(f"preflight_report['turn_index'] must be a positive int, got {turn_idx!r}")
    if not isinstance(intake_pkt, dict): errors.append(f"preflight_report['intake_packet'] must be a dict, got {type(intake_pkt).__name__}")
    elif not isinstance(intake_pkt.get("parsed_mcus"), list): errors.append("preflight_report['intake_packet']['parsed_mcus'] must be a list")
    errors.extend(f"preflight_report[{lk!r}] must be a list, got {type(preflight_report.get(lk)).__name__}" for lk in ("mcu_findings", "conflicts", "flawed_ideas", "clarification_questions", "open_questions") if not isinstance(preflight_report.get(lk), list))
    errors.extend(f"preflight_report[{fk!r}] must be a bool, got {preflight_report.get(fk)!r}" for fk in ("has_ambiguity", "has_open_questions", "has_rule_conflict", "has_flawed_idea", "is_clean") if not isinstance(preflight_report.get(fk), bool))
    if errors: return errors
    has_amb, has_oq, has_rc, has_fi, is_clean = preflight_report["has_ambiguity"], preflight_report["has_open_questions"], preflight_report["has_rule_conflict"], preflight_report["has_flawed_idea"], preflight_report["is_clean"]
    if is_clean != (expected_clean := should_commit_whiteboard(has_amb, has_oq, has_rc, has_fi)): errors.append(f"preflight_report['is_clean']={is_clean!r} != should_commit_whiteboard(...)={expected_clean!r}")
    if has_rc != (len(preflight_report["conflicts"]) > 0): errors.append(f"preflight_report['has_rule_conflict']={has_rc!r} != (len(conflicts) > 0)")
    if has_fi != (len(preflight_report["flawed_ideas"]) > 0): errors.append(f"preflight_report['has_flawed_idea']={has_fi!r} != (len(flawed_ideas) > 0)")
    return errors

def execute_gate_decision(
    preflight_report: dict[str, Any], whiteboard_graph: dict[str, Any] | None = None, *,
    k_cap: int = DEFAULT_K_CAP, max_ring: int = DEFAULT_MAX_RING, store: SQLiteGraphStore | None = None, db_path: str | Path | None = None,
) -> GateDecisionResult:
    """Executes the transactional Whiteboard gate decision, committing via `ingest_intake_packet` iff `should_commit_whiteboard(...)` is `True` (Spec 23B §2.4)."""
    if rep_errors := verify_preflight_report(preflight_report):
        raise ExecutiveGateError("gate_invalid_preflight_report", rep_errors)
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        raise ExecutiveGateError("gate_invalid_whiteboard", [f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}"])
    has_amb, has_oq, has_rc, has_fi = preflight_report["has_ambiguity"], preflight_report["has_open_questions"], preflight_report["has_rule_conflict"], preflight_report["has_flawed_idea"]
    should_commit, decision, bounce_reason = should_commit_whiteboard(has_amb, has_oq, has_rc, has_fi), route_executive_decision(has_amb, has_oq, has_rc, has_fi), resolve_bounce_reason(has_amb, has_oq, has_rc, has_fi)
    parsed_mcus = copy.deepcopy(preflight_report["intake_packet"].get("parsed_mcus", []))
    from lattice.whiteboard import create_empty_context_graph, ingest_intake_packet
    from lattice.whiteboard.frustration import evaluate_lattice_proof
    if should_commit:
        ingestion_res = ingest_intake_packet(whiteboard_graph, preflight_report["intake_packet"], k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path)
        active_graph, whiteboard_mutated, committed_items, blocked_items = ingestion_res["graph"], True, parsed_mcus, []
    else:
        active_graph = create_empty_context_graph(k_cap=k_cap, max_ring=max_ring, store=store, db_path=db_path) if whiteboard_graph is None else whiteboard_graph
        ingestion_res, whiteboard_mutated, committed_items, blocked_items = None, False, [], parsed_mcus
    lattice_proof = evaluate_lattice_proof(active_graph)
    if should_commit and isinstance(active_graph, dict):
        active_graph["lattice_proof"] = lattice_proof
    return GateDecisionResult({"turn_index": int(preflight_report["turn_index"]), "decision": decision, "bounce_reason": bounce_reason, "should_commit": should_commit, "whiteboard_mutated": whiteboard_mutated, "whiteboard_graph": active_graph, "ingestion_result": ingestion_res, "committed_items": committed_items, "blocked_items": blocked_items, "conflicts": copy.deepcopy(preflight_report["conflicts"]), "flawed_ideas": copy.deepcopy(preflight_report["flawed_ideas"]), "clarification_questions": list(preflight_report["clarification_questions"]), "open_questions": list(preflight_report["open_questions"]), "preflight_report": preflight_report, "lattice_proof": lattice_proof})

def should_allow_git_commit(bounds_ok: bool, contracts_ok: bool, bend_proofs_ok: bool, no_dangling_deletes: bool) -> bool:
    """Pure pre-commit gate classifier governed by Bend 2 Law 7 (`git_precommit_gate_law`, Stage 26)."""
    return bool(bounds_ok and contracts_ok and bend_proofs_ok and no_dangling_deletes)

def is_call_graph_skeleton_valid(interface_defined: bool, law_fn_defined: bool, interface_calls_law: bool) -> bool:
    """Pure AST call-graph skeleton gate governed by Bend 2 Law 8 (`skeleton_call_graph_law`, Stage 27)."""
    return bool(interface_defined and law_fn_defined and interface_calls_law)
def is_verified_scope_protected(file_hash_changed: bool = False, scope_still_verified: bool = False) -> bool:
    """Reject commits that modify a baseline-hashed file belonging to a VERIFIED component unless the component was marked STALE."""
    return not (bool(file_hash_changed) and bool(scope_still_verified))
verified_scope_edit_guard_law = is_verified_scope_protected

def _paths_match(p1: str, p2: str) -> bool:
    a = str(p1 or "").strip().removeprefix("uri://file/").removeprefix("uri://").removeprefix("file://").removeprefix("./").split("#", 1)[0].split(":", 1)[0].strip()
    b = str(p2 or "").strip().removeprefix("uri://file/").removeprefix("uri://").removeprefix("file://").removeprefix("./").split("#", 1)[0].split(":", 1)[0].strip()
    return bool(a and b and (a == b or a.endswith(f"/{b}") or b.endswith(f"/{a}")))

def _check_numeric_bound(actual: float, op: str, limit: float) -> bool:
    ops = {"<=": actual <= limit, "le": actual <= limit, "at_most": actual <= limit, "max": actual <= limit, "<": actual < limit, "lt": actual < limit, ">=": actual >= limit, "ge": actual >= limit, "at_least": actual >= limit, "min": actual >= limit, ">": actual > limit, "gt": actual > limit, "==": actual == limit, "=": actual == limit, "eq": actual == limit, "!=": actual != limit, "ne": actual != limit}
    return ops.get(op, True)

def _extract_scope_files(sc: dict[str, Any]) -> list[str]:
    attrs = sc.get("attributes") if isinstance(sc.get("attributes"), dict) else {}
    return [str(x).strip() for k, src in (("files", sc), ("files", attrs), ("_table_files", attrs)) for x in (src.get(k) if isinstance(src.get(k), list) else []) if str(x).strip()]

def _resolve_file_scopes(path: str, explicit_scope: str, scopes: dict[str, Any], artifacts: list[dict[str, Any]]) -> list[str]:
    matched: list[str] = [explicit_scope] if (explicit_scope and explicit_scope in scopes) else []
    for sid, sc in scopes.items():
        if sid != "global_defaults" and isinstance(sc, dict) and sid not in matched and (any(_paths_match(path, fp) for fp in _extract_scope_files(sc)) or f"/{sid}/" in f"/{path}/" or Path(path).stem == sid):
            matched.append(sid)
    for art in artifacts:
        if (asid := str(art.get("scope") or "").strip()) and asid not in matched and _paths_match(path, str(art.get("file") or "").strip()):
            matched.append(asid)
    return matched

def _normalize_symbol_map(raw_syms: Any) -> tuple[set[str], dict[str, set[str]]]:
    names, params_by_name = set(), {}
    items = [(str(k).strip(), v) for k, v in raw_syms.items()] if isinstance(raw_syms, dict) else ([(item.strip(), None) if isinstance(item, str) else (str(item.get("name") or item.get("id") or "").strip(), item) for item in raw_syms if isinstance(item, (str, dict))] if isinstance(raw_syms, (list, tuple, set)) else [])
    for nm, val in items:
        if nm:
            names.add(nm)
            raw_p = val if isinstance(val, (list, tuple, set)) else ((val.get("args") or val.get("inputs") or val.get("params") or val.get("fields") or []) if isinstance(val, dict) else None)
            if isinstance(raw_p, (list, tuple, set)):
                params_by_name[nm] = {str(p).strip().lstrip("*") for p in raw_p if str(p).strip()}
    return names, params_by_name

def _unpack_lattice_and_files(whiteboard_graph: dict[str, Any] | None, staged_diff_report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if whiteboard_graph is not None and not isinstance(whiteboard_graph, dict):
        raise ExecutiveGateError("gate_invalid_whiteboard", [f"whiteboard_graph must be a dict or None, got {type(whiteboard_graph).__name__}"])
    if not isinstance(staged_diff_report, dict):
        raise ExecutiveGateError("gate_invalid_staged_diff_report", [f"staged_diff_report must be a dict, got {type(staged_diff_report).__name__}"])
    wb = whiteboard_graph or {}
    scopes = wb.get("scopes") if isinstance(wb.get("scopes"), dict) else {}
    raw_arts = wb.get("lattice_artifacts")
    artifacts = [a for a in (raw_arts.values() if isinstance(raw_arts, dict) else (raw_arts if isinstance(raw_arts, list) else [])) if isinstance(a, dict)]
    raw_files = staged_diff_report.get("files") if isinstance(staged_diff_report.get("files"), list) else (staged_diff_report.get("staged_files") if isinstance(staged_diff_report.get("staged_files"), list) else [])
    return wb, scopes, artifacts, [f for f in raw_files if isinstance(f, dict)]

def _extract_law_fn_name(art: dict[str, Any]) -> str:
    for k in ("fn_name", "function", "helper_fn", "target_fn"):
        if str(art.get(k) or "").strip():
            return str(art[k]).strip().split(".")[-1]
    expr = str(art.get("lhs") or art.get("formula") or (str(art.get("description") or "").split(":", 1)[-1] if "(" in str(art.get("description") or "") else "")).strip()
    for part in expr.split("(")[:-1]:
        if tokens := part.replace(":", " ").replace(",", " ").replace("{", " ").replace("=", " ").split():
            if (cand := tokens[-1].split(".")[-1].strip()) and cand.isidentifier() and cand.lower() not in _BEND_WRAPPER_FNS and not cand.startswith("check_"):
                return cand
    return ""

def _collect_fn_calls(node: ast.AST, law_fns: set[str]) -> set[str]:
    calls: set[str] = set()
    for ch in ast.iter_child_nodes(node):
        if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): continue
        if isinstance(ch, ast.Assign) and all(isinstance(t, ast.Name) and t.id == "_" for e in ch.targets for t in (e.elts if isinstance(e, (ast.Tuple, ast.List)) else [e])): continue
        if isinstance(ch, ast.AnnAssign) and isinstance(ch.target, ast.Name) and ch.target.id == "_": continue
        if isinstance(ch, ast.Expr):
            calls.update(fn for s in ast.walk(ch.value) if isinstance(s, ast.Call) and (fn := (s.func.id if isinstance(s.func, ast.Name) else (s.func.attr if isinstance(s.func, ast.Attribute) else ""))) and fn not in law_fns)
            continue
        if isinstance(ch, ast.Call) and (fn := (ch.func.id if isinstance(ch.func, ast.Name) else (ch.func.attr if isinstance(ch.func, ast.Attribute) else ""))): calls.add(fn)
        calls.update(_collect_fn_calls(ch, law_fns))
    return calls

@functools.lru_cache(maxsize=256)
def _cached_ast_nodes(src: str) -> tuple[ast.Module, tuple[ast.AST, ...]]:
    return (t := ast.parse(src)), tuple(ast.walk(t))

def _parse_file_call_graph(f: dict[str, Any], law_fns: set[str] | None = None) -> tuple[bool, set[str], dict[str, set[str]]]:
    has_cg, (fn_names, _), (cls_names, _), lset = False, _normalize_symbol_map(f.get("ast_functions")), _normalize_symbol_map(f.get("ast_classes") if "ast_classes" in f else f.get("ast_schemas")), (law_fns or set())
    defined_fns, call_graph = (fn_names | cls_names), {}
    if isinstance(raw_cg := f.get("ast_call_graph"), dict):
        has_cg = True
        for caller, callees in raw_cg.items():
            if c_name := str(caller).strip():
                defined_fns.add(c_name); c_set = {str(x).strip() for x in (callees if isinstance(callees, (list, tuple, set)) else []) if str(x).strip()}
                call_graph.setdefault(c_name, set()).update(c_set)
                if "ast_functions" not in f or f.get("ast_functions") is None: defined_fns.update(c_set)
    src = f.get("source") if f.get("source") is not None else (f.get("content") if f.get("content") is not None else f.get("code"))
    if isinstance(src, str) and src.strip():
        try:
            _, nodes = _cached_ast_nodes(src); has_cg = True
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)): defined_fns.add(node.name); call_graph.setdefault(node.name, set()).update(_collect_fn_calls(node, lset))
                elif isinstance(node, ast.ClassDef): defined_fns.add(node.name)
        except SyntaxError: pass
    defined_fns -= {str(x).strip() for x in (f.get("removed_symbols") or []) if str(x).strip()}
    return has_cg, defined_fns, call_graph

@functools.lru_cache(maxsize=256)
def _parse_ast_imports(code_str: str) -> list[tuple[str, int, bool]]:
    res: list[tuple[str, int, bool]] = []
    try:
        for n in _cached_ast_nodes(code_str)[1]:
            if isinstance(n, ast.ImportFrom):
                lvl, dots = int(n.level or 0), "." * int(n.level or 0)
                res.extend([(f"{dots}{n.module}", lvl, False), *((f"{dots}{n.module}.{a.name}", lvl, True) for a in n.names)] if n.module else [(f"{dots}{a.name}", lvl, False) for a in n.names])
            elif isinstance(n, ast.Import): res.extend((a.name, 0, False) for a in n.names)
    except SyntaxError: pass
    return res

def _collect_dangling_imports(scopes: dict[str, Any], artifacts: list[dict[str, Any]], staged_files: list[dict[str, Any]], staged_diff_report: dict[str, Any]) -> list[dict[str, str]]:
    del_paths = {str(p).strip() for p in (*(staged_diff_report.get("deleted_files") or []), *(staged_diff_report.get("removed_files") or [])) if str(p).strip()}
    rem_files: list[dict[str, Any]] = []
    for f in staged_files:
        p, st = str(f.get("path") or f.get("file") or "").strip(), str(f.get("status") or "M").strip().upper()
        if st.startswith("D") or bool(f.get("deleted")):
            del_paths.update([p] if p else [])
        else:
            rem_files.append(f)
    rem_files.extend(rf if isinstance(rf, dict) else {"path": str(rf)} for rf in (staged_diff_report.get("remaining_files") or staged_diff_report.get("remaining_modules") or []))
    if del_paths:
        seen_p = {str(f.get("path") or f.get("file") or "").strip() for f in rem_files}
        for dp in sorted(del_paths):
            if str(pdir := Path(dp).parent) not in ("", ".") and pdir.is_dir():
                for sib in sorted(pdir.glob("*.py")):
                    if (sp := str(sib)) not in seen_p and not any(_paths_match(sp, d) for d in del_paths):
                        seen_p.add(sp)
                        rem_files.append({"path": sp})
    explicit_known = {str(f.get("path") or f.get("file") or "").strip() for f in rem_files if str(f.get("path") or f.get("file") or "").strip()}
    for ep in (*(staged_diff_report.get("existing_files") or []), *(staged_diff_report.get("package_files") or [])):
        if (eps := str(ep).strip()) and not any(_paths_match(eps, d) for d in del_paths):
            explicit_known.add(eps)
    lattice_known = set(explicit_known)
    for ep in (*(fp for sc in scopes.values() if isinstance(sc, dict) for fp in _extract_scope_files(sc)), *(str(a.get("file") or "") for a in artifacts)):
        if (eps := str(ep).strip()) and not any(_paths_match(eps, d) for d in del_paths):
            lattice_known.add(eps)
    missing_mods = {str(m).strip().lstrip(".") for m in (staged_diff_report.get("missing_modules") or []) if str(m).strip()}
    out, seen_reasons = [], set()

    def _add(fpath: str, mod_str: str, raw_mod: str = "") -> None:
        if (m := str(mod_str or "").strip()) and (reason := f"dangling_import:{fpath}:{m}") not in seen_reasons:
            seen_reasons.add(reason)
            f_sc, alt = _resolve_file_scopes(fpath, "", scopes, artifacts), (f" (dangling_import:{fpath}:{raw_mod})" if raw_mod and raw_mod != m else "")
            out.append({"kind": "CONTRACT_VIOLATION", "scope": (f_sc[0] if f_sc else "global_defaults"), "path": fpath, "file": fpath, "module": m, "reason": reason, "description": f"{reason}{alt}"})

    raw_top = staged_diff_report.get("dangling_imports")
    top_items = [(k, x) for k, v in raw_top.items() for x in (v if isinstance(v, (list, tuple, set)) else [v])] if isinstance(raw_top, dict) else [(None, x) for x in (raw_top if isinstance(raw_top, list) else [])]
    for def_fp, item in [*top_items, *((str(f.get("path") or f.get("file") or "").strip(), x) for f in rem_files for x in (f.get("dangling_imports") or []))]:
        if isinstance(item, dict):
            fp, mod = str(item.get("file") or item.get("path") or item.get("importer") or def_fp or "").strip(), str(item.get("module") or item.get("target") or item.get("import") or item.get("name") or "").strip()
            _add(fp, mod, mod.lstrip("."))
        elif isinstance(item, str) and item.strip():
            s = item.strip().removeprefix("dangling_import:")
            fp, mod = s.split(":", 1) if (":" in s and def_fp is None) else (str(def_fp or (rem_files[0].get("path") or rem_files[0].get("file") if rem_files else "") or "").strip(), s)
            _add(fp, mod, mod.lstrip("."))

    for rf in rem_files:
        fpath, imp_specs = str(rf.get("path") or rf.get("file") or "").strip(), []
        for imp in (*(rf.get("imports") or []), *(rf.get("ast_imports") or []), *(rf.get("relative_imports") or [])):
            if isinstance(imp, dict):
                m, lvl = str(imp.get("module") or imp.get("target") or imp.get("name") or "").strip(), int(imp.get("level") or (1 if imp in (rf.get("relative_imports") or []) else 0))
                imp_specs.append((("." * lvl + m.lstrip(".")) if lvl > 0 else m, max(lvl, len(m) - len(m.lstrip("."))), False))
            elif isinstance(imp, str) and (s := imp.strip()):
                imp_specs.extend(_parse_ast_imports(s) if s.startswith(("from ", "import ")) else [(s, len(s) - len(s.lstrip(".")), False)])
        src = rf.get("source") if rf.get("source") is not None else (rf.get("content") if rf.get("content") is not None else rf.get("code"))
        if src is None and del_paths and fpath and Path(fpath).is_file():
            src = Path(fpath).read_text(encoding="utf-8", errors="ignore")
        if isinstance(src, str) and src.strip():
            imp_specs.extend(_parse_ast_imports(src))
        for raw_mod, lvl, is_sub in imp_specs:
            if not (clean := raw_mod.lstrip(".")):
                continue
            m_py, m_init, base = clean.replace(".", "/") + ".py", clean.replace(".", "/") + "/__init__.py", (Path(fpath).parent if fpath else Path("."))
            for _ in range(max(0, lvl - 1)):
                base = base.parent
            c_py, c_init = (str(base / m_py), str(base / m_init)) if str(base) not in ("", ".") else (m_py, m_init)
            hit_del = any(_paths_match(dp, c_py) or _paths_match(dp, c_init) or _paths_match(dp, m_py) or _paths_match(dp, m_init) or (not is_sub and (Path(dp).parent.name if Path(dp).name == "__init__.py" else Path(dp).stem) == clean and (lvl > 0 or "/" not in dp or (fpath and Path(dp).parent == Path(fpath).parent))) for dp in del_paths)
            hit_miss = False
            if not hit_del and not is_sub:
                if clean in missing_mods or raw_mod in missing_mods:
                    hit_miss = True
                elif lvl == 1 and clean != "__init__":
                    proj_root = Path(os.environ.get("LATTICE_PROJECT_ROOT") or ".")
                    active_known = explicit_known if (str(base) not in ("", ".") and (base.is_dir() or (proj_root / base).is_dir())) else lattice_known
                    in_known = any(_paths_match(kp, c_py) or _paths_match(kp, c_init) or _paths_match(kp, m_py) or _paths_match(kp, m_init) or Path(kp).stem == clean for kp in active_known)
                    on_disk = str(base) not in ("", ".") and ((base / m_py).exists() or (base / m_init).exists() or (proj_root / base / m_py).exists() or (proj_root / base / m_init).exists() or any((Path(str(base).replace(f"/{ed}/", "/core/circuit/")) / p).exists() for ed in ("eng0", "eng1", "eng2", "eng3") if f"/{ed}/" in f"/{base}/" for p in (m_py, m_init)))
                    hit_miss = not (in_known or on_disk)
            if hit_del or hit_miss:
                _add(fpath, clean, raw_mod)
    return out

def verify_ast_call_graph_skeleton(whiteboard_graph: dict[str, Any] | None, staged_diff_report: dict[str, Any]) -> dict[str, Any]:
    """Verifies that each scope's `$ INTERFACE` entrypoints invoke its `% LAW` helper functions in the AST call graph and checks for dangling imports (Stage 27, Law 8)."""
    _, scopes, artifacts, staged_files = _unpack_lattice_and_files(whiteboard_graph, staged_diff_report)
    if not staged_files and any(k in staged_diff_report for k in ("ast_call_graph", "source", "content", "code", "imports", "ast_imports", "relative_imports")):
        staged_files = [staged_diff_report]
    scope_ifaces: dict[str, list[dict[str, Any]]] = {}
    scope_laws: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for art in artifacts:
        akind, asid = str(art.get("kind") or "").upper(), str(art.get("scope") or "").strip()
        if asid and akind == "INTERFACE" and str(art.get("name") or art.get("id") or "").strip():
            scope_ifaces.setdefault(asid, []).append(art)
        elif asid and akind == "LAW" and (lfn := _extract_law_fn_name(art)):
            scope_laws.setdefault(asid, []).append((art, lfn))

    dangling_imports = _collect_dangling_imports(scopes, artifacts, staged_files, staged_diff_report)
    checked_scopes, disconnected_laws, violations, is_valid = [], [], list(dangling_imports), not bool(dangling_imports)
    for sid in sorted(set(scope_ifaces.keys()) & set(scope_laws.keys())):
        ifaces, laws = scope_ifaces[sid], scope_laws[sid]
        law_fn_set = {lfn for _, lfn in laws} | {str(a.get("name") or a.get("id") or "").strip() for a, _ in laws}
        iface_files = {str(a.get("file") or "").strip() for a in ifaces if str(a.get("file") or "").strip()}
        cand_files = [
            f for f in staged_files
            if sid in _resolve_file_scopes(str(f.get("path") or f.get("file") or "").strip(), str(f.get("scope") or f.get("component") or "").strip(), scopes, artifacts)
            or any(_paths_match(str(f.get("path") or f.get("file") or ""), af) for af in iface_files)
            or (not f.get("path") and not f.get("file") and len(scope_ifaces) == 1)
        ]
        cg_files = [f for f in cand_files if _parse_file_call_graph(f, law_fn_set)[0]]
        if not cg_files:
            continue
        proj_root = Path(os.environ.get("LATTICE_PROJECT_ROOT") or ".")
        for raw_sfp in [*_extract_scope_files(scopes.get(sid) or {}), *sorted(iface_files)]:
            sfp = raw_sfp.removeprefix("uri://file/").removeprefix("uri://").removeprefix("file://").removeprefix("./").split("#", 1)[0].split(":", 1)[0].strip()
            if sfp.endswith(".py") and not any(_paths_match(str(cf.get("path") or cf.get("file") or ""), sfp) for cf in cg_files):
                if (dp := (proj_root / sfp) if (proj_root / sfp).is_file() else (Path(sfp) if Path(sfp).is_file() else None)) is not None:
                    cg_files.append({"path": sfp, "source": dp.read_text(encoding="utf-8", errors="ignore")})
        checked_scopes.append(sid)
        defined_fns, merged_cg = set(), {}
        cpath = str(cg_files[0].get("path") or cg_files[0].get("file") or next(iter(iface_files), "")).strip()
        for cf in cg_files:
            _, dfns, cg = _parse_file_call_graph(cf, law_fn_set)
            defined_fns |= dfns
            for k, v in cg.items():
                merged_cg.setdefault(k, set()).update(v)
        iface_names = [str(a.get("name") or a.get("id") or "").strip() for a in ifaces]
        reachable, queue = set(), [fn for fn in iface_names if fn in defined_fns]
        visited = set(queue)
        while queue:
            for callee in merged_cg.get(queue.pop(0), set()):
                reachable.add(callee)
                if callee not in visited and callee in merged_cg:
                    visited.add(callee)
                    queue.append(callee)
        iface_def = bool(iface_names) and all(fn in defined_fns for fn in iface_names)
        for law_art, lfn in laws:
            law_id, lfn_def = str(law_art.get("name") or law_art.get("id") or lfn).strip(), (lfn in defined_fns)
            calls_law = lfn_def and (lfn in reachable or lfn in iface_names)
            if not is_call_graph_skeleton_valid(iface_def, lfn_def, calls_law):
                is_valid = False
                disconnected_laws.append({"scope": sid, "law_id": law_id, "law_fn": lfn, "interfaces": iface_names, "path": cpath, "interface_defined": iface_def, "law_fn_defined": lfn_def, "interface_calls_law": calls_law})
                violations.append({"kind": "CONTRACT_VIOLATION", "scope": sid, "path": cpath, "description": f"LAW function '{lfn}' ({law_id}) in scope '{sid}' is not invoked by INTERFACE {iface_names} in {cpath}"})

    if staged_diff_report.get("call_graph_ok") is False:
        is_valid = False
        if not disconnected_laws and not dangling_imports:
            disconnected_laws.append({"scope": "global_defaults", "law_id": "call_graph_ok", "law_fn": "", "interfaces": [], "path": "", "interface_defined": True, "law_fn_defined": True, "interface_calls_law": False})
            violations.append({"kind": "CONTRACT_VIOLATION", "scope": "global_defaults", "path": "", "description": "Staged diff report flagged call_graph_ok=False"})
    return {"is_valid": is_valid, "disconnected_law_functions": len(disconnected_laws), "disconnected_laws": disconnected_laws, "dangling_imports": dangling_imports, "checked_scopes": checked_scopes, "violations": violations}

def evaluate_git_commit_gate(whiteboard_graph: dict[str, Any] | None, staged_diff_report: dict[str, Any], *, uri_sync_report: dict[str, Any] | None = None) -> GateDecisionResult:
    """Evaluates a staged git commit against the active Whiteboard Lattice (`whiteboard_graph`) without mutating it (Stage 26 & Stage 27)."""
    wb, scopes, artifacts, staged_files = _unpack_lattice_and_files(whiteboard_graph, staged_diff_report)
    eff_uri_sync = uri_sync_report if isinstance(uri_sync_report, dict) else (staged_diff_report.get("uri_sync_report") if isinstance(staged_diff_report.get("uri_sync_report"), dict) else None)
    violations: list[dict[str, Any]] = []
    bounds_ok, contracts_ok, bend_proofs_ok, no_dangling_deletes = True, True, True, True
    raw_mlb = wb.get("max_file_lines") if wb.get("max_file_lines") is not None else staged_diff_report.get("max_file_lines")
    max_lines_bound: int | None = int(raw_mlb) if isinstance(raw_mlb, (int, float)) and not isinstance(raw_mlb, bool) and raw_mlb > 0 else None
    scope_bounds: dict[str, list[tuple[str, str, float, str]]] = {}
    prohibited_tokens: set[str] = set()
    for sid, sc in scopes.items():
        for r in (sc.get("rules") if isinstance(sc, dict) and isinstance(sc.get("rules"), list) else []):
            if not isinstance(r, dict):
                continue
            rtype, rtext, prop = str(r.get("type") or "").upper(), str(r.get("text") or ""), str(r.get("target_prop") or "").strip()
            if (rtype == "BOUND" or r.get("bound_op") is not None) and r.get("bound_val") is not None:
                try:
                    bval, bop = float(r["bound_val"]), str(r.get("bound_op") or "<=").strip()
                    if prop.lower() in ("max_file_lines", "file_lines", "line_count", "lines", "max_lines") or "max_file_lines" in rtext.lower():
                        max_lines_bound = int(bval)
                    elif prop:
                        scope_bounds.setdefault(sid, []).append((prop, bop, bval, rtext or f"{prop} {bop} {bval}"))
                except (TypeError, ValueError):
                    pass
            if rtype == "PROHIBITION" or any(w in rtext.lower() for w in ("forbid", "never", "prohibit")):
                prohibited_tokens.update((["re"] if (any(tok in rtext.lower().replace(",", " ").replace("`", " ").split() for tok in ("re", "regex")) or "import re " in (rtext.lower() + " ")) else []) + ([prop.lower()] if prop else []) + [t for t in ("regiment_nlp", "spacy", "torch", "onnxruntime") if t in rtext.lower()])

    modified_scope_files: dict[str, list[dict[str, Any]]] = {}
    deleted_paths: set[str] = {str(p).strip() for p in (staged_diff_report.get("deleted_files") or []) if str(p).strip()}
    for f in staged_files:
        path, status = str(f.get("path") or f.get("file") or "").strip(), str(f.get("status") or "M").strip().upper()
        if status.startswith("D") or bool(f.get("deleted")):
            deleted_paths.update([path] if path else []); continue
        eff_imps = list(f.get("imports") or [])
        if isinstance(src := (f.get("source") if f.get("source") is not None else (f.get("content") if f.get("content") is not None else f.get("code"))), str) and src.strip():
            if f.get("imports") is None: eff_imps = [imp.lstrip(".") for imp, _, is_sub in _parse_ast_imports(src) if not is_sub and imp.lstrip(".")]
            try:
                tree, t_nodes = _cached_ast_nodes(src)
                f_keys = {s.value for s in t_nodes if isinstance(s, ast.Constant) and isinstance(s.value, str) and s.value.isidentifier()}
                if f.get("ast_constants") is None: f["ast_constants"] = {t.id.lower(): float(n.value.value) for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, (int, float)) and not isinstance(n.value.value, bool) for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
                if f.get("ast_functions") is None:
                    fns: dict[str, list[str]] = {}
                    for n in (x for x in t_nodes if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))): fns.setdefault(n.name, []).extend([a.arg for a in (*n.args.posonlyargs, *n.args.args, *n.args.kwonlyargs) if a.arg != "self"] + (["args"] if n.args.vararg else []) + (["kwargs"] if n.args.kwarg else []))
                    f["ast_functions"] = fns
                if f.get("ast_classes") is None and f.get("ast_schemas") is None:
                    clss = {n.name: ({s.target.id for s in n.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)} | {t.id for s in n.body if isinstance(s, ast.Assign) for t in s.targets if isinstance(t, ast.Name)} | {a.arg for sub in ast.walk(n) if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) for a in (*sub.args.posonlyargs, *sub.args.args, *sub.args.kwonlyargs) if a.arg != "self"} | {sub.value for sub in ast.walk(n) if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value.isidentifier()} | (f_keys if any((isinstance(b, ast.Name) and b.id in ("dict", "_AttrDict", "TypedDict", "_TableBaseDict")) or (isinstance(b, ast.Subscript) and isinstance(b.value, ast.Name) and b.value.id in ("dict", "_AttrDict")) for b in n.bases) else set())) for n in t_nodes if isinstance(n, ast.ClassDef)}
                    for n in (x for x in tree.body if isinstance(x, ast.Assign) and isinstance(x.value, ast.Name) and x.value.id in clss):
                        for t in (x for x in n.targets if isinstance(x, ast.Name)): clss[t.id] = clss[n.value.id]
                    f["ast_classes"] = {k: sorted(v) for k, v in clss.items()}
            except SyntaxError: pass
        f_scopes = _resolve_file_scopes(path, str(f.get("scope") or f.get("component") or "").strip(), scopes, artifacts)
        cur_hash, top_hashes = str(f.get("file_hash") or "").strip(), (wb.get("file_hashes") if isinstance(wb.get("file_hashes"), dict) else {})
        for s in f_scopes:
            modified_scope_files.setdefault(s, []).append(f)
            sc = scopes.get(s) if isinstance(scopes.get(s), dict) else {}
            sc_hashes = sc.get("file_hashes") if isinstance(sc.get("file_hashes"), dict) else top_hashes
            base_h = next((str(v).strip() for k, v in sc_hashes.items() if _paths_match(k, path) and str(v).strip()), "")
            still_ver = str(sc.get("status") or sc.get("build_status") or "").strip().upper() == "VERIFIED"
            if not (is_verified_scope_protected(bool(cur_hash and base_h and cur_hash != base_h), still_ver) and verified_scope_edit_guard_law(bool(cur_hash and base_h and cur_hash != base_h), still_ver)):
                contracts_ok = False
                violations.append({"kind": "CONTRACT_VIOLATION", "scope": s, "path": path, "description": f"{path} modified (file_hash {cur_hash} != baseline {base_h}) while component '{s}' is still VERIFIED; run lattice_turn first to mark '{s}' STALE"})
        line_cnt = len(raw_lc) if isinstance(raw_lc := (f.get("line_count") if f.get("line_count") is not None else f.get("lines")), list) else raw_lc
        f_raw_mlb = f.get("max_file_lines") if f.get("max_file_lines") is not None else max_lines_bound
        f_max_lines = int(f_raw_mlb) if isinstance(f_raw_mlb, (int, float)) and not isinstance(f_raw_mlb, bool) and f_raw_mlb > 0 else None
        if f_max_lines is not None and isinstance(line_cnt, (int, float)) and not isinstance(line_cnt, bool) and line_cnt > f_max_lines:
            bounds_ok = False; violations.append({"kind": "BOUND_VIOLATION", "scope": (f_scopes[0] if f_scopes else "global_defaults"), "path": path, "description": f"{path} line_count ({int(line_cnt)}) exceeds max_file_lines bound ({f_max_lines})"})
        raw_consts = f.get("ast_constants")
        consts_map = {str(k).strip().lower(): v for k, v in raw_consts.items()} if isinstance(raw_consts, dict) else ({str(c.get("name") or "").strip().lower(): c.get("value") for c in raw_consts if isinstance(c, dict) and c.get("name")} if isinstance(raw_consts, list) else {})
        for check_sid in ["global_defaults", *f_scopes]:
            for b_prop, b_op, b_val, b_desc in scope_bounds.get(check_sid, []):
                cand_val = consts_map.get(b_prop.lower(), f.get(b_prop))
                if isinstance(cand_val, (int, float)) and not isinstance(cand_val, bool) and not _check_numeric_bound(float(cand_val), b_op, b_val):
                    bounds_ok = False; violations.append({"kind": "BOUND_VIOLATION", "scope": check_sid, "path": path, "description": f"{path} constant {b_prop}={cand_val} violates bound ({b_desc})"})
        bad_imps = [str(x).strip() for x in (f.get("prohibited_imports") or []) if str(x).strip()] + [str(imp).strip() for imp in eff_imps if str(imp).strip().replace("import ", "").split(".")[0].strip().lower() in prohibited_tokens or str(imp).strip().lower() in prohibited_tokens]
        if bad_imps:
            bounds_ok = False; violations.append({"kind": "PROHIBITION_VIOLATION", "scope": (f_scopes[0] if f_scopes else "global_defaults"), "path": path, "description": f"{path} contains prohibited imports: {', '.join(dict.fromkeys(bad_imps))}"})

    if staged_diff_report.get("bounds_ok") is False:
        bounds_ok = False
        if not any(v["kind"] in ("BOUND_VIOLATION", "PROHIBITION_VIOLATION") for v in violations):
            violations.append({"kind": "BOUND_VIOLATION", "scope": "global_defaults", "path": "", "description": "Staged diff report flagged bounds_ok=False"})

    decl_syms = {str(a.get("name") or a.get("id") or "").strip() for a in artifacts if str(a.get("kind") or "").upper() in ("INTERFACE", "SCHEMA")} | {_extract_law_fn_name(a) for a in artifacts if str(a.get("kind") or "").upper() == "LAW"} | {str(a.get("name") or a.get("id") or "").strip() for a in artifacts if str(a.get("kind") or "").upper() == "LAW"}
    unreg_syms = [{"scope": s, "path": str(f.get("path") or ""), "symbol": sym} for s, s_files in modified_scope_files.items() if str((scopes.get(s) or {}).get("status") or (scopes.get(s) or {}).get("build_status") or "").strip().upper() != "VERIFIED" or any(not f.get("file_hash") for f in s_files) for f in s_files for sym in sorted(_normalize_symbol_map(f.get("ast_functions"))[0] | _normalize_symbol_map(f.get("ast_classes") if "ast_classes" in f else f.get("ast_schemas"))[0]) if sym and not sym.startswith("_") and sym != "main" and sym not in decl_syms][:25]
    flow_schemas = [str(fl.get("payload") or "").strip() for fl in (wb.get("flows") or []) if isinstance(fl, dict) and any(str(a.get("kind") or "").upper() == "SCHEMA" and str(a.get("name") or a.get("id") or "").lower() == str(fl.get("payload") or "").strip().lower() for a in artifacts)]

    for art in (a for a in artifacts if str(a.get("kind") or "").upper() in ("INTERFACE", "SCHEMA")):
        akind, asid, aname, afile = str(art.get("kind") or "").upper(), str(art.get("scope") or "").strip(), str(art.get("name") or art.get("id") or "").strip(), str(art.get("file") or "").strip()
        req_inputs = [str(x).split(":", 1)[0].split("=", 1)[0].strip().lstrip("*") for x in (art.get("inputs") or art.get("in") or art.get("fields") or []) if str(x).strip()]
        cand_cfiles = [f for f in staged_files if (afile and _paths_match(str(f.get("path") or ""), afile))] or modified_scope_files.get(asid, [])
        for cf in cand_cfiles:
            cpath, removed = str(cf.get("path") or afile or "").strip(), {str(x).strip() for x in (cf.get("removed_symbols") or []) if str(x).strip()}
            if aname and aname in removed:
                contracts_ok = False; violations.append({"kind": "CONTRACT_VIOLATION", "scope": asid, "path": cpath, "description": f"Required {akind} '{aname}' was removed from {cpath}"})
            has_fn_ast, has_cls_ast = ("ast_functions" in cf and cf["ast_functions"] is not None), (("ast_classes" in cf and cf["ast_classes"] is not None) or ("ast_schemas" in cf and cf["ast_schemas"] is not None))
            (fn_names, fn_params), (cls_names, cls_fields) = _normalize_symbol_map(cf.get("ast_functions")), _normalize_symbol_map(cf.get("ast_classes") if "ast_classes" in cf else cf.get("ast_schemas"))
            all_names = fn_names | cls_names
            if (akind == "INTERFACE" and has_fn_ast) or (akind == "SCHEMA" and (has_cls_ast or has_fn_ast)):
                if aname not in all_names and (afile or len(cand_cfiles) == 1):
                    contracts_ok = False; violations.append({"kind": "CONTRACT_VIOLATION", "scope": asid, "path": cpath, "description": f"Required {akind} '{aname}' missing from {cpath}"})
                elif akind == "INTERFACE" and req_inputs and aname in fn_params and not any(k in fn_params[aname] for k in ("kwargs", "args")) and (missing_in := [inp for inp in req_inputs if inp not in fn_params[aname]]):
                    contracts_ok = False; violations.append({"kind": "CONTRACT_VIOLATION", "scope": asid, "path": cpath, "description": f"INTERFACE '{aname}' in {cpath} missing required inputs: {missing_in}"})
                elif akind == "SCHEMA" and req_inputs and aname in cls_fields and (missing_f := [fld for fld in req_inputs if fld not in cls_fields[aname]]):
                    contracts_ok = False; violations.append({"kind": "CONTRACT_VIOLATION", "scope": asid, "path": cpath, "description": f"SCHEMA '{aname}' in {cpath} missing required fields: {missing_f}"})

    if not (is_verified_scope_protected(False, False) and verified_scope_edit_guard_law(False, False)): contracts_ok = False
    has_direct_contract_err = not contracts_ok
    cg_report = verify_ast_call_graph_skeleton(whiteboard_graph, staged_diff_report)
    if not cg_report["is_valid"]:
        contracts_ok = False; violations.extend(cg_report["violations"])
    if staged_diff_report.get("contracts_ok") is False:
        contracts_ok = False
        if not any(v["kind"] == "CONTRACT_VIOLATION" for v in violations):
            violations.append({"kind": "CONTRACT_VIOLATION", "scope": "global_defaults", "path": "", "description": "Staged diff report flagged contracts_ok=False"})

    failed_laws = list(staged_diff_report.get("failed_laws") or staged_diff_report.get("bend_failures") or [])
    if not bool(staged_diff_report.get("bend_proofs_ok", True)) or failed_laws:
        bend_proofs_ok = False; violations.append({"kind": "BEND_PROOF_FAILURE", "scope": "global_defaults", "path": "", "description": f"Bend 2 proof verification failed: {failed_laws or 'bend_proofs_ok=False'}"})

    healed_old_paths: set[str] = {str(ren["old_uri"]).strip() for ren in ((eff_uri_sync.get("renamed_uris") or eff_uri_sync.get("healed_renames") or []) if isinstance(eff_uri_sync, dict) else []) if isinstance(ren, dict) and ren.get("old_uri")}
    if isinstance(eff_uri_sync, dict) and (bool(eff_uri_sync.get("has_dangling_deleted_uri")) or bool(eff_uri_sync.get("has_dangling_delete")) or eff_uri_sync.get("is_valid") is False or eff_uri_sync.get("dangling_uris") or eff_uri_sync.get("dangling_deleted_uris")):
        no_dangling_deletes = False
        for duri in (eff_uri_sync.get("dangling_deleted_uris") or eff_uri_sync.get("dangling_uris") or [""]):
            d_str = str(duri.get("uri") if isinstance(duri, dict) else duri).strip()
            violations.append({"kind": "DANGLING_DELETED_URI", "scope": "global_defaults", "path": d_str, "description": f"URI sync report detected dangling deleted URI: {d_str or 'has_dangling_deleted_uri=True'}"})

    for dpath in (dp for dp in sorted(deleted_paths) if not any(_paths_match(dp, hp) for hp in healed_old_paths)):
        for sid, sc in scopes.items():
            if isinstance(sc, dict) and any(_paths_match(dpath, fp) for fp in _extract_scope_files(sc)):
                no_dangling_deletes = False; violations.append({"kind": "DANGLING_DELETED_URI", "scope": sid, "path": dpath, "description": f"Deleted file '{dpath}' is still referenced by active scope '{sid}'"})
        for art in artifacts:
            if art.get("file") and _paths_match(dpath, str(art["file"])):
                no_dangling_deletes = False; violations.append({"kind": "DANGLING_DELETED_URI", "scope": str(art.get("scope") or "global_defaults"), "path": dpath, "description": f"Deleted file '{dpath}' is still referenced by lattice artifact '{art.get('name') or art.get('id')}'"})

    if staged_diff_report.get("no_dangling_deletes") is False:
        no_dangling_deletes = False
        if not any(v["kind"] == "DANGLING_DELETED_URI" for v in violations):
            violations.append({"kind": "DANGLING_DELETED_URI", "scope": "global_defaults", "path": "", "description": "Staged diff report flagged no_dangling_deletes=False"})

    allowed = should_allow_git_commit(bounds_ok, contracts_ok, bend_proofs_ok, no_dangling_deletes)
    non_del_contract_err = has_direct_contract_err or bool(cg_report["disconnected_laws"]) or staged_diff_report.get("contracts_ok") is False or (bool(cg_report["dangling_imports"]) and no_dangling_deletes)
    bounce_reason = BOUNCE_NONE if allowed else (BOUNCE_RULE_CONFLICT if (not bounds_ok or non_del_contract_err) else (BOUNCE_BEND_PROOF_FAILURE if not bend_proofs_ok else BOUNCE_DANGLING_DELETED_URI))
    return GateDecisionResult({"allowed": allowed, "decision": DECISION_ALLOW_COMMIT if allowed else DECISION_REJECT_COMMIT, "bounce_reason": bounce_reason, "bounds_ok": bounds_ok, "contracts_ok": contracts_ok, "bend_proofs_ok": bend_proofs_ok, "no_dangling_deletes": no_dangling_deletes, "call_graph_report": cg_report, "dangling_imports": cg_report["dangling_imports"], "disconnected_law_functions": cg_report["disconnected_law_functions"], "unregistered_public_symbols": unreg_syms, "flow_schema_checked": flow_schemas, "unverified_bound_violations": 0 if allowed else len(violations), "violations": violations})
