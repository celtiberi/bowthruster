"""Spec 21 §1.1: Indexed ACID SQLite Graph Storage Engine (`SQLiteGraphStore`, `init_graph_db`)."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

_CORE_GRAPH_SCALARS = ("version", "turn", "k_cap", "max_ring")

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS graph_meta (key TEXT PRIMARY KEY, val_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scopes (
    id TEXT PRIMARY KEY, parent TEXT, aliases_json TEXT NOT NULL,
    children_json TEXT NOT NULL, in_edges_json TEXT NOT NULL, attributes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scopes_parent ON scopes(parent);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, node_type TEXT NOT NULL, facet TEXT NOT NULL,
    text TEXT NOT NULL, target_prop TEXT, bound_op TEXT, bound_val_json TEXT,
    conditions_json TEXT NOT NULL, refs_json TEXT NOT NULL, overrides_json TEXT NOT NULL,
    exceptions_json TEXT NOT NULL, meta_keys_json TEXT NOT NULL, meta_tokens_json TEXT NOT NULL,
    created_turn INTEGER NOT NULL, updated_turn INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_scope_id ON nodes(scope_id);
CREATE INDEX IF NOT EXISTS idx_nodes_scope_target_prop ON nodes(scope_id, target_prop);
CREATE INDEX IF NOT EXISTS idx_nodes_node_type ON nodes(node_type);
CREATE TABLE IF NOT EXISTS node_meta (
    node_id TEXT NOT NULL, scope_id TEXT NOT NULL, meta_key TEXT NOT NULL, meta_val TEXT NOT NULL,
    PRIMARY KEY (node_id, meta_key)
);
CREATE TABLE IF NOT EXISTS external_links (
    link_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, uri TEXT NOT NULL,
    kind TEXT NOT NULL, summary TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_external_links_node_id ON external_links(node_id);
CREATE TABLE IF NOT EXISTS active_tasks (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
    depends_on_json TEXT NOT NULL, governed_by_json TEXT NOT NULL, created_turn INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ambient_notes (
    id TEXT PRIMARY KEY, text TEXT NOT NULL, access_turns_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS open_blockers (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, scope TEXT NOT NULL,
    text TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flows (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
    payload TEXT, action TEXT, condition TEXT, step INTEGER,
    text TEXT NOT NULL, dimensions_json TEXT NOT NULL, turn INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flows_src_tgt ON flows(source, target);
CREATE TABLE IF NOT EXISTS lattice_artifacts (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, scope_id TEXT NOT NULL,
    name TEXT NOT NULL, spec TEXT NOT NULL, inputs_json TEXT NOT NULL,
    outputs_json TEXT NOT NULL, owner TEXT, stage TEXT,
    dimensions_json TEXT NOT NULL, text TEXT NOT NULL, turn INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lattice_artifacts_coords ON lattice_artifacts(scope_id, kind, owner, stage);
"""


def _safe_int(val: Any, default: int | None = None) -> int | None:
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    if isinstance(val, str) and val.strip().lstrip("-").isdigit():
        return int(val.strip())
    return default


def init_graph_db(conn: sqlite3.Connection) -> None:
    """Initializes the 10 canonical Spec 21 §1.1 relational tables and indices on `conn`."""
    with conn:
        conn.executescript(_SCHEMA_DDL)


def _row_to_node_dict(
    row: sqlite3.Row,
    meta_by_node: dict[str, dict[str, str]] | None = None,
    links_by_node: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Converts a `nodes` table row into a canonical rule/node dictionary."""
    nid, bound_val_raw = str(row["id"]), row["bound_val_json"]
    node: dict[str, Any] = {
        "id": nid, "scope": str(row["scope_id"]), "type": str(row["node_type"]),
        "facet": str(row["facet"]), "text": str(row["text"]),
        "target_prop": row["target_prop"], "bound_op": row["bound_op"],
        "bound_val": json.loads(bound_val_raw) if bound_val_raw is not None else None,
        "conditions": json.loads(row["conditions_json"]), "refs": json.loads(row["refs_json"]),
        "overrides": json.loads(row["overrides_json"]), "exceptions": json.loads(row["exceptions_json"]),
        "meta_keys": json.loads(row["meta_keys_json"]), "meta_tokens": json.loads(row["meta_tokens_json"]),
        "created_turn": int(row["created_turn"]), "updated_turn": int(row["updated_turn"]),
    }
    if meta_by_node and nid in meta_by_node:
        node["meta_keys"] = sorted(meta_by_node[nid].keys())
    if links_by_node and nid in links_by_node:
        node["external_links"] = copy.deepcopy(links_by_node[nid])
    return node


class SQLiteGraphStore:
    """Indexed ACID SQLite persistence backend for `whiteboard`, `the_table`, and `ai_memory` (Spec 21 §1.1)."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000;")
        if self.db_path != ":memory:" and not self.db_path.startswith("file::memory:"):
            self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        init_graph_db(self.conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> SQLiteGraphStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _upsert_link_row(
        self, node_id: str, uri: str, kind: str = "reference",
        summary: str = "", metadata: dict[str, Any] | None = None, link_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError(f"node_id must be a non-empty str, got {node_id!r}")
        if not isinstance(uri, str) or not uri.strip():
            raise ValueError(f"uri must be a non-empty str, got {uri!r}")
        nid, uri_clean = node_id.strip(), uri.strip()
        kind_clean = str(kind or "reference").strip() or "reference"
        sum_clean = str(summary or "").strip()
        meta_dict = copy.deepcopy(metadata) if isinstance(metadata, dict) else {}
        if link_id and isinstance(link_id, str) and link_id.strip():
            lid = link_id.strip()
        else:
            digest = hashlib.sha256(f"{nid}|{uri_clean}|{kind_clean}".encode("utf-8")).hexdigest()[:12]
            lid = f"link_{nid}_{digest}"
        self.conn.execute(
            "INSERT OR REPLACE INTO external_links(link_id, node_id, uri, kind, summary, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
            (lid, nid, uri_clean, kind_clean, sum_clean, json.dumps(meta_dict, sort_keys=True)),
        )
        return {
            "link_id": lid, "node_id": nid, "uri": uri_clean,
            "kind": kind_clean, "summary": sum_clean,
            "metadata": meta_dict
        }

    def upsert_external_link(
        self, node_id: str, uri: str, kind: str = "reference",
        summary: str = "", metadata: dict[str, Any] | None = None, *, link_id: str | None = None,
    ) -> dict[str, Any]:
        """Upserts an indexed LOD 2 external source pointer in `external_links` (Spec 21 §1.1)."""
        with self.conn:
            return self._upsert_link_row(node_id, uri, kind, summary, metadata, link_id)

    def get_external_links(self, node_id: str) -> list[dict[str, Any]]:
        """Returns all external links attached to `node_id` in insertion order (Spec 21 §1.1)."""
        if not isinstance(node_id, str) or not node_id.strip():
            return []
        rows = self.conn.execute(
            "SELECT link_id, node_id, uri, kind, summary, metadata_json FROM external_links WHERE node_id = ? ORDER BY rowid ASC",
            (node_id.strip(),),
        ).fetchall()
        return [
            {
                "link_id": str(r["link_id"]), "node_id": str(r["node_id"]), "uri": str(r["uri"]),
                "kind": str(r["kind"]), "summary": str(r["summary"]), "metadata": json.loads(r["metadata_json"]),
            }
            for r in rows
        ]

    def save_graph(self, graph: dict[str, Any]) -> None:
        """Atomically syncs `graph` into the 10 SQLite relational tables inside a single transaction (Spec 21 §1.1)."""
        if not isinstance(graph, dict):
            raise ValueError(f"graph must be a dict, got {type(graph).__name__}")

        with self.conn:
            for tbl in (
                "graph_meta", "scopes", "nodes", "node_meta", "active_tasks",
                "ambient_notes", "open_blockers", "flows", "lattice_artifacts",
            ):
                self.conn.execute(f"DELETE FROM {tbl}")
            if "external_links" in graph:
                self.conn.execute("DELETE FROM external_links")

            meta_scalars: dict[str, Any] = {
                "version": int(graph.get("version", 1)), "turn": int(graph.get("turn", 0)),
                "k_cap": int(graph.get("k_cap", 5)), "max_ring": int(graph.get("max_ring", 10)),
            }
            if "flows" in graph:
                meta_scalars["_has_flows"] = True
            if "lattice_artifacts" in graph:
                meta_scalars["_has_lattice_artifacts"] = True

            base_scope_keys = ("id", "parent", "aliases", "children", "rules", "in_edges", "attributes", "_meta")
            for sid, scope in graph.get("scopes", {}).items():
                if not isinstance(scope, dict):
                    continue
                attrs_dict = dict(scope.get("attributes", {})) if isinstance(scope.get("attributes"), dict) else {}
                if sbf_extra := {k: v for k, v in scope.items() if k not in base_scope_keys}:
                    attrs_dict["_sbf"] = sbf_extra
                self.conn.execute(
                    "INSERT INTO scopes(id, parent, aliases_json, children_json, in_edges_json, attributes_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(sid), scope.get("parent"), json.dumps(list(scope.get("aliases", []))),
                        json.dumps(list(scope.get("children", []))), json.dumps(list(scope.get("in_edges", []))),
                        json.dumps(attrs_dict, sort_keys=True),
                    ),
                )
                scope_meta = scope.get("_meta", {}) if isinstance(scope.get("_meta"), dict) else {}
                for r in scope.get("rules", []):
                    if not isinstance(r, dict) or not r.get("id"):
                        continue
                    rid, bval = str(r["id"]), r.get("bound_val")
                    self.conn.execute(
                        "INSERT OR REPLACE INTO nodes(id, scope_id, node_type, facet, text, target_prop, bound_op, bound_val_json, "
                        "conditions_json, refs_json, overrides_json, exceptions_json, meta_keys_json, meta_tokens_json, created_turn, updated_turn) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            rid, str(r.get("scope") or sid), str(r.get("type") or r.get("node_type") or "FACT"),
                            str(r.get("facet") or "context_facts"), str(r.get("text") or ""), r.get("target_prop"),
                            r.get("bound_op"), json.dumps(bval) if bval is not None else None,
                            json.dumps(list(r.get("conditions", []))), json.dumps(list(r.get("refs", []))),
                            json.dumps(list(r.get("overrides", []))), json.dumps(list(r.get("exceptions", []))),
                            json.dumps(list(r.get("meta_keys", []))), json.dumps(list(r.get("meta_tokens", []))),
                            int(r.get("created_turn", 1)), int(r.get("updated_turn", 1)),
                        ),
                    )
                    if isinstance(r.get("external_links"), list):
                        for lk in r["external_links"]:
                            if isinstance(lk, dict) and lk.get("uri"):
                                self._upsert_link_row(
                                    rid, str(lk["uri"]), str(lk.get("kind", "reference")),
                                    str(lk.get("summary", "")), lk.get("metadata"), lk.get("link_id"),
                                )
                for rid, rmeta in scope_meta.items():
                    if isinstance(rmeta, dict):
                        for mk, mv in rmeta.items():
                            self.conn.execute(
                                "INSERT OR REPLACE INTO node_meta(node_id, scope_id, meta_key, meta_val) VALUES (?, ?, ?, ?)",
                                (str(rid), str(sid), str(mk), str(mv)),
                            )

            ext_links_top = graph.get("external_links")
            if isinstance(ext_links_top, dict):
                for nid, lk_list in ext_links_top.items():
                    if isinstance(lk_list, list):
                        for lk in lk_list:
                            if isinstance(lk, dict) and lk.get("uri"):
                                self._upsert_link_row(
                                    str(nid), str(lk["uri"]), str(lk.get("kind", "reference")),
                                    str(lk.get("summary", "")), lk.get("metadata"), lk.get("link_id"),
                                )
            elif isinstance(ext_links_top, list):
                for lk in ext_links_top:
                    if isinstance(lk, dict) and lk.get("node_id") and lk.get("uri"):
                        self._upsert_link_row(
                            str(lk["node_id"]), str(lk["uri"]), str(lk.get("kind", "reference")),
                            str(lk.get("summary", "")), lk.get("metadata"), lk.get("link_id"),
                        )

            task_extras: dict[str, dict[str, Any]] = {}
            base_task_keys = ("id", "scope", "text", "status", "depends_on", "governed_by", "created_turn")
            for t in graph.get("active_tasks", []):
                if not isinstance(t, dict) or not t.get("id"):
                    continue
                tid = str(t["id"])
                if t_ext := {k: v for k, v in t.items() if k not in base_task_keys}:
                    task_extras[tid] = t_ext
                self.conn.execute(
                    "INSERT OR REPLACE INTO active_tasks(id, scope, text, status, depends_on_json, governed_by_json, created_turn) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        tid, str(t.get("scope", "global_defaults")), str(t.get("text", "")),
                        str(t.get("status", "ACTIVE")), json.dumps(list(t.get("depends_on", []))),
                        json.dumps(list(t.get("governed_by", []))), int(t.get("created_turn", 1)),
                    ),
                )

            for n in graph.get("ambient_notes", []):
                if isinstance(n, dict) and n.get("id"):
                    self.conn.execute(
                        "INSERT OR REPLACE INTO ambient_notes(id, text, access_turns_json) VALUES (?, ?, ?)",
                        (str(n["id"]), str(n.get("text", "")), json.dumps(list(n.get("access_turns", [1])))),
                    )

            for b in graph.get("open_blockers", []):
                if isinstance(b, dict) and b.get("id"):
                    extra_payload = {k: v for k, v in b.items() if k not in ("id", "type", "scope", "text", "status")}
                    self.conn.execute(
                        "INSERT OR REPLACE INTO open_blockers(id, type, scope, text, status, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(b["id"]), str(b.get("type", "OPEN")), str(b.get("scope", "global_defaults")),
                            str(b.get("text", "")), str(b.get("status", "OPEN")), json.dumps(extra_payload, sort_keys=True),
                        ),
                    )

            base_flow_keys = (
                "id", "source", "target", "payload",
                "action", "condition", "step", "text",
                "dimensions", "turn"
            )
            for idx, fl in enumerate(graph.get("flows", [])):
                if not isinstance(fl, dict):
                    continue
                fid = str(fl.get("id") or f"flow_{idx + 1}")
                dims = dict(fl["dimensions"]) if isinstance(fl.get("dimensions"), dict) else {}
                f_extra = {k: v for k, v in fl.items() if k not in base_flow_keys}
                if "dimensions" not in fl:
                    f_extra["_omit_dims"] = True
                if "id" not in fl:
                    f_extra["_omit_id"] = True
                if f_extra:
                    dims["_extra"] = f_extra
                self.conn.execute(
                    "INSERT OR REPLACE INTO flows(id, source, target, payload, action, condition, step, text, dimensions_json, turn) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fid, str(fl.get("source", "")), str(fl.get("target", "")),
                        fl.get("payload"), fl.get("action"), fl.get("condition"),
                        _safe_int(fl.get("step"), None),
                        str(fl.get("text") if fl.get("text") is not None else fl.get("summary", "")),
                        json.dumps(dims, sort_keys=True), _safe_int(fl.get("turn"), _safe_int(graph.get("turn"), 1)) or 1,
                    ),
                )

            base_art_keys = (
                "id", "kind", "scope_id", "name", "spec",
                "inputs", "outputs", "owner", "stage",
                "dimensions", "text", "turn"
            )
            for idx, art in enumerate(graph.get("lattice_artifacts", [])):
                if not isinstance(art, dict):
                    continue
                aid = str(art.get("id") or f"art_{idx + 1}")
                dims = dict(art["dimensions"]) if isinstance(art.get("dimensions"), dict) else {}
                a_extra = {k: v for k, v in art.items() if k not in base_art_keys}
                if "dimensions" not in art:
                    a_extra["_omit_dims"] = True
                if "scope_id" not in art:
                    a_extra["_omit_scope_id"] = True
                if a_extra:
                    dims["_extra"] = a_extra
                self.conn.execute(
                    "INSERT OR REPLACE INTO lattice_artifacts(id, kind, scope_id, name, spec, inputs_json, outputs_json, "
                    "owner, stage, dimensions_json, text, turn) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        aid, str(art.get("kind", "SCHEMA")),
                        str(art.get("scope_id") or art.get("scope") or "global_defaults"),
                        str(art.get("name") or aid), str(art.get("spec") or ""),
                        json.dumps(list(art.get("inputs", []))), json.dumps(list(art.get("outputs", []))),
                        art.get("owner"), art.get("stage"), json.dumps(dims, sort_keys=True),
                        str(art.get("text") if art.get("text") is not None else art.get("summary", "")),
                        _safe_int(art.get("turn"), _safe_int(graph.get("turn"), 1)) or 1,
                    ),
                )

            if task_extras:
                meta_scalars["_task_extras"] = task_extras
            for k, v in meta_scalars.items():
                self.conn.execute("INSERT INTO graph_meta(key, val_json) VALUES (?, ?)", (k, json.dumps(v)))

    def load_graph(self) -> dict[str, Any]:
        """Reconstructs the canonical graph dictionary from the 10 SQLite tables (Spec 21 §1.1)."""
        scalars: dict[str, Any] = {"version": 1, "turn": 0, "k_cap": 5, "max_ring": 10}
        for r in self.conn.execute("SELECT key, val_json FROM graph_meta").fetchall():
            scalars[str(r["key"])] = json.loads(r["val_json"])
        task_extras = scalars.get("_task_extras", {}) if isinstance(scalars.get("_task_extras"), dict) else {}

        meta_by_scope_node: dict[str, dict[str, dict[str, str]]] = {}
        meta_by_node: dict[str, dict[str, str]] = {}
        for r in self.conn.execute("SELECT node_id, scope_id, meta_key, meta_val FROM node_meta ORDER BY rowid ASC").fetchall():
            nid, sid, mk, mv = str(r["node_id"]), str(r["scope_id"]), str(r["meta_key"]), str(r["meta_val"])
            meta_by_scope_node.setdefault(sid, {}).setdefault(nid, {})[mk] = mv
            meta_by_node.setdefault(nid, {})[mk] = mv

        links_by_node: dict[str, list[dict[str, Any]]] = {}
        for r in self.conn.execute("SELECT link_id, node_id, uri, kind, summary, metadata_json FROM external_links ORDER BY rowid ASC").fetchall():
            nid = str(r["node_id"])
            links_by_node.setdefault(nid, []).append({
                "link_id": str(r["link_id"]), "node_id": nid, "uri": str(r["uri"]),
                "kind": str(r["kind"]), "summary": str(r["summary"]), "metadata": json.loads(r["metadata_json"]),
            })

        rules_by_scope: dict[str, list[dict[str, Any]]] = {}
        for r in self.conn.execute("SELECT * FROM nodes ORDER BY rowid ASC").fetchall():
            rules_by_scope.setdefault(str(r["scope_id"]), []).append(_row_to_node_dict(r, meta_by_node, links_by_node))

        scopes: dict[str, Any] = {}
        for r in self.conn.execute("SELECT * FROM scopes ORDER BY rowid ASC").fetchall():
            sid = str(r["id"])
            attrs_loaded = json.loads(r["attributes_json"])
            sbf_loaded = attrs_loaded.pop("_sbf", None) if isinstance(attrs_loaded, dict) else None
            s_dict: dict[str, Any] = {
                "id": sid, "parent": r["parent"],
                "aliases": json.loads(r["aliases_json"]), "children": json.loads(r["children_json"]),
                "rules": rules_by_scope.get(sid, []), "in_edges": json.loads(r["in_edges_json"]),
                "attributes": attrs_loaded, "_meta": meta_by_scope_node.get(sid, {}),
            }
            if isinstance(sbf_loaded, dict):
                s_dict.update(sbf_loaded)
            scopes[sid] = s_dict

        active_tasks: list[dict[str, Any]] = []
        for r in self.conn.execute("SELECT * FROM active_tasks ORDER BY rowid ASC").fetchall():
            tid = str(r["id"])
            t_dict: dict[str, Any] = {
                "id": tid, "scope": str(r["scope"]), "text": str(r["text"]), "status": str(r["status"]),
                "depends_on": json.loads(r["depends_on_json"]), "governed_by": json.loads(r["governed_by_json"]),
                "created_turn": int(r["created_turn"]),
            }
            if tid in task_extras and isinstance(task_extras[tid], dict):
                t_dict.update(copy.deepcopy(task_extras[tid]))
            active_tasks.append(t_dict)

        ambient_notes = [
            {"id": str(r["id"]), "text": str(r["text"]), "access_turns": json.loads(r["access_turns_json"])}
            for r in self.conn.execute("SELECT * FROM ambient_notes ORDER BY rowid ASC").fetchall()
        ]
        open_blockers: list[dict[str, Any]] = []
        for r in self.conn.execute("SELECT * FROM open_blockers ORDER BY rowid ASC").fetchall():
            b_dict: dict[str, Any] = {
                "id": str(r["id"]), "type": str(r["type"]), "scope": str(r["scope"]),
                "text": str(r["text"]), "status": str(r["status"]),
            }
            b_dict.update(json.loads(r["payload_json"]))
            open_blockers.append(b_dict)

        flows_list: list[dict[str, Any]] = []
        for r in self.conn.execute("SELECT * FROM flows ORDER BY rowid ASC").fetchall():
            fid = str(r["id"])
            dims_loaded = json.loads(r["dimensions_json"])
            f_extra = dims_loaded.pop("_extra", {}) if isinstance(dims_loaded, dict) else {}
            omit_dims = f_extra.pop("_omit_dims", False) if isinstance(f_extra, dict) else False
            omit_id = f_extra.pop("_omit_id", False) if isinstance(f_extra, dict) else False
            f_dict: dict[str, Any] = {
                "id": fid, "source": str(r["source"]), "target": str(r["target"]),
                "payload": r["payload"], "action": r["action"], "condition": r["condition"],
                "step": int(r["step"]) if r["step"] is not None else None,
                "text": str(r["text"]), "dimensions": dims_loaded, "turn": int(r["turn"]),
            }
            if omit_dims and not dims_loaded:
                f_dict.pop("dimensions", None)
            if omit_id:
                f_dict.pop("id", None)
            if isinstance(f_extra, dict) and f_extra:
                f_dict.update(f_extra)
            flows_list.append(f_dict)

        artifacts_list: list[dict[str, Any]] = []
        for r in self.conn.execute("SELECT * FROM lattice_artifacts ORDER BY rowid ASC").fetchall():
            aid = str(r["id"])
            dims_loaded = json.loads(r["dimensions_json"])
            a_extra = dims_loaded.pop("_extra", {}) if isinstance(dims_loaded, dict) else {}
            omit_dims = a_extra.pop("_omit_dims", False) if isinstance(a_extra, dict) else False
            omit_scope_id = a_extra.pop("_omit_scope_id", False) if isinstance(a_extra, dict) else False
            a_dict: dict[str, Any] = {
                "id": aid, "kind": str(r["kind"]), "scope_id": str(r["scope_id"]),
                "name": str(r["name"]), "spec": str(r["spec"]),
                "inputs": json.loads(r["inputs_json"]), "outputs": json.loads(r["outputs_json"]),
                "owner": r["owner"], "stage": r["stage"],
                "dimensions": dims_loaded, "text": str(r["text"]), "turn": int(r["turn"]),
            }
            if omit_dims and not dims_loaded:
                a_dict.pop("dimensions", None)
            if omit_scope_id:
                a_dict.pop("scope_id", None)
            if isinstance(a_extra, dict) and a_extra:
                a_dict.update(a_extra)
            artifacts_list.append(a_dict)

        out: dict[str, Any] = {
            "version": int(scalars.get("version", 1)), "turn": int(scalars.get("turn", 0)),
            "k_cap": int(scalars.get("k_cap", 5)), "max_ring": int(scalars.get("max_ring", 10)),
            "scopes": scopes, "active_tasks": active_tasks,
            "ambient_notes": ambient_notes, "open_blockers": open_blockers,
        }
        if flows_list or scalars.get("_has_flows"):
            out["flows"] = flows_list
        if artifacts_list or scalars.get("_has_lattice_artifacts"):
            out["lattice_artifacts"] = artifacts_list
        if links_by_node:
            out["external_links"] = links_by_node
        return out

    def query_nodes_by_scope(self, scope_id: str) -> list[dict[str, Any]]:
        """Indexed SQLite lookup returning all nodes in `scope_id` (Spec 21 §1.1)."""
        if not isinstance(scope_id, str) or not scope_id.strip():
            return []
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE scope_id = ? ORDER BY rowid ASC",
            (scope_id.strip(),)
        ).fetchall()
        return [_row_to_node_dict(r) for r in rows]

    def query_nodes_by_target_prop(self, scope_id: str, target_prop: str) -> list[dict[str, Any]]:
        """Indexed SQLite lookup returning all nodes matching `(scope_id, target_prop)` (Spec 21 §1.1)."""
        if not isinstance(scope_id, str) or not isinstance(target_prop, str):
            return []
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE scope_id = ? AND target_prop = ? ORDER BY rowid ASC",
            (scope_id.strip(), target_prop.strip()),
        ).fetchall()
        return [_row_to_node_dict(r) for r in rows]
