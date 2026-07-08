"""
Processing Panel — batch-convert .npy burst captures to images and video.

Provides a folder queue, output options, per-well progress, and an optional
auto-process trigger wired from the Experiment tab.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QPushButton, QListWidget, QListWidgetItem,
    QCheckBox, QProgressBar, QTextEdit, QFileDialog,
    QSizePolicy, QAbstractItemView, QTreeView, QListView,
)

# Ensure project root is importable when running standalone
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class _ProcessWorker(QThread):
    well_started  = Signal(str, int, int)    # well label, current, total
    frame_progress = Signal(int, int)         # current_frame, total_frames
    well_done     = Signal(str, bool, str)   # well label, success, message
    log_line      = Signal(str)
    finished      = Signal(int, int)         # wells_ok, wells_failed

    def __init__(self, folders: list[str], do_images: bool, do_video: bool, parent=None):
        super().__init__(parent)
        self._folders  = folders
        self._do_images = do_images
        self._do_video  = do_video
        self._stop      = False

    def stop(self):
        self._stop = True

    def run(self):
        from robocam.postprocess import find_metadata_files, parse_meta_name, process_well

        # Gather all wells across all selected folders
        all_jobs: list[tuple[Path, Path]] = []  # (meta_path, exp_dir)
        for folder in self._folders:
            try:
                metas, exp_dir = find_metadata_files(folder)
                for m in metas:
                    all_jobs.append((m, exp_dir))
            except ValueError as e:
                self.log_line.emit(f"[skip] {folder}: {e}")

        total  = len(all_jobs)
        ok     = 0
        failed = 0

        for i, (meta_path, exp_dir) in enumerate(all_jobs):
            if self._stop:
                self.log_line.emit("Cancelled.")
                break

            well, _ = parse_meta_name(meta_path)
            self.well_started.emit(well, i + 1, total)
            self.log_line.emit(f"\n[{well}]  {meta_path.name}")

            def _frame_cb(cur: int, tot: int):
                self.frame_progress.emit(cur, tot)

            try:
                process_well(
                    meta_path, exp_dir,
                    do_images=self._do_images,
                    do_video=self._do_video,
                    progress_callback=_frame_cb,
                )
                self.well_done.emit(well, True, "")
                ok += 1
                self.log_line.emit(f"  done.")
            except Exception as e:
                self.well_done.emit(well, False, str(e))
                failed += 1
                self.log_line.emit(f"  ERROR: {e}")

        self.finished.emit(ok, failed)


# ---------------------------------------------------------------------------
# Panel widget
# ---------------------------------------------------------------------------

class ProcessingPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[_ProcessWorker] = None

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(8, 8, 8, 8)

        root.addWidget(self._build_folders_group())
        root.addWidget(self._build_options_group())
        root.addWidget(self._build_progress_group())
        root.addStretch()

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _build_folders_group(self) -> QGroupBox:
        grp = QGroupBox("Experiment Folders")
        layout = QVBoxLayout(grp)

        self._folder_list = QListWidget()
        self._folder_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._folder_list.setMinimumHeight(120)
        layout.addWidget(self._folder_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Folder…")
        add_btn.clicked.connect(self._add_folder)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(remove_btn)

        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self._folder_list.clear)
        btn_row.addWidget(clear_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)
        return grp

    def _build_options_group(self) -> QGroupBox:
        grp = QGroupBox("Output Options")
        layout = QHBoxLayout(grp)

        self._do_images_chk = QCheckBox("PNG image sequence")
        self._do_images_chk.setChecked(True)
        layout.addWidget(self._do_images_chk)

        self._do_video_chk = QCheckBox("Video (MP4 + VFR MKV)")
        self._do_video_chk.setChecked(True)
        layout.addWidget(self._do_video_chk)

        layout.addStretch()

        self._process_btn = QPushButton("Process Folders")
        self._process_btn.setFixedHeight(32)
        self._process_btn.clicked.connect(self._start_processing)
        layout.addWidget(self._process_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedHeight(32)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_processing)
        layout.addWidget(self._cancel_btn)

        return grp

    def _build_progress_group(self) -> QGroupBox:
        grp = QGroupBox("Progress")
        layout = QVBoxLayout(grp)

        well_row = QHBoxLayout()
        well_row.addWidget(QLabel("Well:"))
        self._well_lbl = QLabel("—")
        well_row.addWidget(self._well_lbl)
        well_row.addStretch()
        self._overall_lbl = QLabel("0 / 0")
        well_row.addWidget(self._overall_lbl)
        layout.addLayout(well_row)

        self._frame_bar = QProgressBar()
        self._frame_bar.setTextVisible(True)
        self._frame_bar.setFormat("Frames: %v / %m")
        layout.addWidget(self._frame_bar)

        self._well_bar = QProgressBar()
        self._well_bar.setTextVisible(True)
        self._well_bar.setFormat("Wells: %v / %m")
        layout.addWidget(self._well_bar)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(160)
        self._log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._log.setFontFamily("monospace")
        layout.addWidget(self._log)

        return grp

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    def _add_folder(self):
        for path in self._select_multiple_directories():
            self._add_path(path)

    def _select_multiple_directories(self) -> list[str]:
        """Open a directory picker that allows selecting several folders at once.

        Qt's native getExistingDirectory() only returns one path, so this uses
        a non-native QFileDialog in Directory mode with the internal tree/list
        views switched to extended selection. Directory-mode selectedFiles()
        collapses a multi-selection down to a single path, so the selected
        rows are instead read directly off the dialog's internal "listView".
        """
        dialog = QFileDialog(self, "Select Experiment Folders")
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)

        for view in dialog.findChildren(QListView) + dialog.findChildren(QTreeView):
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return []

        view = dialog.findChild(QListView, "listView")
        if view is None or view.model() is None:
            return dialog.selectedFiles()
        model = view.model()
        paths = sorted({model.filePath(idx) for idx in view.selectionModel().selectedIndexes()})
        return paths or dialog.selectedFiles()

    def _add_path(self, path: str):
        # Avoid duplicates
        for i in range(self._folder_list.count()):
            if self._folder_list.item(i).text() == path:
                return
        self._folder_list.addItem(QListWidgetItem(path))

    def _remove_selected(self):
        for item in self._folder_list.selectedItems():
            self._folder_list.takeItem(self._folder_list.row(item))

    # ------------------------------------------------------------------
    # Processing control
    # ------------------------------------------------------------------

    def queue_folder(self, path: str):
        """Add a folder and start processing immediately (called from experiment auto-process)."""
        self._add_path(path)
        self._start_processing()

    def _start_processing(self):
        if self._worker and self._worker.isRunning():
            return

        folders = [self._folder_list.item(i).text()
                   for i in range(self._folder_list.count())]
        if not folders:
            self._log.append("No folders queued.")
            return

        if not self._do_images_chk.isChecked() and not self._do_video_chk.isChecked():
            self._log.append("Select at least one output option.")
            return

        self._log.clear()
        self._frame_bar.setValue(0)
        self._well_bar.setValue(0)
        self._process_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        self._worker = _ProcessWorker(
            folders=folders,
            do_images=self._do_images_chk.isChecked(),
            do_video=self._do_video_chk.isChecked(),
        )
        self._worker.well_started.connect(self._on_well_started)
        self._worker.frame_progress.connect(self._on_frame_progress)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _cancel_processing(self):
        if self._worker:
            self._worker.stop()
        self._cancel_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Worker signals
    # ------------------------------------------------------------------

    def _on_well_started(self, well: str, current: int, total: int):
        self._well_lbl.setText(well)
        self._overall_lbl.setText(f"{current} / {total}")
        self._well_bar.setMaximum(total)
        self._well_bar.setValue(current - 1)
        self._frame_bar.setValue(0)
        self._frame_bar.setMaximum(0)

    def _on_frame_progress(self, current: int, total: int):
        self._frame_bar.setMaximum(total)
        self._frame_bar.setValue(current)
        self._well_bar.setValue(self._well_bar.value())

    def _on_finished(self, ok: int, failed: int):
        self._well_bar.setValue(self._well_bar.maximum())
        self._process_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._log.append(
            f"\nFinished — {ok} well(s) processed"
            + (f", {failed} error(s)." if failed else ".")
        )
        self._well_lbl.setText("Done")
