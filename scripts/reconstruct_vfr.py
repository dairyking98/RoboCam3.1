#!/usr/bin/env python3
"""
reconstruct_vfr.py — unified pipeline: .npy burst → per-frame images + VFR video.

For each well in an experiment, in a single pass through the raw frames:

  1. Loads raw .npy frames from  raw/
  2. Debayers (Bayer RGGB → BGR) or passes through as mono
  3. Saves one lossless PNG per frame to  images/<well>/
       filename: <well>_f<idx>_<µs>us_laser-[on|off].png
       — clean, no overlay; suitable for object tracking / intensity analysis
  4. Adds an asterisk (*) to the top-right corner when laser is ON
  5. Encodes the overlaid frames as a VFR MKV to  videos/
       filename: <well>_<experiment-timestamp>_vfr.mkv
       — variable frame-rate with per-frame PTS from the sidecar metadata

Output layout (written alongside the experiment's raw/ directory):

    outputs/20260625_133324_my_experiment/
      raw/                        ← .npy files + metadata JSONs  (source)
      images/
        A1/
          A1_f00000_000006203us_laser-off.png
          A1_f00152_005003994us_laser-on.png
          ...
        A2/
          ...
      videos/
        A1_20260625_133324_vfr.mkv
        A2_20260625_133324_vfr.mkv

Usage
-----
  # All wells in an experiment directory
  python scripts/reconstruct_vfr.py outputs/20260625_133324_my_experiment/

  # Single well metadata file
  python scripts/reconstruct_vfr.py outputs/exp/raw/A1_20260625_133324_metadata.json

  # Lossless video (ffv1 codec)
  python scripts/reconstruct_vfr.py outputs/exp/ --codec ffv1

  # Monochrome sensor — skip Bayer debayer, keep raw intensity values
  python scripts/reconstruct_vfr.py outputs/exp/ --mono

  # Images only (skip video encoding)
  python scripts/reconstruct_vfr.py outputs/exp/ --no-video

  # Video only (skip image export)
  python scripts/reconstruct_vfr.py outputs/exp/ --no-images

Requires: av  (pip install av)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np

# 90 kHz time base — ~11 µs PTS resolution; compatible with MKV muxer.
# rate=90_000 on the stream makes the codec's internal time base match so
# frame.pts values in 90kHz ticks are interpreted correctly.
TIME_BASE = Fraction(1, 90_000)

LASER_FONT       = cv2.FONT_HERSHEY_SIMPLEX
LASER_FONT_SCALE = 3.5
LASER_OUTLINE_T  = 10   # black outline — readable on any background
LASER_FILL_T     = 4    # white fill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def laser_on_at(t: float, laser_events: list) -> bool:
    """Return True if the laser was ON at time t (checks last event at or before t)."""
    state = False
    for ev in laser_events:
        if ev["time_offset_s"] <= t:
            state = ev["state"] == "ON"
    return state


def draw_laser_indicator(frame_bgr: np.ndarray) -> None:
    """Draw a white asterisk with black outline in the top-right corner, in-place."""
    h, w = frame_bgr.shape[:2]
    x = w - 90
    y = 80
    cv2.putText(frame_bgr, "*", (x, y), LASER_FONT,
                LASER_FONT_SCALE, (0, 0, 0), LASER_OUTLINE_T, cv2.LINE_AA)
    cv2.putText(frame_bgr, "*", (x, y), LASER_FONT,
                LASER_FONT_SCALE, (255, 255, 255), LASER_FILL_T, cv2.LINE_AA)


def npy_to_bgr(arr: np.ndarray, mono: bool) -> np.ndarray:
    """Convert a raw .npy array to BGR uint8 (debayers by default)."""
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    if mono:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_BAYER_RG2BGR)


def find_metadata_files(path: str) -> tuple[list[Path], Path]:
    """Return (list of metadata JSON paths, experiment root directory)."""
    p = Path(path)
    if p.is_file() and "metadata" in p.name and p.suffix == ".json":
        exp_dir = p.parent.parent if p.parent.name == "raw" else p.parent
        return [p], exp_dir
    if p.is_dir():
        raw_sub = p / "raw"
        search  = raw_sub if raw_sub.is_dir() else p
        files   = sorted(search.glob("*_metadata.json"))
        if not files:
            sys.exit(f"No *_metadata.json files found in {path}")
        return files, p
    sys.exit(f"Path must be a metadata JSON or experiment directory: {path}")


def parse_meta_name(meta_path: Path) -> tuple[str, str]:
    """Return (well, exp_timestamp) from a metadata filename like A1_20260625_133324_metadata.json."""
    parts = meta_path.stem.split("_")
    well   = parts[0]
    exp_ts = f"{parts[1]}_{parts[2]}" if len(parts) >= 3 else "unknown"
    return well, exp_ts


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def process_well(
    meta_path: Path,
    exp_dir: Path,
    codec: str = "libx264",
    crf: int = 18,
    mono: bool = False,
    do_images: bool = True,
    do_video: bool = True,
) -> None:
    meta_dir = meta_path.parent   # raw/ or exp root for legacy datasets

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    frames_info  = sorted(meta.get("frames", []), key=lambda x: x["frame_index"])
    laser_events = meta.get("laser_events", [])
    n            = len(frames_info)

    if not frames_info:
        print(f"  [skip] no frames listed in {meta_path.name}")
        return

    well, exp_ts = parse_meta_name(meta_path)
    duration     = meta.get("duration_actual_s", 0)
    fps_avg      = meta.get("fps_average", meta.get("fps_actual", 0))

    print(f"  {n} frames  |  {duration:.3f}s  |  {fps_avg:.2f} fps avg")
    for ev in laser_events:
        print(f"    laser {ev['state']:3s}  t={ev['time_offset_s']:.3f}s  frame {ev['frame_index']}")

    # Probe frame shape from first file
    first_arr = np.load(meta_dir / frames_info[0]["file"])
    h, w = first_arr.shape[:2]

    # Create output directories
    img_dir = exp_dir / "images" / well
    vid_dir = exp_dir / "videos"
    if do_images:
        img_dir.mkdir(parents=True, exist_ok=True)
    if do_video:
        vid_dir.mkdir(parents=True, exist_ok=True)

    mkv_path = str(vid_dir / f"{well}_{exp_ts}_vfr.mkv")
    mp4_path = str(vid_dir / f"{well}_{exp_ts}.mp4")

    # Display fps for MP4: use actual average so playback duration matches reality.
    display_fps = Fraction(n, 1) / Fraction(round(duration * 1000), 1000) if duration > 0 else Fraction(30)

    # Open both containers before the frame loop so we stream-encode in one pass.
    mkv_con = mkv_s = mp4_con = mp4_s = None
    if do_video:
        # --- MKV: VFR archival ---
        # rate=90_000 aligns the codec's internal time_base with TIME_BASE so
        # frame.pts in 90kHz ticks is interpreted correctly.
        # bframes=0 keeps PTS == DTS (no B-frame reordering).
        mkv_con        = av.open(mkv_path, "w")
        mkv_s          = mkv_con.add_stream(codec, rate=90_000)
        mkv_s.width    = w
        mkv_s.height   = h
        mkv_s.pix_fmt  = "yuv420p"
        mkv_s.time_base = TIME_BASE
        if codec in ("libx264", "libx265"):
            mkv_s.options = {"crf": str(crf), "preset": "medium", "bframes": "0"}

        # --- MP4: constant-fps display (H.264 baseline for Pi hardware decode) ---
        mp4_con        = av.open(mp4_path, "w")
        mp4_s          = mp4_con.add_stream("libx264", rate=display_fps)
        mp4_s.width    = w
        mp4_s.height   = h
        mp4_s.pix_fmt  = "yuv420p"
        mp4_s.options  = {"crf": str(crf), "preset": "medium",
                          "profile": "baseline", "bframes": "0"}

    try:
        for i, fi in enumerate(frames_info):
            arr = np.load(meta_dir / fi["file"])
            bgr = npy_to_bgr(arr, mono)
            is_on = laser_on_at(fi["time_offset_s"], laser_events)

            # -- Clean PNG image (no overlay) --
            if do_images:
                t_us      = int(fi["time_offset_s"] * 1_000_000)
                laser_str = "laser-on" if is_on else "laser-off"
                img_name  = f"{well}_{fi['frame_index']:05d}_{t_us:09d}us_{laser_str}.png"
                cv2.imwrite(str(img_dir / img_name), bgr)

            # -- Video frames (with laser asterisk overlay) --
            if do_video:
                vid_frame = bgr.copy()
                if is_on:
                    draw_laser_indicator(vid_frame)

                # MKV — VFR: PTS in 90kHz ticks from recording start
                mkv_frame     = av.VideoFrame.from_ndarray(vid_frame, format="bgr24")
                mkv_frame     = mkv_frame.reformat(format="yuv420p")
                mkv_frame.pts = int(fi["time_offset_s"] * 90_000)
                for packet in mkv_s.encode(mkv_frame):
                    mkv_con.mux(packet)

                # MP4 — constant fps: sequential integer PTS
                mp4_frame     = av.VideoFrame.from_ndarray(vid_frame, format="bgr24")
                mp4_frame     = mp4_frame.reformat(format="yuv420p")
                mp4_frame.pts = i
                for packet in mp4_s.encode(mp4_frame):
                    mp4_con.mux(packet)

            if (i + 1) % 50 == 0 or (i + 1) == n:
                tags = "/".join(filter(None, [
                    "images" if do_images else "",
                    "video"  if do_video  else "",
                ]))
                print(f"  [{tags}] {i + 1}/{n}", end="\r", flush=True)

        if do_video:
            for packet in mkv_s.encode(): mkv_con.mux(packet)
            for packet in mp4_s.encode(): mp4_con.mux(packet)

    finally:
        if mkv_con: mkv_con.close()
        if mp4_con: mp4_con.close()

    print()
    if do_images:
        count = sum(1 for _ in img_dir.glob("*.png"))
        print(f"  images → {img_dir.relative_to(exp_dir)}  ({count} PNGs)")
    if do_video:
        mkv_mb = os.path.getsize(mkv_path) / 1_048_576
        mp4_mb = os.path.getsize(mp4_path) / 1_048_576
        print(f"  videos/  {Path(mkv_path).name}  ({mkv_mb:.1f} MB)  — VFR archival")
        print(f"           {Path(mp4_path).name}  ({mp4_mb:.1f} MB)  — {float(display_fps):.1f} fps display")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert RoboCam .npy bursts to per-frame images and/or VFR video."
    )
    ap.add_argument("path",
        help="Experiment directory (containing raw/) or a specific *_metadata.json.")
    ap.add_argument("--codec", default="libx264",
        choices=["libx264", "libx265", "ffv1"],
        help="Video codec.  ffv1 = lossless.  (default: libx264)")
    ap.add_argument("--crf", type=int, default=18,
        help="CRF quality for libx264/libx265  (0=lossless, 18=high).  (default: 18)")
    ap.add_argument("--mono", action="store_true",
        help="Monochrome sensor — skip Bayer RGGB debayer.")
    ap.add_argument("--no-video", action="store_true",
        help="Export images only; skip video encoding.")
    ap.add_argument("--no-images", action="store_true",
        help="Encode video only; skip per-frame image export.")
    args = ap.parse_args()

    if args.no_video and args.no_images:
        sys.exit("Error: --no-video and --no-images together produces nothing.")

    meta_files, exp_dir = find_metadata_files(args.path)
    print(f"Experiment : {exp_dir.name}")
    print(f"Wells      : {len(meta_files)}")

    for meta_path in meta_files:
        well, exp_ts = parse_meta_name(meta_path)
        print(f"\n[{well}]  {meta_path.name}")
        process_well(
            meta_path, exp_dir,
            codec=args.codec,
            crf=args.crf,
            mono=args.mono,
            do_images=not args.no_images,
            do_video=not args.no_video,
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
