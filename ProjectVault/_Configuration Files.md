---
tags: [hub]
type: config
---


# _Configuration Files

Every config surface and which code reads it. Detail: [[02 - Configuration and Settings]]. Index: [[_Master File Index]].

## [[search-matrix]]
`config/search_matrix.json`
Read by / used by: [[build-search-plan]] · [[prerank-jobs]] · [[two-axis-score]] · [[linkedin-alerts]] · [[run-daily]]

## [[automation-json]]
`config/automation.json`
Read by / used by: [[linkedin-alerts]] · [[gate-jobs]] · [[send-email]] · [[run-daily]]

## [[automation-json-example]]
`config/automation.json.example`
Read by / used by: [[automation-json]]

## [[claude-settings]]
`.claude/settings.json`
Read by / used by: [[rank-jobs-api]] · [[security-guards]]

## [[claude-settings-local]]
`.claude/settings.local.json`
Read by / used by: [[claude-settings]]

## [[gitignore]]
`.gitignore`
Read by / used by: [[security-guards]]

## [[requirements-stage3]]
`requirements-stage3.txt`
Read by / used by: [[stage3-bot]] · [[linkedin-playwright]]

## [[com-salman-jobsearch-daily-plist]]
`com.salman.jobsearch.daily.plist`
Read by / used by: [[run-daily]] · [[install-scheduler]]

## [[com-salman-jobsearch-selector-plist]]
`com.salman.jobsearch.selector.plist`
Read by / used by: [[selector-listener]] · [[install-scheduler]]

## [[ci-workflow]]
`.github/workflows/ci.yml`
Read by / used by: [[lint-skills]] · [[security-guards]] · [[main-example-tex]] · [[cover-example-tex]]

## [[upstream-watch-workflow]]
`.github/workflows/upstream-watch.yml`
Read by / used by: [[check-upstream-updates]] · [[upstream-triage]]

## [[upstream-wontport]]
`.github/upstream-wontport.txt`
Read by / used by: [[upstream-triage]]

## [[funding-yml]]
`.github/FUNDING.yml`
No code readers (repo metadata).

## [[env-stage3-worktree]]
`.claude/worktrees/stage-3-completion/.env.stage3`
Read by / used by: [[stage3-config]]

Per-portal config: [[package-linkedin]] · [[package-freehire]] · [[package-arbeitnow]] · [[package-weworkremotely]] · [[package-jobbank]] · [[package-jobdanmark]] · [[package-jobindex]] · [[package-jobnet]]

TypeScript configs: [[tsconfig-linkedin]] · [[tsconfig-freehire]] · [[tsconfig-arbeitnow]] · [[tsconfig-weworkremotely]] · [[tsconfig-jobbank]] · [[tsconfig-jobdanmark]] · [[tsconfig-jobindex]] · [[tsconfig-jobnet]]

Bun lockfiles: [[bunlock-linkedin]] · [[bunlock-freehire]] · [[bunlock-arbeitnow]] · [[bunlock-jobbank]] · [[bunlock-jobdanmark]] · [[bunlock-jobindex]] · [[bunlock-jobnet]]

Index: [[_Master File Index]]
Flow: [[_Pipeline Flow]]
