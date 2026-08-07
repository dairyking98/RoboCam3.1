"""Tests for robocam/loop_postprocess.py -- turning loop-mode output into
combined per-well artifacts (cross-cycle video, timelapse, stills zip)."""
import json
import zipfile

import cv2
import numpy as np
import pytest

from robocam.loop_postprocess import (
    AV_AVAILABLE,
    build_loop_stills_zip,
    build_loop_timelapse,
    build_loop_video,
    discover_valid_cycles,
    find_loop_wells,
    well_is_raw_mode,
)
from tests.test_experiment import _FakeCamera, _FakeMotion, _make_runner


class TestDiscoverValidCycles:
    def test_filters_to_ok_statuses_and_resolves_retry_suffix(self, tmp_path):
        loop_dir = tmp_path / "20260730_120000_exp_loop"
        loop_dir.mkdir()
        # A folder exists on disk for every attempt, valid or not -- run()
        # creates its exp_dir before it can fail.
        (loop_dir / "20260730_120000_exp_cycle0001").mkdir()
        (loop_dir / "20260730_120005_exp_cycle0002").mkdir()
        (loop_dir / "20260730_120006_exp_cycle0002_retry").mkdir()
        (loop_dir / "20260730_120010_exp_cycle0003").mkdir()
        (loop_dir / "20260730_120011_exp_cycle0003_retry").mkdir()

        records = [
            {"cycle": 1, "attempt": 1, "status": "ok", "labels": ["A1"], "well_count": 1},
            {"cycle": 2, "attempt": 1, "status": "error_retrying", "labels": ["A1"], "well_count": 1},
            {"cycle": 2, "attempt": 2, "status": "ok_after_retry", "labels": ["A1"], "well_count": 1},
            {"cycle": 3, "attempt": 1, "status": "error_retrying", "labels": ["A1"], "well_count": 1},
            {"cycle": 3, "attempt": 2, "status": "aborted_after_retry_failure", "labels": ["A1"], "well_count": 1},
        ]
        with open(loop_dir / "loop_manifest.jsonl", "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        cycles = discover_valid_cycles(loop_dir)
        assert [c["cycle"] for c in cycles] == [1, 2]
        # Cycle 2's failed first-attempt folder must never be picked --
        # only the successful retry's.
        assert cycles[0]["dir"].name == "20260730_120000_exp_cycle0001"
        assert cycles[1]["dir"].name == "20260730_120006_exp_cycle0002_retry"

    def test_cycle_1_and_cycle_10_never_collide(self, tmp_path):
        loop_dir = tmp_path / "loop"
        loop_dir.mkdir()
        (loop_dir / "ts_exp_cycle0001").mkdir()
        (loop_dir / "ts_exp_cycle0010").mkdir()
        with open(loop_dir / "loop_manifest.jsonl", "w") as f:
            f.write(json.dumps({"cycle": 1, "status": "ok", "labels": ["A1"]}) + "\n")
            f.write(json.dumps({"cycle": 10, "status": "ok", "labels": ["A1"]}) + "\n")

        cycles = discover_valid_cycles(loop_dir)
        by_cycle = {c["cycle"]: c["dir"].name for c in cycles}
        assert by_cycle == {1: "ts_exp_cycle0001", 10: "ts_exp_cycle0010"}

    def test_raises_without_manifest(self, tmp_path):
        with pytest.raises(ValueError):
            discover_valid_cycles(tmp_path / "nope")

    def test_real_run_loop_output_discovered_correctly(self, tmp_path):
        # End-to-end against real run_loop() output, not just synthetic
        # manifests -- catches drift between this module's assumptions and
        # what run_loop() actually writes.
        runner = _make_runner(tmp_path)
        runner.run_loop(
            name="realloop", positions=[(0, 0, 0), (1, 1, 0)], labels=["A1", "A2"],
            delay_per_well=0.05, mode="image",
            interval_s=0.0, duration_s=1.0,
        )
        loop_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.endswith("_realloop_loop")]
        assert len(loop_dirs) == 1
        cycles = discover_valid_cycles(loop_dirs[0])
        assert len(cycles) >= 2
        for c in cycles:
            assert c["dir"].is_dir()
            assert c["labels"] == ["A1", "A2"]


class TestFindLoopWells:
    def test_union_preserves_first_seen_order(self):
        cycles = [
            {"labels": ["A1", "A2"]},
            {"labels": ["A2", "A3"]},
            {"labels": []},
        ]
        assert find_loop_wells(cycles) == ["A1", "A2", "A3"]

    def test_no_labels_gives_empty(self):
        assert find_loop_wells([{"labels": []}]) == []


class TestWellIsRawMode:
    def test_true_when_metadata_present(self, tmp_path):
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "A1_20260730_metadata.json").write_text("{}")
        assert well_is_raw_mode(tmp_path, "A1") is True

    def test_false_when_no_raw_metadata(self, tmp_path):
        (tmp_path / "raw").mkdir()
        assert well_is_raw_mode(tmp_path, "A1") is False

    def test_false_when_no_raw_dir_at_all(self, tmp_path):
        assert well_is_raw_mode(tmp_path, "A1") is False


def _make_real_loop(tmp_path, mode, camera=None):
    runner = _make_runner(tmp_path, camera=camera)
    runner.run_loop(
        name="vidtest", positions=[(0, 0, 0)], labels=["A1"],
        delay_per_well=0.0, mode=mode,
        pre_duration=0.05 if mode == "raw" else 5.0,
        interval_s=0.0, duration_s=0.8,
    )
    loop_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.endswith("_vidtest_loop")]
    assert len(loop_dirs) == 1
    return loop_dirs[0]


@pytest.mark.skipif(not AV_AVAILABLE, reason="PyAV not installed")
class TestBuildLoopVideo:
    def test_concatenates_frames_across_cycles(self, tmp_path):
        camera = _FakeCamera(exposure_us=5_000)
        loop_dir = _make_real_loop(tmp_path, mode="raw", camera=camera)
        cycles = discover_valid_cycles(loop_dir)
        assert len(cycles) >= 2

        out_path = str(tmp_path / "combined.mp4")
        result = build_loop_video(cycles, "A1", out_path, fps=10.0)

        assert result["frame_count"] > 0
        import os
        assert os.path.getsize(out_path) > 0

    def test_missing_well_returns_zero_frames(self, tmp_path):
        camera = _FakeCamera(exposure_us=5_000)
        loop_dir = _make_real_loop(tmp_path, mode="raw", camera=camera)
        cycles = discover_valid_cycles(loop_dir)

        result = build_loop_video(cycles, "Z9", str(tmp_path / "nope.mp4"), fps=10.0)
        assert result == {"frame_count": 0, "out_path": None}


@pytest.mark.skipif(not AV_AVAILABLE, reason="PyAV not installed")
class TestBuildLoopTimelapse:
    def test_one_frame_per_cycle(self, tmp_path):
        loop_dir = _make_real_loop(tmp_path, mode="image")
        cycles = discover_valid_cycles(loop_dir)
        assert len(cycles) >= 2

        out_path = str(tmp_path / "timelapse.mp4")
        result = build_loop_timelapse(cycles, "A1", out_path, fps=5.0)

        assert result["frame_count"] == len(cycles)
        import os
        assert os.path.getsize(out_path) > 0


class TestBuildLoopStillsZip:
    def test_bundles_every_cycles_stills(self, tmp_path):
        loop_dir = _make_real_loop(tmp_path, mode="image")
        cycles = discover_valid_cycles(loop_dir)
        assert len(cycles) >= 2

        out_zip = str(tmp_path / "all_cycles.zip")
        build_loop_stills_zip(cycles, out_zip)

        with zipfile.ZipFile(out_zip) as zf:
            names = zf.namelist()
        assert len(names) == len(cycles)  # 1 well x N cycles
        for cycle in cycles:
            assert any(n.startswith(f"{cycle['dir'].name}/A1_") for n in names)

    def test_filters_to_requested_wells(self, tmp_path):
        loop_dir = _make_real_loop(tmp_path, mode="image")
        cycles = discover_valid_cycles(loop_dir)

        out_zip = str(tmp_path / "filtered.zip")
        build_loop_stills_zip(cycles, out_zip, wells=["Z9"])

        with zipfile.ZipFile(out_zip) as zf:
            assert zf.namelist() == []
