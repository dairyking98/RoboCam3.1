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

- [ ] **Motion Connection**: Select `marlin`, click Apply & Reconnect. Status reads `Connected: MARLIN` (green). Connection log box fills in with scan/port/banner/position lines while it connects.
- [ ] **Steppers stay powered**: after connect, confirm the printer does *not* de-energize steppers after its normal idle timeout (`M18 S0` should have been sent) — carriage should resist being moved by hand while idle.
- [ ] **Homing Check**: If position is (0,0,0) after connect, app flags printer as not-homed. Home All Axes to clear.
- [ ] **Camera Detection**: Status bar reads `Camera: playerone`.
- [ ] **Preview**: crosshair overlay visible and correctly centered (see § 3 for full crosshair tests).
- [ ] **Exposure Slider**: Dragging changes preview brightness instantly.
- [ ] **Gain Slider**: Dragging changes sensor gain instantly.
- [ ] **Resolution Dropdown**: Populated from SDK native max. Selecting lower resolution cleanly restarts feed.
- [ ] **Laser Config**: Set to `rpi_gpio`, pin `21`. Click Apply.

---

## 2. Motion Profiles Tab Tests

- [ ] **Read from Printer**: click Read — sliders populate from the printer's actual `M503` response (feed rate, acceleration, jerk for X/Y/Z; X/Y shown as one chained "XY:" row).
- [ ] **Ghost tick**: after Read, each slider's ghost tick marks the read value; dragging a slider away from it highlights the field border orange.
- [ ] **Apply to Printer**: drag a value, click Apply — confirm the printer actually changed (re-read or check behavior), and that the ghost tick moves to match (EEPROM-saved via `M500`).
- [ ] **Reset to Defaults**: after dragging a slider, click Reset — value reverts to the last-read/applied value without contacting the printer.
- [ ] **Load Preset**: load one of the three bundled presets (`ender5_s1_slow_low_vibration`, `_medium_`, `_fast_`) — confirm sliders populate but nothing is sent to the printer until Apply is clicked (Load no longer auto-applies).
- [ ] **Save Preset**: save current slider values under a new name, confirm the file appears in `config/motion_profile_presets/`.
- [ ] **Session restore**: restart the app — confirm the last-loaded preset name is restored and its sliders are populated (still without sending anything).
- [ ] **Klipper**: switch printer backend to Klipper — tab should show a disabled "not supported" message; sliders remain editable offline.
- [ ] **ETA accuracy**: run an experiment (§ 4) and compare the logged move time against the ETA countdown — should track closely now that `F` (M203-derived) and `M204` (travel acceleration) are actually sent per move.

---

## 3. Calibration Tab Tests

- [ ] Jog to all 4 corners and click **Set UL**, **Set LL**, **Set UR**, **Set LR** — blocked with a warning if the printer isn't homed.
- [ ] **Crosshair overlay**: toggle it on, confirm it renders centered and scales proportionally when the preview/window is resized.
- [ ] **Crosshair radius**: drag the radius slider — crosshair updates live and the paired spinbox reflects the value; type into the spinbox — slider snaps to match. Save/reload calibration and confirm radius restores to both controls.
- [ ] Enter grid dimensions (e.g., 12 × 8) and pattern (Raster or Snake).
- [ ] Click **Save Calibration** — file appears in `config/calibrations/`. Open the JSON and verify it contains both `corners` and `interpolated_positions` arrays.
- [ ] **Navigation**: Click well A1 on the visual map — printer moves to upper-left corner (blocked with a warning if unhomed).
- [ ] **Navigation**: Click well H12 — printer moves to lower-right corner.
- [ ] Switch between Raster and Snake — well order updates (verify via CLI or log if needed).
- [ ] **Quick Capture**: grab a still or short raw burst directly from this tab (written to `outputs/quick_capture/`) without running a full experiment.

---

## 4. Experiment Tab Tests

### Output Folder Picker
- [ ] Click **Browse…** next to the output folder label.
- [ ] Select a different directory (e.g., `/tmp/robocam_test`).
- [ ] Verify the label updates to the new path and `config/default_config.json` is updated.

### Auto-Home & ETA
- [ ] Leave the printer unhomed and click **Start Experiment** — confirm it auto-homes instead of blocking, then proceeds into the well loop.
- [ ] Confirm the **Experiment log** box shows per-stage lines (homing, moving to well, dwelling, recording, laser on/off).
- [ ] Confirm the **ETA: MM:SS** countdown appears after homing completes, counts down, and turns red/negative only if the run genuinely overruns — compare final actual vs. estimated time in `robocam.log`.

### Image Mode
- [ ] Select Image mode — confirm **PNG** is the default format (JPG/TIF also selectable), set 1-second dwell.
- [ ] Select 3 wells on the grid, click **Start Experiment**.
- [ ] Verify amber `"EXPERIMENT IN PROGRESS"` overlay appears on the camera preview.
- [ ] Status label cycles through: Moving → Stabilising → Capturing → finished.
- [ ] In `outputs/`, verify timestamped folder contains 3 image files and 1 `.csv`.
- [ ] Overlay disappears when experiment finishes.

### Raw Burst Mode (With Laser)
- [ ] Select Raw Burst mode, check **Use Laser**.
- [ ] Set Pre-laser: 2s, Laser ON: 2s, Post-laser: 2s. Select 1 well.
- [ ] Click **Start Experiment**. Verify amber overlay on preview.
- [ ] Verify laser fires ~2s after capture starts and turns off ~2s later.
- [ ] In `outputs/<exp_dir>/raw/`, verify `<well>_<ts>_stack.npy` (one file for the whole well), `<well>_<ts>_frames.jsonl`, and `<well>_<ts>_metadata.json`.
- [ ] Open the metadata JSON — confirm `frames[]` has individual `time_offset_s` per entry (not just avg fps), `laser_events[]` has two entries (ON + OFF) with accurate timestamps, and `frames_captured` matches the file's real (post-trim) frame count.
- [ ] **Throughput/finalize check**: run several consecutive wells at a high fps/resolution combo; confirm "Moving to next well" fires immediately after capture ends (finalize should overlap in the background, not stall the run), and compare logged actual finish time against the ETA (should be within ~1%).

### Post-Processing Pipeline
- [ ] Activate venv: `source .venv/bin/activate`
- [ ] Run: `python scripts/reconstruct_vfr.py outputs/<exp_dir>/ --png --mp4 --vfr`
- [ ] In `outputs/<exp_dir>/images_png/A1/`, verify PNG files named with frame index, ms timestamp, and laser state (e.g., `A1_00000_000006ms_laser-off.png`).
- [ ] Run again with `--jpeg --zip` — verify `images_jpeg.zip` is created instead of loose files.
- [ ] In `outputs/<exp_dir>/videos_mp4/` and `videos_vfr/`, verify the MP4 and lossless-`ffv1` VFR MKV are created in their own folders.
- [ ] Play the MP4 on the Pi — verify smooth playback and asterisk (*) overlay visible during laser-ON frames.
- [ ] Verify MKV timing: `ffprobe -show_entries frame=best_effort_timestamp_time -select_streams v:0 outputs/<exp_dir>/videos_vfr/A1_*_vfr.mkv | head -20` — timestamps should match `metadata.json` `frames[].time_offset_s`, and the container's reported duration/frame-count should be sane.

### Preview Behavior During Experiment
- [ ] **All modes**: Amber `"EXPERIMENT IN PROGRESS / Preview Paused"` overlay during run. Disappears on finish/stop.
- [ ] **Idle in raw-burst mode** (between wells): Red `"● RECORDING (Preview Paused)"` shown.
- [ ] **Not running**: Live preview at full framerate.

---

## 5. Manual Control Tab Tests

- [ ] **Home All Axes**: Printer homes X, Y, Z.
- [ ] **Disable Steppers**: Click, verify carriage can be moved by hand, and that the app now flags the printer as not-homed (should persist across an app restart).
- [ ] **Jog Controls**: Test X, Y, Z moves at 0.1, 1.0, and 10.0 mm steps.
- [ ] **Custom Step**: Type `5.5` into the custom box and verify it moves exactly that amount.
- [ ] **Go To Position**: Enter X: 50, Y: 50, click Go — blocked with a warning if unhomed.
- [ ] **Manual Laser**: Click Laser ON — physical laser fires. Click Laser OFF.
- [ ] **Raw G-code**: Confirm the on-screen "⚠ WARNING" is visible. Send `M114`, verify the printer responds in the log.
- [ ] **Demo Mode**: click the Demo Mode button — confirm a fullscreen window opens with well navigation, laser control, and arrow-key jogging; nav buttons disable at grid edges; there's no on-screen exit button (confirm the documented keyboard exit works).

---

## 6. Processing Tab Tests

- [ ] Add a finished experiment folder to the queue manually.
- [ ] Select PNG, JPEG, MP4, and VFR output options together — confirm all four land in their own `images_png/`, `images_jpeg/`, `videos_mp4/`, `videos_vfr/` folders, never mixed.
- [ ] Toggle zip packaging for images — confirm a single `.zip` per experiment is produced instead of loose files.
- [ ] Confirm the auto-process hookup: check "Auto-process after experiment" on the Experiment tab, run a short experiment, and verify the folder is queued and starts processing automatically here.
- [ ] Verify per-well and overall progress bars advance and the scrolling log updates.

---

## 7. Headless CLI Tests

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

## 8. Known Issues

- **Pi camera (Picamera2) raw burst → color output** — see `docs/recording_modes.md` Known Issues and `PROJECT_STATE.md` § 9 for the current CSI-2-packing hypothesis; not testable without Pi camera hardware.
- **Z-Hop During Travel** — the experiment runner issues a single `G0 X Y Z` command per well. If the lens is very close to plate walls, lateral travel could cause a collision. A configurable Z-hop is still needed.
- **Klipper motion backend** — implemented but not yet exercised against real Klipper/Moonraker hardware.

---

## 9. Status Summary

| Item | Status |
|---|---|
| PySide6 GUI (6 tabs: Setup, Motion Profiles, Calibration, Experiment, Manual Control, Processing) | Done |
| Setup + Manual Control tabs | Done |
| Motion Profiles tab (feed-rate/accel/jerk, presets) | Implemented — **untested on real Marlin hardware** |
| Calibration tab (4-corner bilinear, crosshair overlay+slider) | Done |
| Experiment tab (Image / Raw Burst, auto-home, ETA, experiment log) | Implemented — ETA/auto-home **untested on real hardware** |
| Raw-burst throughput fix + finalize overlap | Hardware-verified |
| Image-mode format/resolution-aware capture-time ETA | Hardware-verified |
| PlayerOne fps ceiling (exposure-bound) | Hardware-confirmed |
| Per-frame timestamps (not averaged) | Done |
| Laser GPIO + Klipper integration | Done |
| Export format split (PNG/JPEG/MP4/VFR, zip packaging, ffv1 default) | Done |
| `scripts/reconstruct_vfr.py` pipeline | Done |
| Demo Mode (Manual Control) | Implemented — **untested on real hardware** |
| Output folder picker in Experiment tab | Done |
| Session persistence | Done |
| Headless CLI | Done |
| Z-hop during experiment travel | Pending |
| Klipper backend real-hardware verification | Pending |
| Temperature control widgets | Planned |
| Extruder as pump/dispenser | Planned |
