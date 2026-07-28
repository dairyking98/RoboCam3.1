"""
ProfileSliderRow — a labelled slider row with a default-value marker.

Ported from RoboCam-Suite 2.0's ui/profile_slider.py. The X/Y "link"
variant (ProfileSliderPair) is dropped here — RoboCam3.1's stage always
drives X and Y together, so a single XY row is used instead of two rows
plus a checkbox.

Row layout: Label | [====|====] slider | value spinbox | (default: N.NN)
The slider uses integer ticks internally (resolution = 1/step) so Qt's
integer-only QSlider can represent floats. A small triangle marker is
painted on the slider groove at the position of the default value.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel,
    QSlider, QDoubleSpinBox, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QPainter, QColor, QPolygon


class _MarkedSlider(QSlider):
    """QSlider that paints a small triangle at the default-value tick."""

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._default_tick: Optional[int] = None

    def set_default_tick(self, tick: int):
        self._default_tick = tick
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._default_tick is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        lo, hi = self.minimum(), self.maximum()
        if hi == lo:
            return
        frac = (self._default_tick - lo) / (hi - lo)

        margin = 8
        groove_w = self.width() - 2 * margin
        x = int(margin + frac * groove_w)
        cy = self.height() // 2

        size = 5
        pts = QPolygon([
            QPoint(x,        cy - size),
            QPoint(x - size, cy - size * 2),
            QPoint(x + size, cy - size * 2),
        ])
        painter.setBrush(QColor(255, 140, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(pts)
        painter.end()


class ProfileSliderRow(QWidget):
    """
    One labelled slider row.

    Signals
    -------
    value_changed(float)
    """
    value_changed = Signal(float)

    def __init__(
        self,
        label: str,
        lo: float,
        hi: float,
        step: float = 1.0,
        decimals: int = 1,
        suffix: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._lo = lo
        self._hi = hi
        self._step = step
        self._decimals = decimals
        self._resolution = max(1, int(round(1.0 / step))) if step < 1 else 1
        self._blocking = False  # re-entrancy guard

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        lbl = QLabel(label)
        lbl.setFixedWidth(32)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(lbl)

        self._slider = _MarkedSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(int(lo * self._resolution), int(hi * self._resolution))
        self._slider.setSingleStep(max(1, int(step * self._resolution)))
        self._slider.setTickInterval(max(1, int(step * self._resolution * 10)))
        self._slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._slider, stretch=1)

        self._spin = QDoubleSpinBox()
        self._spin.setRange(lo, hi)
        self._spin.setSingleStep(step)
        self._spin.setDecimals(decimals)
        self._spin.setSuffix(suffix)
        self._spin.setFixedWidth(90)
        layout.addWidget(self._spin)

        self._default_lbl = QLabel("")
        self._default_lbl.setStyleSheet("color: #888; font-size: 9px;")
        self._default_lbl.setFixedWidth(80)
        layout.addWidget(self._default_lbl)

        self._slider.valueChanged.connect(self._on_slider_changed)
        self._spin.valueChanged.connect(self._on_spin_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_value(self, v: float):
        self._blocking = True
        clamped = max(self._lo, min(self._hi, v))
        self._spin.setValue(clamped)
        self._slider.setValue(int(round(clamped * self._resolution)))
        self._blocking = False

    def set_default(self, v: float):
        """Mark v as the default (orange triangle + label)."""
        clamped = max(self._lo, min(self._hi, v))
        tick = int(round(clamped * self._resolution))
        self._slider.set_default_tick(tick)
        fmt = f"{clamped:.{self._decimals}f}"
        self._default_lbl.setText(f"default: {fmt}")

    def value(self) -> float:
        return self._spin.value()

    def setEnabled(self, enabled: bool):
        super().setEnabled(enabled)
        self._slider.setEnabled(enabled)
        self._spin.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_slider_changed(self, tick: int):
        if self._blocking:
            return
        v = tick / self._resolution
        self._blocking = True
        self._spin.setValue(v)
        self._blocking = False
        self.value_changed.emit(v)

    def _on_spin_changed(self, v: float):
        if self._blocking:
            return
        self._blocking = True
        self._slider.setValue(int(round(v * self._resolution)))
        self._blocking = False
        self.value_changed.emit(v)
