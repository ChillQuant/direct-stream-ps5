#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# DIRECT STREAM FOR PLAYSTATION 5 — Android / Termux Launcher
# ==============================================================================

trap 'command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock || true' EXIT

# 1. Acquire WakeLock so Android won't sleep Wi-Fi during 50GB+ streaming
if command -v termux-wake-lock >/dev/null 2>&1; then
    echo "[WakeLock] Enabling Background WakeLock..."
    echo "   [Notice] If Android prompts to disable battery optimization, tap 'ALLOW' so streaming won't stop when screen locks."
    termux-wake-lock
fi

# 2. Ensure shared storage is accessible if not already set up
if [ ! -d "$HOME/storage" ] && command -v termux-setup-storage >/dev/null 2>&1; then
    echo "[Storage] Requesting Android File/Storage access..."
    echo "   [Notice] Please tap 'ALLOW' on the system popup to enable streaming local files from Downloads."
    termux-setup-storage
fi


DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Auto-register global 'ps5' shortcut in Termux if not already registered
PREFIX_BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin"
if [ -d "$PREFIX_BIN" ] && [ ! -f "$PREFIX_BIN/ps5" ]; then
    cat << EOF > "$PREFIX_BIN/ps5"
#!/data/data/com.termux/files/usr/bin/bash
exec "$DIR/run_android.sh" "\$@"
EOF
    chmod +x "$PREFIX_BIN/ps5" 2>/dev/null || true
    echo "[Shortcut] Registered global 'ps5' command. From now on, just type 'ps5' in Termux to launch."
fi

# Auto-register Termux:Widget shortcut for 1-tap Home Screen launch
SHORTCUTS_DIR="$HOME/.shortcuts"
if [ ! -f "$SHORTCUTS_DIR/PS5_Streamer.sh" ]; then
    mkdir -p "$SHORTCUTS_DIR" 2>/dev/null || true
    cat << EOF > "$SHORTCUTS_DIR/PS5_Streamer.sh"
#!/data/data/com.termux/files/usr/bin/bash
exec "$DIR/run_android.sh"
EOF
    chmod +x "$SHORTCUTS_DIR/PS5_Streamer.sh" 2>/dev/null || true
fi

echo "[DirectStream] Starting DIRECT STREAM FOR PLAYSTATION 5..."
python3 ps5_streamer.py "$@"

