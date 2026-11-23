#!/usr/bin/env bash
# Install the always-on backend as a per-user launchd agent.
#
#   scripts/install_launchd.sh            # install (or reinstall) and start
#   PYTHON=/path/to/python3 scripts/install_launchd.sh
#
# Interpreter: $PYTHON if set, else the repo's ./venv/bin/python (the README's
# setup), else the plist's default /usr/local/bin/python3 -m uvicorn. That
# python must have the backend requirements installed.
#
# Uninstall: launchctl bootout "gui/$(id -u)/com.swingtrader.backend"
#            rm ~/Library/LaunchAgents/com.swingtrader.backend.plist
set -euo pipefail

LABEL="com.swingtrader.backend"
REPO="/Users/ai-bot/SwingTrader-26"
SRC="$REPO/deploy/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DEFAULT_PYTHON="/usr/local/bin/python3"

if [[ -n "${PYTHON:-}" ]]; then
    python="$PYTHON"
elif [[ -x "$REPO/venv/bin/python" ]]; then
    python="$REPO/venv/bin/python"
else
    python="$DEFAULT_PYTHON"
fi

if ! (cd "$REPO" && SCHEDULER_ENABLED=false "$python" -c "import uvicorn, backend.app.main"); then
    echo "error: $python cannot import uvicorn and the backend;" \
        "install requirements or set PYTHON=..." >&2
    exit 1
fi

mkdir -p "$REPO/logs" "$HOME/Library/LaunchAgents"
sed "s#<string>$DEFAULT_PYTHON</string>#<string>$python</string>#" "$SRC" > "$DEST"
plutil -lint "$DEST" >/dev/null

domain="gui/$(id -u)"
launchctl bootout "$domain/$LABEL" 2>/dev/null || true
launchctl bootstrap "$domain" "$DEST"
echo "Installed $LABEL using $python; logs in $REPO/logs/"
