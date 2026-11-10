"""Deterministic MCU compiler from structured pipe-table rows and flow sentences (`table_mcu_builder.py`)."""

from __future__ import annotations

from typing import Any

from bowthruster.nlp import restore_ascii_dots

_EMPTY_TOKENS = frozenset({"", "none", "-", "—", "n/a", "null", "[]"})


def _pick(mapping: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for k in keys:
        if val := mapping.get(k):
            return val
    return default


def _norm_col_key(raw_hdr: str) -> str:
    cleaned = restore_ascii_dots(str(raw_hdr or "")).strip().strip("\"`'*").strip().lower()
    chars = [
        "_" if ch in ("-", " ", "/", ":") else (ch if (ch.isalnum() or ch == "_") else " ")
        for ch in cleaned
    ]
    return "_".join(part for part in "".join(chars).split() if part).strip("_")


def _clean_cell_val(raw_val: Any) -> str:
    s = restore_ascii_dots(str(raw_val if raw_val is not None else "")).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("`", '"', "'") and s[0] not in s[1:-1]:
        s = s[1:-1].strip()
    return s.replace("`", "").strip()


def _split_list_cell(val: Any) -> list[str]:
    if isinstance(val, list):
        return [c for x in val if (c := _clean_cell_val(x)) and c.lower() not in _EMPTY_TOKENS]
    raw = _clean_cell_val(val)
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1].strip()
    if not raw or raw.lower() in _EMPTY_TOKENS:
        return []
    chunks, buf, depth = [], [], 0
    for ch in raw:
        depth += 1 if ch in "([{" else (-1 if ch in ")]}" and depth > 0 else 0)
        if ch in (",", ";") and depth == 0:
            chunks.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    chunks.append("".join(buf))
    out: list[str] = []
    for part in chunks:
        tok = part.strip().strip("\"`'*").strip()
        if tok and tok.lower() not in _EMPTY_TOKENS and tok not in out:
            out.append(tok)
    return out


def _split_test_paths(val: Any) -> list[str]:
    if isinstance(val, list):
        pieces = [
            tok
            for item in val
            for tok in _clean_cell_val(item).replace(";", " ").replace(",", " ").split()
        ]
    else:
        pieces = _clean_cell_val(val).strip("[]").replace(";", " ").replace(",", " ").split()
    out: list[str] = []
    for raw_tok in pieces:
        tok = raw_tok.strip().strip("\"`'*").strip()
        if tok and tok.lower() not in _EMPTY_TOKENS and tok not in out:
            out.append(tok)
    return out


def _parse_scalar_val(raw: Any) -> int | float | str | None:
    s = _clean_cell_val(raw)
    if not s or s.lower() in _EMPTY_TOKENS:
        return None
    if s.lstrip("+-").isdigit():
        return int(s)
    try:
        return float(s)
    except ValueError:
        return s


def _decompose_rule_expr(
    raw_expr: Any, default_type: str = "BOUND"
) -> tuple[str | None, str | None, Any, list[str]]:
    s = _clean_cell_val(raw_expr)
    if not s or s.lower() in _EMPTY_TOKENS:
        return None, None, None, []
    extra_conds: list[str] = []
    if "->" in s:
        lhs_c, rhs_s = s.split("->", 1)
        c_clean = lhs_c.strip()
        if c_clean.lower().startswith(("when ", "if ")):
            c_clean = c_clean.split(None, 1)[1].strip()
        if c_clean:
            extra_conds.append(c_clean)
        s = rhs_s.strip()
    if s.lower().startswith(("no import ", "forbid import ", "ban import ")):
        return "imports", "!=", s.split(None, 2)[-1].strip().strip(","), extra_conds
    for op in ("<=", ">=", "!=", "==", "<", ">"):
        if op in s:
            lhs_p, rhs_v = s.split(op, 1)
            prop, val = _norm_col_key(lhs_p), _parse_scalar_val(rhs_v)
            if prop and val is not None:
                return prop, op, val, extra_conds
    op_fb = "!=" if default_type == "PROHIBITION" else None
    return _norm_col_key(s) or s, op_fb, None, extra_conds


def _base_mcu(
    next_id: int,
    txt: str,
    scope: str,
    lkind: str,
    payload: dict[str, Any],
    *,
    mcu_type: str = "FACT",
    facet: str = "context_facts",
    secondary_refs: list[str] | None = None,
    target_prop: str | None = None,
    bound_op: str | None = None,
    bound_val: Any = None,
    conditions: list[str] | None = None,
    is_exception: bool = False,
    meta: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mcu: dict[str, Any] = {
        "mcu_id": next_id,
        "head_id": next_id,
        "source_chunk_ids": [next_id],
        "text": txt,
        "canonical_text": txt,
        "type": mcu_type,
        "facet": facet,
        "primary_scope": scope,
        "secondary_refs": list(secondary_refs or []),
        "target_prop": target_prop,
        "bound_op": bound_op,
        "bound_val": bound_val,
        "conditions": list(conditions or []),
        "is_exception": is_exception,
        "superseded_literals": [],
        "meta_keys": [],
        "meta": dict(meta or {}),
        "unclear_spans": [],
        "grounding_status": "GROUNDED",
        "lattice_kind": lkind,
        "lattice_payload": payload,
    }
    if extra:
        mcu.update(extra)
    return mcu


def _build_rule_mcu_from_row(
    srow: dict[str, Any],
    next_id: int,
    scope: str,
    owner: str | None,
    stage: str | None,
) -> dict[str, Any]:
    has_bound_cols = bool(_pick(srow, "op", "bound_op", "value", "bound_val"))
    default_t = "BOUND" if has_bound_cols else "OBLIGATION"
    raw_type = str(_pick(srow, "type", "facet", default=default_t)).strip().upper()
    valid_rtypes = ("BOUND", "PROHIBITION", "OBLIGATION", "FACT", "POLICY", "GOAL", "PROPOSAL", "ACTION")
    rtype = "FACT" if raw_type == "CONTEXT_FACTS" else (raw_type if raw_type in valid_rtypes else "BOUND")
    prop_raw = str(_pick(srow, "property", "target_prop", "prop")).strip()
    op = str(_pick(srow, "op", "bound_op")).strip() or None
    val = _parse_scalar_val(srow.get("value", srow.get("bound_val")))
    expr_cell = _pick(srow, "rule_spec", "bound") or (srow.get("rule") if not prop_raw else None)
    extra_c: list[str] = []
    if (not prop_raw or val is None) and expr_cell:
        d_prop, d_op, d_val, extra_c = _decompose_rule_expr(expr_cell, rtype)
        prop_raw, op = (prop_raw or (d_prop or "")), (op or d_op)
        val = d_val if val is None else val
    prop = _norm_col_key(prop_raw) if prop_raw else (str(srow.get("rule") or "").strip() or None)
    op = op or ("==" if rtype in ("FACT", "OBLIGATION") else ("!=" if rtype == "PROHIBITION" else "<="))
    raw_c = str(_pick(srow, "condition", "when")).strip()
    base_c = [] if raw_c.lower() in _EMPTY_TOKENS else _split_list_cell(raw_c)
    cond_list = base_c + [c for c in extra_c if c]
    cond = cond_list[0] if cond_list else None
    rid = str(_pick(srow, "rule", "id", "name")).strip()
    spec_txt = str(_pick(srow, "spec", "text", "description")).strip()
    cond_sfx = f" when {', '.join(cond_list)}." if cond_list else "."
    default_txt = (
        f"In `{scope}`, `{prop}` {op} {val}{cond_sfx}"
        if prop and val is not None
        else f"Rule {rid or next_id} on `{scope}`."
    )
    txt = spec_txt or default_txt
    f_map = {"BOUND": "Numeric_Bounds", "PROHIBITION": "prohibitions", "FACT": "context_facts"}
    facet = f_map.get(rtype, "obligations")
    lkind = "CONSTRAINT" if rtype in ("BOUND", "PROHIBITION", "OBLIGATION") else "FACT"
    payload = {k: v for k, v in (("id", rid), ("owner", owner), ("stage", stage)) if v}
    return _base_mcu(
        next_id,
        txt,
        scope,
        lkind,
        payload,
        mcu_type=rtype,
        facet=facet,
        target_prop=prop,
        bound_op=op if val is not None else None,
        bound_val=val,
        conditions=cond_list,
        is_exception=bool(cond and cond.lower().startswith(("unless", "except"))),
    )


def _compile_component_row(
    srow: dict[str, Any],
    next_id: int = 1,
    scope: str | None = None,
    owner: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    eff_scope = str(scope or _pick(srow, "scope", "component", default="global_defaults")).strip()
    eff_owner = owner or str(_pick(srow, "owner", "division")).strip() or "core"
    eff_stage = stage or str(_pick(srow, "stage", "phase")).strip() or None
    deps = _split_list_cell(_pick(srow, "depends_on", "dependencies", "deps"))
    files = _split_list_cell(_pick(srow, "files", "file", "raw_scope"))
    tests = _split_test_paths(_pick(srow, "tests", "test_files", "test"))
    has_test_col = any(k in srow for k in ("tests", "test_files", "test"))
    raw_role = str(_pick(srow, "role", "description", "purpose")).strip()
    role = raw_role or f"Component {eff_scope}"
    why = str(_pick(srow, "why", "rationale")).strip() or None
    why_missing = any(k in srow for k in ("why", "rationale")) and not why
    attrs: dict[str, Any] = {"files": list(files), "depends_on": list(deps)}
    payload: dict[str, Any] = {
        "scope": eff_scope,
        "role": role,
        "why": why,
        "_role_missing": not raw_role,
        "_why_missing": why_missing,
        "depends_on": deps,
        "files": files,
        "owner": eff_owner,
        "stage": eff_stage,
    }
    if tests or has_test_col:
        attrs["tests"] = list(tests)
        payload["tests"] = list(tests)
    extra: dict[str, Any] = {"attributes": attrs}
    if tests or has_test_col:
        extra["tests"] = list(tests)
    return _base_mcu(
        next_id,
        f"{eff_scope}: {role}",
        eff_scope,
        "COMPONENT",
        payload,
        secondary_refs=list(deps),
        meta=dict(attrs),
        extra=extra,
    )


def _build_artifact_or_charter_mcu(
    srow: dict[str, Any],
    next_id: int,
    t_kind: str,
    scope: str,
    owner: str | None,
    stage: str | None,
) -> dict[str, Any]:
    if t_kind == "CHARTER":
        cname = str(_pick(srow, "charter", "project", "board", default="project")).strip()
        csum = str(_pick(srow, "summary", "purpose", "why")).strip()
        cprin = str(_pick(srow, "principles", "non_goals")).strip()
        txt = f"Charter [{cname}]: {csum}" + (f" | Principles: {cprin}" if cprin else "")
        c_pay = {"name": cname, "summary": csum, "principles": cprin}
        return _base_mcu(next_id, txt, "global_defaults", "CHARTER", c_pay)
    if t_kind == "COMPONENT":
        return _compile_component_row(srow, next_id, scope, owner, stage)
    name = str(_pick(srow, t_kind.lower(), "name", "id", default=scope)).strip()
    raw_spec = str(_pick(srow, "spec", "description", "theorem")).strip()
    spec_s = raw_spec or f"{t_kind} {name} ({scope})"
    ins = _split_list_cell(_pick(srow, "inputs", "fields", "args"))
    outs = _split_list_cell(_pick(srow, "outputs", "output", "returns"))
    payload = {
        "id": name,
        "name": name,
        "scope": scope,
        "spec": spec_s,
        "spec_missing": not raw_spec,
        "inputs": ins,
        "outputs": outs,
        "owner": owner,
        "stage": stage,
        "file": str(srow.get("file") or "").strip() or None,
    }
    return _base_mcu(next_id, spec_s, scope, t_kind, payload)


def build_mcus_from_tables_and_flows(
    structured_rows: list[dict[str, Any]],
    flow_sentences: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Builds deterministic `parsed_mcus` directly from structured pipe-table rows and flow sentences."""
    mcus: list[dict[str, Any]] = []
    next_id = 1
    for srow in structured_rows:
        t_kind = str(srow.get("table_kind") or "GENERIC")
        scope = str(srow.get("scope") or "global_defaults").strip() or "global_defaults"
        owner = str(_pick(srow, "owner", "division")).strip() or None
        stage = str(_pick(srow, "stage", "phase")).strip() or None
        if t_kind == "FLOW":
            src = str(_pick(srow, "source", "from", default=scope)).strip()
            tgt = str(_pick(srow, "target", "to", "recipient")).strip()
            if not src or not tgt:
                continue
            fnm = str(_pick(srow, "flow", "id", "name")).strip() or None
            pay = str(_pick(srow, "payload", "contract", "schema", "object", "data")).strip() or None
            raw_c = str(_pick(srow, "condition", "when")).strip()
            cond = None if raw_c.lower() in _EMPTY_TOKENS else raw_c
            txt = f"The {src} component sends {pay or 'data'} to {tgt}."
            payload: dict[str, Any] = {
                "source": src,
                "target": tgt,
                "payload": pay,
                "condition": cond,
                "summary": txt,
            }
            if fnm and fnm != pay:
                payload["flow_name"] = fnm
            mcus.append(
                _base_mcu(
                    next_id,
                    txt,
                    src,
                    "FLOW",
                    payload,
                    secondary_refs=[r for r in (tgt, pay) if r and r != src],
                    conditions=[cond] if cond else [],
                    extra={
                        "subject": f"{src} component",
                        "predicate": "sends",
                        "object": pay,
                        "recipient": tgt,
                        "condition": cond,
                    },
                )
            )
            next_id += 1
        elif t_kind == "RULE":
            mcus.append(_build_rule_mcu_from_row(srow, next_id, scope, owner, stage))
            next_id += 1
        elif t_kind in ("CHARTER", "COMPONENT", "INTERFACE", "LAW", "SCHEMA"):
            mcus.append(_build_artifact_or_charter_mcu(srow, next_id, t_kind, scope, owner, stage))
            next_id += 1

    for fl in (flow_sentences or []):
        src, tgt = fl["source"], fl["target"]
        pay, cond, txt = fl.get("payload"), fl.get("condition"), fl["text"]
        payload = {"source": src, "target": tgt, "payload": pay, "condition": cond, "summary": txt}
        mcus.append(
            _base_mcu(
                next_id,
                txt,
                src,
                "FLOW",
                payload,
                secondary_refs=[r for r in (tgt, pay) if r and r != src],
                conditions=[cond] if cond else [],
                extra={
                    "subject": f"{src} component",
                    "predicate": "sends",
                    "object": pay,
                    "recipient": tgt,
                    "condition": cond,
                },
            )
        )
        next_id += 1
    return mcus
