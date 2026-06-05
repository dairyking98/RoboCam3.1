# RoboCam 3.1

RoboCam 3.1 is a Python-only desktop application that bridges the simplicity of RoboCam 2.0 with the robustness and communication improvements of RoboCam 3.0 (RoboCam-Suite). It eliminates the web-based complexities of 3.0 while retaining the advanced Klipper/Marlin serial handling, 4-corner calibration, and modular architecture.

## Key Features

1. **Unified Desktop Interface**: A single, clean Tkinter GUI with tabbed navigation (Motion, Calibration, Experiment).
2. **Dual Motion Backends**: Seamlessly switch between **Marlin** (USB/Serial) and **Klipper** (Moonraker HTTP API over network). Includes robust error recovery, `M400` wait commands, and reliable position tracking for both platforms.
3. **4-Corner Calibration**: Uses bilinear interpolation to generate accurate well-plate paths from four corner coordinates, accounting for rotation and skew.
4. **Automated Experiments**: Runs automated image capture sequences across the well plate with configurable delays and CSV data export.
5. **JSON Configuration**: Centralized settings for hardware, timeouts, and paths.
6. **Simulation Mode**: Built-in hardware simulation for testing without a printer or camera attached.

## Installation

1. Ensure you have Python 3.x installed.
2. Install dependencies:
   ```bash
   pip install pyserial opencv-python pillow requests
   ```
   *(Note: `picamera2` is required if running on a Raspberry Pi with a native camera module).*

## Usage

Run the main application:

```bash
python3 robocam31.py
```

### Workflow

1. **Motion & Camera**: 
   - Select your backend (`marlin` or `klipper`).
   - If using Klipper, enter your printer's IP address and click **Apply & Reconnect**.
   - Use the jog controls to move the camera over the well plate. The live preview helps you position the lens accurately.
2. **Calibration**: 
   - Move to the Upper Left well and click "Set UL".
   - Repeat for Upper Right, Lower Left, and Lower Right.
   - Enter the grid dimensions (e.g., 12x8).
   - Click "Save Calibration".
3. **Experiment**:
   - Select the saved calibration file.
   - Set the desired delay per well.
   - Click "Start Experiment" to begin the automated capture sequence.
   - Images and a CSV summary will be saved in the `outputs/` directory.

## Architecture

- `robocam31.py`: Main application entry point and Tkinter GUI.
- `robocam/motion.py`: Serial communication and G-code handling.
- `robocam/camera.py`: Multi-backend camera capture (Picamera2 or OpenCV).
- `robocam/calibration.py`: 4-corner bilinear interpolation and JSON profile management.
- `robocam/experiment.py`: Automated execution and data logging.
- `robocam/config.py`: Configuration management.
