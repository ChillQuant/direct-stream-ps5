## Summary

Provide a clear and concise summary of what this pull request introduces, fixes, or refactors.

- Closes #(issue number)

---

## Type of Change

Select all that apply:

- [ ] Bug fix (non-breaking change fixing an unexpected error or stall)
- [ ] New feature (non-breaking enhancement adding functionality)
- [ ] Performance optimization (throughput, latency, or memory efficiency)
- [ ] Documentation update
- [ ] Architecture refactor or test improvements

---

## Technical Details

Describe the approach taken, key files modified, and rationale behind architectural decisions:

1. 
2. 
3. 

---

## Core Constraint Verification

Direct Stream for PlayStation 5 enforces strict operational invariants. Verify that this PR adheres to each:

- [ ] **Zero Mac Disk Usage Guarantee:** Transfers, multi-part stitching, and on-the-fly streaming decompression do not write temporary gigabyte-sized files to local disk unless the user explicitly requested local staging.
- [ ] **Emoji-Free Codebase:** All code, docstrings, console logs, error traces, UI strings, and comments contain strictly zero emojis.
- [ ] **Clean FTP Teardown:** FTP sockets, data channels, and reader threads are deterministically closed and freed without resource leaks.
- [ ] **Multi-part & Archive Compatibility:** No regressions introduced to standalone payload routing (`/data/homebrew`, `/data/ShadowMount`, `/data/pkg`) or unrar-ps5 payload automation.

---

## Verification & Testing

### Automated Test Suite
- [ ] Ran automated unit tests:
  ```bash
  .venv/bin/python -m unittest discover -s tests
  ```
  Result: All unit tests pass.

### Native Mac Bundle Synchronization
- [ ] Synchronized native Mac app bundle assets:
  ```bash
  .venv/bin/python build_bundle.py
  ```

### Real Hardware or Mock Testing
Describe how changes were validated (e.g. against physical PS5 FTP server, local `pyftpdlib` mock server, or browser interface):
- Console Firmware & Environment: (e.g., PS5 4.03 / 4.50 / 5.xx, etaHEN v1.8b)
- Host Operating System: (e.g., macOS 15.x Sequoia, Windows 11, Ubuntu 24.04)
- Test Artifacts / Payload: (e.g., .pkg, .rar, .7z, multi-part archive, raw folder)

---

## Checklist

- [ ] My code follows the repository style and architecture guidelines.
- [ ] I have commented complex or non-obvious algorithms.
- [ ] Relevant documentation (README.md, GITHUB_RELEASE_METADATA.md) has been updated if applicable.
- [ ] New or updated functionality includes corresponding unit test coverage in `tests/test_transfer.py`.
