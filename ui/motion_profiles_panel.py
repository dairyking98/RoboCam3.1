"""
Motion Profiles Panel — read/edit/write Marlin feed-rate, acceleration,
and jerk settings (M203 / M201 / M205), plus named presets.

Slider format ported from RoboCam-Suite 2.0 (label | slider | spinbox |
ghost-tick marker), simplified: no extruder axis (this rig has none),
Marlin only — Klipper has no equivalent gcode for these settings — and
X/Y are always chained into a single slider rather than two rows with
a "Link X=Y" checkbox, since this stage never needs them independent.

Slider ranges are the actual M203/M201/M205 edit ceilings from
Creality's shipped Ender-5 S1 Marlin firmware (Configuration.h,
LIMITED_MAX_FR_EDITING / LIMITED_MAX_ACCEL_EDITING / LIMITED_JERK_EDITING
— MARRY_HIGHT_SPEED variant, which is what ships): M203 X/Y up to 600
mm/s (Z 20), M201 X/Y up to 2000 mm/s² (Z 200), M205 X/Y up to 10 mm/s
(Z 0.6). Sending a value above these gets silently clamped by the
firmware anyway, so there's no point letting the slider go higher.
Note the firmware's own boot-time defaults (feed 500/20, accel 3000/100,
jerk 15/0.4) actually exceed some of these edit ceilings — a real
firmware quirk, not a bug here.

Presets (save/load named slider configurations to
config/motion_profile_presets/<name>.json) follow the same convention
as ui/experiment_panel.py's Experiment Presets group, so profiles can
be prepared and saved without a printer connected. Loading a preset
only populates the sliders — it does not send anything to the printer;
Apply (now grouped with Save/Load rather than off in the Read/Reset
row) is the one action that actually pushes gcode. The last-loaded
preset name is persisted to the session and restored (sliders
populated, nothing sent) on the next launch.

On (re)connect, if a profile was already read/applied earlier in this
session, it's re-sent then, in case the printer's actual state drifted
(e.g. a power cycle reset it to firmware defaults) while disconnected
— motion profiles are not known to reliably persist across a printer
power cycle. This connect-time re-push is independent of Load/Apply
above; it only concerns what's already been confirmed on the
printer, not merely populated into the sliders.

Three bundled starting-point presets ship at fast/medium/slow speed
tiers, all biased toward low vibration (see _DEFAULT_PRESET_NAME below
for sourcing). "Slow" is treated as *the* default: it's what the combo
box defaults to on first load, and what gets applied on the very first
printer connect of a session when nothing else is known yet — safer
to start conservative than to trust whatever the firmware happened to
boot with.
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
from robocam.session import session_manager
from ui.profile_slider import ProfileSliderRow

# Each group is (title, [(keys, label, lo, hi, step, decimals, suffix), ...]).
# `keys` has two entries for a chained X/Y row, one for a single axis.
# Ranges are the real Ender-5 S1 firmware M203/M201/M205 edit ceilings
# (see module docstring) — not arbitrary/generic values.
_GROUPS = [
    ("Feed Rate  (M203 / G-code F, mm/s)", [
        (("max_feed_x", "max_feed_y"), "XY:", 0, 600, 5,   1, " mm/s"),
        (("max_feed_z",),              "Z:",  0, 20,  0.5, 1, " mm/s"),
    ]),
    ("Acceleration Rate  (M201 / M204 T, mm/s²)", [
        (("max_accel_x", "max_accel_y"), "XY:", 0, 2000, 25, 0, " mm/s²"),
        (("max_accel_z",),               "Z:",  0, 200,  5,  0, " mm/s²"),
    ]),
    ("Jerk  (M205, mm/s)", [
        (("jerk_x", "jerk_y"), "XY:", 0, 10,  0.5, 1, " mm/s"),
        (("jerk_z",),          "Z:",  0, 0.6, 0.05, 2, " mm/s"),
    ]),
]

# The "slow" tier is the actual default: the preset combo box defaults to
# it, and it's what gets applied on the very first printer connect of a
# session (see _on_printer_connected) when nothing else is known yet —
# safer to start conservative than trust whatever the firmware booted
# with. All three (fast/medium/slow) are reasoned starting points, not
# validated optima — see PROJECT_STATE.md for derivation.
_DEFAULT_PRESET_NAME = "ender5_s1_slow_low_vibration"

# The three bundled starting-point presets — protected from being
# overwritten by Save (see _save_preset) so they stay a known-good
# fallback (_on_printer_connected relies on _DEFAULT_PRESET_NAME always
# being the original, reasoned values) even after a user saves their own
# tuned profiles alongside them.
_BUNDLED_PRESET_NAMES = (
    "ender5_s1_fast_low_vibration",
    "ender5_s1_medium_low_vibration",
    _DEFAULT_PRESET_NAME,
)


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
        # Last profile confirmed on the printer, via Read or a successful
        # Apply — used both for Reset and to re-push on reconnect. Empty
        # until the first Read/Apply. Loading a preset does NOT touch this;
        # it only populates the sliders until Apply is actually clicked.
        self._last_read: dict = {}
        self._read_thread: _ReadThread | None = None
        self._apply_thread: _ApplyThread | None = None

        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        btn_row = QHBoxLayout()
        self.read_btn = QPushButton("Read from Printer")
        self.read_btn.setToolTip(
            "Query M503 and populate the sliders below with the current printer values.\n"
            "Also marks them with a ghost tick, showing where they currently are on hardware."
        )
        self.read_btn.clicked.connect(self._read_profiles)
        btn_row.addWidget(self.read_btn)

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
        self._load_session()

    def _build_presets_group(self) -> QGroupBox:
        grp = QGroupBox("Motion Profile Presets")
        grp.setToolTip(
            "Save the slider values above as a named preset, independent of\n"
            "the printer connection, so profiles can be prepared offline.\n"
            "Loading a preset only populates the sliders — click Apply to\n"
            "actually send it to the printer."
        )
        layout = QHBoxLayout(grp)

        self.preset_combo = QComboBox()
        self.preset_combo.setEditable(True)
        layout.addWidget(self.preset_combo, stretch=1)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_preset)
        layout.addWidget(save_btn)

        load_btn = QPushButton("Load")
        load_btn.setToolTip("Populate the sliders from this preset — does not send anything to the printer.")
        load_btn.clicked.connect(self._load_preset)
        layout.addWidget(load_btn)

        self.apply_btn = QPushButton("Apply to Printer")
        self.apply_btn.setToolTip(
            "Send M203/M201/M205 with the current slider values\n"
            "and save to EEPROM (M500)."
        )
        self.apply_btn.clicked.connect(self._apply_profiles)
        layout.addWidget(self.apply_btn)

        refresh_btn = QPushButton("↺")
        refresh_btn.setFixedWidth(28)
        refresh_btn.clicked.connect(self._refresh_presets)
        layout.addWidget(refresh_btn)

        return grp

    def _build_group(self, title: str, specs: list) -> QGroupBox:
        grp = QGroupBox(title)
        grp.setToolTip(
            "The ghost tick on each slider marks the value last confirmed on the\n"
            "printer (via Read or a successful Apply). A highlighted field means\n"
            "its current position differs from the ghost tick — Apply would\n"
            "actually change that value. X and Y are always kept equal — this\n"
            "stage has no need to tune them independently."
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

    def _populate(self, profiles: dict, mark_current: bool):
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
            if mark_current:
                row.set_current(float(val))

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
        self._populate(profiles, mark_current=True)
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
            self._populate(profiles, mark_current=True)
            self._set_status("Profiles applied and saved to EEPROM (M500).", "green")
        else:
            self._set_status(f"Error applying profiles: {error}", "red")

    def _reset_profiles(self):
        if not self._last_read:
            self._set_status("Nothing to reset — read from printer first.", "orange")
            return
        self._populate(self._last_read, mark_current=False)
        self._set_status("Reset to last-read values.", "gray")

    # ------------------------------------------------------------------
    # Connect handling
    # ------------------------------------------------------------------

    def _on_printer_connected(self):
        """Wired to SetupPanel.motion_connected. Motion profiles aren't
        known to reliably survive a printer power cycle, so if we already
        know what profile *should* be active (read, applied, or loaded
        earlier this session), re-push it now rather than just reading —
        the printer's actual state may have drifted while disconnected.
        On the first connect of a session, with nothing else known yet,
        apply the conservative default preset instead of trusting
        whatever the firmware happened to boot with; only fall back to a
        plain Read if that preset file is missing."""
        if self._last_read:
            self._populate(self._last_read, mark_current=False)
            self._set_status(
                "Printer connected — re-applying the last-known profile "
                "in case it didn't persist…", "gray"
            )
            self._apply_profiles()
            return

        data = self._read_preset_file(_DEFAULT_PRESET_NAME)
        if data is not None:
            self._populate(data, mark_current=False)
            self._set_status(
                f"Printer connected — applying default profile ‘{_DEFAULT_PRESET_NAME}’…", "gray"
            )
            self._apply_profiles()
        else:
            self._read_profiles()

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
        elif not current and _DEFAULT_PRESET_NAME in files:
            # First-ever population (nothing selected yet) — default the
            # combo to the conservative preset rather than leaving it blank.
            self.preset_combo.setCurrentText(_DEFAULT_PRESET_NAME)

    def _save_preset(self):
        name = self.preset_combo.currentText().strip() or "default"
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        if name in _BUNDLED_PRESET_NAMES:
            QMessageBox.warning(
                self, "Reserved Preset Name",
                f"‘{name}’ is a bundled default preset and can't be overwritten.\n\n"
                "Enter a different name to save your own profile."
            )
            return
        path = os.path.join(self._preset_dir(), f"{name}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._preset_data(), f, indent=2)
            self._refresh_presets()
            self.preset_combo.setCurrentText(name)
            self._set_status(f"Preset ‘{name}’ saved.", "green")
        except Exception as e:
            QMessageBox.critical(self, "Preset Error", str(e))

    def _read_preset_file(self, name: str) -> dict | None:
        path = os.path.join(self._preset_dir(), f"{name}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _load_preset(self):
        name = self.preset_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Preset", "Select a preset to load.")
            return
        data = self._read_preset_file(name)
        if data is None:
            QMessageBox.critical(self, "Preset Error", f"Could not load preset ‘{name}’.")
            return
        self._populate(data, mark_current=False)
        self._set_status(f"Preset ‘{name}’ loaded. Click ‘Apply to Printer’ to send it.", "gray")
        session_manager.update("motion_profiles", {"last_loaded_preset": name})
        session_manager.save()

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    def _load_session(self):
        """Restore the last-loaded preset on startup — populates the
        sliders and combo box the same way a manual Load would, but sends
        nothing to the printer (same decoupling as Load itself)."""
        name = session_manager.get("motion_profiles").get("last_loaded_preset", "")
        if not name:
            return
        data = self._read_preset_file(name)
        if data is None:
            return
        idx = self.preset_combo.findText(name)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
        else:
            self.preset_combo.setCurrentText(name)
        self._populate(data, mark_current=False)
        self._set_status(f"Restored last-loaded preset ‘{name}’.", "gray")
