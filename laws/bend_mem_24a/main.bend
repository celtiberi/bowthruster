# Site Y — Stage 24A (`ai_memory` — `schema.py`, `cue_extractor.py`, `uri_resolver.py`) Bend 2 Model (`main.bend`)

import Base

type LinkCountBucket is Data:
  LBZero{}
  LB1To7{}
  LBAtCap8{}

def link_bucket_eq(a: LinkCountBucket, b: LinkCountBucket) -> Bool:
  match a b:
    case LBZero{} LBZero{}:
      True{}
    case LB1To7{} LB1To7{}:
      True{}
    case LBAtCap8{} LBAtCap8{}:
      True{}
    case _ _:
      False{}

def is_within_8_cap(b: LinkCountBucket) -> Bool:
  match b:
    case LBZero{}:
      True{}
    case LB1To7{}:
      True{}
    case LBAtCap8{}:
      True{}

def step_uri_link_registration(cur_bucket: LinkCountBucket, is_duplicate_uri: Bool) -> LinkCountBucket:
  match cur_bucket is_duplicate_uri:
    case LBZero{} True{}:
      LBZero{}
    case LBZero{} False{}:
      LB1To7{}
    case LB1To7{} True{}:
      LB1To7{}
    case LB1To7{} False{}:
      LBAtCap8{}
    case LBAtCap8{} True{}:
      LBAtCap8{}
    case LBAtCap8{} False{}:
      LBAtCap8{}

def is_valid_concept_storage(has_raw_document: Bool, words_within_50: Bool, links_within_8: Bool) -> Bool:
  match has_raw_document:
    case True{}:
      False{}
    case False{}:
      Bool.and(words_within_50, links_within_8)

def should_accept_seed_cue(is_non_empty_token: Bool, within_12_cap: Bool, is_duplicate_seed: Bool) -> Bool:
  match is_non_empty_token:
    case False{}:
      False{}
    case True{}:
      Bool.and(within_12_cap, Bool.not(is_duplicate_seed))

def is_commit_uri_state_valid(has_dangling_deleted_uri: Bool, rename_pointers_updated: Bool) -> Bool:
  Bool.and(Bool.not(has_dangling_deleted_uri), rename_pointers_updated)

def is_symbol_uri_anchored(has_ast_symbol_range: Bool, uri_has_line_anchor: Bool) -> Bool:
  Bool.or(Bool.not(has_ast_symbol_range), uri_has_line_anchor)

def main() -> Bool:
  Bool.and(
    is_valid_concept_storage(False{}, True{}, True{}),
    Bool.and(
      should_accept_seed_cue(True{}, True{}, False{}),
      Bool.and(
        is_within_8_cap(step_uri_link_registration(LBZero{}, False{})),
        is_commit_uri_state_valid(False{}, True{})
      )
    )
  )
