---
tags: [file]
path: scripts/enrich_linkedin.py
type: python
---

# enrich-linkedin

## Location
`scripts/enrich_linkedin.py`

## Purpose
Phase 1c: ≤25 LinkedIn detail fetches via three tiers (WebBridge browser → Playwright → guest CLI) with a shared RequestLedger enforcing the run cap; verification-first allocation.

## Connections
### Calls or imports:
- [[linkedin-extract]] - tier-1 lazy import
- [[linkedin-playwright]] - tier-3 lazy import
- [[aggregate-jobs]] - LINKEDIN_JOB_ID key logic
- [[cmd-detail-linkedin]] - guest CLI detail fetch

### Called by or imported by:
- [[run-daily]] - Phase 1c
- [[prerank-jobs]] - title_match_score import
- [[docs-browser-enrichment]] - documents
- [[test_enrich_browser_path]] - tests
- [[test_enrich_linkedin]] - tests
- [[test_prerank_jobs]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
