import json
import os
from typing import Tuple, List, Optional
from .config import get_config

class CalibrationManager:
    def __init__(self):
        self.config = get_config()
        self.cal_dir = self.config.get("paths.calibration_dir", "config/calibrations")
        os.makedirs(self.cal_dir, exist_ok=True)
        
        self.upper_left = None
        self.lower_left = None
        self.upper_right = None
        self.lower_right = None
        
        self.width = 12
        self.depth = 8
        
    def generate_path(self) -> List[Tuple[float, float, float]]:
        if not all([self.upper_left, self.lower_left, self.upper_right, self.lower_right]):
            raise ValueError("All 4 corners must be set before generating path")
            
        path = []
        x1, y1, z1 = self.upper_left
        x2, y2, z2 = self.lower_left
        x3, y3, z3 = self.upper_right
        x4, y4, z4 = self.lower_right
        
        for i in range(self.depth):
            for j in range(self.width):
                u = j / (self.width - 1) if self.width > 1 else 0.0
                v = i / (self.depth - 1) if self.depth > 1 else 0.0
                
                top_x = x1 + u * (x3 - x1)
                top_y = y1 + u * (y3 - y1)
                top_z = z1 + u * (z3 - z1)
                
                bottom_x = x2 + u * (x4 - x2)
                bottom_y = y2 + u * (y4 - y2)
                bottom_z = z2 + u * (z4 - z2)
                
                x = top_x + v * (bottom_x - top_x)
                y = top_y + v * (bottom_y - top_y)
                z = top_z + v * (bottom_z - top_z)
                
                path.append((x, y, z))
                
        return path
        
    def save(self, name: str):
        if not all([self.upper_left, self.lower_left, self.upper_right, self.lower_right]):
            raise ValueError("All 4 corners must be set")
            
        path = self.generate_path()
        labels = []
        for i in range(self.depth):
            row_char = chr(ord('A') + i)
            for j in range(self.width):
                labels.append(f"{row_char}{j+1}")
                
        data = {
            "name": name,
            "x_quantity": self.width,
            "y_quantity": self.depth,
            "upper_left": self.upper_left,
            "lower_left": self.lower_left,
            "upper_right": self.upper_right,
            "lower_right": self.lower_right,
            "interpolated_positions": path,
            "labels": labels
        }
        
        filepath = os.path.join(self.cal_dir, f"{name}.json")
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
            
    def load(self, filepath: str):
        with open(filepath, 'r') as f:
            data = json.load(f)
            
        self.width = data.get("x_quantity", 12)
        self.depth = data.get("y_quantity", 8)
        self.upper_left = tuple(data["upper_left"]) if data.get("upper_left") else None
        self.lower_left = tuple(data["lower_left"]) if data.get("lower_left") else None
        self.upper_right = tuple(data["upper_right"]) if data.get("upper_right") else None
        self.lower_right = tuple(data["lower_right"]) if data.get("lower_right") else None
        
        return data.get("interpolated_positions", []), data.get("labels", [])
