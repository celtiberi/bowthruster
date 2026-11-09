# Standalone Proof (`law_verified_scope_edit_guard_law.bend`)

import Base
import ./main.bend as MOD
import ./LAWS.bend as Laws

def Laws.verified_scope_edit_guard_law(file_hash_changed, scope_still_verified):
  match file_hash_changed scope_still_verified:
    case False{} False{}:
      {==}
    case False{} True{}:
      {==}
    case True{} False{}:
      {==}
    case True{} True{}:
      {==}
