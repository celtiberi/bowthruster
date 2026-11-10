"""Deterministic Markdown Pipe-Table & Flow Sentence Parser (`bowthruster.regiment.markdown_tables`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any

from bowthruster.nlp import restore_ascii_dots
from bowthruster.regiment.table_mcu_builder import (
    _EMPTY_TOKENS,
    _base_mcu,
    _build_artifact_or_charter_mcu,
    _build_rule_mcu_from_row,
    _clean_cell_val,
    _compile_component_row,
    _decompose_rule_expr,
    _norm_col_key,
    _parse_scalar_val,
    _pick,
    _split_list_cell,
    _split_test_paths,
    build_mcus_from_tables_and_flows,
)

CANONICAL_PIPE_TABLE_HEADERS: tuple[str, ...] = (
    "| Charter | Summary | Principles |",
    "| Component | Role | Owner | Stage | Depends_On | Files |",
    "| Flow | Source | Target | Payload | Condition |",
    "| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |",
    "| Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |",
    "| Schema | Component | Fields | Owner | Stage | File | Spec |",
    "| Rule | Component | Type | Property | Op | Value | Condition |",
)
CANONICAL_HEADERS_ERROR_MSG = (
    "Input contains no valid Markdown pipe tables (or deterministic FLOW sentences); "
    "expected one or more canonical pipe tables:\n  - "
    + "\n  - ".join(CANONICAL_PIPE_TABLE_HEADERS)
)

_FLOW_SENTENCE_RE = re.compile(
    r"^(?:During\s+[^,]+,\s*)?(?:The\s+)?`?([A-Za-z0-9_:-]+)`?(?:\s+component|\s+module)?\s+"
    r"(?:sends|returns|routes|passes|emits|forwards|delivers)\s+"
    r"`?([A-Za-z0-9_.,\s/-]+?)`?\s+to\s+(?:the\s+)?`?([A-Za-z0-9_:-]+)`?(?:\s+component|\s+module)?"
    r"(?:\s+when\s+(.+?))?\.?$",
    re.IGNORECASE,
)
_FLOW_ARROW_RE = re.compile(
    r"^(?:>\s*FLOW\s+)?`?([A-Za-z0-9_:-]+)`?\s*--\[([^\]]*)\]-->\s*`?([A-Za-z0-9_:-]+)`?(?:\s*:\s*(.*))?$"
)


class TableScanMode(str, Enum):
    SCANNING = "SCANNING"
    HEADER_CANDIDATE = "HEADER_CANDIDATE"
    IN_TABLE_ROWS = "IN_TABLE_ROWS"


@dataclass
class TableParserState:
    mode: TableScanMode = TableScanMode.SCANNING
    heading_stack: list[tuple[int, str]] = field(default_factory=list)
    current_heading_path: str | None = None
    candidate_headers: list[str] = field(default_factory=list)
    current_rows: list[dict[str, str]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)

    def update_heading(self, lvl: int, title: str) -> None:
        while self.heading_stack and self.heading_stack[-1][0] >= lvl:
            self.heading_stack.pop()
        if title:
            self.heading_stack.append((lvl, title))
        self.current_heading_path = (
            " > ".join(t for _, t in self.heading_stack) if self.heading_stack else None
        )

    def flush_table(self) -> None:
        if (
            self.mode == TableScanMode.IN_TABLE_ROWS
            and any(self.candidate_headers)
            and self.current_rows
        ):
            self.tables.append({
                "headers": list(self.candidate_headers),
                "rows": list(self.current_rows),
                "heading_path": self.current_heading_path,
            })
        self.mode = TableScanMode.SCANNING
        self.candidate_headers = []
        self.current_rows = []


def _split_pipe_row(line: str) -> list[str]:
    inner = line.strip()[1:-1].replace("\\|", "\x00")
    return [c.replace("\x00", "|").strip() for c in inner.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(bool(c) and "-" in c and not c.strip(" :-") for c in cells)


def _is_pipe_line(s: str) -> bool:
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _dedupe_headers(raw_cells: list[str]) -> list[str]:
    out, seen = [], set()
    for c in raw_cells:
        h = restore_ascii_dots(c).strip().strip("\"`'").strip()
        if (nk := _norm_col_key(h)) and nk in seen:
            h = f"{h}_spec"
        if nk:
            seen.add(nk)
        out.append(h)
    return out


def extract_markdown_tables(raw_text: str) -> list[dict[str, Any]]:
    """Extracts Markdown pipe tables and their active `#` `heading_path` from `raw_text`."""
    if not isinstance(raw_text, str):
        raise ValueError(f"raw_text must be a str, got {type(raw_text).__name__}")
    state = TableParserState()
    for line in raw_text.splitlines():
        s = line.strip()
        if _is_pipe_line(s):
            cells = _split_pipe_row(s)
            if state.mode == TableScanMode.SCANNING:
                state.candidate_headers = _dedupe_headers(cells)
                state.mode = TableScanMode.HEADER_CANDIDATE
            elif state.mode == TableScanMode.HEADER_CANDIDATE:
                if state.candidate_headers and _is_separator_row(cells):
                    state.current_rows = []
                    state.mode = TableScanMode.IN_TABLE_ROWS
                else:
                    state.candidate_headers = _dedupe_headers(cells)
            elif state.mode == TableScanMode.IN_TABLE_ROWS and not _is_separator_row(cells):
                row_dict = {
                    state.candidate_headers[col_idx]: restore_ascii_dots(cells[col_idx].strip())
                    for col_idx in range(min(len(state.candidate_headers), len(cells)))
                    if state.candidate_headers[col_idx]
                }
                if any(v for v in row_dict.values()):
                    state.current_rows.append(row_dict)
            continue
        state.flush_table()
        stripped_lead = line.lstrip()
        if stripped_lead.startswith("#"):
            lvl = len(stripped_lead) - len(stripped_lead.lstrip("#"))
            if 1 <= lvl <= 6 and len(stripped_lead) > lvl and stripped_lead[lvl] == " ":
                title = restore_ascii_dots(stripped_lead[lvl + 1 :].strip()).strip("#").strip()
                state.update_heading(lvl, title)
    state.flush_table()
    return state.tables


def extract_flow_sentences(raw_text: str) -> list[dict[str, Any]]:
    """Extracts deterministic flow sentences outside pipe tables."""
    if not isinstance(raw_text, str):
        return []
    flows: list[dict[str, Any]] = []
    for raw_line in raw_text.splitlines():
        s = raw_line.strip()
        if not s or s.startswith(("|", "#")):
            continue
        for sent in [p.strip() for p in re.split(r"(?<=\.)\s+", s) if p.strip()]:
            if m := (_FLOW_ARROW_RE.match(sent) or _FLOW_SENTENCE_RE.match(sent)):
                src, pay = m.group(1).strip(), (m.group(2).strip() or None)
                tgt, cond = m.group(3).strip(), ((m.group(4) or "").strip() or None)
                flows.append({"source": src, "target": tgt, "payload": pay, "condition": cond, "text": sent})
    return flows


def _classify_table_kind(hset: set[str]) -> str:
    if (
        "charter" in hset
        or "project" in hset
        or ("summary" in hset and bool(hset & {"principles", "purpose", "why", "non_goals"}))
    ):
        return "CHARTER"
    if "flow" in hset or ("source" in hset and "target" in hset):
        return "FLOW"
    if (
        "rule" in hset
        or "bound" in hset
        or ("type" in hset and bool(hset & {"property", "target_prop", "value"}))
    ):
        return "RULE"
    if bool(hset & {"law", "theorem", "bend_dir"}) or ("lhs" in hset and "rhs" in hset):
        return "LAW"
    if bool(hset & {"interface", "endpoint", "signature", "function"}) or (
        "inputs" in hset
        and bool(hset & {"output", "outputs"})
        and not bool(hset & {"depends_on", "role"})
    ):
        return "INTERFACE"
    if bool(hset & {"schema", "typeddict", "dataclass", "packet", "fields"}):
        return "SCHEMA"
    if (
        bool(hset & {"component", "subsystem", "depends_on"})
        or ("owner" in hset and "stage" in hset)
        or ("role" in hset and bool(hset & {"files", "tests", "test_files"}))
    ):
        return "COMPONENT"
    return "GENERIC"


def extract_structured_table_rows(doc_tables: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    """Normalizes Markdown `doc_tables` into structured row dicts with `'table_kind'` and `'scope'`."""
    tables = extract_markdown_tables(doc_tables) if isinstance(doc_tables, str) else doc_tables
    if not isinstance(tables, list):
        raise ValueError(f"doc_tables must be a list or str, got {type(tables).__name__}")
    structured_rows: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        raw_headers = [str(h) for h in (table.get("headers") or []) if str(h).strip()]
        norm_headers = [_norm_col_key(h) for h in raw_headers]
        hset = {nh for nh in norm_headers if nh}
        if not hset:
            continue
        t_kind = _classify_table_kind(hset)
        hp = table.get("heading_path")
        for raw_row in (table.get("rows") or []):
            if not isinstance(raw_row, dict):
                continue
            col_map: dict[str, Any] = {}
            for r_hdr, n_hdr in zip(raw_headers, norm_headers):
                if n_hdr:
                    raw_v = raw_row.get(r_hdr, raw_row.get(n_hdr, ""))
                    col_map[n_hdr] = (
                        _split_list_cell(raw_v)
                        if n_hdr in ("depends_on", "dependencies", "children")
                        else _clean_cell_val(raw_v)
                    )
            first_val = _clean_cell_val(raw_row.get(raw_headers[0], "")) if raw_headers else ""
            if t_kind == "COMPONENT":
                scope = str(_pick(col_map, "component", "scope", "subsystem", default=first_val or "global_defaults"))
            elif t_kind == "FLOW":
                scope = str(_pick(col_map, "source", "from", default=first_val or "global_defaults"))
            elif t_kind in ("INTERFACE", "LAW", "SCHEMA", "RULE"):
                scope = str(_pick(col_map, "component", "scope", "module", default="global_defaults"))
            elif t_kind == "CHARTER":
                scope = "global_defaults"
            else:
                scope = str(_pick(col_map, "scope", "component", default=first_val or "global_defaults"))
            row_entry: dict[str, Any] = {"table_kind": t_kind, **col_map, "scope": scope}
            if "scope" in col_map and col_map["scope"] != scope:
                row_entry["raw_scope"] = col_map["scope"]
            if isinstance(hp, str) and hp.strip():
                row_entry.setdefault("heading_path", hp.strip())
            structured_rows.append(row_entry)
    return structured_rows


__all__ = [
    "CANONICAL_HEADERS_ERROR_MSG",
    "CANONICAL_PIPE_TABLE_HEADERS",
    "TableParserState",
    "TableScanMode",
    "_EMPTY_TOKENS",
    "_base_mcu",
    "_build_artifact_or_charter_mcu",
    "_build_rule_mcu_from_row",
    "_clean_cell_val",
    "_compile_component_row",
    "_decompose_rule_expr",
    "_norm_col_key",
    "_parse_scalar_val",
    "_pick",
    "_split_list_cell",
    "_split_test_paths",
    "build_mcus_from_tables_and_flows",
    "extract_flow_sentences",
    "extract_markdown_tables",
    "extract_structured_table_rows",
]
