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
    def disconnect(self): raise NotImplementedError
    def send_gcode(self, command: str, timeout: Optional[float] = None, ignore_errors: bool = False): raise NotImplementedError
    def home(self): raise NotImplementedError
    def update_position(self) -> Tuple[float, float, float]: raise NotImplementedError
    def move_relative(self, X=None, Y=None, Z=None, speed=None): raise NotImplementedError
    def move_absolute(self, X=None, Y=None, Z=None, speed=None): raise NotImplementedError
    @property
    def is_connected(self) -> bool: raise NotImplementedError

class MarlinBackend(MotionBackend):
    """
    Marlin backend ported directly from RoboCam-Suite 2.0 GCodeSerialMotionController.
    Implements robust M400 checking, command delay, and in_waiting sleep loop.
    """
    def __init__(self):
        self.config = get_config()
        printer_cfg = self.config.get("hardware.printer", {})
        
        self.baud_rate = printer_cfg.get("baudrate", 115200)
        self.timeout = printer_cfg.get("timeout", 10.0)
        self.home_timeout = printer_cfg.get("home_timeout", 90.0)
        self.movement_wait_timeout = printer_cfg.get("movement_wait_timeout", 30.0)
        self.command_delay = printer_cfg.get("command_delay", 0.05)
        
        self.X, self.Y, self.Z = 0.0, 0.0, 0.0
        self.serial_conn = None
        self._m400_supported = None
        
    @property
    def is_connected(self) -> bool:
        return self.serial_conn is not None and self.serial_conn.is_open

    def connect(self):
        if self.is_connected:
            return
            
        port = self._find_port()
        if not port:
            raise ConnectionError("No serial port found for Marlin backend.")
            
        try:
            self.serial_conn = serial.Serial(port, self.baud_rate, timeout=self.timeout)
            time.sleep(2)
            self.serial_conn.reset_input_buffer()
            logger.info(f"[MotionCtrl] Connected to printer on {port}.")
        except serial.SerialException as e:
            raise ConnectionError(f"Failed to connect to motion controller on {port}: {e}")
            
        try:
            self.update_position()
        except Exception as e:
            logger.warning(f"[MotionCtrl] Could not sync position on connect: {e}")

    def disconnect(self):
        if self.is_connected:
            self.serial_conn.close()
            self.serial_conn = None
            self._m400_supported = None
            logger.info("[MotionCtrl] Disconnected from printer.")

    def _find_port(self):
        ports = serial.tools.list_ports.comports()
        for p in ports:
            desc = (p.description or "").upper()
            if any(kw in desc for kw in ("USB", "CH340", "CH341", "ARDUINO", "MARLIN", "FTDI")):
                return p.device
        return None

    def send_gcode(self, command: str, timeout: Optional[float] = None, ignore_errors: bool = False) -> str:
        if not self.is_connected:
            raise ConnectionError("Motion controller is not connected.")
            
        timeout = timeout or self.timeout
        
        logger.info(f">>> {command}")
        self.serial_conn.write((command + '\n').encode('utf-8'))
        self.serial_conn.flush()
        time.sleep(self.command_delay)
        
        start_time = time.time()
        lines = []
        
        while True:
            if time.time() - start_time > timeout:
                raise TimeoutError(f"Timeout waiting for 'ok' from printer after {command!r}.")
                
            if self.serial_conn.in_waiting > 0:
                raw = self.serial_conn.readline()
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    logger.debug(f"Printer response: {line}")
                    lines.append(line)
                    if line.lower().startswith("ok") or "ok" in line.lower():
                        break
                    if line.lower().startswith("error") and not ignore_errors:
                        raise RuntimeError(f"Printer error: {line}")
            else:
                time.sleep(0.01)
                
        return "\n".join(lines)

    def home(self):
        self.send_gcode('G28', timeout=self.home_timeout)
        self._wait_movement()
        try:
            self.update_position()
        except Exception as e:
            logger.warning(f"[MotionCtrl] Could not sync position after home: {e}")

    def update_position(self) -> Tuple[float, float, float]:
        response = self.send_gcode("M114", timeout=5.0)
        match_x = re.search(r"X:\s*(-?[\d.]+)", response)
        match_y = re.search(r"Y:\s*(-?[\d.]+)", response)
        match_z = re.search(r"Z:\s*(-?[\d.]+)", response)
        
        if match_x and match_y and match_z:
            self.X = float(match_x.group(1))
            self.Y = float(match_y.group(1))
            self.Z = float(match_z.group(1))
            
        return self.X, self.Y, self.Z

    def _wait_movement(self, timeout=None):
        timeout = timeout or self.movement_wait_timeout
        
        if self._m400_supported is None:
            logger.debug("Testing M400 support on first move...")
            try:
                self.send_gcode("M400", timeout=5.0)
                self._m400_supported = True
                return
            except Exception as e:
                self._m400_supported = False
                logger.warning(f"M400 not supported ({e}). Switching to delay-based fallback.")
                
        if self._m400_supported:
            try:
                self.send_gcode("M400", timeout=timeout)
                return
            except Exception as e:
                logger.warning(f"M400 failed during move ({e}). Marking M400 as unsupported.")
                self._m400_supported = False
                
        fallback_delay = min(timeout, 2.0)
        time.sleep(fallback_delay)

    def move_relative(self, X=None, Y=None, Z=None, speed=None):
        self.send_gcode('G91')
        self._move(X, Y, Z, speed)
        
    def move_absolute(self, X=None, Y=None, Z=None, speed=None):
        self.send_gcode('G90')
        self._move(X, Y, Z, speed)
        
    def _move(self, X, Y, Z, speed):
        cmd = "G0"
        if X is not None: cmd += f" X{X:.4f}"
        if Y is not None: cmd += f" Y{Y:.4f}"
        if Z is not None: cmd += f" Z{Z:.4f}"
        if speed is not None: cmd += f" F{speed:.1f}"
        
        self.send_gcode(cmd)
        self._wait_movement()
        self.update_position()


class KlipperBackend(MotionBackend):
    """
    Klipper backend using Moonraker HTTP API.
    """
    def __init__(self):
        self.config = get_config()
        klipper_cfg = self.config.get("hardware.klipper", {})
        
        self.host = klipper_cfg.get("host", "127.0.0.1")
        self.port = klipper_cfg.get("port", 7125)
        self.timeout = klipper_cfg.get("timeout", 10.0)
        self.home_timeout = klipper_cfg.get("home_timeout", 90.0)
        self.movement_wait_timeout = klipper_cfg.get("movement_wait_timeout", 30.0)
        
        self.base_url = f"http://{self.host}:{self.port}"
        self.X, self.Y, self.Z = 0.0, 0.0, 0.0
        self._connected = False
        
    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self):
        try:
            resp = requests.get(f"{self.base_url}/printer/info", timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if data.get("result", {}).get("state") != "ready":
                raise ConnectionError(f"Klipper is not ready: {data.get('result', {}).get('state_message')}")
            self._connected = True
            self.update_position()
        except requests.RequestException as e:
            self._connected = False
            raise ConnectionError(f"Failed to connect to Moonraker API: {e}")

    def disconnect(self):
        self._connected = False

    def send_gcode(self, command: str, timeout: Optional[float] = None, ignore_errors: bool = False):
        if not self.is_connected:
            raise ConnectionError("Not connected")
        timeout = timeout or self.timeout
        try:
            logger.info(f">>> {command}")
            resp = requests.post(f"{self.base_url}/printer/gcode/script", 
                                 json={"script": command}, 
                                 timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            if not ignore_errors:
                raise RuntimeError(f"Failed to send G-code '{command}' via Moonraker: {e}")

    def home(self):
        self.send_gcode("G28", timeout=self.home_timeout)
        self._wait_movement()
        self.update_position()

    def update_position(self) -> Tuple[float, float, float]:
        if not self.is_connected:
            return self.X, self.Y, self.Z
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
        self._move(X, Y, Z, speed)

    def move_absolute(self, X=None, Y=None, Z=None, speed=None):
        self.send_gcode('G90')
        self._move(X, Y, Z, speed)
        
    def _move(self, X, Y, Z, speed):
        cmd = "G0"
        if X is not None: cmd += f" X{X:.4f}"
        if Y is not None: cmd += f" Y{Y:.4f}"
        if Z is not None: cmd += f" Z{Z:.4f}"
        if speed is not None: cmd += f" F{speed:.1f}"
        
        self.send_gcode(cmd)
        self._wait_movement()
        self.update_position()


class SimulationBackend(MotionBackend):
    def __init__(self):
        self.X, self.Y, self.Z = 0.0, 0.0, 0.0
        self._connected = False
        
    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self):
        self._connected = True
        logger.info("Simulated motion backend connected.")
        
    def disconnect(self):
        self._connected = False

    def send_gcode(self, command: str, timeout: Optional[float] = None, ignore_errors: bool = False):
        time.sleep(0.01)
        return "ok"

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
        self.backend = None
        self._homed = False
        self.connect()

    def connect(self):
        if self.backend and self.backend.is_connected:
            self.backend.disconnect()

        if self.simulate:
            self.backend = SimulationBackend()
        else:
            backend_type = self.config.get("hardware.motion_backend", "marlin").lower()
            if backend_type == "klipper":
                self.backend = KlipperBackend()
            else:
                self.backend = MarlinBackend()

        self.backend.connect()

        # Infer homed state from position reported at connect.
        # Marlin's power-on default is (0,0,0) before any G28 — treat that as
        # "not homed". Also treat X==Y (e.g. 220,220) as not homed: that is the
        # firmware's default park position, not a real post-home coordinate.
        # A position where X != Y means the stage was actually moved after a
        # previous home, so it is safe to continue without re-homing.
        if self.simulate:
            self._homed = True
        else:
            x, y, z = self.backend.X, self.backend.Y, self.backend.Z
            not_homed = (x == 0.0 and y == 0.0 and z == 0.0) or (x == y)
            self._homed = not not_homed

    def disconnect(self):
        if self.backend:
            self.backend.disconnect()

    @property
    def is_connected(self) -> bool:
        return self.backend is not None and self.backend.is_connected

    @property
    def is_homed(self) -> bool:
        return self._homed

    @property
    def X(self): return self.backend.X
    @property
    def Y(self): return self.backend.Y
    @property
    def Z(self): return self.backend.Z

    def home(self):
        self.backend.home()
        self._homed = True
        
    def update_position(self):
        return self.backend.update_position()
        
    def move_relative(self, X=None, Y=None, Z=None, speed=None):
        self.backend.move_relative(X=X, Y=Y, Z=Z, speed=speed)
        
    def move_absolute(self, X=None, Y=None, Z=None, speed=None):
        self.backend.move_absolute(X=X, Y=Y, Z=Z, speed=speed)

    def send_raw(self, command: str):
        """Send a raw G-code command directly to the backend (e.g. M18, M84)."""
        self.backend.send_gcode(command)
