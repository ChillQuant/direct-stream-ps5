# DIRECT STREAM FOR PLAYSTATION 5

> High-performance parallel streaming engine and web dashboard for streaming HTTP/HTTPS downloads directly into PlayStation 5 FTP storage, or uploading local files from macOS, Windows, and Android. Built with pure Python standard library: zero cloud dependencies, zero pip packages, and no Node.js or npm required.

<p align="center">
  <img src="assets/demo.gif" alt="Direct Stream for PlayStation 5 Studio Demo" width="100%" style="border-radius: 12px; box-shadow: 0 16px 40px rgba(0,0,0,0.5);">
</p>
<p align="center">
  <em>Real-time Gigabit LAN streaming (114+ MB/s), pre-flight link verification, zero-disk archive extraction, automated health diagnostics, and remote PS5 console file manager.</em><br>
  <small><a href="assets/demo.mp4">Download / View 1080p Master Video (assets/demo.mp4)</a></small>
</p>

---

## Quick Navigation

- [Why Direct Stream?](#why-direct-stream)
- [Architecture & Data Flow](#architecture--data-flow)
- [What is New in v2.9.1](#what-is-new-in-v291)
- [How to Capture Direct Download Links](#how-to-capture-direct-download-links)
- [Live Hardware Verification Matrix](#live-hardware-verification-matrix)
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
- [Legal Disclaimer & Statutory Compliance](#legal-disclaimer--statutory-compliance)
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

## What is New in v2.9.1

Version 2.9.1 delivers complete hardware-verified validation, direct link streaming enhancements, and full ecosystem alignment:

1. **Exhaustive Live Hardware Verification (32/32 Tests Passed)**:
   - Comprehensive matrix executed directly on physical PlayStation 5 hardware (`192.168.1.188:2121` FTP and port `9021` decompressor payload).
   - Validated single and multi-part archives, password encryption (`DLPSGAME.COM`), RAM extraction, PS5 console decompression, on-the-fly multi-part PKG stitching, pause and resume via FTP `REST`, bandwidth throttling, collision guards, and verified **0 bytes leaked on console storage**.
2. **Universal Direct Download Link Capture Engine**:
   - Integrated guidance and protocol detection distinguishing direct binary octet-streams from web landing pages.
   - Built-in link verification with automatic resolution for major cloud hosting services (AkiraBox, Rootz, DataNodes, VikingFile, FileDitch, Google Drive, MediaFire, PixelDrain).
3. **Interface 3.2 Polish & Diagnostic Version Alignment**:
   - Integrated quick-reference guide modal and collapsible in-app link capture instructions.
   - Exact synchronization across Python core, macOS `.app` bundle, web dashboard, and diagnostic export bundles.

---

## How to Capture Direct Download Links

Direct Stream for PlayStation 5 works by opening a direct, high-speed HTTP/HTTPS binary socket and piping the data immediately across your local network into the console FTP server. To make this work seamlessly, the application requires a **direct binary download link**, not an HTML webpage URL.

### Understanding Direct Links vs Web Landing Pages

- **Web Landing Page (Will Not Work Directly)**: A URL like `https://filesharing.com/file/12345` loads an HTML webpage with JavaScript, countdown timers, captcha security challenges, or advertisements. A console FTP server cannot interpret HTML code as a game package.
- **Direct Download Link (Required)**: A URL pointing directly to the raw binary file stream (e.g. `https://cdn.filesharing.com/dl/package.pkg?token=abc...` or headers returning `Content-Type: application/octet-stream`).

### The 3-Step Universal Browser Link Capture Method

You can capture a direct download link from virtually any file hosting service using any standard web browser (Chrome, Edge, Firefox, or Safari):

1. **Start the Download in Your Browser**:
   - Visit the download page in your browser and click through the site buttons until the file actually begins downloading in your browser.
2. **Open Your Browser Downloads Manager**:
   - Press `Ctrl + J` on Windows/Linux, or `Cmd + Shift + J` (or `Cmd + Option + L` in Safari) on macOS to view your active downloads.
3. **Copy the Direct Stream Address**:
   - Right-click the downloading file in your browser's download list and click **Copy download link** (or **Copy link address**).
4. **Paste and Stream**:
   - Cancel or pause the download in your web browser so it does not consume your computer's internet bandwidth or SSD storage.
   - Paste the copied link into Direct Stream for PlayStation 5 and click **Verify link** or **Add to queue**.

### Browser Developer Tools Method (Advanced)

For hosting services that obfuscate the download link:

1. Open your browser Developer Tools by pressing `F12` (or `Cmd + Option + I` on macOS) and switch to the **Network** tab.
2. Filter by `Fetch/XHR` or `Media`, then click the site download button.
3. Look for the request that returns a large `Content-Length` or a `Content-Type` of `application/octet-stream`, `application/zip`, or `application/x-rar`.
4. Right-click that request and select **Copy -> Copy URL**, then paste it into Direct Stream.

### Built-In Auto-Resolvers

Direct Stream includes built-in resolvers for popular hosters. For the following services, you can paste the regular sharing URL directly and the application will extract the direct stream automatically:

- **MediaFire**: Standard sharing links
- **PixelDrain**: `pixeldrain.com/u/<id>`
- **Google Drive**: Sharing and file export URLs
- **Internet Archive**: `archive.org/details/<item>`
- **Rootz / AkiraBox / DataNodes / VikingFile / FileDitch**: Auto-resolved endpoints
- **Direct URLs**: Any direct link ending in `.pkg`, `.zip`, `.rar`, `.7z`, or split parts (`.001`, `.part1.rar`, `.z01`)

---

## Live Hardware Verification Matrix

The Direct Stream transfer engine was validated directly against physical PlayStation 5 hardware running etaHEN and ftps5. All 32 real-world testing scenarios passed with complete data integrity and zero leftover storage footprint:

| Category | Combinations Tested on PS5 Hardware | Result |
| :--- | :--- | :--- |
| **Direct Package Streaming** | Single `.pkg` URL streaming and local package delivery | Verified byte-for-byte |
| **Multi-Part PKG Stitching** | 3-part split PKG reassembly on-the-fly in RAM without local files | Verified unified PKG |
| **ZIP Archive Engine** | Single & multi-part, unencrypted & AES password-protected, in-RAM and PS5 unpack | 100% Passed (6 tests) |
| **7Z Archive Engine** | Single & multi-part, unencrypted & encrypted, streaming LZMA decompression | 100% Passed (5 tests) |
| **RAR Archive Engine** | Staged host extraction with folder tree and on-console `unrar-ps5.elf` payload | 100% Passed (4 tests) |
| **Automation & Intelligence** | Scene bracket password auto-discovery (`[DLPSGAME.COM]`), FTP keep-alives | Auto-discovered & verified |
| **Resilience & Safety** | Pause and resume with FTP `REST`, speed throttling, gap detection, wrong-password abort | Fully resilient |
| **Collision Protection** | Default existing file protection guard vs explicit overwrite replacement | Collision guarded |
| **Console Storage Hygiene** | Complete SSD space audit after running all test combinations | 0 bytes leaked |

For the complete 32-row hardware test ledger with exact byte payloads and commands, see [VALIDATION.md](VALIDATION.md).

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

## Legal Disclaimer & Statutory Compliance

Please read this section carefully before using or contributing to Direct Stream for PlayStation 5.

### Trademark Notice & Non-Affiliation
Direct Stream for PlayStation 5 is an independent, community-driven open-source software project. It is **not** endorsed by, certified by, partnered with, associated with, maintained by, or in any way officially connected to Sony Interactive Entertainment Inc. (SIE), Sony Group Corporation, or any of their parent companies, subsidiaries, or affiliates. 

"PlayStation", "PS5", "PS4", "DualSense", "PlayStation Studios", and all associated logos, device names, and brand marks are registered trademarks or service marks of Sony Interactive Entertainment Inc. All product names, logos, brands, and registered trademarks featured or referenced within this software or its documentation are property of their respective trademark holders. Their use in this documentation does not imply any affiliation with or endorsement by them.

### Content-Neutral Architecture & Non-Hosting Policy
Direct Stream for PlayStation 5 is strictly a content-neutral, protocol-level network transfer manager. The authors, contributors, and maintainers of this project:
- **Do not** host, mirror, store, cache, index, provide, or distribute any copyrighted video game files, commercial software packages, system update binaries, decryption keys, license tokens, or game disk images.
- **Do not** provide, link to, promote, or encourage websites that distribute pirated, unauthorized, or infringing digital content.
- **Do not** bypass, disable, circumvent, or defeat any digital rights management (DRM), anti-piracy protections, encryption schemes, or technological protection measures (TPMs).

### Lawful Use & Personal Backup Policy
This tool is distributed strictly for lawful and educational purposes, including:
1. Facilitating local network data transfers of legal, user-created homebrew applications, open-source development utilities, and public domain datasets.
2. Managing legitimate archival backups of legally acquired physical media or digital licenses owned personally by the user, in strict compliance with applicable statutory fair use provisions, personal backup exemptions, and local intellectual property laws.

### User Responsibility & Assumption of Risk
The end-user acknowledges and agrees that:
- It is solely the end-user's legal responsibility to verify that they possess all necessary licenses, permissions, and rights to transfer, store, and execute any software package, file, or disk image moved using this software.
- The user is solely responsible for compliance with all applicable municipal, state, national, and international copyright laws, computer security statutes, terms of service, and end-user license agreements (EULAs).
- Any use of this utility in violation of local laws, terms of service, or copyright acts (such as the DMCA) is strictly prohibited and outside the intended scope of this project.

### Warranty Disclaimer & Limitation of Liability
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS", AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT ARE EXPRESSLY DISCLAIMED. 

IN NO EVENT SHALL THE COPYRIGHT HOLDER, AUTHORS, MAINTAINERS, OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; HARDWARE CONSOLE BRICKING, SYSTEM INSTABILITY, BANNING OR RESTRICTIONS FROM NETWORK SERVICES, OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

---

## License

This project is licensed under the [MIT License](LICENSE).
