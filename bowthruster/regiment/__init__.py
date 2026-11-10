"""Deterministic Markdown Pipe-Table Regiment Package (`bowthruster.regiment`)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from bowthruster.types import ParserLimit, SizeLimits, attempts_of, budget_key, model_key
from .intake_packet import (
    CANONICAL_HEADERS_ERROR_MSG,
    CANONICAL_PIPE_TABLE_HEADERS,
    IntakePacket,
    PARSED_MCU_KEYS,
    Phase3InvariantError,
    TableParserState,
    TableScanMode,
    build_mcus_from_tables_and_flows,
    compile_intake_packet,
    compile_markdown_turn,
    compile_parsed_mcus,
    extract_flow_sentences,
    extract_markdown_tables,
    extract_structured_table_rows,
)

THETA = 0.5
MAX_TOKENS = {
    "regiment.pass0a1_disfluency_repair": 256,
    "regiment.pass0a2_orthography_idiom": 256,
    "regiment.pass0a3_sentence_punctuation": 256,
    "regiment.phase0_clause_split": 512,
    "regiment.phase1_sieve": 512,
    "regiment.phase3_decontextualize": 1024,
}


@dataclass
class Record:
    calls: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def error(self, stage: str, part: int | None, what: str) -> None:
        self.errors.append({"stage": stage, "part": part, "error": what})


_RECORD: ContextVar[Record | None] = ContextVar("regiment_record", default=None)


@contextmanager
def recording() -> Iterator[Record]:
    rec = Record()
    token = _RECORD.set(rec)
    try:
        yield rec
    finally:
        _RECORD.reset(token)


def _rec() -> Record:
    rec = _RECORD.get()
    if rec is None:
        raise RuntimeError("a regimentation stage runs inside recording() (regiment() opens one)")
    return rec


def _jev_budget(jev: Any, limits: SizeLimits) -> int | None:
    key = model_key(jev)[0]
    if key not in limits.budgets:
        raise ValueError(f"jev: no size-limits entry for {key!r}")
    return limits.budgets[key].bytes()


def _parser_limit(gen: Any, limits: SizeLimits) -> ParserLimit:
    key = budget_key("parser", gen)
    if key not in limits.budgets:
        raise ValueError(f"parser: budget_key {key!r} has no entry in the size-limits file")
    return ParserLimit(limits.budgets[key])


def _attempts(meta: Any, failure: BaseException | None) -> dict[str, Any]:
    log = getattr(failure, "attempt_log", None) if failure is not None else (meta or {}).get("attempt_log")
    return {
        "attempts": attempts_of(failure) if failure is not None else (meta or {}).get("attempts", 1),
        "statuses": [a.get("status") for a in log or []],
        "failed": bool(getattr(failure, "no_reply", False)) if failure is not None else False,
    }


def run_phase0(raw_text: str, gen: object = None, limits: object = None, **kwargs: object) -> dict[str, object]:
    """Deterministic compatibility wrapper returning `raw_text` and extracted Markdown tables."""
    _ = gen, limits, kwargs
    return {"raw_text": raw_text, "doc_tables": extract_markdown_tables(raw_text)}


def run_phase1(
    p0_res: dict[str, object], jev: object = None,
    limits: object = None, **kwargs: object
) -> dict[str, object]:
    """Deterministic pass-through compatibility wrapper."""
    _ = jev, limits, kwargs
    return dict(p0_res)


def run_phase2(
    p1_res: dict[str, object], jev: object = None,
    limits: object = None, **kwargs: object
) -> dict[str, object]:
    """Deterministic pass-through compatibility wrapper."""
    _ = jev, limits, kwargs
    return dict(p1_res)


def run_phase3(
    p2_res: dict[str, object] | str, gen: object = None, limits: object = None, **kwargs: object
) -> dict[str, object]:
    """Deterministic Markdown-table compiler returning a `Phase3Result`-shaped dict with `intake_packet`."""
    _ = gen, limits, kwargs
    raw_text = p2_res if isinstance(p2_res, str) else str(p2_res.get("raw_text") or "")
    pkt = compile_markdown_turn(raw_text)
    return {
        "raw_text": raw_text,
        "doc_tables": pkt["doc_tables"],
        "structured_table_rows": pkt["structured_table_rows"],
        "parsed_mcus": pkt["parsed_mcus"],
        "intake_packet": pkt,
        "grounding_status": "GROUNDED",
        "is_locked": True,
        "clarification_questions": [],
        "open_questions": [],
    }


__all__ = [
    "CANONICAL_HEADERS_ERROR_MSG",
    "CANONICAL_PIPE_TABLE_HEADERS",
    "IntakePacket",
    "PARSED_MCU_KEYS",
    "Phase3InvariantError",
    "TableParserState",
    "TableScanMode",
    "build_mcus_from_tables_and_flows",
    "compile_intake_packet",
    "compile_markdown_turn",
    "compile_parsed_mcus",
    "extract_flow_sentences",
    "extract_markdown_tables",
    "extract_structured_table_rows",
    "run_phase0",
    "run_phase1",
    "run_phase2",
    "run_phase3",
]
