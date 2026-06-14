"""
Calibration Panel — jog the stage, record corners, navigate wells.

Three-column QSplitter
----------------------
Col 1 (large)  : Live camera preview
Col 2 (medium) : Scrollable controls — jog, camera settings, corners,
                 plate dimensions, save/load, quick capture
Col 3 (medium) : Well map — compact clickable grid
"""
from __future__ import annotations

import json
import os
import time
import threading
from pathlib import Path
from typing import Optional

import cv2

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QSpinBox, QComboBox,
    QLineEdit, QButtonGroup, QRadioButton, QSplitter,
    QFileDialog, QMessageBox, QScrollArea, QSizePolicy,
    QDoubleSpinBox,
)
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor

from robocam.calibration import WellPlate
import robocam.hw_state as hw_state
from ui.camera_widget import _FrameGrabber, _LivePreview
from ui.well_grid import WellGrid

STEP_PRESETS = ["0.1", "0.5", "1.0", "5.0", "10.0"]
CORNER_NAMES = ["Upper-Left", "Lower-Left", "Upper-Right", "Lower-Right"]


def _default_cal_dir() -> Path:
    return Path.home() / "Documents" / "RoboCam" / "calibrations"


# ---------------------------------------------------------------------------
# Well map widget
# ---------------------------------------------------------------------------

class WellMapWidget(QGroupBox):
    well_clicked = Signal(float, float, float)

    def __init__(self, parent=None):
        super().__init__("Well Map  (click to go to well)", parent)
        self._positions: list[tuple[float, float, float]] = []
        self._rows = 0
        self._cols = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        self._placeholder = QLabel("Set all four corners\nor load a calibration\nto build the map.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: gray; font-size: 10px;")
        outer.addWidget(self._placeholder, stretch=1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.hide()
        outer.addWidget(self._scroll, stretch=1)

        self._grid: Optional[WellGrid] = None

    def build(self, rows: int, cols: int, positions: list):
        self._rows, self._cols, self._positions = rows, cols, positions
        self._placeholder.hide()

        if self._grid is not None:
            self._grid.well_clicked.disconnect()
            self._grid.deleteLater()

        self._grid = WellGrid(rows=rows, cols=cols, mode=WellGrid.Mode.NAVIGATE)
        self._grid.well_clicked.connect(self._on_cell_clicked)
        self._scroll.setWidget(self._grid)
        self._scroll.show()

    def clear(self):
        if self._grid is not None:
            self._grid.well_clicked.disconnect()
            self._grid.deleteLater()
            self._grid = None
        self._positions = []
        self._scroll.setWidget(QWidget())
        self._scroll.hide()
        self._placeholder.show()

    def _on_cell_clicked(self, row: int, col: int):
        idx = row * self._cols + col
        if 0 <= idx < len(self._positions):
            x, y, z = self._positions[idx]
            self.well_clicked.emit(x, y, z)


# ---------------------------------------------------------------------------
# CalibrationPanel
# ---------------------------------------------------------------------------

class CalibrationPanel(QWidget):
    corners_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

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

        # Col 2 — scrollable controls
        col2_inner = QWidget()
        col2_layout = QVBoxLayout(col2_inner)
        col2_layout.setSpacing(6)
        col2_layout.setContentsMargins(4, 4, 4, 4)
        col2_layout.addWidget(self._build_movement_group())
        col2_layout.addWidget(self._build_camera_controls_group())
        col2_layout.addWidget(self._build_calibration_group())
        col2_layout.addWidget(self._build_dimensions_group())
        col2_layout.addWidget(self._build_save_load_group())
        col2_layout.addWidget(self._build_quick_capture_group())
        col2_layout.addStretch()

        col2_scroll = QScrollArea()
        col2_scroll.setWidgetResizable(True)
        col2_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        col2_scroll.setWidget(col2_inner)
        splitter.addWidget(col2_scroll)

        # Col 3 — well map
        col3 = QWidget()
        c3l = QVBoxLayout(col3)
        c3l.setContentsMargins(4, 4, 4, 4)
        self.well_map = WellMapWidget()
        self.well_map.well_clicked.connect(self._goto_xyz)
        c3l.addWidget(self.well_map, stretch=1)
        splitter.addWidget(col3)

        splitter.setSizes([540, 360, 300])
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setCollapsible(0, False)
        col1.setMinimumWidth(380)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

        # Wire grabber
        self._grabber.frame_ready.connect(self._preview.update_frame)
        self._grabber.camera_disconnected.connect(self._preview.show_disconnected)
        self._grabber.start()

        # Position poll
        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._update_position_display)
        self._pos_timer.start(500)

    def closeEvent(self, event):
        self._grabber.stop()
        self._grabber.wait(1000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _build_movement_group(self) -> QGroupBox:
        grp = QGroupBox("Movement Controls")
        layout = QGridLayout(grp)
        layout.setSpacing(4)

        # Position display
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("X:"))
        self.x_pos_lbl = QLabel("0.00")
        self.x_pos_lbl.setMinimumWidth(48)
        pos_row.addWidget(self.x_pos_lbl)
        pos_row.addWidget(QLabel("Y:"))
        self.y_pos_lbl = QLabel("0.00")
        self.y_pos_lbl.setMinimumWidth(48)
        pos_row.addWidget(self.y_pos_lbl)
        pos_row.addWidget(QLabel("Z:"))
        self.z_pos_lbl = QLabel("0.00")
        self.z_pos_lbl.setMinimumWidth(48)
        pos_row.addWidget(self.z_pos_lbl)
        pos_row.addStretch()
        layout.addLayout(pos_row, 0, 0, 1, 5)

        # XY jog pad
        self.y_plus_btn  = QPushButton("Y+")
        self.x_minus_btn = QPushButton("X-")
        self.home_btn    = QPushButton("Home")
        self.x_plus_btn  = QPushButton("X+")
        self.y_minus_btn = QPushButton("Y-")
        layout.addWidget(self.y_plus_btn,  1, 1)
        layout.addWidget(self.x_minus_btn, 2, 0)
        layout.addWidget(self.home_btn,    2, 1)
        layout.addWidget(self.x_plus_btn,  2, 2)
        layout.addWidget(self.y_minus_btn, 3, 1)

        self.z_plus_btn  = QPushButton("Z+")
        self.z_minus_btn = QPushButton("Z-")
        layout.addWidget(self.z_plus_btn,  1, 3)
        layout.addWidget(self.z_minus_btn, 3, 3)

        # Step size
        step_grp = QGroupBox("Step Size (mm)")
        step_layout = QHBoxLayout(step_grp)
        self._step_group = QButtonGroup(self)
        for i, val in enumerate(STEP_PRESETS):
            rb = QRadioButton(val)
            self._step_group.addButton(rb, i)
            step_layout.addWidget(rb)
            if val == "1.0":
                rb.setChecked(True)
        self._custom_rb = QRadioButton("Custom:")
        self._step_group.addButton(self._custom_rb, len(STEP_PRESETS))
        step_layout.addWidget(self._custom_rb)
        self.step_input = QLineEdit("1.0")
        self.step_input.setFixedWidth(55)
        step_layout.addWidget(self.step_input)
        self._step_group.buttonClicked.connect(
            lambda btn: self.step_input.setText(btn.text()) if btn != self._custom_rb else None
        )
        self.step_input.textEdited.connect(lambda _: self._custom_rb.setChecked(True))
        layout.addWidget(step_grp, 4, 0, 1, 5)

        # Go To XYZ
        goto_grp = QGroupBox("Go To Position")
        goto_layout = QHBoxLayout(goto_grp)
        for axis, attr in [("X:", "goto_x"), ("Y:", "goto_y"), ("Z:", "goto_z")]:
            goto_layout.addWidget(QLabel(axis))
            edit = QLineEdit("0.0")
            edit.setFixedWidth(55)
            setattr(self, attr, edit)
            goto_layout.addWidget(edit)
        self.goto_btn = QPushButton("Go")
        self.goto_btn.setFixedWidth(40)
        self.goto_btn.clicked.connect(self._goto_position)
        goto_layout.addWidget(self.goto_btn)
        layout.addWidget(goto_grp, 5, 0, 1, 5)

        # Wire jog
        self.y_plus_btn.clicked.connect(lambda: self._move("y",  1))
        self.y_minus_btn.clicked.connect(lambda: self._move("y", -1))
        self.x_plus_btn.clicked.connect(lambda: self._move("x",  1))
        self.x_minus_btn.clicked.connect(lambda: self._move("x", -1))
        self.z_plus_btn.clicked.connect(lambda: self._move("z",  1))
        self.z_minus_btn.clicked.connect(lambda: self._move("z", -1))
        self.home_btn.clicked.connect(self._home)

        return grp

    def _build_camera_controls_group(self) -> QGroupBox:
        grp = QGroupBox("Camera Controls")
        layout = QGridLayout(grp)

        layout.addWidget(QLabel("Exposure:"), 0, 0)
        self.exp_spin = QSpinBox()
        self.exp_spin.setRange(1, 2000)
        self.exp_spin.setSingleStep(10)
        self.exp_spin.setSuffix(" ms")
        self.exp_spin.setValue(20)
        layout.addWidget(self.exp_spin, 0, 1)

        layout.addWidget(QLabel("Gain:"), 1, 0)
        self.gain_spin = QSpinBox()
        self.gain_spin.setRange(0, 500)
        self.gain_spin.setSingleStep(10)
        self.gain_spin.setValue(100)
        layout.addWidget(self.gain_spin, 1, 1)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._apply_camera_controls)
        layout.addWidget(apply_btn, 2, 0)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_camera_controls)
        layout.addWidget(refresh_btn, 2, 1)

        return grp

    def _build_calibration_group(self) -> QGroupBox:
        grp = QGroupBox("Well-Plate Corner Calibration")
        layout = QGridLayout(grp)
        layout.setSpacing(4)

        # Spatial 2×2: UL/UR top, LL/LR bottom
        POSITIONS = {
            "Upper-Left":  (0, 0),
            "Upper-Right": (0, 1),
            "Lower-Left":  (1, 0),
            "Lower-Right": (1, 1),
        }

        self.corners: dict = {}
        for name, (plate_row, plate_col) in POSITIONS.items():
            grid_row = plate_row * 2
            grid_col = plate_col * 2

            hdr = QHBoxLayout()
            hdr.addWidget(QLabel(f"{name}:"))
            pos_lbl = QLabel("Not Set")
            pos_lbl.setStyleSheet("color: gray;")
            hdr.addWidget(pos_lbl)
            hdr.addStretch()
            layout.addLayout(hdr, grid_row, grid_col, 1, 2)

            set_btn = QPushButton(f"Set {name}")
            layout.addWidget(set_btn, grid_row + 1, grid_col, 1, 2)
            self.corners[name] = {"label": pos_lbl, "button": set_btn, "position": None}
            set_btn.clicked.connect(lambda checked=False, n=name: self._set_corner(n))

        return grp

    def _build_dimensions_group(self) -> QGroupBox:
        grp = QGroupBox("Plate Dimensions")
        layout = QGridLayout(grp)

        layout.addWidget(QLabel("Columns (X):"), 0, 0)
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, 48)
        self.cols_spin.setValue(12)
        layout.addWidget(self.cols_spin, 0, 1)

        layout.addWidget(QLabel("Rows (Y):"), 1, 0)
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 32)
        self.rows_spin.setValue(8)
        layout.addWidget(self.rows_spin, 1, 1)

        layout.addWidget(QLabel("Pattern:"), 2, 0)
        self.pattern_combo = QComboBox()
        self.pattern_combo.addItems([WellPlate.PATTERN_RASTER, WellPlate.PATTERN_SNAKE])
        layout.addWidget(self.pattern_combo, 2, 1)

        layout.addWidget(QLabel("Name:"), 3, 0)
        self.cal_name_edit = QLineEdit("calibration")
        layout.addWidget(self.cal_name_edit, 3, 1)

        update_btn = QPushButton("Update Well Map")
        update_btn.clicked.connect(self._on_update_well_map)
        layout.addWidget(update_btn, 4, 0, 1, 2)

        return grp

    def _build_save_load_group(self) -> QGroupBox:
        grp = QGroupBox("Calibration File")
        root = QVBoxLayout(grp)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Update && Save…")
        save_btn.clicked.connect(self._save_calibration)
        btn_row.addWidget(save_btn)
        load_btn = QPushButton("Load…")
        load_btn.clicked.connect(self._load_calibration)
        btn_row.addWidget(load_btn)
        root.addLayout(btn_row)

        self._cal_dir_lbl = QLabel(str(_default_cal_dir()))
        self._cal_dir_lbl.setStyleSheet("font-size: 10px; color: #888; font-style: italic;")
        root.addWidget(self._cal_dir_lbl)

        self._cal_status_lbl = QLabel("")
        self._cal_status_lbl.setStyleSheet("font-size: 10px; color: green;")
        root.addWidget(self._cal_status_lbl)

        return grp

    def _build_quick_capture_group(self) -> QGroupBox:
        grp = QGroupBox("Quick Capture")
        layout = QGridLayout(grp)

        layout.addWidget(QLabel("Format:"), 0, 0)
        self.qc_fmt_combo = QComboBox()
        self.qc_fmt_combo.addItems(["jpg", "png", "tif"])
        layout.addWidget(self.qc_fmt_combo, 0, 1)

        capture_btn = QPushButton("Capture Image")
        capture_btn.clicked.connect(self._quick_capture_image)
        layout.addWidget(capture_btn, 1, 0, 1, 2)

        layout.addWidget(QLabel("Video (s):"), 2, 0)
        self.qc_video_spin = QDoubleSpinBox()
        self.qc_video_spin.setRange(0.1, 300.0)
        self.qc_video_spin.setValue(5.0)
        self.qc_video_spin.setSingleStep(1.0)
        layout.addWidget(self.qc_video_spin, 2, 1)

        record_btn = QPushButton("Record Video")
        record_btn.clicked.connect(self._quick_record_video)
        layout.addWidget(record_btn, 3, 0, 1, 2)

        self.qc_status_lbl = QLabel("Ready")
        self.qc_status_lbl.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self.qc_status_lbl, 4, 0, 1, 2)

        return grp

    # ------------------------------------------------------------------
    # Refreshers (called from MainWindow on camera_connected signal)
    # ------------------------------------------------------------------

    def _refresh_camera_controls(self):
        cam = hw_state.get_camera()
        if cam and cam.running:
            try:
                self.exp_spin.setValue(int(cam.get_exposure() / 1000))
                self.gain_spin.setValue(int(cam.get_gain()))
            except Exception:
                pass

    def _apply_camera_controls(self):
        cam = hw_state.get_camera()
        if cam and cam.running:
            try:
                cam.set_exposure(self.exp_spin.value() * 1000)
                cam.set_gain(self.gain_spin.value())
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Movement actions
    # ------------------------------------------------------------------

    def _get_step(self) -> float:
        btn = self._step_group.checkedButton()
        if btn and btn != self._custom_rb:
            try:
                return float(btn.text())
            except ValueError:
                pass
        try:
            return float(self.step_input.text())
        except ValueError:
            return 1.0

    def _move(self, axis: str, direction: int):
        mc = hw_state.get_motion()
        if not mc:
            return
        step = self._get_step()
        def task():
            mc.move_relative(**{axis: direction * step})
            mc.update_position()
        threading.Thread(target=task, daemon=True).start()

    def _home(self):
        mc = hw_state.get_motion()
        if not mc:
            return
        threading.Thread(target=mc.home, daemon=True).start()

    def _goto_position(self):
        mc = hw_state.get_motion()
        if not mc:
            return
        try:
            x = float(self.goto_x.text()) if self.goto_x.text().strip() else mc.X or 0.0
            y = float(self.goto_y.text()) if self.goto_y.text().strip() else mc.Y or 0.0
            z = float(self.goto_z.text()) if self.goto_z.text().strip() else mc.Z or 0.0
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Enter valid numeric coordinates.")
            return
        self._goto_xyz(x, y, z)

    def _goto_xyz(self, x: float, y: float, z: float):
        mc = hw_state.get_motion()
        if not mc:
            return
        def task():
            mc.move_absolute(X=x, Y=y, Z=z)
            mc.update_position()
        threading.Thread(target=task, daemon=True).start()

    def _update_position_display(self):
        mc = hw_state.get_motion()
        if mc and mc.is_connected:
            self.x_pos_lbl.setText(f"{mc.X:.2f}")
            self.y_pos_lbl.setText(f"{mc.Y:.2f}")
            self.z_pos_lbl.setText(f"{mc.Z:.2f}")

    # ------------------------------------------------------------------
    # Corner calibration
    # ------------------------------------------------------------------

    def _set_corner(self, name: str):
        mc = hw_state.get_motion()
        if not mc or not mc.is_connected:
            QMessageBox.warning(self, "Not Connected", "Connect and home the printer first.")
            return
        try:
            pos = mc.update_position()
            self.corners[name]["position"] = list(pos)
            self.corners[name]["label"].setText(
                f"X:{pos[0]:.2f}  Y:{pos[1]:.2f}  Z:{pos[2]:.2f}"
            )
            self.corners[name]["label"].setStyleSheet("color: green;")
            self._try_auto_generate_well_map()
            self.corners_changed.emit()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _try_auto_generate_well_map(self):
        if all(self.corners[n]["position"] is not None for n in CORNER_NAMES):
            self._generate_well_map()

    def _on_update_well_map(self):
        missing = [n for n in CORNER_NAMES if self.corners[n]["position"] is None]
        if missing:
            self._cal_status_lbl.setText(f"Missing corners: {', '.join(missing)}")
            self._cal_status_lbl.setStyleSheet("font-size: 10px; color: red;")
            return
        self._generate_well_map()
        self._cal_status_lbl.setText(
            f"Map updated: {self.rows_spin.value()} × {self.cols_spin.value()}"
        )
        self._cal_status_lbl.setStyleSheet("font-size: 10px; color: green;")
        self.corners_changed.emit()

    def _compute_well_positions(self) -> Optional[list]:
        positions = [self.corners[n]["position"] for n in CORNER_NAMES]
        if any(p is None for p in positions):
            return None
        cols = self.cols_spin.value()
        rows = self.rows_spin.value()
        ul, ll, ur, lr = positions
        result = []
        for row_i in range(rows):
            for col_j in range(cols):
                u = col_j / (cols - 1) if cols > 1 else 0.0
                v = row_i / (rows - 1) if rows > 1 else 0.0
                top = [ul[i] + u * (ur[i] - ul[i]) for i in range(3)]
                bot = [ll[i] + u * (lr[i] - ll[i]) for i in range(3)]
                result.append(tuple(top[i] + v * (bot[i] - top[i]) for i in range(3)))
        return result

    def _generate_well_map(self):
        positions = self._compute_well_positions()
        if positions:
            self.well_map.build(
                rows=self.rows_spin.value(),
                cols=self.cols_spin.value(),
                positions=positions,
            )

    def has_well_map(self) -> bool:
        return self.well_map._grid is not None

    def get_corners(self) -> dict:
        return {n: self.corners[n]["position"] for n in CORNER_NAMES}

    def get_well_dimensions(self) -> tuple[int, int]:
        return self.cols_spin.value(), self.rows_spin.value()

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------

    def _get_cal_dir(self) -> Path:
        d = getattr(self, "_cal_dir", _default_cal_dir())
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_calibration(self):
        corners = {n: self.corners[n]["position"] for n in CORNER_NAMES}
        if any(v is None for v in corners.values()):
            QMessageBox.warning(self, "Incomplete", "Set all four corners first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Calibration",
            str(self._get_cal_dir() / f"{self.cal_name_edit.text()}.json"),
            "JSON Files (*.json)",
        )
        if not path:
            return
        data = {
            "corners": corners,
            "cols": self.cols_spin.value(),
            "rows": self.rows_spin.value(),
            "pattern": self.pattern_combo.currentText(),
            "name": self.cal_name_edit.text(),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._cal_status_lbl.setText(f"Saved: {Path(path).name}")
            self._cal_status_lbl.setStyleSheet("font-size: 10px; color: green;")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _load_calibration(self, path: Optional[str] = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Load Calibration",
                str(self._get_cal_dir()),
                "JSON Files (*.json)",
            )
            if not path:
                return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return

        corners = data.get("corners", {})
        for name in CORNER_NAMES:
            pos = corners.get(name)
            if pos is not None:
                self.corners[name]["position"] = pos
                self.corners[name]["label"].setText(
                    f"X:{pos[0]:.2f}  Y:{pos[1]:.2f}  Z:{pos[2]:.2f}"
                )
                self.corners[name]["label"].setStyleSheet("color: green;")

        self.cols_spin.setValue(data.get("cols", 12))
        self.rows_spin.setValue(data.get("rows", 8))
        pat = data.get("pattern", WellPlate.PATTERN_RASTER)
        idx = self.pattern_combo.findText(pat)
        if idx >= 0:
            self.pattern_combo.setCurrentIndex(idx)
        if data.get("name"):
            self.cal_name_edit.setText(data["name"])

        self._generate_well_map()
        self._cal_status_lbl.setText(f"Loaded: {Path(path).name}")
        self._cal_status_lbl.setStyleSheet("font-size: 10px; color: green;")
        self.corners_changed.emit()

    # ------------------------------------------------------------------
    # Quick capture
    # ------------------------------------------------------------------

    def _quick_capture_image(self):
        cam = hw_state.get_camera()
        if not cam or not cam.running:
            QMessageBox.warning(self, "No Camera", "Camera not connected.")
            return
        fmt = self.qc_fmt_combo.currentText()
        out_dir = Path("outputs") / "quick_capture"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"quick_{ts}.{fmt}"
        frame = cam.get_frame()
        if frame is None:
            QMessageBox.warning(self, "Error", "Could not read a frame.")
            return
        if cam.backend == "picamera2":
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(path), frame)
        self.qc_status_lbl.setText(path.name)

    def _quick_record_video(self):
        cam = hw_state.get_camera()
        if not cam or not cam.running:
            QMessageBox.warning(self, "No Camera", "Camera not connected.")
            return
        duration = self.qc_video_spin.value()
        out_dir = Path("outputs") / "quick_capture"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"quick_{ts}.avi"

        self.qc_status_lbl.setText("Recording…")

        def task():
            runner = hw_state.get_runner()
            if runner is None:
                from robocam.experiment import ExperimentRunner
                mc = hw_state.get_motion()
                runner = ExperimentRunner(mc, cam)
            runner.running = True
            try:
                runner._write_video(str(path), duration)
            finally:
                runner.running = False
            from PySide6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(
                self.qc_status_lbl, "setText",
                Qt.ConnectionType.QueuedConnection,
                path.name,
            )

        threading.Thread(target=task, daemon=True).start()
