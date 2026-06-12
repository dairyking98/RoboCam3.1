# RoboCam 3.1 — Live Testing Checklist

This document is the live testing checklist for the physical Raspberry Pi session. Check off items as they are verified. Issues that have already been fixed in code are marked as such. Open items are tracked in the To-Do Summary at the bottom.

---

## Pre-Session Checklist

Before testing on the Pi, confirm the following:

- [ ] Pull the latest code on the Pi: `git pull origin master`
- [ ] Run `bash setup.sh` if this is a fresh clone or if dependencies have changed
- [ ] Confirm the Player One SDK was installed: `PlayerOne_Camera_SDK_Linux_V3.10.0/python/pyPOACamera.py` should exist in the project root
- [ ] Confirm `pyPOACamera.py` has been patched for Linux (check for `libPlayerOneCamera.so` in the file)
- [ ] Confirm the Mars 662M is plugged in via USB
- [ ] Confirm the printer is powered on and connected (USB for Marlin, or network/Tailscale for Klipper)
- [ ] Launch the app: `bash start_robocam.sh`

---

## 1. Setup Tab Tests

- [ ] **Motion Connection**: Select `marlin` and click Apply & Reconnect. Status should read `Connected: MARLIN` (green).
- [ ] **Camera Detection**: Status bar should read `Camera: playerone`.
- [ ] **Preview**: Crosshair overlay should be visible and correctly centered without green artifacts.
- [ ] **Exposure Slider**: Dragging the slider should visually change preview brightness instantly.
- [ ] **Gain Slider**: Dragging the slider should change sensor gain instantly.
- [ ] **Resolution**: Dropdown should be populated. Selecting a lower resolution should cleanly restart the feed.
- [ ] **Laser Config**: Set to `rpi_gpio` and pin `21`. Click Apply.

---

## 2. Manual Control Tab Tests

- [ ] **Home All Axes**: Printer should home X, Y, and Z.
- [ ] **Disable Steppers**: Click the button, then physically verify the carriage can be moved by hand.
- [ ] **Jog Controls**: Test X, Y, Z moves at 0.1, 1.0, and 10.0 mm steps.
- [ ] **Custom Step**: Type `5.5` into the custom box and verify it moves exactly that amount.
- [ ] **Go To Position**: Enter X: 50, Y: 50 and click Go. Verify movement.
- [ ] **Manual Laser**: Click Laser ON. Verify the physical laser turns on. Click Laser OFF.
- [ ] **Raw G-code**: Send `M114` and verify the printer responds in the terminal log.

---

## 3. Calibration Tab Tests

- [ ] Jog to all 4 corners of a well plate and click **Set UL**, **Set UR**, **Set LL**, **Set LR**
- [ ] Enter grid dimensions (e.g., 12 × 8) and click **Update Map & Save**
- [ ] **Navigation**: Click well A1 on the visual map. The printer should move exactly to the upper-left corner.
- [ ] **Pattern Selection**: Switch between Raster and Snake and verify the generated path array updates internally.

---

## 4. Experiment Tab Tests

### Image Mode
- [ ] Select Image mode, select a calibration, set a 1-second delay.
- [ ] Select 3 wells on the grid and click **Start Experiment**.
- [ ] Status label reads `Moving to A1 (1/3)...` → `Waiting for stabilization...` → `Recording well A1...`.
- [ ] Check `outputs/` for the timestamped folder. Verify 3 `.jpg` images and 1 `.csv` file are created.

### Video Mode (No Laser)
- [ ] Select Video mode. Do NOT check "Use Laser".
- [ ] Set Record duration to 3 seconds. Run 1 well.
- [ ] Verify an `.avi` video file and a `.json` sidecar (containing real FPS metadata) are created.

### Raw .npy Mode (With Laser)
- [ ] Select Raw .npy mode. Check "Use Laser".
- [ ] Set Pre-laser: 1s, Laser ON: 1s, Post-laser: 1s. Run 1 well.
- [ ] Verify the laser fires exactly 1 second after capture starts, stays on for 1 second, then turns off while capture continues for 1 more second.
- [ ] Verify a burst of `.npy` files are saved in the output folder.
- [ ] Run `python3 scripts/post_process_raw.py outputs/<folder>` and verify `.npy` files are successfully converted to `.jpg`.

### Preview Mode Behavior
- [ ] **Idle (not recording)**: Live preview runs at high framerate with green crosshairs.
- [ ] **Standard Recording**: Preview updates at ~0.5 fps by reading the last saved `.jpg` from disk. Crosshairs are hidden.
- [ ] **Fast Raw Recording**: Preview is replaced by a black frame with the text `"Preview disabled during Fast Raw Capture"`.

---

## 5. Known Issues

The following issues have been identified. Fixed items are marked; open items are tracked in the To-Do Summary.

~~**Exposure & Gain Controls** — Hardcoded in `camera.py`.~~ *(Fixed: Live sliders in GUI)*

~~**Player One SDK Thread Safety** — No mutex around `GetImageData`.~~ *(Fixed: `threading.Lock` wraps all SDK calls)*

~~**Resolution Configuration** — Hardcoded to `1920×1080`.~~ *(Fixed: Dynamic SDK polling and GUI dropdown)*

**Z-Hop During Travel** — The experiment runner moves X, Y, and Z simultaneously in a single `G0` command. If the lens is positioned very close to the well plate walls, this could cause a collision during lateral travel. A configurable Z-hop (raise Z before XY move, lower Z at destination) needs to be added to `ExperimentRunner.run()`.

---

## 6. To-Do Summary

| Priority | Item | Status |
|---|---|---|
| High | Setup and Manual Control tab split | **Done** |
| High | Laser/GPIO implementation and config | **Done** |
| High | Timed Raw and Video capture modes | **Done** |
| High | Z-hop during experiment travel | Pending |
| Medium | Temperature control widgets | Planned |
| Low | Extruder as pump/dispenser | Planned |
