# DIRECT STREAM FOR PLAYSTATION 5

A high-performance parallel streaming engine and local browser dashboard for streaming HTTP/HTTPS downloads directly into PlayStation 5 FTP storage, or uploading local computer files. Runs entirely locally on your machine with **zero cloud dependencies, zero pip packages, and no Node.js or npm required**.

<p align="center">
  <img src="assets/demo.gif" alt="Direct Stream for PlayStation 5 Studio Demo" width="100%" style="border-radius: 12px; box-shadow: 0 16px 40px rgba(0,0,0,0.5);">
</p>
<p align="center">
  <em>Real-time Gigabit LAN streaming (118+ MB/s), automatic benchmark tuning, and remote PS5 console file manager.</em><br>
  <small><a href="assets/demo.mp4">Download / View 1080p Master Video (assets/demo.mp4)</a></small>
</p>

---

## What is New in Version 2.9.0

Version 2.9.0 introduces major architectural upgrades, the redesigned **Interface 3.2**, native on-console archive extraction, and automated pre-flight URL verification:

```text
+-----------------------------------------------------------------------------------------+
|                              DIRECT STREAM PIPELINE (v2.9.0)                            |
+-----------------------------------------------------------------------------------------+
| [Source URL / File / Folder]                                                            |
|         |                                                                               |
|         v                                                                               |
| [Mandatory Pre-Flight Probe]  --> DNS & Reachability | Range Headers | Password Hints   |
|         |                                                                               |
|         v                                                                               |
| [3-Card Strategy Selector]    --> (1) Extract on PS5 (0 GB local disk, unrar payload)   |
|                                   (2) Extract on this device (RAM ring or local staging)|
|                                   (3) Send without extracting (raw archive storage)     |
|         |                                                                               |
|         v                                                                               |
| [High-Speed Gigabit LAN Stream] -> PS5 Internal SSD (/data/homebrew, /data/pkg)         |
+-----------------------------------------------------------------------------------------+
```

### 1. Native PS5 Console Decompressor (0 GB Local Disk Usage)
- Unpack compressed archives (`.rar`, `.zip`, `.7z`) directly on the console internal SSD using the bundled `unrar-ps5` payload on helper port `9021`.
- Raw archive streams straight from the internet or local disk directly into `/data/unrar` or `/data/homebrew` on the console without consuming local storage on your Mac, PC, or phone.
- Handles massive folder dumps with thousands of files and multipart split volumes (`.part1.rar`, `.part2.rar`).

### 2. Mandatory Pre-Flight Link Verification
- Clicking **Add to queue** automatically probes URLs for server reachability, `Content-Range` chunk support, accurate file size, and archive encryption.
- **Fail-Fast Protection**: Dead or expired links (`404`, connection refused) are caught inside the modal with clear error diagnostics, completely preventing broken jobs from entering the queue.
- If already verified manually, the transfer queues immediately with zero delay.

### 3. Interface 3.2 Experience Redesign
- Redesigned visual hierarchy, refined glassmorphic cards, and responsive touch layouts for mobile and desktop screens.
- **3-Card Archive Handling Selector**: Visual strategy cards with live indicators for `Extract on PS5`, `Extract on this device`, and `Send without extracting`.
- Prominent file size badges, detected archive types, and direct destination previews.

### 4. Smart Destination Routing & Homebrew Default
- Destination defaults to **`/data/homebrew`** across all queue jobs, configuration settings, and console payloads.
- **Auto-Preset Routing**:
  - Raw filesystem images (`.ffpfsc`, `.exfat`, `.ufs`, `.iso`) route to `/data/ShadowMount`.
  - Game package files (`.pkg`) route to `/data/pkg`.
  - Homebrew and extracted games route to `/data/homebrew`.

### 5. Encrypted & Password-Protected Archives
- Built-in detection of password-protected archives with automatic password hint extraction from file naming conventions.
- Dedicated modal password field with auto-staging and self-cleaning temporary directories.

### 6. Interactive Error Diagnostics & One-Click GitHub Reporting
- Comprehensive diagnostic modal with sanitized transfer logs, network status, and actionable recommendations.
- One-click clipboard copy and pre-formatted GitHub issue links for rapid community support.

---

## Archive Extraction Strategies

| Strategy | Local Disk Usage | Console Payload | Best Suited For |
| :--- | :--- | :--- | :--- |
| **Extract on PS5 (Recommended)** | **0 GB (Zero disk writes)** | `unrar-ps5` (Port 9021) | Multi-gigabyte folder dumps, split RAR/ZIP sets, games with thousands of files |
| **Extract on this device** | **0 GB for single files** (RAM ring)<br>Temporary staging for folders | None required | Single `.pkg`, `.ffpfsc`, `.exfat`, or standalone files; machines with fast CPU |
| **Send without extracting** | **0 GB** | None required | Archival backups, manual console management, raw storage |

---

## Highlights

- **Zero-Copy Direct Streaming**: Download HTTP/HTTPS web links straight into PS5 storage over high-speed LAN without staging gigabytes onto your computer disk.
- **Smart Link Resolver**: Paste file sharing landing pages from MediaFire, PixelDrain, Google Drive, Archive.org, or GoFile; the engine automatically extracts and streams the true underlying CDN download link.
- **Pure Python 3.9+ Standard Library**: No third-party runtime dependencies. Uses Python native `http.client`, `ftplib`, and `asyncio`/threading.
- **Fine-Tuned Parallel Multi-Stream Engine**: Configurable 1–32 concurrent HTTP range workers, dynamic RAM ring buffer (32 MiB to 1024 MiB), and segment chunk sizing (2 MiB to 32 MiB).
- **Speed Diagnostics & Automated Benchmark**: Built-in benchmark engine offering **Quick Scan (~10s)** and **Full Test (~60s Multi-Tier Matrix)** to automatically determine and apply the optimal throughput configuration for your specific network.
- **Local File Transfers**: Native file picker or path input for swift direct uploads of local files and directories.
- **Remote Console File Browser**: Browse, inspect, and navigate folders directly on the console FTP storage.
- **Resilience & Verification**: Automatic reconnects with exponential backoff, safe partial file handling (`.ps5part`), strict Content-Range verification, and remote file size confirmation before finalizing.
- **Enterprise-Grade Local Security**: Localhost binding only (`127.0.0.1`), per-session token authorization, strict Content Security Policy (CSP), and zero analytics or tracking.

---

## Quick Start

### macOS & Windows

1. Ensure **Python 3.9 or later** is installed.
2. Double-click **`Launch PS5 Streamer.command`** (macOS) or **`Launch Direct Stream for PlayStation 5.bat`** (Windows).  
   *(Alternatively, run `python3 ps5_streamer.py` directly from terminal).*
3. The dashboard will automatically open in your default browser at `http://127.0.0.1:<port>/#session=...`.
4. Open **Settings**, configure your PS5 IP address and FTP port (default: 2121), and click **Test connection**.
5. Click **New transfer**, paste your download URLs or choose a local file, and start streaming!

> [!NOTE]
> On macOS, if Gatekeeper alerts you on first open, follow the standard confirmation ("Open Anyway"). If execution permissions are needed, run `chmod +x launch.sh "Launch PS5 Streamer.command"`.

---

### Android (via Termux)

Direct Stream runs natively on Android phones with raw Wi-Fi throughput and zero root required:

#### 1-Line Quick Setup (Recommended)
Paste this single command into Termux to install prerequisites, download the app, and launch immediately:

```bash
curl -sSL https://raw.githubusercontent.com/ChillQuant/direct-stream-ps5/main/install_android.sh | bash
```

#### Everyday Launch (Fastest)
Once installed, simply open Termux and type:
```bash
ps5
```

#### 1-Tap Home Screen Launcher (No Terminal)
- **Chrome PWA**: When the dashboard opens in Chrome on your phone, tap the menu (3 dots) -> **"Add to Home screen"** / **"Install app"**. Tapping the icon on your home screen launches the app in standalone mode.
- **Termux:Widget**: If you have the free [Termux:Widget](https://f-droid.org/en/packages/com.termux.widget/) add-on installed, add the **"PS5_Streamer"** widget to your home screen to launch with 1 tap.

#### Manual Setup
```bash
# 1. Update packages & install python + git
pkg update -y && pkg install -y python git

# 2. Clone the repository
git clone https://github.com/ChillQuant/direct-stream-ps5.git
cd direct-stream-ps5

# 3. Start the streamer (auto-registers 'ps5' global command)
./run_android.sh
```

- **Background WakeLock**: Automatically enables Android WakeLock so your phone will not sleep or throttle Wi-Fi during 50GB+ transfers.
- **Remote Access (`--host 0.0.0.0`)**: Pass `--host 0.0.0.0` (or `ps5 --host 0.0.0.0`) if you want to control the Android stream server from your PC, Mac, or tablet on the same Wi-Fi.
- **Phone Storage Access**: Run `termux-setup-storage` to stream local packages directly from `/sdcard/Download/`.

#### Android Permissions (One-Time Setup)
1. **Storage / File Access**: Tap **Allow** when prompted so Direct Stream can access and stream local packages from your phone `Downloads` folder.
2. **Energy / Battery Optimization**: Tap **Allow** (or choose **Unrestricted** under *Android Settings -> Apps -> Termux -> Battery*). This prevents Android from killing background Wi-Fi streaming when the screen locks.

---

## Performance Presets & Diagnostics

Under **Settings -> Transfer engine**, choose from calibrated quick-presets or run the benchmark:

| Preset | Streams | Chunk Size | RAM Buffer | Intended Connection |
| :--- | :--- | :--- | :--- | :--- |
| **Conservative** | 4 streams | 4 MiB | 64 MiB | Wi-Fi / Standard Broadband |
| **Balanced** | 8 streams | 8 MiB | 128 MiB | Default Fiber / Gigabit LAN |
| **Turbo** | 16 streams | 8 MiB | 256 MiB | High-Speed Fiber / Fast LAN |
| **Max Saturation** | 16 streams | 32 MiB | 512 MiB | Gigabit Ethernet Full Saturation |

### Automated Speed Benchmark
In **Speed diagnostics**, input a test download URL and run:
- **Quick Scan (~10s)**: Evaluates Conservative, Balanced, and Gigabit Turbo tiers.
- **Full Test (~60s)**: Evaluates an 8-tier matrix across varying worker streams, chunk slice sizes, and memory cushions.
- Click **Apply Recommended Settings** to immediately update your engine configuration to the winning profile.

---

## Transfer Behavior & Integrity

- **Safe Partials (`.ps5part`)**: Transfers write to a uniquely tagged `filename.<job-id>.ps5part`. Only after transmission finishes and the remote size matches the source byte-for-byte does the engine issue an FTP rename (`RNFR`/`RNTO`) to the final name.
- **Safe Resumes**: Resuming inspects the remote partial length, checks strong HTTP validators (`ETag`, `Last-Modified`, or file fingerprint), and continues from the verified byte offset.
- **Existing File Protection**: Overwriting existing destination files is blocked by default unless explicitly toggled on per transfer.
- **Backpressure Pipeline**: The memory ring buffer ensures incoming HTTP chunks never exceed available RAM. If the FTP receiver is slower than the source, downstream backpressure pauses HTTP reads to prevent buffer overruns.

---

## Security & Privacy

- **100% Local**: Binds only to loopback (`127.0.0.1`) on a dynamic ephemeral port.
- **Session Authentication**: Every API request requires a cryptographically random session token generated on startup.
- **No Password Persistence**: FTP passwords are held only in process memory during runtime and are never written to disk.
- **Configuration Storage**: User settings and queue state are saved to `~/.ps5-transfer/state.json` with strict owner-only permissions (`0600`).

---

## Development & Testing

The application requires only Python standard library for production execution. Testing utilizes `pyftpdlib` for local FTP fixtures:

```bash
# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install test dependencies
pip install -r requirements-dev.txt

# Run integration tests (100 tests covering auth, range checks, retries, resume, and archive extraction)
python -m unittest discover -s tests -v

# Sync changes to the Mac application bundle
python3 build_bundle.py

# Package release distributions (macOS, Windows, Pure Python)
python3 package_releases.py
```

---

## License

This project is licensed under the [MIT License](LICENSE).
