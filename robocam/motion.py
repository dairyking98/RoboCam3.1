import serial
import serial.tools.list_ports
import time
import re
import logging
import requests
from typing import Optional, Tuple
from .config import get_config

logger = logging.getLogger(__name__)

class MotionBackend:
    def connect(self): raise NotImplementedError
    def send_gcode(self, command: str, timeout: Optional[float] = None, ignore_errors: bool = False): raise NotImplementedError
    def home(self): raise NotImplementedError
    def update_position(self) -> Tuple[float, float, float]: raise NotImplementedError
    def move_relative(self, X=None, Y=None, Z=None, speed=None): raise NotImplementedError
    def move_absolute(self, X=None, Y=None, Z=None, speed=None): raise NotImplementedError

class MarlinBackend(MotionBackend):
    def __init__(self):
        self.config = get_config()
        printer_cfg = self.config.get("hardware.printer", {})
        
        self.baud_rate = printer_cfg.get("baudrate", 115200)
        self.timeout = printer_cfg.get("timeout", 10.0)
        self.home_timeout = printer_cfg.get("home_timeout", 90.0)
        self.movement_wait_timeout = printer_cfg.get("movement_wait_timeout", 30.0)
        self.command_delay = printer_cfg.get("command_delay", 0.1)
        self.max_retries = printer_cfg.get("max_retries", 5)
        
        self.X, self.Y, self.Z = None, None, None
        self.serial_conn = None
        self._m400_supported = False
        
    def connect(self):
        port = self._find_port()
        if not port:
            raise ConnectionError("No serial port found.")
            
        retries = 0
        while retries < self.max_retries:
            try:
                self.serial_conn = serial.Serial(port, self.baud_rate, timeout=self.timeout)
                time.sleep(2)
                self._dump_output()
                time.sleep(3)
                self._dump_output()
                
                self.update_position()
                
                try:
                    self.send_gcode("M400", timeout=5.0)
                    self._m400_supported = True
                except:
                    self._m400_supported = False
                    
                return
            except serial.SerialException as e:
                retries += 1
                time.sleep(2)
        raise ConnectionError("Failed to connect to Marlin printer.")
        
    def _find_port(self):
        ports = serial.tools.list_ports.comports()
        for p in ports:
            if 'USB' in p.description:
                try:
                    s = serial.Serial(p.device, self.baud_rate, timeout=1)
                    s.close()
                    return p.device
                except:
                    pass
        return None
        
    def _dump_output(self):
        if not self.serial_conn: return
        while self.serial_conn.in_waiting > 0:
            try:
                self.serial_conn.readline()
            except:
                break
                
    def send_gcode(self, command: str, timeout: Optional[float] = None, ignore_errors: bool = False):
        if not self.serial_conn:
            raise ConnectionError("Not connected")
            
        timeout = timeout or self.timeout
        self.serial_conn.write((command + '\n').encode('utf-8'))
        self.serial_conn.flush()
        time.sleep(self.command_delay)
        
        start_time = time.time()
        while True:
            if time.time() - start_time > timeout:
                raise TimeoutError(f"Command '{command}' timed out")
                
            if self.serial_conn.in_waiting > 0:
                try:
                    resp = self.serial_conn.readline().decode('utf-8', errors='replace').strip()
                    if "ok" in resp.lower():
                        break
                    elif "error" in resp.lower() and not ignore_errors:
                        raise RuntimeError(f"Printer error: {resp}")
                except Exception:
                    continue
            time.sleep(0.01)
            
    def home(self):
        try:
            self.send_gcode('G28', timeout=self.home_timeout)
            self.update_position()
        except Exception as e:
            if "M999" in str(e).upper():
                self.send_gcode("M999", timeout=20.0, ignore_errors=True)
                time.sleep(2)
                self.send_gcode('G28', timeout=self.home_timeout)
                self.update_position()
            else:
                raise
                
    def update_position(self) -> Tuple[float, float, float]:
        self.serial_conn.write(b"M114\n")
        time.sleep(0.1)
        
        start_time = time.time()
        resp = ""
        while time.time() - start_time < self.timeout:
            if self.serial_conn.in_waiting > 0:
                resp = self.serial_conn.readline().decode('utf-8', errors='replace').strip()
                if resp.startswith('X:'):
                    break
            time.sleep(0.01)
            
        matches = re.findall(r'(X|Y|Z):([0-9.-]+)', resp)
        pos = {axis: float(val) for axis, val in matches}
        
        if pos:
            self.X = pos.get('X', self.X)
            self.Y = pos.get('Y', self.Y)
            self.Z = pos.get('Z', self.Z)
            
        self._dump_output()
        return self.X, self.Y, self.Z
        
    def _wait_movement(self, timeout=None):
        timeout = timeout or self.movement_wait_timeout
        if self._m400_supported:
            try:
                self.send_gcode("M400", timeout=timeout)
            except:
                time.sleep(2.0)
        else:
            time.sleep(2.0)
            
    def move_relative(self, X=None, Y=None, Z=None, speed=None):
        self.send_gcode('G91')
        cmd = "G0"
        if speed: cmd += f" F{speed}"
        if X is not None: cmd += f" X{X}"
        if Y is not None: cmd += f" Y{Y}"
        if Z is not None: cmd += f" Z{Z}"
        
        self.send_gcode(cmd)
        self._wait_movement()
        self.update_position()
        
    def move_absolute(self, X=None, Y=None, Z=None, speed=None):
        self.send_gcode('G90')
        cmd = "G0"
        if speed: cmd += f" F{speed}"
        if X is not None: cmd += f" X{X}"
        if Y is not None: cmd += f" Y{Y}"
        if Z is not None: cmd += f" Z{Z}"
        
        self.send_gcode(cmd)
        self._wait_movement()
        self.update_position()


class KlipperBackend(MotionBackend):
    def __init__(self):
        self.config = get_config()
        klipper_cfg = self.config.get("hardware.klipper", {})
        
        self.host = klipper_cfg.get("host", "127.0.0.1")
        self.port = klipper_cfg.get("port", 7125)
        self.timeout = klipper_cfg.get("timeout", 10.0)
        self.home_timeout = klipper_cfg.get("home_timeout", 90.0)
        self.movement_wait_timeout = klipper_cfg.get("movement_wait_timeout", 30.0)
        
        self.base_url = f"http://{self.host}:{self.port}"
        self.X, self.Y, self.Z = None, None, None
        
    def connect(self):
        try:
            resp = requests.get(f"{self.base_url}/printer/info", timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if data.get("result", {}).get("state") != "ready":
                raise ConnectionError(f"Klipper is not ready: {data.get('result', {}).get('state_message')}")
            self.update_position()
        except requests.RequestException as e:
            raise ConnectionError(f"Failed to connect to Moonraker API: {e}")
            
    def send_gcode(self, command: str, timeout: Optional[float] = None, ignore_errors: bool = False):
        timeout = timeout or self.timeout
        try:
            resp = requests.post(f"{self.base_url}/printer/gcode/script", 
                                 json={"script": command}, 
                                 timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            if not ignore_errors:
                raise RuntimeError(f"Failed to send G-code '{command}' via Moonraker: {e}")
                
    def home(self):
        self.send_gcode("G28", timeout=self.home_timeout)
        self.update_position()
        
    def update_position(self) -> Tuple[float, float, float]:
        try:
            resp = requests.get(f"{self.base_url}/printer/objects/query?toolhead", timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            pos = data.get("result", {}).get("status", {}).get("toolhead", {}).get("position", [])
            if len(pos) >= 3:
                self.X, self.Y, self.Z = pos[0], pos[1], pos[2]
        except requests.RequestException as e:
            logger.error(f"Failed to query position: {e}")
            
        return self.X, self.Y, self.Z
        
    def _wait_movement(self, timeout=None):
        timeout = timeout or self.movement_wait_timeout
        self.send_gcode("M400", timeout=timeout)
        
    def move_relative(self, X=None, Y=None, Z=None, speed=None):
        self.send_gcode('G91')
        cmd = "G0"
        if speed: cmd += f" F{speed}"
        if X is not None: cmd += f" X{X}"
        if Y is not None: cmd += f" Y{Y}"
        if Z is not None: cmd += f" Z{Z}"
        
        self.send_gcode(cmd)
        self._wait_movement()
        self.update_position()
        
    def move_absolute(self, X=None, Y=None, Z=None, speed=None):
        self.send_gcode('G90')
        cmd = "G0"
        if speed: cmd += f" F{speed}"
        if X is not None: cmd += f" X{X}"
        if Y is not None: cmd += f" Y{Y}"
        if Z is not None: cmd += f" Z{Z}"
        
        self.send_gcode(cmd)
        self._wait_movement()
        self.update_position()


class SimulationBackend(MotionBackend):
    def __init__(self):
        self.X, self.Y, self.Z = 0.0, 0.0, 0.0
        
    def connect(self):
        logger.info("Simulated motion backend connected.")
        
    def send_gcode(self, command: str, timeout: Optional[float] = None, ignore_errors: bool = False):
        time.sleep(0.1)
        
    def home(self):
        self.X, self.Y, self.Z = 0.0, 0.0, 0.0
        time.sleep(1.0)
        
    def update_position(self) -> Tuple[float, float, float]:
        return self.X, self.Y, self.Z
        
    def move_relative(self, X=None, Y=None, Z=None, speed=None):
        if X is not None: self.X += X
        if Y is not None: self.Y += Y
        if Z is not None: self.Z += Z
        time.sleep(0.5)
        
    def move_absolute(self, X=None, Y=None, Z=None, speed=None):
        if X is not None: self.X = X
        if Y is not None: self.Y = Y
        if Z is not None: self.Z = Z
        time.sleep(0.5)


class MotionController:
    def __init__(self, simulate: bool = False):
        self.config = get_config()
        self.simulate = simulate
        
        if self.simulate:
            self.backend = SimulationBackend()
        else:
            backend_type = self.config.get("hardware.motion_backend", "marlin").lower()
            if backend_type == "klipper":
                self.backend = KlipperBackend()
            else:
                self.backend = MarlinBackend()
                
        self.backend.connect()
        
    @property
    def X(self): return self.backend.X
    @property
    def Y(self): return self.backend.Y
    @property
    def Z(self): return self.backend.Z
    
    def home(self):
        self.backend.home()
        
    def update_position(self):
        return self.backend.update_position()
        
    def move_relative(self, X=None, Y=None, Z=None, speed=None):
        self.backend.move_relative(X=X, Y=Y, Z=Z, speed=speed)
        
    def move_absolute(self, X=None, Y=None, Z=None, speed=None):
        self.backend.move_absolute(X=X, Y=Y, Z=Z, speed=speed)
