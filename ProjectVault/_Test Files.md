---
tags: [hub]
type: test
---


# _Test Files

Python tests (tests/) and portal CLI tests (.agents/skills/*/cli/tests/). Index: [[_Master File Index]].

## Python test suite (tests/)

- [[test_aggregate_jobs]] → covers [[aggregate-jobs]] · [[linkedin-alerts]]
- [[test_apply_records_application]] → covers [[cmd-apply]]
- [[test_check_upstream_updates]] → covers [[check-upstream-updates]]
- [[test_convert_salary_excel]] → covers [[convert-salary-excel]] · [[salary_lookup]]
- [[test_enrich_browser_path]] → covers [[enrich-linkedin]] · [[linkedin-extract]]
- [[test_enrich_linkedin]] → covers [[enrich-linkedin]] · [[aggregate-jobs]]
- [[test_gate_jobs]] → covers [[gate-jobs]] · [[linkedin-alerts]]
- [[test_hard_gates]] → covers [[hard-gates]]
- [[test_html_report_command]] → covers [[cmd-html-report]]
- [[test_linkedin_alerts]] → covers [[linkedin-alerts]]
- [[test_linkedin_extract]] → covers [[linkedin-extract]]
- [[test_linkedin_playwright]] → covers [[linkedin-playwright]]
- [[test_lint_skills]] → covers [[lint-skills]]
- [[test_notification_diagnostics]] → covers [[stage3-diagnostics]]
- [[test_notification_e2e]] → covers [[stage3-bot]] · [[stage3-config]] · [[stage3-diagnostics]]
- [[test_notion_sync_command]] → covers [[cmd-notion-sync]]
- [[test_outcome_followup]] → covers [[cmd-outcome]]
- [[test_prerank_jobs]] → covers [[prerank-jobs]] · [[enrich-linkedin]] · [[run-daily]]
- [[test_rank_command]] → covers [[cmd-rank]]
- [[test_rank_jobs_api]] → covers [[rank-jobs-api]]
- [[test_ranker_calibration]] → covers [[pipeline-phase1-rank]] · [[run-daily]]
- [[test_readme_assets]] → covers [[README-root]] · [[pip-flight-loop-gif]]
- [[test_report_gate_surface]] → covers [[run-daily]]
- [[test_robots_check]] → covers [[robots-check]]
- [[test_salary_lookup]] → covers [[salary_lookup]] · [[readme-salary-tool]]
- [[test_search_plan]] → covers [[build-search-plan]] · [[search-matrix]]
- [[test_security_guards]] → covers [[security-guards]]
- [[test_seen_jobs_migration]] → covers [[migrate-seen-jobs]] · [[seen-jobs]]
- [[test_selector_listener]] → covers [[selector-listener]] · [[telegram-select]]
- [[test_selector_resilience]] → covers [[selector-listener]]
- [[test_stage3_bot]] → covers [[stage3-bot]]
- [[test_stage3_config]] → covers [[stage3-config]]
- [[test_stage3_docs]] → covers [[docs-stage3]] · [[stage3-bot]]
- [[test_stage3_orchestrator]] → covers [[stage3-orchestrator]]
- [[test_stage3_outputs]] → covers [[stage3-orchestrator]] · [[rank-jobs-api]]
- [[test_stage3_progress]] → covers [[stage3-progress]]
- [[test_stage3_regression]] → covers [[stage3-bot]] · [[stage3-orchestrator]] · [[stage3-schedules]]
- [[test_stage3_render]] → covers [[stage3-render]]
- [[test_stage3_schedules]] → covers [[stage3-schedules]]
- [[test_telegram_integration]] → covers [[stage3-config]]
- [[test_telegram_select]] → covers [[telegram-select]]
- [[test_tracker_status_vocab]] → covers [[job-search-tracker-csv]]
- [[test_two_axis_score]] → covers [[two-axis-score]] · [[prerank-jobs]]
- [[test_upskill_skill]] → covers [[skill-upskill]]
- [[test_upstream_triage]] → covers [[upstream-triage]]
- [[test_verify_pdf]] → covers [[verify-pdf]]

Fixtures: [[sandbox-rankset]] · package marker: [[tests-init]]

## Portal CLI tests (.agents/skills/*/cli/tests/)

- **linkedin-search**: [[test-cli-flag-validation-linkedin]] · [[test-parsing-linkedin]] · [[test-request-timeout-linkedin]] · [[test-retry-backoff-linkedin]] · [[test-search-linkedin]] (fixtures: [[testhelpers-linkedin]]) → exercise [[cli-linkedin]]
- **freehire-search**: [[test-cli-flag-validation-freehire]] · [[test-commands-freehire]] · [[test-parsing-freehire]] · [[test-request-timeout-freehire]] · [[test-retry-backoff-freehire]] (fixtures: [[testhelpers-freehire]]) → exercise [[cli-freehire]]
- **arbeitnow-search**: [[test-cli-flag-validation-arbeitnow]] · [[test-parsing-arbeitnow]] (fixtures: [[testhelpers-arbeitnow]]) → exercise [[cli-arbeitnow]]
- **weworkremotely-search**: no test files
- **jobbank-search**: [[test-cli-contract-jobbank]] · [[test-cli-flag-validation-jobbank]] · [[test-detail-jsonld-jobbank]] · [[test-request-timeout-jobbank]] · [[test-retry-backoff-jobbank]] · [[test-rss-fetch-jobbank]] · [[test-rss-parsing-jobbank]] (fixtures: [[testhelpers-jobbank]]) → exercise [[cli-jobbank]]
- **jobdanmark-search**: [[test-cli-contract-jobdanmark]] · [[test-cli-flag-validation-jobdanmark]] · [[test-detail-jsonld-jobdanmark]] · [[test-detail-parsing-jobdanmark]] · [[test-request-timeout-jobdanmark]] · [[test-retry-backoff-jobdanmark]] · [[test-user-agent-jobdanmark]] (fixtures: [[testhelpers-jobdanmark]]) → exercise [[cli-jobdanmark]]
- **jobindex-search**: [[test-cli-contract-jobindex]] · [[test-cli-flag-validation-jobindex]] · [[test-parsing-jobindex]] · [[test-request-timeout-jobindex]] · [[test-retry-backoff-jobindex]] (fixtures: [[testhelpers-jobindex]]) → exercise [[cli-jobindex]]
- **jobnet-search**: [[test-cli-contract-jobnet]] · [[test-cli-flag-validation-jobnet]] · [[test-detail-formatting-jobnet]] · [[test-request-timeout-jobnet]] · [[test-retry-backoff-jobnet]] · [[test-search-normalization-jobnet]] · [[test-user-agent-jobnet]] (fixtures: [[testhelpers-jobnet]]) → exercise [[cli-jobnet]]

CI runner: [[ci-workflow]] · Index: [[_Master File Index]]
