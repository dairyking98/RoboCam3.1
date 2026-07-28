"""
Motion Profiles Panel — read/edit/write Marlin feed-rate, acceleration,
and jerk settings (M203 / M201 / M205).

Simplified relative to RoboCam-Suite 2.0's version: plain spin boxes
instead of custom slider widgets, no X=Y linking, no extruder axis
(this rig has none), and Marlin only — Klipper has no equivalent gcode
for these settings.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QDoubleSpinBox,
)

import robocam.hw_state as hw_state

# (key, axis label, min, max, step, decimals)
_FEED_FIELDS = [
    ("max_feed_x", "X", 0, 2000, 5, 1),
    ("max_feed_y", "Y", 0, 2000, 5, 1),
    ("max_feed_z", "Z", 0, 50, 0.5, 1),
]
_ACCEL_FIELDS = [
    ("max_accel_x", "X", 0, 5000, 50, 0),
    ("max_accel_y", "Y", 0, 5000, 50, 0),
    ("max_accel_z", "Z", 0, 500, 5, 0),
]
_JERK_FIELDS = [
    ("jerk_x", "X", 0, 30, 0.5, 1),
    ("jerk_y", "Y", 0, 30, 0.5, 1),
    ("jerk_z", "Z", 0, 5, 0.1, 2),
]


class _ReadThread(QThread):
    finished = Signal(bool, dict, str)  # success, profiles, error

    def __init__(self, motion, parent=None):
        super().__init__(parent)
        self._motion = motion

    def run(self):
        try:
            profiles = self._motion.read_profiles()
            self.finished.emit(True, profiles, "")
        except Exception as e:
            self.finished.emit(False, {}, str(e))


class _ApplyThread(QThread):
    finished = Signal(bool, str)  # success, error

    def __init__(self, motion, profiles, parent=None):
        super().__init__(parent)
        self._motion = motion
        self._profiles = profiles

    def run(self):
        try:
            self._motion.apply_profiles(self._profiles)
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


class MotionProfilesPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._fields: dict[str, QDoubleSpinBox] = {}
        self._read_thread: _ReadThread | None = None
        self._apply_thread: _ApplyThread | None = None

        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        btn_row = QHBoxLayout()
        self.read_btn = QPushButton("Read from Printer")
        self.read_btn.setToolTip("Query M503 and populate the fields below with the current printer values.")
        self.read_btn.clicked.connect(self._read_profiles)
        btn_row.addWidget(self.read_btn)

        self.apply_btn = QPushButton("Apply to Printer")
        self.apply_btn.setToolTip("Send M203/M201/M205 with the values below and save to EEPROM (M500).")
        self.apply_btn.clicked.connect(self._apply_profiles)
        btn_row.addWidget(self.apply_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.status_lbl = QLabel("Not read — connect the printer or click ‘Read from Printer’.")
        self.status_lbl.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(self.status_lbl)

        root.addWidget(self._build_group("Max Feed Rate  (M203, mm/s)", _FEED_FIELDS))
        root.addWidget(self._build_group("Max Acceleration  (M201, mm/s²)", _ACCEL_FIELDS))
        root.addWidget(self._build_group("Jerk  (M205, mm/s)", _JERK_FIELDS))
        root.addStretch()

        self._set_fields_enabled(False)
        self._refresh_support()

    def _build_group(self, title: str, fields: list) -> QGroupBox:
        grp = QGroupBox(title)
        grid = QGridLayout(grp)
        for col, (key, axis, lo, hi, step, dec) in enumerate(fields):
            grid.addWidget(QLabel(f"{axis}:"), 0, col * 2)
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setDecimals(dec)
            grid.addWidget(spin, 0, col * 2 + 1)
            self._fields[key] = spin
        return grp

    def _set_fields_enabled(self, enabled: bool):
        for spin in self._fields.values():
            spin.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)

    def _set_status(self, text: str, color: str = "gray"):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _refresh_support(self):
        """Called on show and after Read/Apply — disables everything with an
        explanatory message when profiles aren't supported or not connected."""
        mc = hw_state.get_motion()
        if mc is None or not mc.is_connected:
            self.read_btn.setEnabled(False)
            self._set_fields_enabled(False)
            self._set_status("Not read — connect the printer or click ‘Read from Printer’.")
            return
        if not mc.supports_profiles:
            self.read_btn.setEnabled(False)
            self._set_fields_enabled(False)
            self._set_status("Motion profiles are only supported on the Marlin backend.", "orange")
            return
        self.read_btn.setEnabled(True)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_support()

    def _populate(self, profiles: dict):
        for key, spin in self._fields.items():
            val = profiles.get(key)
            if val is not None:
                spin.setValue(float(val))
                spin.setEnabled(True)
        self.apply_btn.setEnabled(True)

    def _read_profiles(self):
        mc = hw_state.get_motion()
        if mc is None or not mc.is_connected or not mc.supports_profiles:
            self._refresh_support()
            return

        self.read_btn.setEnabled(False)
        self._set_status("Reading…")

        self._read_thread = _ReadThread(mc, self)
        self._read_thread.finished.connect(self._on_read_done)
        self._read_thread.start()

    def _on_read_done(self, success: bool, profiles: dict, error: str):
        self.read_btn.setEnabled(True)
        if not success:
            self._set_status(f"Error reading profiles: {error}", "red")
            return
        if not profiles:
            self._set_status("No profile data returned by printer.", "orange")
            return
        self._populate(profiles)
        self._set_status("Values read from printer. Edit and click ‘Apply to Printer’.", "green")

    def _apply_profiles(self):
        mc = hw_state.get_motion()
        if mc is None or not mc.is_connected or not mc.supports_profiles:
            self._refresh_support()
            return

        profiles = {key: spin.value() for key, spin in self._fields.items() if spin.isEnabled()}
        self.apply_btn.setEnabled(False)
        self._set_status("Applying…")

        self._apply_thread = _ApplyThread(mc, profiles, self)
        self._apply_thread.finished.connect(self._on_apply_done)
        self._apply_thread.start()

    def _on_apply_done(self, success: bool, error: str):
        self.apply_btn.setEnabled(True)
        if success:
            self._set_status("Profiles applied and saved to EEPROM (M500).", "green")
        else:
            self._set_status(f"Error applying profiles: {error}", "red")
