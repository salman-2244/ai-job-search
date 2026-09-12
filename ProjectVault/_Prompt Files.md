---
tags: [hub]
type: prompt
---


# _Prompt Files

LLM prompts, slash commands, and skill specs — and which pipeline phase uses each. Index: [[_Master File Index]].

## prompts/ — pipeline prompts

- [[pipeline-phase1-rank]] — Phase 2 (rank_jobs_api) — the only paid LLM call
- [[pipeline-phase2-draft]] — legacy batch draft path (generate_batch)
- [[pipeline-phase3-qa]] — Phase 4 QA (vestigial, run_daily)
- [[selected-job-draft]] — Phase 4 interactive — per-job drafter behind every Telegram Submit

## .claude/commands/ — slash commands

- [[cmd-add-portal]]
- [[cmd-add-template]]
- [[cmd-apply]]
- [[cmd-automation]]
- [[cmd-expand]]
- [[cmd-gmail-sync]]
- [[cmd-html-report]]
- [[cmd-interview]]
- [[cmd-linkedin-alerts]]
- [[cmd-notion-sync]]
- [[cmd-outcome]]
- [[cmd-rank]]
- [[cmd-reset]]
- [[cmd-setup]]

## .claude/skills/ — skill specs

- [[agent-gemini-research-expert]]
- [[skill-01-candidate-profile]]
- [[skill-02-behavioral-profile]]
- [[skill-03-writing-style]]
- [[skill-04-job-evaluation]]
- [[skill-05-cv-templates]]
- [[skill-06-cover-letter-templates]]
- [[skill-07-interview-prep]]
- [[skill-08-application-forms]]
- [[skill-09-web-research]]
- [[skill-job-application-assistant]]
- [[skill-job-scraper]]
- [[skill-search-queries]]
- [[skill-upskill]]

## .agents/skills/ — portal SKILL.md files

- [[skill-linkedin-search]]
- [[skill-freehire-search]]
- [[skill-arbeitnow-search]]
- [[skill-weworkremotely-search]]
- [[skill-jobbank-search]]
- [[skill-jobdanmark-search]]
- [[skill-jobindex-search]]
- [[skill-jobnet-search]]

Agent definition: [[agent-gemini-research-expert]] · Linted by: [[lint-skills]] · Index: [[_Master File Index]]
