---
tags: [hub]
type: script
---


# _Script Files

Shell scripts and the Python pipeline scripts they drive. Flow: [[_Pipeline Flow]]. Index: [[_Master File Index]].

## Shell scripts (scripts/)

- [[run-daily]] — master orchestrator — calls [[linkedin-alerts]], [[build-search-plan]], portal CLIs via bun, [[aggregate-jobs]], [[prerank-jobs]], [[enrich-linkedin]], [[rank-jobs-api]], [[gate-jobs]], [[write-selection-handoff]], [[selector-listener]], [[send-email]]
- [[install-scheduler]] — installs both launchd jobs via [[render-launchd-plist]] from [[com-salman-jobsearch-daily-plist]] + [[com-salman-jobsearch-selector-plist]]
- [[uninstall-scheduler]] — boots out the jobs installed by [[install-scheduler]]
- [[apply-optimizations]] — stale bulk LaTeX pass over [[optimize-documents]] + [[optimize-content]]

## Python pipeline scripts (scripts/)

- [[build-search-plan]]
- [[aggregate-jobs]]
- [[prerank-jobs]]
- [[two-axis-score]]
- [[hard-gates]]
- [[enrich-linkedin]]
- [[linkedin-extract]]
- [[linkedin-playwright]]
- [[linkedin-session]]
- [[linkedin-alerts]]
- [[rank-jobs-api]]
- [[rank-jobs-legacy]]
- [[gate-jobs]]
- [[telegram-select]]
- [[selector-listener]]
- [[write-selection-handoff]]
- [[generate-batch]]
- [[watch-generation]]
- [[migrate-seen-jobs]]
- [[send-email]]
- [[optimize-documents]]
- [[optimize-content]]
- [[render-launchd-plist]]

## stage_3/ (bot package)

- [[stage3-bot]] · [[stage3-config]] · [[stage3-orchestrator]] · [[stage3-progress]] · [[stage3-render]] · [[stage3-schedules]] · [[stage3-diagnostics]] · [[stage3-init]]

## tools/ (dev/CI utilities)

- [[security-guards]] · [[lint-skills]] · [[verify-pdf]] · [[robots-check]] · [[check-upstream-updates]] · [[upstream-triage]] · [[check-framework-version]] · [[convert-salary-excel]]

## Root scripts

- [[salary_lookup]]

Index: [[_Master File Index]]
