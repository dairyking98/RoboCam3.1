"""
Main application window.

Tab order: Setup → Motion Profiles → Calibration → Experiment → Manual Control
"""
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget
from PySide6.QtCore import QTimer

from ui.setup_panel import SetupPanel
from ui.motion_profiles_panel import MotionProfilesPanel
from ui.calibration_panel import CalibrationPanel
from ui.experiment_panel import ExperimentPanel
from ui.manual_control_panel import ManualControlPanel
from ui.processing_panel import ProcessingPanel
import robocam.hw_state as hw_state
from robocam.session import session_manager


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RoboCam 3.1")
        self.setGeometry(100, 100, 1440, 900)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs)

        self.setup_panel          = SetupPanel()
        self.motion_profiles_panel = MotionProfilesPanel()
        self.calibration_panel    = CalibrationPanel()
        self.experiment_panel     = ExperimentPanel(
            calibration_panel=self.calibration_panel
        )
        self.manual_panel         = ManualControlPanel()
        self.processing_panel     = ProcessingPanel()

        self.tabs.addTab(self.setup_panel,           "Setup")
        self.tabs.addTab(self.motion_profiles_panel, "Motion Profiles")
        self.tabs.addTab(self.calibration_panel,     "Calibration")
        self.tabs.addTab(self.experiment_panel,      "Experiment")
        self.tabs.addTab(self.manual_panel,          "Manual Control")
        self.tabs.addTab(self.processing_panel,      "Processing")

        # Tab locking during experiment
        self.experiment_panel.experiment_started.connect(
            lambda: self._set_tabs_enabled(False)
        )
        self.experiment_panel.experiment_finished.connect(
            lambda: self._set_tabs_enabled(True)
        )

        # Calibration → experiment sync
        self.calibration_panel.cols_spin.valueChanged.connect(
            lambda _: self.experiment_panel.sync_from_calibration()
        )
        self.calibration_panel.rows_spin.valueChanged.connect(
            lambda _: self.experiment_panel.sync_from_calibration()
        )
        self.calibration_panel.corners_changed.connect(
            self.experiment_panel.sync_from_calibration
        )

        # Camera connected → refresh controls on other panels
        self.setup_panel.camera_connected.connect(
            self.calibration_panel._refresh_camera_controls
        )
        self.setup_panel.camera_connected.connect(
            self.experiment_panel._update_resolution_label
        )

        # Auto-process: switch to Processing tab and queue the experiment folder
        def _auto_process(exp_dir: str):
            self.processing_panel.queue_folder(exp_dir)
            self.tabs.setCurrentWidget(self.processing_panel)

        self.experiment_panel.experiment_data_ready.connect(_auto_process)

        # Auto initial sync after panels have loaded
        QTimer.singleShot(500, self.experiment_panel.sync_from_calibration)

    def closeEvent(self, event):
        # Stop experiment runner
        if self.experiment_panel._exp_thread and \
                self.experiment_panel._exp_thread.isRunning():
            self.experiment_panel._exp_thread.stop()
            self.experiment_panel._exp_thread.wait(5000)

        # Stop frame grabbers
        for panel in (self.calibration_panel,
                      self.experiment_panel,
                      self.manual_panel):
            grabber = getattr(panel, "_grabber", None)
            if grabber and grabber.isRunning():
                grabber.stop()
                grabber.wait(2000)

        # Save session state from each panel before quitting
        self.calibration_panel._save_session()
        self.experiment_panel._save_session()
        session_manager.save()

        # Disconnect hardware
        cam = hw_state.get_camera()
        if cam:
            try:
                cam.stop()
            except Exception:
                pass

        mc = hw_state.get_motion()
        if mc:
            try:
                mc.disconnect()
            except Exception:
                pass

        event.accept()

    def _set_tabs_enabled(self, enabled: bool):
        exp_idx = self.tabs.indexOf(self.experiment_panel)
        for i in range(self.tabs.count()):
            if i != exp_idx:
                self.tabs.setTabEnabled(i, enabled)
        self.tabs.setTabEnabled(exp_idx, True)
