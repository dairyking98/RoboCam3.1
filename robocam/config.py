import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

class Config:
    DEFAULT_CONFIG = {
        "hardware": {
            "printer": {
                "baudrate": 115200,
                "timeout": 10.0,
                "home_timeout": 90.0,
                "movement_wait_timeout": 30.0,
                "command_delay": 0.1,
                "position_update_delay": 0.1,
                "connection_retry_delay": 2.0,
                "max_retries": 5
            },
            "camera": {
                "preview_resolution": [800, 600],
                "default_fps": 30.0
            }
        },
        "paths": {
            "config_dir": "config",
            "calibration_dir": "config/calibrations",
            "experiment_dir": "experiments",
            "output_dir": "outputs"
        }
    }
    
    def __init__(self, config_file: Optional[str] = None):
        self.config = self._deep_copy(self.DEFAULT_CONFIG)
        self.config_file = Path(config_file) if config_file else Path("config/default_config.json")
        
        if self.config_file.exists():
            self.load_config(str(self.config_file))
        else:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            self.save_config(str(self.config_file))
            
    def _deep_copy(self, d):
        return json.loads(json.dumps(d))
        
    def _deep_update(self, d, u):
        for k, v in u.items():
            if isinstance(v, dict):
                d[k] = self._deep_update(d.get(k, {}), v)
            else:
                d[k] = v
        return d
        
    def load_config(self, filepath: str):
        try:
            with open(filepath, 'r') as f:
                user_config = json.load(f)
                self._deep_update(self.config, user_config)
        except Exception as e:
            print(f"Error loading config: {e}")
            
    def save_config(self, filepath: str):
        try:
            with open(filepath, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")
            
    def get(self, key_path: str, default: Any = None) -> Any:
        keys = key_path.split('.')
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

_global_config = None

def get_config() -> Config:
    global _global_config
    if _global_config is None:
        _global_config = Config()
    return _global_config
