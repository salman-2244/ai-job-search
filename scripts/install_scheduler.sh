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
for log in launchd-stdout.log launchd-stderr.log selector-stdout.log selector-stderr.log; do
    (umask 077; : >> "$PROJECT_DIR/logs/daily/$log")
    chmod 600 "$PROJECT_DIR/logs/daily/$log"
done

for name in com.salman.jobsearch.daily com.salman.jobsearch.selector; do
    source_plist="$PROJECT_DIR/$name.plist"
    installed_plist="$LAUNCH_AGENTS_DIR/$name.plist"
    launchctl unload "$installed_plist" 2>/dev/null || true
    "$PYTHON_BIN" "$PROJECT_DIR/scripts/render_launchd_plist.py" \
        "$source_plist" "$installed_plist" "$PROJECT_DIR" "$PYTHON_BIN"
    launchctl load "$installed_plist"
done

echo "Scheduler and selector installed successfully."
echo "  Checkout: $PROJECT_DIR"
echo "  Schedule: daily at 08:00 Europe/Budapest"
echo "  Labels: com.salman.jobsearch.daily, com.salman.jobsearch.selector"
echo ""
echo "Verify with: launchctl list | grep com.salman.jobsearch"
echo "Run manually: bash $PROJECT_DIR/scripts/run_daily.sh"
