# RoboCam 3.1

RoboCam 3.1 is a Python-only desktop application for automated well-plate imaging using 3D printer mechanics. It combines the simplicity of the original RoboCam Tkinter GUI with the robust hardware drivers, bilinear interpolation math, and Klipper communication architecture from RoboCam-Suite 2.0 — with no web server, no browser, and no external framework required.

---

## Key Features

| Feature | Description |
|---|---|
| **Unified Desktop GUI** | Single Tkinter application with three tabs: Motion & Camera, Calibration, Experiment. No web server required. |
| **Dual Motion Backends** | Switch between **Marlin** (USB/Serial) and **Klipper** (Moonraker HTTP API over network/Tailscale) without restarting. |
| **Player One Camera** | First-class support for Player One Astronomy cameras (Mars 662M, etc.) via the `pyPOACamera` SDK. Auto-detects and falls back to Picamera2 or OpenCV. |
| **Live Camera Controls** | Adjust **Exposure**, **Gain**, and **Resolution** live from the GUI. Resolution list is polled directly from the SDK sensor properties. |
| **4-Corner Calibration** | Bilinear interpolation from four physical corner coordinates accounts for plate rotation and skew. Saves/loads JSON profiles. |
| **Raster & Snake Patterns** | Choose between row-by-row raster scan or alternating snake scan for efficient well-plate traversal. |
| **Two Capture Modes** | Standard `.jpg` capture or **Fast Raw** `.npy` capture (raw sensor buffer, no CPU encoding) for maximum framerate. |
| **Smart Preview Modes** | Live preview when idle; last-frame polling during standard recording; preview disabled during fast raw capture to prevent SDK lock contention. |
| **Automated Experiments** | Full experiment loop with configurable stabilization delay, live status feedback, and CSV data export. |
| **Simulation Mode** | Built-in hardware simulation for UI testing without a printer or camera attached. |
| **Klipper Peripheral Roadmap** | Architecture designed to support laser control, temperature control, and pump dispensing via repurposed printer hardware (see below). |

---

## Project Structure

```
RoboCam3.1/
├── robocam31.py                  # Main application entry point and Tkinter GUI
├── robocam/
│   ├── camera.py                 # Player One / Picamera2 / OpenCV camera handler
│   ├── motion.py                 # Abstract motion backend: Marlin, Klipper, Simulation
│   ├── calibration.py            # WellPlate bilinear interpolation and CalibrationManager
│   ├── experiment.py             # ExperimentRunner: motion, capture, CSV logging
│   └── config.py                 # JSON-based configuration management
├── scripts/
│   ├── install_playerone_sdk.py  # Downloads and patches Player One SDK for Linux/ARM
│   └── post_process_raw.py       # Converts fast raw .npy files to .jpg after experiment
├── config/
│   └── calibrations/             # Saved calibration JSON files
├── outputs/                      # Timestamped experiment output folders
├── setup.sh                      # Linux/macOS/Raspberry Pi setup script
├── setup.bat                     # Windows setup script
└── start_robocam.sh              # One-click launcher script
```

---

## Installation & Setup

### Prerequisites

- Python 3.10 or newer
- Raspberry Pi OS Bookworm (recommended) or any Linux/Windows system
- Player One Camera SDK (downloaded automatically by `setup.sh`)

### 1. Clone the Repository

```bash
git clone https://github.com/dairyking98/RoboCam3.1.git
cd RoboCam3.1
```

### 2. Run the Setup Script

**Linux / Raspberry Pi:**
```bash
bash setup.sh
```

**Windows:**
```bat
setup.bat
```

The setup script performs the following steps:

1. Creates a Python virtual environment (`.venv`). On Raspberry Pi, it uses `--system-site-packages` so that `libcamera` (which cannot be installed via `pip`) remains accessible inside the venv.
2. Installs all pip dependencies from `requirements.txt`.
3. Installs `picamera2` if running on a Raspberry Pi.
4. Runs `scripts/install_playerone_sdk.py`, which downloads the Player One Camera SDK, extracts the correct `.so` library for the detected CPU architecture (aarch64, arm64, or arm32), and patches `pyPOACamera.py` for Linux compatibility.

### 3. Launch the Application

```bash
bash start_robocam.sh
```

Or manually:
```bash
source .venv/bin/activate
python3 robocam31.py
```

---

## Usage

### Step 1 — Motion & Camera Tab

Select the motion backend (`marlin` or `klipper`) from the **Connection Settings** bar at the top of the tab.

- **Marlin**: The app auto-detects the USB serial port by scanning for CH340, FTDI, or Arduino descriptors.
- **Klipper**: Enter the printer's IP address or hostname (e.g., `100.x.x.x` for a Tailscale IP, or `mainsailos.local`). The app communicates via the Moonraker HTTP API on port 7125.

Click **Apply & Reconnect** to connect. The status bar will show `Connected: MARLIN` or `Connected: KLIPPER` in green, along with the active camera backend (e.g., `Camera: playerone`).

**Camera Settings** (below the connection bar):

| Control | Description |
|---|---|
| **Exposure (µs)** | Slider from 100 µs to 1,000,000 µs. Updates the Player One camera live. |
| **Gain** | Slider from 0 to 500. Updates the Player One camera live. |
| **Resolution** | Dropdown populated by querying the SDK sensor properties. Changing resolution restarts the exposure stream cleanly. |

The live preview shows a green crosshair overlay to assist with lens alignment.

### Step 2 — Calibration Tab

1. Jog the camera to each of the four physical corners of the well plate using the jog controls.
2. Click the corresponding **Set** button for each corner: **UL** (upper-left), **UR** (upper-right), **LL** (lower-left), **LR** (lower-right).
3. Enter the grid dimensions (e.g., `12` columns × `8` rows for a standard 96-well plate).
4. Select the scan **Pattern**: **Raster** (left-to-right, top-to-bottom) or **Snake** (alternating direction each row).
5. Click **Save Calibration** to write the bilinear-interpolated positions to a JSON file in `config/calibrations/`.

### Step 3 — Experiment Tab

1. Enter an experiment name.
2. Select a saved calibration file from the dropdown (click **Refresh** if newly saved files do not appear).
3. Set the **Delay per well** in seconds (stabilization time after each move before capture).
4. Choose the capture mode:
   - **Standard Mode** (default): Captures a `.jpg` image at each well. The live preview polls the last saved image at ~0.5 fps during the run.
   - **Fast Raw Capture (.npy)**: Dumps the raw sensor buffer directly to disk as a NumPy binary file with no CPU-side encoding. The live preview is disabled during this mode to prevent SDK lock contention. Post-process the output after the experiment with:
     ```bash
     python3 scripts/post_process_raw.py outputs/<experiment_folder>
     # Add --mono flag if the sensor is monochrome (RAW8 without Bayer pattern)
     ```
5. Click **Start Experiment**. The status label updates in real time (e.g., `Moving to A1 (1/96)...` → `Waiting for stabilization at A1...` → `Recording well A1...`).
6. Click **Stop** at any time to halt the run cleanly.

All output is saved to `outputs/<timestamp>_<experiment_name>/`:
- One image file per well (`.jpg` or `.npy`)
- A CSV log with columns: `Well`, `X`, `Y`, `Z`, `Image_File`, `Timestamp`

---

## Camera Backend Priority

The `Camera` class in `robocam/camera.py` selects a backend in the following order on startup:

1. **Player One** — if `pyPOACamera` SDK is found and at least one camera is detected via `GetCameraCount()`.
2. **Picamera2** — if `picamera2` is installed (Raspberry Pi HQ camera).
3. **OpenCV (`cv2`)** — generic USB webcam fallback.

The SDK is searched in the following locations (in order):
- `PlayerOne_Camera_SDK_Linux_V3.10.0/python/` inside the project root
- Any `PlayerOne_Camera_SDK_Linux_*/python/` directory in the project root
- The `PLAYERONE_SDK_PYTHON` environment variable
- `~/PlayerOne_Camera_SDK_Linux_V3.10.0/python/`
- Any `~/PlayerOne_Camera_SDK_Linux_*/python/` glob match

---

## Motion Backend Details

### Marlin (Serial)

The `MarlinBackend` in `robocam/motion.py` is ported directly from the `GCodeSerialMotionController` in RoboCam-Suite 2.0. Key behaviors:

- **Auto-port detection**: Scans serial ports for USB/CH340/FTDI/Arduino descriptors.
- **M400 capability check**: On the first move, it sends `M400` (wait for moves to finish). If the firmware does not support it, it permanently falls back to a configurable sleep delay for that session.
- **Command delay**: A configurable `command_delay` (default 50 ms) is inserted after each command to prevent buffer overflow.
- **`in_waiting` read loop**: Reads serial data only when bytes are available, avoiding busy-wait CPU spin.
- **Position sync**: Sends `M114` after every move to keep the GUI position display accurate.

### Klipper (Moonraker HTTP API)

The `KlipperBackend` communicates with Klipper over the network via the Moonraker REST API:

| Operation | Endpoint |
|---|---|
| Send G-code | `POST /printer/gcode/script` |
| Query position | `GET /printer/objects/query?toolhead` |
| Check printer state | `GET /printer/info` |

`M400` is always available in Klipper and is used unconditionally for movement completion waiting. The Klipper backend is the recommended backend for network-connected printers and Tailscale-based remote sessions.

---

## Klipper Peripheral Control (Roadmap)

A key advantage of Klipper over Marlin is that **all unused printer peripherals are scriptable via G-code macros**. Hardware originally designed for 3D printing can be repurposed for laboratory automation without any hardware modification.

### Planned Peripheral Mappings

| Printer Hardware | Repurposed Function | G-code / Klipper Command |
|---|---|---|
| **Part Cooling Fan** | Laser enable/disable or illumination control | `M106 S255` (on) / `M107` (off) |
| **Extruder Heater** | Incubation chamber heater or sample warmer | `M104 S<temp>` / `M109 S<temp>` (wait) |
| **Heated Bed** | Slide warmer or culture plate temperature control | `M140 S<temp>` / `M190 S<temp>` (wait) |
| **Extruder Motor** | Peristaltic pump, syringe pump, or media dispenser | `G1 E<mm> F<speed>` |
| **Auxiliary Fan (Fan1)** | Secondary illumination or ventilation | `SET_FAN_SPEED FAN=fan1 SPEED=<0-1>` |
| **Output Pin (GPIO)** | Laser gate, shutter, or TTL trigger | `SET_PIN PIN=<name> VALUE=<0/1>` |
| **Filament Sensor Input** | External trigger (plate sensor, door interlock) | `QUERY_FILAMENT_SENSOR SENSOR=<name>` |

### Implementation Approach

All peripheral control in Klipper is handled through named macros and pin aliases defined in `printer.cfg`. The workflow for adding a new peripheral to RoboCam 3.1 is:

1. **Define the pin in `printer.cfg`**: For example, to use the part cooling fan MOSFET as a laser gate, add an `[output_pin laser]` section.
2. **Optionally add a Klipper macro**: Wrap the command in a `[gcode_macro LASER_ON]` block for clean, readable calls.
3. **Call via Moonraker**: RoboCam 3.1 sends the command via `POST /printer/gcode/script` — the same endpoint used for motion — so no new communication infrastructure is needed.

### Example: Laser On/Off via Fan Output

**`printer.cfg` addition:**
```ini
[output_pin laser]
pin: PA8          # The fan MOSFET pin on your board
value: 0          # Default off
shutdown_value: 0 # Safety: off on Klipper shutdown
```

**RoboCam 3.1 call (via `KlipperBackend.send_gcode`):**
```python
motion.backend.send_gcode("SET_PIN PIN=laser VALUE=1")  # Laser ON
motion.backend.send_gcode("SET_PIN PIN=laser VALUE=0")  # Laser OFF
```

> **Note:** Marlin-based printers support a subset of these controls (fan via `M106`, bed via `M140`, extruder via `G1 E`). Klipper's `output_pin` and macro system provides far greater flexibility and safety control over arbitrary GPIO pins.

---

## Dependencies

| Package | Purpose |
|---|---|
| `pyserial` | Marlin serial communication |
| `numpy` | Frame buffer handling and fast raw `.npy` capture |
| `opencv-python` | Frame processing, image saving, and OpenCV camera fallback |
| `requests` | Klipper Moonraker HTTP API communication |
| `pillow` | Tkinter image display |
| `picamera2` *(optional, Pi only)* | Raspberry Pi HQ camera support |
| Player One SDK *(auto-installed)* | Mars 662M and other Player One cameras |

---

## Known Issues & To-Do

See [TESTING.md](TESTING.md) for the full testing checklist, known issues, and the phased Klipper peripheral development roadmap.
