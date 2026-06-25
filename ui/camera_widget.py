"""
camera_widget.py — shared live-preview components.

Used by CalibrationPanel, ExperimentPanel, and ManualControlPanel.
Each panel creates its own _FrameGrabber + _LivePreview pair so tabs
are fully independent.
"""
from __future__ import annotations

import cv2
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import QWidget

from robocam.hw_state import get_camera


class _FrameGrabber(QThread):
    """Polls the camera at ~15 fps and emits QImage frames."""

    frame_ready = Signal(QImage)
    camera_disconnected = Signal()

    def __init__(self, fps: int = 15, parent=None):
        super().__init__(parent)
        self._fps = fps
        self._running = False
        self._paused = False
        self._was_connected = False

    def stop(self):
        self._running = False

    def set_paused(self, paused: bool):
        self._paused = paused

    def run(self):
        self._running = True
        interval_ms = max(1, int(1000 / self._fps))

        while self._running:
            if self._paused:
                self.msleep(100)
                continue

            try:
                camera = get_camera()
                if camera and camera.running:
                    self._was_connected = True
                    frame = camera.get_frame()
                    if frame is not None:
                        # get_frame() returns BGR; convert to RGB for Qt
                        if getattr(camera, "backend", None) == "picamera2":
                            rgb = frame  # picamera2 already returns RGB
                        else:
                            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        h, w, ch = rgb.shape
                        qimg = QImage(
                            rgb.data.tobytes(), w, h, ch * w,
                            QImage.Format.Format_RGB888,
                        )
                        self.frame_ready.emit(qimg.copy())
                else:
                    if self._was_connected:
                        self.camera_disconnected.emit()
                        self._was_connected = False
            except Exception:
                pass

            self.msleep(interval_ms)


class _LivePreview(QWidget):
    """
    Displays live camera frames.  Shows a dark 'Camera Offline' screen
    when no frames are arriving, a red 'RECORDING' overlay when the
    grabber is paused mid-capture, and an amber 'EXPERIMENT IN PROGRESS'
    overlay when an experiment is running.
    """

    def __init__(self, grabber: _FrameGrabber, parent=None):
        super().__init__(parent)
        self._grabber = grabber
        self._pixmap: Optional[QPixmap] = None
        self._offline = True
        self._experiment_running = False
        self.setMinimumSize(320, 240)

    def update_frame(self, qimg: QImage):
        self._pixmap = QPixmap.fromImage(qimg)
        self._offline = False
        self.update()

    def show_disconnected(self):
        self._offline = True
        self._pixmap = None
        self.update()

    def set_experiment_running(self, running: bool):
        self._experiment_running = running
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.width(), self.height()

        if self._offline or self._pixmap is None:
            painter.fillRect(0, 0, w, h, QColor(30, 30, 30))
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter, "Camera Offline")
        else:
            scaled = self._pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x_off = (w - scaled.width()) // 2
            y_off = (h - scaled.height()) // 2
            painter.drawPixmap(x_off, y_off, scaled)

        if self._experiment_running:
            painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 170))
            painter.setPen(QColor(255, 180, 0))
            font = QFont()
            font.setBold(True)
            font.setPointSize(22)
            painter.setFont(font)
            painter.drawText(
                0, 0, w, h,
                Qt.AlignmentFlag.AlignCenter,
                "EXPERIMENT IN PROGRESS\nPreview Paused",
            )
        elif self._grabber._paused:
            painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 160))
            painter.setPen(QColor(255, 50, 50))
            font = QFont()
            font.setBold(True)
            font.setPointSize(24)
            painter.setFont(font)
            painter.drawText(
                0, 0, w, h,
                Qt.AlignmentFlag.AlignCenter,
                "● RECORDING\n(Preview Paused)",
            )

        painter.end()
