#!/usr/bin/env bash
set -euo pipefail

# Install the daily pipeline and on-demand selector for this physical checkout.
SCRIPT_SOURCE="${BASH_SOURCE[0]:-$0}"
while [[ -L "$SCRIPT_SOURCE" ]]; do
    SCRIPT_DIR=$(cd -P -- "$(dirname -- "$SCRIPT_SOURCE")" && pwd)
    SCRIPT_SOURCE=$(readlink "$SCRIPT_SOURCE")
    if [[ "$SCRIPT_SOURCE" != /* ]]; then
        SCRIPT_SOURCE="$SCRIPT_DIR/$SCRIPT_SOURCE"
    fi
done
SCRIPT_DIR=$(cd -P -- "$(dirname -- "$SCRIPT_SOURCE")" && pwd)
PROJECT_DIR=$(cd -P -- "$SCRIPT_DIR/.." && pwd)
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PYTHON_BIN="${STAGE3_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
    if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
    else
        PYTHON_BIN=$(command -v python3 || true)
    fi
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
    echo "FATAL: no executable Python found; set STAGE3_PYTHON" >&2
    exit 1
fi
PYTHON_BIN=$(cd -P -- "$(dirname -- "$PYTHON_BIN")" && pwd)/$(basename -- "$PYTHON_BIN")

mkdir -p "$LAUNCH_AGENTS_DIR" "$PROJECT_DIR/logs/daily"
chmod 700 "$LAUNCH_AGENTS_DIR" 2>/dev/null || true
for log in launchd-stdout.log launchd-stderr.log selector-stdout.log selector-stderr.log \
           stage3-stdout.log stage3-stderr.log; do
    (umask 077; : >> "$PROJECT_DIR/logs/daily/$log")
    chmod 600 "$PROJECT_DIR/logs/daily/$log"
done

# Runs are triggered from Telegram, so the Stage 3 bot is what has to be up; the
# 08:00 clock trigger is off. com.salman.jobsearch.daily is still rendered and
# installed so `launchctl kickstart` and a manual bash run_daily.sh keep working,
# but it is deliberately NOT loaded -- appending it to the load list below would
# silently resurrect the schedule every time this installer runs.
#
# Set JOBSEARCH_ENABLE_DAILY=1 to put the 08:00 trigger back.
LOAD_LABELS=(com.salman.jobsearch.stage3 com.salman.jobsearch.selector)
RENDER_ONLY_LABELS=(com.salman.jobsearch.daily)
if [[ "${JOBSEARCH_ENABLE_DAILY:-0}" == "1" ]]; then
    LOAD_LABELS+=(com.salman.jobsearch.daily)
    RENDER_ONLY_LABELS=()
fi

render_plist() {
    local name="$1"
    "$PYTHON_BIN" "$PROJECT_DIR/scripts/render_launchd_plist.py" \
        "$PROJECT_DIR/$name.plist" "$LAUNCH_AGENTS_DIR/$name.plist" \
        "$PROJECT_DIR" "$PYTHON_BIN"
}

for name in ${RENDER_ONLY_LABELS+"${RENDER_ONLY_LABELS[@]}"}; do
    launchctl unload "$LAUNCH_AGENTS_DIR/$name.plist" 2>/dev/null || true
    render_plist "$name"
done

for name in "${LOAD_LABELS[@]}"; do
    launchctl unload "$LAUNCH_AGENTS_DIR/$name.plist" 2>/dev/null || true
    render_plist "$name"
    launchctl load "$LAUNCH_AGENTS_DIR/$name.plist"
done

echo "Stage 3 bot and selector installed successfully."
echo "  Checkout: $PROJECT_DIR"
if [[ "${JOBSEARCH_ENABLE_DAILY:-0}" == "1" ]]; then
    echo "  Schedule: daily at 08:00 Europe/Budapest (JOBSEARCH_ENABLE_DAILY=1)"
else
    echo "  Schedule: none -- runs are started from Telegram with /run <count>"
    echo "            (08:00 trigger rendered but not loaded; set"
    echo "             JOBSEARCH_ENABLE_DAILY=1 to re-enable it)"
fi
echo "  Loaded: ${LOAD_LABELS[*]}"
echo ""
echo "Verify with: launchctl list | grep com.salman.jobsearch"
echo "Start a run: send /run 15 to the Stage 3 bot on Telegram"
echo "Run manually: bash $PROJECT_DIR/scripts/run_daily.sh"
