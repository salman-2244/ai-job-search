"""On-demand control surface for the job-search pipeline.

Stage 3 replaces the fixed launchd schedule with a Telegram bot that starts runs when
asked and reports progress while they run. It is deliberately thin: every phase of the
actual work still happens in `scripts/run_daily.sh` and the Stage 1/2 Python it calls.
This package only starts that script, reads its log, and renders what it finds.

Modules:
    config       env-file loading and validation; no secret ever reaches argv or a log
    progress     the pure phase model — log line in, progress state out
    render       progress state to Telegram markup
    orchestrator subprocess supervision, the run lock, cancellation
    bot          the Telegram application: /start, /status, /run, /cancel, keyboards

The split exists so the interesting parts are testable without a bot token or a
network: `progress` and `render` are pure functions over strings, and `orchestrator`
takes an injectable runner.
"""

__all__ = ["config", "progress", "render", "orchestrator", "bot"]
