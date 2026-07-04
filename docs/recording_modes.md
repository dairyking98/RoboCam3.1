# RoboCam 3.1 — Recording Modes

## Philosophy

All experiment captures use **raw burst mode**: frames are written as fast as possible with per-frame timestamps, and video/images are produced in a separate post-processing step. This separates the time-critical capture loop from any encoding overhead, maximises frame rate, and preserves full sensor bit depth for downstream analysis.

Real-time encoded video (the former `Video (AVI)` mode) has been removed. Post-processing produces equivalent output with accurate timing.

*The scientific motivation for this capture method — biological experiment requirements, frame rate needs, laser timing, downstream analysis — will be documented separately.*

---

## Capture Modes by Camera

### PlayerOne (astronomy camera)

**Mode name in UI:** `Raw Burst`

- Reads directly from the sensor SDK buffer — no ISP, no debayering
- Bit depth: 8-bit — `camera.py` explicitly requests `POA_RAW8` at init (`_init_playerone()`), not the sensor's native depth. (An earlier version of this doc said "16-bit (sensor-native)"; that was aspirational, not what the code does. `POA_RAW16` is available in the SDK but unused.)
- Each frame saved as a `.npy` file (NumPy binary array)
- Per-frame timestamps via `time.perf_counter()`
- **Max achievable FPS is currently ~30fps in practice, well under the Mars 662M's advertised 90-120fps** (measured consistently across the 2026-07-01 test dataset). The capture loop's jitter/robustness issues (synchronous disk writes, poll-loop latency, double buffer allocation, cross-tab lock contention) have been fixed in software — see `PROJECT_STATE.md` § 9. The fps *ceiling* itself (exposure, `POA_HQI`, `POA_USB_BANDWIDTH_LIMIT`, sensor-mode selection) is not yet confirmed on hardware; those are now exposed as live UI controls on the Calibration tab for testing next session.
- Capture is decoupled: an acquisition thread pushes frames onto a bounded queue (`RAW_BURST_QUEUE_MAXSIZE` in `experiment.py`), and a separate writer thread does the `.npy` save plus an incremental `<well>_<ts>_frames.jsonl` sidecar (crash-resilient per-frame timing, independent of the final `metadata.json`). The queue is always fully drained before a burst returns — no captured frame is ever dropped, even on `stop()`.
- Each well's `metadata.json` now also includes `capture_failures` (lock-timeout / SDK-timeout-or-error counts), `sdk_dropped_frames` (the SDK's own dropped-frame counter), and `queue_full_stalls`/`queue_full_stall_s_total` (how often/how long acquisition blocked waiting on a full write queue).

**Output folder layout (actual, as written by `ExperimentRunner`):**
```
<exp_dir>/
  raw/
    camera_meta.json                 ← written once per experiment: backend, model, bit depth, resolution,
                                        gain, exposure, fps, hqi_enabled, usb_bandwidth_limit, offset,
                                        sensor_mode_index, sensor_mode_name
    <well>_<ts>_f00000.npy
    <well>_<ts>_f00001.npy
    ...
    <well>_<ts>_frames.jsonl         ← one JSON line per frame, appended as captured (crash-resilient;
                                        not read by postprocess.py — a recovery artifact only)
    <well>_<ts>_metadata.json        ← frames[] (frame_index, file, time_offset_s), laser_events[],
                                        fps_average, duration_actual_s, capture_failures,
                                        sdk_dropped_frames, queue_full_stalls, queue_full_stall_s_total
  <ts>_<name>_points.csv
```

Note: timestamps and laser events live inside the per-well `*_metadata.json`, not in separate `timestamps.json`/`laser_events.json` files as an earlier draft of this document assumed.

---

### Raspberry Pi Camera (picamera2)

**Mode name in UI:** `Raw Burst`

- Uses a **video configuration with a raw stream** — this is the only way to get burst-rate raw frames; still configuration adds inter-frame latency
- Captures via `capture_array("raw")` — true Bayer pattern data, no ISP processing
- Bit depth: 10-bit (Camera Module 3) or 12-bit (HQ Camera), unpacked to uint16 in the array
- Each frame saved as a `.npy` file
- Per-frame timestamps via `time.perf_counter()`
- `camera_meta.json` **must** include Bayer metadata (see below) for correct reconstruction

**Picamera2 configuration:**
```python
cfg = self.picam2.create_video_configuration(
    main={"size": self.resolution, "format": "RGB888"},
    raw={}   # libcamera selects native sensor format
)
```

**`camera_meta.json` fields required for Pi camera reconstruction:**
```json
{
  "backend": "picamera2",
  "model": "...",
  "resolution": [1920, 1080],
  "bayer_pattern": "RGGB",
  "black_level": 64,
  "white_level": 1023,
  "colour_gains": [r_gain, b_gain],
  "analogue_gain": 1.0,
  "exposure_us": 20000,
  "bit_depth": 10
}
```

`bayer_pattern` comes from `camera.camera_properties["ColorFilterArrangement"]` (mapped to RGGB/BGGR/GRBG/GBRG string). `black_level` and `white_level` come from `capture_metadata()["SensorBlackLevels"]` and sensor properties.

---

## What Happened to Video Mode

The former `Video (AVI)` mode has been absorbed into the post-processing pipeline:

| Old mode | Replacement |
|---|---|
| Video (AVI) — real-time encoded | Raw Burst capture → post-process to MP4/MKV |
| Raw .npy | Raw Burst (same behaviour, renamed) |
| Image (single still) | Kept as-is for use cases that don't need burst |

The post-processing step produces video with **accurate per-frame timing** from the timestamp metadata, which real-time AVI encoding could not guarantee.

---

## Post-Processing

### Core pipeline (`robocam/postprocess.py`)

Shared by both the CLI (`scripts/reconstruct_vfr.py`) and the GUI (Processing tab). Reads `backend`/`bayer_pattern`/`bit_depth` from `camera_meta.json` and picks the matching OpenCV debayer code (RGGB/BGGR/GRBG/GBRG), scaling >8-bit sensor data down to `uint8` first. Both PlayerOne and Picamera2 metadata paths are implemented — see the known issue below for the current correctness caveat on the Picamera2 side.

### Processing Tab (GUI) — implemented and verified working on hardware

`ui/processing_panel.py` provides:
- Folder list: add/remove one or more experiment output folders
- Output options: PNG image sequence, MP4 (constant fps, presentation), VFR MKV (accurate timing, archival) — independently toggleable
- **Auto-process after experiment** checkbox in the Experiment tab, which queues and starts processing automatically the moment a run finishes
- Per-well and overall progress bars, scrolling log

**Processing steps per well folder** (`robocam.postprocess.process_well`):
1. Load `camera_meta.json` and the well's `*_metadata.json`
2. Load each `.npy` frame
3. Debayer using the pattern/bit-depth from `camera_meta.json` (same code path for both backends; correctness for Picamera2 raw data is under investigation — see Known Issues)
4. Write PNG files to `images/<well>/`
5. Encode MP4 and/or VFR MKV using per-frame timestamps

---

## Known Issues

- **Pi camera (Picamera2) raw burst → color output is currently wrong.** Something between `Camera.get_raw_frame()`'s `capture_array("raw")` and `postprocess.npy_to_bgr()`'s debayer/scaling is mismatched — likely the actual bit depth/packing of the raw stream vs. what `camera_meta.json` claims. The PlayerOne backend path is unaffected and has been verified end-to-end (including laser-timed bursts) on real hardware. See `PROJECT_STATE.md` § 9 for the investigation notes.
- **PlayerOne effective capture rate is ~30fps, well under the Mars 662M's advertised 90-120fps.** Jitter/robustness causes (synchronous disk writes, poll-loop latency, buffer allocation, cross-tab lock contention) are fixed in software and verified in `simulate=True` mode; the fps *ceiling* causes (exposure, `POA_HQI`, USB bandwidth, sensor mode) are exposed as UI controls but not yet confirmed on real hardware. See `PROJECT_STATE.md` § 9.
- **Klipper motion backend is implemented but not yet exercised on real Klipper hardware** — only Marlin has been run end-to-end so far.

## Open Items

- [ ] Root-cause and fix the Pi camera raw-burst debayer/bit-depth bug above
- [ ] Verify the PlayerOne jitter fixes (queue/writer thread, direct blocking `GetImageData`, buffer reuse, grabber-pause broadcast) actually improve real fps/stability, and find the ceiling fix via the new HQI/USB-bandwidth/sensor-mode/exposure UI controls — camera unavailable until 2026-07-06
- [ ] Benchmark Pi camera max FPS at 1920×1080 with video+raw config
- [ ] Verify the Klipper backend against a real Moonraker/Klipper setup
- [x] Build Processing tab UI — done, verified working
- [x] Decouple raw-burst disk writes from acquisition via bounded queue + writer thread — done, verified in simulate mode
- [x] Fix cross-tab live-preview lock contention during raw-burst capture — done, verified offscreen
- [x] Add auto-process checkbox to Experiment tab — done, verified working
