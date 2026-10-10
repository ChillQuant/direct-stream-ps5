# Validation and Audit Ledger — Version 2.9.1

Hardware validation performed directly against physical PlayStation 5 hardware (`192.168.1.188:2121` FTP and port `9021` helper service) under etaHEN / ftps5 environment, running Python 3.12 and macOS host. All 32 hardware tests completed with 100% pass rate and verified 0 bytes leaked on console storage.

## Version 2.9.1 Live Hardware Verification Matrix (32/32 Passed)

The following verification ledger documents every feature, archive combination, encryption scenario, and resilience guard tested on live PlayStation 5 console storage:

| Test ID | Test Category | Feature / Scenario Description | Payload | Hardware Result | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **HW-01** | PKG Direct Stream | Single PKG streamed directly over HTTP into `/data/pkg` | 1,048,576 B | Byte-exact transfer, atomic rename | PASSED |
| **HW-02** | Multi-Part PKG | 3-part split PKG stitched on-the-fly in RAM into single package | 3,993,600 B | Merged to single PKG without disk wear | PASSED |
| **HW-03** | ZIP RAM Stream | Single unencrypted ZIP extracted in RAM on-the-fly to PS5 | 262,144 B | Extracted to console without host disk write | PASSED |
| **HW-04** | ZIP RAM Encrypted | Password-protected ZIP extracted in RAM (`DLPSGAME.COM`) | 262,144 B | Decrypted and streamed directly | PASSED |
| **HW-05** | ZIP Multi-Part RAM | Split ZIP archive (.z01, .zip) extracted in RAM to console | 524,288 B | Multi-chunk stream reassembled in memory | PASSED |
| **HW-06** | ZIP Multi Encrypted | Split ZIP with password extracted on-the-fly in RAM | 524,288 B | Decrypted across split boundary | PASSED |
| **HW-07** | ZIP PS5 Console Unpack | ZIP uploaded to `/data/unrar` and unpacked via `unrar-ps5.elf` | 524,288 B | Triggered on port 9021, unpacked on console | PASSED |
| **HW-08** | 7Z RAM Stream | Single unencrypted 7Z extracted in RAM on-the-fly to PS5 | 262,144 B | Streaming LZMA decompression | PASSED |
| **HW-09** | 7Z RAM Encrypted | Password-protected 7Z extracted in RAM (`DLPSGAME.COM`) | 262,144 B | AES-256 decryption stream verified | PASSED |
| **HW-10** | 7Z Multi-Part RAM | Split 7Z archive (.7z.001, .7z.002) extracted in RAM | 524,288 B | Multi-volume sequence stitched in RAM | PASSED |
| **HW-11** | 7Z Multi Encrypted | Split 7Z archive with password extracted in RAM | 524,288 B | Decrypted across multiple volumes | PASSED |
| **HW-12** | 7Z PS5 Console Unpack | 7Z uploaded to `/data/unrar` and unpacked via console payload | 524,288 B | Port 9021 triggered, unpacked to destination | PASSED |
| **HW-13** | RAR Staged Extraction | RAR archive with folder hierarchy extracted via host `unar` | 1,048,576 B | Directories recursively created on PS5 | PASSED |
| **HW-14** | RAR PS5 Console Unpack | RAR uploaded to `/data/unrar` and unpacked via `unrar-ps5.elf` | 1,048,576 B | Unpacked on console internal SSD | PASSED |
| **HW-15** | Password Discovery | Auto-discovery of archive password from `[DLPSGAME.COM]` tag | N/A | Extracted from filename brackets | PASSED |
| **HW-16** | Pause & Resume | Mid-stream pause, state preservation, and resume via FTP `REST` | 2,097,152 B | Resumed from partial offset without restart | PASSED |
| **HW-17** | Bandwidth Throttling | Speed limit cap applied during transfer (`limit_mbps=2.0`) | 1,048,576 B | Bandwidth throttled smoothly | PASSED |
| **HW-18** | Directory Upload | Recursive folder upload (`kind="folder"`) over FTP | 12 files | Subdirectories created and files transferred | PASSED |
| **HW-19** | Wrong Password Guard | Encrypted archive attempted with invalid password | N/A | Immediate rejection, clear diagnostic | PASSED |
| **HW-20** | Missing Part Detection | Multi-part set with missing sequence number (gap) | N/A | Aborted before transfer, gap reported | PASSED |
| **HW-21** | Overwrite Guard | Transfer attempted when destination file already exists | N/A | Blocked by default collision protection | PASSED |
| **HW-22** | Overwrite Replace | Explicit overwrite toggle enabled for preexisting file | 524,288 B | Preexisting file cleanly replaced | PASSED |
| **HW-23** | HTTP Range Seeking | HTTP Range request validation and dynamic chunk slicing | N/A | Validated 206 Partial Content responses | PASSED |
| **HW-24** | Storage Clean Audit | Verification of PS5 SSD space after test suite execution | 0 B leak | `/data/pkg` and `/data/unrar` 100% clean | PASSED |
| **HW-25** | Local Split PKG Stitch | Local multi-part PKG files stitched directly to console | 3,145,728 B | Pipelined sequentially without merge file | PASSED |
| **HW-26** | Non-Range Staging | HTTP source lacking Range header automatically staged | 524,288 B | Fallback staging executed seamlessly | PASSED |
| **HW-27** | Solid 7Z Staging | Complex solid-block 7Z staged cleanly when RAM seek fails | 524,288 B | Extracted and streamed to `/data/homebrew` | PASSED |
| **HW-28** | Multi-Part RAR PS5 | Multi-part RAR set unpacked on console via `unrar-ps5.elf` | 1,048,576 B | Port 9021 console decompression verified | PASSED |
| **HW-29** | Encrypted RAR PS5 | Encrypted multi-part RAR unpacked on console with password | 1,048,576 B | Password passed to console payload | PASSED |
| **HW-30** | FTP Keep-Alive | NOOP command heartbeats during prolonged read phases | N/A | Connection preserved without timeout | PASSED |
| **HW-31** | Zero-Byte Guard | Attempted transfer of 0-byte file rejected safely | 0 B | Rejected gracefully without error cascade | PASSED |
| **HW-32** | Buffer Backpressure | RAM ring buffer throttles HTTP download to match FTP write | N/A | RAM usage capped strictly to configured limit | PASSED |

---

# Historical Validation and Audit — Version 2.0.0

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
