# GitHub Repository Metadata & Release Notes

Use this document to copy and paste metadata directly into your new GitHub repository.

---

## 1. Repository Details

### Repository Name
```text
direct-stream-ps5
```
*(or `Direct-Stream-PlayStation-5`)*

### Short Description (About box on GitHub)
```text
High-speed parallel stream engine & local web dashboard for streaming HTTP/HTTPS downloads directly to PlayStation 5 FTP storage. Pure Python standard library, zero pip packages, macOS & Windows.
```

### Website URL (optional)
*(Leave blank or link to your personal profile / releases page)*

### Repository Topics / Tags
```text
ps5, playstation-5, ftp, ftp-streamer, parallel-download, direct-stream, zero-dependencies, python, macos, windows, web-dashboard, networking
```

---

## 2. GitHub Release Notes (v2.8.6 — Smart Direct Link Resolver)

### Tag Version
```text
v2.8.6
```

### Release Title
```text
DIRECT STREAM FOR PLAYSTATION 5 v2.8.6 — Smart Direct Link Resolver
```

### Release Description:
```markdown
## DIRECT STREAM FOR PLAYSTATION 5 (v2.8.6)

Direct Stream for PlayStation 5 now features a built-in **Smart Direct Download Link Resolver**! You can now paste links directly from popular file hosting services without manually extracting the underlying download URL.

---

### What's New in v2.8.6

- **Smart Direct Download Resolver**:
  - **MediaFire**: Automatically extracts direct `.pkg` download URLs from standard sharing links.
  - **PixelDrain**: Converts `pixeldrain.com/u/<id>` sharing links to raw high-speed download endpoints.
  - **Google Drive**: Converts standard Drive sharing links to direct export streams, automatically handling Google's large file virus-scan confirmation screens.
  - **Archive.org**: Seamlessly resolves `archive.org/details/<item>` links to high-speed `/download/` streams.
  - **GoFile**: Pre-resolves sharing pages to direct content streams.
- **Smart Filename Extraction**:
  - Extracts true filenames from HTTP `Content-Disposition` response headers.
- **CAPTCHA & Quota Awareness**:
  - Friendly warnings when a hosting provider requires manual CAPTCHA solving or rate-limit cooldowns.
- **Pure Python Standard Library**:
  - Zero external pip dependencies required.

---

### Push Command (When Ready to Release)
```bash
git checkout main
git merge feature/smart-resolver
git tag v2.8.6
git push origin main --tags
```
```

---

## 3. GitHub Release Notes (v2.8.8 — Multi-Part File Stitching Engine)

### Tag Version
```text
v2.8.8
```

### Release Title
```text
DIRECT STREAM FOR PLAYSTATION 5 v2.8.8 — Multi-Part File Stitching Engine
```

### Release Description:
```markdown
## DIRECT STREAM FOR PLAYSTATION 5 (v2.8.8)

Stream split multi-part files directly into a unified `.pkg` on your PS5 with **Zero Local Disk Buffering**!

---

### What's New in v2.8.8

- **Multi-Part File Stitching Pipeline**:
  - Automatically detects, sorts, and stitches split multi-part files (`.001`, `.002`, `.part1.rar`, etc.).
  - Works over HTTP URLs, local files, or mixed sources.
- **Continuous Zero-Gap FTP Streaming**:
  - Pre-warms the connection for the next part while the current part finishes, eliminating network stall.
- **Zero Local Disk Footprint**:
  - No need to extract or stitch files on your PC or phone first.

---

### Push Command (When Ready to Release)
```bash
git checkout main
git merge feature/multipart-stitcher
git tag v2.8.8
git push origin main --tags
```
```

---

## 4. GitHub Release Notes (v2.9.0 — On-The-Fly Archive Decompression)

### Tag Version
```text
v2.9.0
```

### Release Title
```text
DIRECT STREAM FOR PLAYSTATION 5 v2.9.0 — On-The-Fly Archive Decompressor
```

### Release Description:
```markdown
## DIRECT STREAM FOR PLAYSTATION 5 (v2.9.0)

Stream and extract compressed `.zip` archives directly into PlayStation 5 storage in RAM with **Zero Local Disk Space Required**!

---

### What's New in v2.9.0

- **On-The-Fly Streaming Decompression**:
  - Extract `.zip` (Deflate & Stored) directly to PS5 FTP storage in real-time.
  - **0 GB Local Disk Used**: Stream a 70 GB zipped game from a phone or laptop with only 2 GB of free storage.
- **Sub-Second Remote Header Probing**:
  - Inspects remote ZIP directories in ~100 ms via lightweight HTTP Range requests (~64 KB).
  - Automatically identifies the inner `.pkg` file and its uncompressed size.
- **Tough Data Integrity Guards**:
  - Accurate PS5 storage pre-check against uncompressed file size.
  - Strict size verification before atomic rename.
  - Prevents corrupted mid-stream resumes on compressed bitstreams.
- **Web Dashboard Integration**:
  - Automatic `.zip` detection badge in the UI with an on-the-fly decompression toggle.

---

### Push Command (When Ready to Release)
```bash
git checkout main
git merge feature/v2.9.0-decompressor
git tag v2.9.0
git push origin main --tags
```
```

---

## 5. GitHub Release Notes (v2.8.5 — Android & Mobile Update)

### Tag Version
```text
v2.8.5
```

### Release Title
```text
DIRECT STREAM FOR PLAYSTATION 5 v2.8.5 — Android Support & Mobile UI
```

### Release Description:
```markdown
## DIRECT STREAM FOR PLAYSTATION 5 (v2.8.5)

Direct Stream for PlayStation 5 now runs natively on Android phones with **raw Wi-Fi throughput** and zero root required!

---

### What's New in v2.8.5

- **Native Android & Termux Support**:
  - Run directly on Android devices with zero root needed.
  - **⚡ 1-Line Universal Installer**: Set up everything in one copy-paste command:
    ```bash
    curl -sSL https://raw.githubusercontent.com/ChillQuant/direct-stream-ps5/main/install_android.sh | bash
    ```
  - **🚀 Global `ps5` Command**: Automatically registered in Termux so you can launch by just typing `ps5` anywhere.
  - **🔒 Background WakeLock (`termux-wake-lock`)**: Prevents Android from throttling Wi-Fi or putting Termux to sleep when the screen locks during 50GB–100GB transfers.
  - **📂 Local Phone Storage (`termux-setup-storage`)**: Stream `.pkg` game packages directly from your phone's `/sdcard/Download/` folder.
- **Mobile Responsive UI**:
  - Brand-new single-column touch-optimized layout for mobile screens.
  - **Sticky Bottom Navigation Bar**: One-handed thumb access to **Queue**, **Transfer**, **Settings**, and **Speed Test**.
  - **PWA Standalone App**: Tap **"Add to Home screen"** in Chrome to install Direct Stream as a standalone app with its own icon and no browser URL bar.
- **Universal Push Notifications**:
  - Upgraded notifications to support both macOS Notification Center and Android (`termux-notification`) when transfers complete or fail.
- **Cross-Platform Fixes & Hardening**:
  - Persistent session tokens across mobile browser tabs and tab restoring.
  - Cross-platform offline reconnect prompts.

---

### Downloads & Installation

| Package | Target Platform | Instructions |
| :--- | :--- | :--- |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-Windows.zip`** | Windows 10 / 11 | Extract and run `Launch Direct Stream for PlayStation 5.bat` |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-macOS.zip`** | macOS (Apple Silicon & Intel) | Extract and open `PS5 Direct Streamer.app` |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-PurePython.zip`** | Android / Linux / Cross-Platform | Includes `install_android.sh`, `run_android.sh`, and pure Python CLI |

*Requires Python 3.9+ from [python.org](https://www.python.org/downloads/) or Termux.*
```

---

## 3. GitHub Release Notes (v2.8.1 Hotfix)


### Tag Version
```text
v2.8.1
```

### Release Title
```text
DIRECT STREAM FOR PLAYSTATION 5 v2.8.1 — Windows Hotfix
```

### Release Description:
```markdown
## DIRECT STREAM FOR PLAYSTATION 5 (v2.8.1 Hotfix)

This release resolves a critical startup crash on Windows environments in v2.8.0.

### Fixes & Improvements in v2.8.1

- **Fixed Windows Launch Crash**: Resolved an unguarded Unix `fcntl` import in `main()` that caused `ModuleNotFoundError: No module named 'fcntl'` when running `Launch Direct Stream for PlayStation 5.bat` or `run.bat`.
- **Cross-Platform Single-Instance Locking**: Replaced `fcntl` with `_lock_instance()`:
  - **Windows (`os.name == 'nt'`)**: Uses native `msvcrt` non-blocking byte-range file locking (`msvcrt.LK_NBLCK`).
  - **macOS & Linux**: Uses POSIX `fcntl.flock()`.
  - **Graceful Fallback**: Safely continues if locking primitives are unavailable.
- **Single-Instance Dashboard Reuse**: Preserved existing dashboard reuse when relaunching the app.
- **100% Pure Python**: Zero external pip dependencies required.

---

### Downloads & Installation

| Package | Target Platform | Instructions |
| :--- | :--- | :--- |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-Windows.zip`** | Windows 10 / 11 | Extract and run `Launch Direct Stream for PlayStation 5.bat` |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-macOS.zip`** | macOS (Apple Silicon & Intel) | Extract and open `PS5 Direct Streamer.app` |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-PurePython.zip`** | Linux / BSD / Cross-Platform | Extract and run `python3 ps5_streamer.py` |

*Requires Python 3.9+ from [python.org](https://www.python.org/downloads/).*
```

---

## 3. GitHub Release Notes (v2.8.0)


### Tag Version
```text
v2.8.0
```

### Release Title
```text
DIRECT STREAM FOR PLAYSTATION 5 v2.8.0 — High-Performance Direct FTP Streamer
```

### Release Description (Copy & Paste into GitHub Releases):
```markdown
## DIRECT STREAM FOR PLAYSTATION 5 (v2.8.0)

A high-performance parallel transfer engine and local web dashboard for streaming HTTP/HTTPS web downloads directly into PlayStation 5 FTP storage, or uploading local files over Gigabit LAN. 

**Zero cloud staging, zero pip packages, zero Node/npm dependencies.** Runs 100% locally on Python 3.9+ standard library.

---

### What's New in v2.8.0

- **Precision Multi-Stream Parallel Engine**:
  - Configurable 1–32 concurrent range workers with bounded memory ring buffer (32 MiB – 1024 MiB).
  - 4 calibrated quick presets: **Conservative**, **Balanced**, **Turbo**, and **Max Saturation** (16 streams × 32 MiB chunk × 512 MiB RAM).
- **Speed Diagnostics & Benchmark Suite**:
  - **Quick Scan (~25s)** and **Full Test (~60s Multi-Tier Matrix)** to automatically measure your network link and find the fastest throughput configuration.
  - 1-click **Apply Recommended Settings** button.
- **Cross-Platform Support**:
  - **macOS:** Native `.app` bundle, double-clickable `.command` launcher, and `install_to_applications.sh`.
  - **Windows:** Double-clickable `.bat` launchers and native Windows OpenFileDialog / FolderBrowserDialog via PowerShell.
  - **Pure Python:** Universal cross-platform CLI and web dashboard for any platform with Python 3.9+.
- **Remote Console File Browser**:
  - Browse, navigate folders, and inspect files directly on PS5 storage.
  - Back/Forward history navigation and 1-click remote path copying.
- **Security & Privacy**:
  - Binds strictly to `127.0.0.1` with per-session token authorization and Content Security Policy (CSP).
  - Passwords held only in process memory (never persisted to disk).

---

### Downloads & Installation

| Package | Recommended For | Instructions |
| :--- | :--- | :--- |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-macOS.zip`** | macOS users | Extract and double-click `PS5 Direct Streamer.app` or `Launch Direct Stream for PlayStation 5.command` |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-Windows.zip`** | Windows users | Extract and double-click `Launch Direct Stream for PlayStation 5.bat` |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-PurePython.zip`** | Linux / BSD / Developers | Extract and run `python3 ps5_streamer.py` |

*Requires Python 3.9 or later (from [python.org](https://www.python.org/downloads/)). No external pip packages needed.*
```

---

## 3. Terminal Commands to Push Initial Release

```bash
cd "/Users/kistapas/Downloads/PS5 Transfer 6"

# 1. Initialize git repo on branch main
git init -b main

# 2. Stage all files (temp/venv/dist files are automatically filtered by .gitignore)
git add .

# 3. Commit
git commit -m "feat: initial release of Direct Stream for PlayStation 5 v2.8.0"

# 4. Link to your new GitHub repository and push
git remote add origin https://github.com/<your-username>/<your-repo-name>.git
git push -u origin main
```
