"""Package init for Lattice core tests (zero-NLP, deterministic Markdown pipe-table engine)."""

from __future__ import annotations

import importlib
import sys

import lattice as _lattice

sys.modules.setdefault("core", _lattice)
sys.modules.setdefault("core.circuit", _lattice)

for _subpkg in ("graph_core", "whiteboard", "the_executive", "ai_memory", "the_table", "regiment"):
    _mod = importlib.import_module(f"lattice.{_subpkg}")
    sys.modules.setdefault(_subpkg, _mod)
    sys.modules.setdefault(f"core.circuit.{_subpkg}", _mod)
    for _key, _val in list(sys.modules.items()):
        if _key.startswith(f"lattice.{_subpkg}."):
            _suffix = _key[len(f"lattice.{_subpkg}.") :]
            sys.modules.setdefault(f"{_subpkg}.{_suffix}", _val)
            sys.modules.setdefault(f"core.circuit.{_subpkg}.{_suffix}", _val)
