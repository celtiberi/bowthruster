"""Multi-board `LOD_0` Lattice Atlas topology collector and living spec renderer (`atlas_renderer.py`)."""

from __future__ import annotations

from typing import Any

from .schema import _pick

_BOARD_TOP_KEYS = frozenset({
    "scopes", "flows", "lattice_artifacts", "rules",
    "facts", "open_blockers", "lattice_proof", "ambient_notes",
})


def _normalize_atlas_boards(
    whiteboard_boards: dict[str, dict[str, Any]] | list[dict[str, Any]] | dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Normalizes a board dict, list of board dicts, or `{board_name: board_dict}` into pairs."""
    if isinstance(whiteboard_boards, dict):
        dict_vals = [v for v in whiteboard_boards.values() if isinstance(v, dict)]
        is_multi = bool(dict_vals) and not bool(_BOARD_TOP_KEYS & set(whiteboard_boards.keys()))
        if is_multi:
            return [
                (str(k).strip() or f"board_{i + 1}", v)
                for i, (k, v) in enumerate(whiteboard_boards.items())
                if isinstance(v, dict)
            ]
        bname = (
            str(_pick(whiteboard_boards, "board_name", "board", "name", default="default")).strip()
            or "default"
        )
        return [(bname, whiteboard_boards)]
    if isinstance(whiteboard_boards, list):
        return [
            (
                str(
                    _pick(b, "board_name", "board", "name", default=f"board_{i + 1}")
                ).strip()
                or f"board_{i + 1}",
                b,
            )
            for i, b in enumerate(whiteboard_boards)
        ]
    msg = f"whiteboard_boards must be a dict or list of board dicts, got {type(whiteboard_boards).__name__}"
    raise ValueError(msg)


def _collect_atlas_flows_and_gaps(
    boards: list[tuple[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, str], int, bool, set[str]]:
    flows: list[dict[str, Any]] = []
    seen_flows: set[tuple[str, str, str]] = set()
    ref_sids: dict[str, str] = {}
    explicit_gaps = 0
    saw_explicit_gaps = False
    explicit_hubs: set[str] = set()
    for bname, board in boards:
        for fl in board.get("flows") or []:
            if not fl:
                continue
            src = str(fl.get("source") or "").strip()
            tgt = str(fl.get("target") or "").strip()
            payload = str(fl.get("payload") or "").strip() or None
            if src and tgt:
                if src != "global_defaults":
                    ref_sids.setdefault(src, bname)
                if tgt != "global_defaults":
                    ref_sids.setdefault(tgt, bname)
                fkey = (src, tgt, payload or "")
                if fkey not in seen_flows:
                    seen_flows.add(fkey)
                    flows.append({"source": src, "target": tgt, "payload": payload, "board": bname})
        for art in board.get("lattice_artifacts") or []:
            asid = str(_pick(art, "scope_id", "scope", default="")).strip()
            if asid and asid != "global_defaults":
                ref_sids.setdefault(asid, bname)
        lp = board.get("lattice_proof") or {}
        for g_src in (board.get("open_gaps"), lp.get("open_gaps")):
            if g_src is not None and not isinstance(g_src, bool):
                if isinstance(g_src, int):
                    saw_explicit_gaps = True
                    explicit_gaps += max(0, g_src)
                elif isinstance(g_src, list):
                    saw_explicit_gaps = True
                    explicit_gaps += len(g_src)
                elif isinstance(g_src, str) and g_src.strip().isdigit():
                    saw_explicit_gaps = True
                    explicit_gaps += int(g_src.strip())
        for h_src in (board.get("hub_bottlenecks"), lp.get("hub_bottlenecks")):
            if isinstance(h_src, list):
                explicit_hubs.update(str(h).strip() for h in h_src if str(h).strip())
        for blk in board.get("open_blockers") or []:
            if isinstance(blk, dict) and str(blk.get("status") or "").upper() != "RESOLVED":
                saw_explicit_gaps = True
                explicit_gaps += 1
    return flows, ref_sids, explicit_gaps, saw_explicit_gaps, explicit_hubs


def _is_sbf_component_scope(s_id: str, s_d: dict[str, Any], ref_sids: dict[str, str]) -> bool:
    attrs = s_d.get("attributes") or {}
    merged = {**attrs, **s_d}
    has_deps = any(str(d).strip() and str(d).strip() != s_id for d in (s_d.get("depends_on") or []))
    st_val = str(_pick(s_d, "build_status", "status", default="DRAFT")).strip().upper() or "DRAFT"
    return bool(
        str(_pick(merged, "role", "owner", "stage", default="")).strip()
        or has_deps
        or s_id in ref_sids
        or str(s_d.get("kind") or "").upper() == "COMPONENT"
        or merged.get("files")
        or st_val != "DRAFT"
    )


def _collect_atlas_topology(
    boards: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Two-pass topology collector gathering flows, component scopes, and `atlas_metrics`."""
    components: dict[str, dict[str, Any]] = {}
    flows, ref_sids, explicit_gaps, saw_explicit_gaps, explicit_hubs = (
        _collect_atlas_flows_and_gaps(boards)
    )
    for bname, board in boards:
        scopes = board.get("scopes") or {}
        board_has_sbf_comps = bool(ref_sids) or any(
            _is_sbf_component_scope(str(k).strip(), v, ref_sids)
            for k, v in scopes.items()
            if str(k).strip() and str(k).strip() != "global_defaults" and isinstance(v, dict)
        )
        for raw_sid, sc in scopes.items():
            sid = str(raw_sid).strip()
            is_global = not sid or sid == "global_defaults" or sid.startswith("global_defaults::")
            if is_global or not isinstance(sc, dict):
                continue
            raw_st = str(sc.get("status") or "").strip().upper()
            raw_bst = str(sc.get("build_status") or "").strip().upper()
            if "STALE" in (raw_st, raw_bst):
                st = "STALE"
            elif raw_st not in ("", "DRAFT"):
                st = raw_st
            else:
                st = raw_bst or "DRAFT"
            sc_attrs = sc.get("attributes") or {}
            merged = {**sc_attrs, **sc}
            owner = str(merged.get("owner") or "").strip() or None
            role = str(_pick(merged, "role", "_table_role", default="")).strip() or None
            stage = str(merged.get("stage") or "").strip() or None
            why = str(_pick(merged, "why", "rationale", default="")).strip() or None
            raw_files = merged.get("files") or []
            file_iter = raw_files.split(",") if isinstance(raw_files, str) else raw_files
            files = [str(f).strip() for f in file_iter if str(f).strip()]
            deps = [
                str(d).strip()
                for d in (sc.get("depends_on") or [])
                if str(d).strip() and str(d).strip() != sid
            ]
            untested = bool(merged.get("verified_untested"))
            claimed_by = (
                (str(merged.get("claimed_by") or "").strip() or None) if st != "VERIFIED" else None
            )
            has_comp = _is_sbf_component_scope(sid, sc, ref_sids)
            if not (has_comp or not board_has_sbf_comps):
                continue
            if not (has_comp or sc.get("build_status") or sc.get("status")):
                continue
            if sid not in components:
                components[sid] = {
                    "id": sid,
                    "board": bname,
                    "build_status": st,
                    "status": st,
                    "verified_untested": untested,
                    "claimed_by": claimed_by,
                    "owner": owner,
                    "role": role,
                    "why": why,
                    "stage": stage,
                    "depends_on": list(deps),
                    "files": files,
                }
            else:
                cur = components[sid]
                if st != "DRAFT" and cur["build_status"] == "DRAFT":
                    cur["build_status"] = cur["status"] = st
                if untested:
                    cur["verified_untested"] = True
                if claimed_by and not cur.get("claimed_by"):
                    cur["claimed_by"] = claimed_by
                if owner and not cur["owner"]:
                    cur["owner"], cur["board"] = owner, bname
                for field_k, field_v in (
                    ("role", role),
                    ("why", why),
                    ("stage", stage),
                    ("files", files),
                ):
                    if field_v and not cur.get(field_k):
                        cur[field_k] = field_v
                for d in deps:
                    if d not in cur["depends_on"]:
                        cur["depends_on"].append(d)
    for rsid, bname in ref_sids.items():
        if rsid not in components:
            components[rsid] = {
                "id": rsid,
                "board": bname,
                "build_status": "DRAFT",
                "status": "DRAFT",
                "verified_untested": False,
                "claimed_by": None,
                "owner": None,
                "role": None,
                "why": None,
                "stage": None,
                "depends_on": [],
                "files": [],
            }
    dependents_map: dict[str, set[str]] = {sid: set() for sid in components}
    for sid, comp in components.items():
        for dep in comp["depends_on"]:
            dependents_map.setdefault(dep, set()).add(sid)
    hub_bottlenecks = sorted(
        explicit_hubs | {sid for sid, imps in dependents_map.items() if len(imps) >= 3}
    )
    inferred_gaps = sum(
        1
        for c in components.values()
        if not c["owner"] or c["build_status"] in ("FAILED", "BLOCKED_BY_DEP")
    )
    board_versions: dict[str, dict[str, Any]] = {}
    for bname, bdict in boards:
        act_v = int(bdict.get("schema_version") or 1)
        disk_v = int(bdict.get("_on_disk_schema_version", act_v))
        board_versions[bname] = {
            "on_disk": disk_v,
            "active": act_v,
            "needs_migration": disk_v < act_v,
        }
    tested_verified = sum(
        1
        for c in components.values()
        if c["build_status"] == "VERIFIED" and not bool(c.get("verified_untested"))
    )
    untested_verified = sum(
        1
        for c in components.values()
        if c["build_status"] == "VERIFIED" and bool(c.get("verified_untested"))
    )
    metrics: dict[str, Any] = {
        "total_components": len(components),
        "verified_components": tested_verified,
        "verified_untested_components": untested_verified,
        "claimed_components": sum(1 for c in components.values() if c.get("claimed_by")),
        "total_flows": len(flows),
        "open_gaps": explicit_gaps if saw_explicit_gaps else inferred_gaps,
        "hub_bottlenecks": hub_bottlenecks,
        "board_schema_versions": board_versions,
    }
    return components, flows, metrics


def _extract_board_charter(bname: str, board: dict[str, Any]) -> dict[str, str] | None:
    gd_attrs = ((board.get("scopes") or {}).get("global_defaults") or {}).get("attributes") or {}
    ch = next((v for v in (board.get("charter"), gd_attrs.get("charter")) if v), None)
    if isinstance(ch, dict) and any(ch.get(k) for k in ("summary", "principles")):
        return {
            "name": str(ch.get("name") or bname),
            "summary": str(ch.get("summary") or "").strip(),
            "principles": str(ch.get("principles") or "").strip(),
        }
    return None


def _render_board_spec(
    boards: list[tuple[str, dict[str, Any]]],
    components: dict[str, dict[str, Any]],
    flows: list[dict[str, Any]],
    metrics: dict[str, Any],
    mermaid_str: str,
) -> str:
    bnames = ", ".join(b for b, _ in boards)
    hubs_txt = ", ".join(metrics["hub_bottlenecks"]) or "none"
    untested_n = int(metrics.get("verified_untested_components") or 0)
    untested_sfx = f" (`{untested_n}` untested)" if untested_n else ""
    lines = [
        f"# {bnames} — Living Architecture & Intent Specification",
        "",
        f"> **Status:** `{metrics['verified_components']}/{metrics['total_components']}` "
        f"components `VERIFIED`{untested_sfx} | `{metrics['total_flows']}` data flows | "
        f"`{metrics['open_gaps']}` open gaps | **Hubs:** `{hubs_txt}`",
    ]
    charters = [(bn, ch) for bn, bd in boards if (ch := _extract_board_charter(bn, bd))]
    g_rules = [
        r
        for _, bd in boards
        for k, sc in ((bd.get("scopes") or {}).items() if isinstance(bd.get("scopes"), dict) else [])
        if k == "global_defaults" or str(k).startswith("global_defaults::")
        for r in (sc.get("rules") or [])
        if r.get("text") and not str(r.get("text") or "").startswith("Charter [")
    ]
    if charters or g_rules:
        lines.extend(["", "## 1. Project Charter & Purpose"])
        for _, ch in charters:
            lines.append(f"### `{ch['name']}`")
            if ch["summary"]:
                lines.append(f"- **What & Why (`Summary`):** {ch['summary']}")
            if ch["principles"]:
                lines.append(f"- **Core Principles (`Principles`):** {ch['principles']}")
        if g_rules:
            lines.append("### Standing Global Rules")
            for gr in g_rules:
                prop = _pick(gr, "target_prop", "id", default="global")
                lines.append(f"- **`{gr.get('type', 'RULE')}` (`{prop}`):** {gr.get('text')}")
    lines.extend(["", "## 2. System Topology & Data Flows", "", "```mermaid", mermaid_str, "```"])
    if flows:
        lines.extend(["", "| Source | Target | Payload | Board |", "| :--- | :--- | :--- | :--- |"])
        for fl in flows:
            pay = fl.get("payload") or "-"
            brd = fl.get("board") or "-"
            lines.append(f"| `{fl['source']}` | `{fl['target']}` | `{pay}` | `{brd}` |")
    arts_by_scope: dict[str, list[dict[str, Any]]] = {}
    rules_by_scope: dict[str, list[dict[str, Any]]] = {}
    for _, bd in boards:
        for a in bd.get("lattice_artifacts") or []:
            asid = str(_pick(a, "scope_id", "scope", default="")).strip()
            if asid:
                arts_by_scope.setdefault(asid, []).append(a)
        raw_scopes = bd.get("scopes")
        for sid, sc in (raw_scopes.items() if isinstance(raw_scopes, dict) else []):
            if sid != "global_defaults" and not str(sid).startswith("global_defaults::"):
                for r in sc.get("rules") or []:
                    if r.get("text"):
                        rules_by_scope.setdefault(sid, []).append(r)
    lines.extend(["", "## 3. Components, Rationale & Contracts"])
    for sid, c in components.items():
        disp_st = (
            "VERIFIED (untested)"
            if c["build_status"] == "VERIFIED" and bool(c.get("verified_untested"))
            else c["build_status"]
        )
        claim_h = f", claimed_by: `{c['claimed_by']}`" if c.get("claimed_by") else ""
        header = (
            f"### `{sid}` (`{disp_st}`{claim_h}, owner: `{c['owner'] or 'unassigned'}`, "
            f"stage: `{c['stage'] or '-'}`)"
        )
        lines.extend(["", header, f"- **Purpose (`Role`):** {c.get('role') or f'Component {sid}'}"])
        if c.get("why"):
            lines.append(f"- **Why It Exists (`Rationale`):** {c['why']}")
        lines.append(f"- **Depends On:** {', '.join(f'`{d}`' for d in c['depends_on']) or 'none'}")
        if c.get("files"):
            lines.append(f"- **Files:** {', '.join(f'`{f}`' for f in c['files'])}")
        s_arts = arts_by_scope.get(sid) or []
        kind_labels = (
            ("INTERFACE", "Interfaces"),
            ("SCHEMA", "Schemas"),
            ("LAW", "Formal Laws (Bend 2)"),
        )
        for kind, label in kind_labels:
            k_arts = [a for a in s_arts if str(a.get("kind") or "").upper() == kind]
            if k_arts:
                lines.append(f"- **{label}:**")
                for a in k_arts:
                    nm = _pick(a, "name", "id")
                    sp = _pick(a, "spec", "text", default="")
                    fl = a.get("file") or (a.get("dimensions") or {}).get("file") or ""
                    ins = ", ".join(str(x) for x in (a.get("inputs") or []))
                    outs = ", ".join(str(x) for x in (a.get("outputs") or []))
                    if kind == "INTERFACE":
                        sig = f"`{nm}({ins}) -> {outs}`"
                    elif ins:
                        sig = f"`{nm}({ins})`"
                    else:
                        sig = f"`{nm}`"
                    file_sfx = f" (`{fl}`)" if fl else ""
                    spec_sfx = f": {sp}" if sp else ""
                    lines.append(f"  - {sig}{file_sfx}{spec_sfx}")
        s_rules = rules_by_scope.get(sid)
        if s_rules:
            lines.append("- **Bounds & Invariants:**")
            for r in s_rules:
                lines.append(f"  - `{r.get('type', 'RULE')}`: {r.get('text')}")
    return "\n".join(lines)
