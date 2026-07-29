#!/usr/bin/env python3
"""
reconstruct_vfr.py — CLI wrapper for the RoboCam post-processing pipeline.

Converts .npy burst frames to per-frame PNG/JPEG images and/or MP4/VFR video.
Each output format gets its own directory (images_png/, images_jpeg/,
videos_mp4/, videos_vfr/) — formats are never mixed together in one folder.
Core logic lives in robocam/postprocess.py.

Usage
-----
  # All wells in an experiment directory
  # (default with no format flags: PNG + MP4, images auto-zipped)
  python scripts/reconstruct_vfr.py outputs/20260625_133324_my_experiment/

  # Single well metadata file
  python scripts/reconstruct_vfr.py outputs/exp/raw/A1_20260625_133324_metadata.json

  # Lossless video (ffv1 codec)
  python scripts/reconstruct_vfr.py outputs/exp/ --codec ffv1

  # Monochrome sensor — skip Bayer debayer
  python scripts/reconstruct_vfr.py outputs/exp/ --mono

  # Images only
  python scripts/reconstruct_vfr.py outputs/exp/ --png --jpeg

  # Video only
  python scripts/reconstruct_vfr.py outputs/exp/ --mp4 --vfr

  # Pick exactly the formats you want — naming any format flag switches off
  # the defaults, so only what you list here is produced
  python scripts/reconstruct_vfr.py outputs/exp/ --jpeg --vfr

  # PNG as loose files instead of the zip the default set uses (naming --png
  # explicitly bypasses the auto-zip; add --zip back if you still want it)
  python scripts/reconstruct_vfr.py outputs/exp/ --png --mp4

Requires: av  (pip install av)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on the path so robocam package is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from robocam.postprocess import find_metadata_files, parse_meta_name, process_well, open_export_zip


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert RoboCam .npy bursts to per-frame images and/or VFR video."
    )
    ap.add_argument("path",
        help="Experiment directory (containing raw/) or a specific *_metadata.json.")
    ap.add_argument("--codec", default="ffv1",
        choices=["libx264", "libx265", "ffv1"],
        help="Video codec for the VFR archival stream. ffv1 = lossless. (default: ffv1)")
    ap.add_argument("--crf", type=int, default=18,
        help="CRF quality for libx264/libx265 (0=lossless, 18=high). (default: 18)")
    ap.add_argument("--mono", action="store_true",
        help="Monochrome sensor — skip Bayer debayer.")
    ap.add_argument("--png", action="store_true",
        help="Export PNG image sequence (in its own images_png/ dir).")
    ap.add_argument("--jpeg", action="store_true",
        help="Export JPEG image sequence (in its own images_jpeg/ dir).")
    ap.add_argument("--jpeg-quality", type=int, default=95,
        help="JPEG quality, 0-100. (default: 95)")
    ap.add_argument("--mp4", action="store_true",
        help="Export MP4 display video.")
    ap.add_argument("--vfr", action="store_true",
        help="Export VFR MKV archival video.")
    ap.add_argument("--zip", action="store_true",
        help="Package each selected image format into one .zip per experiment "
             "(all wells) instead of loose files — easier to transfer.")
    args = ap.parse_args()

    # Pick exactly what's named on the command line; if nothing was named,
    # fall back to the same default set as the UI's default-checked boxes:
    # PNG + MP4, images auto-zipped for easy transfer.
    if any([args.png, args.jpeg, args.mp4, args.vfr]):
        do_png, do_jpeg, do_mp4, do_vfr = args.png, args.jpeg, args.mp4, args.vfr
        zip_images = args.zip
    else:
        do_png, do_jpeg, do_mp4, do_vfr = True, False, True, False
        zip_images = True

    try:
        meta_files, exp_dir = find_metadata_files(args.path)
    except ValueError as e:
        sys.exit(str(e))

    print(f"Experiment : {exp_dir.name}")
    print(f"Wells      : {len(meta_files)}")

    zip_png  = open_export_zip(exp_dir, "png")  if do_png  and zip_images else None
    zip_jpeg = open_export_zip(exp_dir, "jpeg") if do_jpeg and zip_images else None

    try:
        for meta_path in meta_files:
            well, exp_ts = parse_meta_name(meta_path)
            print(f"\n[{well}]  {meta_path.name}")
            try:
                process_well(
                    meta_path, exp_dir,
                    codec=args.codec,
                    crf=args.crf,
                    mono=args.mono,
                    do_png=do_png,
                    do_jpeg=do_jpeg,
                    do_mp4=do_mp4,
                    do_vfr=do_vfr,
                    jpeg_quality=args.jpeg_quality,
                    zip_png=zip_png,
                    zip_jpeg=zip_jpeg,
                )
            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
    finally:
        if zip_png is not None:
            zip_png.close()
            print(f"\n[zip] {zip_png.filename}")
        if zip_jpeg is not None:
            zip_jpeg.close()
            print(f"[zip] {zip_jpeg.filename}")

    print("\nDone.")


if __name__ == "__main__":
    main()
