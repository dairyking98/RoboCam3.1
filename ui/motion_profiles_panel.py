"""
Motion Profiles Panel — read/edit/write Marlin feed-rate, acceleration,
and jerk settings (M203 / M201 / M205), plus named presets.

Slider format ported from RoboCam-Suite 2.0 (label | slider | spinbox |
default marker), simplified: no extruder axis (this rig has none),
Marlin only — Klipper has no equivalent gcode for these settings — and
X/Y are always chained into a single slider rather than two rows with
a "Link X=Y" checkbox, since this stage never needs them independent.

Presets (save/load named slider configurations to
config/motion_profile_presets/<name>.json) follow the same convention
as ui/experiment_panel.py's Experiment Presets group, so profiles can
be prepared and saved without a printer connected, then pushed to
hardware later with Apply.
"""
from __future__ import annotations

import glob
import json
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QMessageBox,
)

import robocam.hw_state as hw_state
from robocam.config import get_config
from ui.profile_slider import ProfileSliderRow

# Each group is (title, [(keys, label, lo, hi, step, decimals, suffix), ...]).
# `keys` has two entries for a chained X/Y row, one for a single axis.
_GROUPS = [
    ("Max Feed Rate  (M203, mm/s)", [
        (("max_feed_x", "max_feed_y"), "XY:", 0, 2000, 5, 1, " mm/s"),
        (("max_feed_z",),              "Z:",  0, 50,   0.5, 1, " mm/s"),
    ]),
    ("Max Acceleration  (M201, mm/s²)", [
        (("max_accel_x", "max_accel_y"), "XY:", 0, 5000, 50, 0, " mm/s²"),
        (("max_accel_z",),               "Z:",  0, 500,  5,  0, " mm/s²"),
    ]),
    ("Jerk  (M205, mm/s)", [
        (("jerk_x", "jerk_y"), "XY:", 0, 30, 0.5, 1, " mm/s"),
        (("jerk_z",),          "Z:",  0, 5,  0.1, 2, " mm/s"),
    ]),
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

        # list of (keys_tuple, ProfileSliderRow)
        self._rows: list[tuple[tuple, ProfileSliderRow]] = []
        self._last_read: dict = {}
        self._read_thread: _ReadThread | None = None
        self._apply_thread: _ApplyThread | None = None

        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        btn_row = QHBoxLayout()
        self.read_btn = QPushButton("Read from Printer")
        self.read_btn.setToolTip(
            "Query M503 and populate the sliders below with the current printer values.\n"
            "Also marks them as the default (orange triangle)."
        )
        self.read_btn.clicked.connect(self._read_profiles)
        btn_row.addWidget(self.read_btn)

        self.apply_btn = QPushButton("Apply to Printer")
        self.apply_btn.setToolTip(
            "Send M203/M201/M205 with the current slider values\n"
            "and save to EEPROM (M500)."
        )
        self.apply_btn.clicked.connect(self._apply_profiles)
        btn_row.addWidget(self.apply_btn)

        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.setToolTip(
            "Restore all sliders to the values last read from (or applied to)\n"
            "the printer, without sending any commands."
        )
        self.reset_btn.setEnabled(False)
        self.reset_btn.clicked.connect(self._reset_profiles)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.status_lbl = QLabel("Not read — connect the printer or click ‘Read from Printer’.")
        self.status_lbl.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(self.status_lbl)

        root.addWidget(self._build_presets_group())

        for title, specs in _GROUPS:
            root.addWidget(self._build_group(title, specs))
        root.addStretch()

        self._refresh_support()
        self._refresh_presets()

    def _build_presets_group(self) -> QGroupBox:
        grp = QGroupBox("Motion Profile Presets")
        grp.setToolTip(
            "Save or load the slider values above as a named preset —\n"
            "independent of the printer connection, so profiles can be\n"
            "prepared offline and applied to hardware later."
        )
        layout = QHBoxLayout(grp)

        self.preset_combo = QComboBox()
        self.preset_combo.setEditable(True)
        layout.addWidget(self.preset_combo, stretch=1)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_preset)
        layout.addWidget(save_btn)

        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._load_preset)
        layout.addWidget(load_btn)

        refresh_btn = QPushButton("↺")
        refresh_btn.setFixedWidth(28)
        refresh_btn.clicked.connect(self._refresh_presets)
        layout.addWidget(refresh_btn)

        return grp

    def _build_group(self, title: str, specs: list) -> QGroupBox:
        grp = QGroupBox(title)
        grp.setToolTip(
            "The orange triangle on each slider marks the value last read\n"
            "from (or applied to) the printer. X and Y are always kept\n"
            "equal — this stage has no need to tune them independently."
        )
        layout = QVBoxLayout(grp)
        layout.setSpacing(2)
        for keys, label, lo, hi, step, dec, suffix in specs:
            row = ProfileSliderRow(label, lo, hi, step=step, decimals=dec, suffix=suffix)
            layout.addWidget(row)
            self._rows.append((keys, row))
        return grp

    def _set_status(self, text: str, color: str = "gray"):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _refresh_support(self):
        """Called on show and after Read/Apply — the sliders themselves stay
        editable regardless of connection (so profiles can be prepared and
        saved as presets offline); only Read/Apply need a live Marlin link."""
        mc = hw_state.get_motion()
        if mc is None or not mc.is_connected:
            self.read_btn.setEnabled(False)
            self.apply_btn.setEnabled(False)
            self._set_status("Not read — connect the printer or click ‘Read from Printer’.")
            return
        if not mc.supports_profiles:
            self.read_btn.setEnabled(False)
            self.apply_btn.setEnabled(False)
            self._set_status("Motion profiles are only supported on the Marlin backend.", "orange")
            return
        self.read_btn.setEnabled(True)
        self.apply_btn.setEnabled(True)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_support()

    def _populate(self, profiles: dict, mark_default: bool):
        for keys, row in self._rows:
            # For a chained X/Y row there are two keys; use whichever is
            # present (they're always kept equal by this panel).
            val = None
            for k in keys:
                if profiles.get(k) is not None:
                    val = profiles[k]
                    break
            if val is None:
                continue
            row.set_value(float(val))
            if mark_default:
                row.set_default(float(val))

    # ------------------------------------------------------------------
    # Read / Apply / Reset (hardware)
    # ------------------------------------------------------------------

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
        self._last_read = dict(profiles)
        self.reset_btn.setEnabled(True)
        self._populate(profiles, mark_default=True)
        self._set_status(
            "Values read from printer. Adjust sliders and click ‘Apply to Printer’.", "green"
        )

    def _apply_profiles(self):
        mc = hw_state.get_motion()
        if mc is None or not mc.is_connected or not mc.supports_profiles:
            self._refresh_support()
            return

        profiles = self._preset_data()
        self.apply_btn.setEnabled(False)
        self._set_status("Applying…")

        self._apply_thread = _ApplyThread(mc, profiles, self)
        self._apply_thread.finished.connect(lambda ok, err: self._on_apply_done(ok, err, profiles))
        self._apply_thread.start()

    def _on_apply_done(self, success: bool, error: str, profiles: dict):
        self.apply_btn.setEnabled(True)
        if success:
            self._last_read = dict(profiles)
            self.reset_btn.setEnabled(True)
            self._populate(profiles, mark_default=True)
            self._set_status("Profiles applied and saved to EEPROM (M500).", "green")
        else:
            self._set_status(f"Error applying profiles: {error}", "red")

    def _reset_profiles(self):
        if not self._last_read:
            self._set_status("Nothing to reset — read from printer first.", "orange")
            return
        self._populate(self._last_read, mark_default=False)
        self._set_status("Reset to last-read values.", "gray")

    # ------------------------------------------------------------------
    # Presets (disk, independent of hardware)
    # ------------------------------------------------------------------

    def _preset_dir(self) -> str:
        cfg = get_config()
        d = os.path.join(cfg.get("paths.config_dir", "config"), "motion_profile_presets")
        os.makedirs(d, exist_ok=True)
        return d

    def _preset_data(self) -> dict:
        profiles: dict = {}
        for keys, row in self._rows:
            for k in keys:
                profiles[k] = row.value()
        return profiles

    def _refresh_presets(self):
        files = sorted(
            os.path.splitext(os.path.basename(f))[0]
            for f in glob.glob(os.path.join(self._preset_dir(), "*.json"))
        )
        current = self.preset_combo.currentText()
        self.preset_combo.clear()
        self.preset_combo.addItems(files)
        if current in files:
            self.preset_combo.setCurrentText(current)

    def _save_preset(self):
        name = self.preset_combo.currentText().strip() or "default"
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        path = os.path.join(self._preset_dir(), f"{name}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._preset_data(), f, indent=2)
            self._refresh_presets()
            self.preset_combo.setCurrentText(name)
            self._set_status(f"Preset ‘{name}’ saved.", "green")
        except Exception as e:
            QMessageBox.critical(self, "Preset Error", str(e))

    def _load_preset(self):
        name = self.preset_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Preset", "Select a preset to load.")
            return
        path = os.path.join(self._preset_dir(), f"{name}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Preset Error", str(e))
            return
        self._populate(data, mark_default=False)
        self._set_status(
            f"Preset ‘{name}’ loaded. Click ‘Apply to Printer’ to push it to hardware.", "green"
        )
