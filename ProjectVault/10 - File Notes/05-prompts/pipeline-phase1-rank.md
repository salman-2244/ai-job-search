---
tags: [file]
path: prompts/pipeline_phase1_rank.md
type: prompt
---

# pipeline-phase1-rank

## Location
`prompts/pipeline_phase1_rank.md`

## Purpose
Phase 2 ranker prompt: strict JSON output, untrusted-jobs trust boundary, gate dimensions and weights (.30/.25/.15/.30).

## Connections
### Calls or imports:
- (none directly)

### Called by or imported by:
- [[run-daily]] - ranker prompt (via rank_jobs_api)
- [[rank-jobs-api]] - prompt template
- [[rank-jobs-legacy]] - legacy prompt usage
- [[cmd-rank]] - manual ranking prompt
- [[test_enrich_linkedin]] - tests
- [[test_gate_jobs]] - tests
- [[test_prerank_jobs]] - tests
- [[test_ranker_calibration]] - tests

## Notes
Folder hub: [[_Prompt Files]] · Index: [[_Master File Index]]
