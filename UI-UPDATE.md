# Direct Stream — Interface 3.2

This update refines the existing app; run it with your usual launcher. Quit an older running instance first, extract this archive, and launch from the updated folder. If you use an installed Mac app, replace it with the updated `PS5 Direct Streamer.app` in this folder.

## Interface 3.2: guided transfer experience

- Guided console setup: prepare the network and file server, enter address/port/destination/login, save, then run the existing connection diagnostic. A successful diagnostic is required for the success screen. Failed checks show next steps and expandable details. Settings are still available for experienced users.
- Saved file-server passwords are preserved when omitted from a settings update. Setup can explicitly replace or remove a saved password. Password values remain absent from state snapshots.
- Transfer stages: prepare, transfer, console extraction when applicable, then completion. Local extraction appears before transfer. Pause, retry, and stopping states have plain-language explanations. Stage inference uses the engine's existing job state and status text; it does not claim measured stage percentages.
- Recovery cards and error-dialog actions: replace an expired link, enter an archive password, test the console connection, inspect storage, review details, or queue a retry. Retrying queues the job; users retain control of starting the queue.
- Completion: count and transferred bytes, the destination for each of the latest five completed jobs, an Open destination action, and extraction log access. Archive cleanup settings are reported separately from confirmation; cleanup is not asserted without evidence.
- Language: local device wording replaces Mac-only labels, the path field identifies the device running the app, and the extraction helper is explained. Technical details remain accessible.
- Shared visual identity: consistent setup steps, recovery and success cards, restrained completion feedback, accessible status announcements, and reduced-motion support.

Validation: browser workflow tests cover input validation, failed/successful connection states, recovery API requests, local and console extraction stages, destination browsing, and mobile overflow. Existing menu and extraction-mode checks pass. Backend password-retention/clearing checks pass. The existing 100-test suite still has 96 passes, one skip and the same three macOS extraction-tool errors in Linux. No physical console was available. Preview connection and transfer states use explicit test fixtures.

## Interface 3.1: queue menu and visual character

The queue actions menu is now a body-level overlay, rather than content inside the table row. It has grouped actions, icons, a selected extraction-mode indicator, disabled unavailable reorder commands, a close button, viewport-aware positioning, and scrolling on short screens. Keyboard support includes arrow keys, Home, End, Escape and Tab. Destructive actions retain their existing confirmations. The menu closes when its job disappears or changes state.

The transfer monitor now has a navy surface with brighter progress and legible telemetry. Branding, navigation and source-card feedback use restrained blue accents. Interface 3.1 is shown in the footer.

Validation: reproduced the paused-archive case; verified unchanged row height, fixed positioning, all mode indicators, keyboard and pointer actions, exact extraction action request, and viewport bounds at 1440, 390 and 320 pixels. The main UI smoke checks also passed. Preview transfer figures are illustrative fixture data.

## Interface 3.0 refinement

This version replaces the earlier visual treatment throughout the app: graphite navigation, restrained blue accents, simpler file icons, quieter status labels, consistent type and spacing, refined forms and settings tabs, and a more compact setup area beside source selection. The footer identifies this version as **Interface 3.0**.

Browser interaction checks and layouts at 320, 390, 768, 1024 and 1440 pixels passed. Extraction descriptions and checkbox synchronization were rechecked. The backend engine is unchanged in this refinement.

## Features retained

- A cohesive graphite, white and restrained blue interface with consistent spacing, typography, controls, and responsive layouts.
- Everyday navigation ordered as Transfers, Console files, Diagnostics, Activity, Settings.
- A live overview of console connection, destination and completed transfers.
- Clear file, folder and link entry points, plus a guided first-use screen.
- New-transfer controls ordered by source, destination, then archive handling; advanced options and supported services remain expandable.
- Search and status filters for the queue, with bulk selection limited to visible matching jobs. Changing the filter clears selections to avoid acting on hidden jobs.
- Optional transfer insights keep speed and memory charts available without overwhelming the main view.
- Compact view preference saved in the browser, a quick-start guide, accessible dialog names, improved keyboard navigation, visible focus, and reduced-motion support.
- Truthful connection labels; fixed settings-card navigation, a missing navigation export, progress bars under the existing content security policy, and expandable sections closing unexpectedly when clicking elsewhere.
- Paused transfers retain their progress display. Native Mac bundle resources match the updated source.

The transfer engine and supported sources remain in place. The app still runs on Python's standard library; no new runtime dependencies or external fonts were added.

## Validation

- JavaScript syntax checks passed.
- Browser checks passed against the running Python app: all navigation pages, all source types, actual local file queue creation in an isolated profile, search and status filters, visible-only bulk selection, paused progress, compact preference persistence, modal open/close behavior, keyboard controls, mobile page/dialog overflow, and no uncaught JavaScript errors.
- Desktop (1440px) and mobile (390px) screenshots were inspected. Active-transfer states use test fixtures, not a physical PS5 connection.
- Existing Python suite: 100 tests run; 96 passed, 1 skipped, 3 errors. The three errors involve archive extraction using the bundled macOS `unar` / `lsar` tools, which cannot execute in this Linux validation environment. Real-console transfer and native macOS launch require checking on your hardware.

## Files

The UI changes are in `web/index.html`, `web/app.js`, `web/experience.css`, and `web/experience.js`. The server asset allowlist in `ps5_streamer.py` serves the two new files. Corresponding files are included in the Mac app bundle. No build step is needed.

`ui-preview/` includes screenshots of the desktop workspace and active queue. Active queue data is illustrative.

## Extraction description correction

The Advanced options extraction label now follows the selected PS5, computer, or raw archive mode. Its checkbox also changes the actual extraction mode; re-enabling restores the last selected extraction location. Browser checks passed for all three descriptions and both checkbox directions.
