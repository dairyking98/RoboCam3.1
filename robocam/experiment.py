import os
import time
import csv
import cv2
import json
import logging
import queue
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
    ) -> dict:
        """
        Capture raw sensor frames as fast as possible for `total_duration_s`
        seconds, saving each as a .npy file. Returns a metadata dict.

        Capture (this thread, the producer) and disk writes (a separate
        writer thread, the consumer) are decoupled by a bounded queue, so a
        transient disk stall doesn't stall frame-acquisition timing. The
        producer *blocks* on a full queue rather than dropping frames — an
        already-captured frame is never discarded, matching the previous
        synchronous behaviour; `queue_full_stalls`/`queue_full_stall_s_total`
        in the returned metadata report how often/how long that happened.

        Per-frame records are also appended, as captured, to a
        `<label>_<timestamp>_frames.jsonl` sidecar so a crash/disconnect
        mid-burst doesn't lose timing metadata for frames already on disk —
        the final `frames`/`laser_events`/etc. keys below are unchanged from
        before, for `postprocess.py` compatibility.
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

        def _writer():
            with open(jsonl_path, "w", encoding="utf-8") as jf:
                while True:
                    item = frame_queue.get()
                    if item is None:  # sentinel: producer is done
                        break
                    idx, raw, t_capture = item
                    fname = f"{label}_{timestamp}_f{idx:05d}.npy"
                    np.save(os.path.join(output_dir, fname), raw)
                    record = {"frame_index": idx, "file": fname, "time_offset_s": round(t_capture, 6)}
                    frames_saved.append(record)
                    jf.write(json.dumps(record) + "\n")
                    jf.flush()

        writer_thread = threading.Thread(target=_writer, daemon=True)
        writer_thread.start()

        try:
            while self.running:
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
                    # Timestamp after get_raw_frame() returns — when frame is in hand
                    t_capture = time.perf_counter() - start
                    put_start = time.perf_counter()
                    frame_queue.put((frame_idx, raw, t_capture))
                    put_elapsed = time.perf_counter() - put_start
                    if put_elapsed > 0.001:
                        stall_count += 1
                        stall_s_total += put_elapsed
                    frame_idx += 1
                # No sleep — capture as fast as possible

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

        duration_actual = time.perf_counter() - start
        capture_stats = self.camera.get_capture_stats()
        return {
            "frames_captured": frame_idx,
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

                laser_controller = None
                if use_laser and mode == "raw":
                    laser_controller = LaserController(self.motion)
                    laser_controller.connect()

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

                    self.status_msg = f"Capturing {label} ({i + 1}/{len(positions)})..."
                    logger.info(self.status_msg)
                    if callback:
                        callback(self.status_msg)

                    capture_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    capture_name = ""

                    if mode == "raw":
                        # Burst of raw .npy frames saved to raw/ subdir
                        burst_meta = self._write_raw_burst(
                            raw_dir, label, timestamp, total_duration,
                            laser_controller=laser_controller,
                            laser_on_s=float(laser_on_duration),
                            laser_start_s=laser_start,
                        )
                        capture_name = f"raw/{label}_{timestamp}_f*.npy ({burst_meta['frames_captured']} frames)"
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
