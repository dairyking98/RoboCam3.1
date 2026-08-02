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
- Bayer pattern is read from the SDK's `isColorCamera`/`bayerPattern_` fields (`GetCameraProperties()`) as of 2026-07-06, not hardcoded — the Mars 662M is a monochrome sensor, and demosaicing mono data as if it had a Bayer filter (the previous hardcoded `"RGGB"` behavior) produces color-interpolation artifacts rather than clean grayscale. See `PROJECT_STATE.md` § 9 for the fix, unverified until hardware is back.
- **All of a well's frames are stacked into one memory-mapped `.npy` array** (`(n_frames, H, W)`, written incrementally via `numpy.lib.format.open_memmap()`) — changed 2026-07-06 from one file per frame, specifically to make transporting captures to another machine for processing practical (thousands of small files per well were the dominant transfer cost, not raw byte size). See "Stacked-array format" below.
- Per-frame timestamps via `time.perf_counter()`
- **FPS is exposure-bound and confirmed at ceiling on real hardware (2026-07-08).** The old ~30fps figure (well under the Mars 662M's advertised 90-120fps, measured across the 2026-07-01 test dataset) was jitter/overhead in the capture loop, not a real ceiling — fixed via the acquisition/writer decoupling below, direct blocking `GetImageData()`, and a preallocated frame buffer. Real hardware: 20ms exposure → 50.11fps (ceiling 50fps); 10ms exposure → 94fps (ceiling 100fps). `POA_HQI`/`POA_USB_BANDWIDTH_LIMIT`/sensor-mode selection are exposed as live UI controls (Calibration tab) but weren't the bottleneck — HQI off/bandwidth 100/offset 0/0 selectable modes was the confirmed-working hardware config. See `PROJECT_STATE.md` § 9 for the full writeup.
- Capture is decoupled: an acquisition thread pushes frames onto a bounded queue (`RAW_BURST_QUEUE_MAXSIZE` in `experiment.py`), and a separate writer thread writes each frame into its slot in the stacked array plus an incremental `<well>_<ts>_frames.jsonl` sidecar (crash-resilient per-frame timing, independent of the final `metadata.json`). The queue is always fully drained before a burst returns — no captured frame is ever dropped, even on `stop()`.
- Each well's `metadata.json` now also includes `frames_file` (the stacked array's filename), `capture_failures` (lock-timeout / SDK-timeout-or-error counts), `sdk_dropped_frames` (the SDK's own dropped-frame counter), and `queue_full_stalls`/`queue_full_stall_s_total` (how often/how long acquisition blocked waiting on a full write queue).
- **Finalize (flush + trim) is a backgrounded spawn subprocess, not part of the writer thread (PR #14).** The writer thread above handles per-frame writes during capture; once a well's capture ends, `stack.flush()` (msync) and `_trim_raw_stack()` are handed off to `_finalize_raw_burst_process()` — a `multiprocessing.get_context("spawn").Process` — so the expensive flush overlaps with the next well's movement instead of blocking it. This replaced an earlier thread-based overlap attempt that hardware showed never actually worked (a Python thread calling a non-GIL-releasing flush stalls the whole process regardless). `spawn` (not `fork`) is required because the parent holds live camera/motion connections `fork` would duplicate into the child. Verified on real hardware: 157.96s actual vs. 158s estimated capture-complete time (0.03% error), zero dropped frames, "Moving to next well" firing 10ms after capture ends.

**Stacked-array format**: the array is preallocated to `total_duration_s × RAW_BURST_FPS_CEILING_ESTIMATE` rows (a ceiling constant comfortably above the camera's advertised 90-120fps max), since true achieved fps isn't known ahead of time and the array's shape must be fixed at creation. Unwritten trailing rows are **sparse** on ext4/NVMe — no real disk cost — and are never trimmed; `frames_captured` in `metadata.json` is the only authoritative frame count, never the array's `.shape[0]`. **A memory-mapped write that runs out of backing disk space raises SIGBUS, which can't be caught by Python** (unlike a plain `np.save()` failing with a catchable `OSError`) — the writer thread proactively checks `shutil.disk_usage(...).free` against `MIN_FREE_DISK_BYTES` on the same cadence as its periodic flush and aborts cleanly well before that could happen. **Transfer caveat**: naive `cp`, drag-and-drop, or copying onto a non-sparse-aware filesystem (e.g. an exFAT external drive) will materialize the full preallocated size — use sparse-aware tools (`tar --sparse`, `rsync --sparse`, `cp --sparse=always`) for any packaging/transfer of this data.

**Output folder layout (actual, as written by `ExperimentRunner`):**
```
<exp_dir>/
  raw/
    camera_meta.json                 ← written once per experiment: backend, model, bit depth, resolution,
                                        gain, exposure, fps, hqi_enabled, usb_bandwidth_limit, offset,
                                        sensor_mode_index, sensor_mode_name
    <well>_<ts>_stack.npy            ← one memory-mapped (n_frames, H, W) array for the whole well
    <well>_<ts>_frames.jsonl         ← one JSON line per frame, appended as captured (crash-resilient;
                                        not read by postprocess.py — a recovery artifact only)
    <well>_<ts>_metadata.json        ← frames_file, frames[] (frame_index, time_offset_s), laser_events[],
                                        fps_average, duration_actual_s, capture_failures,
                                        sdk_dropped_frames, queue_full_stalls, queue_full_stall_s_total
  <ts>_<name>_points.csv
```

Pre-2026-07-06 captures (e.g. the 2026-07-01 test dataset) used one `.npy` file per frame instead — `postprocess.py` still reads that format too (no `frames_file` key present is how it tells the two apart), so older data doesn't need migrating.

Note: timestamps and laser events live inside the per-well `*_metadata.json`, not in separate `timestamps.json`/`laser_events.json` files as an earlier draft of this document assumed.

**Finalize-time ETA (`_estimate_finalize_time_s()`, `experiment.py`):** mirrors the same preallocation-sizing formula the stack itself uses (resolution × bit depth × buffered frame count) and divides by a measured-bandwidth constant (700MB/s, conservatively rounded down from a reference hardware run's implied 738MB/s), so the estimate scales with resolution/fps instead of being one flat number. Replaced an earlier flat `RAW_BURST_FINAL_FINALIZE_ESTIMATE_S = 2.5` constant that was only correct at the exact settings it was measured at (a 1024×768@30fps run's true finalize time is ~0.28s, not 2.5s). Verified against three hardware runs at different resolution/fps combos, within 5% of measured. The very last well's finalize has no next well to overlap with, so `run()`'s `finally:` block still waits for it after "Experiment finished." is logged — a known, accounted-for tail cost.

---

## Image-Mode Capture-Time Estimate (PR #14)

`IMAGE_CAPTURE_TIME_ESTIMATE_S = 0.3` had never actually been validated on hardware — every archived experiment had been raw-burst mode. A 24-well hardware run at 1936×1100 JPEG measured ~0.055s/well actual (0.047-0.062s range), 6x lower than the guess.

Image mode's default format was then switched from JPEG to **PNG** (lossless, no compression artifacts — any speed tradeoff deferred to post-processing), which broke the JPEG-only estimate for the new default. `cv2.imwrite()`'s cost really does differ by format — measured at 1936×1100: jpg ~0.054s/well, tif ~0.110s/well (disk-write-bound), png ~0.140s/well (CPU-bound deflate).

The per-well cost was then split into a resolution-independent fixed part (exposure + SDK readback + call overhead) and a per-pixel encode/write part that scales with resolution — measured at a second resolution (640×480): tif fixed=0.0391s + 3.31e-8s/px, png fixed=0.0401s + 4.70e-8s/px (the two formats' fixed components landing within 2.5% of each other is a good sign the split is real). Finally, the fixed part's exposure component (all calibration runs happened to share ~33ms exposure, silently baked into the constant) was pulled out into a live `camera.get_exposure()` query, so a real exposure change adds real time instead of vanishing.

Current model: `_estimate_image_capture_time_s()` = `IMAGE_CAPTURE_BASE_OVERHEAD_S` (0.0066s) + live `exposure_s` + `IMAGE_CAPTURE_PER_PIXEL_S_BY_FORMAT[format] × width × height` (falls back to png's rate for an unrecognized format). Verified against 5 hardware runs across two resolutions and three formats, all within ~2% of measured (e.g. jpg hi-res 0.055 predicted vs. 0.055 measured; tif hi/lo 0.110/0.050 vs. 0.110/0.049; png hi/lo 0.140/0.054 vs. 0.140/0.055).

---

### Raspberry Pi Camera (picamera2)

**Mode name in UI:** `Raw Burst`

- Uses a **video configuration with a raw stream** — this is the only way to get burst-rate raw frames; still configuration adds inter-frame latency
- Captures via `capture_array("raw")` — true Bayer pattern data, no ISP processing
- Bit depth: 10-bit (Camera Module 3) or 12-bit (HQ Camera), unpacked to uint16 in the array
- Frames are stacked into one `<well>_<ts>_stack.npy` array per well, same as the PlayerOne path (see above) — this backend shares `_write_raw_burst()`, nothing Picamera2-specific about the storage format
- Per-frame timestamps via `time.perf_counter()`
- `camera_meta.json` **must** include Bayer metadata (see below) for correct reconstruction
- **Auto-exposure/auto-gain is explicitly disabled at connect** (`_init_picam2()`, fixed 2026-07-06) — previously it was left running until the user manually applied exposure/gain via the Calibration tab, and for darkfield (mostly-black) scenes AE chased a "properly exposed" brightness that drove exposure time way up, measured at only ~15fps on real hardware. `ae_enabled` is now a live-adjustable UI control (Calibration tab, Picamera2 only) and recorded in `camera_meta.json`. See `PROJECT_STATE.md` § 9 — unverified until the Pi camera is available again.

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
- Output options (as of 2026-07-29, each independently toggleable, never mixed into one folder): PNG, JPEG, MP4 (constant fps, presentation), VFR MKV (accurate timing, archival). Images can optionally be packaged into one `.zip` per experiment instead of loose files, streamed directly into the archive rather than written loose then zipped.
- **Auto-process after experiment** checkbox in the Experiment tab, which queues and starts processing automatically the moment a run finishes
- Per-well and overall progress bars, scrolling log

**Processing steps per well folder** (`robocam.postprocess.process_well`):
1. Load `camera_meta.json` and the well's `*_metadata.json`. If `frames_file` is present, open that one stacked array with `mmap_mode="r"` (current format); otherwise fall back to opening each frame's individual `.npy` file named in `frames[].file` (pre-2026-07-06 data).
2. Index into the stack (or load the per-frame file) for each frame
3. Debayer using the pattern/bit-depth from `camera_meta.json` (same code path for both backends; correctness for Picamera2 raw data is under investigation — see Known Issues)
4. Write PNG/JPEG files to `images_png/<well>/` and/or `images_jpeg/<well>/` — or straight into `images_png.zip`/`images_jpeg.zip` (one archive per experiment, all wells) if zip packaging is enabled
5. Encode MP4 to `videos_mp4/` and/or VFR MKV to `videos_vfr/` using per-frame timestamps. VFR's codec defaults to **ffv1** (lossless) as of 2026-07-29 — see size comparison below; MP4 always uses `libx264` regardless, for small/universally-playable presentation output.

**Note on the video path vs. raw content**: `draw_laser_indicator()` burns a white asterisk overlay directly into the frame for any laser-on timestamp, but only on the copy fed to the video encoders — the PNG/JPEG path never sees it. So VFR/MP4 frames captured during a laser-on window are not bit-identical to the source `.npy`/PNG even when the video codec itself is lossless; PNG/JPEG are the ones that preserve the frame content unmodified (aside from the >8-bit-down-to-uint8 rescale in `npy_to_bgr` when the sensor exceeds 8-bit).

### Export format size comparison (2026-07-29)

Measured on real hardware data — `20260728_121408_Jul27_test1` on RoboCam
(`/mnt/nvme/RoboCam3.1/output/`), 3 wells (A1, B3, C5), Mars 662M mono, 8-bit,
1280×960, 908 frames total. This experiment happened to get processed once
before and once after the format-split/ffv1 change landed, on identical raw
data, so every format below is a true apples-to-apples comparison (confirmed
PNG bytes are bit-identical whether loose or zipped, as expected from
`ZIP_STORED`).

| Format | Total size (3 wells) | % of raw | Lossless? |
|---|---|---|---|
| Raw `.npy` | 1,064.0 MiB | 100% | — |
| PNG stack (loose or zipped — identical bytes) | 743.6 MiB | 69.9% | Yes |
| **VFR ffv1 (new default)** | **673.6 MiB** | **63.3%** | **Yes** |
| JPEG stack (q95, zipped) | 469.8 MiB | 44.2% | No |
| MP4 display (libx264) | 225.9 MiB | 21.2% | No |
| VFR libx264 crf18 (old default) | 213.8 MiB | 20.1% | No |

Takeaways:
- **ffv1 beats a zipped PNG stack on real sensor footage** (~9.4% smaller)
  while staying fully lossless — a synthetic blob+noise test earlier
  predicted ~14%; real sensor noise gives somewhat less compression headroom
  but the direction holds.
- PNG only buys ~30% off raw size here — real sensor noise doesn't compress
  well under plain deflate, unlike synthetic gradient content.
- The old libx264-crf18 "VFR" and the MP4 display stream land almost
  identically (213.8 vs 225.9 MiB) since they're the same codec/CRF, just a
  different container — the old VFR default wasn't buying any fidelity over
  the display MP4, only more-accurate timestamps.
- See the note above: none of the video formats (old or new codec) are
  bit-identical to raw for laser-on frames, because of the burned-in laser
  overlay — this is independent of codec losslessness.

**2026-07-29 correction**: both VFR files measured above (ffv1 and the old
libx264-crf18 one) were produced before a bug fix to
`mkv_s.codec_context.time_base` (see the `f4de2c8` commit). The bug corrupted
the muxed container's *duration and avg_frame_rate metadata* only — a
303-frame/~10s file was reporting itself as ~90,000fps and 300,330 frames to
any reader that trusts that metadata (confirmed via Fiji reporting exactly
that on real footage, and likely the cause of VLC's scrub-bar glitches on VFR
files too). It did **not** touch the encoded pixel data, the actual per-frame
PTS values used internally, or the file size — so every number in the table
above is still accurate. Only the container-level duration/frame-count a tool
reports was wrong; the sizes and the lossless/lossy classification stand as
measured.

---

## Known Issues

- **Pi camera (Picamera2) raw burst → color output is currently wrong.** Leading hypothesis (2026-07-06, unconfirmed): the raw format is CSI-2 **packed** (e.g. `"SRGGB10_CSI2P"`) — a genuinely bit-packed byte layout (10-bit: 4 pixels packed into 5 bytes), not one `uint16` per pixel as `get_raw_frame()`'s comment assumes. If `capture_array("raw")` doesn't auto-unpack that, the array's shape/dtype don't correspond to a real pixel grid, and `postprocess.npy_to_bgr()`'s scaling+demosaic run on bit-scrambled data. See `PROJECT_STATE.md` § 9 for the full reasoning and the diagnostic check to run next Pi session.
- **Klipper motion backend is implemented but not yet exercised on real Klipper hardware** — only Marlin has been run end-to-end so far.

**Resolved, hardware-confirmed:**
- ~~PlayerOne `bayer_pattern` hardcoded to `"RGGB"`~~ — fixed and visually confirmed 2026-07-08: reads `isColorCamera`/`bayerPattern_` from the SDK, clean mono grayscale output on the Mars 662M.
- ~~PlayerOne effective capture rate ~30fps~~ — confirmed exposure-bound and at ceiling on real hardware 2026-07-08 (50.11fps @ 20ms exposure, 94fps @ 10ms exposure). See § "Capture Modes by Camera" above.
- ~~Raw-burst throughput still 25-36% below target at high fps/resolution~~ — fixed via spawn-subprocess finalize (PR #14), verified across 5 hardware runs, ETA within 0.03-0.1% of actual.

## Open Items

- [ ] Test the CSI-2 packing hypothesis for the Pi camera debayer bug (print `arr.dtype`/`arr.shape` from `capture_array("raw")`, compare against expected pixel width — see `PROJECT_STATE.md` § 9) and fix if confirmed
- [ ] Benchmark Pi camera max FPS at 1920×1080 with video+raw config
- [ ] Verify the Klipper backend against a real Moonraker/Klipper setup
- [x] Build Processing tab UI — done, verified working
- [x] Decouple raw-burst disk writes from acquisition via bounded queue + writer thread — done, verified in simulate mode
- [x] Fix cross-tab live-preview lock contention during raw-burst capture — done, verified offscreen
- [x] Add auto-process checkbox to Experiment tab — done, verified working
- [x] Verify the PlayerOne jitter fixes and find the fps ceiling — done, hardware-confirmed exposure-bound 2026-07-08
- [x] Fix raw-burst finalize blocking the next well's capture (PR #14) — done, hardware-verified spawn-subprocess overlap
- [x] Calibrate image-mode capture-time ETA against real hardware, format- and resolution-aware (PR #14) — done, see § "Image-Mode Capture-Time Estimate" above
