#!/bin/bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN=""
CANDIDATES=("/Library/Frameworks/Python.framework/Versions/Current/bin/python3" "/opt/homebrew/bin/python3" "/usr/local/bin/python3")
if command -v python3 >/dev/null 2>&1; then CANDIDATES+=("$(command -v python3)"); fi
for candidate in "${CANDIDATES[@]}"; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    if [ "$(uname -s)" = "Darwin" ]; then
        /usr/bin/osascript -e 'display alert "Python 3.9 or later is required" message "Install Python 3 from python.org, then reopen DIRECT STREAM FOR PLAYSTATION 5. No pip packages are needed." as critical'
    fi
    echo "Python 3.9+ not found. Install Python 3 from https://www.python.org/downloads/macos/" >&2
    exit 1
fi
exec "$PYTHON_BIN" "$APP_DIR/ps5_streamer.py" "$@"
