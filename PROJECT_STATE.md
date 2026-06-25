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
| `robocam/experiment.py` | `ExperimentRunner`: well-by-well movement, three capture modes, per-frame timestamps, laser timing, CSV + JSON sidecar. |
| `robocam/peripherals.py` | `LaserController`: `disabled`, `rpi_gpio` (BCM 21), `klipper` (SET_PIN G-code). |
| `robocam/session.py` | Session persistence to `~/.local/share/RoboCam3/session.json`. |
| `robocam/hw_state.py` | Global hardware singleton (camera, motion, runner). |
| `robocam/config.py` | JSON-backed configuration (`config/default_config.json`). `Config.set()` auto-saves. |

### Key UI Modules (`ui/`)

| Module | Purpose |
|---|---|
| `ui/main_window.py` | QMainWindow with four tabs; shared `FrameGrabber` thread. |
| `ui/setup_panel.py` | Hardware connection, camera settings, laser config. |
| `ui/manual_control_panel.py` | Jog, go-to, laser toggle, raw G-code sender. |
| `ui/calibration_panel.py` | Corner capture, well map, calibration save/load. |
| `ui/experiment_panel.py` | Experiment configuration, output folder picker, run/stop control. |
| `ui/camera_widget.py` | Shared `FrameGrabber` (QThread) + `_LivePreview` (QPainter). |
| `ui/well_grid.py` | Drag-selectable well grid widget. |

### Scripts (`scripts/`)

| Script | Purpose |
|---|---|
| `scripts/install_playerone_sdk.py` | Downloads and patches Player One SDK for Linux/ARM. |
| `scripts/reconstruct_vfr.py` | Unified post-processing pipeline: `.npy` → images + VFR MKV + display MP4. |

---

## 2. User Interface (4-Tab PySide6 Layout)

### Tab 1: Setup
- Printer backend dropdown (`marlin` / `klipper`), Klipper host, Apply & Reconnect.
- Exposure slider + ms entry, Gain slider + entry, Resolution dropdown (populated from SDK max native resolution).
- Laser Mode (`disabled`, `rpi_gpio`, `klipper`), GPIO pin, Klipper G-code fields, Apply.
- Live camera preview with crosshair overlay.

### Tab 2: Manual Control
- Home All Axes, Disable Steppers (M18).
- XY/Z jog grid. Step size 0.1 / 1.0 / 10.0 mm or custom.
- Go-to by absolute X, Y, Z.
- Manual Laser ON / OFF + state label.
- Raw G-code sender with log window.

### Tab 3: Calibration
- Set UL / LL / UR / LR corner positions by jogging to each and clicking Set.
- Grid dimensions (Rows × Cols), scan pattern (Raster / Snake).
- Well map: click any well to jog to its bilinearly interpolated position immediately.
- Save Calibration — writes `config/calibrations/<name>.json` with both `corners`/`cols`/`rows` and pre-computed `interpolated_positions`/`labels`.

### Tab 4: Experiment
- Experiment name, calibration file selector.
- **Output folder**: label + Browse button. Changes saved to `config/default_config.json` and applied to the live runner immediately.
- Capture mode: Image / Raw .npy / Video.
  - *Image*: format (JPG/PNG/TIF), dwell per well.
  - *Raw / Video*: record duration. With "Use Laser": Pre-laser / Laser ON / Post-laser timing.
- Well selection grid.
- Start / Stop buttons. Status label updated on each state change.
- **Experiment in progress overlay**: amber `"EXPERIMENT IN PROGRESS / Preview Paused"` shown on the camera preview for all capture modes during a run. Red `"● RECORDING (Preview Paused)"` shown in raw/video modes at idle.

---

## 3. Capture Modes

### Image
Single still per well (JPG/PNG/TIF). Written to `<exp_dir>/`.

### Raw .npy (primary scientific mode)
Max-rate raw Bayer sensor data saved as `.npy` files. No encoding overhead. Per-frame timestamps via `time.perf_counter()`. Sidecar `*_metadata.json` written alongside frames in `raw/` subdir.

Output layout:
```
<exp_dir>/
  raw/
    A1_<ts>_f00000.npy
    A1_<ts>_f00001.npy
    ...
    A1_<ts>_metadata.json    ← frames[], laser_events[], fps_average, duration_actual_s
  points.csv
```

Metadata `frames[]` entry: `{frame_index, file, time_offset_s}` — individual per-frame timestamp, not averaged.

### Video (AVI)
MJPG AVI for a set duration. Sidecar `*_metadata.json` includes `frame_timestamps_s[]` (per-frame, not averaged) and `fps_average`.

### Laser Integration
In Raw or Video mode, "Use Laser" splits capture into three continuous-recording phases: Pre → Laser ON → Post. `laser_events[]` in the metadata records each state transition with `{time_offset_s, state, frame_index}`.

---

## 4. Post-Processing Pipeline (`scripts/reconstruct_vfr.py`)

Single-pass pipeline over `.npy` frames per well:

1. **Load** raw `.npy` array.
2. **Debayer** Bayer RGGB → BGR via `cv2.COLOR_BAYER_RG2BGR` (or pass-through for mono sensors).
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
      A1_f00000_000006203us_laser-off.png
      A1_f00152_005003994us_laser-on.png
    A2/
      ...
  videos/
    A1_<exp_ts>_vfr.mkv    ← VFR archival, accurate timing
    A1_<exp_ts>.mp4         ← constant fps, Pi-friendly display
```

CLI: `python scripts/reconstruct_vfr.py <exp_dir/> [--codec ffv1] [--crf 18] [--mono] [--no-video] [--no-images]`

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
| Pending | Z-hop during experiment travel — single `G0` command moves X/Y/Z simultaneously; collision risk if lens is close to plate walls |
| Planned | Temperature control widgets |
| Planned | Extruder as pump/dispenser |
