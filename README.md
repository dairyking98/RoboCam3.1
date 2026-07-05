# RoboCam 3.1

RoboCam 3.1 is a Python desktop application for automated well-plate imaging using a 3D printer motion system and a Player One astronomy camera (or a Raspberry Pi HQ/Camera Module, or a generic USB webcam). It is designed for scientific use on a Raspberry Pi 4/5, with a PySide6 GUI, a headless CLI, and a post-processing pipeline that turns raw sensor bursts into per-frame images and timing-accurate video.

This is the third rebuild of the platform, following [screamuch/RoboCam](https://github.com/screamuch/RoboCam) and [RoboCam-Suite](https://github.com/dairyking98/RoboCam-Suite)/[RoboCam-Suite2.0](https://github.com/dairyking98/RoboCam-Suite2.0), which in turn were inspired by [FlyCam](https://github.com/E-Lab-SFSU/FlyCam) (Esquerra Lab, SFSU). See [docs/history.md](docs/history.md) for the full lineage.

---

## Key Features

| Feature | Description |
|---|---|
| **PySide6 GUI** | Six-tab desktop application: Setup, Motion Profiles, Calibration, Experiment, Manual Control, Processing. |
| **Dual Motion Backends** | **Marlin** (USB/Serial) and **Klipper** (Moonraker HTTP API). Simulation backend for testing without hardware. |
| **Multi-Camera Support** | Player One astronomy cameras (`pyPOACamera` SDK), Raspberry Pi cameras (Picamera2, true raw Bayer burst), and generic USB webcams (OpenCV). Setup tab enumerates and lets you pick a specific device. |
| **4-Corner Calibration** | Bilinear interpolation from four physical corner coordinates. Saves/loads JSON profiles. Well map syncs live to the Experiment tab. |
| **Two Capture Modes** | **Image** (single still per well) and **Raw Burst** (max-rate timestamped `.npy` sensor frames — the primary scientific capture mode; real-time AVI recording has been removed in favor of post-processing). |
| **Laser Stimulation** | GPIO (`lgpio`, falling back to `RPi.GPIO`) or Klipper-gcode laser trigger, precisely timed within Raw Burst captures. Pre/ON/Post phases recorded continuously in one burst. |
| **Per-Frame Timestamps** | Every raw burst frame is timestamped with `time.perf_counter()` precision. Actual inter-frame intervals are preserved — never averaged. |
| **Post-Processing Pipeline** | The Processing tab (GUI) and `scripts/reconstruct_vfr.py` (CLI) convert `.npy` bursts into per-frame PNGs, a VFR MKV (archival, accurate timing), and a constant-fps MP4 (Pi-friendly playback). |
| **Session Persistence** | All experiment parameters, calibration selection, and camera settings restore automatically on next launch (`~/.local/share/RoboCam3/session.json`). |
| **Homing Safety** | On motion connect, position is checked. If unhomed (0,0,0) or at the firmware park position, the printer is flagged as not-homed and experiments are blocked until "Home All Axes" is run. |
| **Headless CLI** | `python -m robocam <command>` for hardware testing and scripting without the GUI. |

---

## Project Structure

```
RoboCam3.1/
├── robocam31.py                    # Main GUI entry point
├── robocam/
│   ├── __init__.py                 # Empty package marker
│   ├── __main__.py                 # Headless CLI (python -m robocam)
│   ├── camera.py                   # Player One / Picamera2 / OpenCV camera handler
│   ├── motion.py                   # Motion backends: Marlin, Klipper, Simulation
│   ├── calibration.py              # WellPlate bilinear interpolation, CalibrationManager
│   ├── experiment.py                # ExperimentRunner: motion, capture, CSV logging
│   ├── postprocess.py              # .npy burst -> images + VFR MKV + MP4 pipeline core
│   ├── peripherals.py              # LaserController: RPi GPIO (lgpio/RPi.GPIO) and Klipper outputs
│   ├── session.py                  # Session persistence (~/.local/share/RoboCam3/session.json)
│   ├── hw_state.py                 # Global hardware singleton (camera, motion, runner)
│   └── config.py                   # JSON-backed config (config/default_config.json)
├── ui/
│   ├── __init__.py                 # Empty package marker
│   ├── main_window.py              # QMainWindow with six tabs, cross-panel wiring
│   ├── setup_panel.py              # Hardware connection, camera enumeration, laser config
│   ├── motion_profiles_panel.py    # Placeholder tab (feed-rate/accel/jerk — planned)
│   ├── manual_control_panel.py     # Jog, go-to, laser toggle, raw G-code sender
│   ├── calibration_panel.py        # Corner capture, well map, calibration save/load, quick capture
│   ├── experiment_panel.py         # Experiment configuration and run control
│   ├── processing_panel.py         # Batch .npy -> images/video conversion queue + progress
│   ├── camera_widget.py            # Shared live preview (_FrameGrabber + _LivePreview)
│   └── well_grid.py                # Custom-painted well-plate grid widget (navigate/select modes)
├── scripts/
│   ├── install_playerone_sdk.py    # Downloads and patches Player One SDK for Linux/ARM
│   └── reconstruct_vfr.py          # CLI wrapper for robocam/postprocess.py
├── config/
│   └── default_config.json         # Hardware and path configuration (auto-created/updated)
├── tests/
│   ├── test_calibration.py         # WellPlate bilinear interpolation + path generation
│   ├── test_config.py              # Config get/set/deep-update/persistence
│   └── test_cli.py                 # Headless CLI argument parser (no hardware required)
├── docs/
│   ├── recording_modes.md          # Design notes on raw-burst-first capture philosophy
│   └── history.md                  # Project lineage: FlyCam -> screamuch/RoboCam -> RoboCam-Suite -> Suite2.0 -> 3.1
├── outputs/                        # Experiment output folders (configurable in GUI)
├── setup.sh / setup.bat            # Linux/Pi and Windows setup scripts
├── start_robocam.sh / .bat         # One-click launchers
├── requirements.txt                # Core pip dependencies
├── pyproject.toml                  # Packaging metadata, console-script entry points, pytest config
├── CHANGELOG.md                    # Keep-a-Changelog formatted history
├── PROJECT_STATE.md                # Architecture/AI-handoff snapshot (known issues, roadmap)
└── TESTING.md                      # Manual hardware test checklist for live Pi sessions
```

---

## Installation & Setup

### Prerequisites

- Python 3.10+
- Raspberry Pi OS Bookworm (recommended) or any Linux system
- Player One Camera SDK (downloaded automatically by `setup.sh`)

### 1. Clone

```bash
git clone https://github.com/dairyking98/RoboCam3.1.git
cd RoboCam3.1
```

### 2. Setup

```bash
bash setup.sh
```

This creates `.venv` (with `--system-site-packages` on a detected Raspberry Pi, so the venv can see `libcamera`), installs `requirements.txt` (including `PyAV` for video encoding), and — on a Pi — additionally installs `lgpio` (preferred GPIO library, works on both Pi 4 and Pi 5), `RPi.GPIO` (fallback for older Pi OS installs without `lgpio`), and `picamera2`. It finishes by running `scripts/install_playerone_sdk.py` to download and patch the Player One SDK (safe to skip/fail if you don't have a Player One camera).

Windows: run `setup.bat` instead (creates `.venv` and installs `requirements.txt`; Pi-only extras and udev rules do not apply).

### 3. Launch

```bash
bash start_robocam.sh
```

(`start_robocam.bat` on Windows.)

---

## Usage

RoboCam 3.1's main window has six tabs. All tabs except **Experiment** are disabled while an experiment is running.

### Tab 1 — Setup

- **Camera**: "Scan for Cameras" enumerates Player One, Picamera2, and OpenCV devices in a background thread; pick one, choose a resolution and optional FPS cap, then "Apply & Reconnect Camera". If a Player One camera is detected but blocked by USB permissions, an in-app "Install USB Rules" button installs the udev rule and reloads udev without needing to replug the camera.
- **3-D Printer**: Select backend (`marlin` or `klipper`), serial port/baud (Marlin) or host/port (Klipper), then "Apply & Reconnect Printer".
- **Laser / GPIO**: Choose mode (`disabled`, `rpi_gpio`, `klipper`), set the BCM pin or Klipper G-code strings, and apply.
- **Hardware Status**: Live connection/homing indicators, polled every 2s. A warning banner and "Home All Axes" button appear if the printer is connected but not homed.

### Tab 2 — Motion Profiles

Placeholder tab. Will expose feed-rate, acceleration, and jerk settings (`M203`/`M201`/`M204`/`M205`) for both backends — not yet implemented.

### Tab 3 — Calibration

1. Jog to each of the four physical corners of the well plate using the movement pad (with selectable step size) or "Go To Position".
2. Click **Set** for Upper-Left, Lower-Left, Upper-Right, Lower-Right — the well map auto-generates once all four are set.
3. Set grid dimensions (e.g., 12 × 8) and scan pattern (Raster or Snake), then **Update Well Map**.
4. **Update & Save…** writes a calibration JSON (with corners, dimensions, and pre-computed interpolated positions/labels) to `config/calibrations/`. **Load…** restores a saved file.
5. Click any well on the map to jog there immediately.
6. **Quick Capture** lets you grab a single still or record a short raw burst directly from this tab (written to `outputs/quick_capture/`) without running a full experiment.

### Tab 4 — Experiment

1. Enter an experiment name and pick a calibration file (refresh with the ↺ button).
2. Choose capture mode:
   - **Image**: single still per well (JPG/PNG/TIF), dwell time configurable.
   - **Raw Burst**: max-rate raw sensor frames for a set record duration. With **Use Laser** checked, the burst is split into Pre-laser / Laser ON / Post-laser phases, all captured continuously in one burst.
3. Set/browse the output folder (defaults to `outputs/`; persisted to `config/default_config.json` and applied to the live runner immediately, no restart needed).
4. Optionally save/load named **Experiment Presets** (JSON files under `config/experiment_presets/`).
5. Select wells by clicking or dragging on the grid (Check All / Uncheck All / Invert helpers).
6. Check **Auto-process after experiment** to have the Processing tab automatically pick up and convert the output the moment the run finishes.
7. **Start Experiment** (blocked if the printer isn't homed). **Pause/Resume** and **Stop** are available mid-run. The live preview is paused and overlaid with "EXPERIMENT IN PROGRESS" for the whole run.

### Tab 5 — Manual Control

Direct hardware control outside of an experiment: Home All Axes / Disable Steppers (M18), XY/Z jog pad with preset or custom step size, Go-To by absolute coordinate, manual Laser ON/OFF toggle, and a raw G-code sender with a scrolling response log.

### Tab 6 — Processing

Batch-converts one or more experiment folders' `.npy` bursts into PNG image sequences and/or video (MP4 + VFR MKV). Add folders manually, or let the Experiment tab's auto-process feature queue and start a folder automatically. Shows per-well and overall progress bars plus a scrolling log.

---

## Output Structure (Raw Burst Mode)

```
outputs/20260625_133324_my_experiment/
  raw/
    camera_meta.json                     ← written once per experiment (backend, bit depth, bayer pattern, gain, exposure, fps)
    A1_20260625_133324_f00000.npy        ← raw sensor frames
    A1_20260625_133324_f00001.npy
    ...
    A1_20260625_133324_metadata.json     ← per-frame timestamps, laser events, fps_average, duration_actual_s
    A2_20260625_133324_metadata.json
    ...
  20260625_133324_my_experiment_points.csv   ← well positions and capture log
```

After running the post-processing pipeline (Processing tab or `scripts/reconstruct_vfr.py`), `images/` and `videos/` are added:

```
  images/
    A1/
      A1_00000_000006ms_laser-off.png   ← debayered, clean (no overlay)
      A1_00152_005003ms_laser-on.png
      ...
    A2/
      ...
  videos/
    A1_20260625_133324_vfr.mkv   ← VFR archival (accurate per-frame PTS)
    A1_20260625_133324.mp4       ← constant-fps display (H.264 baseline, Pi-friendly)
    A2_20260625_133324_vfr.mkv
    A2_20260625_133324.mp4
```

---

## Post-Processing Pipeline

Run after an experiment (or use the Processing tab) to produce per-frame images and video from the raw `.npy` burst:

```bash
# All wells in an experiment directory
python scripts/reconstruct_vfr.py outputs/20260625_133324_my_experiment/

# Single well
python scripts/reconstruct_vfr.py outputs/exp/raw/A1_20260625_133324_metadata.json

# Images only
python scripts/reconstruct_vfr.py outputs/exp/ --no-video

# Video only
python scripts/reconstruct_vfr.py outputs/exp/ --no-images

# Lossless video
python scripts/reconstruct_vfr.py outputs/exp/ --codec ffv1

# Monochrome sensor (skip Bayer debayer)
python scripts/reconstruct_vfr.py outputs/exp/ --mono
```

**Image filenames** encode frame index, timestamp, and laser state for direct use in tracking pipelines:
```
A1_00152_005003ms_laser-on.png
```

**Videos**: The MKV uses per-frame PTS (90 kHz time base) derived from the sidecar metadata's `time_offset_s`, so timing is accurate to about 1 ms — intended for archival. The MP4 uses the burst's actual average FPS with sequential PTS for smooth, Pi-hardware-decodable playback, with a white/black asterisk overlay marking laser-ON frames.

---

## Headless CLI

Test hardware without launching the GUI:

```bash
source .venv/bin/activate

python -m robocam status
python -m robocam motion pos
python -m robocam motion home
python -m robocam motion move --x 50 --y 50
python -m robocam motion gcode G28
python -m robocam camera info
python -m robocam camera capture --output frame.jpg
python -m robocam config show
python -m robocam config set paths.output_dir /mnt/ssd/outputs

# Simulation mode (no hardware required)
python -m robocam --simulate status
```

---

## Camera Backend Priority

When no explicit backend is requested, `Camera.__init__` auto-detects in this order:

1. **Player One** — `pyPOACamera` SDK, detected via `GetCameraCount()`.
2. **Picamera2** — Raspberry Pi camera, detected via `Picamera2.global_camera_info()`.
3. **OpenCV** — generic USB webcam fallback (`cv2.VideoCapture`).

The Setup tab's camera enumerator lets you override this and pick a specific device/backend explicitly.

---

## Dependencies

| Package | Purpose |
|---|---|
| `PySide6` | Desktop GUI |
| `pyserial` | Marlin serial communication |
| `numpy` | Frame buffer handling and raw `.npy` capture |
| `opencv-python` | Frame debayering, image saving, OpenCV fallback |
| `av` (PyAV) | VFR video encoding with per-frame PTS |
| `requests` | Klipper Moonraker HTTP API |
| `pillow` | Image utilities |
| `lgpio` *(Pi only, via setup.sh)* | Preferred GPIO library — works on Pi 4 and Pi 5 |
| `RPi.GPIO` *(Pi only, via setup.sh)* | Fallback GPIO library for older Pi OS installs without `lgpio` |
| `picamera2` *(Pi only, via setup.sh)* | Raspberry Pi HQ/Camera Module support |
| Player One SDK *(via setup.sh)* | Mars 662M and other Player One cameras |

---

## Testing

`tests/` contains hardware-free unit tests (pytest): calibration bilinear interpolation (`test_calibration.py`), config get/set/persistence (`test_config.py`), and CLI argument parsing (`test_cli.py`). Run with:

```bash
pip install -e ".[dev]"
pytest
```

`TESTING.md` is a separate manual checklist for exercising the app against real hardware on a live Raspberry Pi session (motion connection, homing, camera preview, calibration, full experiment runs, laser timing, etc.) — not automated.

---

## File-by-File Reference

### `robocam31.py`

GUI entry point. Clears theme environment variables that make Qt 6 dropdown popups unreadable on some Linux desktops (`QT_QPA_PLATFORMTHEME`), forces the Fusion style with an explicit light `QPalette` (backgrounds, selection highlight, disabled-state colors) so the UI looks consistent regardless of the system GTK/XDG theme, wires `SIGINT` (Ctrl+C) to close the window cleanly through Qt's normal `closeEvent` path (using a periodic `QTimer` to give the Python signal handler a chance to run inside Qt's C++ event loop), then constructs and shows `ui.main_window.MainWindow`.

### `robocam/__main__.py`

Headless CLI (`python -m robocam ...`), built with `argparse`. Subcommands: `status` (probes camera SDKs, config, serial ports, and does a full motion-controller connect/homed check), `motion pos|home|move|gcode`, `camera info|capture`, `config show|set`. A global `--simulate` flag swaps in `SimulationBackend`/simulated camera so the CLI works without any hardware attached; `--verbose` enables debug logging. Each command builds its own short-lived `MotionController`/`Camera` instance and disconnects/stops it before returning — this module never touches `robocam/hw_state.py`, which is GUI-only.

### `robocam/camera.py`

Camera abstraction layer with three backends behind one `Camera` class:
- **Player One** (`pyPOACamera` SDK): locates the SDK's `python/` directory (checks the project root, `PLAYERONE_SDK_PYTHON` env var, and `~/PlayerOne_Camera_SDK_Linux_*`), patches the SDK's `pyPOACamera.py` in place on first use so its `ctypes.LoadLibrary` call targets `libPlayerOneCamera.so` on Linux instead of the Windows `.dll` it ships with, then opens/inits the camera in continuous "video mode" exposure. All SDK calls are guarded by a `threading.Lock` (`_sdk_lock`) since the UI preview thread and an experiment thread can both want a frame at once.
- **Picamera2**: opens with `create_video_configuration(main=..., raw={})` — a *video* config (not a still config) is required to get burst-rate raw Bayer frames without the inter-frame latency a still config would add. Caches the raw stream's format string, bit depth (parsed out of strings like `SRGGB10_CSI2P`), and Bayer pattern (mapped from libcamera's `ColorFilterArrangement` property) for the `camera_meta.json` sidecar.
- **OpenCV** (`cv2.VideoCapture` with `CAP_V4L2`): generic USB webcam fallback.

Exposes a uniform interface (`get_exposure/set_exposure`, `get_gain/set_gain`, `get_fps/set_fps`, `get_supported_resolutions`, `set_resolution`, `get_frame` for BGR preview/still frames, `get_raw_frame` for the fastest possible raw sensor buffer, `get_camera_meta` for the postprocess sidecar, `stop`). Module-level helpers `get_playerone_sdk_python_path()` and `get_playerone_camera_count()` are also used by the Setup tab's camera enumerator and by `robocam status`.

### `robocam/motion.py`

Three interchangeable motion backends behind a common `MotionBackend` interface (`connect`, `disconnect`, `send_gcode`, `home`, `update_position`, `move_relative`, `move_absolute`, `is_connected`):
- **`MarlinBackend`**: raw serial G-code over USB. Auto-detects the port by scanning `serial.tools.list_ports` for USB/CH340/CH341/Arduino/Marlin/FTDI in the description. `send_gcode` blocks until it sees a line starting with/containing `ok` (or raises on an `error` line, unless `ignore_errors=True`). Movement completion is confirmed with `M400` when the firmware supports it (tested lazily on first move), falling back to a fixed sleep if `M400` isn't supported.
- **`KlipperBackend`**: talks to Moonraker's HTTP API (`/printer/info`, `/printer/gcode/script`, `/printer/objects/query?toolhead`) instead of a serial port.
- **`SimulationBackend`**: in-memory position tracking with `time.sleep` calls standing in for real movement/homing latency — used by `--simulate` and the CLI.

`MotionController` wraps whichever backend is configured (`hardware.motion_backend` in config) and adds a `is_homed` flag: after connecting, if the reported position is exactly `(0,0,0)` (Marlin's power-on default before any `G28`) or `X == Y` (many firmwares park at a coordinate like `(220,220)`, which isn't a real post-home position), the controller is flagged as not homed and the Setup/Experiment tabs block experiments until `home()` is called.

### `robocam/calibration.py`

`WellPlate` takes four corner `(x, y, z)` tuples (Upper-Left, Lower-Left, Upper-Right, Lower-Right) plus a grid width/depth and computes every well's position via bilinear interpolation between the corners, in either Raster (left-to-right every row) or Snake (alternating direction) order. `get_path_with_labels()` also generates spreadsheet-style well labels (A1, A2, ..., Z1, AA1, ...) via a base-26 conversion. `CalibrationManager` is a slightly different, simpler save/load wrapper around the same math (used by `CalibrationManager.save/load`, distinct from — but format-compatible with — the JSON that `ui/calibration_panel.py` writes directly).

### `robocam/experiment.py`

`ExperimentRunner.run()` is the main experiment loop: for each selected well it moves the stage there (`move_absolute`), waits `delay_per_well` seconds to stabilize, captures (either a single still via `camera.get_frame()`, or a raw burst via the internal `_write_raw_burst()`), and appends a row to a per-experiment `points.csv`. In `raw` mode it also writes `camera_meta.json` once (from `camera.get_camera_meta()`) so the post-processing step knows how to debayer later.

`_write_raw_burst()` is the time-critical inner loop: it calls `camera.get_raw_frame()` back-to-back with no sleep, timestamping each frame with `time.perf_counter()` *after* the frame is in hand (not before capture is requested) and saving it immediately as a `.npy` file. If a `LaserController` is passed, it flips the laser on/off at the requested offsets within the same continuous loop and records each transition as a `laser_events` entry — this is what lets Pre/ON/Post all be captured in one uninterrupted burst rather than three separate recordings. The loop also polls `self.running`, which `stop()` clears, and `self.paused`/`resume()` let the outer loop (invoked between wells) pause without killing an in-flight capture. Runs are always driven from a background `QThread` (`ui/experiment_panel.py`'s `_ExperimentThread`), never the Qt main thread.

### `robocam/postprocess.py`

The core of the post-processing pipeline (used by both `scripts/reconstruct_vfr.py` and `ui/processing_panel.py`). `process_well()` does, per well, in one pass over its frames:
1. Load each `.npy` frame and the well's `*_metadata.json` (frame list + laser events) plus the experiment's shared `camera_meta.json` (backend, bit depth, Bayer pattern).
2. `npy_to_bgr()` scales >8-bit sensor data down to `uint8` and debayers with the OpenCV code matching the sensor's actual Bayer pattern (falls back to RGGB if unspecified; passes through for `mono`).
3. Optionally writes a clean PNG per frame (no overlay — suitable for downstream object tracking) named with frame index, microsecond timestamp, and laser on/off state.
4. Optionally encodes two video files via PyAV: a VFR MKV using each frame's real `time_offset_s` converted to a 90 kHz PTS (`bframes=0` for edit-friendliness, accurate archival timing), and a constant-fps MP4 using the burst's actual average FPS with sequential PTS and an asterisk overlay (`draw_laser_indicator()`) burned into laser-ON frames, encoded H.264 baseline profile for compatibility with Pi hardware decoders.

`find_metadata_files()` and `parse_meta_name()` are small path-handling helpers shared by both callers: the former accepts either a single metadata JSON or an experiment directory (searching its `raw/` subfolder) and returns the list of well metadata files plus the experiment root; the latter parses `<well>_<timestamp>_metadata.json` filenames into `(well, exp_timestamp)`.

### `robocam/peripherals.py`

`LaserController` drives a laser/stimulus output in one of three modes: `disabled` (state is tracked/logged but nothing physical happens — useful for keeping timing metadata consistent when no laser is wired up), `rpi_gpio` (tries `lgpio` first — required on Pi 5 since the venv's `RPi.GPIO` is too old for it — and falls back to `RPi.GPIO` for Pi 4/older Pi OS), and `klipper` (sends a `SET_PIN` G-code command through the motion controller's backend, so it requires a `MotionController` to be passed in). `connect()`/`disconnect()` claim and release the GPIO pin (or lgpio handle); `set_laser(bool)` is the operation `ExperimentRunner` calls repeatedly during a burst.

### `robocam/session.py`

`SessionManager` persists UI form state (experiment settings, calibration panel settings) across restarts to a JSON file in the OS-appropriate app-data directory (`~/.local/share/RoboCam3/session.json` on Linux via `XDG_DATA_HOME`, `%APPDATA%\RoboCam3` on Windows, `~/Library/Application Support/RoboCam3` on macOS) — separate from `config/default_config.json`, which holds hardware/path configuration rather than form values. `get(section)` deep-merges stored values over `DEFAULT_SESSION` so new fields added later don't crash on an old session file; `update(section, values)` merges into the in-memory dict without writing to disk; `save()` flushes to disk (called from panel `closeEvent`s and `MainWindow.closeEvent`). A single module-level `session_manager` instance is shared by all panels.

### `robocam/hw_state.py`

A tiny global-singleton module holding the live `Camera`, `MotionController`, and `ExperimentRunner` instances so every UI panel (Setup, Calibration, Experiment, Manual Control) talks to the *same* hardware objects instead of each owning its own. `set_camera()`/`set_motion()` are called by the Setup tab when hardware is (re)connected; `set_motion()` automatically rebuilds the `ExperimentRunner` (since it wraps both a motion controller and a camera) whenever the motion controller changes, and `rebuild_runner()` does the same when only the camera changes. Deliberately has no locking — all access happens on the Qt main thread or via `hw_state.get_*()` calls made from short-lived worker threads that don't mutate the singletons themselves.

### `robocam/config.py`

`Config` is a small JSON-backed settings store layered over a hardcoded `DEFAULT_CONFIG` dict (motion backend, printer/Klipper timeouts, camera defaults, laser defaults, output paths). On construction it loads `config/default_config.json` if present (deep-merging over the defaults) or creates it if not. `get("a.b.c", default)` and `set("a.b.c", value)` use dot-path key traversal; every `set()` immediately rewrites the whole file to disk, so config changes made in the Setup/Experiment tabs (e.g. output directory, laser mode) persist without an explicit "save" step. `get_config()` returns a process-wide singleton (`_global_config`), mirroring the `hw_state` pattern.

### `ui/main_window.py`

`MainWindow` builds the six-tab `QTabWidget` (Setup, Motion Profiles, Calibration, Experiment, Manual Control, Processing) and wires the cross-panel signals that let the tabs act as one coherent app rather than six independent widgets: locks every tab except Experiment while `experiment_started`/unlocks on `experiment_finished`; re-syncs the Experiment tab's well grid whenever the Calibration tab's corners or row/col spinners change; refreshes camera-dependent controls on other tabs when the Setup tab reports a new camera connection; and switches to the Processing tab and queues the just-finished experiment folder when `experiment_data_ready` fires (i.e. when "Auto-process after experiment" was checked). `closeEvent` stops the experiment thread and all live-preview `_FrameGrabber` threads, saves session state, and disconnects the camera/motion controller — this is the only clean-shutdown path, so `robocam31.py` routes `SIGINT` through it rather than exiting directly.

### `ui/camera_widget.py`

Shared live-preview pair used identically by the Calibration, Experiment, and Manual Control tabs (each tab instantiates its own copy so the tabs stay independent). `_FrameGrabber` is a `QThread` polling `hw_state.get_camera()` at a configurable rate (~15 fps), converting each frame to a `QImage` (Picamera2 already returns RGB; other backends are BGR and get `cv2.cvtColor`'d) and emitting `frame_ready`; it also emits `camera_disconnected` the moment `camera.running` goes false after having been true, and supports `set_paused()` so the Experiment tab can stop pulling frames during a raw burst without stopping the thread outright (avoids capture-contention on the camera's SDK lock). `_LivePreview` is the paint widget: shows a dark "Camera Offline" placeholder with no frames, the live scaled frame otherwise, a red "● RECORDING (Preview Paused)" overlay when the grabber is paused, and an amber "EXPERIMENT IN PROGRESS / Preview Paused" overlay (which takes priority) when `set_experiment_running(True)` is active.

### `ui/setup_panel.py`

The largest control surface in the app. Background `QThread`s handle anything that could block the UI: `_CameraEnumerator` probes Picamera2 (`global_camera_info()`), Player One (`GetCameraCount()`/`GetCameraProperties()` per index — including detecting "found but USB permission denied" separately from "not found"), and OpenCV (`VideoCapture` indices 0-3); `_UdevInstaller` copies the Player One SDK's udev rules file to `/etc/udev/rules.d/` via `sudo -n` and reloads udev rules/triggers, so a newly-plugged Player One camera can be granted USB access without a replug or a manual terminal command; `_HomeThread` runs `motion.home()` off the UI thread since homing can take up to 90s.

Builds five group boxes (Camera, 3-D Printer, Laser/GPIO, Hardware Status, Connection) inside a scroll area. `_apply_camera()`/`_apply_printer()`/`_apply_laser()` push the chosen settings into `Config` and, for camera/printer, tear down and rebuild the corresponding `hw_state` singleton (camera reconnect is deliberately delayed 800ms via `QTimer.singleShot` to let the OS release the device handle first). `_refresh_status()` polls every 2s to update the green/red connection labels and the homing warning banner/button.

### `ui/motion_profiles_panel.py`

Single-widget placeholder tab — just a centered label describing planned functionality (reading/writing Marlin `M203`/`M201`/`M204`/`M205` or Klipper equivalents). No logic yet.

### `ui/manual_control_panel.py`

Two-pane (`QSplitter`) layout: live preview on the left, scrollable controls on the right (Machine Controls, Jog, Go-To, Laser, raw G-code sender). All hardware calls run in short-lived daemon `threading.Thread`s (not `QThread`s, unlike the experiment/enumeration workers) so a slow serial round-trip never freezes the UI; the position label is refreshed by a 500ms `QTimer` reading `hw_state.get_motion()` directly rather than via a signal. The manual laser toggle keeps its own `LaserController` instance alive across button presses (reset to `None` on error so the next press re-initializes it) — deliberately separate from the one `ExperimentRunner` constructs internally during a run, so a leftover manual laser session can't collide with GPIO claims made during an experiment.

### `ui/calibration_panel.py`

Three-column (`QSplitter`) layout: live preview, a scrollable stack of control groups (Movement, Camera Controls, Corner Calibration, Plate Dimensions, Save/Load, Quick Capture), and a clickable well map (`WellMapWidget`, wrapping a `WellGrid` in `NAVIGATE` mode). Setting all four corners (`_set_corner`) auto-triggers well-map generation once all four are present; `_compute_well_positions()` re-implements the same bilinear interpolation as `robocam.calibration.WellPlate` inline (kept in sync manually — both must agree since experiment loading falls back to recomputing from raw corners for older calibration files that lack `interpolated_positions`). "Quick Capture" reuses `ExperimentRunner._write_raw_burst()` directly (constructing a throwaway `ExperimentRunner` if `hw_state` doesn't have one yet) to record a short raw burst without going through the full experiment flow — written to `outputs/quick_capture/`. Session state (grid dimensions, pattern, camera exposure/gain, step size, last-loaded calibration path) is persisted through `session_manager` on every meaningful change and restored on tab construction.

### `ui/experiment_panel.py`

Three-column layout: live preview, settings/presets/run controls, and the well-selection grid (`WellGrid` in `SELECT` mode). `_load_cal_positions()` is format-agnostic: it accepts either a `CalibrationManager`-style file (`interpolated_positions`/`labels` already computed) or a `CalibrationPanel`-style file (`corners` dict + `cols`/`rows`, recomputed on the fly via `WellPlate`) so calibrations saved by either code path load correctly. `_MODE_ALIASES` maps old session/preset values (`"Raw .npy"`, `"Video"`) to the current single `"Raw Burst"` mode, so session files and presets saved before that consolidation still load without error.

Starting an experiment (`_start_experiment`) validates a runner exists, the printer is homed, a calibration is selected and has positions, and at least one well is checked; it then pauses the shared `_FrameGrabber` and shows the "EXPERIMENT IN PROGRESS" overlay before handing the actual run off to `_ExperimentThread` (a thin `QThread` wrapper around `ExperimentRunner.run()`), so the run never blocks the Qt event loop. Every settings field auto-saves to `session_manager` on change (`_autosave`) so a crash mid-configuration doesn't lose the operator's setup. When "Auto-process after experiment" is checked and a run finishes, `experiment_data_ready` is emitted with the finished experiment's directory, which `MainWindow` uses to queue it in the Processing tab automatically.

### `ui/processing_panel.py`

Batch post-processing queue: a folder list (add/remove/clear), PNG/video output checkboxes, and progress UI (per-well and overall progress bars, a scrolling log). `_ProcessWorker` (`QThread`) expands every queued folder into individual well metadata-file jobs via `robocam.postprocess.find_metadata_files()`, then calls `robocam.postprocess.process_well()` per well, relaying its `progress_callback(current_frame, total_frames)` calls up through Qt signals to update the frame progress bar live. `queue_folder(path)` is the public entry point `MainWindow` calls for the auto-process feature — it adds the folder and starts processing immediately without any user interaction.

### `ui/well_grid.py`

`WellGrid` is a single custom-painted `QWidget` (no child `QPushButton`s) representing the whole plate grid, in one of two modes: `NAVIGATE` (Calibration tab — click a cell to emit `well_clicked(row, col)`, no selection state) or `SELECT` (Experiment tab — cells have persistent selected/deselected state, click-and-drag paints every cell the cursor passes over to the *opposite* of the first-clicked cell's state, emitting `selection_changed`). Painting the whole grid in one `paintEvent` (rather than one push-button widget per well) was a deliberate choice noted in the module docstring: individual button children don't reliably receive drag-move events from a widget that didn't receive the initial mouse press, which broke click-and-drag well selection in an earlier implementation.

### `scripts/reconstruct_vfr.py`

Thin CLI wrapper (`argparse`) around `robocam.postprocess`: resolves the input path via `find_metadata_files()`, then calls `process_well()` per well with the requested codec/CRF/mono/images/video flags, printing progress to stdout. This is the same code path the GUI's Processing tab uses (via `_ProcessWorker`), so CLI and GUI post-processing always stay in sync.

### `scripts/install_playerone_sdk.py`

Downloads the Player One Camera SDK archive for the current OS (Windows `.zip`, Linux/macOS `.tar.gz`) from player-one-astronomy.com into `PlayerOne_Camera_SDK_Linux_V3.10.0/` at the project root, extracts just `pyPOACamera.py` and the native shared library for the current CPU architecture (preferring `arm64`/`arm32` over `x64` on ARM machines, matching Raspberry Pi), and extracts the udev rules file (Linux) used by the Setup tab's "Install USB Rules" button. It then rewrites the top of `pyPOACamera.py` in place (`_patch_wrapper`) to resolve the native library relative to the wrapper's own file location and to `dlopen` the correct filename per platform (`.dll`/`.dylib`/`.so`) instead of the hardcoded Windows-only loader the SDK ships with — this is what lets the same wrapper file load correctly on a Raspberry Pi. `robocam/camera.py`'s `_ensure_pypoa_patched_for_linux()` performs a lighter-weight version of this same patch defensively, in case an SDK was installed by hand rather than through this script.

### `config/default_config.json`

Auto-created by `robocam.config.Config` on first run (from `Config.DEFAULT_CONFIG`) and rewritten on every `Config.set()` call made anywhere in the app — effectively the live, persisted view of all hardware/path settings (motion backend choice, printer/Klipper connection parameters, camera resolution/fps cap, laser mode/pin/G-code, and `paths.output_dir`/`paths.calibration_dir`). Not meant to be hand-edited except for bootstrapping a fresh install; the GUI is the normal way to change these values.

### `tests/`

Hardware-free pytest suite. `test_calibration.py` checks `WellPlate`'s bilinear interpolation against known corner sets (unit square and realistic mm coordinates) and label generation. `test_config.py` checks `Config.get`/`set` dot-path traversal, deep-merge-over-defaults behavior, and file persistence using a temp file. `test_cli.py` checks `robocam.__main__.build_parser()`'s argument parsing for every subcommand without touching real hardware.

---

## Known Issues / Roadmap

| Status | Item |
|---|---|
| Bug (open) | Raw burst capture on the Pi camera (Picamera2 backend) is not producing correct output — something between `Camera.get_raw_frame()` and the `.npy` → BGR conversion in `postprocess.npy_to_bgr()` is wrong. Needs investigation: check the `raw` stream's actual format/bit-depth from `capture_array("raw")` vs. what `camera_meta.json` (`bit_depth`, `bayer_pattern`) claims, and whether the >8-bit scaling in `npy_to_bgr()` matches the real packed/unpacked pixel format. The Player One backend path is unaffected and has been verified working on real hardware, including full laser-timed experiment runs. |
| Untested | The Klipper motion backend is implemented (Moonraker HTTP API) but has not yet been run against real Klipper hardware — only Marlin has been verified end-to-end so far. |
| Pending | Z-hop during experiment travel — a single `G0` command moves X/Y/Z simultaneously; collision risk if the lens is close to the plate walls. |
| Planned | Motion Profiles tab: feed-rate/acceleration/jerk (`M203`/`M201`/`M204`/`M205`) read/write for both backends. |
| Planned | Temperature control widgets. |
| Planned | Extruder as pump/dispenser. |

**Verified working on real hardware:** Player One raw-burst capture (including laser-timed Pre/ON/Post runs), the Processing tab's batch `.npy` → image/video conversion, `lgpio`-based laser control on Pi 5, the Setup tab's udev USB auto-installer for Player One permissions, and multi-camera enumeration/selection in the Setup tab.

See `CHANGELOG.md` for released/unreleased change history and `PROJECT_STATE.md` for a deeper architecture snapshot intended as AI-agent handoff context.
