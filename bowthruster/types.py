"""Core circuit types (Spec 01 §7.2).

`RuleSpec` here is the runtime view only (Spec 03 §6.7): rule_id, text, applies_to. The rule
category and oracle check never enter circuit code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ABSTAIN = "ABSTAIN"  # Router output when no Floor clears tau_scope


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    tool_name: str
    args_sha256: str
    result_status: str
    timestamp: str | None = None


@dataclass(frozen=True)
class ToolResult:
    name: str
    output: Any
    receipt: Receipt | None = None


@dataclass(frozen=True)
class TraceStep:
    call: ToolCall
    result: ToolResult


Trace = list[TraceStep]


@dataclass
class Draft:
    reply_text: str
    tool_calls: list[ToolCall]
    trace: Trace  # The steps executed while producing this draft
    generator_meta: dict[str, Any] = field(default_factory=dict)  # model id, tokens, stop reason


@dataclass(frozen=True)
class NeuronVerdict:
    neuron_id: str
    fired: bool
    score: float
    threshold: float
    error_span: str | None
    latency_ms: float
    bounce_text: str | None = None  # The message sent back to the generator if this verdict fired


@dataclass(frozen=True)
class FloorSpec:
    floor_id: str
    tool_names: tuple[str, ...]  # At most MAX_FLOOR_TOOLS
    role_prompt: str
    description: str = ""  # Used by the zero-shot NLI router

    def __post_init__(self) -> None:
        if len(self.tool_names) > MAX_FLOOR_TOOLS:
            raise ValueError(f"Floor {self.floor_id} has {len(self.tool_names)} tools; the limit is {MAX_FLOOR_TOOLS}")


MAX_FLOOR_TOOLS = 6  # Spec 01 §3: k <= 6 tools per Floor

AppliesTo = Literal["reply", "trace", "state", "reply + trace", "trace + state"]


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    text: str
    applies_to: str  # One of AppliesTo; kept as str so new oracle-side combinations do not break loading


def to_jsonable(obj: Any) -> Any:
    """Dataclasses, lists and dicts -> plain JSON values for the episode log."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    return obj


def canonical_bytes(obj: Any) -> bytes:
    """Spec 11 §7.2: the UTF-8 bytes of a payload's canonical JSON, the serialisation every size check measures."""
    return json.dumps(to_jsonable(obj), sort_keys=True, ensure_ascii=False, default=str).encode()


def json_bytes(obj: Any) -> int:
    """Spec 11 §7.2: a payload's size, the UTF-8 bytes of its canonical JSON."""
    return len(canonical_bytes(obj))


def input_bytes(instructions: str, state: Any) -> int:
    """Spec 11 §7.2 (QA F2): a judge's input measured as sent, the instructions and the state in one object,
    envelope included."""
    return json_bytes({"instructions": instructions, "state": state})


HOSTED_DIGEST = "gateway"  # The digest a hosted backend reports: it has no local version


def model_key(source: Any) -> tuple[str, bool]:
    """Spec 11 §7.2: (the key of a model and its exact version, whether that version is verified). A local model is
    `<id>@<digest>`. A hosted one with no version (Jev) is `<id> via <provider>`, unverified."""
    model_id, digest = getattr(source, "model_id", ""), getattr(source, "digest", None)
    if digest and digest != HOSTED_DIGEST:
        return f"{model_id}@{digest}", True
    provider = getattr(source, "provider", None)
    return (f"{model_id} via {provider}" if provider else model_id), False


def budget_key(node: str, source: Any) -> str:
    """Spec 11 §7.2 (stand-ins, D19): the key of a node's size-limits entry. A stand-in (a gateway backend, which
    carries budget_key) takes the entry of the small model it stands in for, with no default to its own model's; any
    other node keys by its own model and version, as before. A stand-in with no key refuses, naming the node."""
    if hasattr(source, "budget_key"):
        key = source.budget_key
        if not isinstance(key, str) or not key:
            raise ValueError(f"{node}: a gateway node needs a budget_key naming its size-limits entry "
                             f"(Spec 11 §7.2), got {key!r}")
        return key
    return model_key(source)[0]


def model_node(source: Any) -> dict[str, Any]:
    """A model node's backend and model for run_meta (Director, 2026-10-29: the backend is per node and per run)."""
    key, verified = model_key(source)
    return {"backend": type(source).__name__, "model_id": getattr(source, "model_id", None),
            "provider": getattr(source, "provider", None), "model_key": key, "version_verified": verified,
            # False when the backend sends no sampling parameters (the provider may not accept them); None: not stated
            "deterministic": getattr(source, "deterministic", None),
            # The thinking setting the backend sends (Spec 12 §20.1: "disabled" on every gateway node); None: none sent
            "thinking": getattr(source, "thinking", None),
            # 00a item 14: the connection retry's delay in force (from config); None for a backend that sends nothing
            "retry_delay_s": getattr(source, "retry_delay_s", None),
            # Spec 11 §7.2: the size-limits entry the node is budgeted by: a stand-in's budget_key (None if it has
            # none, which a run with limits refuses), else its own model key
            "budget_key": getattr(source, "budget_key") if hasattr(source, "budget_key") else key}


def _positive(v: Any, kind: type) -> bool:
    return isinstance(v, kind) and not isinstance(v, bool) and v > 0


@dataclass(frozen=True)
class ModelBudget:
    """One model's input budget in bytes: (context_tokens - reserve) x bytes_per_token_floor, or a direct budget_bytes
    when the context is unknown (Jev). The reserve is reserve_tokens, or, with reserve_is_max_tokens, the calling
    node's max_tokens (qwen3:8b)."""

    context_tokens: int | None = None
    reserve_tokens: int | None = None
    bytes_per_token_floor: float | None = None
    budget_bytes: int | None = None
    reserve_is_max_tokens: bool = False
    # T-Division, 2026-10-29: no byte budget, because the provider's API refuses an over-length prompt with an error
    # and never truncates it (Sonnet, until its floor is measured). No size check and no cut for this model's node.
    api_enforced: bool = False

    def __post_init__(self) -> None:
        derived = (self.context_tokens, self.reserve_tokens, self.bytes_per_token_floor)
        if self.api_enforced:
            has_res = self.reserve_is_max_tokens or (isinstance(self.reserve_tokens, int) and self.reserve_tokens >= 0)
            ok = (
                _positive(self.context_tokens, int)
                and self.bytes_per_token_floor is None
                and self.budget_bytes is None
                and has_res
            )
            if not ok:
                msg = "an api_enforced budget has context_tokens and a reserve, and no floor or budget_bytes"
                raise ValueError(msg)
            return
        if self.budget_bytes is not None:
            ok = _positive(self.budget_bytes, int) and derived == (None, None, None) and not self.reserve_is_max_tokens
        elif self.reserve_is_max_tokens:
            ok = (_positive(self.context_tokens, int) and self.reserve_tokens is None
                  and _positive(self.bytes_per_token_floor, (int, float)))
        else:
            ok = (_positive(self.context_tokens, int) and isinstance(self.reserve_tokens, int)
                  and not isinstance(self.reserve_tokens, bool) and 0 <= self.reserve_tokens < self.context_tokens
                  and _positive(self.bytes_per_token_floor, (int, float)))
        if not ok:
            raise ValueError("a model budget is budget_bytes alone, or context_tokens with a positive "
                             "bytes_per_token_floor and either reserve_tokens (0 <= reserve < context) or "
                             "reserve_is_max_tokens")

    def bytes(self, max_tokens: int | None = None) -> int | None:
        """The budget for one call, or None for api_enforced (nothing to check). `max_tokens` is the calling node's,
        used when the reserve is its max_tokens."""
        if self.api_enforced:
            return None
        if self.budget_bytes is not None:
            return self.budget_bytes
        reserve = self.reserve_tokens
        if self.reserve_is_max_tokens:
            if not _positive(max_tokens, int) or max_tokens >= self.context_tokens:
                raise ValueError(f"this budget's reserve is the call's max_tokens, which must be in (0, context): "
                                 f"{max_tokens!r}")
            reserve = max_tokens
        return int((self.context_tokens - reserve) * self.bytes_per_token_floor)


@dataclass(frozen=True)
class SizeLimits:
    """Spec 11 §7.2 (build ruling and proposed values, 2026-10-29): one input budget per model and exact version,
    keyed by model_key. Every connective's limit follows from the model that reads it. Core holds no defaults; the
    values are set by the Director, and None in a run means the size checks are off. With limits set, a run whose
    model node has no entry for its exact model and version refuses to start."""

    budgets: dict[str, ModelBudget]
    # Ruling on QA F3: the largest share of the action node's room (after the hint reserve) one cut result may take,
    # in (0, 1); each later cut again takes this share of what remains
    cut_share: float
    # What these values are for, logged with them in run_meta (Director, 2026-10-29: "raised for development, optimise
    # later"); None when the settings file states none
    envelope: str | None = None

    def __post_init__(self) -> None:
        if not self.budgets or not all(isinstance(b, ModelBudget) for b in self.budgets.values()):
            raise ValueError("size limits need at least one ModelBudget, keyed by model_key")
        if not (isinstance(self.cut_share, (int, float)) and not isinstance(self.cut_share, bool)
                and 0 < self.cut_share < 1):  # 1 would let one cut take all the room (QA F3 starvation)
            raise ValueError(f"cut_share must be in the open interval (0, 1), got {self.cut_share!r}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SizeLimits:
        """From a settings file's {"budgets": {model_key: {ModelBudget fields}}, "cut_share": x[, "envelope": label]};
        other top-level keys are notes."""
        return cls({key: ModelBudget(**fields) for key, fields in data["budgets"].items()}, data["cut_share"],
                   data.get("envelope"))


REFUSED_CONTEXT_OVERFLOW, REFUSED_PAYLOAD_TOO_LARGE = "context_overflow", "payload_too_large"  # InputSizes reasons


@dataclass
class InputSizes:
    """Spec 11 §7.2 (Director, 2026-10-29): every model input's actual size in bytes, per node, logged in every run
    (with or without limits) so its shrinking can be tracked. `nodes` counts inputs that were sent
    `refused` lists,
    separately, each input a size check stopped, with its node, size and reason (QA N1 ruling)."""

    nodes: dict[str, dict[str, int]] = field(default_factory=dict)
    refused: list[dict[str, Any]] = field(default_factory=list)
    # Logging freeze (c): every sent input, in order: {node, seq, bytes, budget} (budget None: not checked)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def refuse(self, node: str, nbytes: int, reason: str) -> None:
        self.refused.append({"node": node, "bytes": nbytes, "reason": reason})

    def add(self, node: str, nbytes: int, budget: int | None = None) -> None:
        self.calls.append({"node": node, "seq": len(self.calls), "bytes": nbytes, "budget": budget})
        s = self.nodes.setdefault(node, {"calls": 0, "max_bytes": 0, "total_bytes": 0})
        s["calls"] += 1
        s["max_bytes"] = max(s["max_bytes"], nbytes)
        s["total_bytes"] += nbytes


@dataclass
class ParserLimit:
    """Spec 11 §7.2 (stand-ins, D19): the parser's size-limits entry for one request parse (None: no limits in this
    run), and the size of every parser call checked against it, in order, for the run's input_sizes. Each call is
    measured as sent (its one user message, as generator.chat_bytes measures) against the entry at that call's own
    max_tokens; over it the call is not made: context_overflow at "parser", naming the call."""

    entry: ModelBudget | None
    sizes: list[dict[str, Any]] = field(default_factory=list)

    def check(self, call: str, prompt: str, max_tokens: int) -> None:
        n = json_bytes({"messages": [{"role": "user", "content": prompt}], "tools": []})
        limit = None if self.entry is None else self.entry.bytes(max_tokens)
        over = limit is not None and n > limit
        self.sizes.append({"node": "parser", "call": call, "bytes": n, "budget": limit,
                           **({"refused": REFUSED_CONTEXT_OVERFLOW} if over else {})})
        if over:
            raise ContextOverflow("parser", limit, {"input": n, "call": call})


class ContextOverflow(Exception):
    """Spec 11 §7.2: the circuit's own part of a model input does not fit (or the backend's prompt reached its
    context). Not the producer's fault, so no retry: the run stops with kind context_overflow."""

    def __init__(self, node: str, limit_bytes: int | None, parts: dict[str, int]) -> None:
        self.detail = {"node": node, "limit_bytes": limit_bytes, "parts": parts}
        super().__init__(f"context_overflow at {node}: {parts} over {limit_bytes}")


class CallError(RuntimeError):
    """A model call that failed in its backend. `attempts` is how many times it was sent: 1, or more after 00a item 14's
    retries (one for a connection failure on every backend; on Jev up to its max_retries, docs 5ea1192). `attempt_log`
    has one {error, errno, status, latency_ms, wait_s} per attempt."""

    def __init__(self, message: str, attempts: int, attempt_log: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts, self.attempt_log = attempts, attempt_log


def attempts_of(e: BaseException) -> int:
    """How many times the failed call was sent: a backend's CallError says; any other failure was one attempt."""
    return e.attempts if isinstance(e, CallError) else 1


def attempt_fields(meta: dict[str, Any], failure: BaseException | None) -> dict[str, Any]:
    """00a item 14: a call log record's attempts (1 or more), and retry_error (the first failed attempt's error) when it
    was retried; from the backend's meta on success, or from its failure."""
    if failure is not None:
        n = attempts_of(failure)
        return {"attempts": n, **({"retry_error": failure.attempt_log[0]["error"]} if n > 1 else {})}
    return {"attempts": meta.get("attempts"), **({"retry_error": meta["retry_error"]} if "retry_error" in meta else {})}


class JudgeError(Exception):
    """T-Division, 2026-10-29: a judge's backend failed (an HTTP error, a malformed reply, a missing meta, or an answer
    is not a probability). In compiled mode it stops the run with kind judge_error, naming the node, the rule and the
    error. It is never counted as a rule firing, an undecided condition or a fact, and the selector never turns it into
    "no tools"."""

    def __init__(self, node: str, rule: str | None, error: str, attempts: int) -> None:
        # attempts: how many times the failing call was sent (1, or more after 00a item 14's retries)
        self.detail = {"node": node, "rule": rule, "error": error, "attempts": attempts}
        super().__init__(f"judge_error at {node} ({rule}): {error}")


class GeneratorError(Exception):
    """T-Division, 2026-10-29 (the generator twin of JE1): the generator's backend failed (an HTTP error, a refusal or
    filter, a "length" finish, an unknown finish reason, a malformed reply). In compiled mode the run ends at stop
    with kind generator_error and {node "generator", error, finish_reason}; never a crash."""

    def __init__(self, error: str, finish_reason: str | None, reported_finish_reason: str | None = None,
                 node: str = "generator", step: str | None = None, *, attempts: int) -> None:
        # reported_finish_reason: what the reply said when the stop is the token count (m17c turn 3), else None.
        # node "parse" with its step (T-Division, 2026-10-29): a request-parse generate call failed, or the P1 split did
        self.detail = {"node": node, **({"step": step} if step is not None else {}), "error": error,
                       "finish_reason": finish_reason, "reported_finish_reason": reported_finish_reason,
                       "attempts": attempts}  # 00a item 14
        super().__init__(f"generator_error: {error}")


class PayloadTooLarge(Exception):
    """Spec 11 §7.2: the producer's payload makes a model input too big. The red node rejects it to the producer."""

    def __init__(self, payload_bytes: int, limit_bytes: int) -> None:
        self.payload_bytes, self.limit_bytes = payload_bytes, limit_bytes
        super().__init__(f"payload of {payload_bytes} bytes exceeds the limit {limit_bytes}")
