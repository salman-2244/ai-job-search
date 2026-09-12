---
tags: [file]
path: .github/workflows/ci.yml
type: config
---

# ci-workflow

## Location
`.github/workflows/ci.yml`

## Purpose
CI: lint_skills, security_guards, unittest discover, dependency-review (PR), LaTeX smoke compiles (lualatex CV + xelatex cover) with text-layer checks.

## Connections
### Calls or imports:
- [[lint-skills]] - lint job
- [[security-guards]] - guards job
- [[tests-init]] - unittest discover
- [[main-example-tex]] - lualatex smoke compile
- [[cover-example-tex]] - xelatex smoke compile

### Called by or imported by:
- (none known)

## Notes
Folder hub: [[_Configuration Files]] · Index: [[_Master File Index]]
