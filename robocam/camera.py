import cv2
import time
import os
import sys
import glob
import logging
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
        
        if not self.simulate:
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
            
            self.backend = "playerone"
            self.running = True
            logger.info(f"Player One camera opened: {w}x{h}")
        finally:
            sys.path[:] = prev

    def _init_picam2(self):
        try:
            self.picam2 = Picamera2()
            cfg = self.picam2.create_preview_configuration(main={"size": self.resolution})
            self.picam2.configure(cfg)
            self.picam2.start()
            self.backend = "picamera2"
            self.running = True
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

    def get_frame(self):
        if self.simulate or not self.running:
            return np.zeros((self.resolution[1], self.resolution[0], 3), dtype=np.uint8)
            
        if self.backend == "playerone":
            poa = self._poa
            cid = self._camera_id
            poa.StartExposure(cid, True)
            
            for _ in range(100):
                _err, ready = poa.ImageReady(cid)
                if ready:
                    break
                time.sleep(0.01)
            else:
                return None
                
            w, h = self.resolution
            buf = np.zeros(w * h, dtype=np.uint8)
            err = poa.GetImageData(cid, buf, 500)
            if err != poa.POAErrors.POA_OK:
                return None
            frame = buf.reshape((h, w)).copy()
            # PlayerOne returns grayscale RAW8, convert to BGR for uniform handling in app
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            
        elif self.backend == "picamera2":
            return self.picam2.capture_array("main")
            
        elif self.backend == "cv2":
            ret, frame = self.cv2_cap.read()
            if ret:
                return frame
                
        return np.zeros((self.resolution[1], self.resolution[0], 3), dtype=np.uint8)

    def stop(self):
        self.running = False
        if self.backend == "playerone" and self._poa is not None:
            try:
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
