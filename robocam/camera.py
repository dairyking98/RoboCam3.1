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
    def __init__(self, resolution=(1920, 1080), simulate=False):
        self.resolution = resolution
        self.simulate = simulate
        self.backend = None
        self.picam2 = None
        self.cv2_cap = None
        self._poa = None
        self._camera_id = None
        self.running = False
        
        # Lock to protect SDK calls from simultaneous UI preview and experiment thread access
        self._sdk_lock = threading.Lock()
        
        if self.simulate:
            self.backend = "simulate"
            self.running = True
        else:
            # Try PlayerOne first
            if get_playerone_camera_count() > 0:
                self._init_playerone()
            # Fallback to Picamera2
            elif PICAM2_AVAILABLE:
                self._init_picam2()
            # Fallback to generic OpenCV
            else:
                self._init_cv2()
                
    def _init_playerone(self):
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
            err, props = poa.GetCameraProperties(0)
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
            
            logger.info(f"Player One camera opened: {w}x{h}")
        finally:
            sys.path[:] = prev

    def _init_picam2(self):
        try:
            self.picam2 = Picamera2()
            # Force RGB888 so capture_array always returns 3-channel RGB
            cfg = self.picam2.create_preview_configuration(
                main={"size": self.resolution, "format": "RGB888"}
            )
            self.picam2.configure(cfg)
            self.picam2.start()
            # Cached values for get_exposure / get_gain (avoids blocking capture_metadata)
            self._picam2_exposure_us = 20000
            self._picam2_gain = 1.0
            self.backend = "picamera2"
            self.running = True
            logger.info(f"Picamera2 opened at {self.resolution}")
        except Exception as e:
            logger.error(f"Picamera2 failed: {e}")
            self._init_cv2()

    def _init_cv2(self):
        self.cv2_cap = cv2.VideoCapture(0)
        if self.cv2_cap.isOpened():
            self.cv2_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            self.cv2_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            self.backend = "cv2"
            self.running = True
        else:
            logger.error("Failed to open cv2 camera")
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
            self.picam2.set_controls({"AnalogueGain": self._picam2_gain, "AeEnable": False})
            return
        if self.simulate or not self.running or self.backend != "playerone":
            return
        with self._sdk_lock:
            val = int(round(float(gain)))
            self._poa.SetGain(self._camera_id, val, False)

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

    def get_raw_frame(self) -> Optional[np.ndarray]:
        """Returns the raw 1D/2D buffer directly from the sensor for fast writing."""
        if self.simulate or not self.running:
            return np.zeros((self.resolution[1], self.resolution[0]), dtype=np.uint8)
            
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
                return buf.reshape((h, w)).copy()
            finally:
                self._sdk_lock.release()
                
        elif self.backend == "picamera2":
            # Picamera2 array is usually RGB, return grayscale equivalent for "raw" fast-write
            frame = self.picam2.capture_array("main")
            return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            
        elif self.backend == "cv2":
            ret, frame = self.cv2_cap.read()
            if ret:
                return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                
        return np.zeros((self.resolution[1], self.resolution[0]), dtype=np.uint8)

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
