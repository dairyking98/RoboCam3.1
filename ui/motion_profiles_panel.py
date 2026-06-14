from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


class MotionProfilesPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl = QLabel(
            "Motion Profiles — coming soon.\n\n"
            "Will support reading and writing feed-rate, acceleration,\n"
            "and jerk settings (M203 / M201 / M204 / M205)\n"
            "for both Marlin and Klipper backends."
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: gray; font-size: 13px;")
        layout.addWidget(lbl)
