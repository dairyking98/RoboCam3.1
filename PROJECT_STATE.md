# RoboCam 3.1 — Project State & AI Handoff Document

This document describes the exact architecture, UI layout, and feature set of the RoboCam 3.1 repository as of the latest commit. It is intended for both user reference and as a fast-resume context for any future AI agent sessions.

## 1. Core Architecture

RoboCam 3.1 is a pure Python desktop application built with `tkinter` and `ttk`. It has no web server or browser dependencies.

### Key Modules (`robocam/`)
- `robocam31.py`: The main entry point and UI definition.
- `camera.py`: The camera abstraction layer. Prioritizes the Player One SDK (patched for Linux via `pyPOACamera.py`), falls back to `Picamera2`, then `cv2`. Handles exposure, gain, and dynamic resolution polling. Includes a thread lock to prevent SDK crashes during simultaneous preview/capture.
- `motion.py`: Abstract motion controller supporting `MarlinBackend` (serial USB, robust M400 checking), `KlipperBackend` (Moonraker HTTP API), and `SimulationBackend`. Includes raw G-code sending.
- `calibration.py`: Handles 4-corner bilinear interpolation for well plates (`WellPlate` class) and JSON save/load. Supports Raster and Snake scan patterns.
- `experiment.py`: The experiment engine. Supports three capture modes (Image, Raw .npy burst, MJPG Video) and handles well-by-well movement, stabilization delays, and sidecar metadata logging.
- `peripherals.py`: Defines `LaserController`. Supports 3 modes: `disabled`, `rpi_gpio` (direct `RPi.GPIO` pin control, default BCM 21), and `klipper` (sends `SET_PIN` G-code via Moonraker).
- `config.py`: JSON-backed configuration system (`config/default_config.json`).

## 2. User Interface (4-Tab Layout)

The UI has been split into four distinct tabs to separate configuration from operation.

### Tab 1: Setup
- **Printer Connection**: Backend dropdown (`marlin` / `klipper`), Klipper host IP, and Apply & Reconnect button.
- **Camera Settings**: Exposure slider + ms entry, Gain slider + entry, Resolution dropdown (dynamically populated from SDK).
- **Laser / GPIO Configuration**: Laser Mode dropdown (`disabled`, `rpi_gpio`, `klipper`), RPi GPIO Pin, Klipper ON/OFF G-code entry fields, and Apply button.
- **Camera Preview**: Live feed with crosshair overlay.

### Tab 2: Manual Control
- **Camera Preview**: Live feed.
- **Machine Controls**: Home All Axes, Disable Steppers (`M18`).
- **Jog Controls**: XY/Z jog grid. Step size selection (0.1, 1.0, 10.0, or Custom text entry).
- **Go To Position**: Manual X, Y, Z entry fields and a Go button.
- **Laser Control**: Manual Laser ON / Laser OFF buttons and state label.
- **Manual G-code Sender**: Text entry and log window.

### Tab 3: Calibration
- **Preview**: Live feed with crosshair.
- **Corner Setup**: Set UL, UR, LL, LR buttons. Plate dimensions (Rows/Cols). Pattern dropdown (Raster/Snake).
- **Well Map**: A custom `WellGrid` canvas. Clicking any well calculates its interpolated XYZ position and jogs the printer there immediately.

### Tab 4: Experiment
- **Preview**: Displays the last captured frame during recording (disabled during Fast Raw capture to preserve bandwidth).
- **Settings**: Mode dropdown (`Image`, `Raw .npy`, `Video`).
  - *Image*: Dwell per well, Image format.
  - *Raw / Video*: Dwell per well, Record duration (s).
  - *Use Laser Checkbox*: When checked on Raw/Video, replaces "Record duration" with "Pre-laser", "Laser ON", and "Post-laser" timing fields.
- **Presets**: Save/Load dropdown for experiment parameters.
- **Well Selection**: A `WellGrid` canvas where the user drags to select/deselect specific wells to be included in the run.

## 3. Capture Modes

1. **Image**: Captures a single still image (`.jpg`, `.png`, etc.) per well.
2. **Raw .npy**: Dumps raw Bayer sensor data directly to disk as binary numpy arrays for a specified duration. Captures at maximum possible speed (no debayering overhead). Requires `scripts/post_process_raw.py` to convert to viewable images later.
3. **Video**: Records an MJPG `.avi` file for a specified duration at maximum possible speed.

**Laser Integration**: In Raw or Video mode, checking "Use Laser" splits the capture into three phases: Pre-laser (laser off), Laser ON (laser fires), Post-laser (laser off). The camera records continuously through all three phases to capture the sample's response to stimulation.

## 4. Setup Scripts

- `setup.sh`: Creates a virtual environment with `--system-site-packages` (to inherit `libcamera` on the Pi), installs pip dependencies, and runs `install_playerone_sdk.py`.
- `install_playerone_sdk.py`: Downloads the Player One Linux SDK, extracts the correct `.so` for the architecture (aarch64/arm32), and patches the Python wrapper.
- `start_robocam.sh`: Activates the venv and launches `robocam31.py`.

## 5. Current Status & Known Issues

- **Hardware Control**: Full Marlin and Klipper motion support is implemented. RPi GPIO laser control is fully implemented and wired into the UI and experiment runner.
- **Preview Framerate**: Idle preview runs at max speed. During recording, preview drops to 2 FPS (polling the last written frame) to prevent SDK thread contention. During Raw mode, preview is intentionally disabled.
- **Z-Hop**: Z-hop during travel moves between wells is not currently implemented but may be needed if the lens clearance is too tight against plate walls.
- **Klipper Peripherals**: The `LaserController` supports Klipper `SET_PIN` commands, but further peripheral integration (heaters, fans, extruder-as-pump) remains on the roadmap.
