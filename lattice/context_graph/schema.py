"""Spec 20 §1.1 & §8: ContextGraph Data Structures and Graph Invariant Verifier."""

from __future__ import annotations

import copy
from typing import Any

from .formatter import (
    DEFAULT_K_CAP,
    DEFAULT_MAX_RING,
    MAX_META_KEY_WORDS,
    MAX_META_SLOTS,
    MAX_META_SLOT_WORDS,
    MAX_META_TOTAL_WORDS,
    _TYPE_TO_FACET,
    clamp_rule_meta,
    format_compact_view,
    token_cost,
    tokenize_content_words,
)


class ContextGraphInvariantError(ValueError):
    """Raised when a ContextGraph structural or thermodynamic invariant is violated (Spec 20 §1.1)."""

    def __init__(self, code: str, errors: list[str]) -> None:
        self.code = code
        self.errors = list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


class RuleEntry(dict[str, Any]):
    """Dict-compatible structured rule node inside a `ScopeNode` (Spec 20 §1.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        self.setdefault("id", "")
        self.setdefault("type", "OBLIGATION")
        self.setdefault("facet", _TYPE_TO_FACET.get(str(self.get("type", "OBLIGATION")), "obligations"))
        self.setdefault("text", "")
        self.setdefault("scope", "global_defaults")
        self.setdefault("target_prop", None)
        self.setdefault("bound_op", None)
        self.setdefault("bound_val", None)
        self.setdefault("conditions", [])
        self.setdefault("is_exception", False)
        self.setdefault("refs", [])
        self.setdefault("overrides", [])
        self.setdefault("exceptions", [])
        self.setdefault("meta_keys", [])
        self.setdefault("meta_tokens", [])
        self.setdefault("turn", 1)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


class ScopeNode(dict[str, Any]):
    """Dict-compatible scope node inside a `ContextGraph` (Spec 20 §1.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        self.setdefault("id", "global_defaults")
        self.setdefault("parent", None)
        self.setdefault("children", [])
        self.setdefault("aliases", [])
        self.setdefault("attributes", {})
        self.setdefault("rules", [])
        self.setdefault("in_edges", [])
        self.setdefault("_meta", {})

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __getattr__(self, name: str) -> Any:
        if name == "_meta":
            return self.get("_meta", {})
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


class ContextGraph(dict[str, Any]):
    """Persistent Bounded Context Graph container supporting both dict and attribute access (Spec 20 §1.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        self.setdefault("scopes", {})
        self.setdefault("ambient_notes", [])
        self.setdefault("active_tasks", [])
        self.setdefault("open_blockers", [])
        self.setdefault("alias_index", {})
        self.setdefault("turn_index", 0)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


def validate_context_graph(
    graph: dict[str, Any],
    *,
    k_cap: int = DEFAULT_K_CAP,
    max_ring: int = DEFAULT_MAX_RING,
) -> list[str]:
    """Validates all Spec 20 §1.1 structural and thermodynamic invariants on `graph`.
    Returns a list of error strings (`[]` when valid). Raises `ValueError` on bare-string `exceptions`."""
    if not isinstance(graph, dict):
        return [f"graph must be a dict, got {type(graph).__name__}"]

    errors: list[str] = []
    scopes = graph.get("scopes")
    if not isinstance(scopes, dict):
        return ["graph['scopes'] must be a dict"]

    all_rules: dict[str, tuple[str, dict[str, Any]]] = {}
    for sid, scope in scopes.items():
        if not isinstance(scope, dict):
            errors.append(f"scope {sid!r} must be a dict")
            continue
        rules = scope.get("rules", [])
        if not isinstance(rules, list):
            errors.append(f"scope {sid!r} rules must be a list")
            continue
        if len(rules) > k_cap:
            errors.append(f"scope {sid!r} has {len(rules)} direct rules, exceeding k_cap={k_cap}")

        scope_meta = scope.get("_meta", {})
        if not isinstance(scope_meta, dict):
            errors.append(f"scope {sid!r} _meta must be a dict")
            scope_meta = {}

        for r in rules:
            if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"]:
                errors.append(f"scope {sid!r} contains malformed rule {r!r}")
                continue
            rid = r["id"]
            if rid in all_rules:
                errors.append(f"duplicate rule id {rid!r} across scopes {all_rules[rid][0]!r} and {sid!r}")
            all_rules[rid] = (sid, r)

            for ex in r.get("exceptions", []):
                if isinstance(ex, str) or not isinstance(ex, dict):
                    raise ValueError(
                        f"rule {rid!r} exceptions entry must be a structured dict {{'rule_id', 'note'}}, got {ex!r}"
                    )
                if not isinstance(ex.get("rule_id"), str) or not ex["rule_id"] or not isinstance(ex.get("note"), str):
                    errors.append(f"rule {rid!r} exception entry missing non-empty 'rule_id' or 'note': {ex!r}")

            mkeys = r.get("meta_keys", [])
            if not isinstance(mkeys, list) or len(mkeys) > MAX_META_SLOTS:
                errors.append(f"rule {rid!r} meta_keys exceeds {MAX_META_SLOTS} slots")
            else:
                for mk in mkeys:
                    if len(str(mk).split()) > MAX_META_KEY_WORDS:
                        errors.append(f"rule {rid!r} meta_key {mk!r} exceeds {MAX_META_KEY_WORDS} words")

            rmeta = scope_meta.get(rid, {})
            if isinstance(rmeta, dict):
                if len(rmeta) > MAX_META_SLOTS:
                    errors.append(f"rule {rid!r} _meta has {len(rmeta)} slots, exceeding {MAX_META_SLOTS}")
                total_w = 0
                for mk, mv in rmeta.items():
                    w_cnt = len(str(mv).split())
                    total_w += w_cnt
                    if w_cnt > MAX_META_SLOT_WORDS:
                        errors.append(f"rule {rid!r} _meta[{mk!r}] has {w_cnt} words, exceeding {MAX_META_SLOT_WORDS}")
                if total_w > MAX_META_TOTAL_WORDS:
                    errors.append(f"rule {rid!r} _meta has {total_w} total words, exceeding {MAX_META_TOTAL_WORDS}")

    for rid, (sid, r) in all_rules.items():
        for target_scope in r.get("refs", []):
            if target_scope not in scopes:
                errors.append(f"rule {rid!r} refs nonexistent target scope {target_scope!r}")
            elif rid not in scopes[target_scope].get("in_edges", []):
                errors.append(f"rule {rid!r} refs {target_scope!r}, but {rid!r} is missing from {target_scope!r}.in_edges")

        for ov_id in r.get("overrides", []):
            if ov_id not in all_rules:
                errors.append(f"rule {rid!r} overrides nonexistent rule {ov_id!r}")
            else:
                _, target_r = all_rules[ov_id]
                if not any(isinstance(ex, dict) and ex.get("rule_id") == rid for ex in target_r.get("exceptions", [])):
                    errors.append(f"rule {rid!r} overrides {ov_id!r}, but {ov_id!r}.exceptions lacks rule_id={rid!r}")

        for ex in r.get("exceptions", []):
            if isinstance(ex, dict):
                ex_rid = ex.get("rule_id")
                if ex_rid not in all_rules:
                    errors.append(f"rule {rid!r} lists nonexistent exception rule_id={ex_rid!r}")
                else:
                    _, ex_r = all_rules[ex_rid]
                    if rid not in ex_r.get("overrides", []):
                        errors.append(f"rule {rid!r} has exception {ex_rid!r}, but {ex_rid!r}.overrides lacks {rid!r}")

    for sid, scope in scopes.items():
        if not isinstance(scope, dict):
            continue
        for in_rid in scope.get("in_edges", []):
            if in_rid not in all_rules:
                errors.append(f"scope {sid!r}.in_edges references nonexistent rule {in_rid!r}")
            else:
                _, src_r = all_rules[in_rid]
                if sid not in src_r.get("refs", []):
                    errors.append(f"scope {sid!r}.in_edges contains {in_rid!r}, but {in_rid!r}.refs lacks {sid!r}")

    ambient = graph.get("ambient_notes", [])
    if not isinstance(ambient, list):
        errors.append("graph['ambient_notes'] must be a list")
    else:
        if len(ambient) > max_ring:
            errors.append(f"len(ambient_notes)={len(ambient)} exceeds max_ring={max_ring}")
        for idx, note in enumerate(ambient):
            if not isinstance(note, dict):
                errors.append(f"ambient_notes[{idx}] must be a dict")
                continue
            aturns = note.get("access_turns")
            if not isinstance(aturns, list) or not aturns or not all(isinstance(t, int) and not isinstance(t, bool) for t in aturns):
                errors.append(f"ambient_notes[{idx}] must contain non-empty access_turns: list[int]")

    return errors


verify_graph_invariants = validate_context_graph
