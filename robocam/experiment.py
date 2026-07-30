import ast
import math
import multiprocessing
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

# How often (in frames) the writer thread checks free disk space.
#
# Used to periodically call stack.flush() here too, on the same cadence.
# Removed: a high-fps/high-res hardware run showed periodic stalls at
# exactly this cadence (13 stalls of ~142ms at 30, 3 stalls of ~468ms
# after bumping to 100 as a direct test — confirmed, the period shifted
# exactly to match). Tuning the cadence didn't help (stall duration scaled
# proportionally with it, so total stalled time barely changed), and a
# standalone GIL test proved why: mmap.flush() doesn't release the GIL for
# its blocking msync duration, so it stalls the whole process regardless
# of which thread calls it — not fixable by moving it to a background
# thread. Disk bandwidth was independently ruled out (292MB/s measured
# vs. ~155MB/s actually demanded), so this was pure flush-call latency.
# Now relies on the kernel's own background dirty-page writeback instead
# of forcing periodic syncs — see the trade-off note in _writer() below.
DISK_CHECK_EVERY_N_FRAMES = 30

# Safety floor checked against shutil.disk_usage(...).free on the cadence
# above. A memory-mapped write that runs out of backing disk space raises
# SIGBUS, not a catchable Python exception — unlike a plain np.save()
# failing with a catchable OSError. Aborting cleanly well before actually
# hitting ENOSPC via the mmap fault path is the only way to keep this
# failure mode inside the writer_failed/RuntimeError path already built
# for other writer failures, instead of crashing the whole process.
MIN_FREE_DISK_BYTES = 500 * 1024 * 1024

# Per-well overhead for still-image capture (Image mode has no fixed
# recording duration like raw bursts do — this covers exposure + camera
# readback + cv2.imwrite() encode/write), used only for the pre-run ETA
# estimate below. Was a pure guess (0.3) until a 24-well hardware run at
# 1936x1100 JPEG logged "[label] Capture took X.XXXs" averaging ~0.054s
# per well (0.047-0.062s range; the very first capture of the run ran
# 0.079s -- a one-time warm-up, not a per-well cost, folded into this
# average rather than split out as its own constant since it's only
# ~26ms against everything else here). The old 0.3 guess was responsible
# for the entire estimate/actual gap on that run: 44s estimated vs.
# 38.1s actual, and 24 wells x (0.3 - 0.054) ~= 5.9s matches exactly.
#
# JPEG-specific: cv2.imwrite()'s cost is format-dependent (PNG's deflate
# compression is typically 5-10x slower than JPEG at the same resolution;
# TIFF is disk-write-bound instead of CPU-bound) and this is only measured
# against JPEG so far -- if PNG/TIFF turn out to matter in practice, this
# will need to become format-aware the same way RAW_BURST_FINALIZE_BYTES_PER_S
# had to become resolution/fps-aware instead of staying a flat number
# measured at one setting.
IMAGE_CAPTURE_TIME_ESTIMATE_S = 0.055

# Stale-frame discard loop (see the priming loop in run()) is never zero —
# even catching up in the minimum "4 discard reads" case seen on hardware
# still takes real time. No live setting to derive this from (unlike
# command_delay for moves), so it's a straight empirical average: two
# separate 24-well hardware runs measured 0.0435s and ~0.044s per well.
# Applies to both capture modes — the discard loop runs regardless of
# mode.
FRAME_PRIMING_ESTIMATE_S = 0.044

# Raw-burst capture consistently records slightly *longer* than the
# requested duration — structural, not incidental: the capture loop's
# exit check (elapsed >= total_duration_s) can only fire at or after the
# target, never exactly at it, so some overshoot every burst is
# essentially guaranteed by how that loop is written. Measured ~0.0458s
# and ~0.0455s average across two 24-well hardware runs. Raw mode only —
# Image mode has no analogous "requested duration" to overrun.
RAW_BURST_OVERRUN_S = 0.046

# Every well's raw-burst finalize (flush + trim) runs in a background
# process overlapped with the *next* well's move/dwell/capture — except
# the very last well, which has no next well to overlap with. Its finalize
# can only be waited out at the tail end (run()'s finally: block joins it
# before returning), so it's a real, unavoidable addition to total
# wall-clock time that every other well's finalize isn't. Without this,
# the ETA undercounts the true finish time by however long that flush
# takes: the UI's "done" signal only fires once run() actually returns
# (see _ExperimentThread), which is after this wait, so the countdown was
# observed ticking past zero into the negative by close to that amount on
# hardware. Raw mode only — Image mode has no finalize step at all.
#
# This isn't a fixed cost — stack.flush()'s msync duration scales with how
# many bytes are dirty, i.e. the stack's *preallocated* size (max_frames x
# resolution x bit depth), not the resolution/fps-independent flat value
# this used to be. A 1024x768 short burst and a 1936x1100x125fps burst do
# not take the same time to flush. See _estimate_finalize_time_s() below,
# calibrated from the one hardware run this used to be hardcoded from: a
# 1936x1100 8-bit stack preallocated to 862 frames (5.0s x 125fps x
# RAW_BURST_FPS_MARGIN + RAW_BURST_FRAME_BUFFER) = ~1836MB, measured
# flushing in 2.487s -> ~738MB/s. Rounded down to 700MB/s for a slight
# conservative bias, since an ETA that undercounts (goes negative on
# screen) is the more confusing failure mode of the two — see the comment
# above about the countdown ticking past zero being the original bug
# report this whole estimate exists to fix.
RAW_BURST_FINALIZE_BYTES_PER_S = 700_000_000


def _estimate_finalize_time_s(camera, total_duration_s: float, bit_depth: int) -> float:
    """Estimate the last well's raw-burst finalize (flush + trim) time,
    scaled by the preallocated stack size. Mirrors _capture_raw_burst()'s
    own max_frames formula exactly, since that's what actually determines
    how many bytes the background finalize process's stack.flush() has to
    write out — resolution and fps both change that size, so both need to
    feed into the ETA, not just a flat constant measured at one particular
    resolution/fps combination."""
    w, h = camera.resolution
    exposure_us = camera.get_exposure()
    fps_ceiling_est = 1_000_000.0 / exposure_us
    target_fps = camera.get_target_fps()
    paced_fps_est = min(fps_ceiling_est, target_fps) if target_fps else fps_ceiling_est
    max_frames = int(total_duration_s * paced_fps_est * RAW_BURST_FPS_MARGIN) + RAW_BURST_FRAME_BUFFER
    itemsize = 1 if bit_depth <= 8 else 2
    preallocated_bytes = max_frames * h * w * itemsize
    return preallocated_bytes / RAW_BURST_FINALIZE_BYTES_PER_S

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


# A second, separate fixed cost — this one lives *inside* M400's own
# measured wait, not in the surrounding G90/G0-ack/M114 round-trips
# above. After the command_delay-derived overhead was confirmed accurate
# (measured round-trips landed within ~3ms of estimate), a 24-well
# hardware run still showed a remaining gap between real M400 wait time
# and the physics-only estimate — but this one was flat regardless of
# move type: ~77ms +-2ms on both short triangular-profile moves (never
# reach cruise speed) and longer trapezoidal ones (do reach cruise).
# That distance-independence is what rules out an accel/feed modeling
# error — a wrong accel value would show a *smaller* gap on the
# trapezoidal move's distance-scaling cruise portion, not the same flat
# gap on both move shapes. So this is some fixed Marlin-internal cost
# inside its own M400 timing (candidates: M400's own polling granularity
# in the firmware's main loop, planner block-buffer overhead — not
# confirmed, can't instrument the firmware itself from here), unrelated
# to command_delay and NOT reduced by shrinking it further. Value is the
# straight average of the actual-minus-estimated deltas across that
# 24-well run (mean 77.3ms, tight spread) rather than a round number.
MARLIN_M400_OVERHEAD_S = 0.077


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
    already uses, that's on the order of 10-30ms — small next to
    MARLIN_M400_OVERHEAD_S below, not worth the added complexity of
    modeling Marlin's junction-velocity math for."""
    tx = _axis_move_time_s(end[0] - start[0], profile.get("max_feed_x"), profile.get("max_accel_x"))
    ty = _axis_move_time_s(end[1] - start[1], profile.get("max_feed_y"), profile.get("max_accel_y"))
    tz = _axis_move_time_s(end[2] - start[2], profile.get("max_feed_z"), profile.get("max_accel_z"))
    return max(tx, ty, tz) + move_overhead_s + MARLIN_M400_OVERHEAD_S


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


def _finalize_raw_burst_process(stack_path: str, frame_idx: int, label: str) -> None:
    """Flushes and trims a raw burst's memory-mapped stack file down to its
    real frame count. Runs in a separate OS *process*, not a thread — a
    thread-based version was tried first and confirmed not to work: a
    standalone test showed mmap.flush() does not release the GIL for its
    blocking msync duration (a spin-loop thread's throughput dropped to
    ~9% of normal while a different thread called flush() on a large
    mmap), and hardware logs confirmed the practical effect — the "next
    well" logging and motion calls on the main thread stalled for close to
    the full flush duration, not just its I/O-bound portion, so the
    intended overlap with the next well's move/dwell never actually
    happened. A separate process has its own GIL, so this call blocking
    itself has zero effect on the parent process.

    Deliberately takes plain, picklable arguments (a path, an int, a
    string) rather than the memmap object _capture_raw_burst() already has
    — multiprocessing can't meaningfully share a live mmap object across
    the process boundary anyway, and re-opening the file here is exactly
    as correct: mmap's underlying page cache is shared per-inode across
    every process that maps the same file, not per-mapping, so flushing
    this independent mapping still persists whatever the parent process
    already wrote (via its own, already-closed mapping — see
    _capture_raw_burst's finally: block) before calling this.

    Uses the `spawn` start method (forced by the caller's process context,
    not this function) rather than the Linux default `fork` — this process
    controls live hardware (a serial connection to the printer, camera SDK
    handles, laser GPIO), and fork() would duplicate the *entire* parent
    memory space into the child, including all of that, which risks the
    child process's exit/cleanup interfering with the parent's still-live
    connections. spawn starts a fresh interpreter with none of that
    inherited, at the cost of a slower start than fork's near-instant
    copy-on-write — an acceptable trade given this call already costs
    multiple seconds regardless.

    Logs and swallows its own errors rather than raising, since nothing is
    left synchronously waiting to catch an exception from a background
    process — check the experiment log.

    spawn also means this process does NOT inherit the app's logging
    setup (that's top-level code in robocam31.py's entry script, which
    spawn — unlike fork — never re-runs; it only re-imports this specific
    module). Without attaching a handler here, these log lines would
    silently go nowhere instead of into robocam.log. Uses append mode
    deliberately: the app's own setup opens robocam.log with mode="w"
    (truncate) once at startup — reusing that here would wipe out
    everything already logged the moment this child creates the handler."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_path = os.path.join(project_root, "robocam.log")
    handler = logging.FileHandler(log_path, mode="a")
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    finalize_start = time.perf_counter()
    try:
        stack = np.lib.format.open_memmap(stack_path, mode="r+")
        stack.flush()
        stack._mmap.close()
        _trim_raw_stack(stack_path, frame_idx)
        logger.info(
            f"[{label}] Finalize (flush + trim, subprocess) took "
            f"{time.perf_counter() - finalize_start:.3f}s"
        )
    except Exception as e:
        logger.error(f"[{label}] Raw-burst finalize failed: {e}", exc_info=True)


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
    def _capture_raw_burst(
        self,
        output_dir: str,
        label: str,
        timestamp: str,
        total_duration_s: float,
        laser_controller=None,
        laser_on_s: float = 0.0,
        laser_start_s: float = 0.0,
        bit_depth: int = 8,
    ) -> Tuple[dict, dict]:
        """
        Capture raw sensor frames as fast as possible for `total_duration_s`
        seconds, stacking them into one `<label>_<timestamp>_stack.npy`
        memory-mapped array `(n_frames, H, W)`. Returns `(burst_meta,
        finalize_ctx)` — burst_meta is complete and ready to use (write to
        JSON, log, etc.) the moment this returns; the writer thread has
        already been fully drained by then too. finalize_ctx just needs to
        be passed to _finalize_raw_burst_process() afterward to flush and
        trim the file to its final size — the one remaining expensive step,
        safe to run in the background (see that function's docstring for
        why it needs to be a separate process, not a thread).

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
                        if n_written % DISK_CHECK_EVERY_N_FRAMES == 0:
                            # No periodic stack.flush() here anymore — see
                            # DISK_CHECK_EVERY_N_FRAMES above for why.
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
                # Flush here (error path only, NOT a finally: — see below)
                # so whatever was written before the crash is still durable;
                # a memmap write that never reached this point could
                # otherwise be lost to OS page-cache buffering.
                writer_exc["error"] = e
                writer_failed.set()
                try:
                    stack.flush()
                except Exception:
                    pass
                while True:
                    item = frame_queue.get()
                    if item is None:
                        return
            # Deliberately no finally: stack.flush() here — that was the
            # actual bug behind a "0 frames queued but unwritten" drain still
            # taking ~2.5s on hardware: finally: runs on the *normal* return
            # path too (if item is None: return), so every single burst was
            # still doing one full, unbatched, GIL-blocking flush right here
            # regardless of DISK_CHECK_EVERY_N_FRAMES — this was functionally
            # the same bug the periodic-flush removal was supposed to fix,
            # just relocated. Normal completion now gets its one flush from
            # the explicit, backgrounded _finalize_raw_burst_process() instead.

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

            # Diagnostic timing (temporary): stack.flush()+trim is confirmed
            # fast now (moved to _finalize_raw_burst_process, logged separately) and
            # jf.flush() benchmarks at ~0.01ms/call on this hardware — both
            # ruled out — yet duration_actual_s still showed a ~1.6s gap
            # after the last captured frame on a real hardware run. This
            # narrows down whether that gap is the producer loop taking a
            # while to notice elapsed >= total_duration_s, or the writer
            # thread taking a while to drain its backlog after being
            # signaled, instead of continuing to guess between them.
            loop_exit_t = time.perf_counter() - start
            logger.info(
                f"[{label}] Producer loop exited at t={loop_exit_t:.3f}s "
                f"(target {total_duration_s:.1f}s, {frame_idx} frames sent to writer)"
            )

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
            # frame to actually be written into the stack array before
            # returning — never lose an already-captured frame, whether
            # stopped normally or by the user. Kept synchronous (unlike the
            # flush+trim below): writing rows into the mmap and appending to
            # frames_saved/the jsonl sidecar is cheap now that there's no
            # periodic stack.flush() left in the writer's loop to slow it
            # down, and burst_meta below needs frames_saved/frame_idx to be
            # fully complete, not whatever's landed so far.
            drain_start = time.perf_counter()
            queue_backlog = frame_queue.qsize()
            frame_queue.put(None)
            writer_thread.join()
            drain_s = time.perf_counter() - drain_start
            logger.info(
                f"[{label}] Writer drain (sentinel + join) took {drain_s:.3f}s "
                f"({queue_backlog} frames queued but unwritten at signal time)"
            )
            # Release this process's own mapping now — cheap (just unmaps
            # virtual memory, doesn't force dirty pages to disk the way
            # flush() does), unlike the actual durability guarantee, which
            # happens in a separate OS process (see
            # _finalize_raw_burst_process()) via its own independent
            # mapping of the same file. Dirty pages already written by
            # this process stay in the OS
            # page cache (shared per-inode across every process, not
            # per-mapping) until that other mapping's flush() picks them up
            # — closing this one first doesn't lose or discard anything.
            stack._mmap.close()

        duration_actual = time.perf_counter() - start
        capture_stats = self.camera.get_capture_stats()
        burst_meta = {
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
        finalize_ctx = {
            "label": label,
            "stack_path": stack_path,
            "frame_idx": frame_idx,
        }
        return burst_meta, finalize_ctx

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
        image_format: str = "png",
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
            capture_time_est = FRAME_PRIMING_ESTIMATE_S + (
                total_duration + RAW_BURST_OVERRUN_S if mode == "raw" else IMAGE_CAPTURE_TIME_ESTIMATE_S
            )
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
            if mode == "raw" and positions:
                bit_depth = int(self.camera.get_camera_meta().get("bit_depth", 8))
                total_estimate_s += _estimate_finalize_time_s(self.camera, total_duration, bit_depth)
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
                # Previous well's raw-burst finalize (flush + trim), still
                # running in the background — bounded to at most one in
                # flight at a time, joined right before starting the next
                # one so it overlaps with this well's move/dwell/capture
                # instead of blocking on it. A separate OS process, not a
                # thread — see _finalize_raw_burst_process()'s docstring
                # for why a thread doesn't actually achieve the overlap.
                pending_finalize: Optional[multiprocessing.process.BaseProcess] = None

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
                    else:
                        # Loop ran out of reads without ever catching up to
                        # real-time (the "for" completed all iterations without
                        # hitting the break above) — at a higher fps than this
                        # was tuned against, the camera's backlog may be deeper
                        # than STALE_FRAME_MAX_DISCARDS can drain, so the
                        # recording could still be starting on a stale pre-dwell
                        # frame. Distinct from merely using the last iteration
                        # to genuinely catch up, which for/else does not flag.
                        logger.warning(
                            f"[{label}] Frame priming hit the {STALE_FRAME_MAX_DISCARDS}-read cap "
                            f"without catching up to real-time — backlog may not be "
                            f"fully drained; consider raising STALE_FRAME_MAX_DISCARDS."
                        )
                    priming_total_s = time.perf_counter() - priming_start
                    logger.info(
                        f"[{label}] Frame priming took {priming_total_s:.3f}s "
                        f"({priming_reads} discard reads, "
                        f"{priming_total_s / priming_reads:.3f}s avg/read)"
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
                        burst_meta, finalize_ctx = self._capture_raw_burst(
                            raw_dir, label, timestamp, total_duration,
                            laser_controller=laser_controller,
                            laser_on_s=float(laser_on_duration),
                            laser_start_s=laser_start,
                            bit_depth=int(cam_meta.get("bit_depth", 8)),
                        )
                        capture_call_s = time.perf_counter() - capture_call_start
                        logger.info(
                            f"[{label}] Capture took {capture_call_s:.3f}s "
                            f"(recording {burst_meta['duration_actual_s']:.3f}s) — "
                            f"finalize (flush + trim) running in a background process"
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

                        # Bounded to one in flight: wait for the *previous*
                        # well's finalize before starting this one's, so it
                        # overlaps with whatever move/dwell/capture time this
                        # well already took above instead of piling up
                        # unbounded background processes. spawn, not the
                        # Linux default fork — see
                        # _finalize_raw_burst_process()'s docstring for why
                        # (this process holds live hardware connections that
                        # fork would duplicate into the child).
                        if pending_finalize is not None:
                            pending_finalize.join()
                        pending_finalize = multiprocessing.get_context("spawn").Process(
                            target=_finalize_raw_burst_process,
                            args=(finalize_ctx["stack_path"], finalize_ctx["frame_idx"], finalize_ctx["label"]),
                            daemon=True,
                        )
                        pending_finalize.start()

                    else:
                        # Standard still image
                        fmt = (image_format or "png").lower().lstrip(".")
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
            # The experiment isn't really done until the last well's raw
            # burst is actually flushed and trimmed to disk, even though
            # run() itself has otherwise finished (or errored, or been
            # stopped) — wait for it here rather than leaving a dangling
            # background thread when this method returns.
            if "pending_finalize" in locals() and pending_finalize is not None:
                pending_finalize.join()
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
