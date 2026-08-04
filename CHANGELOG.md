# Changelog

All notable changes to Svodika will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added in 1.0.16
- Added a compact red icon-only stop button directly to the lower-right recording status overlay.

### Changed in 1.0.16
- Moved screen capture and H.264 encoding into a separate below-normal-priority process so recording no longer competes with the Qt interface.
- Switched screen encoding to the low-CPU x264 ultrafast/zero-latency profile while preserving the configured frame rate and quality.
- Reduced redundant audio-buffer copies, shortened callback lock time, and throttled waveform UI refreshes to keep recording controls responsive.
- Reduced needless recording-clock repaints while preserving sub-second status updates.

### Added in 1.0.15
- Added a visible `Auto / NVIDIA GPU / CPU` transcription-device selector directly under the recognition language in Settings.
- NVIDIA availability is checked with the same CTranslate2 runtime used for transcription; unavailable GPU selection is disabled with a clear explanation.

### Changed in 1.0.15
- Device changes save and apply immediately, reset compute precision to automatic selection, and reload the local Whisper backend in the background.
- Removed the obsolete post-transcription delivery and live-preview controls from the transcription settings page.

### Fixed in 1.0.15
- Explicit or stale NVIDIA selections now fall back safely to CPU when CUDA is unavailable.
- Removed the loading-screen glow around the microphone while preserving the animated progress bar.
- Fixed the loading screen failing to start after the glow cleanup.

### Fixed in 1.0.14
- Archived transcripts now expose a delete action with an explicit permanent-deletion confirmation.
- Meeting titles automatically include the meeting date and time; rename dialogs only require the descriptive part.

### Added in 1.0.13
- Added a pencil action for renaming a complete meeting package, including its media, transcript sidecars, and saved history references.
- Archived transcripts can now be given a clear custom title even when their original media file is no longer available.

### Fixed in 1.0.13
- A second launch now restores the already-running tray instance through the Qt event loop instead of creating a blank, unresponsive window.
- Applied the Svodika Windows taskbar identity before heavy imports and gave the loading window an explicit application icon.
- Removed duplicate tray show/hide handling that could cause window-state flicker.
- Removed obsolete project-origin copy from the public README.
- Settings changed in the embedded settings pages now save and apply automatically, including the transcription language.
- Replaced the ambiguous missing-file label with an explicit archived-transcript state.

### Fixed in 1.0.12
- Replaced the machine-looking `Welinkton/MeetingRecorder` user-data and install paths with product-only Svodika paths.
- Automatically migrate existing settings, database, history, and local environment data from the legacy branded folder.
- Removed the legacy publisher name from user-facing About text and Windows package metadata.

### Added in 1.0.11
- Added a persisted transcription-language selector with Russian as the default and English as an option for both local and OpenAI recognition.

### Changed in 1.0.11
- Renamed the user-facing application and Windows installer product to Svodika while preserving executable and update compatibility.
- Simplified the loading screen to one concise status line.
- Moved source, releases, and automatic update checks to the standalone `welinktoon/svodika` repository.

### Fixed in 1.0.10
- Tightened popup-menu spacing and aligned toolbar menus inward with a consistent six-pixel gap.

### Fixed in 1.0.9
- Replaced the strong blue hover/open fill in popup menus with a restrained neutral surface.

### Fixed in 1.0.8
- Restored the native Windows 11 frame, outline, shadow, rounded corners, Snap integration, and standard window controls.
- Fixed repeated maximize/restore toggles getting out of sync.
- Replaced the meeting-sort dropdown with a compact icon menu beside search and increased spacing in transcripts and new controls.
- Codex results for filesystem-only meetings now remain successful after their sidecar is written instead of failing on a nonexistent database row.
- The Windows installer now clears obsolete versioned icons and explicitly refreshes the shell icon cache after upgrading shortcuts.

### Changed in 1.0.7
- Reworked Codex meeting processing into three clear variants: brief, full, and full with the exact original transcript.
- Full meeting cards now cover context, participants, discussion, decisions, risks, open questions, next steps, and a separate task list with an explicit responsible person, role, or job title for every task.
- Added an icon-only reprocessing menu to every existing transcript, including already enhanced meetings, while preserving the editable raw transcript as the source of truth.

### Changed in 1.0.6
- Replaced all Windows-facing artwork with the approved soft-3D microphone in a precisely centered transparent blue badge.
- Added a matching transparent microphone-only mark for the title bar and loading screen, where the circular badge would be visually redundant.

### Changed in 1.0.5
- Replaced the application artwork with a centered, transparent, large-format microphone mark and regenerated the multi-resolution Windows icon.
- Restored standard Windows taskbar minimize/restore behavior for the frameless main window.
- A single click on the system-tray icon now toggles the main window; a double-click always restores it.

### Fixed in 1.0.4
- The Windows release now bundles the CUDA 12 runtime libraries required by CTranslate2 instead of detecting an NVIDIA GPU and failing on the first transcription.
- CUDA inference failures now retry once on CPU/int8, so recordings are still transcribed when the GPU runtime or driver is incompatible.

### Fixed in 1.0.3
- Uninstalling on Windows now gracefully exits the tray application, with a forced process-tree fallback for unresponsive or older builds.
- Changing the recordings folder now applies to the running application and immediately scans existing meetings.
- The settings save action appears only when there are unsaved changes; the redundant cancel action was removed.
- The Windows installer now offers checked options for Desktop and Start menu shortcuts.
- Removed audio-format controls that were visible but not connected to the recorder.

### Added
- **Project website** - [openwhisper.fiorilabs.tech](https://openwhisper.fiorilabs.tech/)
- **Model Technical Profiles** - Model Manager tiles now open bundled, offline technical profiles with model origin, practical guidance, specifications, limitations, and explicit links to the conversion and upstream model pages
- **Explicit Hugging Face Download Consent** - Model loading is now cache-first: cached models always load locally with zero network checks. A missing model triggers a consent dialog (Download once / Always allow / Cancel) governed by a three-value policy in Settings → Advanced (`ask`/`always`/`never`). The legacy fully-offline toggle migrates automatically (`true`→`never`, otherwise `ask`); `HF_HUB_OFFLINE=1` in the environment remains a hard override that disables downloads entirely
- **Fully Offline Setting** - Settings → Advanced toggle to skip HuggingFace Hub metadata checks on startup (same effect as `HF_HUB_OFFLINE=1`, without needing an environment variable); superseded in this cycle by the download-consent policy above
- **Cross-Platform Support** - macOS fork merged into a single codebase: Carbon global hotkeys, Accessibility trust handling for auto-paste, persistent overlay visibility, platform-specific default hotkeys
- **Minimize-to-Tray Hotkey** - `Ctrl+Alt+M` global shortcut
- **CLI Launchers** - `ow`/`openwhisper` commands with PATH installer scripts
- **Database-Backed History** - SQLite (SQLAlchemy) persistence replaces flat JSON history files, with one-time automatic migration
- **Faster Startup** - Startup profiling and lazy imports
- **Streaming Tiny-Model Option** - Dedicated tiny model for real-time streaming transcription
- **Collapsible UI Sections** - Collapsible transcription panel and section headers with smooth window resizing
- **Inline Local-Engine Controls** - Model/device/quantization controls in the main window with debounced engine reloads
- **Hotkey Watchdog** - Detects sleep/resume gaps and re-registers keyboard hooks automatically
- **History Search** - Debounced search box filtering transcription history by text or timestamp

### Fixed
- **Cleanup model dropdown type-to-filter** - Settings → Cleanup → General model picker now filters its own dropdown as you type (case-insensitive substring match) instead of appending characters to the current model id with no filtering
- **GPU transcription "cublas64_12.dll is not found" on Windows** - CTranslate2 loads CUDA libraries via `LoadLibrary`, which consults `PATH`, but the DLL dirs were only registered with `os.add_dll_directory` (ignored by that loader). Startup now also prepends the NVIDIA wheel `bin` dirs to `PATH`.
- **GPU never auto-detected** - Hardware detection used `import torch`, which is not a dependency, so `device: auto` always fell back to CPU on GPU machines. Detection now uses CTranslate2's `get_cuda_device_count()`.

### Added
- **`requirements-gpu.txt`** - Opt-in NVIDIA CUDA wheels (cuDNN 9, cuBLAS, CUDA 12 runtime) so GPU acceleration works without installing the CUDA Toolkit.

### Changed
- **"Transcription Engine" naming** - The main-window backend picker is now labeled "Transcription Engine" (was "Transcription Model"), reserving the word "model" for actual models (local Whisper checkpoints, cleanup chat models)
- **History Sidebar Redesign** - Single animation clock drives both the sidebar and window resize in lockstep (no more main-content wobble), fixed-width content is clipped instead of re-laid-out every frame, content populates before the first expand (no pop-in), section headers show counts, history cards show a model badge, and both sections share one scroll area
- Explicit overlay state routing via `OverlayState` enum and naming standardization
- Centralized module-level logging across services and UI
- Default hotkeys are numpad-aware on Windows/Linux (`kp *`, `kp -`)

### Removed
- Experimental Meeting Mode and meeting insights (added and removed during this cycle; never in a tagged release)
- **Duplicate model controls in Settings** - "Default Model" (General tab) and the Whisper Model/Device/Compute combos (Advanced tab) duplicated the main window's engine dropdown and inline Engine Settings panel while writing the same settings keys; each choice now has a single home (engine dropdown; Engine Settings panel / Model Manager)
- Experimental live typing into the focused window (settings toggle and keystroke injection)

## [1.0.0] - 2026-01-10

### Added
- **Real-time Streaming Transcription** - Live text preview while recording with draggable overlay
- **Caret Paste Indicator** - Visual feedback showing where text will be pasted
- **Dynamic Streaming Settings** - Reconfigure streaming behavior without restart
- **Enhanced Crash Diagnostics** - Improved logging with Qt message handling for debugging
- **Window Geometry Persistence** - App remembers size and position between sessions
- **Audio Input Device Selection** - Choose your preferred microphone from settings

### Fixed
- Window vertical resizing not working properly
- Numpad hotkeys now correctly distinguished from regular number keys(Thanks meonester)
- Crashes on workstations without GPU or unsupported compute configurations
- Various stability improvements for CPU-only systems

### Changed
- Optimized CUDA/GPU detection and fallback behavior
- Improved model benchmark tooling
- Updated Python 3.12 recommendation for best compatibility
