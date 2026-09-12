---
tags: [file]
path: .claude/skills/job-scraper/SKILL.md
type: prompt
---

# skill-job-scraper

## Location
`.claude/skills/job-scraper/SKILL.md`

## Purpose
The /scrape workflow: orchestrates the .agents/skills portal CLIs, dedups against seen_jobs + tracker, presents new matches with quick fit.

## Connections
### Calls or imports:
- [[skill-linkedin-search]] - orchestrates portal CLIs
- [[seen-jobs]] - dedup state
- [[skill-search-queries]] - query vocabulary

### Called by or imported by:
- [[AGENTS]] - thin-pointer to workflow specs

## Notes
Folder hub: [[_Prompt Files]] · Index: [[_Master File Index]]
