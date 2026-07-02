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
Max-rate raw Bayer sensor data saved as `.npy` files. No encoding overhead. Per-frame timestamps via `time.perf_counter()`, taken after the frame is in hand (not before capture is requested). Sidecar `*_metadata.json` written alongside frames in `raw/` subdir, plus a `camera_meta.json` written once per experiment (backend, bit depth, Bayer pattern, gain, exposure, fps).

Output layout:
```
<exp_dir>/
  raw/
    camera_meta.json
    A1_<ts>_f00000.npy
    A1_<ts>_f00001.npy
    ...
    A1_<ts>_metadata.json    ← frames[], laser_events[], fps_average, duration_actual_s
  <ts>_<name>_points.csv
```

Metadata `frames[]` entry: `{frame_index, file, time_offset_s}` — individual per-frame timestamp, not averaged.

### Laser Integration
In Raw Burst mode, "Use Laser" splits capture into three continuous-recording phases: Pre → Laser ON → Post, all within a single uninterrupted burst. `laser_events[]` in the metadata records each state transition with `{time_offset_s, state, frame_index}`. Confirmed accurate on real hardware.

---

## 4. Post-Processing Pipeline (`robocam/postprocess.py`, used by both `ui/processing_panel.py` and `scripts/reconstruct_vfr.py`)

Single-pass pipeline over `.npy` frames per well:

1. **Load** raw `.npy` array plus the well's `*_metadata.json` and the experiment's shared `camera_meta.json`.
2. **Debayer** using the Bayer pattern from `camera_meta.json` (RGGB/BGGR/GRBG/GBRG → BGR via the matching `cv2.COLOR_BAYER_*2BGR` code, falling back to RGGB if unspecified; pass-through for mono sensors). `>8`-bit sensor data is scaled down to `uint8` first.
3. **Save clean PNG** to `images/<well>/` — no overlay, suitable for object tracking.
   - Filename: `<well>_<idx>_<µs>us_laser-[on|off].png`
4. **Add laser asterisk** overlay (top-right, white fill + black outline) on a copy for the video frames.
5. **Encode VFR MKV** — per-frame PTS from `time_offset_s × 90_000` ticks (90 kHz time base, `bframes=0`).
6. **Encode constant-fps MP4** — H.264 baseline, `bframes=0`, sequential PTS; compatible with Pi hardware decode.

Output:
```
<exp_dir>/
  images/
    A1/
      A1_00000_000006203us_laser-off.png
      A1_00152_005003994us_laser-on.png
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

## 8. Known Issues / Roadmap

| Status | Item |
|---|---|
| Bug (open, unverified fixed) | Raw burst capture on the Pi camera (picamera2 backend) is not producing correct output — something between `Camera.get_raw_frame()` and the `.npy` → BGR debayer conversion in `postprocess.npy_to_bgr()` is wrong. Needs investigation: check `raw` stream format/bit-depth actually returned by `capture_array("raw")` vs. what `camera_meta.json` (`bit_depth`, `bayer_pattern`) claims, and whether the >8-bit scaling in `npy_to_bgr()` matches the real packed/unpacked pixel format. PlayerOne backend path is unaffected and has been used successfully for full laser-timed experiment runs. |
| Untested | Klipper motion backend is implemented (Moonraker HTTP API) but has not yet been exercised on real Klipper hardware — only Marlin has been run end-to-end. |
| Pending | Z-hop during experiment travel — single `G0` command moves X/Y/Z simultaneously; collision risk if lens is close to plate walls |
| Planned | Motion Profiles tab (feed-rate/acceleration/jerk for both backends) |
| Planned | Temperature control widgets |
| Planned | Extruder as pump/dispenser |
