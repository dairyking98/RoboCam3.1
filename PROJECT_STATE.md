# RoboCam 3.1 — Project State & AI Handoff Document

This document describes the exact architecture, UI layout, and feature set of the RoboCam 3.1 repository as of the latest commit. It is intended for both user reference and as a fast-resume context for any future AI agent sessions.

## 1. Core Architecture

RoboCam 3.1 is a Python desktop application built with **PySide6** (Qt 6). It has no web server or browser dependencies.

### Key Modules (`robocam/`)

| Module | Purpose |
|---|---|
| `robocam31.py` | Main GUI entry point. |
| `robocam/__main__.py` | Headless CLI (`python -m robocam`). |
| `robocam/camera.py` | Camera abstraction layer. Priority: Player One SDK (`pyPOACamera`) → Picamera2 → OpenCV. Handles exposure, gain, dynamic resolution, thread lock. |
| `robocam/motion.py` | Motion backends: `MarlinBackend` (serial/USB with M400 checking), `KlipperBackend` (Moonraker HTTP), `SimulationBackend`. |
| `robocam/calibration.py` | `WellPlate` bilinear interpolation from 4 corners. `CalibrationManager` save/load JSON. Raster and Snake scan patterns. |
| `robocam/experiment.py` | `ExperimentRunner`: well-by-well movement, two capture modes (Image, Raw Burst), per-frame timestamps, laser timing, CSV + JSON sidecar. |
| `robocam/postprocess.py` | `.npy` burst → debayered PNGs + VFR MKV + display MP4. Shared core used by both the Processing tab and `reconstruct_vfr.py`. |
| `robocam/peripherals.py` | `LaserController`: `disabled`, `rpi_gpio` (`lgpio` preferred, `RPi.GPIO` fallback — **lgpio-on-Pi-5 path verified working**), `klipper` (SET_PIN G-code). |
| `robocam/session.py` | Session persistence to `~/.local/share/RoboCam3/session.json`. |
| `robocam/hw_state.py` | Global hardware singleton (camera, motion, runner). |
| `robocam/config.py` | JSON-backed configuration (`config/default_config.json`). `Config.set()` auto-saves. |

### Key UI Modules (`ui/`)

| Module | Purpose |
|---|---|
| `ui/main_window.py` | QMainWindow with six tabs; cross-panel signal wiring; clean shutdown. |
| `ui/setup_panel.py` | Hardware connection, camera enumeration/settings, laser config, udev installer. |
| `ui/motion_profiles_panel.py` | Slider-based feed-rate (M203) / acceleration (M201) / jerk (M205) editor, X/Y always chained, Marlin only, with save/load presets. |
| `ui/profile_slider.py` | `ProfileSliderRow` — labeled slider + spinbox + default-value marker, ported from RoboCam-Suite 2.0. |
| `ui/manual_control_panel.py` | Jog, go-to, laser toggle, raw G-code sender. |
| `ui/calibration_panel.py` | Corner capture, well map, calibration save/load, quick capture. |
| `ui/experiment_panel.py` | Experiment configuration, output folder picker, run/stop/pause control. |
| `ui/processing_panel.py` | Batch `.npy` → images/video conversion queue, per-well progress. |
| `ui/camera_widget.py` | Shared `_FrameGrabber` (QThread) + `_LivePreview` (QPainter). |
| `ui/well_grid.py` | Custom-painted well grid widget (navigate/select modes). |

### Scripts (`scripts/`)

| Script | Purpose |
|---|---|
| `scripts/install_playerone_sdk.py` | Downloads and patches Player One SDK for Linux/ARM. |
| `scripts/reconstruct_vfr.py` | Unified post-processing pipeline: `.npy` → images + VFR MKV + display MP4. |

---

## 2. User Interface (6-Tab PySide6 Layout)

### Tab 1: Setup
- Camera enumeration ("Scan for Cameras") across Player One / Picamera2 / OpenCV, with device/resolution/fps-cap selection and Apply & Reconnect.
- udev USB permission auto-installer for Player One cameras (**verified working on hardware** — grants access without a replug).
- Printer backend dropdown (`marlin` / `klipper`), serial port/baud or Klipper host/port, Apply & Reconnect.
- Laser Mode (`disabled`, `rpi_gpio`, `klipper`), GPIO pin, Klipper G-code fields, Apply.
- Hardware Status group with live connection/homing indicators and a "Home All Axes" shortcut.

### Tab 2: Motion Profiles
Read/edit/write max feed rate (`M203`), max acceleration (`M201`), and jerk (`M205`) for X/Y/Z via slider rows (`ui/profile_slider.py`, ported from RoboCam-Suite 2.0 — labeled slider + spinbox + a "ghost tick" marking the value last confirmed on the printer; a field's border highlights orange when its current position differs from that ghost tick, i.e. Apply would actually change it). X and Y are always chained into a single "XY:" row — no per-axis split, since this stage never needs them independent (Suite 2.0's "Link X=Y" checkbox is gone; it's just always linked).

Slider ranges are the **real firmware edit ceilings** for this rig's actual printer — a Creality **Ender-5 S1** (identified from `RoboCam-Suite/robocam/robocam.py:33`'s board-detection comment; confirmed against Creality's own shipped `Marlin/Configuration.h` at `github.com/CrealityOfficial/Ender-5S1`, `MACHINE_TYPE "Ender-5 S1"`, `MARRY_HIGHT_SPEED` variant which is what ships) — not generic/guessed values: M203 max feed rate X/Y ≤600 mm/s, Z ≤20 mm/s (`MAX_FEEDRATE_EDIT_VALUES`); M201 max acceleration X/Y ≤2000 mm/s², Z ≤200 mm/s² (`MAX_ACCEL_EDIT_VALUES`); M205 jerk X/Y ≤10 mm/s, Z ≤0.6 mm/s (`MAX_JERK_EDIT_VALUES`). Sending a value above these gets silently clamped by the firmware regardless, so the sliders don't go higher. Notably the firmware's own boot-time defaults (feed 500 XY/20 Z, accel 3000 XY/100 Z, jerk 15 XY/0.4 Z) actually *exceed* some of these same edit ceilings — a real quirk in Creality's shipped config, not a bug introduced here.

No values are pre-populated/defaulted anywhere (including in `SimulationBackend`, which now starts with an empty profile dict) — motion profile values are treated as unknown until actually read from (or set on) a printer. Three bundled starting-point presets ship in `config/motion_profile_presets/`, all biased toward low vibration but at increasing speed tiers:

| Preset | Feed XY/Z (mm/s) | Accel XY/Z (mm/s²) | Jerk XY/Z (mm/s) |
|---|---|---|---|
| `ender5_s1_slow_low_vibration.json` | 100 / 10 | 500 / 50 | 5.0 / 0.2 |
| `ender5_s1_medium_low_vibration.json` | 200 / 15 | 1000 / 75 | 6.5 / 0.3 |
| `ender5_s1_fast_low_vibration.json` | 300 / 20 | 1500 / 100 | 8.0 / 0.4 |

`slow` is treated as **the actual default**, not just one option among three: the preset combo box defaults to it on first load, and it's what gets applied on the very first printer connect of a session (`_on_printer_connected`, `_DEFAULT_PRESET_NAME`) when nothing has been read/applied/loaded yet — safer to start conservative than trust whatever the firmware happened to boot with (recall the firmware's own boot defaults exceed its own edit ceilings, per above). All three are a **reasoned but unvalidated** compromise between this printer's own official Klipper config for the same hardware (`max_velocity` 300, `square_corner_velocity` 5.0 — from `Klipper3d/klipper`'s `printer-creality-ender5-s1-2023.cfg`, used as the `fast` tier's ceiling) and general classic-jerk tuning guidance, biased toward the low-vibration side since jerk/accel (not feed rate) dominate settle time on the short well-to-well hops this rig actually makes. None have been validated against real ringing/resonance behavior on the physical unit — treat them as starting points to refine empirically, not computed optima.

"Read from Printer" queries `M503`; "Apply to Printer" sends the M-codes and saves to EEPROM (`M500`); "Reset to Defaults" reverts sliders to the last-read/applied values without contacting the printer. Marlin (and `SimulationBackend`, for testing) only — Klipper has no equivalent gcode for these, so the tab shows a disabled "not supported" message when that backend is selected; sliders themselves stay editable regardless of connection state, so profiles can be prepared offline.

**Motion Profile Presets**: save/load named slider configurations to `config/motion_profile_presets/<name>.json` (same convention as Experiment Presets). **Updated in PR #12**: Load now only populates the sliders — it no longer also Applies, matching Load's semantics everywhere else in the app (Apply is the one action that sends gcode). The last-loaded preset name persists across restarts (`motion_profiles` session section) and is restored — sliders populated, nothing sent — on next launch. On `SetupPanel.motion_connected`, if a profile was already read/applied earlier this session it's still re-applied immediately (in case the printer's actual state drifted while disconnected); only falls back to a plain Read when nothing is known yet this session.

Untested on real Marlin hardware — only exercised in simulate mode so far.

### Tab 3: Calibration
- Set UL / LL / UR / LR corner positions by jogging to each and clicking Set; well map auto-generates once all four are set.
- Grid dimensions (Rows × Cols), scan pattern (Raster / Snake).
- Well map: click any well to jog to its bilinearly interpolated position immediately.
- **Crosshair overlay** (PR #7, #10): toggleable center crosshair (lines + circle) on the live preview for aligning a well under the camera before recording a corner. Radius is a % of frame height (not raw px), so it stays visually consistent across resolutions/window sizes; adjustable via a spinbox and a synced `QSlider`. Verified via rendered screenshots at different frame/widget sizes.
- Save/Load Calibration — writes `config/calibrations/<name>.json` with both `corners` and pre-computed `interpolated_positions`/`labels`.
- Quick Capture: grab a still or short raw burst directly from this tab, outside a full experiment.

### Tab 4: Experiment
- Experiment name, calibration file selector, experiment presets (save/load named JSON configs).
- **Output folder**: label + Browse button. Changes saved to `config/default_config.json` and applied to the live runner immediately.
- Capture mode: **Image** or **Raw Burst** (the former "Raw .npy" and "Video (AVI)" modes were consolidated into Raw Burst; real-time AVI encoding was removed in favor of post-processing).
  - *Image*: format **PNG (default, as of PR #14)** / JPG / TIF, dwell per well. PNG was made the default for lossless, artifact-free output, with any speed tradeoff deferred to post-processing; the format combo box lists png first so a fresh install with no `session.json` yet also defaults to it.
  - *Raw Burst*: record duration. With "Use Laser": Pre-laser / Laser ON / Post-laser timing, captured continuously in one burst (**verified working on hardware**, including `laser_events` timing accuracy).
- Well selection grid, with "Auto-process after experiment" checkbox to hand the finished folder straight to the Processing tab.
- **Auto-home**: if the printer reports not-homed when a run starts, `ExperimentRunner.run()` homes automatically before the well loop rather than blocking Start (PR #11, untested on real hardware).
- **Experiment log**: scrolling box with verbose per-stage logging (homing, moving to well, dwelling, recording, laser on/off), plus a live "ETA: MM:SS" countdown computed right after homing from the motion profile's feed-rate/accel and exact dwell/capture durations — turns red and goes negative on overrun, shows "unavailable" on backends without a motion profile (Klipper). PR #14 tightened the underlying finalize- and capture-time estimates this depends on to within ~0.1% error on hardware (see § 3).
- Start / Stop / Pause buttons. Status label updated on each state change.
- **Experiment in progress overlay**: amber `"EXPERIMENT IN PROGRESS / Preview Paused"` shown on the camera preview for the whole run.

### Tab 5: Manual Control
- Home All Axes, Disable Steppers (M18).
- XY/Z jog grid. Step size 0.1 / 1.0 / 10.0 mm or custom.
- Go-to by absolute X, Y, Z.
- Manual Laser ON / OFF + state label.
- **Demo Mode** button opens a separate fullscreen `QMainWindow` for kiosk-style/remote operation: well navigation and laser control, arrow-key jogging (uppercase axis keys), on-screen keyboard-shortcut hints, nav buttons disabled at grid edges, no on-screen exit button (keyboard-only exit). Untested on real hardware.
- Steppers stay powered (`M18 S0` sent on printer connect, disabling Marlin's idle-timeout de-energize) so a parked axis can't silently drift between calibration and experiment start (PR #11); a manually-sent M18/M84 (e.g. the Disable Steppers button above) clears the controller's `is_homed` flag, since it cuts holding torque the same way.
- Raw G-code sender with log window.

### Tab 6: Processing
- Folder queue (add/remove/clear) of experiment output directories.
- Output options: PNG image sequence, video (MP4 + VFR MKV), or both.
- Per-well and overall progress bars, scrolling log.
- **Verified working on hardware** end-to-end (batch `.npy` → images/video), including the auto-process hookup from the Experiment tab.

---

## 3. Capture Modes

### Image
Single still per well (JPG/PNG/TIF). Written to `<exp_dir>/`.

### Raw Burst (primary scientific mode)
Max-rate raw Bayer sensor data, all of a well's frames stacked into **one** memory-mapped `.npy` array (`(n_frames, H, W)`, written incrementally via `numpy.lib.format.open_memmap()` — not per-frame files as in earlier versions of this doc). No encoding overhead. Per-frame timestamps via `time.perf_counter()`, taken after the frame is in hand (not before capture is requested). Sidecar `*_metadata.json` written alongside the stack in `raw/` subdir, plus a `camera_meta.json` written once per experiment (backend, bit depth, Bayer pattern, gain, exposure, fps).

`delay_per_well` (`ExperimentRunner.run()`, default 1.0s) is the settle time between arriving at a well and starting capture — verified sufficient (no motion artifacts) against the 2026-07-01 4-well hardware test. PlayerOne capture rate is exposure-bound and now lands within noise of that ceiling (e.g. ~50fps at 20ms exposure, ~94fps at 10ms) — see § 9 Known Issues for the full investigation history.

**Finalize (flush + trim) runs in a backgrounded spawn subprocess, not inline (PR #14):** each well's capture returns as soon as its frames are in memory; `stack.flush()` (msync to disk) and `_trim_raw_stack()` are handed off to `_finalize_raw_burst_process()`, a `multiprocessing.get_context("spawn").Process` that overlaps with the next well's movement/capture instead of blocking it. `spawn` (not `fork`) is required because the parent process holds live camera/motion connections that `fork` would duplicate into the child. The ETA (`ExperimentRunner.eta_finish_time`, § 2 Tab 4) accounts for this: `_estimate_finalize_time_s()` mirrors the same preallocation-sizing formula as the stack itself and divides by a measured-bandwidth constant (700MB/s), so the estimate scales with resolution/fps instead of using one flat number — verified within 5% of measured finalize time across three different resolution/fps combinations on real hardware. The very last well's finalize has no next well to overlap with, so `run()`'s `finally:` block still waits for it after "Experiment finished." is logged — this is a known, accounted-for tail cost, not a bug. A related bug this surfaced and fixed: the app's log `FileHandler` was opened once in `mode="w"` for the whole session, which doesn't set `O_APPEND` — every finalize subprocess's own `mode="a"` log write could land at a stale offset the main process's next write would silently overwrite. Fixed by truncating the log once at startup, then opening the long-lived handler in `mode="a"` so every writer (main process and every subprocess) is `O_APPEND`-consistent.

**Stacked-array format (as of 2026-07-17):** the array is preallocated to `total_duration_s × fps_ceiling_est × RAW_BURST_FPS_MARGIN` rows (`experiment.py`), where `fps_ceiling_est` is derived per-burst from the camera's current exposure setting (`1e6 / exposure_us` — fps is confirmed exposure-bound, see § 9) rather than one flat guess across all exposures, since true achieved fps still isn't known ahead of capture and the array's shape must be fixed at creation. Earlier revisions of this doc claimed unwritten trailing rows were sparse (no real disk cost); that turned out to be false in practice — a preallocated file is fully materialized on disk, confirmed via `stat` (e.g. a 30s well sized for a 150fps ceiling but capturing at ~30fps was 5.59GB on disk for ~1.12GB of real data). `_trim_raw_stack()` now truncates each `stack.npy` down to its real `frames_captured` size immediately after that well's capture finishes — a cheap in-place header rewrite + `os.truncate()`, not a data copy, since frames are laid out contiguously and the unused rows are always a clean tail. `frames_captured` in `*_metadata.json` remains the authoritative frame count regardless (older, untrimmed captures may still have an oversized `.shape[0]`).

Output layout:
```
<exp_dir>/
  raw/
    camera_meta.json
    A1_<ts>_stack.npy        ← one memory-mapped (n_frames, H, W) array for the whole well
    A1_<ts>_frames.jsonl     ← one JSON line per frame, appended as captured (crash-resilient sidecar)
    A1_<ts>_metadata.json    ← frames_file, frames[], laser_events[], fps_average, duration_actual_s
  <ts>_<name>_points.csv
```

Metadata `frames[]` entry: `{frame_index, time_offset_s}` — individual per-frame timestamp, not averaged. (No `"file"` key — that only exists in the pre-2026-07-06 per-frame-file format, e.g. the 2026-07-01 test dataset; `postprocess.py` still reads that old format too, see § 4.)

### Laser Integration
In Raw Burst mode, "Use Laser" splits capture into three continuous-recording phases: Pre → Laser ON → Post, all within a single uninterrupted burst. `laser_events[]` in the metadata records each state transition with `{time_offset_s, state, frame_index}`. Confirmed accurate on real hardware.

---

## 4. Post-Processing Pipeline (`robocam/postprocess.py`, used by both `ui/processing_panel.py` and `scripts/reconstruct_vfr.py`)

Single-pass pipeline over a well's frames:

1. **Load** the well's `*_metadata.json` and the experiment's shared `camera_meta.json`. If `frames_file` is present (current format), open that one stacked array with `np.load(..., mmap_mode="r")` and index into it by `frame_index` — nothing is loaded into RAM until a frame is actually needed. If absent (pre-2026-07-06 data, e.g. the 2026-07-01 test dataset), fall back to opening each frame's individual `.npy` file named in `frames[].file`, unchanged from before.
2. **Debayer** using the Bayer pattern from `camera_meta.json` (RGGB/BGGR/GRBG/GBRG → BGR via the matching `cv2.COLOR_BAYER_*2BGR` code, falling back to RGGB if unspecified; pass-through for mono sensors). `>8`-bit sensor data is scaled down to `uint8` first.
3. **Save clean PNG** to `images/<well>/` — no overlay, suitable for object tracking.
   - Filename: `<well>_<idx>_<ms>ms_laser-[on|off].png`
4. **Add laser asterisk** overlay (top-right, white fill + black outline) on a copy for the video frames.
5. **Encode VFR MKV** — per-frame PTS from `time_offset_s × 90_000` ticks (90 kHz time base, `bframes=0`).
6. **Encode constant-fps MP4** — H.264 baseline, `bframes=0`, sequential PTS; compatible with Pi hardware decode.

Output:
```
<exp_dir>/
  images/
    A1/
      A1_00000_000006ms_laser-off.png
      A1_00152_005003ms_laser-on.png
    A2/
      ...
  videos/
    A1_<exp_ts>_vfr.mkv    ← VFR archival, accurate timing
    A1_<exp_ts>.mp4         ← constant fps, Pi-friendly display
```

CLI: `python scripts/reconstruct_vfr.py <exp_dir/> [--codec ffv1] [--crf 18] [--mono] [--no-video] [--no-images]`

GUI: Processing tab — verified working on hardware.

---

## 5. Output Directory Configuration

Default: `outputs/` (relative to project root). User can set any path in the Experiment tab via **Browse…**. The path is saved to `config/default_config.json` under `paths.output_dir` and applied to the live runner without restart.

Can also be set via CLI: `python -m robocam config set paths.output_dir /mnt/ssd/outputs`

---

## 6. Calibration File Format

Saved to `config/calibrations/<name>.json`. Contains both raw input and pre-computed well positions:
```json
{
  "corners": {"ul": [x,y,z], "ll": [...], "ur": [...], "lr": [...]},
  "cols": 12, "rows": 8, "pattern": "raster", "name": "...",
  "interpolated_positions": [[x,y,z], ...],
  "labels": ["A1", "A2", ...]
}
```
The experiment panel loads `interpolated_positions`/`labels` directly or falls back to computing them from `corners`/`cols`/`rows` for legacy files.

---

## 7. Setup Scripts

- `setup.sh`: Creates `.venv` with `--system-site-packages` (Pi inherits `libcamera`), installs pip deps including `av` (PyAV), runs `install_playerone_sdk.py`.
- `install_playerone_sdk.py`: Downloads Player One Linux SDK, extracts `.so` for aarch64/arm32, patches Python wrapper.
- `start_robocam.sh`: Activates venv and launches `robocam31.py`.

---

## 8. Downstream Analysis / Tracking Pipeline (external, not yet integrated into this repo)

Frames captured by RoboCam eventually feed a separate cell-tracking pipeline (not yet shared into this repo as of 2026-07-03, expected soon):

- Frames are cropped to each circular well.
- Tracking runs on darkfield images: white = cells, black = background. Contour detection is used to find/count them.
- **Legacy input path was video**: an older (pre-3.1) version of this pipeline decoded an encoded video file frame-by-frame before tracking. That decode step is now redundant — RoboCam 3.1's raw-burst mode writes each well's frames directly as one memory-mapped `.npy` array (see § 3, changed 2026-07-06 from one file per frame to this stacked format specifically to make transport/consumption on another machine easier), so video is no longer the only available frame source. Whether the legacy pipeline can consume that array (or the cropped PNGs from post-processing, see § 4) directly instead of round-tripping through an encoded video depends on that pipeline's actual input assumptions — open question until it's shared and reviewed.

**Bit depth (8-bit vs 16-bit) and tracking:** capture currently uses `POA_RAW8` (8-bit, 256 levels — see § 3 and `docs/recording_modes.md`), chosen for fps/bandwidth headroom. For a high-contrast binary threshold + contour pipeline like this one, bit depth is unlikely to be the limiting factor for segmentation quality — SNR and illumination consistency matter more than quantization fineness when the two populations (cell vs. background) are well separated. 16-bit would only be worth the 2x bandwidth/storage cost (which directly fights the fps ceiling investigation in § 9) if intensity-weighted tracking is needed, or if visible banding/stair-stepping appears at cell-threshold boundaries in current 8-bit frames — check for that before switching.

---

## 9. Known Issues / Roadmap

| Status | Item |
|---|---|
| **Resolved, hardware-confirmed 2026-07-08** | PlayerOne (Mars 662M) raw-burst fps was stuck at ~30fps regardless of the camera's advertised 90-120fps (measured 29.95-29.98fps across the 2026-07-01 test dataset). Root cause was jitter/overhead in the capture loop, not a real ceiling — the fixes below (queue/writer-thread decoupling, direct blocking `GetImageData`, preallocated frame buffer, cross-tab lock-contention fix) brought real hardware to within noise of the pure exposure-bound ceiling: 20ms exposure → 451 frames/9s = **50.11fps** (theoretical ceiling 50fps, ~0.04ms/frame overhead); 10ms exposure → 282 frames/3s = **94fps** (theoretical ceiling 100fps, ~0.64ms/frame overhead). fps is now understood to be purely exposure-bound (`fps ≈ 1000/exposure_ms`); confirmed 2026-07-17 by removing the old independent `fps_limit`/`set_fps`/`get_fps` machinery entirely and replacing it with the Calibration tab's Target FPS field (now further decoupled from Exposure as a software pacing cap — see the Target FPS row below). Root-cause detail kept for reference: (1) exposure was hardcoded to 20ms at init, capping max fps to 50 before any overhead; (2) `POA_HQI`, `POA_USB_BANDWIDTH_LIMIT`, and sensor-mode selection were exposed as live UI controls (Calibration tab → Camera Controls) to test as ceiling levers but turned out not to be the bottleneck — HQI off/bandwidth 100/offset 0/0 selectable sensor modes was the confirmed-working hardware configuration; (3) jitter fixes: `_write_raw_burst()` (`experiment.py`) runs capture (producer) and `np.save()`+JSONL-append (writer thread) on separate threads joined by a bounded `queue.Queue` (`RAW_BURST_QUEUE_MAXSIZE`), `get_raw_frame()` (`camera.py`) calls `GetImageData()` directly instead of polling `ImageReady()` in a 5ms sleep loop, and the per-frame buffer is preallocated once instead of reallocated every call. Also added: `Camera.get_capture_stats()`/`reset_capture_stats()` (folded into raw-burst metadata as `capture_failures`), `Camera.get_dropped_frames_count()` (`sdk_dropped_frames`), a crash-resilient `<well>_<ts>_frames.jsonl` sidecar, an abort-after-`MAX_CONSECUTIVE_CAPTURE_FAILURES`-(50) safety net for a real camera disconnect, and centralized live-preview grabber-pausing (`ui/main_window.py._set_grabbers_paused()`) so the Calibration/Manual Control tabs' preview threads stop contending for `Camera._sdk_lock` during a raw-burst run. Stacked `.npy` array format also verified end-to-end (saved + processed correctly) on real hardware for the first time on 2026-07-08. **Note:** this row described the writer as a background *thread*; that finalize step was later found to never actually overlap (a Python thread calling a non-GIL-releasing flush stalls the whole process) and was replaced with a spawn subprocess — see the "raw-burst throughput stall" row below. |
| Fixed & visually confirmed 2026-07-08 | `get_camera_meta()`'s PlayerOne branch hardcoded `"bayer_pattern": "RGGB"` regardless of the actual camera, and the Mars 662M is a **monochrome** sensor — meaning every PlayerOne capture was being run through a Bayer color-interpolation demosaic instead of the correct mono pass-through, producing color-interpolation artifacts rather than clean grayscale. `_init_playerone()` (`camera.py`) now reads `isColorCamera` from `GetCameraProperties()` and sets `self._playerone_bayer_pattern` to `"mono"` if false, else maps `bayerPattern_` to its real string. Confirmed on hardware 2026-07-08: clean grayscale output, no color-interpolation artifacts. |
| **Resolved, hardware-verified (5 runs) — PR #14** | Raw-burst throughput was still ~25-36% below target at high fps/resolution even after the above (76fps achieved vs. 125fps target, zero dropped frames) — traced to periodic `stack.flush()` calls blocking the GIL for the duration of the underlying `msync` (proved standalone; disk bandwidth ruled out). Thread-based overlap of the flush was tried first but hardware showed it never actually worked (a background thread calling a non-GIL-releasing function still stalls the whole process) — fixed by moving the finalize step (`stack.flush()` + `_trim_raw_stack()`) into a `multiprocessing` **spawn** subprocess (`_finalize_raw_burst_process()`, not `fork`, since the parent holds live hardware connections `fork` would duplicate into the child) so it genuinely overlaps with the next well's movement/capture. Final hardware run: 157.96s actual vs. 158s estimated capture-complete time (0.03% error), "Moving to next well" firing 10ms after capture ends. See § 3 for the current finalize architecture and § "Raw Burst" ETA details below. |
| Bug (open, hypothesis identified 2026-07-06, unverified — needs Pi camera hardware) | Raw burst capture on the Pi camera (picamera2 backend) is not producing correct output — something between `Camera.get_raw_frame()` and the `.npy` → BGR debayer conversion in `postprocess.npy_to_bgr()` is wrong. **Leading hypothesis: CSI-2 packed raw format never gets unpacked.** `_init_picam2()` (`camera.py:251`) reads the actual raw format libcamera picked — the code's own example comment shows `"SRGGB10_CSI2P"`. The bit-depth extraction (`camera.py:253-258`) pulls out the `10` but discards the `_CSI2P` suffix entirely, which is the important part: CSI-2 **Packed** is a real, specific bit-packed layout (for 10-bit: every 4 pixels packed into 5 bytes — 4 bytes of top-8-bits-per-pixel plus 1 byte holding all four 2-bit remainders — not "one uint16 per pixel"). `get_raw_frame()`'s picamera2 branch (`camera.py:592-595`) just returns `self.picam2.capture_array("raw")` with a comment claiming it's "uint16 array at native sensor resolution" — if `capture_array()` actually hands back the packed bytes as-is (doesn't auto-unpack), then the array's `dtype`/`shape` don't correspond to a clean `(H, W)` pixel grid at all (the "width" would be a packed byte stride, not the real pixel count), and `postprocess.npy_to_bgr()`'s scaling (`arr.astype(float32)/max_val*255`) + OpenCV Bayer demosaic then run on bit-scrambled data relative to true pixel intensity — producing structured garbage (diagonal streaking / periodic artifacts tied to the packing period), which matches "debayering not successful" better than a subtle scaling bug would. **Not confirmed**: some picamera2 versions auto-unpack packed raw formats inside `capture_array()` as a convenience, in which case this isn't the bug — couldn't verify further since `picamera2` isn't installable in this dev sandbox (Pi-specific, needs libcamera) and no Pi camera is available right now. **Test plan for next Pi session**: right after `capture_array("raw")`, print `arr.dtype` and `arr.shape[1]`, compare against `self.resolution[0]` (expected pixel width) — a dtype other than `uint16`, or a width that doesn't cleanly match, confirms the packing mismatch. Fix would be either manually unpacking the CSI2P bytes before saving, or requesting an explicitly unpacked raw format at configure time if the sensor driver supports it. PlayerOne backend path is a separate camera/pipeline and has been used successfully for full laser-timed experiment runs (though see the mono/bayer-detection fix above — that verification also predates today's fix). |
| Bug (fixed in software, unverified — Pi camera untestable right now, also deferred to 2026-07-06) | Pi camera (picamera2) raw-burst fps measured at only ~15fps on real hardware (user-reported), worse than even the PlayerOne's ~30fps deficit. Root cause: **auto-exposure/auto-gain was never disabled at connect time.** `_init_picam2()` (`camera.py`) started the camera and cached `self._picam2_exposure_us = 20000` as a Python-side number but never pushed it to hardware or called `set_controls({"AeEnable": False})` — `AeEnable` only got disabled inside `set_exposure()`/`set_gain()`, which only fire if the user manually hits Apply on the Calibration tab first. Left running full AE/AGC, the camera was chasing a "properly exposed" average brightness for what is intentionally a **darkfield (mostly-black) scene** — AE algorithms target mid-range brightness, so a near-black scene drives exposure time (and gain) up trying to brighten it, directly capping fps (e.g. ~65ms exposure alone would explain ~15fps). Fixed: `_init_picam2()` now explicitly calls `set_controls({"ExposureTime", "AnalogueGain", "AeEnable": False})` at connect, matching `_init_playerone()`'s explicit `SetExp(...,False)`/`SetGain(...,False)` pattern. Added full tunability parity with the PlayerOne controls: `Camera.get_ae_enabled()`/`set_ae_enabled()`, an "Auto Exposure" checkbox in Calibration tab → Camera Controls (shown/enabled only for the picamera2 backend, off by default, session-persisted), and `ae_enabled` now recorded in `camera_meta.json`. Exposure/gain controls already applied generically to both backends before this fix — only the init-time default and the explicit AE toggle were missing. **Separate, not yet addressed**: `_init_picam2()` also configures a full-resolution `main` RGB888 stream alongside `raw` (`camera.py`) — during raw-burst capture nothing reads `main` (confirmed unused thanks to the grabber-pause fix above), so the ISP is doing wasted per-frame work converting/scaling a stream nobody consumes; shrinking or dynamically dropping `main` during bursts is a secondary, still-open fps lever. Verified only via syntax/offscreen-GUI checks — real timing validation needs the Pi camera, which is also unavailable right now. |
| Untested | Klipper motion backend is implemented (Moonraker HTTP API) but has not yet been exercised on real Klipper hardware — only Marlin has been run end-to-end. |
| Pending | Z-hop during experiment travel — single `G0` command moves X/Y/Z simultaneously; collision risk if lens is close to plate walls |
| Untested | Motion Profiles tab (feed-rate/acceleration/jerk, Marlin only — see § 2 Tab 2) implemented but only exercised in `simulate=True` mode; needs a real Marlin printer to confirm `M503` parsing and `M203`/`M201`/`M205`/`M500` round-trip against actual firmware output formatting. |
| Planned | Temperature control widgets |
| Planned | Extruder as pump/dispenser |
| Untested (unit-tested, no real-hardware verification yet) | Exposure and Target FPS (Calibration tab → Camera Controls) are now independent instead of hard-linked at `fps = 1000/exposure_ms` — a short exposure can be paired with a deliberately slow target fps (e.g. darkfield well scanning without over-illuminating). Neither field auto-changes the other; a red conflict warning + "Rectify" button appears only when Target FPS is requested above what the current exposure can physically achieve (`_check_fps_conflict()`/`_rectify_fps_conflict()` in `ui/calibration_panel.py`). `Camera.get_target_fps()`/`set_target_fps()` (`camera.py`) store this as a software pacing cap (not an SDK/hardware control); `ExperimentRunner._write_raw_burst()` (`experiment.py`) now sleeps between frames to hold the raw-burst capture rate at the target when it's lower than the exposure-derived ceiling, and sizes its preallocated frame buffer off `min(exposure_ceiling, target_fps)` accordingly. A target fps at or above the ceiling is a no-op fallback to the old uncapped-by-exposure-only behavior. Covered by `tests/test_experiment.py::TestTargetFpsPacing` (uncapped / paced-down / conflict-value-has-no-effect cases, via a fake camera). Not yet exercised on real PlayerOne/Pi hardware — verify actual paced fps_average and jitter match the target on next hardware session. **Follow-ups (PR #8, #9, UI-only, offscreen-Qt-verified):** Rectify originally always lowered Target FPS to the exposure-derived max, even when the conflict was caused by the user editing Target FPS itself — it now adjusts whichever field the user did *not* just edit (tracked via `_last_edited_field`, guarded against session-load/hardware-refresh/Rectify's-own-writes stealing priority), so an edit is never silently undone. Separately, when Rectify lowers Exposure to satisfy a requested Target FPS, the floor to a whole-ms `QSpinBox` value generally doesn't land exactly on the requested fps (e.g. 30fps → 33ms floor → true max ≈30.3fps) — Rectify now also snaps the displayed Target FPS to the exact achievable value at the new integer exposure, so both fields always agree with what capture pacing will actually do. |
| Verified (screenshots, PR #7 + #10) | Calibration tab live preview has a toggleable center crosshair (lines + circle) for aligning a well under the camera before recording a corner — radius is sized as a % of frame height (not raw px) so it stays visually consistent across resolutions/window sizes, adjustable via a spinbox and a synced `QSlider` (PR #10). |
| Untested on real hardware | Auto-home, keep-steppers-powered, and ETA logging for experiments (PR #11): `MotionController` sends `M18 S0` on connect to disable Marlin's default idle-timeout stepper de-energize (steppers hold torque for the life of the connection so an axis can't drift while parked); `ExperimentRunner.run()` now auto-homes if `motion.is_homed` is false before the well loop starts; `send_raw()` clears `is_homed` when a manual M18/M84 is sent (e.g. Manual Control's "Disable Steppers" button), since that cuts holding torque the same way and position can no longer be trusted. Per-stage logging (homing/moving/dwelling/recording/laser) is bridged into a scrolling "Experiment log" box in the panel (same `_QtLogHandler` pattern as the Setup tab's printer-connect log), alongside a live "ETA: MM:SS" countdown computed from the motion profile's feed-rate/accel (trapezoidal per-axis move-time estimate) plus exact dwell/capture durations — turns red and goes negative on overrun, shows "unavailable" on backends without a motion profile (Klipper). See below for how PR #14 tightened the underlying capture/finalize time estimates this ETA depends on. |
| **Hardware-verified — PR #14** | Image-mode capture-time ETA (`_estimate_image_capture_time_s()`, `experiment.py`) is now format-aware, resolution-aware, and queries exposure live rather than baking any of those into a flat constant. History: the original flat `IMAGE_CAPTURE_TIME_ESTIMATE_S = 0.3` had never been hardware-validated (every prior archived experiment was raw-burst) — a 24-well JPEG run measured ~0.055s/well actual, 6x lower than the guess. Image mode's default format was then switched from JPEG to **PNG** (lossless, no compression artifacts; speed tradeoff deferred to post-processing), which made the JPEG-only estimate wrong for the new default — fixed by measuring PNG/TIFF/JPEG separately (jpg ~0.054s, tif ~0.110s, png ~0.140s per well at 1936x1100) and then splitting each format's per-well cost into a resolution-independent `IMAGE_CAPTURE_BASE_OVERHEAD_S` (0.0066s, fixed exposure/SDK/call overhead minus the known exposure at calibration time) plus a live `camera.get_exposure()` term plus a per-pixel encode/write rate (`IMAGE_CAPTURE_PER_PIXEL_S_BY_FORMAT`), the same fixed+scaling shape `_estimate_finalize_time_s()` (below) already used for raw mode. Verified against 5 hardware runs across two resolutions and three formats, all within ~2% of measured. |
| **Hardware-verified — PR #12** | ETA accuracy and homing-safety audit, found iteratively against real hardware logs. **ETA**: moves were sent as bare `G0`/`G1` with no `F`, so Marlin used whatever feed rate was last active instead of the Motion Profiles tab's `M203` ceiling (fixed: cache the profile, send explicit `F`); the same gap existed for acceleration (`M201` is also just a ceiling — actual acceleration comes from `M204`, which was never sent; fixed via `_apply_travel_accel()` sending `M204 T<min(max_accel_x, max_accel_y)>`); a remaining flat ~0.3s/move gap was three fixed ~0.1s serial round-trips per move (`G90`, `G0`'s ack, `M114`), now accounted for via `MOVE_COMMAND_OVERHEAD_S = 0.3` (verified within ~10-15ms of actual); and all five UI move handlers were calling `mc.update_position()` right after a move that already syncs position internally, doubling the `M114` query for nothing — removed. **Homing safety**: `is_homed` clearing on a manual M18/M84 is now durable across a restart (`send_raw()` sends `G92 X0 Y0` to overwrite Marlin's own position counter with the same not-homed sentinel `connect()`'s heuristic checks for); an audit found several absolute-move UI paths that never checked `is_homed` before this — Calibration tab's well-map click, `_set_corner()`, and Manual Control's `_move_well()`/`_goto()` — all now warn-and-block when unhomed, with steppers-disabled called out as a possible cause in the warning text alongside an unhomed session. Raw G-code Sender now shows a bold "⚠ WARNING" that it bypasses every safety check by design. |
| Untested on real hardware | Fullscreen **Demo Mode** (Manual Control tab): a separate `QMainWindow` for driving the rig from a distance/kiosk-style — well navigation and laser control, arrow-key jogging (uppercase axis keys), on-screen keyboard-shortcut hints, nav buttons disabled at grid edges, no on-screen exit button (keyboard-only exit). Implemented across several small direct-to-master commits rather than one PR; only exercised via offscreen/simulate-mode checks so far. |
