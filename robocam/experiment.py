import os
import time
import cv2
import csv
from datetime import datetime
from typing import List, Tuple, Optional
from .config import get_config

class ExperimentRunner:
    def __init__(self, motion_controller, camera):
        self.motion = motion_controller
        self.camera = camera
        self.config = get_config()
        self.out_dir = self.config.get("paths.output_dir", "outputs")
        os.makedirs(self.out_dir, exist_ok=True)
        
        self.running = False
        self.paused = False
        
    def run(self, name: str, positions: List[Tuple[float, float, float]], labels: List[str], delay_per_well: float = 1.0):
        self.running = True
        self.paused = False
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exp_dir = os.path.join(self.out_dir, f"{timestamp}_{name}")
        os.makedirs(exp_dir, exist_ok=True)
        
        csv_path = os.path.join(exp_dir, f"{timestamp}_{name}_points.csv")
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Well", "X", "Y", "Z", "Image_File"])
            
            for i, (pos, label) in enumerate(zip(positions, labels)):
                if not self.running:
                    break
                    
                while self.paused:
                    time.sleep(0.1)
                    if not self.running:
                        break
                        
                if not self.running:
                    break
                    
                x, y, z = pos
                print(f"Moving to {label} at ({x:.2f}, {y:.2f}, {z:.2f})")
                self.motion.move_absolute(X=x, Y=y, Z=z)
                
                # Wait for stabilization
                time.sleep(delay_per_well)
                
                # Capture
                img_name = f"{label}_{timestamp}.jpg"
                img_path = os.path.join(exp_dir, img_name)
                
                frame = self.camera.get_frame()
                if frame is not None:
                    # Convert RGB to BGR for OpenCV save if it's from picamera
                    if self.camera.backend == "picamera2":
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    cv2.imwrite(img_path, frame)
                    
                writer.writerow([label, x, y, z, img_name])
                f.flush()
                
        self.running = False
        print("Experiment finished.")
        
    def stop(self):
        self.running = False
        
    def pause(self):
        self.paused = True
        
    def resume(self):
        self.paused = False
