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
- Bit depth: 16-bit (sensor-native)
- Each frame saved as a `.npy` file (NumPy binary array)
- Per-frame timestamps via `time.perf_counter()`
- Max achievable FPS depends on camera model, resolution, and USB bandwidth

**Output folder layout:**
```
<exp_dir>/
  <well>_<timestamp>/
    frame_000000.npy
    frame_000001.npy
    ...
    camera_meta.json     ← written once: model, bit depth, resolution, gain, exposure
    timestamps.json      ← per-frame: {frame_index, time_offset_s}
    laser_events.json    ← if laser used: [{time_offset_s, state, frame_index}, ...]
```

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

### Existing CLI (`scripts/reconstruct_vfr.py`)

Handles PlayerOne `.npy` → PNG images + VFR MKV + display MP4. Needs to be extended for Pi camera (different debayer path).

**Planned extension:**
- Detect `backend` in `camera_meta.json`
- If `picamera2`: unpack Bayer, apply black-level subtraction, debayer with OpenCV using the pattern from metadata
- If `playerone`: existing path (mono or RGB debayer depending on sensor)

### Planned Processing Tab (GUI)

A new tab in the main window for post-processing experiment output folders.

**Layout:**
- Folder list: add/remove one or more experiment output folders
- Per-folder: show well count, frame count, camera backend, estimated output size
- Output options:
  - [ ] PNG image sequence (one folder per well)
  - [ ] MP4 video (constant FPS, for presentation)
  - [ ] VFR MKV (variable frame rate, accurate timing, for archival)
- **Auto-process after experiment** checkbox (also available in the Experiment tab)
- Progress bar per folder, overall progress
- Log/status output

**Processing steps per well folder:**
1. Load `camera_meta.json` and `timestamps.json`
2. Load each `.npy` frame
3. If Pi camera: subtract black level, debayer (OpenCV `cvtColor`), scale to 8-bit for output
4. If PlayerOne: existing debayer path
5. Write PNG files to `images/<well>/`
6. Encode MP4 and/or VFR MKV using per-frame timestamps

---

## Open Items

- [ ] Benchmark Pi camera max FPS at 1920×1080 with video+raw config
- [ ] Confirm PlayerOne `camera_meta.json` already captures all fields needed by `reconstruct_vfr.py`
- [ ] Implement Pi camera raw stream config in `camera.py`
- [ ] Extend `reconstruct_vfr.py` with Pi camera debayer path
- [ ] Build Processing tab UI
- [ ] Add auto-process checkbox to Experiment tab
- [ ] Update `PROJECT_STATE.md` capture modes section once implementation is done
