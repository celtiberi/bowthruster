# Site Y — Stage 24A (`ai_memory` — `schema.py`, `cue_extractor.py`, `uri_resolver.py`) Formal Proofs (`PROOF.bend`)

import Base
import ./main.bend as MEMA
import ./LAWS.bend as Laws

def Laws.pointer_only_storage_law(has_raw, words_ok, links_ok):
  match has_raw words_ok links_ok:
    case False{} False{} False{}:
      {==}
    case False{} False{} True{}:
      {==}
    case False{} True{} False{}:
      {==}
    case False{} True{} True{}:
      {==}
    case True{} False{} False{}:
      {==}
    case True{} False{} True{}:
      {==}
    case True{} True{} False{}:
      {==}
    case True{} True{} True{}:
      {==}

def Laws.cue_seed_determinism_law(non_empty, within_cap, is_dup):
  match non_empty within_cap is_dup:
    case False{} False{} False{}:
      {==}
    case False{} False{} True{}:
      {==}
    case False{} True{} False{}:
      {==}
    case False{} True{} True{}:
      {==}
    case True{} False{} False{}:
      {==}
    case True{} False{} True{}:
      {==}
    case True{} True{} False{}:
      {==}
    case True{} True{} True{}:
      {==}

def Laws.uri_link_idempotence_law(b, is_dup):
  match b is_dup:
    case MEMA.LBZero{} False{}:
      {==}
    case MEMA.LBZero{} True{}:
      {==}
    case MEMA.LB1To7{} False{}:
      {==}
    case MEMA.LB1To7{} True{}:
      {==}
    case MEMA.LBAtCap8{} False{}:
      {==}
    case MEMA.LBAtCap8{} True{}:
      {==}

def Laws.git_uri_integrity_law(has_dangling_delete, rename_updated):
  match has_dangling_delete rename_updated:
    case False{} False{}:
      {==}
    case False{} True{}:
      {==}
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}

def Laws.ast_symbol_uri_anchor_law(has_ast_symbol_range, uri_has_line_anchor):
  match has_ast_symbol_range uri_has_line_anchor:
    case False{} False{}:
      {==}
    case False{} True{}:
      {==}
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}



