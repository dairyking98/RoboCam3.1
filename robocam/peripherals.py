import logging
from typing import Optional

from .config import get_config

logger = logging.getLogger(__name__)


class LaserController:
    """Controls a laser/stimulus output through Raspberry Pi GPIO or Klipper."""

    MODE_DISABLED = "disabled"
    MODE_RPI_GPIO = "rpi_gpio"
    MODE_KLIPPER = "klipper"

    def __init__(self, motion_controller=None):
        self.config = get_config()
        self.motion = motion_controller
        self.mode = self.config.get("hardware.laser.mode", self.MODE_DISABLED)
        self.rpi_pin = int(self.config.get("hardware.laser.rpi_pin", 21))
        self.klipper_on = self.config.get("hardware.laser.klipper_on_gcode", "SET_PIN PIN=laser VALUE=1")
        self.klipper_off = self.config.get("hardware.laser.klipper_off_gcode", "SET_PIN PIN=laser VALUE=0")
        self._gpio: Optional[object] = None
        self._connected = False
        self._state = False

    def connect(self):
        if self.mode == self.MODE_RPI_GPIO:
            try:
                import RPi.GPIO as GPIO
            except ImportError as exc:
                raise RuntimeError("RPi.GPIO is required for Raspberry Pi laser control.") from exc

            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self.rpi_pin, GPIO.OUT)
            GPIO.output(self.rpi_pin, GPIO.LOW)
            self._gpio = GPIO
            self._connected = True
        elif self.mode == self.MODE_KLIPPER:
            if not self.motion:
                raise RuntimeError("Klipper laser control requires a motion controller.")
            self._connected = True
            self.set_laser(False)
        else:
            self._connected = True
            logger.info("Laser controller disabled; laser timing will be logged only.")

    def disconnect(self):
        try:
            self.set_laser(False)
        except Exception:
            pass
        if self._gpio is not None:
            try:
                self._gpio.cleanup(self.rpi_pin)
            except Exception:
                pass
        self._connected = False

    def set_laser(self, enabled: bool):
        if not self._connected:
            self.connect()

        enabled = bool(enabled)
        if self.mode == self.MODE_RPI_GPIO and self._gpio is not None:
            self._gpio.output(self.rpi_pin, self._gpio.HIGH if enabled else self._gpio.LOW)
        elif self.mode == self.MODE_KLIPPER:
            command = self.klipper_on if enabled else self.klipper_off
            backend = getattr(self.motion, "backend", None)
            if backend and hasattr(backend, "send_gcode"):
                backend.send_gcode(command)
            else:
                raise RuntimeError("Motion backend does not support raw G-code commands.")

        self._state = enabled

    def get_laser_state(self) -> bool:
        return self._state
