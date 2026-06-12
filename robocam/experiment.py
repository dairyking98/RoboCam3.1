import os
import time
import cv2
import csv
import json
import logging
import numpy as np
from datetime import datetime
from typing import List, Tuple
from .config import get_config
from .peripherals import LaserController

logger = logging.getLogger(__name__)


class ExperimentRunner:
    """
    Experiment Runner for RoboCam 3.1.

    Capture modes
    -------------
    image       : Single still image per well (JPG/PNG/TIF).
    raw         : Single raw .npy frame per well — fastest possible, no encoding.
    video       : Records for `pre_duration` seconds at maximum camera framerate.
                  No laser. Metadata JSON written alongside the AVI.
    laser_video : Records for `pre_duration + laser_on_duration + post_duration`
                  seconds at maximum camera framerate. Laser fires only during the
                  middle window. Metadata JSON includes per-frame laser events.

    Video is always captured at the maximum rate the camera can deliver — no FPS
    throttle. The actual achieved FPS is written into the metadata JSON so the
    footage can be played back at the correct speed in post-processing.
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

        self.is_fast_raw_mode = False
        self.last_written_image_path = None
        self.last_written_video_path = None

    # ------------------------------------------------------------------
    # Internal video writer — captures at max camera rate, no throttle
    # ------------------------------------------------------------------
    def _write_video(
        self,
        output_path: str,
        duration_s: float,
        laser_controller=None,
        laser_on_s: float = 0.0,
        laser_start_s: float = 0.0,
    ) -> str:
        """
        Record video for `duration_s` seconds at the maximum rate the camera
        delivers. If `laser_controller` is provided and `laser_on_s > 0`, the
        laser fires between `laser_start_s` and `laser_start_s + laser_on_s`.

        A sidecar *_metadata.json* is written with actual FPS, frame count,
        resolution, and a timestamped laser event log.
        """
        # Grab the first valid frame to get resolution
        first_frame = None
        for _ in range(30):
            first_frame = self.camera.get_frame()
            if first_frame is not None:
                break
            time.sleep(0.033)

        if first_frame is None:
            raise RuntimeError("Could not read a frame to start video recording.")

        if self.camera.backend == "picamera2":
            first_frame = cv2.cvtColor(first_frame, cv2.COLOR_RGB2BGR)

        h, w = first_frame.shape[:2]

        # Use a placeholder FPS for the container — actual FPS is in metadata.
        # MJPG is used for broad compatibility and fast encoding on the Pi.
        container_fps = 30.0
        writer = cv2.VideoWriter(
            output_path,
            cv2.VideoWriter_fourcc(*"MJPG"),
            container_fps,
            (w, h),
        )
        if not writer.isOpened():
            raise RuntimeError(f"Could not open video writer for {output_path}")

        frames = 0
        laser_events = []
        last_laser_state = False
        laser_end_s = laser_start_s + laser_on_s
        start = time.time()

        try:
            while self.running:
                elapsed = time.time() - start
                if elapsed >= duration_s:
                    break

                # Laser gating
                should_laser = bool(
                    laser_controller
                    and laser_on_s > 0
                    and laser_start_s <= elapsed < laser_end_s
                )
                if should_laser != last_laser_state and laser_controller:
                    laser_controller.set_laser(should_laser)
                    laser_events.append({
                        "time_offset_s": round(elapsed, 4),
                        "state": "ON" if should_laser else "OFF",
                        "frame_index": frames,
                    })
                    last_laser_state = should_laser

                frame = first_frame if frames == 0 else self.camera.get_frame()
                if frame is not None:
                    if self.camera.backend == "picamera2":
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    writer.write(frame)
                    frames += 1
                # No sleep — capture as fast as the camera allows

        finally:
            # Ensure laser is off on exit
            if laser_controller and last_laser_state:
                laser_controller.set_laser(False)
                laser_events.append({
                    "time_offset_s": round(time.time() - start, 4),
                    "state": "OFF",
                    "frame_index": frames,
                })
            duration_actual = time.time() - start
            writer.release()

        # Write sidecar metadata
        meta_path = os.path.splitext(output_path)[0] + "_metadata.json"
        metadata = {
            "video_file": os.path.basename(output_path),
            "frames_captured": frames,
            "duration_requested_s": round(duration_s, 3),
            "duration_actual_s": round(duration_actual, 3),
            "fps_actual": round(frames / duration_actual, 2) if duration_actual > 0 else 0.0,
            "fps_container": container_fps,
            "resolution": [w, h],
            "laser_events": laser_events,
            "timestamp": datetime.now().isoformat(),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        self.last_written_video_path = output_path
        return output_path

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
        # Timing fields — unified across video and laser_video
        pre_duration: float = 5.0,       # plain video duration OR pre-laser record time
        laser_on_duration: float = 1.0,  # laser_video only — ignored for plain video
        post_duration: float = 2.0,      # laser_video only — ignored for plain video
    ):
        self.running = True
        self.paused = False
        mode = (mode or "image").lower()
        self.is_fast_raw_mode = mode == "raw"
        self.last_written_image_path = None
        self.last_written_video_path = None
        self.status_msg = "Starting experiment..."
        if callback:
            callback(self.status_msg)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exp_dir = os.path.join(self.out_dir, f"{timestamp}_{name}")
        os.makedirs(exp_dir, exist_ok=True)

        csv_path = os.path.join(exp_dir, f"{timestamp}_{name}_points.csv")

        try:
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Well", "X", "Y", "Z", "Capture_File", "Capture_Mode", "Timestamp"])

                laser_controller = None
                if mode == "laser_video":
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
                        # Fastest possible: dump raw sensor buffer to .npy
                        capture_name = f"{label}_{timestamp}.npy"
                        img_path = os.path.join(exp_dir, capture_name)
                        raw_frame = self.camera.get_raw_frame()
                        if raw_frame is not None:
                            np.save(img_path, raw_frame)
                        else:
                            logger.warning(f"Failed to capture raw frame for {label}")

                    elif mode == "video":
                        # Plain video: record for pre_duration seconds, no laser
                        capture_name = f"{label}_{timestamp}.avi"
                        video_path = os.path.join(exp_dir, capture_name)
                        self._write_video(video_path, float(pre_duration))

                    elif mode == "laser_video":
                        # Laser video: pre + laser_on + post, laser fires in the middle
                        capture_name = f"{label}_{timestamp}.avi"
                        video_path = os.path.join(exp_dir, capture_name)
                        total_duration = float(pre_duration) + float(laser_on_duration) + float(post_duration)
                        self._write_video(
                            video_path,
                            total_duration,
                            laser_controller=laser_controller,
                            laser_on_s=float(laser_on_duration),
                            laser_start_s=float(pre_duration),
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
                        else:
                            logger.warning(f"Failed to capture frame for {label}")

                    writer.writerow([label, x, y, z, capture_name, mode, capture_time])
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
            self.is_fast_raw_mode = False

    def stop(self):
        self.running = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False
