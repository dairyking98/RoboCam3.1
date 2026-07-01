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
        self._lgpio: Optional[object] = None
        self._lgpio_handle: Optional[object] = None
        self._connected = False
        self._state = False

    def connect(self):
        if self.mode == self.MODE_RPI_GPIO:
            # Try lgpio first — required on Pi 5 (RPi.GPIO in the venv is too old)
            try:
                import lgpio
                h = lgpio.gpiochip_open(0)
                lgpio.gpio_claim_output(h, self.rpi_pin, 0)
                self._lgpio_handle = h
                self._lgpio = lgpio
                self._gpio = None
                self._connected = True
            except Exception:
                # Fall back to RPi.GPIO (Pi 4 and earlier)
                try:
                    import RPi.GPIO as GPIO
                    GPIO.setmode(GPIO.BCM)
                    GPIO.setwarnings(False)
                    GPIO.setup(self.rpi_pin, GPIO.OUT)
                    GPIO.output(self.rpi_pin, GPIO.LOW)
                    self._gpio = GPIO
                    self._lgpio_handle = None
                    self._lgpio = None
                    self._connected = True
                except Exception as exc:
                    raise RuntimeError(
                        f"GPIO init failed on pin {self.rpi_pin}. "
                        "Install lgpio (pip install lgpio) or RPi.GPIO."
                    ) from exc
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
        if getattr(self, "_lgpio_handle", None) is not None:
            try:
                self._lgpio.gpiochip_close(self._lgpio_handle)
            except Exception:
                pass
            self._lgpio_handle = None
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
        if self.mode == self.MODE_RPI_GPIO:
            if getattr(self, "_lgpio_handle", None) is not None:
                self._lgpio.gpio_write(self._lgpio_handle, self.rpi_pin, 1 if enabled else 0)
            elif self._gpio is not None:
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
