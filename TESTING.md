# RoboCam 3.1 — Testing & QA Plan

This document is the live testing checklist for the Raspberry Pi session via Tailscale. Check off items as they are verified. Issues that have already been fixed in code are marked as such. Open items are tracked in the To-Do Summary at the bottom.

---

## Pre-Session Checklist

Before connecting to the Pi, confirm the following:

- [ ] Pull the latest code on the Pi: `git pull origin master`
- [ ] Run `bash setup.sh` if this is a fresh clone or if dependencies have changed
- [ ] Confirm the Player One SDK was installed: `PlayerOne_Camera_SDK_Linux_V3.10.0/python/pyPOACamera.py` should exist in the project root
- [ ] Confirm `pyPOACamera.py` has been patched for Linux (check for `libPlayerOneCamera.so` in the file)
- [ ] Confirm the Mars 662M is plugged in via USB
- [ ] Confirm the printer is powered on and connected (USB for Marlin, or network/Tailscale for Klipper)
- [ ] Launch the app: `bash start_robocam.sh`

---

## 1. Setup Script Tests

- [ ] `setup.sh` completes without errors on the Pi
- [ ] Virtual environment is created at `.venv/` with `--system-site-packages` (required for libcamera)
- [ ] `picamera2` installs successfully (or is already present via system packages)
- [ ] `scripts/install_playerone_sdk.py` downloads and extracts the correct ARM64 `.so` library
- [ ] `pyPOACamera.py` is patched correctly (check that `libPlayerOneCamera.so` appears in the file)
- [ ] `start_robocam.sh` activates the venv and launches the app cleanly with no import errors

---

## 2. Camera Detection Tests

- [ ] Player One SDK is auto-detected and the Mars 662M initializes on startup
- [ ] Live preview appears in the Motion & Camera tab immediately
- [ ] Preview colors look correct (RAW8 grayscale debayered to BGR/RGB properly — no color cast)
- [ ] Status bar shows `Camera: playerone`
- [ ] **Resolution dropdown** is populated with resolutions polled from the SDK sensor properties
- [ ] Changing the resolution in the dropdown restarts the exposure stream and the preview updates
- [ ] **Exposure slider** updates the camera live (drag to a very high value and confirm the image brightens)
- [ ] **Gain slider** updates the camera live (drag to maximum and confirm noise increases)
- [ ] If the Player One camera is unplugged, the app falls back to Picamera2 or cv2 without crashing

---

## 3. Motion Controller Tests

### Marlin (Serial)
- [ ] USB serial port is auto-detected (check console for `Connected to printer on /dev/ttyUSB0` or similar)
- [ ] Status bar shows `Connected: MARLIN` in green
- [ ] On the first jog move, console logs whether `M400` is supported or whether the fallback sleep is active
- [ ] Position label (`X: Y: Z:`) updates after each jog move

### Klipper (Network / Tailscale)
- [ ] Switch backend to `klipper` and enter the Tailscale IP of the printer
- [ ] Click **Apply & Reconnect** and confirm `Connected: KLIPPER` in green
- [ ] Console logs `GET /printer/info` response confirming Klipper state is `ready`
- [ ] Position is polled from Moonraker (`/printer/objects/query?toolhead`) and displayed correctly

### Jogging & Homing
- [ ] **Home All**: printer homes X, Y, Z and position label resets to `0.00, 0.00, 0.00`
- [ ] **Jog X+/X-** at 0.1 mm, 1.0 mm, 10.0 mm steps — position label updates correctly each time
- [ ] **Jog Y+/Y-** at 0.1 mm, 1.0 mm, 10.0 mm steps
- [ ] **Jog Z+/Z-** at 0.1 mm, 1.0 mm, 10.0 mm steps
- [ ] Jogging does not block the GUI (UI remains responsive during moves)

---

## 4. Calibration Tests

- [ ] Jog to all 4 corners of a well plate and click **Set UL**, **Set UR**, **Set LL**, **Set LR**
- [ ] Each corner label updates with the recorded coordinate
- [ ] Save a **12×8 Raster** calibration and open the JSON in `config/calibrations/`
  - [ ] Verify `interpolated_positions` contains exactly 96 entries
  - [ ] Verify coordinates increase monotonically left-to-right and top-to-bottom (no wild jumps)
- [ ] Save a **12×8 Snake** calibration and verify:
  - [ ] Row 0 (A): columns 1 → 12
  - [ ] Row 1 (B): columns 12 → 1 (reversed)
  - [ ] Row 2 (C): columns 1 → 12 again
- [ ] Load the calibration in the Experiment tab and confirm it appears in the dropdown

---

## 5. Experiment Runner Tests

### Standard Capture Mode
- [ ] Select a calibration, set a 1-second delay, and click **Start Experiment**
- [ ] Status label reads `Moving to A1 (1/96)...` as the printer moves
- [ ] Status label reads `Waiting for stabilization at A1...` during the delay
- [ ] Status label reads `Recording well A1...` during capture
- [ ] Printer moves to `A2` next (Raster) or `A12` next (Snake)
- [ ] **Stop** button halts the experiment cleanly without crashing the GUI or leaving the printer mid-move
- [ ] Check `outputs/` for the timestamped experiment folder
- [ ] Verify `.jpg` images are saved and are not corrupted (open one in an image viewer)
- [ ] Open the CSV and verify all columns are populated: `Well`, `X`, `Y`, `Z`, `Image_File`, `Timestamp`

### Fast Raw Capture Mode
- [ ] Check the **Fast Raw Capture (.npy)** checkbox and start an experiment
- [ ] Preview window shows black placeholder: `"Preview disabled during Fast Raw Capture"`
- [ ] Experiment runs noticeably faster per well (no JPG encoding delay)
- [ ] `.npy` files are saved in the output folder
- [ ] Post-processing script converts them correctly:
  ```bash
  python3 scripts/post_process_raw.py outputs/<experiment_folder>
  ```
- [ ] `.jpg` files appear alongside the `.npy` files and look correct

### Preview Mode Behavior
- [ ] **Idle (not recording)**: Live preview runs at high framerate (~30 fps) with green crosshairs.
- [ ] **Standard Recording**: Preview updates at ~0.5 fps by reading the last saved `.jpg` from disk. Crosshairs are hidden.
- [ ] **Fast Raw Recording**: Preview is replaced by a black frame with the text `"Preview disabled during Fast Raw Capture"`.
- [ ] After the experiment finishes or is stopped, the preview returns to live high-framerate mode automatically.

---

## 6. Known Issues

The following issues have been identified. Fixed items are marked; open items are tracked in the To-Do Summary.

~~**Exposure & Gain Controls** — Hardcoded in `camera.py`.~~ *(Fixed: Live sliders in GUI)*

~~**Player One SDK Thread Safety** — No mutex around `GetImageData`.~~ *(Fixed: `threading.Lock` wraps all SDK calls)*

~~**Resolution Configuration** — Hardcoded to `1920×1080`.~~ *(Fixed: Dynamic SDK polling and GUI dropdown)*

**Z-Hop During Travel** — The experiment runner moves X, Y, and Z simultaneously in a single `G0` command. If the lens is positioned very close to the well plate walls, this could cause a collision during lateral travel. A configurable Z-hop (raise Z before XY move, lower Z at destination) needs to be added to `ExperimentRunner.run()`.

---

## 7. Klipper Peripheral Control (Future Development)

This section tracks the planned implementation of custom Klipper peripheral control for repurposing unused 3D printer hardware for laboratory automation. See `README.md` for the full peripheral mapping table and `printer.cfg` examples.

### Phase 1 — Foundation (Next Sprint)
- [ ] Create `robocam/peripherals.py` with a `KlipperPeripheralController` class
- [ ] Implement `laser_on()` / `laser_off()` using `SET_PIN PIN=laser VALUE=1/0`
- [ ] Implement `set_fan_speed(speed: float)` using `M106 S<0-255>`
- [ ] Add a **Peripherals** tab to the GUI with laser toggle and fan speed slider
- [ ] Document the required `printer.cfg` additions for each peripheral in `docs/`

### Phase 2 — Temperature Control
- [ ] Implement `set_bed_temp(celsius: float)` and `wait_for_bed_temp()` using `M140` / `M190`
- [ ] Implement `set_hotend_temp(celsius: float)` using `M104` / `M109`
- [ ] Add temperature readback via Moonraker (`/printer/objects/query?heater_bed&extruder`)
- [ ] Add temperature display and setpoint widgets to the Peripherals tab

### Phase 3 — Extruder as Pump / Dispenser
- [ ] Implement `dispense(volume_ul: float, flow_rate: float)` using `G1 E<mm> F<speed>`
- [ ] Create a calibration routine to map extruder steps to dispensed volume (µL/mm)
- [ ] Integrate dispense call into the experiment loop (dispense at each well before capture)

### Phase 4 — Marlin Compatibility Layer
- [ ] Audit which peripheral commands are compatible with Marlin (`M106`, `M104`, `M140`, `G1 E`)
- [ ] Add conditional logic in `peripherals.py` so the same API works on both backends
- [ ] Document which features are Klipper-only vs cross-compatible

---

## 8. To-Do Summary

| Priority | Item | Status |
|---|---|---|
| High | Exposure & gain controls in GUI | **Done** |
| High | Player One SDK thread lock | **Done** |
| High | Dynamic resolution polling from SDK | **Done** |
| High | Z-hop during experiment travel | Pending |
| Medium | Peripherals tab (laser, fan on/off) | Planned — Phase 1 |
| Medium | Temperature control widgets | Planned — Phase 2 |
| Low | Extruder as pump/dispenser | Planned — Phase 3 |
| Low | Marlin peripheral compatibility layer | Planned — Phase 4 |
