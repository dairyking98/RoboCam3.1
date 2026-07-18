"""
Manual Control Panel — direct hardware control outside an experiment.

Two-pane QSplitter
------------------
Left  : Live camera preview
Right : Scrollable controls — machine controls, jog, go-to,
        laser toggle, raw G-code sender
"""
from __future__ import annotations

import glob
import json
import os
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QLineEdit, QTextEdit,
    QButtonGroup, QRadioButton, QSplitter, QScrollArea,
    QSizePolicy, QComboBox, QMessageBox,
)
from PySide6.QtGui import QFont

import robocam.hw_state as hw_state
from robocam.calibration import WellPlate
from robocam.config import get_config
from robocam.session import session_manager
from ui.camera_widget import _FrameGrabber, _LivePreview

STEP_PRESETS = ["0.1", "0.5", "1.0", "5.0", "10.0"]

LASER_ON_S = 10.0
LASER_COOLDOWN_S = 20.0


def _load_calibration(path: str):
    """Load a calibration JSON and return (grid, cols, rows).

    ``grid[row][col]`` is ``(label, x, y, z)``. Handles both on-disk formats
    (CalibrationManager's precomputed path, and CalibrationPanel's raw
    corners) and un-snakes the stored order so row/col directions are
    consistent regardless of the pattern used to generate the plate.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("interpolated_positions") and data.get("labels"):
        positions = data["interpolated_positions"]
        labels = data["labels"]
        cols = int(data.get("cols", data.get("x_quantity", 12)))
        rows = int(data.get("rows", data.get("y_quantity", 8)))
        pattern = data.get("pattern", WellPlate.PATTERN_RASTER)
    else:
        corners_dict = data.get("corners", {})
        ul = corners_dict.get("Upper-Left") or data.get("upper_left")
        ll = corners_dict.get("Lower-Left") or data.get("lower_left")
        ur = corners_dict.get("Upper-Right") or data.get("upper_right")
        lr = corners_dict.get("Lower-Right") or data.get("lower_right")
        if not all([ul, ll, ur, lr]):
            raise ValueError("Calibration file is missing corner points.")
        cols = int(data.get("cols", data.get("x_quantity", 12)))
        rows = int(data.get("rows", data.get("y_quantity", 8)))
        pattern = data.get("pattern", WellPlate.PATTERN_RASTER)
        plate = WellPlate(cols, rows, [ul, ll, ur, lr], pattern)
        labeled = plate.get_path_with_labels()
        labels = [item[0] for item in labeled]
        positions = [item[1] for item in labeled]

    if len(positions) != cols * rows or len(labels) != cols * rows:
        raise ValueError("Calibration position/label count does not match grid size.")

    grid = [[None] * cols for _ in range(rows)]
    idx = 0
    for row_i in range(rows):
        col_order = range(cols)
        if pattern == WellPlate.PATTERN_SNAKE and row_i % 2 == 1:
            col_order = range(cols - 1, -1, -1)
        for col_j in col_order:
            x, y, z = positions[idx]
            grid[row_i][col_j] = (labels[idx], x, y, z)
            idx += 1

    return grid, cols, rows


def _nearest_well(grid, rows: int, cols: int, x: float, y: float) -> tuple[int, int]:
    """Return the (row, col) whose stored XY is closest to (x, y)."""
    best = (0, 0)
    best_dist = None
    for r in range(rows):
        for c in range(cols):
            _, wx, wy, _ = grid[r][c]
            dist = (wx - x) ** 2 + (wy - y) ** 2
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = (r, c)
    return best


def _get_or_init_laser(mc):
    from robocam.peripherals import LaserController
    laser = hw_state.get_laser()
    if laser is None:
        laser = LaserController(mc)
        laser.connect()
        hw_state.set_laser(laser)
    return laser


_NAV_BTN_STYLE = """
QPushButton {
    background-color: #333; color: white; font-size: 20px; font-weight: bold;
    border: 1px solid #555; border-radius: 6px; min-width: 56px; min-height: 56px;
}
QPushButton:hover { background-color: #444; }
QPushButton:pressed { background-color: #555; }
QPushButton:disabled { background-color: #222; color: #555; border-color: #333; }
"""

_LASER_IDLE_STYLE = """
QPushButton {
    background-color: #a23; color: white; font-size: 18px; font-weight: bold;
    border-radius: 8px; padding: 10px 24px;
}
QPushButton:hover { background-color: #c34; }
"""
_LASER_ACTIVE_STYLE = """
QPushButton {
    background-color: #444; color: #ccc; font-size: 18px; font-weight: bold;
    border-radius: 8px; padding: 10px 24px;
}
"""


class _DemoWindow(QWidget):
    """Standalone fullscreen booth/demo window: camera preview + well
    navigation + laser control. Esc exits, arrow keys move wells, space
    fires the laser; every control is also clickable on screen.
    """

    closed = Signal()

    def __init__(self, grabber: _FrameGrabber, grid=None, cols: int = 0, rows: int = 0, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("RoboCam — Demo Mode")
        self.setStyleSheet("background-color: black;")

        self._grid = grid
        self._cols = cols
        self._rows = rows
        self._row = 0
        self._col = 0

        mc = hw_state.get_motion()
        if self._grid is not None and mc and mc.is_connected and mc.X is not None and mc.Y is not None:
            self._row, self._col = _nearest_well(grid, rows, cols, mc.X, mc.Y)

        self._laser_state = "idle"  # idle | firing | cooldown
        self._laser_end_ts = 0.0
        self._laser_timer = QTimer(self)
        self._laser_timer.setInterval(200)
        self._laser_timer.timeout.connect(self._tick_laser)

        laser = hw_state.get_laser()
        if laser is not None and laser.get_laser_state():
            self._laser_state = "firing"
            self._laser_end_ts = time.monotonic() + LASER_ON_S
            self._laser_timer.start()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._grabber = grabber
        self._preview = _LivePreview(grabber)
        layout.addWidget(self._preview, stretch=1)

        layout.addWidget(self._build_control_bar())

        grabber.frame_ready.connect(self._preview.update_frame)
        grabber.camera_disconnected.connect(self._preview.show_disconnected)

        self._update_well_label()
        self._tick_laser()
        self.showFullScreen()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_control_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet("background-color: #111; border-top: 1px solid #333;")
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 12, 20, 12)
        row.setSpacing(24)

        self.well_lbl = QLabel("--")
        self.well_lbl.setStyleSheet("color: white; font-size: 32px; font-weight: bold;")
        self.well_lbl.setFixedWidth(120)
        row.addWidget(self.well_lbl)

        nav = QGridLayout()
        nav.setSpacing(4)
        up_btn    = QPushButton("▲")
        down_btn  = QPushButton("▼")
        left_btn  = QPushButton("◄")
        right_btn = QPushButton("►")
        for b in (up_btn, down_btn, left_btn, right_btn):
            b.setStyleSheet(_NAV_BTN_STYLE)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        up_btn.setToolTip("Move to well above (↑ arrow key)")
        down_btn.setToolTip("Move to well below (↓ arrow key)")
        left_btn.setToolTip("Move to well left (← arrow key)")
        right_btn.setToolTip("Move to well right (→ arrow key)")
        nav.addWidget(up_btn,    0, 1)
        nav.addWidget(left_btn,  1, 0)
        nav.addWidget(right_btn, 1, 2)
        nav.addWidget(down_btn,  2, 1)
        up_btn.clicked.connect(lambda: self._move_well(-1, 0))
        down_btn.clicked.connect(lambda: self._move_well(1, 0))
        left_btn.clicked.connect(lambda: self._move_well(0, -1))
        right_btn.clicked.connect(lambda: self._move_well(0, 1))
        self._nav_buttons = (up_btn, down_btn, left_btn, right_btn)
        row.addLayout(nav)

        nav_hint = QLabel("(or arrow keys)")
        nav_hint.setStyleSheet("color: #888; font-size: 10px;")
        row.addWidget(nav_hint)

        row.addStretch()

        self.laser_btn = QPushButton("Fire Laser (Space)")
        self.laser_btn.setStyleSheet(_LASER_IDLE_STYLE)
        self.laser_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.laser_btn.setToolTip("Fire the laser (spacebar also works)")
        self.laser_btn.clicked.connect(self._fire_laser)
        row.addWidget(self.laser_btn)

        if self._grid is None:
            self.well_lbl.setText("--")
            self.well_lbl.setToolTip("No calibration selected — well navigation disabled.")
            for b in self._nav_buttons:
                b.setEnabled(False)

        return bar

    def _update_well_label(self):
        if self._grid is None:
            return
        label, *_ = self._grid[self._row][self._col]
        self.well_lbl.setText(label)

    # ------------------------------------------------------------------
    # Well navigation
    # ------------------------------------------------------------------

    def _move_well(self, d_row: int, d_col: int):
        if self._grid is None:
            return
        new_row = self._row + d_row
        new_col = self._col + d_col
        if not (0 <= new_row < self._rows and 0 <= new_col < self._cols):
            return
        self._row, self._col = new_row, new_col
        self._update_well_label()

        mc = hw_state.get_motion()
        if not mc:
            return
        _, x, y, z = self._grid[self._row][self._col]

        def task():
            mc.move_absolute(X=x, Y=y, Z=z)
            mc.update_position()
        threading.Thread(target=task, daemon=True).start()

    # ------------------------------------------------------------------
    # Laser
    # ------------------------------------------------------------------

    def _fire_laser(self):
        if self._laser_state != "idle":
            return
        mc = hw_state.get_motion()
        try:
            laser = _get_or_init_laser(mc)
            laser.set_laser(True)
        except Exception:
            hw_state.set_laser(None)
            self.laser_btn.setText("Laser Error")
            return

        self._laser_state = "firing"
        self._laser_end_ts = time.monotonic() + LASER_ON_S
        self._laser_timer.start()
        self._tick_laser()

    def _tick_laser(self):
        remaining = self._laser_end_ts - time.monotonic()
        if remaining > 0:
            if self._laser_state == "firing":
                self.laser_btn.setText(f"Firing… {remaining:0.0f}s")
            else:
                self.laser_btn.setText(f"Cooldown… {remaining:0.0f}s")
            self.laser_btn.setStyleSheet(_LASER_ACTIVE_STYLE)
            return

        if self._laser_state == "firing":
            laser = hw_state.get_laser()
            if laser:
                try:
                    laser.set_laser(False)
                except Exception:
                    pass
            self._laser_state = "cooldown"
            self._laser_end_ts = time.monotonic() + LASER_COOLDOWN_S
            self._tick_laser()
        else:
            self._laser_state = "idle"
            self._laser_timer.stop()
            self.laser_btn.setText("Fire Laser (Space)")
            self.laser_btn.setStyleSheet(_LASER_IDLE_STYLE)

    # ------------------------------------------------------------------
    # Input / lifecycle
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Up:
            self._move_well(-1, 0)
        elif key == Qt.Key.Key_Down:
            self._move_well(1, 0)
        elif key == Qt.Key.Key_Left:
            self._move_well(0, -1)
        elif key == Qt.Key.Key_Right:
            self._move_well(0, 1)
        elif key == Qt.Key.Key_Space:
            self._fire_laser()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._laser_timer.stop()
        if self._laser_state != "idle":
            laser = hw_state.get_laser()
            if laser:
                try:
                    laser.set_laser(False)
                except Exception:
                    pass
        self._grabber.frame_ready.disconnect(self._preview.update_frame)
        self._grabber.camera_disconnected.disconnect(self._preview.show_disconnected)
        self.closed.emit()
        super().closeEvent(event)


class ManualControlPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left — live preview
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        hdr_row = QHBoxLayout()
        hdr = QLabel("Live Camera Preview")
        hdr.setStyleSheet("font-weight: bold; font-size: 11px;")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        hdr_row.addWidget(QLabel("Well Plate:"))
        self.cal_combo = QComboBox()
        self.cal_combo.setMinimumWidth(140)
        self.cal_combo.currentIndexChanged.connect(self._autosave_cal)
        hdr_row.addWidget(self.cal_combo)
        demo_btn = QPushButton("Demo Mode")
        demo_btn.setToolTip(
            "Fullscreen booth/demo view for events (e.g. Open Sauce): live camera, "
            "well-to-well navigation, and laser control. Press Esc to exit."
        )
        demo_btn.clicked.connect(self._enter_demo_mode)
        hdr_row.addWidget(demo_btn)
        ll.addLayout(hdr_row)
        self._grabber = _FrameGrabber(fps=15)
        self._preview = _LivePreview(self._grabber)
        ll.addWidget(self._preview, stretch=1)
        splitter.addWidget(left)

        # Right — scrollable controls
        right_inner = QWidget()
        right_layout = QVBoxLayout(right_inner)
        right_layout.setSpacing(6)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.addWidget(self._build_machine_group())
        right_layout.addWidget(self._build_jog_group())
        right_layout.addWidget(self._build_goto_group())
        right_layout.addWidget(self._build_laser_group())
        right_layout.addWidget(self._build_gcode_group())
        right_layout.addStretch()

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setWidget(right_inner)
        splitter.addWidget(right_scroll)

        splitter.setSizes([700, 400])
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

        self._grabber.frame_ready.connect(self._preview.update_frame)
        self._grabber.camera_disconnected.connect(self._preview.show_disconnected)
        self._grabber.start()

        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._refresh_position)
        self._pos_timer.start(500)

        self._demo_window: _DemoWindow | None = None
        self._refresh_cals()

    def closeEvent(self, event):
        self._grabber.stop()
        self._grabber.wait(1000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Demo mode
    # ------------------------------------------------------------------

    def _refresh_cals(self):
        cfg = get_config()
        cal_dir = cfg.get("paths.calibration_dir", "config/calibrations")
        files = [os.path.basename(f) for f in glob.glob(os.path.join(cal_dir, "*.json"))]
        current = self.cal_combo.currentText()
        self.cal_combo.blockSignals(True)
        self.cal_combo.clear()
        self.cal_combo.addItems(files)
        saved = session_manager.get("manual_control").get("cal_file", "")
        target = current if current in files else (saved if saved in files else "")
        if target:
            self.cal_combo.setCurrentText(target)
        self.cal_combo.blockSignals(False)

    def _autosave_cal(self):
        session_manager.update("manual_control", {"cal_file": self.cal_combo.currentText()})
        session_manager.save()

    def _enter_demo_mode(self):
        if self._demo_window is not None:
            return
        self._refresh_cals()

        grid = cols = rows = None
        cal_file = self.cal_combo.currentText()
        if cal_file:
            cfg = get_config()
            cal_path = os.path.join(cfg.get("paths.calibration_dir", "config/calibrations"), cal_file)
            try:
                grid, cols, rows = _load_calibration(cal_path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load calibration: {e}")
                return

        self._demo_window = _DemoWindow(self._grabber, grid, cols or 0, rows or 0)
        self._demo_window.closed.connect(self._exit_demo_mode)

    def _exit_demo_mode(self):
        self._demo_window = None

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _build_machine_group(self) -> QGroupBox:
        grp = QGroupBox("Machine Controls")
        layout = QVBoxLayout(grp)

        self.pos_lbl = QLabel("X: ---  Y: ---  Z: ---")
        self.pos_lbl.setStyleSheet("font-family: monospace; font-weight: bold;")
        layout.addWidget(self.pos_lbl)

        btn_row = QHBoxLayout()
        home_btn = QPushButton("Home All Axes")
        home_btn.clicked.connect(self._home)
        btn_row.addWidget(home_btn)

        dis_btn = QPushButton("Disable Steppers")
        dis_btn.setToolTip("Send M18 — cuts power to all stepper motors.")
        dis_btn.clicked.connect(self._disable_steppers)
        btn_row.addWidget(dis_btn)
        layout.addLayout(btn_row)

        return grp

    def _build_jog_group(self) -> QGroupBox:
        grp = QGroupBox("Jog Controls")
        layout = QVBoxLayout(grp)

        # Step sizes
        step_row = QHBoxLayout()
        self._step_group = QButtonGroup(self)
        for i, val in enumerate(STEP_PRESETS):
            rb = QRadioButton(val)
            self._step_group.addButton(rb, i)
            step_row.addWidget(rb)
            if val == "1.0":
                rb.setChecked(True)
        self._custom_rb = QRadioButton("Custom:")
        self._step_group.addButton(self._custom_rb, len(STEP_PRESETS))
        step_row.addWidget(self._custom_rb)
        self.step_input = QLineEdit("1.0")
        self.step_input.setFixedWidth(55)
        step_row.addWidget(self.step_input)
        self.step_input.textEdited.connect(lambda _: self._custom_rb.setChecked(True))
        layout.addLayout(step_row)

        # XY/Z pad
        grid = QGridLayout()
        y_plus  = QPushButton("Y+")
        x_minus = QPushButton("X-")
        x_plus  = QPushButton("X+")
        y_minus = QPushButton("Y-")
        z_plus  = QPushButton("Z+")
        z_minus = QPushButton("Z-")
        grid.addWidget(y_plus,  0, 1)
        grid.addWidget(x_minus, 1, 0)
        grid.addWidget(x_plus,  1, 2)
        grid.addWidget(y_minus, 2, 1)
        grid.addWidget(z_plus,  0, 3)
        grid.addWidget(z_minus, 2, 3)
        y_plus.clicked.connect(lambda:  self._jog("Y",  1))
        y_minus.clicked.connect(lambda: self._jog("Y", -1))
        x_plus.clicked.connect(lambda:  self._jog("X",  1))
        x_minus.clicked.connect(lambda: self._jog("X", -1))
        z_plus.clicked.connect(lambda:  self._jog("Z",  1))
        z_minus.clicked.connect(lambda: self._jog("Z", -1))
        layout.addLayout(grid)

        return grp

    def _build_goto_group(self) -> QGroupBox:
        grp = QGroupBox("Go To Position")
        layout = QHBoxLayout(grp)
        for axis, attr in [("X:", "goto_x"), ("Y:", "goto_y"), ("Z:", "goto_z")]:
            layout.addWidget(QLabel(axis))
            edit = QLineEdit("")
            edit.setFixedWidth(60)
            edit.setPlaceholderText("—")
            setattr(self, attr, edit)
            layout.addWidget(edit)
        go_btn = QPushButton("Go")
        go_btn.clicked.connect(self._goto)
        layout.addWidget(go_btn)
        return grp

    def _build_laser_group(self) -> QGroupBox:
        grp = QGroupBox("Laser Control")
        layout = QVBoxLayout(grp)

        btn_row = QHBoxLayout()
        self.laser_on_btn = QPushButton("Laser ON")
        self.laser_on_btn.clicked.connect(lambda: self._set_laser(True))
        btn_row.addWidget(self.laser_on_btn)
        self.laser_off_btn = QPushButton("Laser OFF")
        self.laser_off_btn.clicked.connect(lambda: self._set_laser(False))
        btn_row.addWidget(self.laser_off_btn)
        layout.addLayout(btn_row)

        self.laser_state_lbl = QLabel("Laser: OFF")
        self.laser_state_lbl.setStyleSheet("color: gray;")
        layout.addWidget(self.laser_state_lbl)

        return grp

    def _build_gcode_group(self) -> QGroupBox:
        grp = QGroupBox("Manual G-code Sender")
        layout = QVBoxLayout(grp)

        input_row = QHBoxLayout()
        self.gcode_input = QLineEdit()
        self.gcode_input.setPlaceholderText("G-code command…")
        self.gcode_input.returnPressed.connect(self._send_gcode)
        input_row.addWidget(self.gcode_input)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._send_gcode)
        input_row.addWidget(send_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self.gcode_log.clear())
        input_row.addWidget(clear_btn)
        layout.addLayout(input_row)

        self.gcode_log = QTextEdit()
        self.gcode_log.setReadOnly(True)
        mono = QFont("Courier New", 9)
        self.gcode_log.setFont(mono)
        self.gcode_log.setMinimumHeight(120)
        self.gcode_log.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.gcode_log)

        return grp

    # ------------------------------------------------------------------
    # Actions
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

    def _jog(self, axis: str, direction: int):
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
        if mc:
            threading.Thread(target=mc.home, daemon=True).start()

    def _disable_steppers(self):
        mc = hw_state.get_motion()
        if mc:
            threading.Thread(target=lambda: mc.send_raw("M18"), daemon=True).start()

    def _goto(self):
        mc = hw_state.get_motion()
        if not mc:
            return
        cur_x = mc.X or 0.0
        cur_y = mc.Y or 0.0
        cur_z = mc.Z or 0.0

        def _parse(text, fallback):
            s = text.strip()
            return float(s) if s else fallback

        try:
            x = _parse(self.goto_x.text(), cur_x)
            y = _parse(self.goto_y.text(), cur_y)
            z = _parse(self.goto_z.text(), cur_z)
        except ValueError:
            self.gcode_log.append("[Error] Invalid coordinate — enter numbers only.")
            return

        def task():
            mc.move_absolute(X=x, Y=y, Z=z)
            mc.update_position()
        threading.Thread(target=task, daemon=True).start()

    def _set_laser(self, state: bool):
        mc = hw_state.get_motion()
        try:
            laser = _get_or_init_laser(mc)
            laser.set_laser(state)
            label = "ON" if state else "OFF"
            color = "red" if state else "gray"
            self.laser_state_lbl.setText(f"Laser: {label}")
            self.laser_state_lbl.setStyleSheet(f"color: {color};")
        except Exception as e:
            hw_state.set_laser(None)  # force re-init on next press
            self.gcode_log.append(f"[Laser Error] {e}")

    def _send_gcode(self):
        cmd = self.gcode_input.text().strip()
        if not cmd:
            return
        self.gcode_input.clear()
        mc = hw_state.get_motion()
        if not mc or not mc.is_connected:
            self.gcode_log.append("[Error] Printer not connected.")
            return

        self.gcode_log.append(f">>> {cmd}")

        def task():
            try:
                response = mc.backend.send_gcode(cmd, ignore_errors=True)
                if response:
                    for line in response.splitlines():
                        self.gcode_log.append(f"    {line}")
            except Exception as e:
                self.gcode_log.append(f"[Error] {e}")
            self.gcode_log.append("")
            # Sync position if it was a move command
            if any(c in cmd.upper() for c in ("G0", "G1", "G28", "G92")):
                mc.update_position()

        threading.Thread(target=task, daemon=True).start()

    def _refresh_position(self):
        mc = hw_state.get_motion()
        if mc and mc.is_connected:
            self.pos_lbl.setText(
                f"X: {mc.X:.2f}  Y: {mc.Y:.2f}  Z: {mc.Z:.2f}"
            )
            self.pos_lbl.setStyleSheet("font-family: monospace; font-weight: bold; color: black;")
        else:
            self.pos_lbl.setText("X: ---  Y: ---  Z: ---")
            self.pos_lbl.setStyleSheet("font-family: monospace; color: gray;")
