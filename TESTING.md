# RoboCam 3.1 — Testing & QA Plan

This document outlines the testing procedures, known issues, and to-do items to be executed during the live Raspberry Pi session via Tailscale. Check off items as they are verified.

---

## Pre-Session Checklist (Before Connecting)

- [ ] Pull the latest code on the Pi: `git pull origin master`
- [ ] Run `bash setup.sh` if this is a fresh clone, or if dependencies have changed
- [ ] Confirm the Player One SDK was installed: check that `PlayerOne_Camera_SDK_Linux_V3.10.0/python/pyPOACamera.py` exists
- [ ] Confirm the Mars 662M is plugged in via USB
- [ ] Confirm the printer is powered on and connected (USB for Marlin, or network for Klipper)
- [ ] Launch the app: `bash start_robocam.sh`

---

## 1. Setup Script Tests

- [ ] `setup.sh` completes without errors on the Pi
- [ ] Virtual environment is created with `--system-site-packages` (required for libcamera)
- [ ] `picamera2` installs successfully (or is already present via system packages)
- [ ] `install_playerone_sdk.py` downloads and extracts the correct ARM64 `.so` library
- [ ] `pyPOACamera.py` is patched correctly (check for `RTLD_GLOBAL` in the file after install)
- [ ] `start_robocam.sh` activates the venv and launches the app cleanly

---

## 2. Camera Detection Tests

- [ ] Player One SDK is auto-detected and the Mars 662M initializes on startup
- [ ] Live preview appears in the Motion & Camera tab
- [ ] Preview colors look correct (RAW8 grayscale converted to BGR/RGB properly)
- [ ] Status bar shows `Camera: playerone`
- [ ] If the Player One camera is unplugged, the app falls back to Picamera2 or cv2 without crashing

---

## 3. Motion Controller Tests

### Marlin (Serial)
- [ ] USB serial port is auto-detected (check console for `Connected to printer on /dev/ttyUSB0`)
- [ ] Status bar shows `Connected: MARLIN`
- [ ] `M400` capability is tested on the first move and result is logged to console
- [ ] Position label updates after each jog

### Klipper (Network)
- [ ] Switch backend to `klipper` and enter the Tailscale IP of the printer
- [ ] Click **Apply & Reconnect** and confirm `Connected: KLIPPER` in green
- [ ] Position is polled from Moonraker (`/printer/objects/query?toolhead`) and displayed correctly

### Jogging & Homing
- [ ] **Home All**: printer homes X, Y, Z and position label resets to `0.00`
- [ ] **Jog X+/X-** at 0.1mm, 1.0mm, 10.0mm steps
- [ ] **Jog Y+/Y-** at 0.1mm, 1.0mm, 10.0mm steps
- [ ] **Jog Z+/Z-** at 0.1mm, 1.0mm, 10.0mm steps
- [ ] Position label updates correctly after every jog move

---

## 4. Calibration Tests

- [ ] Jog to all 4 corners of a well plate and set UL, UR, LL, LR
- [ ] Save a 12×8 Raster calibration and inspect the JSON in `config/calibrations/`
- [ ] Verify the `interpolated_positions` array contains 96 entries with no wild coordinate jumps
- [ ] Save a 12×8 Snake calibration and verify the coordinate ordering reverses on odd rows
- [ ] Load the calibration in the Experiment tab and confirm it appears in the dropdown

---

## 5. Experiment Runner Tests

- [ ] Select a calibration and click **Start Experiment**
- [ ] Printer moves to well `A1` and the status label reads `Moving to A1 (1/96)...`
- [ ] Printer pauses for the configured delay and status reads `Waiting for stabilization at A1...`
- [ ] Camera captures and status reads `Recording well A1...`
- [ ] Printer moves to `A2` (Raster) or `A12` (Snake) next
- [ ] **Stop** button halts the experiment cleanly without crashing the GUI
- [ ] Check `outputs/` for the timestamped experiment folder
- [ ] Verify JPG images are saved and not corrupted
- [ ] Open the CSV and verify all columns (Well, X, Y, Z, Image_File, Timestamp) are populated

---

## 6. Known Issues

The following issues are known and must be addressed before the system is considered production-ready.

~~**Exposure & Gain Controls** — The Player One camera exposure time and gain are currently hardcoded in `camera.py`.~~ *(Fixed: Live sliders added to GUI)*

**Z-Hop During Travel** — The experiment runner currently moves X, Y, and Z simultaneously in a single `G0` command. If the lens is positioned very close to the well plate walls, this could cause a collision during lateral travel. A configurable Z-hop (raise Z before XY move, lower Z at destination) needs to be added to the `ExperimentRunner`.

~~**Player One SDK Thread Safety** — The Player One SDK is not thread-safe.~~ *(Fixed: `threading.Lock` implemented around all SDK calls)*

~~**Resolution Configuration** — The camera resolution is hardcoded to `1920×1080`.~~ *(Fixed: Dynamic resolution polling from SDK and GUI dropdown added)*

---

## 7. Klipper Peripheral Control (Future Development)

This section tracks the planned implementation of custom Klipper peripheral control for repurposing unused 3D printer hardware (extruder, fans, heated bed, GPIO pins) for laboratory automation tasks such as laser control, sample heating, and media dispensing.

### Phase 1 — Foundation (Next Sprint)
- [ ] Create `robocam/peripherals.py` with a `KlipperPeripheralController` class
- [ ] Implement `laser_on()` / `laser_off()` using `SET_PIN PIN=laser VALUE=1/0`
- [ ] Implement `set_fan_speed(speed: float)` using `M106 S<0-255>`
- [ ] Add a **Peripherals** tab to the GUI with laser toggle and fan speed slider
- [ ] Document the required `printer.cfg` additions for each peripheral

### Phase 2 — Temperature Control
- [ ] Implement `set_bed_temp(celsius: float)` and `wait_for_bed_temp()` using `M140` / `M190`
- [ ] Implement `set_hotend_temp(celsius: float)` using `M104` / `M109`
- [ ] Add temperature readback via Moonraker (`/printer/objects/query?heater_bed&extruder`)
- [ ] Add temperature display widgets to the Peripherals tab

### Phase 3 — Extruder as Pump / Dispenser
- [ ] Implement `dispense(volume_ul: float, flow_rate: float)` using `G1 E<mm> F<speed>`
- [ ] Create a calibration routine to map extruder steps to dispensed volume (µL/mm)
- [ ] Integrate dispense call into the experiment loop (dispense at each well before capture)

### Phase 4 — Marlin Compatibility Layer
- [ ] Audit which peripheral commands are compatible with Marlin (M106, M104, M140, G1 E)
- [ ] Add conditional logic in `peripherals.py` so the same API works on both backends
- [ ] Document which features are Klipper-only vs cross-compatible

---

## 8. To-Do Summary

| Priority | Item | Status |
|---|---|---|
| High | Exposure & gain controls in GUI | Done |
| High | Z-hop during experiment travel | Pending |
| High | Player One SDK thread lock | Done |
| Medium | Resolution config in GUI | Done |
| Medium | Peripherals tab (laser, fan) | Planned |
| Medium | Temperature control widgets | Planned |
| Low | Extruder as pump/dispenser | Planned |
| Low | Marlin peripheral compatibility layer | Planned |
