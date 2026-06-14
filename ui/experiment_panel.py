"""
Experiment Panel — configure and run well-plate experiments.

Three-column QSplitter
----------------------
Col 1 : Live camera preview (paused during raw/video capture)
Col 2 : Settings — name, calibration, mode, timing, laser, presets,
        start/stop/pause
Col 3 : Well selection grid
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QLineEdit,
    QDoubleSpinBox, QCheckBox, QSplitter, QScrollArea,
    QMessageBox,
)

from robocam.calibration import WellPlate
from robocam.config import get_config
import robocam.hw_state as hw_state
from ui.camera_widget import _FrameGrabber, _LivePreview
from ui.well_grid import WellGrid

CORNER_NAMES = ["Upper-Left", "Lower-Left", "Upper-Right", "Lower-Right"]


# ---------------------------------------------------------------------------
# Experiment runner thread wrapper
# ---------------------------------------------------------------------------

class _ExperimentThread(QThread):
    status_update = Signal(str)
    finished = Signal()

    def __init__(self, runner, name, positions, labels, delay,
                 mode, image_format, use_laser,
                 pre_duration, laser_on_duration, post_duration, parent=None):
        super().__init__(parent)
        self._runner = runner
        self._kwargs = dict(
            name=name, positions=positions, labels=labels,
            delay_per_well=delay, callback=self._on_status,
            mode=mode, image_format=image_format, use_laser=use_laser,
            pre_duration=pre_duration, laser_on_duration=laser_on_duration,
            post_duration=post_duration,
        )

    def _on_status(self, msg: str):
        self.status_update.emit(msg)

    def run(self):
        self._runner.run(**self._kwargs)
        self.finished.emit()

    def stop(self):
        self._runner.stop()

    def pause(self):
        self._runner.pause()

    def resume(self):
        self._runner.resume()


# ---------------------------------------------------------------------------
# ExperimentPanel
# ---------------------------------------------------------------------------

class ExperimentPanel(QWidget):
    experiment_started  = Signal()
    experiment_finished = Signal()

    def __init__(self, calibration_panel=None, parent=None):
        super().__init__(parent)
        self._cal_panel = calibration_panel
        self._exp_thread: Optional[_ExperimentThread] = None

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Col 1 — live preview
        col1 = QWidget()
        c1l = QVBoxLayout(col1)
        c1l.setContentsMargins(0, 0, 4, 0)
        hdr = QLabel("Live Camera Preview")
        hdr.setStyleSheet("font-weight: bold; font-size: 11px;")
        c1l.addWidget(hdr)
        self._grabber = _FrameGrabber(fps=15)
        self._preview = _LivePreview(self._grabber)
        c1l.addWidget(self._preview, stretch=1)
        splitter.addWidget(col1)

        # Col 2 — settings
        col2_inner = QWidget()
        col2_layout = QVBoxLayout(col2_inner)
        col2_layout.setSpacing(6)
        col2_layout.setContentsMargins(4, 4, 4, 4)
        col2_layout.addWidget(self._build_settings_group())
        col2_layout.addWidget(self._build_presets_group())
        col2_layout.addWidget(self._build_control_group())
        col2_layout.addStretch()

        col2_scroll = QScrollArea()
        col2_scroll.setWidgetResizable(True)
        col2_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        col2_scroll.setWidget(col2_inner)
        splitter.addWidget(col2_scroll)

        # Col 3 — well selection
        col3 = QWidget()
        c3l = QVBoxLayout(col3)
        c3l.setContentsMargins(4, 4, 4, 4)
        c3l.addWidget(self._build_well_selection_group())
        splitter.addWidget(col3)

        splitter.setSizes([540, 360, 300])
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

        self._grabber.frame_ready.connect(self._preview.update_frame)
        self._grabber.camera_disconnected.connect(self._preview.show_disconnected)
        self._grabber.start()

        self._refresh_cals()
        self._refresh_presets()
        self._update_mode_visibility()

    def closeEvent(self, event):
        self._grabber.stop()
        self._grabber.wait(1000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _build_settings_group(self) -> QGroupBox:
        grp = QGroupBox("Experiment Settings")
        layout = QGridLayout(grp)

        row = 0
        layout.addWidget(QLabel("Experiment name:"), row, 0)
        self.name_edit = QLineEdit("my_experiment")
        layout.addWidget(self.name_edit, row, 1); row += 1

        layout.addWidget(QLabel("Calibration file:"), row, 0)
        cal_row = QHBoxLayout()
        self.cal_combo = QComboBox()
        self.cal_combo.setMinimumWidth(150)
        cal_row.addWidget(self.cal_combo)
        refresh_cal_btn = QPushButton("↺")
        refresh_cal_btn.setFixedWidth(28)
        refresh_cal_btn.clicked.connect(self._refresh_cals)
        cal_row.addWidget(refresh_cal_btn)
        layout.addLayout(cal_row, row, 1); row += 1

        layout.addWidget(QLabel("Mode:"), row, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Image", "Raw .npy", "Video"])
        self.mode_combo.currentTextChanged.connect(self._update_mode_visibility)
        layout.addWidget(self.mode_combo, row, 1); row += 1

        layout.addWidget(QLabel("Dwell per well (s):"), row, 0)
        self.dwell_spin = QDoubleSpinBox()
        self.dwell_spin.setRange(0.0, 300.0)
        self.dwell_spin.setValue(1.0)
        self.dwell_spin.setSingleStep(0.5)
        layout.addWidget(self.dwell_spin, row, 1); row += 1

        # Image format (Image mode only)
        self.lbl_img_fmt = QLabel("Image format:")
        layout.addWidget(self.lbl_img_fmt, row, 0)
        self.img_fmt_combo = QComboBox()
        self.img_fmt_combo.addItems(["jpg", "png", "tif"])
        layout.addWidget(self.img_fmt_combo, row, 1); row += 1

        # Duration (Raw/Video modes)
        self.lbl_duration = QLabel("Record duration (s):")
        layout.addWidget(self.lbl_duration, row, 0)
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1, 3600.0)
        self.duration_spin.setValue(5.0)
        self.duration_spin.setSingleStep(1.0)
        layout.addWidget(self.duration_spin, row, 1); row += 1

        # Use Laser checkbox
        self.use_laser_chk = QCheckBox("Use Laser")
        self.use_laser_chk.toggled.connect(self._update_mode_visibility)
        layout.addWidget(self.use_laser_chk, row, 0, 1, 2); row += 1

        # Laser ON duration
        self.lbl_laser_on = QLabel("Laser ON (s):")
        layout.addWidget(self.lbl_laser_on, row, 0)
        self.laser_on_spin = QDoubleSpinBox()
        self.laser_on_spin.setRange(0.0, 300.0)
        self.laser_on_spin.setValue(1.0)
        self.laser_on_spin.setSingleStep(0.5)
        layout.addWidget(self.laser_on_spin, row, 1); row += 1

        # Post-laser duration
        self.lbl_post = QLabel("Post-laser (s):")
        layout.addWidget(self.lbl_post, row, 0)
        self.post_spin = QDoubleSpinBox()
        self.post_spin.setRange(0.0, 300.0)
        self.post_spin.setValue(2.0)
        self.post_spin.setSingleStep(0.5)
        layout.addWidget(self.post_spin, row, 1)

        return grp

    def _build_presets_group(self) -> QGroupBox:
        grp = QGroupBox("Experiment Presets")
        layout = QVBoxLayout(grp)

        self.preset_combo = QComboBox()
        self.preset_combo.setEditable(True)
        layout.addWidget(self.preset_combo)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_preset)
        btn_row.addWidget(save_btn)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._load_preset)
        btn_row.addWidget(load_btn)
        refresh_btn = QPushButton("↺")
        refresh_btn.setFixedWidth(28)
        refresh_btn.clicked.connect(self._refresh_presets)
        btn_row.addWidget(refresh_btn)
        layout.addLayout(btn_row)

        return grp

    def _build_control_group(self) -> QGroupBox:
        grp = QGroupBox("Run")
        layout = QVBoxLayout(grp)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start Experiment")
        self.start_btn.clicked.connect(self._start_experiment)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_experiment)
        btn_row.addWidget(self.stop_btn)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self.pause_btn)
        layout.addLayout(btn_row)

        self.status_lbl = QLabel("Status: Ready")
        self.status_lbl.setStyleSheet("font-style: italic; color: #555;")
        layout.addWidget(self.status_lbl)

        return grp

    def _build_well_selection_group(self) -> QGroupBox:
        grp = QGroupBox("Well Selection  (drag to toggle)")
        layout = QVBoxLayout(grp)

        tb = QHBoxLayout()
        check_all_btn = QPushButton("Check All")
        check_all_btn.clicked.connect(lambda: self.well_grid.check_all())
        tb.addWidget(check_all_btn)
        uncheck_btn = QPushButton("Uncheck All")
        uncheck_btn.clicked.connect(lambda: self.well_grid.uncheck_all())
        tb.addWidget(uncheck_btn)
        invert_btn = QPushButton("Invert")
        invert_btn.clicked.connect(lambda: self.well_grid.invert())
        tb.addWidget(invert_btn)
        self.sel_count_lbl = QLabel("0 / 0 selected")
        self.sel_count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tb.addWidget(self.sel_count_lbl)
        layout.addLayout(tb)

        self.well_grid = WellGrid(rows=8, cols=12, mode=WellGrid.Mode.SELECT)
        self.well_grid.selection_changed.connect(self._update_sel_count)
        layout.addWidget(self.well_grid)

        self._update_sel_count()
        return grp

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def _update_mode_visibility(self):
        mode = self.mode_combo.currentText()
        is_timed = mode in ("Raw .npy", "Video")
        use_laser = self.use_laser_chk.isChecked() and is_timed

        self.lbl_img_fmt.setVisible(mode == "Image")
        self.img_fmt_combo.setVisible(mode == "Image")

        self.lbl_duration.setVisible(is_timed)
        self.duration_spin.setVisible(is_timed)
        if is_timed:
            self.lbl_duration.setText("Pre-laser (s):" if use_laser else "Record duration (s):")

        self.use_laser_chk.setVisible(is_timed)
        if not is_timed:
            self.use_laser_chk.setChecked(False)

        self.lbl_laser_on.setVisible(use_laser)
        self.laser_on_spin.setVisible(use_laser)
        self.lbl_post.setVisible(use_laser)
        self.post_spin.setVisible(use_laser)

    def _update_sel_count(self):
        sel = self.well_grid.selected_count()
        tot = self.well_grid.total_count()
        self.sel_count_lbl.setText(f"{sel} / {tot} selected")

    def _update_resolution_label(self):
        pass  # placeholder for signal compatibility with MainWindow

    # ------------------------------------------------------------------
    # Calibration sync
    # ------------------------------------------------------------------

    def sync_from_calibration(self):
        """Called by MainWindow when calibration corners or dimensions change."""
        if self._cal_panel is None:
            return
        cols, rows = self._cal_panel.get_well_dimensions()
        if cols > 0 and rows > 0:
            self.well_grid.rebuild(rows, cols)
            self._update_sel_count()

    def _refresh_cals(self):
        cfg = get_config()
        cal_dir = cfg.get("paths.calibration_dir", "config/calibrations")
        files = [os.path.basename(f) for f in glob.glob(os.path.join(cal_dir, "*.json"))]
        current = self.cal_combo.currentText()
        self.cal_combo.clear()
        self.cal_combo.addItems(files)
        if current in files:
            self.cal_combo.setCurrentText(current)

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def _preset_dir(self) -> str:
        cfg = get_config()
        d = os.path.join(cfg.get("paths.config_dir", "config"), "experiment_presets")
        os.makedirs(d, exist_ok=True)
        return d

    def _preset_data(self) -> dict:
        return {
            "name": self.name_edit.text(),
            "mode": self.mode_combo.currentText(),
            "dwell": self.dwell_spin.value(),
            "image_format": self.img_fmt_combo.currentText(),
            "duration": self.duration_spin.value(),
            "use_laser": self.use_laser_chk.isChecked(),
            "laser_on": self.laser_on_spin.value(),
            "post": self.post_spin.value(),
            "cal_file": self.cal_combo.currentText(),
        }

    def _apply_preset_data(self, data: dict):
        self.name_edit.setText(data.get("name", self.name_edit.text()))
        idx = self.mode_combo.findText(data.get("mode", ""))
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.dwell_spin.setValue(float(data.get("dwell", 1.0)))
        fmt_idx = self.img_fmt_combo.findText(data.get("image_format", "jpg"))
        if fmt_idx >= 0:
            self.img_fmt_combo.setCurrentIndex(fmt_idx)
        self.duration_spin.setValue(float(data.get("duration", 5.0)))
        self.use_laser_chk.setChecked(bool(data.get("use_laser", False)))
        self.laser_on_spin.setValue(float(data.get("laser_on", 1.0)))
        self.post_spin.setValue(float(data.get("post", 2.0)))
        cal = data.get("cal_file", "")
        if cal:
            cal_idx = self.cal_combo.findText(cal)
            if cal_idx >= 0:
                self.cal_combo.setCurrentIndex(cal_idx)
        self._update_mode_visibility()

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
                self._apply_preset_data(json.load(f))
        except Exception as e:
            QMessageBox.critical(self, "Preset Error", str(e))

    # ------------------------------------------------------------------
    # Experiment control
    # ------------------------------------------------------------------

    def _start_experiment(self):
        runner = hw_state.get_runner()
        if runner is None:
            QMessageBox.critical(self, "Error", "Motion controller not connected.")
            return

        cal_file = self.cal_combo.currentText()
        if not cal_file:
            QMessageBox.critical(self, "Error", "Select a calibration file.")
            return

        cfg = get_config()
        cal_path = os.path.join(
            cfg.get("paths.calibration_dir", "config/calibrations"), cal_file
        )
        try:
            from robocam.calibration import CalibrationManager
            cm = CalibrationManager()
            positions, labels = cm.load(cal_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load calibration: {e}")
            return

        selected = self.well_grid.get_selected_indices()
        if not selected:
            QMessageBox.critical(self, "Error", "No wells selected.")
            return

        filtered_pos    = [positions[i] for i in selected if i < len(positions)]
        filtered_labels = [labels[i]    for i in selected if i < len(labels)]

        mode_map = {"Image": "image", "Raw .npy": "raw", "Video": "video"}
        mode = mode_map.get(self.mode_combo.currentText(), "image")

        # Pause preview during raw/video so we don't fight the SDK lock
        if mode in ("raw", "video"):
            self._grabber.set_paused(True)

        self._exp_thread = _ExperimentThread(
            runner=runner,
            name=self.name_edit.text(),
            positions=filtered_pos,
            labels=filtered_labels,
            delay=self.dwell_spin.value(),
            mode=mode,
            image_format=self.img_fmt_combo.currentText(),
            use_laser=self.use_laser_chk.isChecked(),
            pre_duration=self.duration_spin.value(),
            laser_on_duration=self.laser_on_spin.value(),
            post_duration=self.post_spin.value(),
        )
        self._exp_thread.status_update.connect(
            lambda msg: self.status_lbl.setText(f"Status: {msg}")
        )
        self._exp_thread.finished.connect(self._on_experiment_finished)
        self._exp_thread.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setEnabled(True)
        self.experiment_started.emit()

    def _stop_experiment(self):
        if self._exp_thread:
            self._exp_thread.stop()

    def _toggle_pause(self):
        if self._exp_thread is None:
            return
        runner = hw_state.get_runner()
        if runner and runner.paused:
            self._exp_thread.resume()
            self.pause_btn.setText("Pause")
        else:
            self._exp_thread.pause()
            self.pause_btn.setText("Resume")

    def _on_experiment_finished(self):
        self._grabber.set_paused(False)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self.status_lbl.setText("Status: Finished")
        self.experiment_finished.emit()
        self._exp_thread = None
