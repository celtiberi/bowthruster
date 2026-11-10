"""Deterministic Markdown Pipe-Table `IntakePacket` Compiler (`bowthruster.regiment.intake_packet`)."""

from __future__ import annotations

import copy
import math
from typing import Any

from .markdown_tables import (
    CANONICAL_HEADERS_ERROR_MSG,
    CANONICAL_PIPE_TABLE_HEADERS,
    TableParserState,
    TableScanMode,
    _clean_cell_val,
    _decompose_rule_expr,
    _is_separator_row,
    _norm_col_key,
    _parse_scalar_val,
    _split_list_cell,
    _split_pipe_row,
    build_mcus_from_tables_and_flows,
    extract_flow_sentences,
    extract_markdown_tables,
    extract_structured_table_rows,
)


def _num_bounds(op: str | None, val: float) -> tuple[float, bool, float, bool, set[float]]:
    """Maps `(op, val)` to `(low, low_closed, high, high_closed, neq_set)`."""
    if op == "<=":
        return (-math.inf, True, val, True, set())
    if op == "<":
        return (-math.inf, True, val, False, set())
    if op == ">=":
        return (val, True, math.inf, True, set())
    if op == ">":
        return (val, False, math.inf, True, set())
    if op == "==":
        return (val, True, val, True, set())
    if op == "!=":
        return (-math.inf, True, math.inf, True, {val})
    return (-math.inf, True, math.inf, True, set())


def _interval_subsumes(op_a: str | None, va: float, op_b: str | None, vb: float) -> bool:
    """Returns `True` iff numeric interval `(op_b, vb)` is a subset of `(op_a, va)`."""
    la, lac, ua, uac, neq_a = _num_bounds(op_a, va)
    lb, lbc, ub, ubc, neq_b = _num_bounds(op_b, vb)
    low_ok = (lb > la) or (lb == la and (lac or not lbc))
    high_ok = (ub < ua) or (ub == ua and (uac or not ubc))
    if not (low_ok and high_ok):
        return False
    for ex in neq_a:
        in_b = ((ex > lb) or (ex == lb and lbc)) and ((ex < ub) or (ex == ub and ubc)) and (ex not in neq_b)
        if in_b:
            return False
    return True


def _parse_cond_atoms(conds: Any) -> tuple[dict[str, list[tuple[str, Any]]], set[str]]:
    """Parses condition strings into `(var -> [(op, val)], unparsed_raw_atoms)` without `re`."""
    atoms: dict[str, list[tuple[str, Any]]] = {}
    raw: set[str] = set()
    for c in (conds or []):
        s = str(c).strip()
        if not s or s.lower() in ("none", "-", "—", "n/a", "null", "unconditional", "always"):
            continue
        parts = [s]
        for sep in (" and ", " AND ", "&&", ";"):
            parts = [sub for p in parts for sub in p.split(sep)]
        for p in parts:
            cl = p.strip().strip("()").strip()
            for pfx in ("when ", "if "):
                if cl.lower().startswith(pfx):
                    cl = cl[len(pfx) :].strip()
            if not cl:
                continue
            norm = cl.replace(" is not ", " != ").replace(" is ", " == ")
            matched = False
            for op in ("<=", ">=", "!=", "==", "<", ">", "="):
                if op in norm:
                    lhs, _, rhs = norm.partition(op)
                    var = "_".join(lhs.strip().strip("\"`'").lower().split())
                    val = _parse_scalar_val(rhs.strip())
                    if var and val is not None:
                        atoms.setdefault(var, []).append(("==" if op == "=" else op, val))
                        matched = True
                    break
            if not matched:
                raw.add(cl.lower())
    return atoms, raw


def _conditions_satisfiable(conds_a: Any, conds_b: Any) -> bool:
    """Returns `True` iff condition sets `conds_a` and `conds_b` can be simultaneously satisfied."""
    atoms_a, raw_a = _parse_cond_atoms(conds_a)
    atoms_b, raw_b = _parse_cond_atoms(conds_b)
    if not atoms_a and not atoms_b:
        return bool(raw_a & raw_b) if (raw_a and raw_b) else True
    for var in set(atoms_a) & set(atoms_b):
        constraints = atoms_a[var] + atoms_b[var]
        for i, (op1, v1) in enumerate(constraints):
            n1 = isinstance(v1, (int, float)) and not isinstance(v1, bool)
            for op2, v2 in constraints[i + 1 :]:
                n2 = isinstance(v2, (int, float)) and not isinstance(v2, bool)
                if n1 and n2:
                    l1, l1c, u1, u1c, neq1 = _num_bounds(op1, float(v1))
                    l2, l2c, u2, u2c, neq2 = _num_bounds(op2, float(v2))
                    low, lc = (l1, l1c) if l1 > l2 else ((l2, l2c) if l2 > l1 else (l1, l1c and l2c))
                    high, hc = (u1, u1c) if u1 < u2 else ((u2, u2c) if u2 < u1 else (u1, u1c and u2c))
                    if low > high or (low == high and (not (lc and hc) or low in (neq1 | neq2))):
                        return False
                else:
                    s1, s2 = str(v1).strip().lower(), str(v2).strip().lower()
                    if (op1 == "==" and op2 == "==" and s1 != s2) or ({op1, op2} == {"==", "!="} and s1 == s2):
                        return False
    return True


class Phase3InvariantError(ValueError):
    """Raised when an `IntakePacket` input violates deterministic schema invariants."""

    def __init__(self, code: str, errors: list[str]) -> None:
        self.code = code
        self.errors = list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


PARSED_MCU_KEYS = frozenset(
    {
        "mcu_id",
        "head_id",
        "source_chunk_ids",
        "text",
        "canonical_text",
        "type",
        "facet",
        "primary_scope",
        "secondary_refs",
        "target_prop",
        "bound_op",
        "bound_val",
        "conditions",
        "is_exception",
        "superseded_literals",
        "meta_keys",
        "meta",
        "unclear_spans",
        "grounding_status",
    }
)


class IntakePacket(dict[str, Any]):
    """Structured `IntakePacket` schema (`turn_index`, `doc_tables`, `parsed_mcus`)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        self.setdefault("turn_index", 1)
        self.setdefault("doc_tables", [])
        self.setdefault("parsed_mcus", [])
        self.setdefault("structured_table_rows", [])
        self.setdefault("grounding_status", "GROUNDED")
        self.setdefault("is_locked", True)
        self.setdefault("clarification_questions", [])
        self.setdefault("open_questions", [])


def compile_parsed_mcus(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Compiles a `result` dict into `parsed_mcus` using deterministic table/flow extraction."""
    if not isinstance(result, dict):
        raise Phase3InvariantError("intake_invalid_phase3_result", ["result must be a dict"])
    if isinstance(result.get("parsed_mcus"), list) and result["parsed_mcus"]:
        return copy.deepcopy(result["parsed_mcus"])
    raw_text = str(result.get("raw_text") or "")
    tables = (
        result.get("doc_tables")
        if isinstance(result.get("doc_tables"), list)
        else extract_markdown_tables(raw_text)
    )
    srows = (
        result.get("structured_table_rows")
        if isinstance(result.get("structured_table_rows"), list)
        else extract_structured_table_rows(tables)
    )
    flow_sents = extract_flow_sentences(raw_text)
    if srows or flow_sents:
        return build_mcus_from_tables_and_flows(srows, flow_sents)
    if isinstance(result.get("mcus"), list) and isinstance(result.get("propositions"), list):
        if len(result["mcus"]) != len(result["propositions"]):
            raise Phase3InvariantError(
                "intake_invalid_phase3_result", ["equal-length 'mcus' and 'propositions' required"]
            )
        out: list[dict[str, Any]] = []
        for idx, (m, p) in enumerate(zip(result["mcus"], result["propositions"]), 1):
            txt = str(next((_v for _v in (p.get("canonical_text"), m.get("text")) if _v), None) or "").strip()
            cat = str(p.get("canonical_category", m.get("canonical_category")) or "CONTEXT").upper()
            has_num_lit = any(
                l.get("kind") == "NUM" for l in (m.get("required_literals") or []) if isinstance(l, dict)
            )
            if cat == "RULE" and has_num_lit:
                ntype = "BOUND"
            elif cat == "RULE":
                ntype = "OBLIGATION"
            elif cat == "ACTION":
                ntype = "ACTION"
            elif cat == "QUESTION":
                ntype = "QUESTION"
            else:
                ntype = "FACT"
            facet_map = {
                "BOUND": "Numeric_Bounds",
                "OBLIGATION": "obligations",
                "ACTION": "actions",
            }
            out.append(
                {
                    "mcu_id": int(m.get("mcu_id", idx)),
                    "head_id": int(m.get("head_id", idx)),
                    "source_chunk_ids": list(m.get("source_chunk_ids", [idx])),
                    "text": txt,
                    "canonical_text": txt,
                    "type": ntype,
                    "facet": facet_map.get(ntype, "context_facts"),
                    "primary_scope": str(m.get("primary_scope") or "global_defaults"),
                    "secondary_refs": list(m.get("secondary_refs", [])),
                    "target_prop": m.get("target_prop"),
                    "bound_op": m.get("bound_op"),
                    "bound_val": m.get("bound_val"),
                    "conditions": list(m.get("conditions", [])),
                    "is_exception": bool(m.get("is_exception", False)),
                    "superseded_literals": copy.deepcopy(m.get("superseded_literals", [])),
                    "meta_keys": [],
                    "meta": {},
                    "unclear_spans": list(p.get("unclear_spans", [])),
                    "grounding_status": "NEEDS_CLARIFICATION" if p.get("unclear_spans") else "GROUNDED",
                }
            )
        return out
    return []


def compile_markdown_turn(raw_text: str, *, turn_index: int = 1) -> IntakePacket:
    """Deterministically compiles Markdown pipe tables in `raw_text` into an `IntakePacket` (`< 5ms`, zero LLMs)."""
    if not isinstance(raw_text, str):
        raise Phase3InvariantError(
            "intake_invalid_raw_text", [f"raw_text must be a str, got {type(raw_text).__name__}"]
        )
    tables = extract_markdown_tables(raw_text)
    flow_sents = extract_flow_sentences(raw_text)
    if not (tables or flow_sents):
        raise ValueError(CANONICAL_HEADERS_ERROR_MSG)
    structured_rows = extract_structured_table_rows(tables)
    parsed_mcus = build_mcus_from_tables_and_flows(structured_rows, flow_sents)
    return IntakePacket(
        {
            "turn_index": int(turn_index),
            "raw_text": raw_text,
            "doc_tables": tables,
            "structured_table_rows": structured_rows,
            "parsed_mcus": parsed_mcus,
            "grounding_status": "GROUNDED",
            "is_locked": True,
            "clarification_questions": [],
            "open_questions": [],
        }
    )


def compile_intake_packet(
    phase3_result: dict[str, Any] | str,
    *,
    doc_tables: list[dict[str, Any]] | None = None,
    turn_index: int = 1,
) -> IntakePacket:
    """Compiles a Markdown pipe-table turn or `Phase3Result` dict into an `IntakePacket`."""
    if not isinstance(turn_index, int) or isinstance(turn_index, bool) or turn_index < 1:
        raise Phase3InvariantError(
            "intake_invalid_turn_index", [f"turn_index must be a positive int, got {turn_index!r}"]
        )
    if isinstance(phase3_result, str):
        return compile_markdown_turn(phase3_result, turn_index=turn_index)
    if not isinstance(phase3_result, dict):
        raise Phase3InvariantError("intake_invalid_phase3_result", ["phase3_result must be a dict or str"])
    if "grounding_status" not in phase3_result or "is_locked" not in phase3_result:
        if isinstance(phase3_result.get("raw_text"), str) and "mcus" not in phase3_result:
            return compile_markdown_turn(phase3_result["raw_text"], turn_index=turn_index)
        raise Phase3InvariantError(
            "intake_invalid_phase3_result", ["result must contain 'grounding_status' and 'is_locked'"]
        )

    raw_text = str(phase3_result.get("raw_text") or "")
    if doc_tables is None:
        tables = (
            copy.deepcopy(phase3_result["doc_tables"])
            if isinstance(phase3_result.get("doc_tables"), list)
            else extract_markdown_tables(raw_text)
        )
    else:
        tables = copy.deepcopy(list(doc_tables))
    flow_sents = extract_flow_sentences(raw_text)
    has_tables_or_mcus = bool(
        tables or flow_sents or phase3_result.get("mcus", phase3_result.get("parsed_mcus"))
    )
    if raw_text.strip() and not has_tables_or_mcus:
        raise ValueError(CANONICAL_HEADERS_ERROR_MSG)
    structured_rows = extract_structured_table_rows(tables)
    parsed_mcus = compile_parsed_mcus(
        {**phase3_result, "doc_tables": tables, "structured_table_rows": structured_rows}
    )
    return IntakePacket(
        {
            "turn_index": int(turn_index),
            "doc_tables": tables,
            "structured_table_rows": structured_rows,
            "parsed_mcus": parsed_mcus,
            "grounding_status": phase3_result["grounding_status"],
            "is_locked": bool(phase3_result["is_locked"]),
            "clarification_questions": [
                str(q).strip() for q in phase3_result.get("clarification_questions", []) if str(q).strip()
            ],
            "open_questions": [
                str(q).strip() for q in phase3_result.get("open_questions", []) if str(q).strip()
            ],
        }
    )


__all__ = [
    "CANONICAL_HEADERS_ERROR_MSG",
    "CANONICAL_PIPE_TABLE_HEADERS",
    "IntakePacket",
    "Phase3InvariantError",
    "TableParserState",
    "TableScanMode",
    "_clean_cell_val",
    "_conditions_satisfiable",
    "_decompose_rule_expr",
    "_interval_subsumes",
    "_is_separator_row",
    "_norm_col_key",
    "_parse_cond_atoms",
    "_parse_scalar_val",
    "_split_list_cell",
    "_split_pipe_row",
    "build_mcus_from_tables_and_flows",
    "compile_intake_packet",
    "compile_markdown_turn",
    "compile_parsed_mcus",
    "extract_flow_sentences",
    "extract_markdown_tables",
    "extract_structured_table_rows",
]
