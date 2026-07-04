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
| `ui/motion_profiles_panel.py` | Placeholder tab (feed-rate/accel/jerk — not yet implemented). |
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
Placeholder only — no controls wired up yet. Intended to expose `M203`/`M201`/`M204`/`M205` (feed-rate/acceleration/jerk) for both backends.

### Tab 3: Calibration
- Set UL / LL / UR / LR corner positions by jogging to each and clicking Set; well map auto-generates once all four are set.
- Grid dimensions (Rows × Cols), scan pattern (Raster / Snake).
- Well map: click any well to jog to its bilinearly interpolated position immediately.
- Save/Load Calibration — writes `config/calibrations/<name>.json` with both `corners` and pre-computed `interpolated_positions`/`labels`.
- Quick Capture: grab a still or short raw burst directly from this tab, outside a full experiment.

### Tab 4: Experiment
- Experiment name, calibration file selector, experiment presets (save/load named JSON configs).
- **Output folder**: label + Browse button. Changes saved to `config/default_config.json` and applied to the live runner immediately.
- Capture mode: **Image** or **Raw Burst** (the former "Raw .npy" and "Video (AVI)" modes were consolidated into Raw Burst; real-time AVI encoding was removed in favor of post-processing).
  - *Image*: format (JPG/PNG/TIF), dwell per well.
  - *Raw Burst*: record duration. With "Use Laser": Pre-laser / Laser ON / Post-laser timing, captured continuously in one burst (**verified working on hardware**, including `laser_events` timing accuracy).
- Well selection grid, with "Auto-process after experiment" checkbox to hand the finished folder straight to the Processing tab.
- Start / Stop / Pause buttons. Status label updated on each state change.
- **Experiment in progress overlay**: amber `"EXPERIMENT IN PROGRESS / Preview Paused"` shown on the camera preview for the whole run.

### Tab 5: Manual Control
- Home All Axes, Disable Steppers (M18).
- XY/Z jog grid. Step size 0.1 / 1.0 / 10.0 mm or custom.
- Go-to by absolute X, Y, Z.
- Manual Laser ON / OFF + state label.
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

`delay_per_well` (`ExperimentRunner.run()`, default 1.0s) is the settle time between arriving at a well and starting capture — verified sufficient (no motion artifacts) against the 2026-07-01 4-well hardware test. PlayerOne effective capture rate is currently ~30fps well short of the camera's 90-120fps spec; see § 9 Known Issues for the open investigation.

**Stacked-array format (as of 2026-07-06):** the array is preallocated to `total_duration_s × RAW_BURST_FPS_CEILING_ESTIMATE` rows (`experiment.py`, ceiling constant comfortably above the camera's advertised 90-120fps max) since true achieved fps isn't known ahead of time and the array's shape must be fixed at creation. Unwritten trailing rows are **sparse** — no real disk cost on ext4/NVMe — and are never trimmed down; `frames_captured` in `*_metadata.json` is the only authoritative frame count, never the array's own `.shape[0]`. **This makes the file a transfer footgun**: a naive `cp`, drag-and-drop, or copy onto a non-sparse-aware filesystem (e.g. the exFAT external drive used for archival) will materialize the full preallocated size. Any packaging/transfer tooling for this data must use sparse-aware copy (`tar --sparse`, `rsync --sparse`, `cp --sparse=always`).

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
| Investigating (open) — jitter fixes implemented, pending hardware verification 2026-07-06 | PlayerOne (Mars 662M) raw-burst capture rate is stuck at ~30fps regardless of the camera's advertised 90-120fps. Measured 29.95-29.98fps across 16 well-captures in the 2026-07-01 test dataset (`fps_average` in each well's `*_metadata.json`), fully consistent across 4 separate runs — not a fluke. Root-caused into two classes: **(A) what caps the ceiling** (still open, needs live hardware — camera unavailable until 2026-07-06) — (1) exposure hardcoded to 20ms at init (`camera.py:169`) caps max fps to 50 before any overhead; (2) `POA_HQI` ("for cameras without DDR (guide camera), reduce frame rate to improve image quality") may default on for this guide-camera-class sensor; (3) `POA_USB_BANDWIDTH_LIMIT` may be throttling; (4) the SDK's `SetSensorMode`/`GetSensorModeCount` API was never used — a faster mode may exist unused at index 0. All four are now exposed as live-adjustable UI controls (Calibration tab → Camera Controls: HQI checkbox, USB Bandwidth spinbox, Offset spinbox, Sensor Mode dropdown — hidden/disabled automatically if the camera reports 0 selectable modes) instead of requiring code edits, and `camera_meta.json` now records all of them (`hqi_enabled`, `usb_bandwidth_limit`, `offset`, `sensor_mode_index`, `sensor_mode_name`) for reproducibility. **(B) jitter/instability under whatever the ceiling is — implemented, verified in `simulate=True` mode, pending real-camera timing validation:** (1) `_write_raw_burst()` (`experiment.py`) now runs capture (producer) and `np.save()`+JSONL-append (consumer/writer thread) on separate threads joined by a bounded `queue.Queue` (`RAW_BURST_QUEUE_MAXSIZE = 128`), so disk I/O no longer blocks frame acquisition timing; the producer blocks rather than drops on a full queue (`queue_full_stalls`/`queue_full_stall_s_total` in metadata report how often/how long), and the queue is fully drained before `_write_raw_burst()` returns on both normal completion and `stop()`, so no already-captured frame is ever lost; (2) `get_raw_frame()` (`camera.py`) no longer manually polls `ImageReady()` in a 5ms Python sleep loop — it calls `GetImageData()` directly with a bounded exposure-derived timeout, letting the SDK's own internal wait handle it; (3) the double per-frame buffer allocation (`np.zeros()` + `.copy()` every call) is fixed by preallocating `self._po_frame_buf` once in `_init_playerone()`/`set_resolution()`. Also added: `Camera.get_capture_stats()`/`reset_capture_stats()` (lock-timeout / SDK-timeout-or-error counts, now folded into raw-burst metadata as `capture_failures`) and `Camera.get_dropped_frames_count()` (wraps the SDK's own `GetDroppedImagesCount`, folded in as `sdk_dropped_frames`) — previously capture failures were silently swallowed with no visibility. A crash-resilient `<well>_<ts>_frames.jsonl` sidecar is now written incrementally as each frame is captured, so a crash/disconnect mid-burst no longer loses all per-frame timing metadata (the final `metadata.json` schema is unchanged for `postprocess.py` compatibility). Two follow-up gaps were caught and fixed in a second pass: a real camera disconnect looked identical to a normal timing miss and would silently "complete" with near-empty data — `_write_raw_burst()` now aborts with a clear `RuntimeError` after `MAX_CONSECUTIVE_CAPTURE_FAILURES` (50) consecutive failed grabs; and the writer thread itself had no exception handling, so a failed `np.save()`/JSONL write (disk full, drive unmounted) would silently kill the thread and leave the producer blocked forever on the next full-queue `put()` — the writer now catches failures, switches to drain-only mode so the producer is never stuck, and the error propagates up through `run()`'s existing exception handling. Separately, **a real cross-tab bug was found and fixed**: `ui/calibration_panel.py` and `ui/manual_control_panel.py` each run their own live-preview `_FrameGrabber` QThread continuously from app launch regardless of visible tab, and only the Experiment tab's own grabber was being paused during a raw-burst run — the other two kept contending for `Camera._sdk_lock` throughout every capture. Pausing is now centralized in `ui/main_window.py._set_grabbers_paused()`, wired to `experiment_panel`'s existing `experiment_started`/`experiment_finished` signals, broadcasting to every panel's grabber. **Verification performed:** `simulate=True` unit-style tests (frames flow through the queue and land on disk correctly, JSONL sidecar matches frame count, `metadata.json` schema unchanged, `stop()` mid-burst drains the queue with zero frame loss, new getters return sane defaults) and an offscreen (`QT_QPA_PLATFORM=offscreen`) GUI launch confirming the new Camera Controls render and the grabber-pause broadcast correctly pauses/resumes all three panels. **Not yet verified:** actual fps/jitter improvement on real hardware, and whether HQI/sensor-mode/USB-bandwidth actually move the needle — all deferred to the 2026-07-06 hardware session. |
| Bug (fixed in software, unverified — camera unavailable until 2026-07-06) | `get_camera_meta()`'s PlayerOne branch hardcoded `"bayer_pattern": "RGGB"` regardless of the actual camera, and the Mars 662M is a **monochrome** sensor (confirmed by user 2026-07-06) — meaning every PlayerOne capture was being run through a Bayer color-interpolation demosaic (`cv2.COLOR_BAYER_RG2BGR` in `postprocess.npy_to_bgr()`) instead of the correct mono pass-through (`cv2.COLOR_GRAY2BGR`), producing color-interpolation artifacts rather than clean grayscale. `_init_playerone()` (`camera.py`) already fetches `props` from `GetCameraProperties()` but only ever read `maxWidth`/`maxHeight`/`cameraModelName` off it — `props.isColorCamera` and `props.bayerPattern_` were fetched and discarded. Fixed: `_init_playerone()` now reads `isColorCamera` and sets `self._playerone_bayer_pattern` to `"mono"` if false, else maps `bayerPattern_` (SDK enum: 0=RGGB/1=BGGR/2=GRBG/3=GBRG) to its real string instead of assuming RGGB; `get_camera_meta()` reports that instead of the hardcoded value. `postprocess.py`'s mono pass-through path already existed and needed no changes. This calls into question the "verified end-to-end on real hardware" note elsewhere in this doc for PlayerOne raw-burst output — that verification predates this fix and may have been looking at demosaic artifacts on a genuinely mono sensor. Re-verify visually on 2026-07-06. |
| Bug (open, unverified fixed) | Raw burst capture on the Pi camera (picamera2 backend) is not producing correct output — something between `Camera.get_raw_frame()` and the `.npy` → BGR debayer conversion in `postprocess.npy_to_bgr()` is wrong. Needs investigation: check `raw` stream format/bit-depth actually returned by `capture_array("raw")` vs. what `camera_meta.json` (`bit_depth`, `bayer_pattern`) claims, and whether the >8-bit scaling in `npy_to_bgr()` matches the real packed/unpacked pixel format. PlayerOne backend path is unaffected and has been used successfully for full laser-timed experiment runs. |
| Untested | Klipper motion backend is implemented (Moonraker HTTP API) but has not yet been exercised on real Klipper hardware — only Marlin has been run end-to-end. |
| Pending | Z-hop during experiment travel — single `G0` command moves X/Y/Z simultaneously; collision risk if lens is close to plate walls |
| Planned | Motion Profiles tab (feed-rate/acceleration/jerk for both backends) |
| Planned | Temperature control widgets |
| Planned | Extruder as pump/dispenser |
