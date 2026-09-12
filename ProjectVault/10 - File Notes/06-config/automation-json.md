---
tags: [file]
path: config/automation.json
type: config
---

# automation-json

## Location
`config/automation.json`

## Purpose
Live runtime switches + credentials (gitignored — values never documented here): schedule, max_jobs_to_apply, min_score_threshold, skip_portals, SMTP/IMAP, safety approval flags.

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[run-daily]] - reads switches/creds
- [[linkedin-alerts]] - IMAP creds
- [[gate-jobs]] - max_jobs_to_apply/min_score
- [[send-email]] - SMTP creds
- [[cmd-automation]] - edits switches
- [[automation-json-example]] - template of

## Notes
Folder hub: [[_Configuration Files]] · Index: [[_Master File Index]]
