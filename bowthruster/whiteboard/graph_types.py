"""Spec 22A §2.1: Core Whiteboard & nD Lattice Typed Entries & Board Normalizer (`graph_types.py`)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from bowthruster.graph_core import _TYPE_TO_FACET
from bowthruster.nlp import restore_ascii_dots


@dataclass
class ScopeSpec:
    """Canonical normalized board scope specification."""

    id: str = "global_defaults"
    status: str = "DRAFT"
    claimed_by: str | None = None
    files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class ArtifactSpec:
    """Canonical normalized board artifact (`SCHEMA` / `INTERFACE` / `LAW`) specification."""

    id: str = ""
    kind: str = "SCHEMA"
    name: str = ""
    scope_id: str = "global_defaults"
    file: str = ""
    spec: str = ""


LATTICE_KINDS = (
    "COMPONENT", "FLOW", "SCHEMA", "INTERFACE", "LAW", "CONSTRAINT", "FACT", "TASK",
)
BUILD_STATUSES = (
    "DRAFT", "BLOCKED_BY_DEP", "READY", "IN_PROGRESS", "IN_QA", "FAILED", "VERIFIED", "STALE",
)
_ARTIFACT_KINDS = frozenset({"SCHEMA", "INTERFACE", "LAW"})
_RULE_STORE_KINDS = frozenset({
    "CONSTRAINT", "FACT", "BOUND", "PROHIBITION", "OBLIGATION",
    "POLICY", "GOAL", "PROPOSAL", "REVOCATION",
})
_SCOPE_SBF_DEFAULTS: tuple[tuple[str, Any], ...] = (
    ("role", None),
    ("inputs", []),
    ("outputs", []),
    ("depends_on", []),
    ("files", []),
    ("tests", []),
    ("owner", None),
    ("stage", None),
    ("kind", None),
    ("status", "DRAFT"),
    ("build_status", "DRAFT"),
    ("build_tag", None),
    ("core_tag", None),
    ("failure_diagnostic", None),
    ("claimed_by", None),
    ("claimed_at", None),
)


def _apply_entry_defaults(
    target: dict[str, Any],
    defaults: tuple[tuple[str, Any], ...],
    *,
    with_turns: bool = True,
) -> None:
    for k, v in defaults:
        target.setdefault(k, copy.deepcopy(v))
    if with_turns:
        raw_t = target.get("turn", target.get("updated_turn", target.get("created_turn", 1)))
        turn_val = int(raw_t)
        target.setdefault("turn", turn_val)
        target.setdefault("created_turn", int(target.get("created_turn", turn_val)))
        target.setdefault("updated_turn", int(target.get("updated_turn", turn_val)))


def _clean_str_list(items: list[str] | str | None) -> list[str]:
    if isinstance(items, str):
        raw_parts = [x.strip() for x in items.replace(";", ",").split(",")]
    elif isinstance(items, list):
        raw_parts = [str(x).strip() for x in items]
    else:
        raw_parts = []
    out: list[str] = []
    for x in raw_parts:
        s = restore_ascii_dots(x).strip()
        if s and s.lower() not in ("none", "-", "—", "n/a", "null") and s not in out:
            out.append(s)
    return out


def _clean_opt_str(val: Any) -> str | None:
    return (restore_ascii_dots(str(val)).strip() or None) if val is not None else None


class ContextGraphInvariantError(ValueError):
    """Raised when a Whiteboard / ContextGraph invariant is violated (Spec 22A §2.1)."""

    def __init__(self, code: str, errors: list[str]) -> None:
        self.code, self.errors = code, list(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


class _AttrDict(dict[str, Any]):
    """Base dict supporting `.to_dict()` and attribute access for Whiteboard entries."""

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or hasattr(dict, name):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


class RuleEntry(_AttrDict):
    """Dict-compatible structured rule node inside a `ScopeNode` (Spec 22A §2.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        rtype = str(self.get("type", "OBLIGATION"))
        _apply_entry_defaults(
            self,
            (
                ("id", ""),
                ("type", "OBLIGATION"),
                ("facet", _TYPE_TO_FACET.get(rtype, "obligations")),
                ("text", ""),
                ("scope", "global_defaults"),
                ("target_prop", None),
                ("bound_op", None),
                ("bound_val", None),
                ("conditions", []),
                ("is_exception", False),
                ("refs", []),
                ("overrides", []),
                ("exceptions", []),
                ("meta_keys", []),
                ("meta_tokens", []),
            ),
        )


class FlowEntry(_AttrDict):
    """Dict-compatible directed flow edge in `graph['flows']` (Spec 22A §2.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        _apply_entry_defaults(
            self,
            (
                ("id", ""),
                ("kind", "FLOW"),
                ("source", ""),
                ("target", ""),
                ("payload", None),
                ("action", None),
                ("condition", None),
                ("step", None),
                ("text", ""),
                ("dimensions", {}),
            ),
        )


class ArtifactEntry(_AttrDict):
    """Dict-compatible `SCHEMA` / `INTERFACE` / `LAW` entry in `graph['lattice_artifacts']`."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        raw_sid = next((_v for _v in (self.get("scope_id"), self.get("scope")) if _v), None)
        sid = str(raw_sid or "global_defaults")
        _apply_entry_defaults(
            self,
            (
                ("id", ""),
                ("kind", "SCHEMA"),
                ("name", ""),
                ("scope_id", sid),
                ("scope", sid),
                ("spec", ""),
                ("inputs", []),
                ("outputs", []),
                ("owner", None),
                ("stage", None),
                ("dimensions", {}),
                ("text", ""),
            ),
        )


class ScopeNode(_AttrDict):
    """Dict-compatible component / scope node inside a `ContextGraph` (Spec 22A §2.1)."""

    def __init__(self, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(data or {}, **kwargs)
        _apply_entry_defaults(
            self,
            (
                ("id", "global_defaults"),
                ("parent", None),
                ("children", []),
                ("aliases", []),
                ("attributes", {}),
                ("rules", []),
                ("in_edges", []),
                ("_meta", {}),
                *_SCOPE_SBF_DEFAULTS,
            ),
            with_turns=False,
        )
        cur_st = str(self.get("status", self.get("build_status")) or "DRAFT")
        self["status"] = self["build_status"] = cur_st

    def __getattr__(self, name: str) -> Any:
        return self.get("_meta", {}) if name == "_meta" else super().__getattr__(name)


def _normalize_scope_entry(sid: str, sc: dict[str, Any]) -> None:
    sc.setdefault("id", sid)
    if not bool(sc.get("attributes")):
        sc["attributes"] = {}
    attrs, raw_st = sc["attributes"], sc.get("status")
    b_st = (
        next((_v for _v in (sc.get("build_status"), attrs.get("status")) if _v), None)
        or attrs.get("build_status")
    )
    chosen_st = b_st if raw_st in (None, "", "DRAFT") and b_st else (raw_st or b_st or "DRAFT")
    sc["status"] = sc["build_status"] = str(chosen_st).strip().upper()
    if sc["status"] != "VERIFIED":
        sc["verified_untested"] = False
    raw_files = sc.get("files") if sc.get("files") is not None else attrs.get("files")
    raw_tests = sc.get("tests")
    if raw_tests is None:
        if sc.get("test_files") is not None:
            raw_tests = sc.get("test_files")
        elif attrs.get("tests") is not None:
            raw_tests = attrs.get("tests")
        else:
            raw_tests = attrs.get("test_files")
    files_list: list[str] = []
    tests_list: list[str] = []
    for f_entry in _clean_str_list(raw_files):
        if f_entry.lower().startswith("tests:"):
            if (t_val := f_entry.split(":", 1)[1].strip()) and t_val not in tests_list:
                tests_list.append(t_val)
        elif f_entry not in files_list:
            files_list.append(f_entry)
    for t_entry in _clean_str_list(raw_tests):
        clean_t = (
            t_entry.split(":", 1)[1].strip()
            if t_entry.lower().startswith("tests:")
            else t_entry
        )
        if clean_t and clean_t not in tests_list:
            tests_list.append(clean_t)
    sc["files"], sc["tests"] = files_list, tests_list
    sc.pop("test_files", None)
    sc["depends_on"] = _clean_str_list(sc.get("depends_on"))
    sc["claimed_by"] = None if sc["status"] == "VERIFIED" else _clean_opt_str(sc.get("claimed_by"))
    if sc["claimed_by"] is None:
        sc["claimed_at"] = None
    for k in ("rules", "children", "aliases", "in_edges"):
        if sc.get(k) is None:
            sc[k] = []
    if sc.get("_meta") is None:
        sc["_meta"] = {}


def normalize_board_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Normalizes `scopes`, `flows`, `lattice_artifacts`, and `external_links` in-place."""
    if not isinstance(graph, dict):
        return graph
    if (scopes := graph.get("scopes")) is None:
        scopes = graph["scopes"] = {}
    if isinstance(scopes, dict):
        for sid, sc in scopes.items():
            if sc:
                _normalize_scope_entry(str(sid), sc)
    if graph.get("flows") is None:
        graph["flows"] = []
    else:
        for fl in list(graph["flows"]):
            fl.setdefault("kind", "FLOW")
            fl.setdefault("source", "")
            fl.setdefault("target", "")
            if not isinstance(fl.get("dimensions"), dict):
                fl["dimensions"] = {}
    if graph.get("lattice_artifacts") is None:
        graph["lattice_artifacts"] = []
    else:
        for art in list(graph["lattice_artifacts"]):
            raw_sid = art.get("scope_id", art.get("scope")) or "global_defaults"
            art["scope_id"] = art["scope"] = str(raw_sid)
            art["name"] = str(art.get("name", art.get("id")) or "")
            if not isinstance(art.get("dimensions"), dict):
                art["dimensions"] = {}
            dims = art["dimensions"]
            if not art.get("spec") and dims.get("spec", art.get("text")):
                art["spec"] = str(dims.get("spec", art.get("text")) or "")
            if not art.get("file") and dims.get("file"):
                art["file"] = str(dims.get("file"))
    ext = graph.get("external_links")
    if isinstance(ext, list):
        by_node: dict[str, list[dict[str, Any]]] = {}
        for lk in [x for x in ext if isinstance(x, dict) and x.get("node_id")]:
            by_node.setdefault(str(lk["node_id"]), []).append(lk)
        graph["external_links"] = by_node
    elif not isinstance(ext, dict):
        graph["external_links"] = {}
    return graph


def _ensure_scope_node_nosync(
    graph: dict[str, Any], scope_id: str, *, parent: str | None = None
) -> dict[str, Any]:
    """Ensures `scopes[scope_id]` exists with SBF defaults and wires `parent`/`children`."""
    if not isinstance(scope_id, str) or not scope_id.strip():
        raise ValueError(f"scope_id must be a non-empty str, got {scope_id!r}")
    sid, scopes = scope_id.strip(), graph.setdefault("scopes", {})
    if sid not in scopes or not isinstance(scopes[sid], dict):
        scopes[sid] = ScopeNode({"id": sid, "parent": parent})
    else:
        scope = scopes[sid]
        _apply_entry_defaults(
            scope,
            (
                ("id", sid),
                ("parent", parent),
                ("children", []),
                ("aliases", []),
                ("attributes", {}),
                ("rules", []),
                ("in_edges", []),
                ("_meta", {}),
                *_SCOPE_SBF_DEFAULTS,
            ),
            with_turns=False,
        )
        _normalize_scope_entry(sid, scope)
        if parent is not None and parent != sid:
            scope["parent"] = parent
    alias_idx = graph.setdefault("alias_index", {})
    alias_idx[sid.lower()] = sid
    for al in scopes[sid].get("aliases", []):
        if isinstance(al, str) and al.strip():
            alias_idx[al.strip().lower()] = sid
    eff_parent = scopes[sid].get("parent")
    if isinstance(eff_parent, str) and eff_parent.strip() and eff_parent.strip() != sid:
        p_children = _ensure_scope_node_nosync(graph, eff_parent.strip()).setdefault("children", [])
        if sid not in p_children:
            p_children.append(sid)
    return scopes[sid]
