# RoboCam 3.1

RoboCam 3.1 is a Python-only desktop application designed for automated well-plate imaging using 3D printer mechanics. It bridges the simplicity of the original RoboCam 2.0 Tkinter GUI with the robust architecture, advanced hardware drivers, and math models from RoboCam-Suite 2.0.

It completely eliminates the web-based complexities while retaining native Player One Astronomy camera support, Klipper/Marlin dual-backend motion control, and accurate 4-corner bilinear interpolation.

---

## Key Features

1. **Unified Desktop Interface**: A single, responsive Tkinter GUI with tabbed navigation (Motion & Camera, Calibration, Experiment). No web server required.
2. **Dual Motion Backends**: Seamlessly switch between **Marlin** (USB/Serial) and **Klipper** (Moonraker HTTP API over network) without restarting the application.
3. **Native Player One Camera Support**: First-class integration with the Player One Astronomy `pyPOACamera` SDK (e.g., Mars 662M), with automatic SDK detection, Linux patching, and fallback to Picamera2 or OpenCV.
4. **4-Corner Calibration**: Uses bilinear interpolation to generate accurate well-plate paths from four corner coordinates, accounting for physical rotation and skew. Supports both **Raster** and **Snake** scan patterns.
5. **Automated Experiments**: Runs automated image capture sequences with configurable stabilization delays, detailed live status feedback, and CSV data export.
6. **Simulation Mode**: Built-in hardware simulation for UI testing without a printer or camera attached.

---

## Installation & Setup

### Prerequisites

- Python 3.10 or newer
- A Linux environment (Raspberry Pi OS Bookworm recommended) or Windows

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

The setup script will:
- Create a Python virtual environment (`.venv`) with `--system-site-packages` on Raspberry Pi to ensure `libcamera` is accessible.
- Install all pip dependencies from `requirements.txt`.
- Install `picamera2` if a Raspberry Pi is detected.
- Download and install the Player One Camera SDK from player-one-astronomy.com into `PlayerOne_Camera_SDK_Linux_V3.10.0/`, patching `pyPOACamera.py` for Linux/ARM compatibility automatically.

### 3. Launch the Application

**Every subsequent launch:**
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

### Workflow

**Step 1 — Motion & Camera Tab**

Select your motion backend (`marlin` or `klipper`). If using Klipper, enter the printer's IP address (e.g., `192.168.1.100` or a Tailscale IP) and click **Apply & Reconnect**. The status bar will confirm the connection and display the active camera backend (PlayerOne, Picamera2, or cv2). Use the jog controls to move the camera over the well plate. The live preview features a center crosshair to help align the lens.

**Step 2 — Calibration Tab**

Jog to each of the four corners of the well plate and click the corresponding **Set** button (UL, UR, LL, LR). Enter the grid dimensions (e.g., 12 columns × 8 rows) and select a path pattern (**Raster** or **Snake**). Click **Save Calibration** to write the bilinear-interpolated positions to a JSON file in `config/calibrations/`.

**Step 3 — Experiment Tab**

Select the saved calibration file from the dropdown. Set the desired stabilization delay per well. Click **Start Experiment** and watch the live status indicator as the rig moves to each well (e.g., `Moving to A1 (1/96)...` → `Recording well A1...`). Images and a CSV log are saved in the `outputs/` directory.

---

## Architecture

| Module | Description |
|---|---|
| `robocam31.py` | Main application entry point and Tkinter GUI. Manages threads and UI callbacks. |
| `robocam/motion.py` | Abstract `MotionBackend` interface with `MarlinBackend` (serial), `KlipperBackend` (Moonraker HTTP), and `SimulationBackend`. |
| `robocam/camera.py` | Auto-detecting camera handler. Priority order: Player One → Picamera2 → OpenCV. |
| `robocam/calibration.py` | `WellPlate` class for 4-corner bilinear interpolation and standard well labeling (A1–H12). |
| `robocam/experiment.py` | `ExperimentRunner` orchestrating motion, capture, and CSV logging in a background thread. |
| `robocam/config.py` | JSON-based configuration management, persisting settings across sessions. |
| `scripts/install_playerone_sdk.py` | Downloads and patches the Player One SDK for the current platform and CPU architecture. |

---

## Klipper Peripheral Control (Roadmap)

One of the most powerful advantages of using a Klipper-based printer over Marlin is that **all unused printer peripherals become scriptable via G-code macros**. This means hardware that was originally designed for 3D printing — the extruder, heated bed, part cooling fan, hotend heater, and auxiliary outputs — can be repurposed for laboratory automation tasks without any hardware modification.

The plan is to expose these controls through a dedicated **Peripherals** panel in the RoboCam 3.1 GUI, backed by a `KlipperPeripheralController` class that sends the appropriate G-code or Moonraker API calls.

### Planned Peripheral Mappings

| Printer Hardware | Repurposed Function | G-code / Klipper Command |
|---|---|---|
| **Part Cooling Fan** | Laser enable/disable or illumination control | `M106 S255` (on) / `M107` (off) |
| **Extruder Heater** | Incubation chamber heater or sample warmer | `M104 S<temp>` / `M109 S<temp>` (wait) |
| **Heated Bed** | Slide warmer or culture plate temperature control | `M140 S<temp>` / `M190 S<temp>` (wait) |
| **Extruder Motor** | Peristaltic pump, syringe pump, or media dispenser | `G1 E<mm> F<speed>` |
| **Auxiliary Fan (Fan1)** | Secondary illumination or ventilation | `SET_FAN_SPEED FAN=fan1 SPEED=<0-1>` |
| **Output Pin (GPIO)** | Laser gate, shutter, or TTL trigger | `SET_PIN PIN=<name> VALUE=<0/1>` |
| **Filament Sensor Input** | External trigger (e.g., plate sensor, door interlock) | `QUERY_FILAMENT_SENSOR SENSOR=<name>` |

### Implementation Approach

All peripheral control in Klipper is handled through **named macros and pin aliases** defined in `printer.cfg`. The workflow for adding a new peripheral to RoboCam 3.1 will be:

1. **Define the pin in `printer.cfg`**: For example, to use the part cooling fan output as a laser gate, add an `[output_pin laser]` section pointing to the fan MOSFET pin.
2. **Add a Klipper macro** (optional): Wrap the control command in a `[gcode_macro LASER_ON]` / `[gcode_macro LASER_OFF]` block for clean, readable G-code calls.
3. **Call via Moonraker**: RoboCam 3.1 sends the command via `POST /printer/gcode/script` — the same endpoint used for motion commands — so no new communication infrastructure is needed.

### Example: Laser On/Off via Fan Output

**`printer.cfg` addition:**
```ini
[output_pin laser]
pin: PA8          # The fan MOSFET pin on your board
value: 0          # Default off
shutdown_value: 0 # Safety: off on shutdown
```

**RoboCam 3.1 call (via `KlipperBackend.send_gcode`):**
```python
motion.backend.send_gcode("SET_PIN PIN=laser VALUE=1")  # Laser ON
motion.backend.send_gcode("SET_PIN PIN=laser VALUE=0")  # Laser OFF
```

This same pattern applies to all other peripherals. The `KlipperPeripheralController` class (to be implemented) will wrap these calls with named methods (`laser_on()`, `set_bed_temp(37.0)`, `dispense(volume_ul)`) and expose them as buttons and sliders in the GUI.

> **Note:** Marlin-based printers can also use this approach to a limited degree (fan, bed, extruder heater via standard G-code), but Klipper's `output_pin` and macro system gives far greater flexibility and safety control over arbitrary GPIO pins.

---

## Dependencies

| Package | Purpose |
|---|---|
| `pyserial` | Marlin serial communication |
| `numpy` | Frame buffer handling for Player One camera |
| `opencv-python` | Frame processing, image saving, and CV2 camera fallback |
| `requests` | Klipper Moonraker HTTP API communication |
| `pillow` | Tkinter image display |
| `picamera2` *(optional, Pi only)* | Raspberry Pi HQ camera support |
| Player One SDK *(optional)* | Mars 662M and other Player One cameras |

---

## Known Issues & To-Do

See [TESTING.md](TESTING.md) for the full testing checklist and known issues to be addressed during the live Pi session.
