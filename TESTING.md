# RoboCam 3.1 Testing & QA Plan

This document outlines the testing procedures, known issues, and to-do items to be executed during the live Raspberry Pi session via Tailscale.

---

## 1. Hardware Connection Tests

### Camera Detection
- [ ] **Player One SDK Detection**: Verify that `camera.py` successfully locates the Player One SDK on the Pi.
- [ ] **Player One Capture**: Verify that the Mars 662M initializes, sets `POA_RAW8` format, and successfully streams frames to the Tkinter GUI.
- [ ] **Picamera2 Fallback**: If the Player One camera is unplugged, verify that the Pi HQ camera initializes instead.
- [ ] **Color Space**: Ensure the live preview colors look correct (Player One RAW8 is converted to BGR, then to RGB for Tkinter).

### Motion Controller Connection
- [ ] **Marlin (Serial)**: Connect via USB, verify auto-baud rate detection, and confirm the `Connected: MARLIN` status appears.
- [ ] **Klipper (Network)**: Switch backend to Klipper, enter the Pi/Mainsail IP, and confirm `Connected: KLIPPER` status.
- [ ] **M400 Capability Check**: (Marlin only) Verify the console logs show the `M400` test passing on the first move.

---

## 2. Motion & Calibration Tests

### Jogging & Homing
- [ ] **Homing**: Click "Home All" and verify the printer homes X, Y, and Z.
- [ ] **Jogging**: Test the X/Y/Z jog buttons at 0.1mm, 1.0mm, and 10.0mm steps.
- [ ] **Position Sync**: Verify the UI position label (X/Y/Z) updates correctly after every jog.

### 4-Corner Calibration
- [ ] **Setting Corners**: Jog to all 4 corners of a well plate and click the respective "Set" buttons.
- [ ] **Interpolation Math**: Save a 12x8 calibration and inspect the generated JSON file in `config/calibrations/` to ensure the `interpolated_positions` look mathematically sound (no wild jumps).
- [ ] **Snake vs Raster**: Generate two calibrations (one Snake, one Raster) and verify the JSON coordinate ordering reflects the pattern choice.

---

## 3. Experiment Runner Tests

### Execution
- [ ] **Load Calibration**: Select a saved calibration in the Experiment tab.
- [ ] **Start Run**: Click Start and verify the printer moves to `A1`.
- [ ] **Delay & Capture**: Verify the printer pauses for the configured delay, captures the image, and then moves to `A2` (or the next well).
- [ ] **Status Feedback**: Verify the UI status label updates correctly (`Moving to A1...` -> `Waiting for stabilization...` -> `Recording...`).
- [ ] **Pause/Stop**: Test the Stop button mid-experiment to ensure the thread exits gracefully without crashing the app.

### Data Output
- [ ] **Images**: Check the `outputs/` directory to ensure JPGs are saved correctly and are not corrupted.
- [ ] **CSV Log**: Open the generated CSV and verify the columns (Well, X, Y, Z, Image_File, Timestamp) are populated correctly.

---

## 4. Known Issues & To-Do List

### To-Do Before Deployment
- [ ] **Exposure & Gain Controls**: The current `camera.py` hardcodes the Player One exposure and gain. We need to expose these settings to the UI (likely in the Motion & Camera tab) so they can be adjusted live.
- [ ] **Z-Axis Handling During Experiment**: Currently, the experiment moves X, Y, and Z simultaneously. We may need to add a "Z-hop" feature (raise Z, move XY, lower Z) if the lens is too close to the well plate walls during travel.
- [ ] **Laser/GPIO Support**: RoboCam-Suite 2.0 had GPIO support for triggering a laser. This was omitted in 3.1 for simplicity, but we need to confirm if it needs to be ported back.
- [ ] **Resolution Settings**: Hardcoded to `1920x1080` in `camera.py`. Need to confirm if the Mars 662M should run at its native max resolution instead.

### Known Quirks
- **Tkinter Threading**: Tkinter is not thread-safe. The `update_camera_preview` loop runs on the main thread via `.after()`, while the `ExperimentRunner` runs in a background thread. The `callback` passed to the runner uses `.after(0, ...)` to safely update the UI, but heavy I/O during capture could theoretically cause slight UI stutters.
- **Player One SDK Lock**: The Player One SDK is notoriously thread-unsafe. `camera.py` currently does not implement the strict threading lock seen in Suite 2.0 because the capture loop here is simpler, but if crashes occur during experiments, a `threading.Lock()` around `GetImageData` will need to be added.
