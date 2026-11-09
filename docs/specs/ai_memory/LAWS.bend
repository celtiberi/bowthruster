# Site Y — Canonical Spec 24 (`ai_memory` Hippocampal Concept Index & External Source Links) Formal Laws (`LAWS.bend`)
# Owned by T-Division (Bethe). Engineers (`eng0`..`eng3`) may NOT edit `LAWS.bend`.
# Every `ai_memory` build tag in `ModelCircuits/eng0` must prove these laws via `PROOF.bend`.

import Base
import ./main.bend as AM

# LAW 1: Progressive LOD drill-down monotonicity — `step_lod(LOD0{}) == LOD1{}`,
# `step_lod(LOD1{}) == LOD2{}`, and `step_lod(LOD2{}) == LOD2{}` (idempotent ceiling at LOD 2).
law lod_step_idempotent_at_lod2:
  for +lvl: AM.LODLevel
  {AM.step_lod(AM.step_lod(AM.step_lod(lvl))) == AM.LOD2{} : AM.LODLevel}

# LAW 2: External source dereference guard — `should_dereference_external(lvl, has_links, deep_detail_needed)`
# returns `True{}` iff `lvl == LOD2{}`, `has_links == True{}`, and `deep_detail_needed == True{}`
# (raw external books/documents are never fetched during LOD 0 / LOD 1 associative recall).
law external_deref_only_at_lod2:
  for +lvl: AM.LODLevel
  for +has_links: Bool
  for +deep_detail_needed: Bool
  {AM.should_dereference_external(lvl, has_links, deep_detail_needed) ==
   Bool.and(AM.is_lod2(lvl), Bool.and(has_links, deep_detail_needed)) : Bool}
