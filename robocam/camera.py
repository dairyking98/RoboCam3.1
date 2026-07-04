import cv2
import time
import os
import sys
import glob
import logging
import threading
import numpy as np
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Try to import Picamera2 as fallback/alternative
try:
    from picamera2 import Picamera2
    PICAM2_AVAILABLE = True
except ImportError:
    PICAM2_AVAILABLE = False

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYERONE_SDK_FULL_PYTHON = os.path.join(_PROJECT_ROOT, "PlayerOne_Camera_SDK_Linux_V3.10.0", "python")

def get_playerone_sdk_python_path() -> Optional[str]:
    if os.path.isdir(PLAYERONE_SDK_FULL_PYTHON):
        return PLAYERONE_SDK_FULL_PYTHON
    try:
        for d in os.listdir(_PROJECT_ROOT):
            if d.startswith("PlayerOne_Camera_SDK_Linux_") and os.path.isdir(os.path.join(_PROJECT_ROOT, d)):
                py_path = os.path.join(_PROJECT_ROOT, d, "python")
                if os.path.isdir(py_path):
                    return py_path
    except Exception:
        pass
    path = os.environ.get("PLAYERONE_SDK_PYTHON")
    if path and os.path.isdir(path):
        return path
    default = os.path.expanduser("~/PlayerOne_Camera_SDK_Linux_V3.10.0/python")
    if os.path.isdir(default):
        return default
    try:
        candidates = glob.glob(os.path.expanduser("~/PlayerOne_Camera_SDK_Linux_*/python"))
        if candidates:
            return candidates[0]
    except Exception:
        pass
    return None

def _ensure_pypoa_patched_for_linux(sdk_python_path: str) -> bool:
    if sys.platform == "win32":
        return True
    py_path = os.path.join(sdk_python_path, "pyPOACamera.py")
    if not os.path.isfile(py_path):
        return False
    try:
        with open(py_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if "sys.platform" in content and "LoadLibrary" in content and ".so" in content:
            return True
        if "import sys" not in content:
            content = content.replace(
                "from enum import Enum\n",
                "from enum import Enum\nimport sys\n",
                1,
            )
        new_content = content
        for line in content.split("\n"):
            if "dll" in line and "LoadLibrary" in line and "PlayerOneCamera.dll" in line:
                replacement = (
                    'dll = (cdll.LoadLibrary("./PlayerOneCamera.dll") if sys.platform == "win32" '
                    'else cdll.LoadLibrary("libPlayerOneCamera.so"))'
                )
                new_content = content.replace(line, replacement, 1)
                break
        if new_content != content:
            with open(py_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_content)
        return True
    except Exception as e:
        logger.debug(f"Could not patch pyPOACamera.py: {e}")
        return False

def get_playerone_camera_count() -> int:
    sdk_path = get_playerone_sdk_python_path()
    if sdk_path is None:
        return 0
    _ensure_pypoa_patched_for_linux(sdk_path)
    try:
        prev = list(sys.path)
        if sdk_path not in sys.path:
            sys.path.insert(0, sdk_path)
        try:
            import pyPOACamera as poa
            count = poa.GetCameraCount()
            return int(count) if count is not None else 0
        finally:
            sys.path[:] = prev
    except Exception:
        return 0

class Camera:
    def __init__(self, resolution=(1920, 1080), simulate=False, backend=None, device_index=0, fps: float = 0.0):
        self.resolution = resolution
        self.simulate = simulate
        self.backend = None
        self.picam2 = None
        self.cv2_cap = None
        self._poa = None
        self._camera_id = None
        self.running = False
        self._device_index = device_index
        self.fps_limit = 0.0
        self._po_frame_buf: Optional[np.ndarray] = None

        # Capture-failure counters for get_raw_frame(), reset per raw-burst
        self._stat_lock_timeout = 0
        self._stat_sdk_timeout_or_error = 0

        # Lock to protect SDK calls from simultaneous UI preview and experiment thread access
        self._sdk_lock = threading.Lock()

        if self.simulate:
            self.backend = "simulate"
            self.running = True
        elif backend == "picamera2":
            self._init_picam2(device_index)
        elif backend == "playerone":
            self._init_playerone(device_index)
        elif backend == "cv2":
            self._init_cv2(device_index)
        else:
            # Auto-detect: PlayerOne → picamera2 → cv2
            if get_playerone_camera_count() > 0:
                self._init_playerone(0)
            elif PICAM2_AVAILABLE and Picamera2.global_camera_info():
                self._init_picam2(0)
            else:
                self._init_cv2(0)

        if fps and fps > 0 and self.running:
            self.set_fps(fps)

    def _init_playerone(self, device_index: int = 0):
        sdk_path = get_playerone_sdk_python_path()
        if not sdk_path:
            raise RuntimeError("Player One SDK Python not found.")
        _ensure_pypoa_patched_for_linux(sdk_path)

        prev = list(sys.path)
        if sdk_path not in sys.path:
            sys.path.insert(0, sdk_path)
        try:
            import pyPOACamera as poa
            self._poa = poa
            err, props = poa.GetCameraProperties(device_index)
            if err != poa.POAErrors.POA_OK:
                raise RuntimeError(f"GetCameraProperties failed: {err}")

            self._camera_id = props.cameraID
            err = poa.OpenCamera(self._camera_id)
            if err != poa.POAErrors.POA_OK:
                raise RuntimeError(f"OpenCamera failed: {err}")

            err = poa.InitCamera(self._camera_id)
            if err != poa.POAErrors.POA_OK:
                raise RuntimeError(f"InitCamera failed: {err}")

            w, h = self.resolution
            poa.SetImageStartPos(self._camera_id, 0, 0)
            poa.SetImageSize(self._camera_id, w, h)
            poa.SetImageBin(self._camera_id, 1)
            poa.SetImageFormat(self._camera_id, poa.POAImgFormat.POA_RAW8)
            self._po_frame_buf = np.zeros(w * h, dtype=np.uint8)

            # Set default exposure and gain
            poa.SetExp(self._camera_id, 20000, False)
            poa.SetGain(self._camera_id, 100, False)

            # Video mode continuous capture
            poa.StartExposure(self._camera_id, False)

            self.backend = "playerone"
            self.running = True

            # Fetch max resolution from properties to populate supported list later
            self._max_width = props.maxWidth
            self._max_height = props.maxHeight
            self._playerone_model = props.cameraModelName.decode(errors="replace").strip()

            # isColorCamera/bayerPattern_ come straight from the SDK rather
            # than being assumed — a mono sensor (e.g. the Mars 662M) run
            # through a Bayer demosaic (cv2.COLOR_BAYER_*2BGR) produces
            # color-interpolation artifacts, not clean grayscale, since
            # there's no real color filter array to interpolate from.
            _POA_BAYER_NAMES = {0: "RGGB", 1: "BGGR", 2: "GRBG", 3: "GBRG"}
            if bool(props.isColorCamera):
                self._playerone_bayer_pattern = _POA_BAYER_NAMES.get(props.bayerPattern_, "RGGB")
            else:
                self._playerone_bayer_pattern = "mono"

            logger.info(f"Player One camera opened: {w}x{h}")
        finally:
            sys.path[:] = prev

    def _init_picam2(self, device_index: int = 0):
        available = Picamera2.global_camera_info()
        if not available:
            raise RuntimeError(
                "Picamera2 is installed but libcamera detected no camera sensor. "
                "Check that the ribbon cable is seated correctly and that you are "
                "using the right cable for your Pi model (Pi 5 needs a 22-pin FFC; "
                "Camera Module 2/v1 ship with 15-pin and need an adapter)."
            )
        if device_index >= len(available):
            device_index = 0
        try:
            self.picam2 = Picamera2(device_index)
            # Video config with raw stream: gives burst-rate true Bayer capture
            # alongside the RGB preview stream. create_video_configuration is
            # required — still configs add inter-frame latency that kills burst FPS.
            cfg = self.picam2.create_video_configuration(
                main={"size": self.resolution, "format": "RGB888"},
                raw={},
            )
            self.picam2.configure(cfg)
            self.picam2.start()

            # Cached values for get_exposure / get_gain
            self._picam2_exposure_us = 20000
            self._picam2_gain = 1.0

            # Explicitly disable auto-exposure/auto-gain and push a manual
            # default, mirroring _init_playerone()'s SetExp(...,False)/
            # SetGain(...,False) at connect time. Without this, the camera
            # runs full AE/AGC until the user manually hits Apply in the
            # Calibration tab — for darkfield (mostly-black) scenes, AE
            # chases a "properly exposed" average brightness that was never
            # the point, driving exposure time (and thus achievable fps) up
            # arbitrarily. See PROJECT_STATE.md § 9.
            self._picam2_ae_enabled = False
            self.picam2.set_controls({
                "ExposureTime": self._picam2_exposure_us,
                "AnalogueGain": self._picam2_gain,
                "AeEnable": False,
            })

            # Sensor's native FrameDurationLimits (us) — used to reset an uncapped fps
            fd_limits = self.picam2.camera_controls.get("FrameDurationLimits")
            self._picam2_frame_duration_range = (fd_limits[0], fd_limits[1]) if fd_limits else (100, 1_000_000_000)

            # Cache raw stream metadata for camera_meta.json sidecar
            raw_fmt = (self.picam2.camera_config.get("raw") or {}).get("format", "")
            self._picam2_raw_format = raw_fmt
            # Extract bit depth from format string e.g. "SRGGB10_CSI2P" → 10
            self._picam2_bit_depth = 10
            for bd in (16, 14, 12, 10, 8):
                if str(bd) in raw_fmt:
                    self._picam2_bit_depth = bd
                    break

            # Bayer pattern from libcamera ColorFilterArrangement property
            _CFA = {0: "RGGB", 1: "GRBG", 2: "BGGR", 3: "GBRG", 4: "mono"}
            cfa = self.picam2.camera_properties.get("ColorFilterArrangement", 0)
            self._picam2_bayer_pattern = _CFA.get(cfa, "RGGB")

            self.backend = "picamera2"
            self.running = True
            logger.info(
                f"Picamera2 index {device_index} opened at {self.resolution}, "
                f"raw={raw_fmt or 'none'}, bayer={self._picam2_bayer_pattern}"
            )
        except Exception as e:
            raise RuntimeError(f"Picamera2 init failed: {e}") from e

    def _init_cv2(self, device_index: int = 0):
        self.cv2_cap = cv2.VideoCapture(device_index, cv2.CAP_V4L2)
        if self.cv2_cap.isOpened():
            self.cv2_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            self.cv2_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            self.backend = "cv2"
            self.running = True
        else:
            logger.error(f"Failed to open cv2 camera index {device_index}")
            self.running = False

    def get_exposure(self) -> int:
        """Get exposure in microseconds."""
        if self.backend == "picamera2":
            return self._picam2_exposure_us
        if self.simulate or not self.running or self.backend != "playerone":
            return 20000
        with self._sdk_lock:
            _, val, _ = self._poa.GetConfig(self._camera_id, self._poa.POAConfig.POA_EXPOSURE)
            return int(val)

    def set_exposure(self, us: int) -> None:
        """Set exposure in microseconds."""
        if self.backend == "picamera2":
            self._picam2_exposure_us = int(us)
            self._picam2_ae_enabled = False
            self.picam2.set_controls({"ExposureTime": int(us), "AeEnable": False})
            return
        if self.simulate or not self.running or self.backend != "playerone":
            return
        with self._sdk_lock:
            val = int(round(float(us)))
            self._poa.SetExp(self._camera_id, val, False)

    def get_gain(self) -> int:
        """Get gain; for picamera2 returns analogue gain * 100 to match PlayerOne scale."""
        if self.backend == "picamera2":
            return int(self._picam2_gain * 100)
        if self.simulate or not self.running or self.backend != "playerone":
            return 100
        with self._sdk_lock:
            _, val, _ = self._poa.GetConfig(self._camera_id, self._poa.POAConfig.POA_GAIN)
            return int(val)

    def set_gain(self, gain: int) -> None:
        """Set gain; for picamera2 interprets gain/100 as analogue gain multiplier."""
        if self.backend == "picamera2":
            self._picam2_gain = max(1.0, gain / 100.0)
            self._picam2_ae_enabled = False
            self.picam2.set_controls({"AnalogueGain": self._picam2_gain, "AeEnable": False})
            return
        if self.simulate or not self.running or self.backend != "playerone":
            return
        with self._sdk_lock:
            val = int(round(float(gain)))
            self._poa.SetGain(self._camera_id, val, False)

    def get_ae_enabled(self) -> bool:
        """Get auto-exposure/auto-gain state (Picamera2 only; always False elsewhere)."""
        if self.backend != "picamera2" or self.simulate or not self.running:
            return False
        return bool(getattr(self, "_picam2_ae_enabled", False))

    def set_ae_enabled(self, enabled: bool) -> None:
        """Toggle auto-exposure/auto-gain (Picamera2 only). Off by default at connect —
        see the note in _init_picam2() on why AE fights darkfield/high-fps capture."""
        if self.backend != "picamera2" or self.simulate or not self.running:
            return
        self._picam2_ae_enabled = bool(enabled)
        self.picam2.set_controls({"AeEnable": bool(enabled)})

    def get_fps(self) -> float:
        """Get the current frame-rate cap; 0 means uncapped (max sensor/backend rate)."""
        if self.backend != "playerone" or self.simulate or not self.running:
            return self.fps_limit
        with self._sdk_lock:
            _, val, _ = self._poa.GetConfig(self._camera_id, self._poa.POAConfig.POA_FRAME_LIMIT)
            return float(val)

    def set_fps(self, fps: float) -> None:
        """Set a frame-rate cap; 0 means uncapped (max sensor/backend rate)."""
        fps = max(0.0, float(fps))
        self.fps_limit = fps
        if self.backend == "picamera2":
            if fps > 0:
                dur_us = int(round(1_000_000 / fps))
                self.picam2.set_controls({"FrameDurationLimits": (dur_us, dur_us)})
            else:
                self.picam2.set_controls({"FrameDurationLimits": self._picam2_frame_duration_range})
            return
        if self.backend == "cv2":
            if self.cv2_cap:
                self.cv2_cap.set(cv2.CAP_PROP_FPS, fps if fps > 0 else 30.0)
            return
        if self.simulate or not self.running or self.backend != "playerone":
            return
        with self._sdk_lock:
            self._poa.SetConfig(self._camera_id, self._poa.POAConfig.POA_FRAME_LIMIT, int(round(fps)), False)

    def get_hqi(self) -> bool:
        """Get High Quality Image mode — on cameras without DDR, HQI trades frame rate for image quality."""
        if self.simulate or not self.running or self.backend != "playerone":
            return False
        with self._sdk_lock:
            _, val, _ = self._poa.GetConfig(self._camera_id, self._poa.POAConfig.POA_HQI)
            return bool(val)

    def set_hqi(self, enabled: bool) -> None:
        """Set High Quality Image mode on/off."""
        if self.simulate or not self.running or self.backend != "playerone":
            return
        with self._sdk_lock:
            self._poa.SetConfig(self._camera_id, self._poa.POAConfig.POA_HQI, bool(enabled), False)

    def get_usb_bandwidth(self) -> int:
        """Get USB bandwidth limit, percent [35-100]."""
        if self.simulate or not self.running or self.backend != "playerone":
            return 100
        with self._sdk_lock:
            _, val, _ = self._poa.GetConfig(self._camera_id, self._poa.POAConfig.POA_USB_BANDWIDTH_LIMIT)
            return int(val)

    def set_usb_bandwidth(self, percent: int) -> None:
        """Set USB bandwidth limit, percent [35-100]."""
        if self.simulate or not self.running or self.backend != "playerone":
            return
        with self._sdk_lock:
            val = max(35, min(100, int(round(percent))))
            self._poa.SetConfig(self._camera_id, self._poa.POAConfig.POA_USB_BANDWIDTH_LIMIT, val, False)

    def get_offset(self) -> int:
        """Get sensor black-level offset."""
        if self.simulate or not self.running or self.backend != "playerone":
            return 0
        with self._sdk_lock:
            _, val, _ = self._poa.GetConfig(self._camera_id, self._poa.POAConfig.POA_OFFSET)
            return int(val)

    def set_offset(self, offset: int) -> None:
        """Set sensor black-level offset."""
        if self.simulate or not self.running or self.backend != "playerone":
            return
        with self._sdk_lock:
            self._poa.SetConfig(self._camera_id, self._poa.POAConfig.POA_OFFSET, int(round(offset)), False)

    def list_sensor_modes(self) -> list[str]:
        """Return sensor mode names supported by this camera; empty if the camera has no mode selection."""
        if self.simulate or not self.running or self.backend != "playerone":
            return []
        with self._sdk_lock:
            err, count = self._poa.GetSensorModeCount(self._camera_id)
            if err != self._poa.POAErrors.POA_OK or count <= 0:
                return []
            names = []
            for i in range(count):
                err, info = self._poa.GetSensorModeInfo(self._camera_id, i)
                if err == self._poa.POAErrors.POA_OK:
                    names.append(info.name.decode(errors="replace").strip())
                else:
                    names.append(f"Mode {i}")
            return names

    def get_sensor_mode_index(self) -> int:
        """Get the currently active sensor mode index; -1 if unsupported."""
        if self.simulate or not self.running or self.backend != "playerone":
            return -1
        with self._sdk_lock:
            err, idx = self._poa.GetSensorMode(self._camera_id)
            if err != self._poa.POAErrors.POA_OK:
                return -1
            return int(idx)

    def set_sensor_mode(self, index: int) -> None:
        """Set the sensor mode by index. Stops and restarts exposure, as the SDK requires."""
        if self.simulate or not self.running or self.backend != "playerone":
            return
        with self._sdk_lock:
            self._poa.StopExposure(self._camera_id)
            self._poa.SetSensorMode(self._camera_id, int(index))
            self._poa.StartExposure(self._camera_id, False)

    def get_supported_resolutions(self) -> list[Tuple[int, int]]:
        """Return supported resolutions based on camera properties."""
        standards = [
            (640, 480),
            (800, 600),
            (1024, 768),
            (1280, 720),
            (1280, 960),
            (1600, 1200),
            (1920, 1080)
        ]
        
        if self.simulate or not self.running or self.backend != "playerone":
            return standards + [(1936, 1100)]
            
        res = []
        for w, h in standards:
            if w <= getattr(self, "_max_width", 1920) and h <= getattr(self, "_max_height", 1080):
                res.append((w, h))
                
        native = (getattr(self, "_max_width", 1920), getattr(self, "_max_height", 1080))
        if native not in res:
            res.append(native)
            
        res.sort(key=lambda x: x[0] * x[1])
        return res

    def set_resolution(self, w: int, h: int) -> None:
        """Set the camera resolution dynamically."""
        if self.simulate or not self.running or self.backend != "playerone":
            self.resolution = (w, h)
            return
            
        with self._sdk_lock:
            self._poa.StopExposure(self._camera_id)
            self._poa.SetImageSize(self._camera_id, w, h)
            self.resolution = (w, h)
            self._po_frame_buf = np.zeros(w * h, dtype=np.uint8)
            self._poa.StartExposure(self._camera_id, False)
            logger.info(f"Resolution changed to {w}x{h}")

    def get_frame(self) -> Optional[np.ndarray]:
        """Returns a BGR image frame for display or saving."""
        if self.simulate or not self.running:
            return np.zeros((self.resolution[1], self.resolution[0], 3), dtype=np.uint8)
            
        if self.backend == "playerone":
            if not self._sdk_lock.acquire(timeout=0.05):
                return None
            try:
                poa = self._poa
                cid = self._camera_id
                
                deadline = time.monotonic() + 0.1
                while time.monotonic() < deadline:
                    err, ready = poa.ImageReady(cid)
                    if err == poa.POAErrors.POA_OK and ready:
                        break
                    time.sleep(0.005)
                else:
                    return None
                    
                w, h = self.resolution
                buf = np.zeros(w * h, dtype=np.uint8)
                err = poa.GetImageData(cid, buf, 500)
                if err != poa.POAErrors.POA_OK:
                    return None
                    
                frame = buf.reshape((h, w)).copy()
                return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            finally:
                self._sdk_lock.release()
                
        elif self.backend == "picamera2":
            return self.picam2.capture_array("main")
            
        elif self.backend == "cv2":
            ret, frame = self.cv2_cap.read()
            if ret:
                return frame
                
        return np.zeros((self.resolution[1], self.resolution[0], 3), dtype=np.uint8)

    def reset_capture_stats(self) -> None:
        """Zero the get_raw_frame() failure counters — call at the start of a raw burst."""
        self._stat_lock_timeout = 0
        self._stat_sdk_timeout_or_error = 0

    def get_capture_stats(self) -> dict:
        """Return counts of get_raw_frame() failures since the last reset_capture_stats()."""
        return {
            "lock_timeout": self._stat_lock_timeout,
            "sdk_timeout_or_error": self._stat_sdk_timeout_or_error,
        }

    def get_dropped_frames_count(self) -> int:
        """SDK-side dropped-frame count (frames the driver dropped before delivery); resets on stop."""
        if self.simulate or not self.running or self.backend != "playerone":
            return 0
        with self._sdk_lock:
            err, count = self._poa.GetDroppedImagesCount(self._camera_id)
            if err != self._poa.POAErrors.POA_OK:
                return 0
            return int(count)

    def get_raw_frame(self) -> Optional[np.ndarray]:
        """Returns the raw 1D/2D buffer directly from the sensor for fast writing."""
        if self.simulate or not self.running:
            return np.zeros((self.resolution[1], self.resolution[0]), dtype=np.uint8)

        if self.backend == "playerone":
            if not self._sdk_lock.acquire(timeout=0.05):
                self._stat_lock_timeout += 1
                return None
            try:
                poa = self._poa
                cid = self._camera_id

                # Direct blocking fetch instead of a manual ImageReady() poll loop —
                # the SDK's own GetImageData() blocks internally until the frame is
                # ready or the timeout elapses. Timeout is bounded (not -1/infinite)
                # so control still returns often enough for the caller to notice a
                # stop request. See PROJECT_STATE.md § 9.
                # NOTE: use poa.GetConfig() directly, not self.get_exposure() — that
                # method re-acquires _sdk_lock, which would deadlock since we're
                # already holding it here (threading.Lock is not reentrant).
                _, exposure_us, _ = poa.GetConfig(cid, poa.POAConfig.POA_EXPOSURE)
                timeout_ms = min(200, max(20, int(exposure_us) // 1000 + 50))
                err = poa.GetImageData(cid, self._po_frame_buf, timeout_ms)
                if err != poa.POAErrors.POA_OK:
                    self._stat_sdk_timeout_or_error += 1
                    return None
                w, h = self.resolution
                return self._po_frame_buf.reshape((h, w)).copy()
            finally:
                self._sdk_lock.release()
                
        elif self.backend == "picamera2":
            # True Bayer raw from the raw stream configured in _init_picam2.
            # Returns uint16 array at native sensor resolution.
            return self.picam2.capture_array("raw")
            
        elif self.backend == "cv2":
            ret, frame = self.cv2_cap.read()
            if ret:
                return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                
        return np.zeros((self.resolution[1], self.resolution[0]), dtype=np.uint8)

    def get_camera_meta(self) -> dict:
        """Return a metadata dict to write as camera_meta.json alongside .npy frames."""
        if self.backend == "picamera2":
            info = Picamera2.global_camera_info()
            model = (info[self._device_index].get("Model", "unknown")
                     if info and self._device_index < len(info) else "unknown")
            return {
                "backend": "picamera2",
                "model": model,
                "resolution": list(self.resolution),
                "raw_format": getattr(self, "_picam2_raw_format", ""),
                "bit_depth": getattr(self, "_picam2_bit_depth", 10),
                "bayer_pattern": getattr(self, "_picam2_bayer_pattern", "RGGB"),
                "analogue_gain": getattr(self, "_picam2_gain", 1.0),
                "exposure_us": getattr(self, "_picam2_exposure_us", 20000),
                "ae_enabled": getattr(self, "_picam2_ae_enabled", False),
                "fps_limit": self.fps_limit,
            }
        if self.backend == "playerone":
            sensor_modes = self.list_sensor_modes()
            mode_idx = self.get_sensor_mode_index()
            return {
                "backend": "playerone",
                "model": getattr(self, "_playerone_model", ""),
                "resolution": list(self.resolution),
                "bit_depth": 8,
                "bayer_pattern": getattr(self, "_playerone_bayer_pattern", "RGGB"),
                "gain": self.get_gain(),
                "exposure_us": self.get_exposure(),
                "fps_limit": self.get_fps(),
                "hqi_enabled": self.get_hqi(),
                "usb_bandwidth_limit": self.get_usb_bandwidth(),
                "offset": self.get_offset(),
                "sensor_mode_index": mode_idx,
                "sensor_mode_name": sensor_modes[mode_idx] if 0 <= mode_idx < len(sensor_modes) else "",
            }
        return {
            "backend": self.backend or "unknown",
            "resolution": list(self.resolution),
        }

    def stop(self):
        self.running = False
        if self.backend == "playerone" and self._poa is not None:
            with self._sdk_lock:
                try:
                    self._poa.StopExposure(self._camera_id)
                    self._poa.CloseCamera(self._camera_id)
                except Exception:
                    pass
                self._poa = None
        elif self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
            except:
                pass
        elif self.cv2_cap:
            self.cv2_cap.release()
