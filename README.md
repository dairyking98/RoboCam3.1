# RoboCam 3.1

RoboCam 3.1 is a Python desktop application for automated well-plate imaging using a 3D printer motion system and a Player One astronomy camera. It is designed for scientific use on a Raspberry Pi 4, with a PySide6 GUI, headless CLI, and a post-processing pipeline for per-frame image export and variable-frame-rate video reconstruction.

---

## Key Features

| Feature | Description |
|---|---|
| **PySide6 GUI** | Four-tab desktop application: Setup, Manual Control, Calibration, Experiment. |
| **Dual Motion Backends** | **Marlin** (USB/Serial) and **Klipper** (Moonraker HTTP API). Simulation backend for testing without hardware. |
| **Player One Camera** | First-class support for Player One astronomy cameras via the `pyPOACamera` SDK. Auto-detects and falls back to Picamera2 or OpenCV. |
| **4-Corner Calibration** | Bilinear interpolation from four physical corner coordinates. Saves/loads JSON profiles. Well map syncs to the Experiment tab. |
| **Three Capture Modes** | **Image** (single still per well), **Raw .npy** (timed max-rate sensor burst), **Video** (AVI). |
| **Laser Stimulation** | GPIO or Klipper-gcode laser trigger, perfectly timed with timed captures. Pre/ON/Post phases recorded continuously. |
| **Per-Frame Timestamps** | Every raw burst frame is timestamped with µs precision. Actual inter-frame intervals are preserved — not averaged. |
| **VFR Reconstruction** | `scripts/reconstruct_vfr.py` converts `.npy` bursts to per-frame PNGs and two video formats (VFR MKV + constant-fps MP4) in one pass. |
| **Session Persistence** | All experiment parameters, calibration, camera settings restore automatically on next launch. |
| **Homing Safety** | On motion connect, position is checked. If (0, 0, 0), the printer is flagged as not-homed and experiments are blocked until homed. |
| **Headless CLI** | `python -m robocam <command>` for hardware testing without the GUI. |

---

## Project Structure

```
RoboCam3.1/
├── robocam31.py                    # Main GUI entry point
├── robocam/
│   ├── __main__.py                 # Headless CLI (python -m robocam)
│   ├── camera.py                   # Player One / Picamera2 / OpenCV camera handler
│   ├── motion.py                   # Motion backends: Marlin, Klipper, Simulation
│   ├── calibration.py              # WellPlate bilinear interpolation, CalibrationManager
│   ├── experiment.py               # ExperimentRunner: motion, capture, CSV logging
│   ├── peripherals.py              # LaserController: RPi GPIO and Klipper outputs
│   ├── session.py                  # Session persistence (~/.local/share/RoboCam3/session.json)
│   ├── hw_state.py                 # Global hardware singleton (camera, motion, runner)
│   └── config.py                   # JSON-backed config (config/default_config.json)
├── ui/
│   ├── main_window.py              # QMainWindow with four tabs
│   ├── setup_panel.py              # Hardware connection and camera controls
│   ├── manual_control_panel.py     # Jog, go-to, laser, raw G-code
│   ├── calibration_panel.py        # Corner capture, well map, calibration save/load
│   ├── experiment_panel.py         # Experiment configuration and run control
│   ├── camera_widget.py            # Shared live preview (FrameGrabber + LivePreview)
│   └── well_grid.py                # Interactive well selection grid widget
├── scripts/
│   ├── install_playerone_sdk.py    # Downloads and patches Player One SDK for Linux/ARM
│   └── reconstruct_vfr.py          # Post-processing: .npy → images + VFR MKV + display MP4
├── config/
│   └── default_config.json         # Hardware and path configuration
├── outputs/                        # Experiment output folders (configurable in GUI)
├── setup.sh                        # Linux / Raspberry Pi setup script
└── start_robocam.sh                # One-click launcher
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

This creates `.venv`, installs all pip dependencies (including `PyAV` for video encoding), installs `RPi.GPIO` and `picamera2` on Raspberry Pi, and downloads/patches the Player One SDK.

### 3. Launch

```bash
bash start_robocam.sh
```

---

## Usage

### Step 1 — Setup Tab

- **Printer**: Select backend (`marlin` or `klipper`), connect, and home all axes.
- **Camera**: Adjust exposure, gain, and resolution live.
- **Laser**: Choose mode (`disabled`, `rpi_gpio`, `klipper`), set pin or G-code, and apply.

### Step 2 — Manual Control Tab

- Jog X/Y/Z with preset or custom step sizes.
- Go-to by absolute coordinate.
- Manual laser toggle and raw G-code sender.

### Step 3 — Calibration Tab

1. Jog to each of the four physical corners of the well plate.
2. Click **Set** for Upper-Left, Lower-Left, Upper-Right, Lower-Right.
3. Set grid dimensions (e.g., 12 × 8) and scan pattern (Raster or Snake).
4. Click **Save Calibration** — positions are bilinearly interpolated and saved to `config/calibrations/`.
5. Click any well on the visual map to jog there immediately.

### Step 4 — Experiment Tab

1. Enter an experiment name.
2. Select a calibration file.
3. Set the output folder (defaults to `outputs/`; click **Browse…** to save to an SSD or other location).
4. Choose capture mode:
   - **Image**: Single still per well (JPG/PNG/TIF).
   - **Raw .npy**: Max-rate raw sensor burst for a set duration. Produces per-frame timestamped `.npy` files + sidecar metadata JSON.
   - **Video**: MJPG AVI for a set duration.
5. Optionally enable **Use Laser** (Raw and Video modes) to split capture into Pre-laser / Laser ON / Post-laser phases.
6. Select wells on the grid.
7. Click **Start Experiment**.

---

## Output Structure (Raw .npy Mode)

```
outputs/20260625_133324_my_experiment/
  raw/
    A1_20260625_133324_f00000.npy    ← raw sensor frames
    A1_20260625_133324_f00001.npy
    ...
    A1_20260625_133324_metadata.json ← per-frame timestamps, laser events, fps
    A2_20260625_133324_metadata.json
  points.csv                         ← well positions and capture log
```

After running the pipeline script, `images/` and `videos/` are added:

```
  images/
    A1/
      A1_f00000_000006203us_laser-off.png   ← debayered, clean (no overlay)
      A1_f00152_005003994us_laser-on.png
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

Run after an experiment to produce per-frame images and video:

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
A1_f00152_005003994us_laser-on.png
```

**Videos**: The MKV uses per-frame PTS from the sidecar metadata so timing is accurate to 1 ms. The MP4 uses the actual average FPS for smooth playback with an asterisk overlay marking laser-ON frames.

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

1. **Player One** — `pyPOACamera` SDK, detected via `GetCameraCount()`.
2. **Picamera2** — Raspberry Pi HQ camera.
3. **OpenCV** — generic USB webcam fallback.

---

## Dependencies

| Package | Purpose |
|---|---|
| `PySide6` | Desktop GUI |
| `pyserial` | Marlin serial communication |
| `numpy` | Frame buffer handling and raw `.npy` capture |
| `opencv-python` | Frame debayering, image saving, OpenCV fallback |
| `av` | PyAV — VFR video encoding with per-frame PTS |
| `requests` | Klipper Moonraker HTTP API |
| `pillow` | Image utilities |
| `RPi.GPIO` *(Pi only, via setup.sh)* | GPIO laser control |
| `picamera2` *(Pi only, via setup.sh)* | Raspberry Pi HQ camera |
| Player One SDK *(via setup.sh)* | Mars 662M and other Player One cameras |
