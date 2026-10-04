# DIRECT STREAM FOR PLAYSTATION 5

A high-performance parallel transfer engine and local browser dashboard for streaming HTTP/HTTPS downloads directly to PlayStation 5 FTP storage, or uploading local Mac files. Runs entirely locally on your Mac with **zero cloud dependencies, zero pip packages, and no Node.js or npm required**.

<p align="center">
  <img src="assets/demo.gif" alt="Direct Stream for PlayStation 5 Studio Demo" width="100%" style="border-radius: 12px; box-shadow: 0 16px 40px rgba(0,0,0,0.5);">
</p>
<p align="center">
  <em>⚡ Real-time Gigabit LAN streaming (118+ MB/s), automatic benchmark tuning, and remote PS5 console file manager.</em><br>
  <small><a href="assets/demo.mp4">▶ Download / View 1080p Master Video (assets/demo.mp4)</a></small>
</p>

---

## Highlights

- **Zero-Copy Direct Streaming**: Download HTTP/HTTPS web links straight into PS5 storage over high-speed LAN without staging gigabytes onto your Mac disk.
- **Smart Link Resolver**: Paste file sharing landing pages from MediaFire, PixelDrain, Google Drive, or Internet Archive; the engine automatically extracts and streams the true underlying CDN download link.
- **Pure Python 3.9+ Standard Library**: No third-party runtime dependencies. Uses Python's native `http.client`, `ftplib`, and `asyncio`/threading.
- **Fine-Tuned Parallel Multi-Stream Engine**: Configurable 1–32 concurrent HTTP range workers, dynamic RAM ring buffer (32 MiB to 1024 MiB), and segment chunk sizing (2 MiB to 32 MiB).
- **Speed Diagnostics & Automated Benchmark**: Built-in benchmark engine offering **Quick Scan (~10s)** and **Full Test (~60s Multi-Tier Matrix)** to automatically determine and apply the optimal throughput configuration for your specific network.
- **Local File Transfers**: Native macOS file picker or path input for swift direct uploads.
- **Remote Console File Browser**: Browse, inspect, and navigate folders directly on the console's FTP storage.
- **Resilience & Verification**: Automatic reconnects with exponential backoff, safe partial file handling (`.ps5part`), strict Content-Range verification, and remote file size confirmation before finalizing.
- **Enterprise-Grade Local Security**: Localhost binding only (`127.0.0.1`), per-session token authorization, strict Content Security Policy (CSP), and zero analytics or tracking.

---

## Quick Start

### macOS & Windows
1. Ensure **Python 3.9 or later** is installed.
2. Double-click **`Launch PS5 Streamer.command`** (macOS) or **`Launch Direct Stream for PlayStation 5.bat`** (Windows).
   *(Alternatively, run `python3 ps5_streamer.py` directly from terminal).*
3. The dashboard will automatically open in your default browser at `http://127.0.0.1:<port>/#session=...`.
4. Open **Settings**, configure your PS5 IP address, FTP port, and destination folder, and click **Test connection**.
5. Click **New Transfer**, paste your download URLs or choose a local file, and start streaming!

> [!NOTE]
> On macOS, if Gatekeeper alerts you on first open, follow the standard confirmation ("Open Anyway"). If permissions are lost, run `chmod +x launch.sh "Launch PS5 Streamer.command"`.

### Android (via Termux)
Direct Stream runs natively on Android phones with **raw Wi-Fi throughput** and zero root required:

#### ⚡ 1-Line Quick Setup (Recommended)
Paste this single command into Termux to install prerequisites, download the app, and launch immediately:

```bash
curl -sSL https://raw.githubusercontent.com/ChillQuant/direct-stream-ps5/main/install_android.sh | bash
```

#### 🚀 Everyday Launch (Fastest)
Once installed, you never need to type git commands or change folders again. Simply open Termux and type:
```bash
ps5
```

#### 📱 1-Tap Home Screen Launcher (No Terminal!)
- **Chrome PWA**: When the dashboard opens in Chrome on your phone, tap the **3 dots** menu (⋮) → **"Add to Home screen"** / **"Install app"**. Tapping the icon on your home screen launches the app in full-screen standalone mode!
- **Termux:Widget**: If you have the free [Termux:Widget](https://f-droid.org/en/packages/com.termux.widget/) add-on installed, add the **"PS5_Streamer"** widget to your home screen to launch with 1 tap.

#### 🛠️ Manual Setup
```bash
# 1. Update packages & install python + git
pkg update -y && pkg install -y python git

# 2. Clone the repository
git clone https://github.com/ChillQuant/direct-stream-ps5.git
cd direct-stream-ps5

# 3. Start the streamer (auto-registers 'ps5' global command)
./run_android.sh
```


- **Background WakeLock**: Automatically enables Android WakeLock so your phone won't sleep or throttle Wi-Fi during 50GB+ streaming transfers.
- **Remote Access (`--host 0.0.0.0`)**: Pass `--host 0.0.0.0` (or `ps5 --host 0.0.0.0`) if you want to control the Android stream server from your PC, Mac, or tablet on the same Wi-Fi.
- **Phone Storage Access**: Run `termux-setup-storage` to stream local packages directly from `/sdcard/Download/`.

#### 🔑 Android Permissions (One-Time Setup)
When launching for the first time, Android will prompt you for two system permissions:
1. **📂 Storage / File Access**: Tap **Allow** when prompted so Direct Stream can access and stream local `.pkg` packages from your phone's `Downloads` folder.
2. **🔋 Energy / Battery Optimization**: Tap **Allow** (or choose **Unrestricted** under *Android Settings → Apps → Termux → Battery*). This prevents Android from putting Wi-Fi to sleep or killing the background process when your screen locks during huge 50GB–100GB transfers!



---

## Performance Presets & Diagnostics

Under **Settings → Transfer engine**, choose from calibrated quick-presets or run the benchmark:

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
- **Backpressure Pipeline**: The memory ring buffer ensures incoming HTTP chunks never exceed available RAM. If the FTP receiver is slower than the source, downstream backpressure gently pauses HTTP reads to prevent buffer overruns.

---

## Security & Privacy

- **100% Local**: Binds only to loopback (`127.0.0.1`) on a dynamic ephemeral port.
- **Session Authentication**: Every API request requires a cryptographically random session token generated on startup.
- **No Password Persistence**: FTP passwords are held only in process memory during runtime and are never written to disk.
- **Configuration Storage**: User settings and queue state are saved to `~/.ps5-transfer/state.json` with strict owner-only permissions (`0600`).

---

## Development & Testing

The application requires only Python's standard library. Testing requires `pyftpdlib` for the local FTP test fixtures:

```bash
# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install test dependencies
pip install -r requirements-dev.txt

# Run integration tests (32 tests covering auth, range checks, retries, resume, and bounds)
python -m unittest discover -s tests -v

# Sync changes to the Mac application bundle
python3 build_bundle.py
```

---

## License

This project is licensed under the [MIT License](LICENSE).
