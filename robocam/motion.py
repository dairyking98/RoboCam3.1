import serial
import serial.tools.list_ports
import time
import re
import logging
from typing import Optional, Tuple
from .config import get_config

logger = logging.getLogger(__name__)

class MotionController:
    def __init__(self, simulate: bool = False):
        self.config = get_config()
        printer_cfg = self.config.get("hardware.printer", {})
        
        self.baud_rate = printer_cfg.get("baudrate", 115200)
        self.timeout = printer_cfg.get("timeout", 10.0)
        self.home_timeout = printer_cfg.get("home_timeout", 90.0)
        self.movement_wait_timeout = printer_cfg.get("movement_wait_timeout", 30.0)
        self.command_delay = printer_cfg.get("command_delay", 0.1)
        self.max_retries = printer_cfg.get("max_retries", 5)
        
        self.simulate = simulate
        self.X, self.Y, self.Z = None, None, None
        self.serial_conn = None
        self._m400_supported = False
        
        if self.simulate:
            self.X, self.Y, self.Z = 0.0, 0.0, 0.0
            logger.info("MotionController running in SIMULATION MODE")
        else:
            self._connect()
            
    def _connect(self):
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
                
                # Test M114
                self.update_position()
                
                # Test M400
                try:
                    self.send_gcode("M400", timeout=5.0)
                    self._m400_supported = True
                except:
                    self._m400_supported = False
                    
                return
            except serial.SerialException as e:
                retries += 1
                time.sleep(2)
        raise ConnectionError("Failed to connect.")
        
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
        if self.simulate:
            time.sleep(self.command_delay)
            return
            
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
        if self.simulate:
            self.X, self.Y, self.Z = 0.0, 0.0, 0.0
            return
            
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
        if self.simulate:
            return self.X, self.Y, self.Z
            
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
        if self.simulate:
            if X is not None: self.X += X
            if Y is not None: self.Y += Y
            if Z is not None: self.Z += Z
            return
            
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
        if self.simulate:
            if X is not None: self.X = X
            if Y is not None: self.Y = Y
            if Z is not None: self.Z = Z
            return
            
        self.send_gcode('G90')
        cmd = "G0"
        if speed: cmd += f" F{speed}"
        if X is not None: cmd += f" X{X}"
        if Y is not None: cmd += f" Y{Y}"
        if Z is not None: cmd += f" Z{Z}"
        
        self.send_gcode(cmd)
        self._wait_movement()
        self.update_position()
