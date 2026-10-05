#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# DIRECT STREAM FOR PLAYSTATION 5 — 1-Line Universal Android Installer
# ==============================================================================
set -e

echo "🎮 DIRECT STREAM FOR PLAYSTATION 5 — Android Setup"
echo "--------------------------------------------------------"

# 1. Update Termux and install dependencies
echo "📦 Installing prerequisites (python, git)..."
if command -v pkg >/dev/null 2>&1; then
    pkg update -y || true
    pkg install -y python git || true
elif command -v apt-get >/dev/null 2>&1; then
    apt-get update -y || true
    apt-get install -y python git || true
fi

# 2. Target installation directory
TARGET_DIR="$HOME/direct-stream-ps5"
BRANCH="${BRANCH:-main}"


if [ -d "$TARGET_DIR/.git" ]; then
    echo "🔄 Existing installation found. Updating to latest version..."
    cd "$TARGET_DIR"
    git fetch origin "$BRANCH" 2>/dev/null || true
    git checkout "$BRANCH" 2>/dev/null || true
    git pull origin "$BRANCH" 2>/dev/null || true
else
    echo "📥 Downloading Direct Stream for PlayStation 5..."
    git clone -b "$BRANCH" https://github.com/ChillQuant/direct-stream-ps5.git "$TARGET_DIR"
    cd "$TARGET_DIR"
fi

chmod +x "$TARGET_DIR/run_android.sh"

# 3. Create global 'ps5' shortcut in Termux binary path
PREFIX_BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin"
if [ -d "$PREFIX_BIN" ]; then
    cat << EOF > "$PREFIX_BIN/ps5"
#!/data/data/com.termux/files/usr/bin/bash
exec "$TARGET_DIR/run_android.sh" "\$@"
EOF
    chmod +x "$PREFIX_BIN/ps5"
    echo "✨ Shortcut installed! From now on, just type 'ps5' in Termux."
fi

# 4. Create Termux:Widget shortcut for Home Screen
SHORTCUTS_DIR="$HOME/.shortcuts"
mkdir -p "$SHORTCUTS_DIR"
cat << EOF > "$SHORTCUTS_DIR/PS5_Streamer.sh"
#!/data/data/com.termux/files/usr/bin/bash
exec "$TARGET_DIR/run_android.sh"
EOF
chmod +x "$SHORTCUTS_DIR/PS5_Streamer.sh"

echo "--------------------------------------------------------"
echo "🚀 Setup complete! Launching DIRECT STREAM FOR PLAYSTATION 5..."
exec "$TARGET_DIR/run_android.sh" "$@"
