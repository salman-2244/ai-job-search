#!/usr/bin/env bash
set -euo pipefail

# Uninstall the daily job search pipeline scheduler.

PLIST_NAME="com.salman.jobsearch.daily"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

if [[ ! -f "$PLIST_DST" ]]; then
    echo "Scheduler not installed (plist not found at $PLIST_DST)"
    exit 0
fi

# Unload the job (ignore errors if already unloaded)
launchctl unload "$PLIST_DST" 2>/dev/null || true

# Remove the plist
rm -f "$PLIST_DST"

echo "Scheduler uninstalled."
echo "  Removed: $PLIST_DST"
echo "  Pipeline will no longer run automatically."
