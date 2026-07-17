"""
Manual Control Panel — direct hardware control outside an experiment.

Two-pane QSplitter
------------------
Left  : Live camera preview
Right : Scrollable controls — machine controls, jog, go-to,
        laser toggle, raw G-code sender
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QLineEdit, QTextEdit,
    QButtonGroup, QRadioButton, QSplitter, QScrollArea,
    QSizePolicy,
)
from PySide6.QtGui import QFont

import robocam.hw_state as hw_state
from ui.camera_widget import _FrameGrabber, _LivePreview

STEP_PRESETS = ["0.1", "0.5", "1.0", "5.0", "10.0"]


class _DemoWindow(QWidget):
    """Standalone fullscreen camera-only window for booth/demo use. Esc exits."""

    closed = Signal()

    def __init__(self, grabber: _FrameGrabber, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("RoboCam — Demo Mode")
        self.setStyleSheet("background-color: black;")
        self.setCursor(Qt.CursorShape.BlankCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._grabber = grabber
        self._preview = _LivePreview(grabber)
        layout.addWidget(self._preview)

        grabber.frame_ready.connect(self._preview.update_frame)
        grabber.camera_disconnected.connect(self._preview.show_disconnected)

        self.showFullScreen()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
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
        demo_btn = QPushButton("Demo Mode")
        demo_btn.setToolTip(
            "Fullscreen camera-only view for showing off at events (e.g. Open Sauce). "
            "Press Esc to exit."
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

    def closeEvent(self, event):
        self._grabber.stop()
        self._grabber.wait(1000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Demo mode
    # ------------------------------------------------------------------

    def _enter_demo_mode(self):
        if self._demo_window is not None:
            return
        self._demo_window = _DemoWindow(self._grabber)
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
        from robocam.peripherals import LaserController
        mc = hw_state.get_motion()
        try:
            laser = hw_state.get_laser()
            if laser is None:
                laser = LaserController(mc)
                laser.connect()
                hw_state.set_laser(laser)
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
