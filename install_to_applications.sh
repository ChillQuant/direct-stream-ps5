#!/bin/bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_APP="$SOURCE_DIR/PS5 Direct Streamer.app"
APPS_DIR="$HOME/Applications"
DEST_APP="$APPS_DIR/PS5 Direct Streamer.app"
if [ "$(uname -s)" != "Darwin" ]; then echo "This installer is for macOS." >&2; exit 1; fi
if [ ! -f "$SOURCE_APP/Contents/Resources/transfer_core.py" ]; then echo "App bundle is incomplete. Extract the entire ZIP again." >&2; exit 1; fi
mkdir -p "$APPS_DIR"
STAGE_DIR="$(mktemp -d "$APPS_DIR/.ps5-install.XXXXXX")"
trap 'rmdir "$STAGE_DIR" 2>/dev/null || true' EXIT
/usr/bin/ditto "$SOURCE_APP" "$STAGE_DIR/PS5 Direct Streamer.app"
chmod +x "$STAGE_DIR/PS5 Direct Streamer.app/Contents/MacOS/ps5_streamer"
if [ -d "$DEST_APP" ]; then
    BACKUP="$APPS_DIR/PS5 Direct Streamer.backup.$(date +%Y%m%d-%H%M%S).app"
    mv "$DEST_APP" "$BACKUP"
    echo "Previous version preserved at: $BACKUP"
fi
mv "$STAGE_DIR/PS5 Direct Streamer.app" "$DEST_APP"
touch "$DEST_APP"
echo "Installed: $DEST_APP"
echo "Close any old running version, then open the installed app."
