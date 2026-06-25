# RoboCam 3.1 — Live Testing Checklist

This document is the live testing checklist for the physical Raspberry Pi session. Check off items as they are verified.

---

## Pre-Session Checklist

- [ ] Pull the latest code on the Pi: `git pull origin master`
- [ ] Run `bash setup.sh` if this is a fresh clone or if dependencies have changed (includes `av` / PyAV)
- [ ] Confirm the Player One SDK is installed: `PlayerOne_Camera_SDK_Linux_V3.10.0/python/pyPOACamera.py` should exist
- [ ] Confirm `pyPOACamera.py` has been patched for Linux (should reference `libPlayerOneCamera.so`)
- [ ] Confirm the Mars 662M is plugged in via USB
- [ ] Confirm the printer is powered on and connected (USB for Marlin, network/Tailscale for Klipper)
- [ ] Launch the app: `bash start_robocam.sh`

---

## 1. Setup Tab Tests

- [ ] **Motion Connection**: Select `marlin`, click Apply & Reconnect. Status reads `Connected: MARLIN` (green).
- [ ] **Homing Check**: If position is (0,0,0) after connect, app flags printer as not-homed. Home All Axes to clear.
- [ ] **Camera Detection**: Status bar reads `Camera: playerone`.
- [ ] **Preview**: Crosshair overlay visible and correctly centered.
- [ ] **Exposure Slider**: Dragging changes preview brightness instantly.
- [ ] **Gain Slider**: Dragging changes sensor gain instantly.
- [ ] **Resolution Dropdown**: Populated from SDK native max. Selecting lower resolution cleanly restarts feed.
- [ ] **Laser Config**: Set to `rpi_gpio`, pin `21`. Click Apply.

---

## 2. Manual Control Tab Tests

- [ ] **Home All Axes**: Printer homes X, Y, Z.
- [ ] **Disable Steppers**: Click, verify carriage can be moved by hand.
- [ ] **Jog Controls**: Test X, Y, Z moves at 0.1, 1.0, and 10.0 mm steps.
- [ ] **Custom Step**: Type `5.5` into the custom box and verify it moves exactly that amount.
- [ ] **Go To Position**: Enter X: 50, Y: 50, click Go. Verify movement.
- [ ] **Manual Laser**: Click Laser ON — physical laser fires. Click Laser OFF.
- [ ] **Raw G-code**: Send `M114`, verify the printer responds in the log.

---

## 3. Calibration Tab Tests

- [ ] Jog to all 4 corners and click **Set UL**, **Set LL**, **Set UR**, **Set LR**.
- [ ] Enter grid dimensions (e.g., 12 × 8) and pattern (Raster or Snake).
- [ ] Click **Save Calibration** — file appears in `config/calibrations/`. Open the JSON and verify it contains both `corners` and `interpolated_positions` arrays.
- [ ] **Navigation**: Click well A1 on the visual map — printer moves to upper-left corner.
- [ ] **Navigation**: Click well H12 — printer moves to lower-right corner.
- [ ] Switch between Raster and Snake — well order updates (verify via CLI or log if needed).

---

## 4. Experiment Tab Tests

### Output Folder Picker
- [ ] Click **Browse…** next to the output folder label.
- [ ] Select a different directory (e.g., `/tmp/robocam_test`).
- [ ] Verify the label updates to the new path and `config/default_config.json` is updated.

### Image Mode
- [ ] Select Image mode, select a calibration file, set 1-second dwell.
- [ ] Select 3 wells on the grid, click **Start Experiment**.
- [ ] Verify amber `"EXPERIMENT IN PROGRESS"` overlay appears on the camera preview.
- [ ] Status label cycles through: Moving → Stabilising → Capturing → finished.
- [ ] In `outputs/`, verify timestamped folder contains 3 image files and 1 `.csv`.
- [ ] Overlay disappears when experiment finishes.

### Raw .npy Mode (With Laser)
- [ ] Select Raw .npy mode, check **Use Laser**.
- [ ] Set Pre-laser: 2s, Laser ON: 2s, Post-laser: 2s. Select 1 well.
- [ ] Click **Start Experiment**. Verify amber overlay on preview.
- [ ] Verify laser fires ~2s after capture starts and turns off ~2s later.
- [ ] In `outputs/<exp_dir>/raw/`, verify `.npy` files and `*_metadata.json`.
- [ ] Open the metadata JSON — confirm `frames[]` has individual `time_offset_s` per entry (not just avg fps), and `laser_events[]` has two entries (ON + OFF) with accurate timestamps.

### Post-Processing Pipeline
- [ ] Activate venv: `source .venv/bin/activate`
- [ ] Run: `python scripts/reconstruct_vfr.py outputs/<exp_dir>/`
- [ ] In `outputs/<exp_dir>/images/A1/`, verify PNG files named with frame index, µs timestamp, and laser state (e.g., `A1_f00000_000006203us_laser-off.png`).
- [ ] In `outputs/<exp_dir>/videos/`, verify `A1_<ts>_vfr.mkv` and `A1_<ts>.mp4` are created.
- [ ] Play the MP4 on the Pi — verify smooth playback and asterisk (*) overlay visible during laser-ON frames.
- [ ] Verify MKV timing: `ffprobe -show_entries frame=best_effort_timestamp_time -select_streams v:0 outputs/<exp_dir>/videos/A1_*_vfr.mkv | head -20` — timestamps should match `metadata.json` `frames[].time_offset_s`.

### Video Mode (No Laser)
- [ ] Select Video mode, no laser. Record duration 3s. Run 1 well.
- [ ] Verify `.avi` and `*_metadata.json` sidecar. Open JSON — confirm `frame_timestamps_s[]` array present (individual per-frame timestamps).

### Preview Behavior During Experiment
- [ ] **All modes**: Amber `"EXPERIMENT IN PROGRESS / Preview Paused"` overlay during run. Disappears on finish/stop.
- [ ] **Idle in raw/video mode** (between wells): Red `"● RECORDING (Preview Paused)"` shown.
- [ ] **Not running**: Live preview at full framerate.

---

## 5. Headless CLI Tests

```bash
source .venv/bin/activate
python -m robocam status
python -m robocam motion pos
python -m robocam camera info
python -m robocam config show
python -m robocam --simulate status
```

- [ ] `status` shows connected hardware.
- [ ] `motion pos` returns current X/Y/Z.
- [ ] `camera info` shows backend and resolution.
- [ ] `config show` displays current config.
- [ ] `--simulate` runs without hardware.

---

## 6. Known Issues

**Z-Hop During Travel** — The experiment runner issues a single `G0 X Y Z` command per well. If the lens is very close to plate walls, lateral travel could cause a collision. A configurable Z-hop is needed in `ExperimentRunner.run()`.

---

## 7. Status Summary

| Item | Status |
|---|---|
| PySide6 GUI (4 tabs) | Done |
| Setup + Manual Control tabs | Done |
| Calibration tab (4-corner bilinear) | Done |
| Experiment tab (Image / Raw / Video) | Done |
| Per-frame timestamps (not averaged) | Done |
| Laser GPIO + Klipper integration | Done |
| `scripts/reconstruct_vfr.py` pipeline | Done |
| VFR MKV + constant-fps MP4 dual output | Done |
| Per-frame PNG export with timestamp in filename | Done |
| Output folder picker in Experiment tab | Done |
| Session persistence | Done |
| Headless CLI | Done |
| Z-hop during experiment travel | Pending |
| Temperature control widgets | Planned |
| Extruder as pump/dispenser | Planned |
