# RoboCam 3.1

RoboCam 3.1 is a Python-only desktop application designed for automated well-plate imaging using 3D printer mechanics. It bridges the simplicity of the original RoboCam 2.0 Tkinter GUI with the robust architecture, advanced hardware drivers, and math models from RoboCam-Suite 2.0.

It completely eliminates the web-based complexities while retaining native Player One Astronomy camera support, Klipper/Marlin dual-backend motion control, and accurate 4-corner bilinear interpolation.

---

## Key Features

1. **Unified Desktop Interface**: A single, responsive Tkinter GUI with tabbed navigation (Motion & Camera, Calibration, Experiment). No web server required.
2. **Dual Motion Backends**: 
   - **Marlin (Serial)**: Features robust error recovery, `M400` wait commands, and non-blocking serial read loops.
   - **Klipper (Network)**: Communicates directly via the Moonraker HTTP API for instant execution and position polling.
3. **Advanced Camera Support**:
   - **Player One Astronomy**: Native integration with the `pyPOACamera` SDK for high-quality microscopy cameras (e.g., Mars 662M).
   - **Picamera2**: Native libcamera support for Raspberry Pi HQ cameras.
   - **OpenCV**: Fallback for standard USB webcams.
4. **4-Corner Calibration**: Uses bilinear interpolation to generate accurate well-plate paths from four corner coordinates, accounting for physical rotation and skew. Supports both **Raster** and **Snake** scan patterns.
5. **Automated Experiments**: Runs automated image capture sequences across the well plate with configurable stabilization delays, detailed UI status feedback, and CSV data export.
6. **Simulation Mode**: Built-in hardware simulation for UI testing without a printer or camera attached.

---

## Installation & Setup

### Prerequisites
- Python 3.x
- A Linux environment (Raspberry Pi OS recommended) or Windows/macOS.

### 1. Clone the Repository
```bash
git clone https://github.com/dairyking98/RoboCam3.1.git
cd RoboCam3.1
```

### 2. Install Dependencies
```bash
pip install pyserial opencv-python pillow requests numpy
```
*(Note: `picamera2` is required if running on a Raspberry Pi with a native camera module).*

### 3. Player One Camera SDK (Optional but Recommended)
If you are using a Player One camera (e.g., Mars 662M), you must have the official SDK installed on your system. RoboCam 3.1 will automatically search for it in:
- `~/PlayerOne_Camera_SDK_Linux_V3.10.0/python`
- `PLAYERONE_SDK_PYTHON` environment variable.
- Or any matching directory in the project root.

The app will dynamically patch the SDK for Linux compatibility at runtime.

---

## Usage

Run the main application:

```bash
python3 robocam31.py
```

### Workflow

1. **Motion & Camera**: 
   - Select your backend (`marlin` or `klipper`).
   - If using Klipper, enter your printer's IP address and click **Apply & Reconnect**.
   - Verify the camera backend (PlayerOne, Picamera2, or CV2) is correctly detected in the status bar.
   - Use the jog controls to move the camera over the well plate. The live preview features a center crosshair to help you align the lens accurately.
2. **Calibration**: 
   - Jog to the Upper Left well and click "Set UL".
   - Repeat for Upper Right, Lower Left, and Lower Right.
   - Enter the grid dimensions (e.g., 12 columns x 8 rows).
   - Select the path pattern (**Raster** or **Snake**).
   - Click "Save Calibration".
3. **Experiment**:
   - Select the saved calibration file from the dropdown.
   - Set the desired delay per well (stabilization time before capture).
   - Click "Start Experiment".
   - Watch the live status indicator as the rig moves to each well (e.g., `A1`, `B3`) and captures an image.
   - Images and a CSV summary will be saved in the `outputs/` directory.

---

## Architecture Overview

RoboCam 3.1 is structured into modular components, heavily inspired by RoboCam-Suite 2.0:

- `robocam31.py`: Main application entry point and Tkinter GUI. Handles thread management and UI updates.
- `robocam/motion.py`: Abstract `MotionBackend` interface with concrete implementations for `MarlinBackend` (serial), `KlipperBackend` (Moonraker API), and `SimulationBackend`.
- `robocam/camera.py`: Abstract camera handler that auto-detects and loads the best available backend (`PlayerOne`, `Picamera2`, or `cv2`).
- `robocam/calibration.py`: Contains the `WellPlate` class for 4-corner bilinear interpolation and standard well labeling math.
- `robocam/experiment.py`: The `ExperimentRunner` that orchestrates the motion, camera, and logging loops in a background thread.
- `robocam/config.py`: JSON-based configuration management for persisting settings across sessions.
