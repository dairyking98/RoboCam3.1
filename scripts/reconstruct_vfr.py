#!/usr/bin/env python3
"""
reconstruct_vfr.py — CLI wrapper for the RoboCam post-processing pipeline.

Converts .npy burst frames to per-frame PNG images and/or VFR video.
Core logic lives in robocam/postprocess.py.

Usage
-----
  # All wells in an experiment directory
  python scripts/reconstruct_vfr.py outputs/20260625_133324_my_experiment/

  # Single well metadata file
  python scripts/reconstruct_vfr.py outputs/exp/raw/A1_20260625_133324_metadata.json

  # Lossless video (ffv1 codec)
  python scripts/reconstruct_vfr.py outputs/exp/ --codec ffv1

  # Monochrome sensor — skip Bayer debayer
  python scripts/reconstruct_vfr.py outputs/exp/ --mono

  # Images only (skip video encoding)
  python scripts/reconstruct_vfr.py outputs/exp/ --no-video

  # Video only (skip image export)
  python scripts/reconstruct_vfr.py outputs/exp/ --no-images

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

from robocam.postprocess import find_metadata_files, parse_meta_name, process_well


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert RoboCam .npy bursts to per-frame images and/or VFR video."
    )
    ap.add_argument("path",
        help="Experiment directory (containing raw/) or a specific *_metadata.json.")
    ap.add_argument("--codec", default="libx264",
        choices=["libx264", "libx265", "ffv1"],
        help="Video codec. ffv1 = lossless. (default: libx264)")
    ap.add_argument("--crf", type=int, default=18,
        help="CRF quality for libx264/libx265 (0=lossless, 18=high). (default: 18)")
    ap.add_argument("--mono", action="store_true",
        help="Monochrome sensor — skip Bayer debayer.")
    ap.add_argument("--no-video", action="store_true",
        help="Export images only; skip video encoding.")
    ap.add_argument("--no-images", action="store_true",
        help="Encode video only; skip per-frame image export.")
    args = ap.parse_args()

    if args.no_video and args.no_images:
        sys.exit("Error: --no-video and --no-images together produces nothing.")

    try:
        meta_files, exp_dir = find_metadata_files(args.path)
    except ValueError as e:
        sys.exit(str(e))

    print(f"Experiment : {exp_dir.name}")
    print(f"Wells      : {len(meta_files)}")

    for meta_path in meta_files:
        well, exp_ts = parse_meta_name(meta_path)
        print(f"\n[{well}]  {meta_path.name}")
        try:
            process_well(
                meta_path, exp_dir,
                codec=args.codec,
                crf=args.crf,
                mono=args.mono,
                do_images=not args.no_images,
                do_video=not args.no_video,
            )
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)

    print("\nDone.")


if __name__ == "__main__":
    main()
