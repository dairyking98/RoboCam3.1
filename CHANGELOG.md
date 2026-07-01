# Changelog

All notable changes to RoboCam are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added
- **Processing tab** — batch-convert `.npy` burst captures to PNG image
  sequences and video (MP4 + VFR MKV) with per-well progress. Auto-process
  checkbox in the Experiment tab triggers it automatically after each run.
- **Pi camera true raw burst** — Picamera2 now opens with a video+raw stream
  config so `get_raw_frame()` returns genuine 10/12-bit Bayer data at burst
  rate instead of ISP-processed greyscale. A `camera_meta.json` sidecar is
  written once per experiment for correct debayering during post-processing.
- **Multi-camera selection** — Setup panel enumerates all connected Pi cameras
  by model/index; PlayerOne cameras blocked by USB permissions show an
  in-app udev rule installer.

### Changed
- **Raw Burst replaces both "Raw .npy" and "Video" modes** — real-time AVI
  encoding is removed; video is produced in post-processing with accurate
  per-frame timing from timestamp metadata.
- **GPIO: lgpio preferred over RPi.GPIO** — `lgpio` is tried first (works on
  Pi 4 and Pi 5); `RPi.GPIO` is kept as a fallback for older Pi OS installs.
  `setup.sh` now installs both.

### Fixed
- Experiment would silently jump to "finished" if `cv2` import was missing
  from `experiment.py` after the video mode cleanup.

---

## [0.1.0] — 2026-06-26

First versioned release of RoboCam 3.1. This release consolidates the full
imaging pipeline developed iteratively across earlier RoboCam repositories.

### Added

- **PySide6 GUI** — four-tab desktop application (Setup, Manual Control,
  Calibration, Experiment) replacing the earlier Tkinter interface.
- **Dual motion backends** — Marlin (USB/serial) and Klipper (Moonraker HTTP
  API), plus a `SimulationBackend` for testing without hardware.
- **Player One camera support** — first-class integration via the
  `pyPOACamera` SDK, with automatic fallback to Picamera2 and OpenCV.
- **4-corner bilinear calibration** — corner positions captured interactively;
  all well positions interpolated and saved to JSON profiles.
- **Three capture modes** — Image (single still), Raw `.npy` (max-rate sensor
  burst), and Video (MJPG AVI).
- **Per-frame timestamps** — every raw burst frame carries a `time_offset_s`
  from `time.perf_counter()`; actual inter-frame intervals are preserved, not
  averaged.
- **Laser stimulation** — GPIO (RPi BCM) and Klipper G-code triggers with
  Pre / ON / Post phase recording.
- **VFR reconstruction pipeline** — `scripts/reconstruct_vfr.py` converts
  `.npy` bursts to per-frame PNGs (filename-encoded timestamp + laser state),
  a VFR MKV (accurate per-frame PTS), and a constant-fps MP4 for display.
- **Headless CLI** — `python -m robocam <command>` for hardware testing and
  scripted workflows without the GUI.
- **Session persistence** — all experiment parameters and calibration restore
  automatically on next launch.
- **Homing safety** — position is checked on motion connect; experiments are
  blocked until the printer is homed.
- **Pytest suite** — 50 hardware-free tests covering bilinear interpolation,
  raster/snake path generation, well labels, config persistence, and CLI
  argument parsing.
- **GitHub Actions CI** — runs pytest on Python 3.10, 3.11, and 3.12.
- **MIT license**.

[Unreleased]: https://github.com/dairyking98/RoboCam3.1/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dairyking98/RoboCam3.1/releases/tag/v0.1.0
