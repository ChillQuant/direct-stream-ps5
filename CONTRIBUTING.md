# Contributing to Direct Stream for PlayStation 5

Thank you for your interest in contributing to Direct Stream for PlayStation 5. We welcome community bug fixes, direct link resolvers, performance optimizations, and platform improvements.

---

## Reporting Issues

If you encounter unexpected behavior, stalls, or console decompression errors:

1. **Use In-App Diagnostics:**
   When a transfer fails in the dashboard, open the error modal and click **Copy bug report** or **Push to GitHub Issue**. This gathers safe diagnostic metrics (app version, transfer kind, active strategy, sanitized destination path, and Python traceback) without exposing passwords or private credentials.

2. **Select the Right Issue Template:**
   - **Transfer or Extraction Failure:** For transfer stalls, 100% hang-ups, or unrar-ps5 issues.
   - **Bug Report:** For UI anomalies, general crashes, or auto-resolver errors.
   - **Feature Request:** For new hosting resolvers or workflow enhancements.

3. **Check Existing Issues:**
   Search open and closed issues before submitting to avoid duplicate reports.

---

## Development Setup

### Prerequisites
- Python 3.10 or higher
- macOS (recommended for native bundle testing), Windows, or Linux
- Git

### Initializing the Workspace
```bash
# Clone the repository
git clone https://github.com/ChillQuant/direct-stream-ps5.git
cd direct-stream-ps5

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install development test dependencies
pip install -r requirements-dev.txt
```

---

## Architectural Principles & Invariants

All contributions must preserve the core architecture of Direct Stream:

1. **Zero Mac Disk Usage Guarantee:**
   Direct streaming transfers, multi-part stitching, and on-the-fly decompression must stream data directly into console sockets via RAM ring buffers. Never write gigabyte-sized archives or extracted game payloads to local disk unless the user explicitly configured local staging.

2. **Strictly Zero Emojis:**
   All Python source code, docstrings, UI HTML/CSS/JS, terminal output, error messages, and documentation must contain strictly zero emojis.

3. **Deterministic Socket & Resource Teardown:**
   Every FTP data connection, reader pipe, and worker thread must implement deterministic cleanup in `finally` blocks to prevent socket exhaustion or hanging file descriptors.

4. **Default Destination Conventions:**
   The standard default console destination across the system is `/data/homebrew`. Standalone `.pkg` files auto-route to `/data/pkg`, and raw mountable disk images (`.ffpfsc`, `.exfat`) route to `/data/ShadowMount` when not explicitly set by the user.

---

## Running Tests

Before submitting changes, ensure all unit and integration tests pass:

```bash
# Run the complete test suite
.venv/bin/python -m unittest discover -s tests
```

To run a specific test case:
```bash
.venv/bin/python -m unittest tests.test_transfer.Integration.test_smart_destination_routing_and_per_job_folder
```

---

## Synchronizing the Mac Application Bundle

If you modify files in the repository root (`ps5_streamer.py`, `transfer_core.py`, `resolver.py`, `zip_streamer.py`, or `web/`), you must synchronize the native macOS application bundle:

```bash
.venv/bin/python build_bundle.py
```

Verify with `git status` that both root files and their corresponding bundle copies under `PS5 Direct Streamer.app/Contents/Resources/` are tracked together.

---

## Submitting Pull Requests

1. **Create a Feature Branch:**
   ```bash
   git checkout -b fix/transfer-timeout-retry
   ```

2. **Write Clean, Focused Commits:**
   Keep commits focused on a single logical change. Use clear, descriptive commit messages.

3. **Verify All Invariants:**
   - [ ] All 100+ unit tests pass.
   - [ ] `build_bundle.py` was executed.
   - [ ] Zero emojis in modified code or documentation.
   - [ ] Zero Mac disk usage invariant preserved.

4. **Open Pull Request:**
   Submit your pull request against the `main` branch. Fill out the provided Pull Request Template completely.
