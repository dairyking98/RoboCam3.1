import json
import os
from typing import Tuple, List
from .config import get_config

class WellPlate:
    """Generates and stores the path for a well plate experiment, matching Suite 2.0."""
    PATTERN_RASTER = "Raster"
    PATTERN_SNAKE  = "Snake"

    def __init__(
        self,
        width: int,
        depth: int,
        corners: List[Tuple[float, float, float]],
        pattern: str = PATTERN_RASTER,
    ):
        if len(corners) != 4:
            raise ValueError("Exactly four corner points are required (UL, LL, UR, LR).")
        self.width   = width
        self.depth   = depth
        self.corners = corners
        self.pattern = pattern
        self.path    = self._generate_path()

    def _interpolate(self, row_i: int, col_j: int) -> Tuple[float, float, float]:
        """Bilinear interpolation for a single well position."""
        upper_left, lower_left, upper_right, lower_right = self.corners
        x1, y1, z1 = upper_left
        x2, y2, z2 = lower_left
        x3, y3, z3 = upper_right
        x4, y4, z4 = lower_right

        u = col_j / (self.width - 1) if self.width > 1 else 0.0
        v = row_i / (self.depth - 1) if self.depth > 1 else 0.0

        top_x = x1 + u * (x3 - x1)
        top_y = y1 + u * (y3 - y1)
        top_z = z1 + u * (z3 - z1)

        bot_x = x2 + u * (x4 - x2)
        bot_y = y2 + u * (y4 - y2)
        bot_z = z2 + u * (z4 - z2)

        return (
            top_x + v * (bot_x - top_x),
            top_y + v * (bot_y - top_y),
            top_z + v * (bot_z - top_z),
        )

    def _generate_path(self) -> List[Tuple[float, float, float]]:
        path: List[Tuple[float, float, float]] = []
        for row_i in range(self.depth):
            cols = range(self.width)
            if self.pattern == self.PATTERN_SNAKE and row_i % 2 == 1:
                cols = range(self.width - 1, -1, -1)
            for col_j in cols:
                path.append(self._interpolate(row_i, col_j))
        return path

    def get_path_with_labels(self) -> List[Tuple[str, Tuple[float, float, float]]]:
        def _row_label(i: int) -> str:
            label = ""
            i += 1
            while i > 0:
                i, rem = divmod(i - 1, 26)
                label = chr(ord('A') + rem) + label
            return label

        result: List[Tuple[str, Tuple[float, float, float]]] = []
        for row_i in range(self.depth):
            cols = range(self.width)
            if self.pattern == self.PATTERN_SNAKE and row_i % 2 == 1:
                cols = range(self.width - 1, -1, -1)
            for col_j in cols:
                label = f"{_row_label(row_i)}{col_j + 1}"
                result.append((label, self._interpolate(row_i, col_j)))
        return result


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
        self.pattern = WellPlate.PATTERN_RASTER
        
    def generate_path_with_labels(self) -> List[Tuple[str, Tuple[float, float, float]]]:
        if not all([self.upper_left, self.lower_left, self.upper_right, self.lower_right]):
            raise ValueError("All 4 corners must be set before generating path")
            
        corners = [self.upper_left, self.lower_left, self.upper_right, self.lower_right]
        plate = WellPlate(self.width, self.depth, corners, self.pattern)
        return plate.get_path_with_labels()
        
    def save(self, name: str):
        path_with_labels = self.generate_path_with_labels()
        labels = [item[0] for item in path_with_labels]
        path = [item[1] for item in path_with_labels]
                
        data = {
            "name": name,
            "x_quantity": self.width,
            "y_quantity": self.depth,
            "pattern": self.pattern,
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
        self.pattern = data.get("pattern", WellPlate.PATTERN_RASTER)
        self.upper_left = tuple(data["upper_left"]) if data.get("upper_left") else None
        self.lower_left = tuple(data["lower_left"]) if data.get("lower_left") else None
        self.upper_right = tuple(data["upper_right"]) if data.get("upper_right") else None
        self.lower_right = tuple(data["lower_right"]) if data.get("lower_right") else None
        
        return data.get("interpolated_positions", []), data.get("labels", [])
