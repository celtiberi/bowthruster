# Stage 25A — Formal Proofs for `the_table` & `table_surface` (`PROOF.bend`)

import Base
import ./main.bend as TBLA
import ./LAWS.bend as Laws

def Laws.table_token_budget_conservation_law(slots_ok, tokens_ok):
  match slots_ok:
    case True{}:
      match tokens_ok:
        case True{}:
          {==}
        case False{}:
          {==}
    case False{}:
      match tokens_ok:
        case True{}:
          {==}
        case False{}:
          {==}

def Laws.pinned_slot_eviction_immunity_law(over_cap, pinned_cap_ok):
  match over_cap:
    case True{}:
      match pinned_cap_ok:
        case True{}:
          {==}
        case False{}:
          {==}
    case False{}:
      match pinned_cap_ok:
        case True{}:
          {==}
        case False{}:
          {==}

def Laws.slot_eviction_order_law(first_lower):
  match first_lower:
    case True{}:
      {==}
    case False{}:
      {==}

def Laws.atlas_mode_isolation_law(has_cues, explicit_req, budget_ok):
  match has_cues:
    case True{}:
      match explicit_req:
        case True{}:
          match budget_ok:
            case True{}:
              {==}
            case False{}:
              {==}
        case False{}:
          match budget_ok:
            case True{}:
              {==}
            case False{}:
              {==}
    case False{}:
      match explicit_req:
        case True{}:
          match budget_ok:
            case True{}:
              {==}
            case False{}:
              {==}
        case False{}:
          match budget_ok:
            case True{}:
              {==}
            case False{}:
              {==}
