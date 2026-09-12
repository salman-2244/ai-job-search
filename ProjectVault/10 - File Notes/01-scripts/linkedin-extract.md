---
tags: [file]
path: scripts/linkedin_extract.py
type: python
---

# linkedin-extract

## Location
`scripts/linkedin_extract.py`

## Purpose
Tier-1 browser extraction through the Kimi WebBridge daemon (127.0.0.1:10086): semantic-id selectors, tab foregrounding, ~15× faster than the guest path.

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[enrich-linkedin]] - tier-1 lazy import
- [[docs-phase-b-browser-verification]] - planned verification via
- [[docs-linkedin-selector-findings]] - extraction findings
- [[test_enrich_browser_path]] - tests
- [[test_linkedin_extract]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
