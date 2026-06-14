"""
well_grid.py — shared well-plate grid widget used by both the Calibration
and Experiment panels.

Design goals
------------
* A single custom-painted QWidget owns the entire grid.  No individual
  QPushButton children are created, which eliminates the drag-detection
  problem that arises when Qt routes mouse events to the widget that
  received the initial press rather than to widgets the cursor moves over.
* No row/column axis header labels — every cell is already labelled
  (A1, B3, etc.) so the headers are redundant.
* Consistent visual style across both panels.
* Efficient: one paintEvent redraws all cells; no per-cell stylesheet
  recalculations.

Two modes
---------
WellGrid.Mode.NAVIGATE
    Used by the Calibration panel.  Clicking a cell emits ``well_clicked``
    with the (row, col) of the cell.  No selection state is maintained.

WellGrid.Mode.SELECT
    Used by the Experiment panel.  Cells have a selected/deselected state.
    Click-and-drag paints all cells the cursor passes over to the target
    state determined by the first cell touched in the stroke.
    Emits ``selection_changed`` whenever the selection changes.
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Optional

from PySide6.QtCore import Qt, Signal, QRect, QPoint, QSize
from PySide6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QPen, QMouseEvent,
)
from PySide6.QtWidgets import QWidget, QSizePolicy


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

_COL_SEL        = QColor("#2a7ae2")   # selected well background
_COL_SEL_HOVER  = QColor("#1a5ab2")   # selected well hovered
_COL_DESEL      = QColor("#555555")   # deselected well background
_COL_DESEL_HOVER= QColor("#6a6a6a")   # deselected well hovered
_COL_NAV        = QColor("#3a3a3a")   # navigate-mode well background
_COL_NAV_HOVER  = QColor("#2a7ae2")   # navigate-mode well hovered
_COL_TEXT_SEL   = QColor("#ffffff")
_COL_TEXT_DESEL = QColor("#aaaaaa")
_COL_BORDER_SEL = QColor("#1a5ab2")
_COL_BORDER_DESEL = QColor("#333333")
_COL_BG         = QColor("#f0f0f0")   # widget background


# ---------------------------------------------------------------------------
# WellGrid
# ---------------------------------------------------------------------------

class WellGrid(QWidget):
    """
    Custom-painted well-plate grid.

    Parameters
    ----------
    rows, cols : int
        Initial plate dimensions.
    mode : WellGrid.Mode
        NAVIGATE or SELECT (see module docstring).
    cell_w, cell_h : int
        Cell size in pixels.
    spacing : int
        Gap between cells in pixels.
    """

    class Mode(Enum):
        NAVIGATE = auto()
        SELECT   = auto()

    # Signals
    well_clicked       = Signal(int, int)   # (row, col) — NAVIGATE mode
    selection_changed  = Signal()           # SELECT mode

    def __init__(
        self,
        rows: int = 8,
        cols: int = 12,
        mode: "WellGrid.Mode" = None,
        cell_w: int = 36,
        cell_h: int = 22,
        spacing: int = 2,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._mode    = mode if mode is not None else WellGrid.Mode.SELECT
        self._rows    = rows
        self._cols    = cols
        self._cell_w  = cell_w
        self._cell_h  = cell_h
        self._spacing = spacing

        # SELECT mode state
        self._selected: list[list[bool]] = [
            [True] * cols for _ in range(rows)
        ]
        self._drag_target: Optional[bool] = None
        self._hover_cell: Optional[tuple[int, int]] = None

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumSize(self.sizeHint())

    # ------------------------------------------------------------------
    # Size
    # ------------------------------------------------------------------

    def sizeHint(self) -> QSize:
        w = self._cols * (self._cell_w + self._spacing) + self._spacing
        h = self._rows * (self._cell_h + self._spacing) + self._spacing
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rebuild(self, rows: int, cols: int):
        """Resize the grid, preserving existing selection state where possible."""
        old = [row[:] for row in self._selected]
        self._rows = rows
        self._cols = cols
        self._selected = []
        for r in range(rows):
            row_data = []
            for c in range(cols):
                if r < len(old) and c < len(old[r]):
                    row_data.append(old[r][c])
                else:
                    row_data.append(True)
            self._selected.append(row_data)
        self._drag_target = None
        self._hover_cell = None
        self.setMinimumSize(self.sizeHint())
        self.updateGeometry()
        self.update()

    def check_all(self):
        for r in range(self._rows):
            for c in range(self._cols):
                self._selected[r][c] = True
        self.selection_changed.emit()
        self.update()

    def uncheck_all(self):
        for r in range(self._rows):
            for c in range(self._cols):
                self._selected[r][c] = False
        self.selection_changed.emit()
        self.update()

    def invert(self):
        for r in range(self._rows):
            for c in range(self._cols):
                self._selected[r][c] = not self._selected[r][c]
        self.selection_changed.emit()
        self.update()

    def get_selected_indices(self) -> list[int]:
        """Return flat indices of selected wells in row-major order."""
        indices = []
        idx = 0
        for r in range(self._rows):
            for c in range(self._cols):
                if self._selected[r][c]:
                    indices.append(idx)
                idx += 1
        return indices

    def selected_count(self) -> int:
        return sum(self._selected[r][c] for r in range(self._rows) for c in range(self._cols))

    def total_count(self) -> int:
        return self._rows * self._cols

    # ------------------------------------------------------------------
    # Cell geometry helpers
    # ------------------------------------------------------------------

    def _cell_rect(self, row: int, col: int) -> QRect:
        x = self._spacing + col * (self._cell_w + self._spacing)
        y = self._spacing + row * (self._cell_h + self._spacing)
        return QRect(x, y, self._cell_w, self._cell_h)

    def _cell_at(self, pos: QPoint) -> Optional[tuple[int, int]]:
        """Return (row, col) for a pixel position, or None if outside any cell."""
        for r in range(self._rows):
            for c in range(self._cols):
                if self._cell_rect(r, c).contains(pos):
                    return (r, c)
        return None

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        painter.fillRect(self.rect(), _COL_BG)

        font = QFont()
        font.setPixelSize(9)
        painter.setFont(font)

        for r in range(self._rows):
            for c in range(self._cols):
                rect = self._cell_rect(r, c)
                is_hover = (self._hover_cell == (r, c))
                label = f"{chr(ord('A') + r)}{c + 1}"

                if self._mode == WellGrid.Mode.SELECT:
                    sel = self._selected[r][c]
                    if sel:
                        bg = _COL_SEL_HOVER if is_hover else _COL_SEL
                        border = _COL_BORDER_SEL
                        fg = _COL_TEXT_SEL
                    else:
                        bg = _COL_DESEL_HOVER if is_hover else _COL_DESEL
                        border = _COL_BORDER_DESEL
                        fg = _COL_TEXT_DESEL
                else:  # NAVIGATE
                    bg = _COL_NAV_HOVER if is_hover else _COL_NAV
                    border = _COL_BORDER_DESEL
                    fg = _COL_TEXT_SEL

                # Cell background with rounded corners
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bg)
                painter.drawRoundedRect(rect, 3, 3)

                # Border
                painter.setPen(QPen(border, 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, 3, 3)

                # Label
                painter.setPen(fg)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.end()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        cell = self._cell_at(event.position().toPoint())
        if cell is None:
            return
        r, c = cell
        if self._mode == WellGrid.Mode.NAVIGATE:
            self.well_clicked.emit(r, c)
        else:
            # Start drag: target state is the opposite of the clicked cell
            self._drag_target = not self._selected[r][c]
            self._selected[r][c] = self._drag_target
            self.selection_changed.emit()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position().toPoint()
        cell = self._cell_at(pos)

        # Update hover highlight
        if cell != self._hover_cell:
            self._hover_cell = cell
            self.update()

        # Drag painting (SELECT mode only)
        if (
            self._mode == WellGrid.Mode.SELECT
            and self._drag_target is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and cell is not None
        ):
            r, c = cell
            if self._selected[r][c] != self._drag_target:
                self._selected[r][c] = self._drag_target
                self.selection_changed.emit()
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_target = None

    def leaveEvent(self, event):
        self._hover_cell = None
        self.update()
