"""
Setup Panel — hardware configuration.

Groups
------
Camera       : device detection, resolution
3-D Printer  : backend (marlin/klipper), serial port, baud, Klipper host
Laser / GPIO : mode (disabled/rpi_gpio/klipper), RPi pin, Klipper G-codes
Status       : live connection indicators
Connection   : Connect All / Disconnect All
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import serial.tools.list_ports

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox,
    QSpinBox, QLineEdit, QScrollArea, QCheckBox, QFrame,
)

from robocam.config import get_config
import robocam.hw_state as hw_state

PRINTER_BAUDRATES = [115200, 250000, 57600, 38400, 19200, 9600]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UDEV_RULES_SRC = _PROJECT_ROOT / "PlayerOne_Camera_SDK_Linux_V3.10.0" / "99-player_one_astronomy.rules"
_UDEV_RULES_DEST = Path("/etc/udev/rules.d/99-player_one_astronomy.rules")


# ---------------------------------------------------------------------------
# Background home thread
# ---------------------------------------------------------------------------

class _HomeThread(QThread):
    finished = Signal(bool, str)  # success, message

    def __init__(self, motion, parent=None):
        super().__init__(parent)
        self._motion = motion

    def run(self):
        try:
            self._motion.home()
            self.finished.emit(True, "Homed successfully.")
        except Exception as e:
            self.finished.emit(False, str(e))


# ---------------------------------------------------------------------------
# Udev rule installer (background thread)
# ---------------------------------------------------------------------------

class _UdevInstaller(QThread):
    """Installs the PlayerOne udev rule and reloads udev — no replug needed."""

    finished = Signal(bool, str)  # success, message

    def run(self):
        if not _UDEV_RULES_SRC.exists():
            self.finished.emit(False, f"Rules file not found:\n{_UDEV_RULES_SRC}")
            return
        try:
            subprocess.run(
                ["sudo", "-n", "cp", str(_UDEV_RULES_SRC), str(_UDEV_RULES_DEST)],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["sudo", "-n", "udevadm", "control", "--reload-rules"],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["sudo", "-n", "udevadm", "trigger"],
                check=True, capture_output=True,
            )
            self.finished.emit(True, "USB rules installed. Rescanning cameras…")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
            self.finished.emit(
                False,
                f"Could not install automatically (sudo returned {e.returncode}).\n"
                f"Run manually:\n"
                f"  sudo cp {_UDEV_RULES_SRC} {_UDEV_RULES_DEST}\n"
                f"  sudo udevadm control --reload-rules && sudo udevadm trigger\n"
                + (f"\n{stderr}" if stderr else ""),
            )


# ---------------------------------------------------------------------------
# Camera enumerator (background thread)
# ---------------------------------------------------------------------------

class _CameraEnumerator(QThread):
    """Probes available camera devices; emits list of (label, backend, index)."""

    cameras_found = Signal(list)

    def run(self):
        devices = []
        try:
            os_name = platform.system()

            # Raspberry Pi / picamera2 — only add if libcamera actually sees a sensor
            if os_name == "Linux":
                try:
                    from robocam.camera import PICAM2_AVAILABLE
                    if PICAM2_AVAILABLE:
                        from picamera2 import Picamera2
                        found = Picamera2.global_camera_info()
                        if found:
                            for i, info in enumerate(found):
                                model = info.get("Model", "Pi Camera")
                                devices.append((f"Raspberry Pi Camera — {model} (index {i})", "picamera2", i))
                        else:
                            devices.append((
                                "Raspberry Pi Camera — not detected (check CSI cable/adapter)",
                                "picamera2", 0,
                            ))
                except Exception:
                    pass

            # Player One cameras
            try:
                from robocam.camera import get_playerone_camera_count, get_playerone_sdk_python_path, _ensure_pypoa_patched_for_linux
                import sys
                count = get_playerone_camera_count()
                sdk_path = get_playerone_sdk_python_path()
                if sdk_path:
                    _ensure_pypoa_patched_for_linux(sdk_path)
                    prev = list(sys.path)
                    if sdk_path not in sys.path:
                        sys.path.insert(0, sdk_path)
                    try:
                        import pyPOACamera as poa
                        for i in range(count):
                            err, props = poa.GetCameraProperties(i)
                            if err == poa.POAErrors.POA_OK:
                                model = props.cameraModelName.decode(errors="replace").strip()
                                devices.append((f"PlayerOne — {model} (index {i})", "playerone", i, props.maxWidth, props.maxHeight))
                            else:
                                # Detected but can't open — usually a USB permission problem.
                                devices.append((f"PlayerOne #{i} — USB permission denied (install udev rules)", "playerone", i))
                    finally:
                        sys.path[:] = prev
            except Exception:
                pass

            # OpenCV webcams (indices 0–3)
            try:
                import cv2
                for idx in range(4):
                    cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
                    if cap.isOpened():
                        devices.append((f"USB / Webcam (index {idx})", "cv2", idx))
                    cap.release()
            except Exception:
                pass

        except Exception:
            pass

        if not devices:
            devices.append(("No cameras detected", "cv2", 0))

        self.cameras_found.emit(devices)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_label(text: str = "Unknown") -> QLabel:
    lbl = QLabel(text)
    lbl.setMinimumWidth(100)
    return lbl


def _set_status(lbl: QLabel, connected: bool, disabled: bool = False):
    if disabled:
        lbl.setText("Disabled")
        lbl.setStyleSheet("color: gray; font-weight: bold;")
    elif connected:
        lbl.setText("Connected")
        lbl.setStyleSheet("color: green; font-weight: bold;")
    else:
        lbl.setText("Disconnected")
        lbl.setStyleSheet("color: red; font-weight: bold;")


# ---------------------------------------------------------------------------
# SetupPanel
# ---------------------------------------------------------------------------

class SetupPanel(QWidget):
    camera_connected = Signal()
    motion_connected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = get_config()
        self._camera_devices: list[tuple] = []  # (label, backend, dev_idx[, max_w, max_h])

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        scroll.setWidget(inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        root = QVBoxLayout(inner)
        root.setSpacing(10)
        root.setContentsMargins(10, 10, 10, 10)

        root.addWidget(self._build_camera_group())
        root.addWidget(self._build_printer_group())
        root.addWidget(self._build_laser_group())
        root.addWidget(self._build_status_group())
        root.addWidget(self._build_connection_group())
        root.addStretch()

        self._load_from_config()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_status)
        self._poll_timer.start(2000)
        self._refresh_status()

        QTimer.singleShot(300, self._enumerate_cameras)

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _build_camera_group(self) -> QGroupBox:
        grp = QGroupBox("Camera")
        layout = QGridLayout(grp)

        layout.addWidget(QLabel("Detected device:"), 0, 0)
        self.cam_device_combo = QComboBox()
        self.cam_device_combo.setMinimumWidth(300)
        layout.addWidget(self.cam_device_combo, 0, 1)

        self.cam_scan_btn = QPushButton("Scan for Cameras")
        self.cam_scan_btn.clicked.connect(self._enumerate_cameras)
        layout.addWidget(self.cam_scan_btn, 0, 2)

        self.cam_scan_status = QLabel("Scanning…")
        self.cam_scan_status.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self.cam_scan_status, 1, 0, 1, 3)

        layout.addWidget(QLabel("Resolution:"), 2, 0)
        self.cam_res_combo = QComboBox()
        layout.addWidget(self.cam_res_combo, 2, 1, 1, 2)

        self.cam_apply_btn = QPushButton("Apply && Reconnect Camera")
        self.cam_apply_btn.clicked.connect(self._apply_camera)
        layout.addWidget(self.cam_apply_btn, 3, 0, 1, 3)

        # Udev permission warning — hidden until a USB-denied device is detected
        self._udev_warning = QFrame()
        self._udev_warning.setFrameShape(QFrame.Shape.StyledPanel)
        self._udev_warning.setStyleSheet(
            "QFrame { background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; }"
        )
        _uw_layout = QHBoxLayout(self._udev_warning)
        _uw_layout.setContentsMargins(8, 6, 8, 6)
        _uw_layout.setSpacing(8)
        self._udev_warn_lbl = QLabel()
        self._udev_warn_lbl.setWordWrap(True)
        self._udev_warn_lbl.setStyleSheet("color: #856404;")
        _uw_layout.addWidget(self._udev_warn_lbl, stretch=1)
        self._udev_install_btn = QPushButton("Install USB Rules")
        self._udev_install_btn.clicked.connect(self._install_udev_rules)
        _uw_layout.addWidget(self._udev_install_btn)
        self._udev_warning.hide()
        layout.addWidget(self._udev_warning, 4, 0, 1, 3)

        self.cam_device_combo.currentIndexChanged.connect(
            lambda _: self._populate_resolution_combo()
        )

        return grp

    def _build_printer_group(self) -> QGroupBox:
        grp = QGroupBox("3-D Printer (Motion Controller)")
        layout = QGridLayout(grp)

        layout.addWidget(QLabel("Backend:"), 0, 0)
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["marlin", "klipper"])
        self.backend_combo.currentTextChanged.connect(self._on_backend_changed)
        layout.addWidget(self.backend_combo, 0, 1)

        self._serial_port_label = QLabel("Serial port:")
        layout.addWidget(self._serial_port_label, 1, 0)
        self.printer_port_combo = QComboBox()
        self.printer_port_combo.setEditable(True)
        layout.addWidget(self.printer_port_combo, 1, 1)
        self._serial_refresh_btn = QPushButton("↺")
        self._serial_refresh_btn.setFixedWidth(30)
        self._serial_refresh_btn.clicked.connect(self._refresh_printer_ports)
        layout.addWidget(self._serial_refresh_btn, 1, 2)

        self._baud_label = QLabel("Baud rate:")
        layout.addWidget(self._baud_label, 2, 0)
        self.printer_baud_combo = QComboBox()
        for b in PRINTER_BAUDRATES:
            self.printer_baud_combo.addItem(str(b), b)
        layout.addWidget(self.printer_baud_combo, 2, 1)

        self._klipper_host_label = QLabel("Klipper host:")
        layout.addWidget(self._klipper_host_label, 3, 0)
        self.klipper_host_edit = QLineEdit()
        self.klipper_host_edit.setPlaceholderText("127.0.0.1")
        layout.addWidget(self.klipper_host_edit, 3, 1)

        self._klipper_port_label = QLabel("Klipper port:")
        layout.addWidget(self._klipper_port_label, 4, 0)
        self.klipper_port_spin = QSpinBox()
        self.klipper_port_spin.setRange(1, 65535)
        self.klipper_port_spin.setValue(7125)
        layout.addWidget(self.klipper_port_spin, 4, 1)

        self.printer_apply_btn = QPushButton("Apply && Reconnect Printer")
        self.printer_apply_btn.clicked.connect(self._apply_printer)
        layout.addWidget(self.printer_apply_btn, 5, 0, 1, 3)

        self._on_backend_changed(self.backend_combo.currentText())
        return grp

    def _build_laser_group(self) -> QGroupBox:
        grp = QGroupBox("Laser / GPIO")
        layout = QGridLayout(grp)

        layout.addWidget(QLabel("Laser mode:"), 0, 0)
        self.laser_mode_combo = QComboBox()
        self.laser_mode_combo.addItems(["disabled", "rpi_gpio", "klipper"])
        self.laser_mode_combo.currentTextChanged.connect(self._on_laser_mode_changed)
        layout.addWidget(self.laser_mode_combo, 0, 1, 1, 2)

        layout.addWidget(QLabel("RPi GPIO pin (BCM):"), 1, 0)
        self.laser_pin_spin = QSpinBox()
        self.laser_pin_spin.setRange(0, 40)
        self.laser_pin_spin.setValue(21)
        layout.addWidget(self.laser_pin_spin, 1, 1)

        layout.addWidget(QLabel("Klipper ON G-code:"), 2, 0)
        self.laser_on_edit = QLineEdit("SET_PIN PIN=laser VALUE=1")
        layout.addWidget(self.laser_on_edit, 2, 1, 1, 2)

        layout.addWidget(QLabel("Klipper OFF G-code:"), 3, 0)
        self.laser_off_edit = QLineEdit("SET_PIN PIN=laser VALUE=0")
        layout.addWidget(self.laser_off_edit, 3, 1, 1, 2)

        self.laser_apply_btn = QPushButton("Apply Laser Settings")
        self.laser_apply_btn.clicked.connect(self._apply_laser)
        layout.addWidget(self.laser_apply_btn, 4, 0, 1, 3)

        self._on_laser_mode_changed(self.laser_mode_combo.currentText())
        return grp

    def _build_status_group(self) -> QGroupBox:
        grp = QGroupBox("Hardware Status")
        layout = QGridLayout(grp)

        layout.addWidget(QLabel("3-D Printer:"), 0, 0)
        self.printer_status_lbl = _status_label()
        layout.addWidget(self.printer_status_lbl, 0, 1)

        layout.addWidget(QLabel("Homing:"), 1, 0)
        self.homed_status_lbl = _status_label("Unknown")
        layout.addWidget(self.homed_status_lbl, 1, 1)

        self.home_now_btn = QPushButton("Home All Axes")
        self.home_now_btn.setEnabled(False)
        self.home_now_btn.clicked.connect(self._home_now)
        layout.addWidget(self.home_now_btn, 1, 2)

        layout.addWidget(QLabel("Camera:"), 2, 0)
        self.camera_status_lbl = _status_label()
        layout.addWidget(self.camera_status_lbl, 2, 1)

        # Warning banner — shown when printer is connected but not homed
        self._home_warning = QFrame()
        self._home_warning.setFrameShape(QFrame.Shape.StyledPanel)
        self._home_warning.setStyleSheet(
            "QFrame { background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; padding: 4px; }"
        )
        warn_layout = QHBoxLayout(self._home_warning)
        warn_layout.setContentsMargins(6, 4, 6, 4)
        warn_lbl = QLabel("Printer position unknown — home required before running experiments.")
        warn_lbl.setStyleSheet("color: #856404; font-weight: bold;")
        warn_lbl.setWordWrap(True)
        warn_layout.addWidget(warn_lbl)
        self._home_warning.hide()
        layout.addWidget(self._home_warning, 3, 0, 1, 3)

        # Error banner — shown when a Home All Axes attempt fails
        self._home_error_lbl = QLabel()
        self._home_error_lbl.setStyleSheet("color: #b00020; font-weight: bold;")
        self._home_error_lbl.setWordWrap(True)
        self._home_error_lbl.hide()
        layout.addWidget(self._home_error_lbl, 4, 0, 1, 3)

        return grp

    def _build_connection_group(self) -> QGroupBox:
        grp = QGroupBox("Connection")
        layout = QHBoxLayout(grp)

        connect_btn = QPushButton("Connect All")
        connect_btn.clicked.connect(self._connect_all)
        layout.addWidget(connect_btn)

        disconnect_btn = QPushButton("Disconnect All")
        disconnect_btn.clicked.connect(self._disconnect_all)
        layout.addWidget(disconnect_btn)

        return grp

    # ------------------------------------------------------------------
    # Camera enumeration
    # ------------------------------------------------------------------

    def _enumerate_cameras(self):
        self.cam_scan_btn.setEnabled(False)
        self.cam_scan_status.setText("Scanning for cameras…")
        self._enumerator = _CameraEnumerator()
        self._enumerator.cameras_found.connect(self._on_cameras_found)
        self._enumerator.start()

    def _on_cameras_found(self, devices: list):
        self._camera_devices = devices
        current = self.cam_device_combo.currentText()
        self.cam_device_combo.clear()
        for dev in devices:
            self.cam_device_combo.addItem(dev[0])
        idx = self.cam_device_combo.findText(current)
        if idx >= 0:
            self.cam_device_combo.setCurrentIndex(idx)

        real_count = sum(1 for dev in devices if "No cameras" not in dev[0])
        self.cam_scan_status.setText(
            f"{real_count} device(s) found." if real_count else "No cameras detected."
        )
        self.cam_scan_btn.setEnabled(True)
        self._populate_resolution_combo()

        # Show udev warning if any PlayerOne camera was detected but access was denied
        perm_denied = any("permission denied" in dev[0].lower() for dev in devices)
        if perm_denied:
            already_installed = _UDEV_RULES_DEST.exists()
            if already_installed:
                self._udev_warn_lbl.setText(
                    "<b>PlayerOne camera found — USB access still blocked.</b><br>"
                    "The udev rule is installed but hasn't taken effect yet. "
                    "Try clicking Install USB Rules again, or unplug and replug the camera."
                )
            else:
                self._udev_warn_lbl.setText(
                    "<b>PlayerOne camera detected but USB access is denied.</b><br>"
                    "A one-time permission rule must be installed. "
                    "Click <b>Install USB Rules</b> — no cable disconnect needed."
                )
            self._udev_install_btn.setEnabled(True)
            self._udev_install_btn.setText("Install USB Rules")
            self._udev_warning.show()
        else:
            self._udev_warning.hide()

    def _populate_resolution_combo(self):
        """Fill resolution list based on selected camera."""
        idx = self.cam_device_combo.currentIndex()
        if idx < 0 or idx >= len(self._camera_devices):
            return
        dev = self._camera_devices[idx]
        _label, backend, _dev_idx = dev[0], dev[1], dev[2]
        max_w = dev[3] if len(dev) > 3 else None
        max_h = dev[4] if len(dev) > 4 else None

        standards = [
            (640, 480), (800, 600), (1024, 768),
            (1280, 720), (1280, 960), (1600, 1200), (1920, 1080),
        ]
        resolutions = [r for r in standards if (max_w is None or r[0] <= max_w) and (max_h is None or r[1] <= max_h)]
        if max_w and max_h:
            native = (max_w, max_h)
            if native not in resolutions:
                resolutions.append(native)

        self.cam_res_combo.clear()
        for w, h in resolutions:
            self.cam_res_combo.addItem(f"{w} x {h}", (w, h))

        # Try to match config resolution
        cfg_res = self._cfg.get("hardware.camera.resolution", [1920, 1080])
        target = tuple(cfg_res)
        for i in range(self.cam_res_combo.count()):
            if self.cam_res_combo.itemData(i) == target:
                self.cam_res_combo.setCurrentIndex(i)
                return
        self.cam_res_combo.setCurrentIndex(self.cam_res_combo.count() - 1)

    # ------------------------------------------------------------------
    # Udev rule installer
    # ------------------------------------------------------------------

    def _install_udev_rules(self):
        self._udev_install_btn.setEnabled(False)
        self._udev_install_btn.setText("Installing…")
        self._udev_warn_lbl.setText(
            "<b>Installing USB permission rule…</b><br>"
            "This takes a moment — camera list will refresh automatically."
        )
        self._udev_thread = _UdevInstaller(self)
        self._udev_thread.finished.connect(self._on_udev_install_finished)
        self._udev_thread.start()

    def _on_udev_install_finished(self, success: bool, message: str):
        if success:
            self._udev_warn_lbl.setText(
                f"<span style='color:green;'><b>Done.</b></span> {message}"
            )
            self._udev_install_btn.hide()
            QTimer.singleShot(800, self._enumerate_cameras)
        else:
            self._udev_warn_lbl.setText(
                f"<b>Auto-install failed.</b> Run these commands in a terminal:<br>"
                f"<code>sudo cp {_UDEV_RULES_SRC} {_UDEV_RULES_DEST}</code><br>"
                f"<code>sudo udevadm control --reload-rules &amp;&amp; sudo udevadm trigger</code>"
            )
            self._udev_install_btn.setEnabled(True)
            self._udev_install_btn.setText("Retry")

    # ------------------------------------------------------------------
    # Port helpers
    # ------------------------------------------------------------------

    def _available_ports(self) -> list[str]:
        return ["auto"] + [p.device for p in serial.tools.list_ports.comports()]

    def _refresh_printer_ports(self):
        current = self.printer_port_combo.currentText()
        self.printer_port_combo.clear()
        self.printer_port_combo.addItems(self._available_ports())
        idx = self.printer_port_combo.findText(current)
        if idx >= 0:
            self.printer_port_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # Visibility toggles
    # ------------------------------------------------------------------

    def _on_backend_changed(self, backend: str):
        is_klipper = backend == "klipper"
        for w in (self._klipper_host_label, self.klipper_host_edit,
                  self._klipper_port_label, self.klipper_port_spin):
            w.setVisible(is_klipper)
        for w in (self._serial_port_label, self.printer_port_combo,
                  self._serial_refresh_btn, self._baud_label, self.printer_baud_combo):
            w.setVisible(not is_klipper)

    def _on_laser_mode_changed(self, mode: str):
        self.laser_pin_spin.setEnabled(mode == "rpi_gpio")
        self.laser_on_edit.setEnabled(mode == "klipper")
        self.laser_off_edit.setEnabled(mode == "klipper")

    # ------------------------------------------------------------------
    # Apply handlers
    # ------------------------------------------------------------------

    def _apply_camera(self):
        idx = self.cam_device_combo.currentIndex()
        if idx < 0 or idx >= len(self._camera_devices):
            return
        dev = self._camera_devices[idx]
        _label, backend, dev_idx = dev[0], dev[1], dev[2]
        res_data = self.cam_res_combo.currentData()
        w, h = res_data if res_data else (1920, 1080)

        # Disconnect existing camera
        cam = hw_state.get_camera()
        if cam is not None:
            try:
                cam.stop()
            except Exception:
                pass
        hw_state.set_camera(None)

        # Update config
        self._cfg.set("hardware.camera.resolution", [w, h])

        # Reconnect after short delay to let OS release the device
        self._pending_cam_backend = backend
        self._pending_cam_idx = dev_idx
        self._pending_cam_res = (w, h)
        QTimer.singleShot(800, self._reconnect_camera)

    def _reconnect_camera(self):
        try:
            from robocam.camera import Camera
            cam = Camera(
                resolution=self._pending_cam_res,
                simulate=False,
                backend=self._pending_cam_backend,
                device_index=self._pending_cam_idx,
            )
            hw_state.set_camera(cam)
            hw_state.rebuild_runner()
            self.camera_connected.emit()
        except Exception as e:
            print(f"[Setup] Camera reconnect failed: {e}")
        self._refresh_status()

    def _apply_printer(self):
        backend = self.backend_combo.currentText()
        self._cfg.set("hardware.motion_backend", backend)

        if backend == "marlin":
            port = self.printer_port_combo.currentText() or "auto"
            baud = int(self.printer_baud_combo.currentData() or 115200)
            self._cfg.set("hardware.printer.baudrate", baud)
        else:
            host = self.klipper_host_edit.text() or "127.0.0.1"
            port_num = self.klipper_port_spin.value()
            self._cfg.set("hardware.klipper.host", host)
            self._cfg.set("hardware.klipper.port", port_num)

        # Disconnect existing motion controller
        motion = hw_state.get_motion()
        if motion is not None:
            try:
                motion.disconnect()
            except Exception:
                pass
        hw_state.set_motion(None)

        try:
            from robocam.motion import MotionController
            mc = MotionController(simulate=False)
            hw_state.set_motion(mc)
            self.motion_connected.emit()
        except Exception as e:
            print(f"[Setup] Printer reconnect failed: {e}")
        self._refresh_status()

    def _apply_laser(self):
        mode = self.laser_mode_combo.currentText()
        self._cfg.set("hardware.laser.mode", mode)
        self._cfg.set("hardware.laser.rpi_pin", self.laser_pin_spin.value())
        self._cfg.set("hardware.laser.klipper_on_gcode", self.laser_on_edit.text())
        self._cfg.set("hardware.laser.klipper_off_gcode", self.laser_off_edit.text())

    def _home_now(self):
        motion = hw_state.get_motion()
        if motion is None or not motion.is_connected:
            return
        self.home_now_btn.setEnabled(False)
        self.home_now_btn.setText("Homing…")
        self._home_error_lbl.hide()
        self._home_thread = _HomeThread(motion, self)
        self._home_thread.finished.connect(self._on_home_finished)
        self._home_thread.start()

    def _on_home_finished(self, success: bool, message: str):
        self.home_now_btn.setText("Home All Axes")
        if success:
            self._home_error_lbl.hide()
        else:
            self._home_error_lbl.setText(f"Homing failed: {message}")
            self._home_error_lbl.show()
        self._refresh_status()

    def _connect_all(self):
        self._apply_camera()
        self._apply_printer()

    def _disconnect_all(self):
        cam = hw_state.get_camera()
        if cam:
            try:
                cam.stop()
            except Exception:
                pass
        hw_state.set_camera(None)

        motion = hw_state.get_motion()
        if motion:
            try:
                motion.disconnect()
            except Exception:
                pass
        hw_state.set_motion(None)

        self._refresh_status()

    # ------------------------------------------------------------------
    # Status refresh
    # ------------------------------------------------------------------

    def _refresh_status(self):
        cam = hw_state.get_camera()
        cam_ok = cam is not None and getattr(cam, "running", False)
        _set_status(self.camera_status_lbl, cam_ok)

        motion = hw_state.get_motion()
        mc_ok = motion is not None and getattr(motion, "is_connected", False)
        _set_status(self.printer_status_lbl, mc_ok)

        # Homing state
        if not mc_ok:
            self.homed_status_lbl.setText("Unknown")
            self.homed_status_lbl.setStyleSheet("color: gray; font-weight: bold;")
            self.home_now_btn.setEnabled(False)
            self._home_warning.hide()
        elif motion.is_homed:
            self.homed_status_lbl.setText("Homed")
            self.homed_status_lbl.setStyleSheet("color: green; font-weight: bold;")
            self.home_now_btn.setEnabled(True)
            self._home_warning.hide()
        else:
            self.homed_status_lbl.setText("Not homed")
            self.homed_status_lbl.setStyleSheet("color: red; font-weight: bold;")
            self.home_now_btn.setEnabled(True)
            self._home_warning.show()

    # ------------------------------------------------------------------
    # Load from config
    # ------------------------------------------------------------------

    def _load_from_config(self):
        # Backend
        backend = self._cfg.get("hardware.motion_backend", "marlin")
        idx = self.backend_combo.findText(backend)
        if idx >= 0:
            self.backend_combo.setCurrentIndex(idx)

        # Printer ports
        self._refresh_printer_ports()
        baud_str = str(self._cfg.get("hardware.printer.baudrate", 115200))
        bidx = self.printer_baud_combo.findText(baud_str)
        if bidx >= 0:
            self.printer_baud_combo.setCurrentIndex(bidx)

        # Klipper
        self.klipper_host_edit.setText(
            self._cfg.get("hardware.klipper.host", "127.0.0.1")
        )
        self.klipper_port_spin.setValue(
            int(self._cfg.get("hardware.klipper.port", 7125))
        )

        # Laser
        mode = self._cfg.get("hardware.laser.mode", "disabled")
        midx = self.laser_mode_combo.findText(mode)
        if midx >= 0:
            self.laser_mode_combo.setCurrentIndex(midx)
        self.laser_pin_spin.setValue(
            int(self._cfg.get("hardware.laser.rpi_pin", 21))
        )
        self.laser_on_edit.setText(
            self._cfg.get("hardware.laser.klipper_on_gcode", "SET_PIN PIN=laser VALUE=1")
        )
        self.laser_off_edit.setText(
            self._cfg.get("hardware.laser.klipper_off_gcode", "SET_PIN PIN=laser VALUE=0")
        )
