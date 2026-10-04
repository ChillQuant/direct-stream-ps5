# Validation and audit — version 2.0.0

Validation performed in a Linux execution environment with Python 3.12 and Chromium 134. This is a rebuilt application, not a measured tuning session on the user's Mac or PS5.

## Performance measurement

A controlled, synthetic comparison used the actual original `ParallelPipelinedReader` from the uploaded ZIP and the updated `ParallelReader`:

- 128 MiB binary payload over loopback HTTP/1.1.
- 50 ms additional delay per HTTP request, modeling request latency.
- Identical 4 workers and 64 MiB buffer setting.
- Original 2 MiB range size versus updated 8 MiB range size.
- Three runs each, consuming all bytes and checking SHA-256 against the fixture.

| Engine | Three runs, MB/s | Median, MB/s |
| --- | --- | --- |
| Original, 2 MiB ranges | 152.78, 153.76, 149.95 | 152.78 |
| Updated, 8 MiB ranges | 468.23, 497.83, 487.54 | 487.54 |

The updated downloader was about **3.19× faster in this request-latency fixture**. This demonstrates reduced request overhead; it is **not** a PS5 upload benchmark and does **not** predict 3.19× improvement on the user's connection. Loopback does not model PS5 CPU, FTP payload behavior, Wi-Fi, disk speed, or internet throttling. The final worker-local buffer cleanup does not change request sizes or the measured algorithm; it additionally releases stale memory references.

## Automated transfer/API coverage

29 tests passed against local HTTP and a real `pyftpdlib` FTP server:

- Parallel HTTP → FTP with complete byte/hash comparison and final rename.
- Redirects into a ranged source; single-stream fallback; unknown-length source.
- Resuming a non-aligned byte offset; local-file resume and local-file upload.
- Zero-byte local files.
- Wrong Content-Range, ignored resume, truncated HTTP, HTML and compressed range rejection.
- Changed source identity and mid-transfer ETag changes for parallel and sequential reads.
- Transient HTTP errors and bounded per-range retry.
- Partial larger than source; partial without ownership history, including repeated attempts.
- Protection of preexisting final files and explicit replacement.
- Cancellation while source download is in progress.
- Sliding-window bounds and validation of settings/control-character injection.
- Restoring a queue paused after process restart; owner-only state files; no password persistence.
- Destination-change protection before a resumed queue job starts.
- API session-token, Host and Origin enforcement.

## Browser checks

Chromium checks passed with no JavaScript console errors:

- Settings, presets, adding one or several URLs, and queue pause/resume controls.
- Connection diagnostic against the local FTP fixture.
- A complete workflow: start → live progress → pause → update URL → resume → completion.
- Byte-for-byte comparison of the resulting file after that workflow.
- FTP folder listing, source speed diagnostic, activity view.
- Visual review at 1440px desktop, 1024px compact desktop and 390px narrow width.
- No horizontal overflow of the whole page; the queue table scrolls within its container on narrow screens.
- Dialogs, queue action-menu positioning, and browser security policy.

## Identified issues addressed

| Original issue | Change |
| --- | --- |
| Each worker requested the original URL even after a redirect probe | Workers use the validated final URL from the probe |
| HTTP resume could append a full HTTP 200 body to a partial file | Require matching 206 range responses before accepting resumed bytes |
| Only response status/length were checked for parallel chunks | Validate exact Content-Range and source validators |
| HTTP 416 could be treated as “nothing to resume,” even for an oversized partial | Probe from byte zero, compare sizes, reject oversized partials |
| TLS certificate/hostname verification explicitly disabled | Use system-trusted TLS validation; reject HTTPS downgrade redirects |
| Small 2 MiB chunks incurred frequent request overhead | Tunable ranges; 8 MiB default with persistent workers |
| Repeated range assembly/slicing copied large byte buffers | Preallocated bytearrays, readinto and memoryview consumption |
| Buffer setting could be exceeded automatically as stream counts rose | Explicit validated window; worker count constrained by available slots |
| Stalled I/O and retry sleeps delayed cancellation | Close tracked sockets, wake readers, cancellable delays and bounded joins |
| Resume and source state were not persisted safely | Atomic owner-only queue state and source/destination identity checks |
| Incomplete and completed files shared a final name | Unique partial names, remote size check, then rename |
| Missing queue, local upload, and bottleneck diagnostics | New dashboard and operations described in README |
| Launcher preferred an author's hardcoded project path | Relative paths and self-contained app resources |
| Installer deleted the previous app before copying | Stage the install and retain a timestamped backup |
| “MB/s” displayed binary units | Explicit decimal speeds and binary memory units |

## Not verified here

- Real Mac Python discovery, Finder bundle launch, native AppleScript picker, `caffeinate`, signing/Gatekeeper, or actual macOS Local Network permissions. Shell scripts and plist were statically checked; there is no Mac hardware in this environment.
- The user's PS5 payload implementation of SIZE, REST, STOR and rename, available disk space, or maximum sustainable transfer rate. A small real-device trial is required before large transfers.
- Independent remote SHA-256 verification; the application checks remote length and source metadata, not full destination hashes.

No claim is made that every possible defect has been eliminated. The source and regression suite are included for further changes.
