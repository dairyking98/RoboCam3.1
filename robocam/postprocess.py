"""
robocam/postprocess.py — core .npy burst → images + video pipeline.

Handles both camera backends:
  playerone  : 8-bit RGGB Bayer (current SDK config).
  picamera2  : 10/12-bit Bayer, pattern and bit-depth read from camera_meta.json.

The sidecar camera_meta.json is written once per experiment (by ExperimentRunner)
into the raw/ directory alongside the *_metadata.json well files.
"""
from __future__ import annotations

import json
import os
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

try:
    import av
    AV_AVAILABLE = True
except ImportError:
    AV_AVAILABLE = False

# 90 kHz time base — ~11 µs PTS resolution, compatible with MKV muxer.
_TIME_BASE = Fraction(1, 90_000)

_LASER_FONT       = cv2.FONT_HERSHEY_SIMPLEX
_LASER_FONT_SCALE = 3.5
_LASER_OUTLINE_T  = 10
_LASER_FILL_T     = 4

# libcamera ColorFilterArrangement enum → string
_CFA_MAP = {0: "RGGB", 1: "GRBG", 2: "BGGR", 3: "GBRG", 4: "mono"}

# Bayer pattern string → OpenCV debayer code (→ BGR output)
_BAYER_TO_CV2 = {
    "RGGB": cv2.COLOR_BAYER_RG2BGR,
    "BGGR": cv2.COLOR_BAYER_BG2BGR,
    "GRBG": cv2.COLOR_BAYER_GR2BGR,
    "GBRG": cv2.COLOR_BAYER_GB2BGR,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def laser_on_at(t: float, laser_events: list) -> bool:
    """Return True if the laser was ON at time t."""
    state = False
    for ev in laser_events:
        if ev["time_offset_s"] <= t:
            state = ev["state"] == "ON"
    return state


def draw_laser_indicator(frame_bgr: np.ndarray) -> None:
    """Draw a white asterisk with black outline in the top-right corner, in-place."""
    h, w = frame_bgr.shape[:2]
    x, y = w - 90, 80
    cv2.putText(frame_bgr, "*", (x, y), _LASER_FONT,
                _LASER_FONT_SCALE, (0, 0, 0), _LASER_OUTLINE_T, cv2.LINE_AA)
    cv2.putText(frame_bgr, "*", (x, y), _LASER_FONT,
                _LASER_FONT_SCALE, (255, 255, 255), _LASER_FILL_T, cv2.LINE_AA)


def npy_to_bgr(arr: np.ndarray, mono: bool,
               camera_meta: Optional[dict] = None) -> np.ndarray:
    """
    Convert a raw .npy Bayer array to BGR uint8.

    camera_meta fields used (all optional, fall back to PlayerOne defaults):
      bayer_pattern : "RGGB" | "BGGR" | "GRBG" | "GBRG" | "mono"
      bit_depth     : int (e.g. 8, 10, 12)
    """
    if arr.ndim == 3:
        arr = arr[:, :, 0]

    meta = camera_meta or {}
    bayer  = meta.get("bayer_pattern", "RGGB")
    depth  = int(meta.get("bit_depth", 8))

    # Scale >8-bit data down to uint8
    if depth > 8:
        max_val = (1 << depth) - 1
        arr = (arr.astype(np.float32) / max_val * 255).clip(0, 255).astype(np.uint8)
    else:
        arr = arr.astype(np.uint8)

    if mono or bayer == "mono":
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)

    debayer_code = _BAYER_TO_CV2.get(bayer, cv2.COLOR_BAYER_RG2BGR)
    return cv2.cvtColor(arr, debayer_code)


def find_metadata_files(path: str) -> tuple[list[Path], Path]:
    """
    Return (list of *_metadata.json paths, experiment root directory).
    Raises ValueError if nothing useful is found.
    """
    p = Path(path)
    if p.is_file() and "metadata" in p.name and p.suffix == ".json":
        exp_dir = p.parent.parent if p.parent.name == "raw" else p.parent
        return [p], exp_dir
    if p.is_dir():
        raw_sub = p / "raw"
        search  = raw_sub if raw_sub.is_dir() else p
        files   = sorted(search.glob("*_metadata.json"))
        if not files:
            raise ValueError(f"No *_metadata.json files found in {path}")
        return files, p
    raise ValueError(f"Path must be a metadata JSON or experiment directory: {path}")


def parse_meta_name(meta_path: Path) -> tuple[str, str]:
    """Return (well, exp_timestamp) from a metadata filename like A1_20260625_133324_metadata.json."""
    parts = meta_path.stem.split("_")
    well   = parts[0]
    exp_ts = f"{parts[1]}_{parts[2]}" if len(parts) >= 3 else "unknown"
    return well, exp_ts


def open_export_zip(exp_dir: Path, fmt: str) -> zipfile.ZipFile:
    """
    Open exp_dir/images_<fmt>.zip for streaming frame writes, one archive per
    experiment (all wells share it — call once before the well loop and close
    after). ZIP_STORED (no re-compression): PNG/JPEG bytes are already
    compressed, so DEFLATE would just burn CPU for no size benefit.
    """
    return zipfile.ZipFile(exp_dir / f"images_{fmt}.zip", mode="w",
                            compression=zipfile.ZIP_STORED)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def process_well(
    meta_path: Path,
    exp_dir: Path,
    codec: str = "ffv1",
    crf: int = 18,
    mono: bool = False,
    do_png: bool = True,
    do_jpeg: bool = False,
    do_mp4: bool = True,
    do_vfr: bool = True,
    jpeg_quality: int = 95,
    zip_png: Optional[zipfile.ZipFile] = None,
    zip_jpeg: Optional[zipfile.ZipFile] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    """
    Process one well: .npy burst → PNG and/or JPEG image sequence, and/or
    VFR MKV / display MP4 video. Each output format gets its own directory
    (images_png/, images_jpeg/, videos_mp4/, videos_vfr/) — formats are never
    mixed into the same folder.

    zip_png/zip_jpeg: pass an already-open zipfile.ZipFile (see
    open_export_zip) to stream that format's frames straight into a
    per-experiment .zip instead of writing loose files — the caller owns the
    zip's lifetime since it spans every well in the experiment, not just this
    one. Leave as None for loose-file output.

    progress_callback(current_frame, total_frames) is called periodically
    during the frame loop.
    """
    if (do_mp4 or do_vfr) and not AV_AVAILABLE:
        raise RuntimeError("PyAV not installed — cannot encode video. "
                           "Install with: pip install av")

    meta_dir = meta_path.parent

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    # Load camera metadata if present (written once per experiment into raw/)
    cam_meta_path = meta_dir / "camera_meta.json"
    camera_meta: dict = {}
    if cam_meta_path.exists():
        with open(cam_meta_path, encoding="utf-8") as f:
            camera_meta = json.load(f)

    frames_info  = sorted(meta.get("frames", []), key=lambda x: x["frame_index"])
    laser_events = meta.get("laser_events", [])
    n            = len(frames_info)

    if not frames_info:
        print(f"  [skip] no frames in {meta_path.name}")
        return

    well, exp_ts = parse_meta_name(meta_path)
    duration     = meta.get("duration_actual_s", 0)
    fps_avg      = meta.get("fps_average", meta.get("fps_actual", 0))

    # Mono sensors (e.g. PlayerOne Mars 662M) have no real color data.
    # Encoding the display MP4 as true single-channel (pix_fmt "gray") was
    # tried and reverted — Fiji's video importer decodes it back to RGB
    # anyway (decoders resynthesize chroma for generic playback
    # compatibility) and it bloated file size ~50x on real hardware footage.
    # PNGs don't have that problem (color-type metadata isn't decoded away),
    # so is_mono is only used below to write a true single grayscale channel
    # for the image-sequence output, not the video.
    is_mono = mono or camera_meta.get("bayer_pattern", "RGGB") == "mono"

    print(f"  {n} frames  |  {duration:.3f}s  |  {fps_avg:.2f} fps avg")
    for ev in laser_events:
        print(f"    laser {ev['state']:3s}  t={ev['time_offset_s']:.3f}s  frame {ev['frame_index']}")

    # New format: all of a well's frames are stacked into one memory-mapped
    # (n_frames, H, W) array named by the "frames_file" metadata key, and
    # per-frame entries no longer carry a "file" key — index by frame_index
    # instead of opening a separate file per frame. Old format (pre-2026-07,
    # e.g. the 2026-07-01 test dataset): each frame is its own .npy file
    # named in fi["file"]; kept working unchanged below.
    frames_file = meta.get("frames_file")
    if frames_file:
        stack = np.load(meta_dir / frames_file, mmap_mode="r")
        # stack.shape[0] is the preallocated ceiling, not the real count —
        # frames_info (from frames_captured) is the only authoritative count.
        _, h, w = stack.shape
    else:
        stack = None
        first_arr = np.load(meta_dir / frames_info[0]["file"])
        # Raw arrays from Pi camera are at sensor resolution; use their actual shape
        if first_arr.ndim == 3:
            h, w = first_arr.shape[:2]
        else:
            h, w = first_arr.shape

    # After debayering, pixel dimensions are the same (cv2 Bayer → BGR preserves h×w)

    img_png_dir  = exp_dir / "images_png" / well
    img_jpeg_dir = exp_dir / "images_jpeg" / well
    mp4_dir      = exp_dir / "videos_mp4"
    vfr_dir      = exp_dir / "videos_vfr"
    if do_png and zip_png is None:
        img_png_dir.mkdir(parents=True, exist_ok=True)
    if do_jpeg and zip_jpeg is None:
        img_jpeg_dir.mkdir(parents=True, exist_ok=True)
    if do_mp4:
        mp4_dir.mkdir(parents=True, exist_ok=True)
    if do_vfr:
        vfr_dir.mkdir(parents=True, exist_ok=True)

    mkv_path = str(vfr_dir / f"{well}_{exp_ts}_vfr.mkv")
    mp4_path = str(mp4_dir / f"{well}_{exp_ts}.mp4")

    display_fps = (Fraction(n, 1) / Fraction(round(duration * 1000), 1000)
                   if duration > 0 else Fraction(30))

    mkv_con = mkv_s = mp4_con = mp4_s = None
    if do_vfr:
        mkv_con        = av.open(mkv_path, "w")
        mkv_s          = mkv_con.add_stream(codec, rate=90_000)
        mkv_s.width    = w
        mkv_s.height   = h
        mkv_s.pix_fmt  = "yuv420p"
        mkv_s.time_base = _TIME_BASE
        if codec in ("libx264", "libx265"):
            mkv_s.options = {"crf": str(crf), "preset": "medium", "bframes": "0"}

    if do_mp4:
        mp4_con        = av.open(mp4_path, "w")
        mp4_s          = mp4_con.add_stream("libx264", rate=display_fps)
        mp4_s.width    = w
        mp4_s.height   = h
        mp4_s.pix_fmt  = "yuv420p"
        mp4_s.options  = {"crf": str(crf), "preset": "medium",
                          "profile": "baseline", "bframes": "0"}

    try:
        for i, fi in enumerate(frames_info):
            arr    = stack[fi["frame_index"]] if stack is not None else np.load(meta_dir / fi["file"])
            bgr    = npy_to_bgr(arr, mono, camera_meta)
            is_on  = laser_on_at(fi["time_offset_s"], laser_events)

            if do_png or do_jpeg:
                t_ms      = int(fi["time_offset_s"] * 1_000)
                laser_str = "laser-on" if is_on else "laser-off"
                base_name = f"{well}_{fi['frame_index']:05d}_{t_ms:06d}ms_{laser_str}"
                # PNG color-type metadata is unambiguous (unlike H.264 chroma
                # flags, which decoders resynthesize to RGB on playback) — so
                # this is what actually makes Fiji's Image Sequence importer
                # produce an 8-bit stack for mono sensors, not the mp4 fix above.
                img_out = bgr[:, :, 0] if is_mono else bgr

            if do_png:
                png_name = f"{base_name}.png"
                if zip_png is not None:
                    ok, buf = cv2.imencode(".png", img_out)
                    zip_png.writestr(f"{well}/{png_name}", buf.tobytes())
                else:
                    cv2.imwrite(str(img_png_dir / png_name), img_out)

            if do_jpeg:
                jpg_name = f"{base_name}.jpg"
                jpg_params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
                if zip_jpeg is not None:
                    ok, buf = cv2.imencode(".jpg", img_out, jpg_params)
                    zip_jpeg.writestr(f"{well}/{jpg_name}", buf.tobytes())
                else:
                    cv2.imwrite(str(img_jpeg_dir / jpg_name), img_out, jpg_params)

            if do_mp4 or do_vfr:
                vid_frame = bgr.copy()
                if is_on:
                    draw_laser_indicator(vid_frame)

                if do_vfr:
                    mkv_frame     = av.VideoFrame.from_ndarray(vid_frame, format="bgr24")
                    mkv_frame     = mkv_frame.reformat(format="yuv420p")
                    mkv_frame.pts = int(fi["time_offset_s"] * 90_000)
                    for packet in mkv_s.encode(mkv_frame):
                        mkv_con.mux(packet)

                if do_mp4:
                    mp4_frame     = av.VideoFrame.from_ndarray(vid_frame, format="bgr24")
                    mp4_frame     = mp4_frame.reformat(format="yuv420p")
                    mp4_frame.pts = i
                    for packet in mp4_s.encode(mp4_frame):
                        mp4_con.mux(packet)

            if progress_callback:
                progress_callback(i + 1, n)
            elif (i + 1) % 50 == 0 or (i + 1) == n:
                tags = "/".join(filter(None, [
                    "png"  if do_png  else "",
                    "jpeg" if do_jpeg else "",
                    "mp4"  if do_mp4  else "",
                    "vfr"  if do_vfr  else "",
                ]))
                print(f"  [{tags}] {i + 1}/{n}", end="\r", flush=True)

        if do_vfr:
            for packet in mkv_s.encode(): mkv_con.mux(packet)
        if do_mp4:
            for packet in mp4_s.encode(): mp4_con.mux(packet)

    finally:
        if mkv_con: mkv_con.close()
        if mp4_con: mp4_con.close()

    print()
    if do_png:
        if zip_png is not None:
            print(f"  png   → {Path(zip_png.filename).name}  [{well}/]  ({n} frames, zipped)")
        else:
            count = sum(1 for _ in img_png_dir.glob("*.png"))
            print(f"  png   → {img_png_dir.relative_to(exp_dir)}  ({count} PNGs)")
    if do_jpeg:
        if zip_jpeg is not None:
            print(f"  jpeg  → {Path(zip_jpeg.filename).name}  [{well}/]  ({n} frames, zipped)")
        else:
            count = sum(1 for _ in img_jpeg_dir.glob("*.jpg"))
            print(f"  jpeg  → {img_jpeg_dir.relative_to(exp_dir)}  ({count} JPEGs)")
    if do_vfr:
        mkv_mb = os.path.getsize(mkv_path) / 1_048_576
        print(f"  vfr   → {Path(mkv_path).relative_to(exp_dir)}  ({mkv_mb:.1f} MB)  — VFR archival")
    if do_mp4:
        mp4_mb = os.path.getsize(mp4_path) / 1_048_576
        print(f"  mp4   → {Path(mp4_path).relative_to(exp_dir)}  ({mp4_mb:.1f} MB)  — {float(display_fps):.1f} fps display")
