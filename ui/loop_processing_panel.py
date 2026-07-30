"""
Loop Processing Panel — turn one loop-mode run's per-cycle output into
combined per-well artifacts: a cross-cycle video (raw-mode loops), a
timelapse (image-mode/growth-imaging loops), and a whole-loop stills zip.

Separate from the Processing tab on purpose — that tab already handles any
single experiment folder with zero changes, but has no notion of "many
cycles of one loop, aggregated per well," which is a different input shape
(one loop folder, not a queue of arbitrary experiment folders) and
different processing (concatenate/timelapse across cycles, not just
debayer one burst).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QPushButton, QCheckBox, QDoubleSpinBox,
    QProgressBar, QTextEdit, QFileDialog, QMessageBox, QSizePolicy,
)

# Ensure project root is importable when running standalone
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class _LoopProcessWorker(QThread):
    well_started   = Signal(str, int, int)   # well label, current, total
    cycle_progress = Signal(int, int)         # cycles done, total cycles for this well
    well_done      = Signal(str, bool, str)   # well label, success, message
    log_line       = Signal(str)
    finished       = Signal(int, int)         # wells_ok, wells_failed

    def __init__(self, loop_dir: str, fps: float, do_video: bool, do_zip: bool, parent=None):
        super().__init__(parent)
        self._loop_dir = loop_dir
        self._fps = fps
        self._do_video = do_video
        self._do_zip = do_zip
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from robocam.loop_postprocess import (
            discover_valid_cycles, find_loop_wells, well_is_raw_mode,
            build_loop_video, build_loop_timelapse, build_loop_stills_zip,
        )

        loop_dir = Path(self._loop_dir)
        try:
            cycles = discover_valid_cycles(loop_dir)
        except ValueError as e:
            self.log_line.emit(f"[error] {e}")
            self.finished.emit(0, 0)
            return

        if not cycles:
            self.log_line.emit("No valid (successful) cycles found in this loop.")
            self.finished.emit(0, 0)
            return

        self.log_line.emit(f"Found {len(cycles)} valid cycle(s).")
        wells = find_loop_wells(cycles)
        total = len(wells)
        ok = 0
        failed = 0

        for i, well in enumerate(wells):
            if self._stop:
                self.log_line.emit("Cancelled.")
                break

            self.well_started.emit(well, i + 1, total)
            is_raw = well_is_raw_mode(cycles[0]["dir"], well)
            self.log_line.emit(f"\n[{well}]  {'raw' if is_raw else 'image'} mode, {len(cycles)} cycles")

            def _cycle_cb(cur: int, tot: int):
                self.cycle_progress.emit(cur, tot)

            try:
                if self._do_video:
                    if is_raw:
                        out_path = str(loop_dir / f"{well}_loop_video.mp4")
                        result = build_loop_video(cycles, well, out_path, fps=self._fps, progress_callback=_cycle_cb)
                    else:
                        out_path = str(loop_dir / f"{well}_loop_timelapse.mp4")
                        result = build_loop_timelapse(cycles, well, out_path, fps=self._fps, progress_callback=_cycle_cb)

                    if result["frame_count"] == 0:
                        self.log_line.emit(f"  [skip] no frames found for {well}")
                    else:
                        self.log_line.emit(f"  -> {result['out_path']} ({result['frame_count']} frames)")

                self.well_done.emit(well, True, "")
                ok += 1
            except Exception as e:
                self.well_done.emit(well, False, str(e))
                failed += 1
                self.log_line.emit(f"  ERROR: {e}")

        if self._do_zip and not self._stop:
            zip_wells = [w for w in wells if not well_is_raw_mode(cycles[0]["dir"], w)]
            if zip_wells:
                out_zip = str(loop_dir / "all_cycles_images.zip")
                try:
                    build_loop_stills_zip(cycles, out_zip, wells=zip_wells)
                    self.log_line.emit(f"\n[zip] {out_zip}")
                except Exception as e:
                    self.log_line.emit(f"[zip] ERROR: {e}")

        self.finished.emit(ok, failed)


# ---------------------------------------------------------------------------
# Panel widget
# ---------------------------------------------------------------------------

class LoopProcessingPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[_LoopProcessWorker] = None
        self._loop_dir: Optional[str] = None

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(8, 8, 8, 8)

        root.addWidget(self._build_folder_group())
        root.addWidget(self._build_options_group())
        root.addWidget(self._build_progress_group())
        root.addStretch()

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _build_folder_group(self) -> QGroupBox:
        grp = QGroupBox("Loop Folder")
        layout = QHBoxLayout(grp)

        self._loop_dir_lbl = QLabel("(none selected)")
        self._loop_dir_lbl.setWordWrap(True)
        self._loop_dir_lbl.setStyleSheet("color: gray;")
        layout.addWidget(self._loop_dir_lbl, stretch=1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_loop_folder)
        layout.addWidget(browse_btn)

        return grp

    def _build_options_group(self) -> QGroupBox:
        grp = QGroupBox("Output Options")
        outer = QVBoxLayout(grp)

        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("Output FPS:"))
        self._fps_spin = QDoubleSpinBox()
        self._fps_spin.setRange(0.1, 120.0)
        self._fps_spin.setValue(10.0)
        self._fps_spin.setToolTip(
            "Drives both video builders below -- a raw-mode loop's combined "
            "video plays back at this rate, and an image-mode loop's "
            "timelapse shows this many cycles per second."
        )
        fps_row.addWidget(self._fps_spin)
        fps_row.addStretch()
        outer.addLayout(fps_row)

        self._do_video_chk = QCheckBox(
            "Build combined video per well (raw-mode: every cycle's frames "
            "concatenated in order; image-mode: one frame per cycle, a timelapse)"
        )
        self._do_video_chk.setChecked(True)
        outer.addWidget(self._do_video_chk)

        self._do_zip_chk = QCheckBox("Package all cycles' images into one zip (image-mode wells only)")
        self._do_zip_chk.setChecked(True)
        outer.addWidget(self._do_zip_chk)

        btn_row = QHBoxLayout()
        self._process_btn = QPushButton("Process Loop")
        self._process_btn.setFixedHeight(32)
        self._process_btn.clicked.connect(self._start_processing)
        btn_row.addWidget(self._process_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedHeight(32)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_processing)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

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

        self._cycle_bar = QProgressBar()
        self._cycle_bar.setTextVisible(True)
        self._cycle_bar.setFormat("Cycles: %v / %m")
        layout.addWidget(self._cycle_bar)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(160)
        self._log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._log.setFontFamily("monospace")
        layout.addWidget(self._log)

        return grp

    # ------------------------------------------------------------------
    # Folder selection
    # ------------------------------------------------------------------

    def _browse_loop_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select Loop Folder")
        if not chosen:
            return
        path = Path(chosen)
        if not (path / "loop_manifest.jsonl").exists():
            QMessageBox.critical(
                self, "Error",
                "That folder doesn't look like a loop folder\n"
                "(no loop_manifest.jsonl found inside it)."
            )
            return
        self._loop_dir = str(path)
        self._loop_dir_lbl.setText(str(path))
        self._loop_dir_lbl.setStyleSheet("color: black;")

    # ------------------------------------------------------------------
    # Processing control
    # ------------------------------------------------------------------

    def _start_processing(self):
        if self._worker and self._worker.isRunning():
            return

        if not self._loop_dir:
            self._log.append("No loop folder selected.")
            return

        do_video = self._do_video_chk.isChecked()
        do_zip = self._do_zip_chk.isChecked()
        if not do_video and not do_zip:
            self._log.append("Select at least one output option.")
            return

        self._log.clear()
        self._cycle_bar.setValue(0)
        self._process_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        self._worker = _LoopProcessWorker(
            loop_dir=self._loop_dir,
            fps=self._fps_spin.value(),
            do_video=do_video,
            do_zip=do_zip,
        )
        self._worker.well_started.connect(self._on_well_started)
        self._worker.cycle_progress.connect(self._on_cycle_progress)
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
        self._cycle_bar.setValue(0)
        self._cycle_bar.setMaximum(0)

    def _on_cycle_progress(self, current: int, total: int):
        self._cycle_bar.setMaximum(total)
        self._cycle_bar.setValue(current)

    def _on_finished(self, ok: int, failed: int):
        self._process_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._log.append(
            f"\nFinished — {ok} well(s) processed"
            + (f", {failed} error(s)." if failed else ".")
        )
        self._well_lbl.setText("Done")
