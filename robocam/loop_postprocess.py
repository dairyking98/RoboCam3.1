"""
robocam/loop_postprocess.py -- turn a loop-mode run's per-cycle output
(outputs/<loop_ts>_<name>_loop/<cycle_ts>_<name>_cycleNNNN/, see
ExperimentRunner.run_loop() in robocam/experiment.py) into one combined
artifact per well spanning every valid cycle: a concatenated video for
raw-mode loops, a timelapse for image-mode (growth imaging) loops, and a
zip of every cycle's still images.

Deliberately manifest-driven, not directory-globbed: loop_manifest.jsonl
is the only source of truth for which cycles actually succeeded (status
"ok"/"ok_after_retry") -- a failed cycle's folder still exists on disk
(created by run() before it failed) but holds partial/garbage data that
must never silently end up in an aggregate video.
"""
from __future__ import annotations

import json
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Optional

import cv2
import numpy as np

from robocam.postprocess import AV_AVAILABLE, npy_to_bgr

try:
    import av
except ImportError:
    pass

_VALID_STATUSES = ("ok", "ok_after_retry")


def discover_valid_cycles(loop_dir: Path) -> list[dict]:
    """Parse loop_dir/loop_manifest.jsonl, return the records for cycles
    that actually succeeded (status "ok" or "ok_after_retry"), in cycle
    order, each augmented with a resolved "dir": Path pointing at that
    cycle's experiment folder.

    A cycle's folder name always ends in the exact literal suffix
    f"_cycle{cycle:04d}" (first attempt) or f"_cycle{cycle:04d}_retry"
    (successful retry) -- see run_loop()'s cycle_name construction. Glob
    matching that exact suffix (no wildcard after it) means cycle 1 and
    cycle 1's retry, or cycle 1 and cycle 10, can never collide.
    """
    manifest_path = loop_dir / "loop_manifest.jsonl"
    if not manifest_path.exists():
        raise ValueError(f"No loop_manifest.jsonl found in {loop_dir}")

    cycles: list[dict] = []
    with open(manifest_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("status") not in _VALID_STATUSES:
                continue
            cycle_num = record["cycle"]
            suffix = f"_cycle{cycle_num:04d}"
            if record["status"] == "ok_after_retry":
                suffix += "_retry"
            matches = list(loop_dir.glob(f"*{suffix}"))
            if not matches:
                continue
            cycles.append({**record, "dir": matches[0]})

    cycles.sort(key=lambda r: r["cycle"])
    return cycles


def find_loop_wells(cycles: list[dict]) -> list[str]:
    """Union of every valid cycle's "labels" field, preserving the order
    from the first cycle that has them."""
    seen: list[str] = []
    for cycle in cycles:
        for label in cycle.get("labels", []):
            if label not in seen:
                seen.append(label)
    return seen


def well_is_raw_mode(cycle_dir: Path, well: str) -> bool:
    """True if this well has a raw-mode metadata sidecar in this cycle."""
    return bool(list((cycle_dir / "raw").glob(f"{well}_*_metadata.json")))


def _well_still_path(cycle_dir: Path, well: str) -> Optional[Path]:
    """The one loose still-image file image mode writes directly into the
    cycle folder for this well, if present."""
    matches = [
        p for p in cycle_dir.glob(f"{well}_*")
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff")
    ]
    return matches[0] if matches else None


def _encode_bgr_frames_to_mp4(
    frames: Iterable[np.ndarray], out_path: str, fps: float, w: int, h: int, crf: int = 18,
) -> int:
    """Encode an ordered sequence of BGR uint8 frames into a display MP4
    at a fixed fps. The MP4-only subset of process_well()'s existing
    video-writing block (robocam/postprocess.py) -- same
    av.open()/add_stream("libx264", rate=fps)/pix_fmt="yuv420p"/
    incrementing-index-PTS pattern already proven there. Returns the
    number of frames written.
    """
    if not AV_AVAILABLE:
        raise RuntimeError("PyAV not installed -- cannot encode video. Install with: pip install av")

    container = av.open(out_path, "w")
    stream = container.add_stream("libx264", rate=Fraction(fps).limit_denominator(1000))
    stream.width = w
    stream.height = h
    stream.pix_fmt = "yuv420p"
    stream.options = {"crf": str(crf), "preset": "medium", "profile": "baseline", "bframes": "0"}

    n = 0
    try:
        for bgr in frames:
            frame = av.VideoFrame.from_ndarray(bgr, format="bgr24").reformat(format="yuv420p")
            frame.pts = n
            for packet in stream.encode(frame):
                container.mux(packet)
            n += 1
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return n


def build_loop_video(
    cycles: list[dict], well: str, out_path: str, fps: float = 10.0,
    crf: int = 18, mono: bool = False,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """Raw-mode path: concatenate every valid cycle's debayered frames for
    `well`, in cycle order, into one combined MP4. Cycles missing this
    well (a partial capture) are skipped, not treated as an error --
    matches how process_well() already treats missing data per-well, not
    per-experiment. progress_callback reports (cycles_done, total_cycles)
    -- per-cycle, not per-frame, since a raw burst's frame count varies
    and isn't known up front.
    """
    well_metas = [
        meta_path
        for cycle in cycles
        for meta_path in sorted((cycle["dir"] / "raw").glob(f"{well}_*_metadata.json"))
    ]
    if not well_metas:
        return {"frame_count": 0, "out_path": None}

    # Frame dimensions come from the first cycle's stack shape directly --
    # no need to decode a frame just to learn this.
    with open(well_metas[0], encoding="utf-8") as f:
        first_meta = json.load(f)
    first_stack = np.load(well_metas[0].parent / first_meta["frames_file"], mmap_mode="r")
    _, h, w = first_stack.shape
    total = len(well_metas)

    def _frames():
        for idx, meta_path in enumerate(well_metas):
            if progress_callback:
                progress_callback(idx + 1, total)
            meta_dir = meta_path.parent
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            cam_meta_path = meta_dir / "camera_meta.json"
            camera_meta = {}
            if cam_meta_path.exists():
                with open(cam_meta_path, encoding="utf-8") as f:
                    camera_meta = json.load(f)
            frames_info = sorted(meta.get("frames", []), key=lambda x: x["frame_index"])
            frames_file = meta.get("frames_file")
            if not frames_info or not frames_file:
                continue
            stack = np.load(meta_dir / frames_file, mmap_mode="r")
            for fi in frames_info:
                yield npy_to_bgr(stack[fi["frame_index"]], mono, camera_meta)

    n = _encode_bgr_frames_to_mp4(_frames(), out_path, fps, w, h, crf)
    return {"frame_count": n, "out_path": out_path}


def build_loop_timelapse(
    cycles: list[dict], well: str, out_path: str, fps: float = 10.0,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """Image-mode path: one frame per valid cycle (that captured this
    well), read from its already-final still image, encoded as a
    timelapse MP4. Cycles missing this well are skipped."""
    still_paths = [
        p for p in (_well_still_path(cycle["dir"], well) for cycle in cycles)
        if p is not None
    ]
    if not still_paths:
        return {"frame_count": 0, "out_path": None}

    first = cv2.imread(str(still_paths[0]))
    h, w = first.shape[:2]
    total = len(still_paths)

    def _frames():
        for i, path in enumerate(still_paths):
            if progress_callback:
                progress_callback(i + 1, total)
            yield first if i == 0 else cv2.imread(str(path))

    n = _encode_bgr_frames_to_mp4(_frames(), out_path, fps, w, h)
    return {"frame_count": n, "out_path": out_path}


def build_loop_stills_zip(cycles: list[dict], out_zip_path: str, wells: Optional[list[str]] = None) -> Path:
    """Bundle every valid cycle's still image files (image-mode loops)
    into one zip, entries organized as <cycle_dir_name>/<well>_<ts>.<ext>
    -- source files as-is, no re-encoding (they're already final images),
    ZIP_STORED to match open_export_zip()'s reasoning (already-compressed
    formats gain nothing from DEFLATE)."""
    wells_set = set(wells) if wells is not None else None
    out_path = Path(out_zip_path)
    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for cycle in cycles:
            cycle_dir = cycle["dir"]
            for path in sorted(cycle_dir.iterdir()):
                if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
                    continue
                well = path.name.split("_", 1)[0]
                if wells_set is not None and well not in wells_set:
                    continue
                zf.write(path, arcname=f"{cycle_dir.name}/{path.name}")
    return out_path
