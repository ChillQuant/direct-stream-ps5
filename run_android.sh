#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# DIRECT STREAM FOR PLAYSTATION 5 — Android / Termux Launcher
# ==============================================================================

# Acquire WakeLock so Android won't sleep Wi-Fi during 50GB+ streaming
if command -v termux-wake-lock >/dev/null 2>&1; then
    echo "🔒 Enabling Android WakeLock (preventing sleep)..."
    termux-wake-lock
fi

# Ensure shared storage is accessible if not already set up
if [ ! -d "$HOME/storage" ] && command -v termux-setup-storage >/dev/null 2>&1; then
    echo "📂 Initializing Android shared storage access..."
    termux-setup-storage
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "🚀 Starting DIRECT STREAM FOR PLAYSTATION 5..."
python3 ps5_streamer.py "$@"
