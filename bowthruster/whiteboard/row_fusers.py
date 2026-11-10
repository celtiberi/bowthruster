"""Spec 22C §2.2: Whiteboard Pipe-Table Row Fusers for Flows, Rules, Contracts, Laws, & Components."""

from __future__ import annotations

from typing import Any

from bowthruster.nlp import restore_ascii_dots
from bowthruster.regiment.markdown_tables import _decompose_rule_expr
from bowthruster.whiteboard.mutations import (
    prune_dysfluency_and_upsert_rule,
    upsert_component_spec,
    upsert_flow_edge,
    upsert_lattice_artifact,
)
from .table_helpers import (
    _EMPTY_TOKENS,
    _clean_cell_text,
    _extract_canonical_and_aliases,
    _get_table_cell,
    _parse_list_cell,
    _parse_scalar_cell,
    _register_scope_aliases,
    _resolve_or_register_scope,
    _slugify_scope_id,
)


def _fuse_flow_or_rule_rows(
    graph: dict[str, Any],
    rows: list[Any],
    headers: list[Any],
    norm_headers: list[str],
    is_flow_t: bool,
    turn_index: int,
    fused_scopes: list[str],
) -> None:
    if is_flow_t:
        for row in rows:
            raw_src = _get_table_cell(row, headers, norm_headers, ("source", "from", "sender", "producer"))
            raw_tgt = _get_table_cell(row, headers, norm_headers, ("target", "to", "recipient", "consumer"))
            if not raw_src.strip() or not raw_tgt.strip():
                continue
            src = _resolve_or_register_scope(graph, raw_src, create=True)
            tgt = _resolve_or_register_scope(graph, raw_tgt, create=True)
            for sid in (src, tgt):
                if sid not in fused_scopes:
                    fused_scopes.append(sid)
            raw_fnm = _clean_cell_text(row, headers, norm_headers, ("flow", "id", "name"))
            pay_keys = ("payload", "contract", "schema", "data", "artifact", "message")
            raw_pay = _clean_cell_text(row, headers, norm_headers, pay_keys)
            pay = None if raw_pay.lower() in _EMPTY_TOKENS else raw_pay
            raw_cond = _clean_cell_text(row, headers, norm_headers, ("condition", "when", "guard"))
            cond = None if raw_cond.lower() in _EMPTY_TOKENS else raw_cond
            upsert_flow_edge(
                graph,
                src,
                tgt,
                payload=pay,
                condition=cond,
                current_turn=int(turn_index),
                text=f"{src} -> {tgt}" + (f" ({pay})" if pay else ""),
                dimensions={"flow_name": raw_fnm} if raw_fnm and raw_fnm != raw_pay else None,
            )
        return
    valid_rtypes = (
        "BOUND", "PROHIBITION", "OBLIGATION", "FACT",
        "POLICY", "GOAL", "PROPOSAL", "PERMISSION",
    )
    for row in rows:
        raw_comp = _get_table_cell(row, headers, norm_headers, ("component", "scope", "scope_id", "module"))
        comp_sid = (
            _resolve_or_register_scope(graph, raw_comp, create=True)
            if raw_comp.strip()
            else "global_defaults"
        )
        if comp_sid not in fused_scopes:
            fused_scopes.append(comp_sid)
        raw_type = _clean_cell_text(row, headers, norm_headers, ("type", "kind", "facet")).upper() or "BOUND"
        rtype = raw_type if raw_type in valid_rtypes else "BOUND"
        prop_keys = ("property", "target_prop", "field", "metric", "rule", "bound")
        raw_prop = _clean_cell_text(row, headers, norm_headers, prop_keys)
        t_prop = _slugify_scope_id(raw_prop) if raw_prop and raw_prop.lower() not in _EMPTY_TOKENS else None
        raw_op = _clean_cell_text(row, headers, norm_headers, ("op", "bound_op", "operator"))
        b_op = raw_op if raw_op and raw_op.lower() not in _EMPTY_TOKENS else ("==" if t_prop else None)
        raw_val = _get_table_cell(row, headers, norm_headers, ("value", "bound_val", "limit", "target"))
        clean_rv = restore_ascii_dots(raw_val).strip().lower()
        b_val = None if clean_rv in _EMPTY_TOKENS else _parse_scalar_cell(raw_val)
        conds = _parse_list_cell(
            _get_table_cell(row, headers, norm_headers, ("condition", "conditions", "when"))
        )
        has_op_sym = any(sym in raw_prop for sym in ("<=", ">=", "==", "!=", "<", ">", "import "))
        if b_val is None or has_op_sym:
            expr_cell = (
                _clean_cell_text(row, headers, norm_headers, ("rule_spec", "bound", "rule", "spec"))
                or raw_prop
            )
            ep, eo, ev, econds = _decompose_rule_expr(expr_cell, rtype)
            t_prop = _slugify_scope_id(ep) if ep else t_prop
            b_op = eo or b_op
            b_val = ev if ev is not None else b_val
            conds = [*conds, *[c for c in econds if c not in conds]]
        if not t_prop and b_val is None:
            continue
        r_text = (
            _clean_cell_text(row, headers, norm_headers, ("spec", "description", "text", "summary"))
            or f"{comp_sid}.{t_prop or 'rule'} {b_op or '=='} {b_val}"
        )
        prune_dysfluency_and_upsert_rule(
            graph,
            comp_sid,
            {
                "type": rtype,
                "text": r_text,
                "target_prop": t_prop,
                "bound_op": b_op,
                "bound_val": b_val,
                "conditions": conds,
                "turn": int(turn_index),
            },
        )


def _extract_owner_and_stage(
    row: dict[str, Any], headers: list[Any], norm_headers: list[str]
) -> tuple[str | None, str | None]:
    raw_owner = _get_table_cell(row, headers, norm_headers, ("owner", "division"))
    if raw_owner.strip():
        owner_val = (
            _extract_canonical_and_aliases(raw_owner)[0]
            if ("`" in raw_owner or "(" in raw_owner)
            else restore_ascii_dots(raw_owner).strip().strip("\"`'*").strip()
        )
    else:
        owner_val = None
    raw_stage = _clean_cell_text(row, headers, norm_headers, ("stage", "phase"))
    return owner_val, (raw_stage or None)


def _extract_law_helper_fn(lhs_raw: str) -> str:
    if "(" not in lhs_raw:
        return ""
    callee = lhs_raw.split("(", 1)[0].strip().split(".")[-1].strip()
    if callee in ("bool_eq", "and", "or", "not", "eq") and "(" in lhs_raw.split("(", 1)[1]:
        inner = lhs_raw[lhs_raw.find("(") + 1 : -1].split(",", 1)[0].strip()
        if "(" in inner:
            callee = inner.split("(", 1)[0].strip().split(".")[-1].strip()
    return callee


def _fuse_artifact_or_law_rows(
    graph: dict[str, Any],
    rows: list[Any],
    headers: list[Any],
    norm_headers: list[str],
    turn_index: int,
    fused_scopes: list[str],
) -> None:
    if "schema" in norm_headers or "interface" in norm_headers:
        art_kind = "SCHEMA" if "schema" in norm_headers else "INTERFACE"
        name_keys = ("schema", "name", "id") if art_kind == "SCHEMA" else ("interface", "name", "id")
        in_keys = (
            ("fields", "field", "inputs", "input", "args", "params")
            if art_kind == "SCHEMA"
            else ("inputs", "input", "args", "params")
        )
        for row in rows:
            raw_art = _get_table_cell(row, headers, norm_headers, name_keys) or str(
                row.get(headers[0], "")
            )
            if not (art_name := restore_ascii_dots(raw_art).strip().strip("\"`'*").strip()):
                continue
            raw_comp = _get_table_cell(
                row, headers, norm_headers, ("component", "scope", "scope_id", "module")
            )
            comp_sid = (
                _resolve_or_register_scope(graph, raw_comp, create=True)
                if raw_comp.strip()
                else "global_defaults"
            )
            if comp_sid not in fused_scopes:
                fused_scopes.append(comp_sid)
            raw_file = _clean_cell_text(row, headers, norm_headers, ("file", "files", "path"))
            clean_file = "" if raw_file.lower() in _EMPTY_TOKENS else raw_file
            inputs_list = _parse_list_cell(_get_table_cell(row, headers, norm_headers, in_keys))
            out_cell = _get_table_cell(row, headers, norm_headers, ("output", "outputs", "return", "returns"))
            outputs_list = [] if art_kind == "SCHEMA" else _parse_list_cell(out_cell)
            owner_val, stage_val = _extract_owner_and_stage(row, headers, norm_headers)
            raw_spec = restore_ascii_dots(
                _get_table_cell(row, headers, norm_headers, ("spec", "description", "role", "summary"))
            ).strip()
            default_sig = (
                f"{art_name}({', '.join(inputs_list)})"
                if art_kind == "SCHEMA"
                else f"{art_name}({', '.join(inputs_list)}) -> {', '.join(outputs_list)}"
            )
            spec_str = raw_spec or default_sig
            dims: dict[str, Any] = {"_from_table": True, "spec_missing": not bool(raw_spec)}
            if clean_file:
                dims["file"] = clean_file
            if inputs_list:
                dims["fields" if art_kind == "SCHEMA" else "inputs"] = list(inputs_list)
            if outputs_list:
                dims["outputs"] = list(outputs_list)
            art_entry = upsert_lattice_artifact(
                graph,
                art_kind,
                art_name,
                scope_id=comp_sid,
                spec=spec_str,
                inputs=inputs_list or None,
                outputs=outputs_list or None,
                owner=owner_val,
                stage=stage_val,
                dimensions=dims,
                text=spec_str,
                current_turn=int(turn_index),
            )
            if clean_file:
                art_entry["file"] = clean_file
        return
    for row in rows:
        raw_law = _get_table_cell(row, headers, norm_headers, ("law", "name", "id", "theorem")) or str(
            row.get(headers[0], "")
        )
        if not (law_name := restore_ascii_dots(raw_law).strip().strip("\"`'*").strip()):
            continue
        raw_comp = _get_table_cell(
            row, headers, norm_headers, ("component", "scope", "scope_id", "module")
        )
        comp_sid = (
            _resolve_or_register_scope(graph, raw_comp, create=True)
            if raw_comp.strip()
            else "global_defaults"
        )
        if comp_sid not in fused_scopes:
            fused_scopes.append(comp_sid)
        law_fields = {
            lkey: rv
            for lkey in ("bend_dir", "args", "lhs", "rhs")
            if (rv := _clean_cell_text(row, headers, norm_headers, (lkey,)))
            and rv.lower() not in _EMPTY_TOKENS
        }
        raw_thm = _clean_cell_text(
            row, headers, norm_headers, ("theorem", "equation", "formula", "invariant")
        )
        if ("lhs" not in law_fields or "rhs" not in law_fields) and "==" in raw_thm:
            lhs_p, rhs_p = raw_thm.split("==", 1)
            lhs_clean = (
                lhs_p.split(":", 1)[1] if ("->" in lhs_p and ":" in lhs_p) else lhs_p
            ).strip().strip("\"`'")
            law_fields.setdefault("lhs", lhs_clean.strip())
            law_fields.setdefault("rhs", rhs_p.strip().strip("\"`'").strip())
        if lfn := _extract_law_helper_fn(str(law_fields.get("lhs") or "")):
            graph["lattice_artifacts"] = [
                a
                for a in graph.get("lattice_artifacts", [])
                if not (
                    a.get("kind") == "INTERFACE"
                    and (a.get("scope_id") or a.get("scope")) == comp_sid
                    and str(a.get("name") or "") == lfn
                    and bool((a.get("dimensions") or {}).get("spec_missing"))
                )
            ]
        owner_val, stage_val = _extract_owner_and_stage(row, headers, norm_headers)
        raw_spec = restore_ascii_dots(
            _get_table_cell(row, headers, norm_headers, ("spec", "description", "summary"))
        )
        default_law_spec = (
            f"{law_fields['lhs']} == {law_fields['rhs']}"
            if ("lhs" in law_fields and "rhs" in law_fields)
            else f"LAW {law_name}"
        )
        spec_str = raw_spec.strip() or default_law_spec
        art_entry = upsert_lattice_artifact(
            graph,
            "LAW",
            law_name,
            scope_id=comp_sid,
            spec=spec_str,
            owner=owner_val,
            stage=stage_val,
            dimensions={"_from_table": True, **law_fields},
            text=spec_str,
            current_turn=int(turn_index),
        )
        for lkey, lval in law_fields.items():
            art_entry[lkey] = lval


def _reconcile_bootstrapped_file_claims(
    graph: dict[str, Any], scope_id: str, claimed_files: list[str]
) -> None:
    if not graph.get("bootstrapped") or not claimed_files:
        return
    claimed_set = set(claimed_files)
    scopes = graph.get("scopes") or {}
    artifacts = graph.get("lattice_artifacts") or []
    for other_sid, other_sc in list(scopes.items()):
        if other_sid in (scope_id, "global_defaults"):
            continue
        other_files = list(other_sc.get("files") or [])
        if not any(f in claimed_set for f in other_files):
            continue
        for art in artifacts:
            if (art.get("scope") or art.get("scope_id")) == other_sid:
                raw_af = art.get("file") or (art.get("dimensions") or {}).get("file") or ""
                if str(raw_af).split("#", 1)[0] in claimed_set:
                    art["scope"] = art["scope_id"] = scope_id
        rem_files = [f for f in other_files if f not in claimed_set]
        other_sc["files"] = rem_files
        other_sc.setdefault("attributes", {})["files"] = rem_files
        if not rem_files and other_sc.get("_bootstrapped"):
            scopes.pop(other_sid, None)
            for art in artifacts:
                if (art.get("scope") or art.get("scope_id")) == other_sid:
                    art["scope"] = art["scope_id"] = scope_id
            for snode in scopes.values():
                if isinstance(snode.get("depends_on"), list) and other_sid in snode["depends_on"]:
                    snode["depends_on"] = [
                        d
                        for d in (scope_id if x == other_sid else x for x in snode["depends_on"])
                        if d != snode.get("id")
                    ]


def _fuse_component_rows(
    graph: dict[str, Any],
    table: dict[str, Any],
    rows: list[Any],
    headers: list[Any],
    norm_headers: list[str],
    turn_index: int,
    fused_scopes: list[str],
) -> None:
    primary_col_idx = next(
        (norm_headers.index(k) for k in ("component", "scope", "scope_id", "module") if k in norm_headers),
        0,
    )
    primary_hdr, heading_path = str(headers[primary_col_idx]), table.get("heading_path")
    non_alias_cols = frozenset({
        "role", "purpose", "responsibility", "why", "rationale", "owner", "division",
        "stage", "phase", "parent", "depends_on", "deps", "dependencies", "children",
        "inputs", "input", "outputs", "output", "files", "file", "scope",
    })
    for row in rows:
        raw_entity = str(row.get(primary_hdr, row.get(norm_headers[primary_col_idx], ""))).strip()
        if not raw_entity:
            continue
        scope_id = _resolve_or_register_scope(graph, raw_entity, create=True)
        scope = graph["scopes"][scope_id]
        if scope_id not in fused_scopes:
            fused_scopes.append(scope_id)
        attrs = scope.setdefault("attributes", {})
        if isinstance(heading_path, str) and heading_path.strip():
            attrs.setdefault("heading_path", heading_path.strip())
        attrs["updated_turn"] = int(turn_index)
        sbf_kwargs: dict[str, Any] = {}
        files_list: list[str] | None = None
        for col_idx, raw_hdr in enumerate(headers):
            if col_idx == primary_col_idx or (raw_hdr not in row and norm_headers[col_idx] not in row):
                continue
            col_key = norm_headers[col_idx]
            raw_cell_str = str(row.get(raw_hdr, row.get(col_key, "")))
            cell_val = _parse_scalar_cell(raw_cell_str)
            attrs[col_key] = cell_val
            if col_key not in non_alias_cols and isinstance(cell_val, str) and cell_val:
                if "`" in raw_cell_str or "/" in cell_val:
                    _register_scope_aliases(graph, scope_id, _extract_canonical_and_aliases(cell_val)[1])
            clean_str = str(cell_val).strip()
            is_empty_cell = clean_str.lower() in _EMPTY_TOKENS
            if col_key in ("role", "purpose", "responsibility"):
                if not is_empty_cell:
                    sbf_kwargs["role"] = attrs["_table_role"] = clean_str
                    scope["_role_missing"] = False
                elif not scope.get("role"):
                    scope["_role_missing"] = True
            elif col_key in ("why", "rationale"):
                if not is_empty_cell:
                    scope["why"] = attrs["why"] = clean_str
                    scope["_why_missing"] = False
                elif not scope.get("why"):
                    scope["_why_missing"] = True
            elif col_key in ("owner", "division") and not is_empty_cell:
                sbf_kwargs["owner"] = (
                    _extract_canonical_and_aliases(raw_cell_str)[0]
                    if ("`" in raw_cell_str or "(" in raw_cell_str)
                    else clean_str
                )
            elif col_key in ("stage", "phase") and not is_empty_cell:
                sbf_kwargs["stage"] = clean_str
            elif col_key == "parent" and not is_empty_cell:
                sbf_kwargs["parent"] = _slugify_scope_id(clean_str) or clean_str
            elif col_key in ("depends_on", "deps", "dependencies") and (
                not is_empty_cell or not scope.get("depends_on")
            ):
                dep_sids = [
                    _resolve_or_register_scope(graph, d, create=True)
                    for d in _parse_list_cell(raw_cell_str)
                    if d
                ]
                sbf_kwargs["depends_on"] = [d for d in dep_sids if d != scope_id]
                attrs["_table_depends_on"] = list(sbf_kwargs["depends_on"])
            elif col_key == "children":
                sbf_kwargs["children"] = [
                    _resolve_or_register_scope(graph, c, create=True)
                    for c in _parse_list_cell(raw_cell_str)
                    if c
                ]
            elif col_key in ("inputs", "input"):
                sbf_kwargs["inputs"] = _parse_list_cell(raw_cell_str)
            elif col_key in ("outputs", "output"):
                sbf_kwargs["outputs"] = _parse_list_cell(raw_cell_str)
            elif col_key in ("files", "file") or (
                col_key == "scope" and files_list is None and not is_empty_cell
            ):
                parsed_f = _parse_list_cell(raw_cell_str)
                if parsed_f or not scope.get("files"):
                    files_list = parsed_f
                    scope["files"] = attrs["files"] = files_list
                    attrs["_table_files"] = list(files_list)
                    _reconcile_bootstrapped_file_claims(graph, scope_id, files_list)
            elif col_key in ("tests", "test", "test_files") and not is_empty_cell:
                t_list = _parse_list_cell(raw_cell_str)
                scope["tests"] = attrs["tests"] = sbf_kwargs["tests"] = t_list
        if sbf_kwargs or files_list is not None:
            eff_owner = (
                next((_v for _v in (sbf_kwargs.get("owner"), scope.get("owner")) if _v), None)
                or "core"
            )
            upsert_component_spec(
                graph,
                scope_id,
                role=sbf_kwargs.get("role"),
                inputs=sbf_kwargs.get("inputs"),
                outputs=sbf_kwargs.get("outputs"),
                depends_on=sbf_kwargs.get("depends_on"),
                current_turn=int(turn_index),
                children=sbf_kwargs.get("children"),
                parent=sbf_kwargs.get("parent"),
                owner=eff_owner,
                stage=sbf_kwargs.get("stage"),
                kind="COMPONENT",
                files=files_list,
                tests=sbf_kwargs.get("tests"),
            )
