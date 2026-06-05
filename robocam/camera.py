import cv2
import time
from typing import Optional

try:
    from picamera2 import Picamera2
    PICAM2_AVAILABLE = True
except ImportError:
    PICAM2_AVAILABLE = False

class Camera:
    def __init__(self, resolution=(800, 600), simulate=False):
        self.resolution = resolution
        self.simulate = simulate
        self.backend = None
        self.picam2 = None
        self.cv2_cap = None
        self.running = False
        
        if not self.simulate:
            if PICAM2_AVAILABLE:
                try:
                    self.picam2 = Picamera2()
                    cfg = self.picam2.create_preview_configuration(main={"size": self.resolution})
                    self.picam2.configure(cfg)
                    self.picam2.start()
                    self.backend = "picamera2"
                    self.running = True
                except Exception as e:
                    print(f"Picamera2 failed: {e}")
                    self._init_cv2()
            else:
                self._init_cv2()
                
    def _init_cv2(self):
        self.cv2_cap = cv2.VideoCapture(0)
        if self.cv2_cap.isOpened():
            self.cv2_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            self.cv2_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            self.backend = "cv2"
            self.running = True
        else:
            print("Failed to open cv2 camera")
            self.running = False
            
    def get_frame(self):
        if self.simulate or not self.running:
            import numpy as np
            # Return dummy black frame
            return np.zeros((self.resolution[1], self.resolution[0], 3), dtype=np.uint8)
            
        if self.backend == "picamera2":
            return self.picam2.capture_array("main")
        elif self.backend == "cv2":
            ret, frame = self.cv2_cap.read()
            if ret:
                return frame
            
        import numpy as np
        return np.zeros((self.resolution[1], self.resolution[0], 3), dtype=np.uint8)
        
    def stop(self):
        self.running = False
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
            except:
                pass
        if self.cv2_cap:
            self.cv2_cap.release()
