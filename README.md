# DIRECT STREAM FOR PLAYSTATION 5

> High-performance parallel streaming engine and web dashboard for streaming HTTP/HTTPS downloads directly into PlayStation 5 FTP storage, or uploading local files from macOS, Windows, and Android. Built with pure Python standard library: zero cloud dependencies, zero pip packages, and no Node.js or npm required.

<p align="center">
  <img src="assets/demo.gif" alt="Direct Stream for PlayStation 5 Studio Demo" width="100%" style="border-radius: 12px; box-shadow: 0 16px 40px rgba(0,0,0,0.5);">
</p>
<p align="center">
  <em>Real-time Gigabit LAN streaming (118+ MB/s), automatic benchmark tuning, and remote PS5 console file manager.</em><br>
  <small><a href="assets/demo.mp4">Download / View 1080p Master Video (assets/demo.mp4)</a></small>
</p>

---

## Quick Navigation

- [Why Direct Stream?](#why-direct-stream)
- [Architecture & Data Flow](#architecture--data-flow)
- [What is New in v2.9.0](#what-is-new-in-v290)
- [Core Features](#core-features)
- [Supported Hosting Providers](#supported-hosting-providers)
- [Archive Extraction Strategies](#archive-extraction-strategies)
- [Smart Destination Routing](#smart-destination-routing)
- [Quick Start Guide](#quick-start-guide)
  - [macOS](#macos)
  - [Windows](#windows)
  - [Android (via Termux)](#android-via-termux)
  - [Linux / Headless Server](#linux--headless-server)
- [Performance Presets & Diagnostics](#performance-presets--diagnostics)
- [Transfer Resilience & Integrity](#transfer-resilience--integrity)
- [Frequently Asked Questions (FAQ)](#frequently-asked-questions-faq)
- [Development & Test Suite](#development--test-suite)
- [License](#license)

---

## Why Direct Stream?

Traditional methods of installing games and homebrew on PlayStation 5 require multiple wasteful read/write cycles and gigabytes of free disk space on your computer:

| Task Stage | Traditional Transfer Method | Direct Stream for PlayStation 5 |
| :--- | :--- | :--- |
| **Download Stage** | Saves 50GB–100GB to your computer SSD | Streams directly into RAM ring buffer (0 GB local disk) |
| **Unpack Stage** | Extracts 50GB–100GB onto local computer drive | Unpacks on console via native `unrar-ps5` payload (0 GB local disk) |
| **Local Disk Wear** | **100GB to 200GB written to your SSD** | **0 bytes written to your local drive** |
| **Transfer Stage** | Manual FTP upload after hours of local extraction | Direct, simultaneous internet-to-PS5 stream over Gigabit LAN |
| **Overall Time** | Download time + Unpack time + Upload time | **Single-pass network throughput at LAN wire speed** |

---

## Architecture & Data Flow

```text
+---------------------------------------------------------------------------------------------------------+
|                                    DIRECT STREAM FOR PLAYSTATION 5                                      |
+---------------------------------------------------------------------------------------------------------+
|                                                                                                         |
|   [WEB SOURCES]                  [LOCAL STORAGE]                                                        |
|   MediaFire, PixelDrain,         Local .pkg packages,                                                   |
|   Google Drive, Archive.org,     disc dumps, or folder sets                                             |
|   Direct HTTP/HTTPS URLs         from Mac / Windows / Phone                                             |
|              \                          /                                                               |
|               \                        /                                                                |
|                v                      v                                                                 |
|        +----------------------------------------------+                                                 |
|        |        MANDATORY PRE-FLIGHT VERIFIER         |                                                 |
|        |  Checks DNS, Range headers, size & passwords |                                                 |
|        +----------------------------------------------+                                                 |
|                               |                                                                         |
|                               v                                                                         |
|        +----------------------------------------------+                                                 |
|        |        DYNAMIC RAM RING BUFFER ENGINE        |                                                 |
|        |     Zero disk staging · Backpressure sync    |                                                 |
|        +----------------------------------------------+                                                 |
|                               |                                                                         |
|               118+ MB/s Gigabit LAN Parallel FTP                                                        |
|                               |                                                                         |
|                               v                                                                         |
|        +----------------------------------------------------------------+                               |
|        |                        PLAYSTATION 5                           |                               |
|        |  /data/homebrew  <-- Direct Stream / unrar-ps5 console payload  |                               |
|        |  /data/pkg       <-- Package files (.pkg)                      |                               |
|        |  /data/ShadowMount <-- Disc images (.ffpfsc, .exfat, .ufs)     |                               |
|        +----------------------------------------------------------------+                               |
+---------------------------------------------------------------------------------------------------------+
```

---

## What is New in v2.9.0

Version 2.9.0 is a major milestone release that elevates performance, reliability, and user interface aesthetics:

1. **Native PS5 Console Decompressor (0 GB Local Disk Usage)**:
   - Stream compressed `.rar`, `.zip`, and `.7z` archives straight into PS5 storage.
   - Triggers the bundled `unrar_ps5.elf` payload via TCP helper port `9021` to extract archives on the console internal SSD with zero computer disk wear.
2. **Mandatory Pre-Flight Link Verification**:
   - Clicking **Add to queue** automatically probes URLs for server reachability, `Content-Range` chunk support, accurate file size, and archive encryption.
   - Stops broken, expired, or 404 links before they enter the queue.
3. **Interface 3.2 Visual Experience**:
   - Modernized visual hierarchy with an interactive **3-Card Archive Strategy Selector**.
   - Fully responsive layout for desktop browsers and mobile screens.
4. **Smart Destination Routing & `/data/homebrew` Default**:
   - Default destination updated to `/data/homebrew`.
   - Automatic routing of filesystem dumps (`.ffpfsc`, `.exfat`, `.ufs`) to `/data/ShadowMount` and packages to `/data/pkg`.
5. **Encrypted Archive Support**:
   - Automatic extraction of password hints from file names with dedicated password input and secure staging.
6. **Interactive Error Diagnostics & One-Click GitHub Reporting**:
   - In-app error receipt inspector with sanitized system info, troubleshooting recommendations, and pre-formatted bug reporting links.

---

## Core Features

- **Zero-Copy Direct Streaming**: Download HTTP/HTTPS web links directly into PS5 storage over high-speed LAN without writing gigabytes to your local drive.
- **Pure Python Standard Library**: 100% self-contained Python 3.9+ runtime with zero external pip packages or npm/Node.js dependencies.
- **Multi-Stream Parallel Engine**: Configurable 1 to 32 concurrent HTTP range workers with backpressure flow control to saturate Gigabit LAN connections.
- **Dynamic RAM Ring Buffer**: Configurable buffer sizing (32 MiB to 1024 MiB) keeps transfers smooth while absorbing network jitter.
- **Automated Speed Diagnostics**: Built-in benchmark suite (Quick Scan and Full Matrix) tests your connection and automatically applies the optimal profile.
- **Multi-Part Archive Stitching**: Automatically detects, sequences, and stitches multi-part split archives (`.part1.rar`, `.001`) into unified console packages.
- **Remote PS5 Console File Browser**: Browse, inspect, and navigate folders directly on the console internal and external storage.
- **Enterprise-Grade Local Security**: Binds strictly to localhost (`127.0.0.1`), enforces per-session token authorization, and saves settings with owner-only permissions (`0600`).

---

## Supported Hosting Providers

The built-in Smart Link Resolver automatically converts file sharing landing pages into direct CDN download streams:

| Provider | Supported Formats | Automatic Behavior |
| :--- | :--- | :--- |
| **MediaFire** | Standard sharing links | Automatically extracts direct download endpoint |
| **PixelDrain** | `pixeldrain.com/u/<id>` | Resolves to high-speed raw API stream |
| **Google Drive** | Sharing & export links | Bypasses large-file virus scan confirmation screens |
| **Internet Archive** | `archive.org/details/<item>` | Resolves to full-speed direct file downloads |
| **GoFile** | Sharing pages | Pre-resolves to direct content stream |
| **1fichier** | Direct access links | Handles direct inline CDN downloads |
| **BuzzHeavier / Send.cm** | Sharing links | Extracts underlying download stream |
| **Direct HTTP / HTTPS** | Any direct web link | Full parallel range chunking with multi-stream workers |

---

## Archive Extraction Strategies

When adding an archive (`.zip`, `.rar`, `.7z`), choose from three tailored workflows:

| Strategy Card | Local Disk Usage | Console Payload | Best Suited For |
| :--- | :--- | :--- | :--- |
| **Extract on PS5 (Recommended)** | **0 GB (Zero disk writes)** | `unrar-ps5` (Port 9021) | Multi-gigabyte folder dumps, split RAR/ZIP sets, games with thousands of files |
| **Extract on this device** | **0 GB for single files** (RAM ring)<br>Temporary staging for folders | None required | Standalone files (`.pkg`, `.ffpfsc`, `.exfat`) streaming through RAM; fast CPUs |
| **Send without extracting** | **0 GB** | None required | Archival raw backups, manual on-console management |

---

## Smart Destination Routing

Direct Stream automatically routes files to their optimal destination folder based on file extension:

| File Type / Extension | Destination Folder | Purpose on PlayStation 5 |
| :--- | :--- | :--- |
| **Homebrew & Unpacked Games** | `/data/homebrew` | Default homebrew application and game directory |
| **Package Files (`.pkg`)** | `/data/pkg` | Direct package installation staging |
| **Disc Images (`.ffpfsc`, `.exfat`, `.ufs`, `.iso`)** | `/data/ShadowMount` | Direct raw disc mounting via ShadowMount |
| **Raw Archives (PS5 Unpack)** | `/data/unrar` or `/data/homebrew` | Staging for native `unrar-ps5` console payload |

---

## Quick Start Guide

### macOS

1. Ensure **Python 3.9 or later** is installed.
2. Double-click **`Launch PS5 Streamer.command`** or open **`PS5 Direct Streamer.app`**.
3. The dashboard opens automatically in your browser at `http://127.0.0.1:<port>/#session=...`.
4. Open **Settings**, enter your PS5 IP address and FTP port (default: `2121`), and click **Test connection**.
5. Click **New transfer**, paste your download link, and start streaming!

> [!NOTE]
> If macOS Gatekeeper displays an alert on first open, follow standard confirmation (*Right-click -> Open* or *System Settings -> Privacy & Security -> Open Anyway*).

---

### Windows

1. Ensure **Python 3.9 or later** is installed from [python.org](https://www.python.org/downloads/) *(check "Add Python to PATH" during installation)*.
2. Double-click **`Launch Direct Stream for PlayStation 5.bat`**.
3. The dashboard opens automatically in your default browser.
4. Open **Settings**, configure your PS5 IP address, and click **Test connection**.

---

### Android (via Termux)

Run Direct Stream natively on Android phones with raw Wi-Fi throughput and zero root required:

#### 1-Line Universal Setup (Recommended)
Paste this single command into Termux to install prerequisites, download the app, and launch immediately:

```bash
curl -sSL https://raw.githubusercontent.com/ChillQuant/direct-stream-ps5/main/install_android.sh | bash
```

#### Everyday Launch
Once installed, open Termux and type:
```bash
ps5
```

#### Remote Control from PC or Tablet
If you want to control the Android stream server from your computer or tablet on the same Wi-Fi:
```bash
ps5 --host 0.0.0.0
```

#### Android System Permissions
- **Storage Access**: Tap **Allow** when prompted so Direct Stream can access packages in `/sdcard/Download/`.
- **Battery Optimization**: Choose **Unrestricted** under *Android Settings -> Apps -> Termux -> Battery* to prevent Android from killing background transfers when your screen locks.

---

### Linux / Headless Server

```bash
# Clone the repository
git clone https://github.com/ChillQuant/direct-stream-ps5.git
cd direct-stream-ps5

# Launch the server (opens default browser or prints console URL)
python3 ps5_streamer.py

# Or launch for LAN access from another device
python3 ps5_streamer.py --host 0.0.0.0
```

---

## Performance Presets & Diagnostics

Under **Settings -> Transfer engine**, select from calibrated quick-presets or run the benchmark:

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

## Transfer Resilience & Integrity

- **Safe Partials (`.ps5part`)**: Transfers write to a uniquely tagged `filename.<job-id>.ps5part`. Only after transmission completes and the remote size matches the source byte-for-byte does the engine issue an FTP rename (`RNFR`/`RNTO`) to finalize the file.
- **Safe Resumes**: Resuming inspects the remote partial length, verifies HTTP validators (`ETag`, `Last-Modified`, or content fingerprint), and resumes seamlessly from the verified byte offset.
- **Existing File Protection**: Overwriting existing console files is blocked by default unless explicitly toggled on per transfer.
- **Downstream Backpressure**: The memory ring buffer ensures incoming HTTP chunks never exceed available RAM. If the console FTP receiver is slower than the internet connection, reads pause gently to prevent buffer overruns.

---

## Frequently Asked Questions (FAQ)

#### Does Direct Stream require jailbreak or root?
On the console side, your PS5 must be running an FTP server (such as ftps5, LightningMods FTP, GoldHEN FTP, or itemzflow). On your computer or Android phone, **no root or administrator access is required**.

#### Does streaming a 60GB game wear out my computer SSD?
No. When using direct streaming or the `Extract on PS5` strategy, bytes flow directly from the incoming network socket into process memory (RAM) and immediately out to the PS5 over LAN. Zero bytes are written to your computer disk.

#### How does the PS5 console decompressor work?
The bundled `unrar_ps5.elf` payload is sent to port `9021` on the console. It natively unpacks RAR, ZIP, and 7Z archives directly on the console internal SSD, avoiding the need to stage folder dumps on your local computer.

#### What happens if my network drops during a 100GB transfer?
The engine features automatic reconnects with exponential backoff. If interrupted, simply click **Resume**; the engine validates the existing `.ps5part` file on the console and resumes from the exact byte where it left off.

---

## Development & Test Suite

Direct Stream requires only the Python standard library for production execution. The test suite uses `pyftpdlib` for local FTP mock fixtures:

```bash
# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install test dependencies
pip install -r requirements-dev.txt

# Run integration test suite (100 tests covering auth, ranges, retries, resume, and archives)
python -m unittest discover -s tests -v

# Sync changes to native macOS application bundle
python3 build_bundle.py

# Package release distributions (macOS, Windows, Pure Python)
python3 package_releases.py
```

---

## License

This project is licensed under the [MIT License](LICENSE).
