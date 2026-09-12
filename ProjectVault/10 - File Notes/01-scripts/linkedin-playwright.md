---
tags: [file]
path: scripts/linkedin_playwright.py
type: python
---

# linkedin-playwright

## Location
`scripts/linkedin_playwright.py`

## Purpose
Tier-3 PlaywrightDetailProvider: storage-state auth, typed wall errors (login/CAPTCHA/consent), charges the RequestLedger before every navigation.

## Connections
### Calls or imports:
- [[linkedin-session]] - storage-state provisioned by it

### Called by or imported by:
- [[enrich-linkedin]] - tier-3 lazy import
- [[test_linkedin_playwright]] - tests

## Notes
Folder hub: [[_Pipeline Flow]] · Index: [[_Master File Index]]
