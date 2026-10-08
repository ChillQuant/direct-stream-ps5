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

## 2. GitHub Release Notes (v2.9.0 — Console Decompressor & Interface 3.2)

### Tag Version
```text
v2.9.0
```

### Release Title
```text
DIRECT STREAM FOR PLAYSTATION 5 v2.9.0 — Console Decompressor & Interface 3.2
```

### Release Description:
```markdown
## DIRECT STREAM FOR PLAYSTATION 5 (v2.9.0)

Direct Stream for PlayStation 5 v2.9.0 is a major milestone release introducing **native PS5 console decompressor integration (0 GB local disk usage)**, **mandatory pre-flight URL verification**, the redesigned **Interface 3.2 experience**, and default destination routing to **`/data/homebrew`**.

---

### Highlights at a Glance

- **Native PS5 Console Decompressor**: Stream `.rar`, `.zip`, and `.7z` archives directly into PS5 storage and unpack on the console internal SSD using the bundled `unrar-ps5` payload on port 9021 with **0 GB local disk space used**.
- **Mandatory Pre-Flight Link Verification**: Automatically probes URLs on queue submission for DNS reachability, range support, file sizes, and archive encryption to stop broken transfers before they start.
- **Interface 3.2 Experience Redesign**: Refined UI typography, responsive touch controls, and an interactive 3-card Archive Strategy selector (`Extract on PS5`, `Extract on this device`, `Send without extracting`).
- **Default `/data/homebrew` Routing**: Presets, payloads, and queues now default to `/data/homebrew`, with smart automatic redirection of disk dumps (`.ffpfsc`, `.exfat`, `.ufs`) to `/data/ShadowMount` and packages to `/data/pkg`.
- **Encrypted & Password-Protected Archives**: Automatic detection of encrypted archives, filename password hint extraction, and secure temporary staging.
- **Interactive Error Diagnostics**: In-app error receipt inspector with sanitized system info, troubleshooting recommendations, and one-click GitHub issue reporting.

---

### What is New in Detail

#### 1. Native PS5 Console Decompressor (0 GB Local Disk Footprint)
- Sends compressed archives directly across your LAN to `/data/unrar` or `/data/homebrew` on the console.
- Launches the bundled `unrar_ps5.elf` payload via TCP port 9021 to perform native on-console extraction directly into `/data/homebrew`.
- Ideal for massive multi-gigabyte game dumps containing hundreds of subdirectories and thousands of loose files, or multi-volume `.part1.rar` sequences.
- Completely avoids filling up your Mac, PC, or Android phone with temporary staging files.

#### 2. Mandatory Pre-Flight URL Verification & Fail-Fast Protection
- When clicking **Add to queue**, Direct Stream automatically verifies each link in the background:
  - Validates HTTP server reachability and DNS resolution.
  - Probes `Accept-Ranges` and `Content-Range` chunk capabilities.
  - Extracts exact remote content length and true filenames from `Content-Disposition`.
  - Inspects archive headers to detect compression format and encryption status.
- **Fail-Fast Safety**: Unreachable links (404, DNS error, server rejected) are flagged immediately inside the modal with a red diagnostic card, keeping the transfer queue clean and reliable.

#### 3. Archive Handling Strategy Matrix

| Strategy Card | Local Disk Footprint | Console Payload | Best Suited For |
| :--- | :--- | :--- | :--- |
| **Extract on PS5 (Recommended)** | **0 GB (Zero local disk writes)** | `unrar-ps5` (Port 9021) | Game folder dumps with tons of loose files, split multi-part RAR/ZIP sets |
| **Extract on this device** | **0 GB for single files** (RAM ring)<br>Temporary staging for folders | None required | Standalone files (`.pkg`, `.ffpfsc`, `.iso`) streaming through RAM; fast CPUs |
| **Send without extracting** | **0 GB** | None required | Archival raw storage, manual on-console management |

#### 4. Smart Destination Preset Routing
- Destination directory defaults to **`/data/homebrew`**.
- Intelligent automatic preset detection:
  - Disc image dumps (`.ffpfsc`, `.exfat`, `.ufs`, `.iso`, `.bin`, `.img`) auto-route to **`/data/ShadowMount`**.
  - Package installers (`.pkg`) auto-route to **`/data/pkg`**.
  - General games and unpacked folders route to **`/data/homebrew`**.

#### 5. Interactive Error Diagnostics & One-Click GitHub Reporting
- When a network or transfer error occurs, click **Inspect Diagnostics** for an instant breakdown.
- Displays sanitized network information, remote FTP server state, and concrete recovery steps.
- **Report to GitHub** button pre-fills a professional bug report using our official repository issue templates.

---

### Downloads & Installation

| Package | Target Platform | Instructions |
| :--- | :--- | :--- |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-macOS.zip`** | macOS (Apple Silicon & Intel) | Extract and open `PS5 Direct Streamer.app` |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-Windows.zip`** | Windows 10 / 11 | Extract and run `Launch Direct Stream for PlayStation 5.bat` |
| **`DIRECT-STREAM-FOR-PLAYSTATION-5-PurePython.zip`** | Android / Linux / Cross-Platform | Includes `install_android.sh`, `run_android.sh`, and pure Python CLI |

#### Android 1-Line Setup (via Termux):
```bash
curl -sSL https://raw.githubusercontent.com/ChillQuant/direct-stream-ps5/main/install_android.sh | bash
```

---

### Technical Invariants & Quality Standards

- **100% Native Pure Python**: Pure Python 3.9+ standard library (`http.client`, `ftplib`, `asyncio`, `threading`). Zero third-party pip dependencies required.
- **Strict Zero-Emoji Design**: All code, terminal outputs, UI labels, and documentation conform to professional clean text standards.
- **Automated Test Suite**: 100 integration and unit tests covering range chunking, FTP socket handling, atomic partial renames, and archive decompression.
```

---

## 3. GitHub Release Notes (v2.8.6 — Smart Direct Link Resolver)

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

## 4. GitHub Release Notes (v2.9.0 — The Ultimate Direct Stream Release)

### Tag Version
```text
v2.9.0
```

### Release Title
```text
DIRECT STREAM FOR PLAYSTATION 5 v2.9.0 — Folders, Archive Streaming & Multi-Part Stitcher
```

### Release Description:
```markdown
## DIRECT STREAM FOR PLAYSTATION 5 (v2.9.0)

The largest release yet! Stream compressed `.zip` archives, recursive game directories, split multi-part packages, and direct cloud links straight into PlayStation 5 storage with **Zero Local Disk Overhead** and an authentic, emoji-free studio dashboard.

---

### What's New in v2.9.0

- **Full Game Folder Uploads**:
  - Stream complete game directory hierarchies directly to `/data/ShadowMount/<GameFolder>` or custom paths over high-speed FTP.
  - Recursively creates destination directories on the PS5.
  - Preserves exact directory structure, skips already-verified files on resume, and automatically maintains RFC binary mode integrity.
  - Native file & folder pickers on macOS and Windows automatically foregrounded.

- **On-The-Fly Streaming Archive Decompression**:
  - Extract `.zip` (Deflate & Stored) directly to PS5 FTP storage in real-time in RAM.
  - **0 GB Local Disk Used**: Stream a 70 GB zipped game from a phone or laptop with only 2 GB of free storage.
  - Remote central directory probing in ~100 ms via HTTP Range requests (~64 KB).
  - Accurate PS5 storage pre-check against uncompressed file size.

- **Multi-Part File Stitching Pipeline**:
  - Automatically detects, sorts, and stitches split multi-part files (`.001`, `.002`, `.part1`, etc.) into a unified single package directly on the PS5.
  - 3D Multi-Part Sequence Inspector in the UI with live stacked preview.

- **Smart Direct Download Link Resolvers**:
  - Direct streaming from 11 popular cloud hosts: MediaFire, PixelDrain, Google Drive, Archive.org, GoFile, AkiraBox, Rootz, DataNodes, VikingFile, and FileDitch.
  - Pre-flight link verifier in the Add Transfer dialog checks reachability, file size, headers, and range support before queueing.

- **Authentic PlayStation Studio UI Overhaul**:
  - Complete zero-emoji purge across all dashboard dialogs, cards, toasts, and status bars.
  - Real PlayStation game cards with official system badges (`PS5`, `PKG`, `PFS`, `exFAT`, `DIR`, `ARCHIVE`).
  - Redesigned Diagnostics benchmark comparison matrix with real-time latency and winner badges.

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
  - **1-Line Universal Installer**: Set up everything in one copy-paste command:
    ```bash
    curl -sSL https://raw.githubusercontent.com/ChillQuant/direct-stream-ps5/main/install_android.sh | bash
    ```
  - **Global `ps5` Command**: Automatically registered in Termux so you can launch by just typing `ps5` anywhere.
  - **Background WakeLock (`termux-wake-lock`)**: Prevents Android from throttling Wi-Fi or putting Termux to sleep when the screen locks during 50GB–100GB transfers.
  - **Local Phone Storage (`termux-setup-storage`)**: Stream `.pkg` game packages directly from your phone's `/sdcard/Download/` folder.
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
