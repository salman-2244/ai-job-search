---
tags: [note, deps]
---
# 08 - Dependencies

What the project needs at runtime, and where each requirement is declared.
Parent: [[00 - Project Overview]].

## Python packages

**Declared requirement files:**

- `requirements-stage3.txt` (the only requirements file in the repo):
  - `python-telegram-bot==22.8` — Stage 3 + selector bots (exactly pinned so
    unattended polling cannot drift after an environment rebuild)
  - `playwright==1.62.0` — optional tier-3 enrichment (exactly pinned; browser
    binary installed separately: `playwright install chromium`)
- No general `requirements.txt` exists. The **pipeline itself is stdlib-only**
  (`json`, `re`, `subprocess`, `urllib`, `imaplib`, `smtplib`, `asyncio`, `csv`,
  `ssl`, `argparse`, `email`, `html.parser`, `dataclasses`, `zoneinfo`, …) — a
  deliberate choice so the 08:00 launchd job has no pip surface at all.

**Optional / dev-only Python packages:**

- `PyYAML` — needed by `tools/lint_skills.py` only (CI installs it explicitly).
- `pytest` — the test suite is written with pytest-style classes but runs under
  stdlib `unittest discover` in CI (`.github/workflows/ci.yml` runs
  `python -m unittest discover -s tests -t .`); the local `.venv` has pytest
  (`.pytest_cache/` present).
- Not used: `dotenv`, `requests`, `croniter` (the cron parser is in-house —
  `stage_3/schedules.py`; the requirements file documents this deliberately).

## Non-Python runtime dependencies

| Dependency | Used by | Notes |
|---|---|---|
| **Bun** | every portal-search CLI (`bun run .agents/skills/*-search/cli/src/cli.ts`) | TypeScript executed directly; `linkedin-search` and `freehire-search` have zero runtime deps (node_modules holds dev types only) |
| **Claude Code CLI (`claude`)** | document drafters (`telegram_select.generate_one`, Phase 4 QA) + the whole interactive workflow | Authenticated via the user's Claude account/gateway |
| **LaTeX: lualatex** (+ xelatex for the stock cover example) | CV/cover-letter compilation | CI uses `texlive/texlive` container; active templates `onepage-ats` + `minimal-onepage` are lualatex-only |
| **poppler (`pdftotext`, `pdfinfo`)** | ATS text-layer checks (`tools/verify_pdf.py`, drafting prompt Step 5) | Optional; checks degrade gracefully when missing |
| **Kimi WebBridge daemon** (`~/.kimi-webbridge/bin/kimi-webbridge`, port 10086) | tier-1 LinkedIn enrichment | External to the repo; started idempotently, never stopped by the pipeline |
| **macOS launchd** | both plists | `launchctl kickstart -k` for the selector |
| **`tg-notify`** (`~/.local/bin`) | Telegram pings | External CLI; guarded with `command -v` everywhere |
| **git + GitHub CLI** | upstream-watch, triage tools | |

## Portal CLI dependencies (`.agents/skills/*/cli/package.json`)

All zero-runtime-dependency by design (security posture: no lifecycle scripts —
enforced by `tools/security_guards.py` check 3). `linkedin-search` and
`freehire-search` run with plain `bun`; the four Danish portals install
TypeScript dev types via `bun install` (README quick start installs 6 of the 8).

## Version pinning summary

| Package | Version | Where |
|---|---|---|
| python-telegram-bot | `==22.8` (exact) | requirements-stage3.txt |
| playwright | `==1.62.0` (exact) | requirements-stage3.txt |
| PyYAML | unpinned (CI installs latest) | ci.yml lint job |
| GitHub Actions | pinned to commit SHAs | both workflows |
| Node (plist PATH hint) | v24.19.0 (nvm path baked into launchd PATH) | daily plist + `run_daily.sh:7` |

## Missing / implicit dependencies

- **No `requirements.txt` for the pipeline** — intentional (stdlib-only), but a
  fresh machine needs to know: Python 3.10+ (3.12 in CI), and only the two Stage 3
  pins if the Telegram bot or tier 3 is wanted. `docs/STAGE_3.md` §1 documents this.
- **`zoneinfo`** needs the system tz database (macOS ships it).
- **The `.venv`** at the repo root (gitignored) is the local interpreter the
  installer prefers (`STAGE3_PYTHON` → `.venv/bin/python` → `python3`).
- Anything relying on `tg-notify`, WebBridge, or `claude` degrades gracefully
  (guarded by `command -v` / try-import) rather than failing the run — the only
  hard external deps for the fetch→rank→report path are `bun`, the portal CLIs,
  and the configured LLM gateway.

Related: [[02 - Configuration and Settings]] · [[01 - Pipeline Architecture]] ·
[[09 - Recommendations]].

## Vault graph

Dependency files: [[requirements-stage3]] · [[package-linkedin]] · [[package-freehire]] · [[package-arbeitnow]] · [[package-weworkremotely]] · [[package-jobbank]] · [[package-jobdanmark]] · [[package-jobindex]] · [[package-jobnet]] · [[environment-dirs]] — hub: [[_Configuration Files]]
