#!/usr/bin/env bash
set -euo pipefail

# Install the daily job search pipeline scheduler via launchd.
# This registers a daily job at 08:00 Europe/Budapest.

PROJECT_DIR="/Users/salman/Projects/ai-job-search"
PLIST_NAME="com.salman.jobsearch.daily"
PLIST_SRC="${PROJECT_DIR}/${PLIST_NAME}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

# Ensure LaunchAgents directory exists
mkdir -p "$HOME/Library/LaunchAgents"

# Unload existing job if present (ignore errors)
launchctl unload "$PLIST_DST" 2>/dev/null || true

# Copy plist
cp "$PLIST_SRC" "$PLIST_DST"

# Load the job
launchctl load "$PLIST_DST"

echo "Scheduler installed successfully."
echo "  Plist: $PLIST_DST"
echo "  Schedule: daily at 08:00 Europe/Budapest"
echo "  Label: $PLIST_NAME"
echo ""
echo "Verify with: launchctl list | grep $PLIST_NAME"
echo "Run manually: bash ${PROJECT_DIR}/scripts/run_daily.sh"
