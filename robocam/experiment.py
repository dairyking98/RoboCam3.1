import os
import time
import cv2
import csv
import logging
import numpy as np
from datetime import datetime
from typing import List, Tuple
from .config import get_config

logger = logging.getLogger(__name__)

class ExperimentRunner:
    """
    Experiment Runner aligned with RoboCam-Suite 2.0 behavior, 
    now supporting fast raw capture to prioritize framerate.
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
        
    def run(self, name: str, positions: List[Tuple[float, float, float]], labels: List[str], delay_per_well: float = 1.0, callback=None, fast_raw_mode: bool = False):
        self.running = True
        self.paused = False
        self.is_fast_raw_mode = fast_raw_mode
        self.last_written_image_path = None
        self.status_msg = "Starting experiment..."
        if callback: callback(self.status_msg)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exp_dir = os.path.join(self.out_dir, f"{timestamp}_{name}")
        os.makedirs(exp_dir, exist_ok=True)
        
        csv_path = os.path.join(exp_dir, f"{timestamp}_{name}_points.csv")
        
        try:
            with open(csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Well", "X", "Y", "Z", "Image_File", "Timestamp"])
                
                for i, (pos, label) in enumerate(zip(positions, labels)):
                    if not self.running:
                        self.status_msg = "Experiment stopped by user."
                        if callback: callback(self.status_msg)
                        break
                        
                    while self.paused:
                        self.status_msg = "Experiment paused."
                        if callback: callback(self.status_msg)
                        time.sleep(0.1)
                        if not self.running:
                            break
                            
                    if not self.running:
                        break
                        
                    self.current_well = label
                    x, y, z = pos
                    
                    self.status_msg = f"Moving to {label} ({i+1}/{len(positions)})..."
                    logger.info(self.status_msg)
                    if callback: callback(self.status_msg)
                    
                    self.motion.move_absolute(X=x, Y=y, Z=z)
                    
                    self.status_msg = f"Waiting for stabilization at {label}..."
                    if callback: callback(self.status_msg)
                    time.sleep(delay_per_well)
                    
                    self.status_msg = f"Recording well {label}..."
                    logger.info(self.status_msg)
                    if callback: callback(self.status_msg)
                    
                    capture_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    if fast_raw_mode:
                        # Capture raw sensor buffer and dump to binary .npy for maximum speed
                        img_name = f"{label}_{timestamp}.npy"
                        img_path = os.path.join(exp_dir, img_name)
                        
                        raw_frame = self.camera.get_raw_frame()
                        if raw_frame is not None:
                            np.save(img_path, raw_frame)
                        else:
                            logger.warning(f"Failed to capture raw frame for well {label}")
                    else:
                        # Standard BGR/RGB capture to JPG
                        img_name = f"{label}_{timestamp}.jpg"
                        img_path = os.path.join(exp_dir, img_name)
                        
                        frame = self.camera.get_frame()
                        if frame is not None:
                            if self.camera.backend == "picamera2":
                                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                            cv2.imwrite(img_path, frame)
                            self.last_written_image_path = img_path
                        else:
                            logger.warning(f"Failed to capture frame for well {label}")
                        
                    writer.writerow([label, x, y, z, img_name, capture_time])
                    f.flush()
                    
            if self.running:
                self.status_msg = "Experiment finished."
                logger.info(self.status_msg)
                if callback: callback(self.status_msg)
                
        except Exception as e:
            self.status_msg = f"Experiment error: {e}"
            logger.error(self.status_msg, exc_info=True)
            if callback: callback(self.status_msg)
        finally:
            self.running = False
            self.current_well = ""
            self.is_fast_raw_mode = False
            
    def stop(self):
        self.running = False
        
    def pause(self):
        self.paused = True
        
    def resume(self):
        self.paused = False
