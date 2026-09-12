---
tags: [file]
path: tools/lint_skills.py
type: python
---

# lint-skills

## Location
`tools/lint_skills.py`

## Purpose
Skill/command frontmatter linter (needs PyYAML); run in CI over .claude/skills and .claude/commands.

## Connections
### Calls or imports:
- [[skill-job-application-assistant]] - lints frontmatter
- [[cmd-apply]] - lints command frontmatter

### Called by or imported by:
- [[ci-workflow]] - lint job
- [[test_lint_skills]] - tests

## Notes
Folder hub: [[_Script Files]] · Index: [[_Master File Index]]
