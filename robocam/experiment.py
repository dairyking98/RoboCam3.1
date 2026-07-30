import ast
import math
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
from datetime import datetime, timedelta
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

# Cap on how many backlogged frames to discard when priming a well's capture
# (see the comment above the discard loop in run()). Bounds the loop so a
# camera with no backlog can't spin forever if get_exposure() ever returns 0.
STALE_FRAME_MAX_DISCARDS = 6

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

# Rough per-well overhead for still-image capture (Image mode has no fixed
# recording duration like raw bursts do — this covers exposure + camera
# readback + disk write) used only for the pre-run ETA estimate below.
IMAGE_CAPTURE_TIME_ESTIMATE_S = 0.3

# Every move is 3 command/ack round-trips that have nothing to do with
# physical travel — G90, G0's "queued" ack, and M114 — each blocked on
# MarlinBackend.command_delay (send + blind sleep + wait-for-"ok"; see
# motion.py). Only M400's wait reflects real physical travel time.
# Derived as round-trip count x the *actual configured* per-trip cost
# (read from the live MotionController at estimate time — see
# _move_overhead_s() below) rather than a hardcoded constant, so this
# self-corrects if command_delay is ever tuned again instead of going
# stale (it already did once: command_delay dropped from 0.1 to 0.02
# after hardware logs showed it was ~the entire round-trip cost).
GCODE_CALLS_PER_MOVE = 3


def _move_overhead_s(motion) -> float:
    """Fixed per-move serial round-trip overhead for the ETA estimate,
    derived from the connected backend's actual command_delay rather
    than a hardcoded value. 0.0 for backends with no such concept
    (Klipper's HTTP calls, the simulated backend) — this only applies to
    MarlinBackend's blind-sleep-per-command pattern."""
    delay = motion.command_delay
    return GCODE_CALLS_PER_MOVE * delay if delay else 0.0


def _axis_move_time_s(distance_mm: float, max_feed: Optional[float], max_accel: Optional[float]) -> float:
    """Trapezoidal-profile time estimate for one axis covering `distance_mm`,
    given its configured max feed rate (mm/s) and max acceleration (mm/s^2)
    from the motion profile (M203/M201). Falls back to a constant-velocity
    estimate if accel is unknown, and to 0 if feed rate is unknown."""
    distance_mm = abs(distance_mm)
    if distance_mm <= 0 or not max_feed:
        return 0.0
    if not max_accel:
        return distance_mm / max_feed

    accel_dist = (max_feed ** 2) / (2.0 * max_accel)
    if 2 * accel_dist >= distance_mm:
        # Never reaches max_feed before needing to decelerate again (triangular profile).
        return 2.0 * math.sqrt(distance_mm / max_accel)

    cruise_dist = distance_mm - 2 * accel_dist
    return 2.0 * (max_feed / max_accel) + (cruise_dist / max_feed)


def _estimate_move_time_s(
    start: Tuple[float, float, float], end: Tuple[float, float, float], profile: dict,
    move_overhead_s: float = 0.0,
) -> float:
    """Estimate a coordinated G0 XYZ move's duration from the motion profile.

    Axes move simultaneously to the same target, so this approximates
    Marlin's planner by taking the slowest of the three independent
    per-axis trapezoidal estimates rather than replicating its exact
    multi-axis feed-rate scaling — good enough for an ETA, not a substitute
    for the firmware's own timing.

    Jerk (M205) is intentionally not modeled. Every RoboCam move fully
    stops (M400 is awaited before the next one starts), so jerk's actual
    effect here is letting the planner start/end each move already moving
    at up to the configured jerk speed instead of ramping from a dead
    stop — saving roughly 2 * (jerk / accel) seconds per move. At this
    rig's jerk range (<=10 mm/s XY) against the accel this estimate
    already uses, that's on the order of 10-30ms — inside the ~10-15ms
    residual this estimate already showed against real hardware after
    the F/M204 fixes, not worth the added complexity of modeling
    Marlin's junction-velocity math for."""
    tx = _axis_move_time_s(end[0] - start[0], profile.get("max_feed_x"), profile.get("max_accel_x"))
    ty = _axis_move_time_s(end[1] - start[1], profile.get("max_feed_y"), profile.get("max_accel_y"))
    tz = _axis_move_time_s(end[2] - start[2], profile.get("max_feed_z"), profile.get("max_accel_z"))
    return max(tx, ty, tz) + move_overhead_s


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
        # Estimated wall-clock finish time, set once after homing at the
        # start of run(). None if a motion profile isn't available to
        # estimate from. The UI polls this directly (rather than a callback)
        # to keep a live countdown ticking between stage-change callbacks.
        self.eta_finish_time: Optional[datetime] = None

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

        # Target fps is an independent software pacing cap (see
        # Camera.set_target_fps()) — it can only ever slow capture down
        # below the exposure-derived ceiling, never speed it up past it, so
        # take whichever is lower for both pacing and buffer sizing.
        target_fps = self.camera.get_target_fps()
        paced_fps_est = min(fps_ceiling_est, target_fps) if target_fps else fps_ceiling_est
        frame_interval_s = (1.0 / target_fps) if target_fps and target_fps < fps_ceiling_est else 0.0

        max_frames = (
            int(total_duration_s * paced_fps_est * RAW_BURST_FPS_MARGIN)
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

        next_frame_due_s = 0.0

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
                    logger.info(
                        f"[{label}] Laser (GPIO) {'ON' if should_laser else 'OFF'} "
                        f"at t={elapsed:.2f}s"
                    )
                    laser_events.append({
                        "time_offset_s": round(elapsed, 6),
                        "state": "ON" if should_laser else "OFF",
                        "frame_index": frame_idx,
                    })
                    last_laser_state = should_laser

                if frame_interval_s > 0.0 and elapsed < next_frame_due_s:
                    time.sleep(next_frame_due_s - elapsed)

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
                    if frame_interval_s > 0.0:
                        next_frame_due_s += frame_interval_s
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_CAPTURE_FAILURES:
                        raise RuntimeError(
                            f"Camera unresponsive: {consecutive_failures} consecutive "
                            f"failed frame grabs for well {label} — aborting burst."
                        )
                # Otherwise no sleep — capture as fast as exposure allows
                # (frame_interval_s stays 0.0 unless a slower target fps was set)

            if writer_failed.is_set():
                raise RuntimeError(
                    f"Raw-burst writer thread failed for well {label}: {writer_exc['error']}"
                ) from writer_exc["error"]

        finally:
            if laser_controller and last_laser_state:
                laser_controller.set_laser(False)
                logger.info(f"[{label}] Laser (GPIO) OFF (forced at burst end)")
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
        self.eta_finish_time = None

        if not self.motion.is_homed:
            self.status_msg = "Not homed — homing before experiment start..."
            logger.info(self.status_msg)
            if callback:
                callback(self.status_msg)
            self.motion.home()
            self.status_msg = "Homed."
            logger.info(self.status_msg)
            if callback:
                callback(self.status_msg)

        self.status_msg = "Starting experiment..."
        logger.info(self.status_msg)
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

        # Time estimate, computed once now that the stage is at a known
        # (just-homed or already-homed) position — move times come from the
        # motion profile (M203 feed / M201 accel), dwell and capture times
        # are exact from the settings above.
        try:
            profile = self.motion.read_profiles() if self.motion.supports_profiles else {}
        except Exception as e:
            logger.warning(f"[Experiment] Could not read motion profile for time estimate: {e}")
            profile = {}

        # Per-well move-time estimates, kept around (not just summed) so the
        # well loop below can log each move's actual time next to the
        # estimate that predicted it — needed to empirically pin down where
        # real-hardware runs diverge from the estimate (see PROJECT_STATE.md
        # / robustness_log.md: a 24-well hardware run overshot the total
        # estimate by ~1.08s/well and the cause wasn't obvious from theory
        # alone).
        move_time_estimates: List[float] = []
        if profile:
            capture_time_est = total_duration if mode == "raw" else IMAGE_CAPTURE_TIME_ESTIMATE_S
            move_overhead_s = _move_overhead_s(self.motion)
            cur_pos = (self.motion.X, self.motion.Y, self.motion.Z)
            total_estimate_s = 0.0
            for pos in positions:
                move_est = _estimate_move_time_s(cur_pos, pos, profile, move_overhead_s)
                move_time_estimates.append(move_est)
                total_estimate_s += move_est
                total_estimate_s += float(delay_per_well)
                total_estimate_s += capture_time_est
                cur_pos = pos
            self.eta_finish_time = datetime.now() + timedelta(seconds=total_estimate_s)
            self.status_msg = (
                f"Estimated duration {total_estimate_s:.0f}s for {len(positions)} wells "
                f"— ETA finish {self.eta_finish_time.strftime('%H:%M:%S')}"
            )
        else:
            self.status_msg = "Time estimate unavailable (no motion profile from this backend)."
        logger.info(self.status_msg)
        if callback:
            callback(self.status_msg)

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

                wells_captured = 0
                total_capture_failures = {"lock_timeout": 0, "sdk_timeout_or_error": 0}
                total_dropped_frames = 0

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

                    move_start = time.perf_counter()
                    self.motion.move_absolute(X=x, Y=y, Z=z)
                    move_actual_s = time.perf_counter() - move_start
                    move_est_s = move_time_estimates[i] if i < len(move_time_estimates) else None
                    logger.info(
                        f"[{label}] Move took {move_actual_s:.3f}s"
                        + (f" (estimated {move_est_s:.3f}s)" if move_est_s is not None else " (no estimate)")
                    )

                    self.status_msg = f"Dwelling at {label} ({delay_per_well:.1f}s)..."
                    logger.info(self.status_msg)
                    if callback:
                        callback(self.status_msg)
                    dwell_start = time.perf_counter()
                    time.sleep(delay_per_well)
                    logger.info(f"[{label}] Dwell took {time.perf_counter() - dwell_start:.3f}s")

                    # Cameras run in continuous free-running exposure, so the SDK
                    # can have a small backlog of already-completed frames queued
                    # up from before/during the move above (see GetDroppedImagesCount
                    # in pyPOACamera.py — the driver keeps a short ring buffer and
                    # a single read only pops its oldest entry). GetImageData
                    # returns near-instantly while backlog remains queued, and
                    # only blocks for close to a full exposure once we're caught
                    # up to real time, so keep discarding until that happens
                    # instead of assuming one read is enough — a single discard
                    # left up to 2 stale pre-dwell frames at the start of the
                    # burst on hardware. Bounded so a genuinely fast sensor with
                    # no backlog can't loop forever.
                    priming_start = time.perf_counter()
                    exposure_s = self.camera.get_exposure() / 1_000_000.0
                    priming_reads = 0
                    for _ in range(STALE_FRAME_MAX_DISCARDS):
                        discard_start = time.perf_counter()
                        if mode == "raw":
                            self.camera.get_raw_frame()
                        else:
                            self.camera.get_frame()
                        priming_reads += 1
                        if time.perf_counter() - discard_start >= exposure_s * 0.5:
                            break
                    logger.info(
                        f"[{label}] Frame priming took {time.perf_counter() - priming_start:.3f}s "
                        f"({priming_reads} discard reads)"
                    )

                    if mode == "raw":
                        self.status_msg = (
                            f"Recording {label} ({i + 1}/{len(positions)}) — "
                            f"{total_duration:.1f}s burst"
                            f"{' with laser' if use_laser else ''}..."
                        )
                    else:
                        self.status_msg = f"Capturing {label} ({i + 1}/{len(positions)})..."
                    logger.info(self.status_msg)
                    if callback:
                        callback(self.status_msg)

                    capture_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    capture_name = ""
                    capture_call_start = time.perf_counter()

                    if mode == "raw":
                        # Burst of raw frames stacked into one .npy array in raw/ subdir
                        burst_meta = self._write_raw_burst(
                            raw_dir, label, timestamp, total_duration,
                            laser_controller=laser_controller,
                            laser_on_s=float(laser_on_duration),
                            laser_start_s=laser_start,
                            bit_depth=int(cam_meta.get("bit_depth", 8)),
                        )
                        capture_call_s = time.perf_counter() - capture_call_start
                        # duration_actual_s covers only up to when the recording loop
                        # itself decides it's done — the gap to capture_call_s is
                        # everything after: writer-thread drain/join, memmap flush
                        # and close, and _trim_raw_stack()'s truncate.
                        finalize_s = capture_call_s - burst_meta["duration_actual_s"]
                        logger.info(
                            f"[{label}] Capture took {capture_call_s:.3f}s total "
                            f"(recording {burst_meta['duration_actual_s']:.3f}s, "
                            f"finalize {finalize_s:.3f}s)"
                        )
                        capture_name = f"raw/{burst_meta['frames_file']} ({burst_meta['frames_captured']} frames)"
                        meta_path = os.path.join(raw_dir, f"{label}_{timestamp}_metadata.json")
                        burst_meta["well"] = label
                        burst_meta["timestamp"] = capture_time
                        with open(meta_path, "w", encoding="utf-8") as mf:
                            json.dump(burst_meta, mf, indent=2)

                        wells_captured += 1
                        for k in total_capture_failures:
                            total_capture_failures[k] += burst_meta["capture_failures"].get(k, 0)
                        total_dropped_frames += burst_meta["sdk_dropped_frames"]

                        logger.debug(
                            f"{label} capture summary: {burst_meta['frames_captured']} frames, "
                            f"{burst_meta['duration_actual_s']:.3f}s actual vs "
                            f"{burst_meta['duration_requested_s']:.3f}s requested "
                            f"({burst_meta['fps_average']:.2f} fps avg), "
                            f"laser_events={burst_meta['laser_events']}, "
                            f"capture_failures={burst_meta['capture_failures']}, "
                            f"sdk_dropped_frames={burst_meta['sdk_dropped_frames']}, "
                            f"queue_full_stalls={burst_meta['queue_full_stalls']} "
                            f"({burst_meta['queue_full_stall_s_total']:.3f}s total)"
                        )

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
                            wells_captured += 1
                            logger.debug(f"{label} capture summary: wrote {img_path}")
                        else:
                            logger.warning(f"Failed to capture frame for {label}")
                        logger.info(
                            f"[{label}] Capture took {time.perf_counter() - capture_call_start:.3f}s"
                        )

                    writer.writerow([label, x, y, z, capture_name, mode,
                                     "yes" if use_laser else "no", capture_time])
                    f.flush()

            if self.running:
                self.status_msg = "Experiment finished."
                logger.info(self.status_msg)
                logger.debug(
                    f"Experiment summary: {wells_captured}/{len(positions)} wells captured, "
                    f"total capture_failures={total_capture_failures}, "
                    f"total sdk_dropped_frames={total_dropped_frames}"
                )
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
