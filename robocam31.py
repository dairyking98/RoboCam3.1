import logging
import os
import signal
import sys

# Clear platform theme env vars before Qt initialises — qt5ct/qt6ct with no
# config file produce broken palette values in Qt 6 (e.g. gray highlight,
# near-white highlighted-text that makes dropdown items invisible).
os.environ.pop("QT_QPA_PLATFORMTHEME", None)

_verbose = "--verbose" in sys.argv or "-v" in sys.argv

logging.basicConfig(
    level=logging.DEBUG if _verbose else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "robocam.log"), mode="w"),
    ],
)

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from ui.main_window import MainWindow


def _apply_palette(app: QApplication) -> None:
    """
    Force a clean light palette so dropdown text is always legible regardless
    of what the system GTK/XDG theme reports.  Qt 6.7+ does its own
    colour-scheme detection that can override palette values even after the
    platform theme env var is cleared; setting the palette explicitly wins.
    """
    app.setStyle(QStyleFactory.create("Fusion"))

    p = QPalette()
    # Backgrounds
    p.setColor(QPalette.ColorRole.Window,        QColor("#efefef"))
    p.setColor(QPalette.ColorRole.Base,          QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#e4e4e4"))
    p.setColor(QPalette.ColorRole.Button,        QColor("#dcdcdc"))
    p.setColor(QPalette.ColorRole.ToolTipBase,   QColor("#ffffdc"))
    # Foregrounds
    p.setColor(QPalette.ColorRole.WindowText,    QColor("#111111"))
    p.setColor(QPalette.ColorRole.Text,          QColor("#111111"))
    p.setColor(QPalette.ColorRole.ButtonText,    QColor("#111111"))
    p.setColor(QPalette.ColorRole.ToolTipText,   QColor("#111111"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#888888"))
    # Selection — a clear blue so highlighted items are obviously selected
    p.setColor(QPalette.ColorRole.Highlight,        QColor("#2979c8"))
    p.setColor(QPalette.ColorRole.HighlightedText,  QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link,             QColor("#2675bf"))
    # Disabled state
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#888888"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor("#888888"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#888888"))

    app.setPalette(p)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RoboCam 3.1")
    _apply_palette(app)

    # Ctrl+C → close via Qt's normal window-close path (triggers MainWindow.closeEvent)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Qt's C++ event loop won't yield to Python without a periodic wakeup,
    # so the signal above would never fire without this timer.
    _sigint_timer = QTimer()
    _sigint_timer.start(200)
    _sigint_timer.timeout.connect(lambda: None)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
