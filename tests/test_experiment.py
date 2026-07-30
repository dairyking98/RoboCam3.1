"""Tests for the raw-burst .npy stack trim helper and fps pacing."""
import time

import numpy as np
import pytest

from robocam.experiment import ExperimentRunner, _trim_raw_stack, _finalize_raw_burst_process


class _FakeCamera:
    """Minimal stand-in for robocam.camera.Camera -- just enough surface
    for ExperimentRunner._capture_raw_burst() to run against a fixed exposure
    and (optionally) a decoupled target fps, without any real hardware."""

    def __init__(self, exposure_us=10_000, target_fps=None, resolution=(4, 4)):
        self.resolution = resolution
        self._exposure_us = exposure_us
        self._target_fps = target_fps

    def get_exposure(self):
        return self._exposure_us

    def get_target_fps(self):
        return self._target_fps

    def set_target_fps(self, fps):
        self._target_fps = float(fps) if fps else None

    def reset_capture_stats(self):
        pass

    def get_capture_stats(self):
        return {}

    def get_dropped_frames_count(self):
        return 0

    def get_raw_frame(self):
        # Real hardware can't return a frame faster than the exposure time
        # -- mirror that here so the exposure-derived buffer ceiling isn't
        # blown through by an instant fake grabbing thousands of "frames"
        # per second regardless of exposure.
        time.sleep(self._exposure_us / 1_000_000.0)
        return np.zeros(self.resolution, dtype=np.uint8)


def _make_stack(path, ceiling, real, h=8, w=10, dtype=np.uint8, fill=None):
    stack = np.lib.format.open_memmap(str(path), mode="w+", dtype=dtype, shape=(ceiling, h, w))
    if fill is None:
        rng = np.random.default_rng(0)
        data = rng.integers(0, 255, size=(real, h, w)).astype(dtype)
    else:
        data = np.full((real, h, w), fill, dtype=dtype)
    stack[:real] = data
    stack.flush()
    del stack
    return data


class TestTrimRawStack:
    def test_trims_to_real_frame_count_and_preserves_data(self, tmp_path):
        path = tmp_path / "A1_stack.npy"
        data = _make_stack(path, ceiling=500, real=137)

        _trim_raw_stack(str(path), 137)

        loaded = np.load(path)
        assert loaded.shape == (137, 8, 10)
        assert np.array_equal(loaded, data)

    def test_shrinks_file_size(self, tmp_path):
        path = tmp_path / "A1_stack.npy"
        _make_stack(path, ceiling=500, real=137)
        before = path.stat().st_size

        _trim_raw_stack(str(path), 137)

        after = path.stat().st_size
        assert after < before
        # Real payload is 137*8*10 bytes; allow only header-sized slack on top.
        assert after < 137 * 8 * 10 + 200

    def test_zero_frames_captured(self, tmp_path):
        path = tmp_path / "A1_stack.npy"
        _make_stack(path, ceiling=500, real=0, h=4, w=4, dtype=np.uint16)

        _trim_raw_stack(str(path), 0)

        loaded = np.load(path)
        assert loaded.shape == (0, 4, 4)

    def test_shape_digit_count_shrinks(self, tmp_path):
        # Ceiling has 4 digits (1000), real has 1 (9) -- header text gets
        # shorter, exercising the padding-to-original-length path.
        path = tmp_path / "A1_stack.npy"
        data = _make_stack(path, ceiling=1000, real=9, h=3, w=3, fill=7)

        _trim_raw_stack(str(path), 9)

        loaded = np.load(path)
        assert loaded.shape == (9, 3, 3)
        assert np.array_equal(loaded, data)

    def test_rejects_non_npy_file(self, tmp_path):
        path = tmp_path / "not_npy.npy"
        path.write_bytes(b"not a real npy file header")

        with pytest.raises(ValueError):
            _trim_raw_stack(str(path), 1)


class TestTargetFpsPacing:
    """Exposure and target fps are decoupled: a target fps below what the
    exposure would otherwise allow must actually throttle the raw-burst
    capture loop (not just cosmetically appear in the UI)."""

    def _run_burst(self, tmp_path, monkeypatch, camera, duration_s=0.3):
        monkeypatch.chdir(tmp_path)
        runner = ExperimentRunner(motion_controller=None, camera=camera)
        runner.running = True
        burst_meta, finalize_ctx = runner._capture_raw_burst(
            output_dir=str(tmp_path), label="A1", timestamp="t",
            total_duration_s=duration_s,
        )
        # Call in-process rather than via multiprocessing.Process: it's a
        # plain function now (see _finalize_raw_burst_process's docstring
        # for why it needs to be spawn-process-safe in real use), and a
        # unit test's job here is the capture+finalize logic, not
        # re-exercising Python's multiprocessing module itself.
        _finalize_raw_burst_process(
            finalize_ctx["stack_path"], finalize_ctx["frame_idx"], finalize_ctx["label"]
        )
        return burst_meta

    def test_uncapped_when_no_target_fps_set(self, tmp_path, monkeypatch):
        # 1ms exposure -> ~1000fps ceiling; no target fps -> capture as fast
        # as exposure allows, same as before this feature existed.
        camera = _FakeCamera(exposure_us=1_000, target_fps=None)
        result = self._run_burst(tmp_path, monkeypatch, camera)
        assert result["fps_average"] > 200

    def test_paced_down_to_target_fps_below_exposure_ceiling(self, tmp_path, monkeypatch):
        # Same 1ms exposure (~1000fps ceiling), but a much slower target fps
        # -- this is the "short exposure, slow capture" use case. Achieved
        # rate should land near the target, not near the exposure ceiling.
        camera = _FakeCamera(exposure_us=1_000, target_fps=10.0)
        result = self._run_burst(tmp_path, monkeypatch, camera, duration_s=0.35)
        assert 5 < result["fps_average"] < 20
        assert result["frames_captured"] < 20

    def test_target_fps_above_exposure_ceiling_has_no_effect(self, tmp_path, monkeypatch):
        # A target fps that's actually a conflict (higher than exposure can
        # achieve) must never speed capture up past the exposure ceiling --
        # it just falls back to uncapped, exposure-bound behaviour.
        camera = _FakeCamera(exposure_us=10_000, target_fps=5_000.0)
        result = self._run_burst(tmp_path, monkeypatch, camera)
        assert result["fps_average"] < 150
