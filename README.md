# RoboCam 3.1

RoboCam 3.1 is a Python-only desktop application for automated well-plate imaging using 3D printer mechanics. It combines the simplicity of the original RoboCam Tkinter GUI with the robust hardware drivers, bilinear interpolation math, and Klipper communication architecture from RoboCam-Suite 2.0 — with no web server, no browser, and no external framework required.

---

## Key Features

| Feature | Description |
|---|---|
| **Unified Desktop GUI** | Single Tkinter application with four tabs: Setup, Manual Control, Calibration, Experiment. No web server required. |
| **Dual Motion Backends** | Switch between **Marlin** (USB/Serial) and **Klipper** (Moonraker HTTP API over network/Tailscale) without restarting. |
| **Player One Camera** | First-class support for Player One Astronomy cameras (Mars 662M, etc.) via the `pyPOACamera` SDK. Auto-detects and falls back to Picamera2 or OpenCV. |
| **Live Camera Controls** | Adjust **Exposure**, **Gain**, and **Resolution** live from the GUI. Resolution list is polled directly from the SDK sensor properties. |
| **4-Corner Calibration** | Bilinear interpolation from four physical corner coordinates accounts for plate rotation and skew. Saves/loads JSON profiles. |
| **Raster & Snake Patterns** | Choose between row-by-row raster scan or alternating snake scan for efficient well-plate traversal. |
| **Three Capture Modes** | **Image** (single still), **Raw .npy** (timed raw sensor burst), and **Video** (MJPG AVI). Raw and Video capture at max framerate with real FPS metadata. |
| **Hardware Stimulation** | Integrated Laser/GPIO controller. Trigger a laser via direct Raspberry Pi GPIO or Klipper G-code, perfectly timed with timed captures. |
| **Smart Preview Modes** | Live preview when idle; last-frame polling during standard recording; preview disabled during fast raw capture to prevent SDK lock contention. |

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
│   ├── peripherals.py            # LaserController for RPi GPIO and Klipper outputs
│   └── config.py                 # JSON-based configuration management
├── scripts/
│   ├── install_playerone_sdk.py  # Downloads and patches Player One SDK for Linux/ARM
│   └── post_process_raw.py       # Converts fast raw .npy files to .jpg after experiment
├── config/
│   └── default_config.json       # Base configuration file
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

---

## Usage

### Step 1 — Setup Tab

Configure hardware connections before operating the machine.

- **Printer Connection**: Select your backend (`marlin` or `klipper`), enter the Klipper IP if applicable, and click **Apply & Reconnect**.
- **Camera Settings**: Adjust Exposure (ms) and Gain using sliders or precise numeric entry. Select the camera resolution.
- **Laser / GPIO Configuration**: Choose how your laser is connected (`disabled`, `rpi_gpio`, or `klipper`). Set the Raspberry Pi BCM pin (default `21`) or the Klipper G-code commands (`SET_PIN PIN=laser VALUE=1`). Click **Apply Laser Settings**.

### Step 2 — Manual Control Tab

Directly control the hardware outside of an experiment.

- **Machine Controls**: Click **Home All Axes** to home the printer. Click **Disable Steppers** to move the stage by hand.
- **Jog Controls**: Move X, Y, and Z by preset amounts (0.1, 1.0, 10.0) or type a custom step size.
- **Go To Position**: Type exact X, Y, Z coordinates to move the stage immediately.
- **Laser Control**: Manually toggle the laser ON or OFF for testing.
- **Manual G-code**: Send raw G-code commands directly to the printer and view the log.

### Step 3 — Calibration Tab

1. Jog the camera to each of the four physical corners of the well plate using the jog controls.
2. Click the corresponding **Set** button for each corner: **UL** (upper-left), **UR** (upper-right), **LL** (lower-left), **LR** (lower-right).
3. Enter the grid dimensions (e.g., `12` columns × `8` rows for a standard 96-well plate).
4. Select the scan **Pattern**: **Raster** (left-to-right, top-to-bottom) or **Snake** (alternating direction each row).
5. Click **Update Map & Save** to write the bilinear-interpolated positions to a JSON file in `config/calibrations/`.
6. You can now click any well on the visual map to immediately jog the printer to that well.

### Step 4 — Experiment Tab

1. Enter an experiment name.
2. Select a saved calibration file from the dropdown.
3. Set the **Delay per well** in seconds (stabilization time after each move before capture).
4. Choose the capture mode:
   - **Image**: Captures a single still image per well.
   - **Raw .npy**: Dumps raw binary sensor data for a specified duration at max framerate.
   - **Video**: Records an MJPG `.avi` for a specified duration at max framerate.
5. If using Raw or Video, check **Use Laser** to enable stimulation. This replaces the "Record duration" field with three phases: **Pre-laser**, **Laser ON**, and **Post-laser**. The camera records continuously through all three phases.
6. Drag on the well grid to select which wells to include in the run.
7. Click **Start Experiment**.

---

## Post-Processing Fast Raw Capture

If you run an experiment in **Raw .npy** mode, the camera dumps raw binary sensor data to disk to achieve maximum framerates. These files cannot be viewed directly.

After the experiment finishes, run the post-processing script to debayer the `.npy` files into standard `.jpg` images:

```bash
python3 scripts/post_process_raw.py outputs/<experiment_folder>
```

---

## Camera Backend Priority

The `Camera` class in `robocam/camera.py` selects a backend in the following order on startup:

1. **Player One** — if `pyPOACamera` SDK is found and at least one camera is detected via `GetCameraCount()`.
2. **Picamera2** — if `picamera2` is installed (Raspberry Pi HQ camera).
3. **OpenCV (`cv2`)** — generic USB webcam fallback.

---

## Motion Backend Details

### Marlin (Serial)

The `MarlinBackend` in `robocam/motion.py` features:
- **Auto-port detection**: Scans serial ports for USB/CH340/FTDI/Arduino descriptors.
- **M400 capability check**: On the first move, it sends `M400` (wait for moves to finish). If unsupported, it falls back to a sleep delay.
- **`in_waiting` read loop**: Reads serial data only when bytes are available, avoiding busy-wait CPU spin.

### Klipper (Moonraker HTTP API)

The `KlipperBackend` communicates with Klipper over the network via the Moonraker REST API:
- Send G-code: `POST /printer/gcode/script`
- Query position: `GET /printer/objects/query?toolhead`

---

## Klipper Peripheral Roadmap (Lab Automation)

RoboCam 3.1's architecture is designed to repurpose unused 3D printer hardware (heaters, fans, extruders) for lab automation tasks via Klipper.

| Printer Hardware | Repurposed Function | G-code / Klipper Command | Status |
|---|---|---|---|
| **Output Pin (GPIO)** | Laser gate, shutter, or TTL trigger | `SET_PIN PIN=<name> VALUE=<0/1>` | **Implemented** |
| **Part Cooling Fan** | Laser enable/disable or illumination control | `M106 S255` (on) / `M107` (off) | Planned |
| **Extruder Heater** | Incubation chamber heater or sample warmer | `M104 S<temp>` / `M109 S<temp>` (wait) | Planned |
| **Heated Bed** | Slide warmer or culture plate temperature control | `M140 S<temp>` / `M190 S<temp>` (wait) | Planned |
| **Extruder Motor** | Peristaltic pump, syringe pump, or media dispenser | `G1 E<mm> F<speed>` | Planned |

### Setting up a Klipper Laser Pin
To use the `klipper` laser mode today, add this to your `printer.cfg`:

```ini
[output_pin laser]
pin: PA0  # Replace with your actual controller board pin
pwm: False
value: 0
```
Then in the RoboCam Setup tab, set the Klipper ON G-code to `SET_PIN PIN=laser VALUE=1`.

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
