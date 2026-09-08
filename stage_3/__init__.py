"""On-demand control surface for the job-search pipeline.

Stage 3 replaces the fixed launchd schedule with a Telegram bot that starts runs when
asked and reports progress while they run. It is deliberately thin: every phase of the
actual work still happens in `scripts/run_daily.sh` and the Stage 1/2 Python it calls.
This package only starts that script, reads its log, and renders what it finds.

Modules:
    config        env-file loading and validation; no secret ever reaches argv or a log
    progress      the pure phase model — log line in, progress state out
    render        progress state to Telegram markup and selection keyboards
    orchestrator  subprocess supervision, run manifests, cancellation, retention
    schedules     validated cron records with an atomic primary/backup store
    bot           the Telegram application: commands, keyboards, live progress,
                  the scheduler loop

The split exists so the interesting parts are testable without a bot token or a
network: `progress`, `render` and `schedules` are pure, and `orchestrator` takes an
injectable runner. Run the bot with `python -m stage_3.bot`.
"""

__all__ = ["config", "progress", "render", "orchestrator", "schedules", "bot"]
