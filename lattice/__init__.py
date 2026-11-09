"""Lattice: Deterministic Markdown Pipe-Table Driven Cognitive Architecture (`lattice`)."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
from pathlib import Path
import sys
import types as _types
from typing import Any

sys.modules.setdefault("lattice", sys.modules[__name__])
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if (_REGIMENT_NLP_ROOT := _REPO_ROOT / "regiment_nlp").is_dir() and str(_REGIMENT_NLP_ROOT) not in sys.path:
    sys.path.append(str(_REGIMENT_NLP_ROOT))

if "core" not in sys.modules:
    _core_mod = _types.ModuleType("core")
    _core_mod.__path__ = [str(Path(__file__).resolve().parent.parent)]
    _core_mod.circuit = sys.modules[__name__]
    sys.modules["core"] = _core_mod
sys.modules["core.circuit"] = sys.modules[__name__]


class _RegimentAliasLoader(importlib.abc.Loader):
    def __init__(self, target_name: str) -> None:
        self.target_name = target_name

    def create_module(self, spec: Any) -> Any:
        mod = importlib.import_module(self.target_name)
        sys.modules[spec.name] = mod
        if "." in spec.name:
            parent_name, attr = spec.name.rsplit(".", 1)
            if (parent := sys.modules.get(parent_name)) is not None:
                setattr(parent, attr, mod)
        return mod

    def exec_module(self, module: Any) -> None:
        pass


class _RegimentAliasFinder(importlib.abc.MetaPathFinder):
    _PHASE_PREFIXES = (
        "lattice.phase0", "lattice.phase1", "lattice.phase2", "lattice.phase3", "lattice.phase4",
        "lattice.regiment.phase0", "lattice.regiment.phase1", "lattice.regiment.phase2", "lattice.regiment.phase3", "lattice.regiment.phase4",
        "core.circuit.phase0", "core.circuit.phase1", "core.circuit.phase2", "core.circuit.phase3", "core.circuit.phase4",
        "core.circuit.regiment.phase0", "core.circuit.regiment.phase1", "core.circuit.regiment.phase2", "core.circuit.regiment.phase3", "core.circuit.regiment.phase4",
    )

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        target_name: str | None = None
        if fullname.endswith((".phase3.intake_packet", ".phase3.markdown_tables")):
            leaf = fullname.rsplit(".", 1)[-1]
            target_name = f"lattice.regiment.{leaf}"
        elif any(fullname == p or fullname.startswith(p + ".") for p in self._PHASE_PREFIXES):
            suffix = fullname.split(".phase", 1)[1]
            if suffix == "4" or suffix.startswith("4."):
                sub = suffix[2:] if suffix.startswith("4.") else ""
                target_name = f"lattice.whiteboard.{sub}" if sub else "lattice.whiteboard"
            else:
                target_name = f"regiment_nlp.regiment.phase{suffix}"
        elif fullname in ("lattice.http_backend", "lattice.onnx_backend", "lattice.prompts", "core.circuit.http_backend", "core.circuit.onnx_backend", "core.circuit.prompts"):
            leaf = fullname.rsplit(".", 1)[-1]
            target_name = f"regiment_nlp.{leaf}"
        elif fullname.startswith("core.circuit."):
            target_name = f"lattice.{fullname[len('core.circuit.'):]}"
        if not target_name or target_name == fullname:
            return None
        try:
            target_spec = importlib.util.find_spec(target_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            target_spec = None
        if target_spec is not None:
            is_pkg = target_spec.submodule_search_locations is not None
            spec = importlib.util.spec_from_loader(fullname, _RegimentAliasLoader(target_name), is_package=is_pkg)
            if is_pkg and spec is not None:
                spec.submodule_search_locations = list(target_spec.submodule_search_locations or [])
            return spec
        return None


if not any(type(f).__name__ == "_RegimentAliasFinder" for f in sys.meta_path):
    sys.meta_path.insert(0, _RegimentAliasFinder())
