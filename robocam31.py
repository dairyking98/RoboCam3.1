import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RoboCam 3.1")

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
