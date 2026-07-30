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
- **Motion Profiles tab** (#3) — read/edit/write Marlin max feed rate (`M203`),
  acceleration (`M201`), and jerk (`M205`) for X/Y/Z via slider rows with a
  "ghost tick" marking the value last confirmed on the printer. Save/load
  named presets (`config/motion_profile_presets/`); three bundled Ender-5 S1
  presets (`slow`/`medium`/`fast`, low-vibration biased) ship by default.
  Marlin only — Klipper has no equivalent gcode.
- **JPEG image export and zip packaging** (#4) — JPEG added alongside
  PNG/TIF for Image mode and post-processing output; PNG/JPEG image
  sequences can optionally be packaged into one `.zip` per experiment,
  streamed directly into the archive.
- **Verbose printer-connect log** (#5) — Setup tab's printer group gets a
  live "Connection log" box showing each step of `MarlinBackend.connect()`
  (port scan, port found, boot-banner wait, position query); connect now
  runs off the GUI thread.
- **Well crosshair overlay** (#7, #10) — toggleable center crosshair (lines
  + circle) on the Calibration tab's live preview for aligning a well before
  recording a corner. Radius is a % of frame height (consistent across
  resolutions), adjustable via a spinbox and a synced slider.
- **Auto-home, powered steppers, and experiment ETA** (#11) — experiments
  auto-home instead of blocking Start when position isn't known; `M18 S0`
  sent on printer connect keeps steppers powered (no idle-timeout
  de-energize) for the life of the connection; a scrolling "Experiment log"
  and a live "ETA: MM:SS" countdown (from the motion profile plus measured
  capture/finalize costs) are shown during a run.
- **Fullscreen Demo Mode** (Manual Control tab) — kiosk-style well
  navigation and laser control from a distance, with arrow-key jogging and
  on-screen keyboard-shortcut hints.
- **Target FPS** (#6) — Calibration tab gains an independent Target FPS
  field alongside Exposure, letting a short exposure pair with a
  deliberately slow capture rate (e.g. darkfield scanning). Enforced as a
  software pacing cap in raw-burst capture.

### Changed
- **Raw Burst replaces both "Raw .npy" and "Video" modes** — real-time AVI
  encoding is removed; video is produced in post-processing with accurate
  per-frame timing from timestamp metadata.
- **GPIO: lgpio preferred over RPi.GPIO** — `lgpio` is tried first (works on
  Pi 4 and Pi 5); `RPi.GPIO` is kept as a fallback for older Pi OS installs.
  `setup.sh` now installs both.
- **Export output formats split** (#4) — PNG/JPEG images and MP4/VFR video
  each get their own folder (`images_png/`, `images_jpeg/`, `videos_mp4/`,
  `videos_vfr/`) instead of sharing one, so formats are never mixed
  together. VFR's default codec switched from lossy `libx264` to lossless
  `ffv1` (measured smaller than a zipped PNG stack on real hardware footage
  while staying fully lossless).
- **Image mode defaults to PNG instead of JPEG** — lossless, no compression
  artifacts; any speed tradeoff is deferred to post-processing.
- **Exposure and Target FPS decoupled** (#6, #8, #9) — editing either field
  no longer silently overwrites the other. A conflict warning + "Rectify"
  button appears only when Target FPS exceeds what the current exposure can
  achieve; Rectify adjusts whichever field the user did *not* just edit, and
  snaps Target FPS to the exact achievable value when it lowers Exposure
  instead of leaving a rounding mismatch.
- **Motion Profiles Load no longer auto-applies to the printer** — Load now
  only populates the sliders, matching Load's semantics everywhere else in
  the app; Apply is the one action that sends gcode. Last-loaded preset
  persists across restarts.
- **Raw-burst finalize (flush + trim) moved to a backgrounded spawn
  subprocess** instead of running inline — overlaps with the next well's
  movement/capture instead of stalling it, fixing a ~25-36% throughput loss
  at high fps/resolution. Verified on hardware: 0.03% ETA error across a
  full run.
- **Image-mode and raw-burst finalize capture-time estimates are now
  format-, resolution-, and exposure-aware** instead of flat constants —
  brought ETA accuracy from a multi-second guess down to sub-1%-error on
  real hardware across both capture modes.
- **Experiment ETA now accounts for real per-move overhead** — explicit `F`
  (from the motion profile's max feed rate) and `M204` (travel acceleration)
  are now sent with every move instead of relying on whatever was last
  active; a further flat ~0.3s/move serial round-trip cost is now modeled
  explicitly.

### Fixed
- Experiment would silently jump to "finished" if `cv2` import was missing
  from `experiment.py` after the video mode cleanup.
- **PlayerOne `bayer_pattern` was hardcoded to `"RGGB"`** regardless of the
  actual camera — the Mars 662M is monochrome and was being demosaiced as
  if it had a color filter array, producing interpolation artifacts instead
  of clean grayscale. Now read from the SDK.
- **PlayerOne raw-burst fps was jitter-bound at ~30fps** instead of its true
  exposure-bound ceiling (confirmed on hardware: ~50fps @ 20ms exposure,
  ~94fps @ 10ms) — fixed via acquisition/writer-thread decoupling, a direct
  blocking SDK call replacing a polling loop, and a preallocated frame
  buffer.
- **VFR MKV files reported corrupted duration/frame-count metadata** to
  readers like Fiji/VLC (encoded pixel data and file size were unaffected)
  — traced to `add_stream(codec, rate=...)` misusing the `rate=` parameter;
  fixed by syncing `codec_context.time_base` explicitly.
- **Rectify (fps conflict) always overwrote Target FPS**, even when the
  conflict was caused by editing Target FPS itself, silently undoing the
  user's own edit — now adjusts whichever field wasn't just edited.
- **`is_homed` clearing on a manual M18/M84 didn't survive an app
  restart** — `send_raw()` now sends `G92 X0 Y0` to overwrite Marlin's own
  position counter with the not-homed sentinel `connect()` checks for.
- **Several absolute-move UI paths never checked `is_homed`** before
  moving (Calibration's well-map click and `_set_corner()`, Manual
  Control's `_move_well()`/`_goto()`) — all now warn-and-block when unhomed.
- A stray `stack.flush()` in the raw-burst writer thread's `finally:` block
  fired on every exit path, including normal completion, defeating the
  finalize-overlap fix above — now only flushes there on the error path.
- The app's log file (`mode="w"` `FileHandler`, held open all session) could
  silently lose a finalize subprocess's completion log line to a stale
  write offset — fixed by truncating once at startup and reopening in
  `mode="a"` so every writer stays `O_APPEND`-consistent.

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
