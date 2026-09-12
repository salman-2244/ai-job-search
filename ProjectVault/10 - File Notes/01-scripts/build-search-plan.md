---
tags: [file]
path: scripts/build_search_plan.py
type: python
---

# build-search-plan

## Location
`scripts/build_search_plan.py`

## Purpose
Turns config/search_matrix.json into a TSV of portal CLI invocations; LinkedIn geo rotation arithmetic under the 90-request cap; --list-geos feeds the Stage 3 bot buttons.

## Connections
### Calls or imports:
- [[search-matrix]] - reads tracks/geos/budgets

### Called by or imported by:
- [[run-daily]] - Phase 1 plan step
- [[stage3-bot]] - --list-geos feeds bot buttons
- [[spec-linkedin-job-discovery]] - designed
- [[test_search_plan]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
