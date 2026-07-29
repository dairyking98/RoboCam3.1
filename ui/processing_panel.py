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

    def __init__(self, folders: list[str], do_png: bool, do_jpeg: bool,
                 do_mp4: bool, do_vfr: bool, zip_images: bool, parent=None):
        super().__init__(parent)
        self._folders    = folders
        self._do_png     = do_png
        self._do_jpeg    = do_jpeg
        self._do_mp4     = do_mp4
        self._do_vfr     = do_vfr
        self._zip_images = zip_images
        self._stop       = False

    def stop(self):
        self._stop = True

    def run(self):
        from robocam.postprocess import (
            find_metadata_files, parse_meta_name, process_well, open_export_zip,
        )

        # Gather all wells across all selected folders, grouped by experiment
        # directory — a zip archive (when enabled) spans every well in one
        # experiment, so it has to be opened/closed once per exp_dir, not per well.
        jobs_by_exp: dict[Path, list[Path]] = {}
        for folder in self._folders:
            try:
                metas, exp_dir = find_metadata_files(folder)
                jobs_by_exp.setdefault(exp_dir, []).extend(metas)
            except ValueError as e:
                self.log_line.emit(f"[skip] {folder}: {e}")

        total  = sum(len(metas) for metas in jobs_by_exp.values())
        done   = 0
        ok     = 0
        failed = 0

        for exp_dir, metas in jobs_by_exp.items():
            if self._stop:
                break

            zip_png  = (open_export_zip(exp_dir, "png")
                        if self._do_png and self._zip_images else None)
            zip_jpeg = (open_export_zip(exp_dir, "jpeg")
                        if self._do_jpeg and self._zip_images else None)

            try:
                for meta_path in metas:
                    if self._stop:
                        self.log_line.emit("Cancelled.")
                        break

                    well, _ = parse_meta_name(meta_path)
                    done += 1
                    self.well_started.emit(well, done, total)
                    self.log_line.emit(f"\n[{well}]  {meta_path.name}")

                    def _frame_cb(cur: int, tot: int):
                        self.frame_progress.emit(cur, tot)

                    try:
                        process_well(
                            meta_path, exp_dir,
                            do_png=self._do_png,
                            do_jpeg=self._do_jpeg,
                            do_mp4=self._do_mp4,
                            do_vfr=self._do_vfr,
                            zip_png=zip_png,
                            zip_jpeg=zip_jpeg,
                            progress_callback=_frame_cb,
                        )
                        self.well_done.emit(well, True, "")
                        ok += 1
                        self.log_line.emit(f"  done.")
                    except Exception as e:
                        self.well_done.emit(well, False, str(e))
                        failed += 1
                        self.log_line.emit(f"  ERROR: {e}")
            finally:
                if zip_png is not None:
                    zip_png.close()
                    self.log_line.emit(f"\n[zip] {zip_png.filename}")
                if zip_jpeg is not None:
                    zip_jpeg.close()
                    self.log_line.emit(f"[zip] {zip_jpeg.filename}")

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
        outer = QVBoxLayout(grp)

        # Formats are never mixed into one folder — each gets its own
        # images_png/, images_jpeg/, videos_mp4/, videos_vfr/ directory.
        img_row = QHBoxLayout()
        img_row.addWidget(QLabel("Images:"))

        # Percentages are approximate output-size-vs-raw-.npy ratios, measured
        # on a real 3-well mono-sensor burst (see docs/recording_modes.md,
        # "Export format size comparison") — actual ratio depends on sensor
        # noise/content, treat these as ballpark, not a guarantee.
        size_note = ("Approximate output size vs. the raw .npy burst, measured "
                     "on a real 3-well mono-sensor capture (see "
                     "docs/recording_modes.md, \"Export format size "
                     "comparison\"). Actual ratio depends on sensor noise and "
                     "image content — treat as ballpark, not a guarantee.")

        self._do_png_chk = QCheckBox("PNG (~70% of raw)")
        self._do_png_chk.setChecked(True)
        self._do_png_chk.setToolTip("Lossless.\n" + size_note)
        img_row.addWidget(self._do_png_chk)

        self._do_jpeg_chk = QCheckBox("JPEG (~44% of raw)")
        self._do_jpeg_chk.setChecked(False)
        self._do_jpeg_chk.setToolTip("Lossy (quality 95).\n" + size_note)
        img_row.addWidget(self._do_jpeg_chk)

        self._zip_images_chk = QCheckBox("Package as .zip (one archive per experiment, easier to transfer)")
        self._zip_images_chk.setChecked(True)
        self._zip_images_chk.setToolTip(
            "Streams PNG/JPEG frames straight into one .zip per experiment "
            "instead of writing loose files — same total size (ZIP_STORED, "
            "no re-compression), just packaged as a single file for transfer."
        )
        img_row.addWidget(self._zip_images_chk)

        img_row.addStretch()
        outer.addLayout(img_row)

        vid_row = QHBoxLayout()
        vid_row.addWidget(QLabel("Video:"))

        self._do_mp4_chk = QCheckBox("MP4 (display, ~21% of raw)")
        self._do_mp4_chk.setChecked(True)
        self._do_mp4_chk.setToolTip(
            "Lossy (libx264), constant frame rate — for quick viewing, not analysis.\n" + size_note)
        vid_row.addWidget(self._do_mp4_chk)

        self._do_vfr_chk = QCheckBox("VFR MKV (archival, lossless, ~63% of raw)")
        self._do_vfr_chk.setChecked(False)
        self._do_vfr_chk.setToolTip(
            "Lossless (ffv1), real per-frame timestamps — for analysis, not "
            "quick viewing (larger than MP4, and true VFR content can look odd "
            "scrubbing in some players).\n" + size_note)
        vid_row.addWidget(self._do_vfr_chk)

        vid_row.addStretch()

        self._process_btn = QPushButton("Process Folders")
        self._process_btn.setFixedHeight(32)
        self._process_btn.clicked.connect(self._start_processing)
        vid_row.addWidget(self._process_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedHeight(32)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_processing)
        vid_row.addWidget(self._cancel_btn)

        outer.addLayout(vid_row)
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

        do_png  = self._do_png_chk.isChecked()
        do_jpeg = self._do_jpeg_chk.isChecked()
        do_mp4  = self._do_mp4_chk.isChecked()
        do_vfr  = self._do_vfr_chk.isChecked()
        if not any([do_png, do_jpeg, do_mp4, do_vfr]):
            self._log.append("Select at least one output option.")
            return

        self._log.clear()
        self._frame_bar.setValue(0)
        self._well_bar.setValue(0)
        self._process_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        self._worker = _ProcessWorker(
            folders=folders,
            do_png=do_png,
            do_jpeg=do_jpeg,
            do_mp4=do_mp4,
            do_vfr=do_vfr,
            zip_images=self._zip_images_chk.isChecked(),
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
