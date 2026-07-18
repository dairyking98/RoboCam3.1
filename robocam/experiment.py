import ast
import os
import time
import csv
import cv2
import json
import logging
import queue
import shutil
import threading
import numpy as np
from datetime import datetime
from typing import List, Optional, Tuple
from .config import get_config
from .peripherals import LaserController

logger = logging.getLogger(__name__)

# Bounded queue depth between the raw-burst capture (producer) thread and the
# disk-write (consumer) thread. Sized as initial headroom for the NVMe M.2
# HAT; revisit after real-world timing numbers come back from hardware
# (see PROJECT_STATE.md § 9).
RAW_BURST_QUEUE_MAXSIZE = 128

# Abort a raw burst if get_raw_frame() fails this many times in a row — a
# real camera disconnect looks identical to a normal timing miss otherwise,
# and the burst would silently "complete" with near-empty data instead of
# surfacing an error.
MAX_CONSECUTIVE_CAPTURE_FAILURES = 50

# All of a well's frames are stacked into one (n_frames, H, W) array, written
# incrementally via np.lib.format.open_memmap() so it's one file from the
# first frame, not built in RAM and dumped at the end. The array shape has to
# be fixed at creation time, and true achieved fps isn't known in advance, so
# it's preallocated to an estimated ceiling rather than risk running out of
# rows mid-burst.
#
# fps is exposure-bound, confirmed on hardware (~50/94fps at two tested
# exposures — see PROJECT_STATE.md § 9 and the same fps ≈ 1e6/exposure_us
# link used in calibration_panel.py's fps field), so the ceiling is computed
# per-burst from the camera's current exposure setting rather than one flat
# guess shared across all exposures. RAW_BURST_FPS_MARGIN is extra headroom
# on top of that estimate; RAW_BURST_FRAME_BUFFER is a small flat buffer for
# the last partial flush interval.
#
# Unwritten trailing rows are NOT sparse on disk in practice (confirmed via
# stat: a preallocated file is fully materialized, not hole-punched), so
# _trim_raw_stack() truncates each stack.npy down to its real frames_captured
# size right after capture finishes rather than leaving the ceiling-sized
# file around.
RAW_BURST_FPS_MARGIN = 1.3
RAW_BURST_FRAME_BUFFER = 50

# How often (in frames) the writer thread flushes the memmap to disk and
# checks free space. Piggybacks one cadence for both instead of a separate
# timer.
MEMMAP_FLUSH_EVERY_N_FRAMES = 30

# Safety floor checked against shutil.disk_usage(...).free on the same
# cadence as the periodic flush above. A memory-mapped write that runs out
# of backing disk space raises SIGBUS, not a catchable Python exception —
# unlike a plain np.save() failing with a catchable OSError. Aborting
# cleanly well before actually hitting ENOSPC via the mmap fault path is the
# only way to keep this failure mode inside the writer_failed/RuntimeError
# path already built for other writer failures, instead of crashing the
# whole process.
MIN_FREE_DISK_BYTES = 500 * 1024 * 1024


def _trim_raw_stack(stack_path: str, frames_captured: int) -> None:
    """Truncate a preallocated raw *_stack.npy file in place, dropping the
    unused ceiling-sized tail beyond `frames_captured` real frames.

    Frames are stored row-major with the frame axis outermost, so every row
    past frames_captured - 1 is one contiguous block at the end of the file
    -- trimming never touches real frame bytes. Only the header's shape
    field is rewritten, padded with spaces to occupy the exact same byte
    length as before so data_offset doesn't move, and the file is then
    truncated at the new end.
    """
    with open(stack_path, "r+b") as f:
        magic = f.read(6)
        if magic != np.lib.format.MAGIC_PREFIX:
            raise ValueError(f"{stack_path} is not a valid .npy file")
        major = f.read(1)[0]
        f.read(1)  # minor version, unused
        len_field_size = 2 if major == 1 else 4
        hlen = int.from_bytes(f.read(len_field_size), "little")
        header_text_offset = f.tell()
        header_text = f.read(hlen).decode("latin1")
        data_offset = f.tell()

        header_dict = ast.literal_eval(header_text)
        old_shape = header_dict["shape"]
        dtype = np.dtype(header_dict["descr"])
        new_shape = (frames_captured,) + tuple(old_shape[1:])

        new_dict_str = (
            "{'descr': " + repr(header_dict["descr"])
            + ", 'fortran_order': " + repr(header_dict["fortran_order"])
            + ", 'shape': " + repr(new_shape) + ", }"
        )
        pad_len = hlen - len(new_dict_str) - 1  # -1 for the trailing newline
        if pad_len < 0:
            raise ValueError(
                f"Trimmed .npy header for {stack_path} doesn't fit in the "
                f"original {hlen}-byte header slot"
            )
        new_header_text = new_dict_str + (" " * pad_len) + "\n"

        f.seek(header_text_offset)
        f.write(new_header_text.encode("latin1"))

        row_elems = 1
        for d in old_shape[1:]:
            row_elems *= d
        row_bytes = row_elems * dtype.itemsize
        f.truncate(data_offset + frames_captured * row_bytes)


class ExperimentRunner:
    """
    Experiment Runner for RoboCam 3.1.

    Capture modes
    -------------
    image : Single still image per well (JPG/PNG/TIF).
    raw   : Burst of raw .npy frames for `pre_duration` seconds at maximum
            camera rate. No encoding overhead — fastest possible capture.
            A camera_meta.json sidecar is written once per experiment so the
            post-processing pipeline can debayer and reconstruct video correctly.

    Laser flag (applies to raw mode)
    ---------------------------------
    When use_laser=True the laser fires during the middle window:
        pre_duration  → camera records, laser OFF
        laser_on      → camera records, laser ON
        post_duration → camera records, laser OFF
    When use_laser=False only pre_duration is used (total record time).
    """

    def __init__(self, motion_controller, camera):
        self.motion = motion_controller
        self.camera = camera
        self.config = get_config()
        self.out_dir = self.config.get("paths.output_dir", "outputs")
        os.makedirs(self.out_dir, exist_ok=True)

        self.running = False
        self.paused = False
        self.current_well = ""
        self.status_msg = "Ready"

        self.is_raw_mode = False
        self.last_written_image_path = None
        self.last_written_video_path = None
        self.last_exp_dir: Optional[str] = None

    # ------------------------------------------------------------------
    # Internal: max-rate raw burst writer
    # ------------------------------------------------------------------
    def _write_raw_burst(
        self,
        output_dir: str,
        label: str,
        timestamp: str,
        total_duration_s: float,
        laser_controller=None,
        laser_on_s: float = 0.0,
        laser_start_s: float = 0.0,
        bit_depth: int = 8,
    ) -> dict:
        """
        Capture raw sensor frames as fast as possible for `total_duration_s`
        seconds, stacking them into one `<label>_<timestamp>_stack.npy`
        memory-mapped array `(n_frames, H, W)`. Returns a metadata dict.

        Capture (this thread, the producer) and disk writes (a separate
        writer thread, the consumer) are decoupled by a bounded queue, so a
        transient disk stall doesn't stall frame-acquisition timing. The
        producer *blocks* on a full queue rather than dropping frames — an
        already-captured frame is never discarded, matching the previous
        synchronous behaviour; `queue_full_stalls`/`queue_full_stall_s_total`
        in the returned metadata report how often/how long that happened.

        The stack array is preallocated to `total_duration_s * fps_ceiling_est
        * RAW_BURST_FPS_MARGIN` rows, where `fps_ceiling_est` is derived from
        the camera's current exposure setting (fps is exposure-bound — see
        the module-level comment), since true achieved fps isn't known in
        advance and the array's shape is fixed at creation time. Once the
        burst finishes, `_trim_raw_stack()` truncates the file down to
        `frames_captured` real rows before this method returns.

        Per-frame timing records are also appended, as captured, to a
        `<label>_<timestamp>_frames.jsonl` sidecar so a crash/disconnect
        mid-burst doesn't lose timing metadata for frames already on disk.
        """
        frames_saved = []
        laser_events = []
        last_laser_state = False
        laser_end_s = laser_start_s + laser_on_s
        frame_idx = 0
        start = time.perf_counter()

        self.camera.reset_capture_stats()

        frame_queue: "queue.Queue" = queue.Queue(maxsize=RAW_BURST_QUEUE_MAXSIZE)
        stall_count = 0
        stall_s_total = 0.0
        jsonl_path = os.path.join(output_dir, f"{label}_{timestamp}_frames.jsonl")

        w, h = self.camera.resolution
        dtype = np.uint8 if bit_depth <= 8 else np.uint16
        exposure_us = self.camera.get_exposure()
        fps_ceiling_est = 1_000_000.0 / exposure_us
        max_frames = (
            int(total_duration_s * fps_ceiling_est * RAW_BURST_FPS_MARGIN)
            + RAW_BURST_FRAME_BUFFER
        )
        stack_filename = f"{label}_{timestamp}_stack.npy"
        stack_path = os.path.join(output_dir, stack_filename)
        stack = np.lib.format.open_memmap(
            stack_path, mode="w+", dtype=dtype, shape=(max_frames, h, w)
        )

        writer_failed = threading.Event()
        writer_exc: dict = {}

        def _writer():
            n_written = 0
            try:
                with open(jsonl_path, "w", encoding="utf-8") as jf:
                    while True:
                        item = frame_queue.get()
                        if item is None:  # sentinel: producer is done
                            return
                        idx, raw, t_capture = item
                        stack[idx] = raw
                        record = {"frame_index": idx, "time_offset_s": round(t_capture, 6)}
                        frames_saved.append(record)
                        jf.write(json.dumps(record) + "\n")
                        jf.flush()
                        n_written += 1
                        if n_written % MEMMAP_FLUSH_EVERY_N_FRAMES == 0:
                            stack.flush()
                            free = shutil.disk_usage(output_dir).free
                            if free < MIN_FREE_DISK_BYTES:
                                raise OSError(
                                    f"Only {free} bytes free in {output_dir}, "
                                    f"below the {MIN_FREE_DISK_BYTES}-byte safety floor "
                                    f"— aborting before a memmap write can hit ENOSPC."
                                )
            except Exception as e:
                # Record the failure and switch to drain-only mode — the
                # producer must never block forever on a full queue waiting
                # for a writer that has died (e.g. disk full, drive unmounted).
                writer_exc["error"] = e
                writer_failed.set()
                while True:
                    item = frame_queue.get()
                    if item is None:
                        return
            finally:
                # Whatever was written must be durable even on abort — a
                # memmap write that never reached this point could otherwise
                # be lost to OS page-cache buffering.
                try:
                    stack.flush()
                except Exception:
                    pass

        writer_thread = threading.Thread(target=_writer, daemon=True)
        writer_thread.start()

        try:
            consecutive_failures = 0
            while self.running:
                if writer_failed.is_set():
                    break

                elapsed = time.perf_counter() - start
                if elapsed >= total_duration_s:
                    break

                should_laser = bool(
                    laser_controller
                    and laser_on_s > 0
                    and laser_start_s <= elapsed < laser_end_s
                )
                if should_laser != last_laser_state and laser_controller:
                    laser_controller.set_laser(should_laser)
                    laser_events.append({
                        "time_offset_s": round(elapsed, 6),
                        "state": "ON" if should_laser else "OFF",
                        "frame_index": frame_idx,
                    })
                    last_laser_state = should_laser

                raw = self.camera.get_raw_frame()
                if raw is not None:
                    if frame_idx >= max_frames:
                        # Deterministic guard, not just reactive: relying only
                        # on the writer noticing an IndexError and setting
                        # writer_failed leaves a race where this producer
                        # loop could queue many more frames before it next
                        # checks that flag, silently discarding captures that
                        # frames_captured would then over-report. Should
                        # never fire on real hardware (RAW_BURST_FPS_MARGIN
                        # gives headroom above the exposure-derived estimate),
                        # but must be deterministic if that estimate ever
                        # proves wrong (e.g. exposure changed after this
                        # burst's shape was already fixed).
                        raise RuntimeError(
                            f"Raw-burst preallocation ceiling ({max_frames} frames) "
                            f"reached before {total_duration_s}s elapsed for well "
                            f"{label} — exposure-derived fps ceiling estimate "
                            f"({fps_ceiling_est:.1f}fps @ {exposure_us}us exposure, "
                            f"x{RAW_BURST_FPS_MARGIN} margin) is too low for the "
                            f"achieved capture rate."
                        )
                    consecutive_failures = 0
                    # Timestamp after get_raw_frame() returns — when frame is in hand
                    t_capture = time.perf_counter() - start
                    put_start = time.perf_counter()
                    frame_queue.put((frame_idx, raw, t_capture))
                    put_elapsed = time.perf_counter() - put_start
                    if put_elapsed > 0.001:
                        stall_count += 1
                        stall_s_total += put_elapsed
                    frame_idx += 1
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_CAPTURE_FAILURES:
                        raise RuntimeError(
                            f"Camera unresponsive: {consecutive_failures} consecutive "
                            f"failed frame grabs for well {label} — aborting burst."
                        )
                # No sleep — capture as fast as possible

            if writer_failed.is_set():
                raise RuntimeError(
                    f"Raw-burst writer thread failed for well {label}: {writer_exc['error']}"
                ) from writer_exc["error"]

        finally:
            if laser_controller and last_laser_state:
                laser_controller.set_laser(False)
                laser_events.append({
                    "time_offset_s": round(time.perf_counter() - start, 6),
                    "state": "OFF",
                    "frame_index": frame_idx,
                })
            # Drain: signal no more frames, then wait for every already-queued
            # frame to actually be written before returning — never lose an
            # already-captured frame, whether stopped normally or by the user.
            frame_queue.put(None)
            writer_thread.join()

        # Close the memmap before truncating its backing file below — the
        # writer thread already flushed it in its own finally block, but the
        # mmap object (kept alive by the writer thread's closure) must be
        # closed first or a truncate here could race a lingering mapping.
        stack.flush()
        stack._mmap.close()
        _trim_raw_stack(stack_path, frame_idx)

        duration_actual = time.perf_counter() - start
        capture_stats = self.camera.get_capture_stats()
        return {
            "frames_captured": frame_idx,
            "frames_file": stack_filename,
            "duration_requested_s": round(total_duration_s, 3),
            "duration_actual_s": round(duration_actual, 6),
            "fps_average": round(frame_idx / duration_actual, 4) if duration_actual > 0 else 0.0,
            "laser_events": laser_events,
            "frames": frames_saved,
            "capture_failures": capture_stats,
            "sdk_dropped_frames": self.camera.get_dropped_frames_count(),
            "queue_full_stalls": stall_count,
            "queue_full_stall_s_total": round(stall_s_total, 6),
        }

    # ------------------------------------------------------------------
    # Main experiment loop
    # ------------------------------------------------------------------
    def run(
        self,
        name: str,
        positions: List[Tuple[float, float, float]],
        labels: List[str],
        delay_per_well: float = 1.0,
        callback=None,
        mode: str = "image",
        image_format: str = "jpg",
        use_laser: bool = False,
        pre_duration: float = 5.0,
        laser_on_duration: float = 1.0,
        post_duration: float = 2.0,
    ):
        self.running = True
        self.paused = False
        mode = (mode or "image").lower()
        self.is_raw_mode = mode == "raw"
        self.last_written_image_path = None
        self.last_written_video_path = None
        self.status_msg = "Starting experiment..."
        if callback:
            callback(self.status_msg)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exp_dir = os.path.join(self.out_dir, f"{timestamp}_{name}")
        raw_dir = os.path.join(exp_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        self.last_exp_dir = exp_dir

        csv_path = os.path.join(exp_dir, f"{timestamp}_{name}_points.csv")

        # Pre-calculate total duration for raw mode
        if use_laser:
            total_duration = float(pre_duration) + float(laser_on_duration) + float(post_duration)
            laser_start = float(pre_duration)
        else:
            total_duration = float(pre_duration)
            laser_start = 0.0
            laser_on_duration = 0.0

        try:
            # Write camera metadata once for the whole experiment so the
            # post-processing pipeline knows how to debayer the .npy frames.
            if mode == "raw":
                cam_meta = self.camera.get_camera_meta()
                with open(os.path.join(raw_dir, "camera_meta.json"), "w", encoding="utf-8") as mf:
                    json.dump(cam_meta, mf, indent=2)

            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Well", "X", "Y", "Z", "Capture_File", "Capture_Mode", "Laser", "Timestamp"])

                # Reuse the shared LaserController (e.g. one already claimed by
                # the Manual Control panel) rather than claiming the GPIO pin
                # again, which would fail with "GPIO busy".
                laser_controller = None
                laser_owned_here = False
                if use_laser and mode == "raw":
                    import robocam.hw_state as hw_state
                    laser_controller = hw_state.get_laser()
                    if laser_controller is None:
                        laser_controller = LaserController(self.motion)
                        laser_controller.connect()
                        hw_state.set_laser(laser_controller)
                        laser_owned_here = True

                for i, (pos, label) in enumerate(zip(positions, labels)):
                    if not self.running:
                        self.status_msg = "Experiment stopped by user."
                        if callback:
                            callback(self.status_msg)
                        break

                    while self.paused:
                        self.status_msg = "Experiment paused."
                        if callback:
                            callback(self.status_msg)
                        time.sleep(0.1)
                        if not self.running:
                            break

                    if not self.running:
                        break

                    self.current_well = label
                    x, y, z = pos

                    self.status_msg = f"Moving to {label} ({i + 1}/{len(positions)})..."
                    logger.info(self.status_msg)
                    if callback:
                        callback(self.status_msg)

                    self.motion.move_absolute(X=x, Y=y, Z=z)

                    self.status_msg = f"Stabilising at {label}..."
                    if callback:
                        callback(self.status_msg)
                    time.sleep(delay_per_well)

                    # Cameras run in continuous free-running exposure, so the frame
                    # sitting in the buffer right now may have been captured while
                    # the stage was still moving. Discard it so the first frame we
                    # actually use/save reflects the post-dwell, settled position.
                    if mode == "raw":
                        self.camera.get_raw_frame()
                    else:
                        self.camera.get_frame()

                    self.status_msg = f"Capturing {label} ({i + 1}/{len(positions)})..."
                    logger.info(self.status_msg)
                    if callback:
                        callback(self.status_msg)

                    capture_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    capture_name = ""

                    if mode == "raw":
                        # Burst of raw frames stacked into one .npy array in raw/ subdir
                        burst_meta = self._write_raw_burst(
                            raw_dir, label, timestamp, total_duration,
                            laser_controller=laser_controller,
                            laser_on_s=float(laser_on_duration),
                            laser_start_s=laser_start,
                            bit_depth=int(cam_meta.get("bit_depth", 8)),
                        )
                        capture_name = f"raw/{burst_meta['frames_file']} ({burst_meta['frames_captured']} frames)"
                        meta_path = os.path.join(raw_dir, f"{label}_{timestamp}_metadata.json")
                        burst_meta["well"] = label
                        burst_meta["timestamp"] = capture_time
                        with open(meta_path, "w", encoding="utf-8") as mf:
                            json.dump(burst_meta, mf, indent=2)

                    else:
                        # Standard still image
                        fmt = (image_format or "jpg").lower().lstrip(".")
                        capture_name = f"{label}_{timestamp}.{fmt}"
                        img_path = os.path.join(exp_dir, capture_name)
                        frame = self.camera.get_frame()
                        if frame is not None:
                            if self.camera.backend == "picamera2":
                                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                            cv2.imwrite(img_path, frame)
                            self.last_written_image_path = img_path
                        else:
                            logger.warning(f"Failed to capture frame for {label}")

                    writer.writerow([label, x, y, z, capture_name, mode,
                                     "yes" if use_laser else "no", capture_time])
                    f.flush()

            if self.running:
                self.status_msg = "Experiment finished."
                logger.info(self.status_msg)
                if callback:
                    callback(self.status_msg)

        except Exception as e:
            self.status_msg = f"Experiment error: {e}"
            logger.error(self.status_msg, exc_info=True)
            if callback:
                callback(self.status_msg)
        finally:
            if "laser_controller" in locals() and laser_controller:
                laser_controller.disconnect()
            self.running = False
            self.current_well = ""
            self.is_raw_mode = False

    def stop(self):
        self.running = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False
