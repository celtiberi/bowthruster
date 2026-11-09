# Site Y — Stage 24A (`ai_memory` — `schema.py`, `cue_extractor.py`, `uri_resolver.py`) Formal Laws (`LAWS.bend`)
# Generated directly from Lattice `--board ai_memory` (`pkt_wave1_ai_memory`, `pkt_wave1_cue_extractor`, `pkt_wave1_uri_resolver`).
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `ai_memory` 24A build tag in `ModelCircuits/eng0` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as MEMA

# LAW 1: Pointer-Only Storage Law (`pointer_only_storage_law`) —
# `is_valid_concept_storage(has_raw_document, words_within_50, links_within_8)` is `True{}`
# iff `has_raw_document == False{}` AND `words_within_50 == True{}` AND `links_within_8 == True{}`.
law pointer_only_storage_law:
  for +has_raw: Bool
  for +words_ok: Bool
  for +links_ok: Bool
  {MEMA.is_valid_concept_storage(has_raw, words_ok, links_ok) ==
   Bool.and(Bool.not(has_raw), Bool.and(words_ok, links_ok)) : Bool}

# LAW 2: Cue Seed Determinism & Fan-Out Bound Law (`cue_seed_determinism_law`) —
# `should_accept_seed_cue(is_non_empty_token, within_12_cap, is_duplicate_seed)` is `True{}`
# iff `is_non_empty_token == True{}` AND `within_12_cap == True{}` AND `is_duplicate_seed == False{}`.
law cue_seed_determinism_law:
  for +non_empty: Bool
  for +within_cap: Bool
  for +is_dup: Bool
  {MEMA.should_accept_seed_cue(non_empty, within_cap, is_dup) ==
   Bool.and(non_empty, Bool.and(within_cap, Bool.not(is_dup))) : Bool}

# LAW 3: URI Link Idempotence & Bound Law (`uri_link_idempotence_law`) —
# `step_uri_link_registration(cur_bucket, is_duplicate_uri)`:
# (1) When `is_duplicate_uri == True{}`, preserves `cur_bucket` unchanged (`link_bucket_eq(..., cur_bucket) == True{}`);
# (2) When `cur_bucket == LBAtCap8{}`, never overflows past 8 (`is_within_8_cap(step_uri_link_registration(LBAtCap8{}, is_dup)) == True{}`).
law uri_link_idempotence_law:
  for +b: MEMA.LinkCountBucket
  for +is_dup: Bool
  {Bool.and(
    MEMA.link_bucket_eq(MEMA.step_uri_link_registration(b, True{}), b),
    MEMA.is_within_8_cap(MEMA.step_uri_link_registration(MEMA.LBAtCap8{}, is_dup))
  ) == True{} : Bool}

# LAW 4: Git Diff URI Integrity Law (`git_uri_integrity_law`, Stage 26 `uri_git_healer`) —
# `is_commit_uri_state_valid(has_dangling_deleted_uri, rename_pointers_updated)` is `True{}`
# iff `has_dangling_deleted_uri == False{}` AND `rename_pointers_updated == True{}`.
law git_uri_integrity_law:
  for +has_dangling_delete: Bool
  for +rename_updated: Bool
  {MEMA.is_commit_uri_state_valid(has_dangling_delete, rename_updated) ==
   Bool.and(Bool.not(has_dangling_delete), rename_updated) : Bool}

law ast_symbol_uri_anchor_law:
  for +has_ast_symbol_range: Bool
  for +uri_has_line_anchor: Bool
  {MEMA.is_symbol_uri_anchored(has_ast_symbol_range, uri_has_line_anchor) ==
   Bool.or(Bool.not(has_ast_symbol_range), uri_has_line_anchor) : Bool}

