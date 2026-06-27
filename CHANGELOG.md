# Changelog

All notable changes to RoboCam are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

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
