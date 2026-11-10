"""Spec 23C §2.1: Story-Calibrated Executive Voice, Prompt Loader, & Bend 2-Verified Reply Guard (`voice.py`)."""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from typing import Any

from bowthruster.types import SizeLimits
from bowthruster.whiteboard import scan_graph

EXECUTIVE_PROMPT_KEY = "executive.synthesize_turn"
EXECUTIVE_MAX_TOKENS = 2048
_REQUIRED_REPLY_KEYS: tuple[str, ...] = ("decision", "bounce_reason", "user_message", "cited_items")


def _first_val(mapping: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        if val := mapping.get(k):
            return str(val)
    return default


class ExecutiveVoiceError(ValueError):
    """Fail-closed error raised when Executive Voice inputs or LLM replies violate Spec 23C contracts (§2.1)."""

    def __init__(self, code: str, errors: list[str]) -> None:
        self.code = code
        self.errors = list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


def _resolve_executive_prompt_path() -> Path:
    """Locates `prompts/executive/executive.json` relative to the package root."""
    here = Path(__file__).resolve()
    target = here.parents[1] / "prompts" / "executive" / "executive.json"
    if target.is_file():
        return target
    raise ExecutiveVoiceError("executive_prompt_not_found", [f"executive.json not found at {target}"])


_DEFAULT_EXECUTIVE_PROMPT: dict[str, Any] = {
    "version": "p-executive-3",
    "name": "executive_synthesize_turn",
    "text": (
        "The Parable of the Unanchored Instruction | The Parable of the Silent Contradiction | "
        "The Parable of the Foolish Plan | The Parable of the Decisive Commit\n"
        "gate_decision: {gate_decision}\nbounce_reason: {bounce_reason}\n"
        "committed_items:\n{committed_items}\nconflicts:\n{conflicts}\n"
        "flawed_ideas:\n{flawed_ideas}\nclarification_questions:\n{clarification_questions}\n"
        "open_questions:\n{open_questions}\nwhiteboard_summary:\n{whiteboard_summary}"
    ),
    "schema": {
        "type": "object",
        "required": ["decision", "bounce_reason", "user_message", "cited_items"],
        "properties": {
            "decision": {"type": "string"},
            "bounce_reason": {"type": "string"},
            "user_message": {"type": "string"},
            "cited_items": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def load_executive_prompt(key: str = EXECUTIVE_PROMPT_KEY) -> dict[str, Any]:
    """Loads `key` from `prompts/executive/executive.json` or falls back to the built-in deterministic template."""
    if not isinstance(key, str) or not key.strip():
        raise ExecutiveVoiceError("executive_invalid_prompt_key", [f"key must be a non-empty str, got {key!r}"])
    try:
        path = _resolve_executive_prompt_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        prompts = data.get("prompts", {}) if isinstance(data, dict) else {}
        if key not in prompts or not isinstance(prompts[key], dict):
            raise ExecutiveVoiceError("executive_prompt_key_missing", [f"prompt key {key!r} not found in {path}"])
        entry = prompts[key]
        return {
            "version": str(data.get("version", "")),
            "text": str(entry["text"]),
            "name": str(entry["name"]),
            "schema": copy.deepcopy(entry["schema"]),
        }
    except ExecutiveVoiceError as exc:
        if exc.code == "executive_prompt_not_found" and key == EXECUTIVE_PROMPT_KEY:
            return copy.deepcopy(_DEFAULT_EXECUTIVE_PROMPT)
        raise


def _summarize_whiteboard_for_prompt(whiteboard_graph: Any) -> str:
    """Renders a compact text summary of `whiteboard_graph` for the `whiteboard_summary` prompt slot."""
    if not isinstance(whiteboard_graph, dict) or not whiteboard_graph.get("scopes"):
        return "[EMPTY_WHITEBOARD]"
    view = scan_graph(copy.deepcopy(whiteboard_graph))
    text = str(view.get("text") or "").strip()
    return text if text else "[EMPTY_WHITEBOARD]"


def format_executive_prompt(gate_result: dict[str, Any]) -> str:
    """Formats `executive.synthesize_turn` with the 8 required `gate_result` placeholders (Spec 23C §2.1.A)."""
    if not isinstance(gate_result, dict):
        raise ExecutiveVoiceError(
            "executive_invalid_gate_result",
            [f"gate_result must be a dict, got {type(gate_result).__name__}"],
        )
    if "decision" not in gate_result or "bounce_reason" not in gate_result:
        raise ExecutiveVoiceError(
            "executive_invalid_gate_result",
            ["gate_result must contain 'decision' and 'bounce_reason'"],
        )
    prompt_meta = load_executive_prompt(EXECUTIVE_PROMPT_KEY)
    template = prompt_meta["text"]
    return template.format(
        gate_decision=str(gate_result.get("decision") or ""),
        bounce_reason=str(gate_result.get("bounce_reason") or "NONE"),
        committed_items=json.dumps(gate_result.get("committed_items", []), indent=2, ensure_ascii=False),
        conflicts=json.dumps(gate_result.get("conflicts", []), indent=2, ensure_ascii=False),
        flawed_ideas=json.dumps(gate_result.get("flawed_ideas", []), indent=2, ensure_ascii=False),
        clarification_questions=json.dumps(
            gate_result.get("clarification_questions", []),
            indent=2,
            ensure_ascii=False,
        ),
        open_questions=json.dumps(gate_result.get("open_questions", []), indent=2, ensure_ascii=False),
        whiteboard_summary=_summarize_whiteboard_for_prompt(gate_result.get("whiteboard_graph")),
    )


def is_reply_valid(
    decision_matches_gate: bool,
    reason_matches_gate: bool,
    message_non_empty: bool,
    all_required_citations_present: bool,
) -> bool:
    """Pure reply validity gate governed by Bend 2 Laws 1–3 (`LAWS.bend`, Spec 23C §2.1.B)."""
    return bool(
        decision_matches_gate
        and reason_matches_gate
        and message_non_empty
        and all_required_citations_present
    )


def _citation_errors(
    gate_result: dict[str, Any],
    msg_str: str,
    cited_items: list[str],
) -> list[str]:
    """Pure citation verifier returning descriptive errors for any missing conflict, flaw, question, or commit."""
    cit_errors: list[str] = []
    combined_low = (msg_str + " " + " ".join(cited_items)).lower()
    expected_decision = gate_result.get("decision")

    for idx, c in enumerate(gate_result.get("conflicts", [])):
        if not isinstance(c, dict):
            continue
        anchors = [
            str(c.get(k) or "").strip().lower()
            for k in ("target_prop", "existing_rule_id", "scope")
            if str(c.get(k) or "").strip()
        ]
        if len(cited_items) < 1 or (anchors and not any(a in combined_low for a in anchors)):
            cit_errors.append(f"conflict[{idx}] missing required anchor {anchors!r} or citation in reply")

    for idx, f in enumerate(gate_result.get("flawed_ideas", [])):
        if not isinstance(f, dict):
            continue
        anchors = [
            str(f.get(k) or "").strip().lower()
            for k in ("target_prop", "basis_id", "scope", "description")
            if str(f.get(k) or "").strip()
        ]
        if len(cited_items) < 1 or (anchors and not any(a in combined_low for a in anchors)):
            cit_errors.append(f"flawed_ideas[{idx}] missing required anchor {anchors!r} or citation in reply")

    for idx, cq in enumerate(gate_result.get("clarification_questions", [])):
        cq_clean = str(cq).strip()
        if not cq_clean:
            continue
        if cq_clean.lower() not in combined_low and not (len(cited_items) >= 1 and "?" in msg_str):
            cit_errors.append(f"clarification_questions[{idx}] ({cq_clean!r}) not addressed in reply")

    for idx, oq in enumerate(gate_result.get("open_questions", [])):
        oq_clean = str(oq).strip()
        if not oq_clean:
            continue
        if oq_clean.lower() not in combined_low and len(cited_items) < 1:
            cit_errors.append(f"open_questions[{idx}] ({oq_clean!r}) not addressed in reply")

    if expected_decision == "COMMIT_TO_WHITEBOARD" and gate_result.get("committed_items"):
        if len(cited_items) < 1:
            cit_errors.append("COMMIT_TO_WHITEBOARD with committed_items requires non-empty cited_items")

    return cit_errors


def verify_executive_reply(gate_result: dict[str, Any], reply: dict[str, Any]) -> list[str]:
    """Fail-closed verifier checking `reply` against `gate_result` and `is_reply_valid` (Spec 23C §2.1.B)."""
    if not isinstance(gate_result, dict):
        return [f"gate_result must be a dict, got {type(gate_result).__name__}"]
    if not isinstance(reply, dict):
        return [f"reply must be a dict, got {type(reply).__name__}"]

    errors: list[str] = []
    for k in _REQUIRED_REPLY_KEYS:
        if k not in reply:
            errors.append(f"reply missing required key {k!r}")

    expected_decision = gate_result.get("decision")
    expected_reason = gate_result.get("bounce_reason")
    decision_matches_gate = reply.get("decision") == expected_decision
    if not decision_matches_gate:
        errors.append(f"reply['decision']={reply.get('decision')!r} != gate_result['decision']={expected_decision!r}")

    reason_matches_gate = reply.get("bounce_reason") == expected_reason
    if not reason_matches_gate:
        errors.append(
            f"reply['bounce_reason']={reply.get('bounce_reason')!r} != gate_result['bounce_reason']={expected_reason!r}"
        )

    user_msg = reply.get("user_message")
    message_non_empty = isinstance(user_msg, str) and bool(user_msg.strip())
    if not message_non_empty:
        errors.append("reply['user_message'] must be a non-empty string")

    cited_items = reply.get("cited_items")
    list_valid = isinstance(cited_items, list) and all(isinstance(x, str) and bool(x.strip()) for x in cited_items)
    if not list_valid:
        errors.append("reply['cited_items'] must be a list of non-empty strings")
        all_required_citations_present = False
    else:
        msg_str = user_msg.strip() if isinstance(user_msg, str) else ""
        cit_errors = _citation_errors(gate_result, msg_str, cited_items)
        errors.extend(cit_errors)
        all_required_citations_present = not cit_errors

    valid = is_reply_valid(
        decision_matches_gate,
        reason_matches_gate,
        message_non_empty,
        all_required_citations_present,
    )
    if not valid and not errors:
        errors.append("is_reply_valid rejected reply")
    return errors


def render_deterministic_executive_reply(gate_result: dict[str, Any]) -> dict[str, Any]:
    """Synthesizes a deterministic, story-aligned Executive reply directly from `gate_result` (Spec 23C §2.1.C)."""
    if not isinstance(gate_result, dict) or "decision" not in gate_result or "bounce_reason" not in gate_result:
        raise ExecutiveVoiceError(
            "executive_invalid_gate_result",
            ["gate_result must be a dict containing 'decision' and 'bounce_reason'"],
        )

    decision = str(gate_result["decision"])
    bounce_reason = str(gate_result["bounce_reason"])
    cited_items: list[str] = []

    if decision == "SEND_BACK_TO_USER":
        parts: list[str] = [
            "The marker remains capped; nothing from this turn has been written to the Whiteboard."
        ]
        for c in gate_result.get("conflicts", []):
            if not isinstance(c, dict):
                continue
            scope = _first_val(c, "scope", default="global_defaults")
            t_prop = c.get("target_prop")
            old_rid = _first_val(c, "existing_rule_id", "id", default="existing_rule")
            old_txt = _first_val(c, "existing_rule_text", "text")
            inc_txt = _first_val(c, "incoming_text")
            desc = _first_val(c, "description", "text", default=f"Rule conflict on {t_prop} in {scope}").strip()
            parts.append(
                f"Rule conflict in scope '{scope}' on '{t_prop}': standing rule '{old_rid}' ({old_txt}) "
                f"clashes with incoming item ({inc_txt}). {desc}"
            )
            cited_items.append(desc)

        for f in gate_result.get("flawed_ideas", []):
            if not isinstance(f, dict):
                continue
            f_kind = _first_val(f, "flaw_kind", default="FLAWED_IDEA")
            scope = _first_val(f, "scope", default="global_defaults")
            t_prop = f.get("target_prop")
            basis_id = _first_val(f, "basis_id", default="basis")
            basis_txt = _first_val(f, "basis_text")
            desc = _first_val(f, "description", default=f"Flawed premise ({f_kind}) on {t_prop} in {scope}").strip()
            parts.append(
                f"Flawed premise ({f_kind}) in scope '{scope}' on '{t_prop}' against '{basis_id}' ({basis_txt}): {desc}"
            )
            cited_items.append(desc)

        for cq in gate_result.get("clarification_questions", []):
            if cq_s := str(cq).strip():
                parts.append(f"Clarification needed: {cq_s}")
                cited_items.append(cq_s)

        for oq in gate_result.get("open_questions", []):
            if oq_s := str(oq).strip():
                parts.append(f"Open question: {oq_s}")
                cited_items.append(oq_s)

        if not cited_items:
            cited_items.append(f"Bounce reason: {bounce_reason}")
        parts.append("How would you like to resolve these items before we write to the Whiteboard?")
        return {
            "decision": decision,
            "bounce_reason": bounce_reason,
            "user_message": " ".join(parts),
            "cited_items": cited_items,
        }

    item_summaries: list[str] = []
    for m in gate_result.get("committed_items", []):
        if not isinstance(m, dict):
            continue
        scope = _first_val(m, "primary_scope", "scope", default="global_defaults").strip()
        txt = _first_val(m, "text", "canonical_text", "id").strip()
        if txt:
            cited_items.append(txt)
            item_summaries.append(f"[{scope}] {txt}")

    if item_summaries:
        msg = "Written to the Whiteboard: " + "; ".join(item_summaries)
    else:
        msg = "Written to the Whiteboard; all turn items are grounded and conflict-free."
    return {
        "decision": decision,
        "bounce_reason": bounce_reason,
        "user_message": msg,
        "cited_items": cited_items,
    }


def _invoke_voice_generator(
    gen: Any,
    prompt: str,
    schema_rec: dict[str, Any],
    max_tokens: int,
    gate_result: dict[str, Any],
) -> dict[str, Any]:
    """Calls `gen.generate_json(prompt, schema_rec, max_tokens)` or callable `gen` and normalizes a dict reply."""
    if hasattr(gen, "generate_json") and callable(gen.generate_json):
        raw = gen.generate_json(prompt, schema_rec, max_tokens)
    elif callable(gen):
        sig = inspect.signature(gen)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
        if has_varargs or len(params) >= 3:
            raw = gen(prompt, schema_rec, max_tokens)
        elif len(params) == 2:
            raw = gen(prompt, schema_rec)
        elif len(params) == 1:
            pname = params[0].name.lower()
            if "gate" in pname or "result" in pname or "report" in pname:
                raw = gen(gate_result)
            else:
                try:
                    raw = gen(prompt)
                except (KeyError, TypeError, AttributeError):
                    raw = gen(gate_result)
        else:
            raw = gen()
    else:
        raise ExecutiveVoiceError(
            "executive_invalid_generator",
            [f"gen must expose generate_json or be callable, got {type(gen).__name__}"],
        )

    if isinstance(raw, tuple) and len(raw) == 2:
        raw, meta = raw
        if isinstance(meta, dict) and meta.get("done_reason") in ("length", "max_tokens"):
            raise ExecutiveVoiceError(
                "executive_voice_output_truncated",
                [f"generator output truncated: done_reason={meta.get('done_reason')!r}"],
            )

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception as exc:
            raise ExecutiveVoiceError("executive_voice_invalid_json", [str(exc)]) from exc
    if not isinstance(raw, dict):
        raise ExecutiveVoiceError(
            "executive_voice_invalid_reply",
            [f"generator must return a dict or JSON object string, got {type(raw).__name__}"],
        )
    return raw


def synthesize_executive_voice(
    gate_result: dict[str, Any],
    *,
    gen: Any = None,
    limits: SizeLimits | None = None,
) -> dict[str, Any]:
    """Synthesizes and fail-closed verifies the Executive Voice reply for `gate_result` (Spec 23C §2.1.C)."""
    _ = limits
    if gen is None:
        reply = render_deterministic_executive_reply(gate_result)
        errors = verify_executive_reply(gate_result, reply)
        if errors:
            raise ExecutiveVoiceError("executive_voice_guard_failed", errors)
        return reply

    prompt_meta = load_executive_prompt(EXECUTIVE_PROMPT_KEY)
    base_prompt = format_executive_prompt(gate_result)
    schema_rec = {"name": prompt_meta["name"], "schema": prompt_meta["schema"]}

    active_prompt = base_prompt
    errors: list[str] = []
    for _ in range(2):
        reply = _invoke_voice_generator(gen, active_prompt, schema_rec, EXECUTIVE_MAX_TOKENS, gate_result)
        errors = verify_executive_reply(gate_result, reply)
        if not errors:
            return reply
        active_prompt = (
            f"{base_prompt}\n\nPREVIOUS REPLY FAILED GUARD VERIFICATION:\n"
            + "\n".join(f"- {e}" for e in errors)
        )
    raise ExecutiveVoiceError("executive_voice_guard_failed", errors)
