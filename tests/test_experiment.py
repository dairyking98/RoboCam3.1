"""Tests for the raw-burst .npy stack trim helper, fps pacing, and loop mode."""
import json
import threading
import time
from datetime import datetime, timedelta

import numpy as np
import pytest

from robocam.experiment import (
    ExperimentRunner, _trim_raw_stack, _finalize_raw_burst_process, estimate_loop_cycle_count,
)


class _FakeCamera:
    """Minimal stand-in for robocam.camera.Camera -- just enough surface
    for ExperimentRunner._capture_raw_burst() to run against a fixed exposure
    and (optionally) a decoupled target fps, without any real hardware."""

    def __init__(self, exposure_us=10_000, target_fps=None, resolution=(4, 4)):
        self.resolution = resolution
        self._exposure_us = exposure_us
        self._target_fps = target_fps
        self.backend = "generic"

    def get_frame(self):
        # Still-image path (loop mode / growth imaging) -- unlike
        # get_raw_frame(), no exposure-length sleep here since these frames
        # aren't used for fps-pacing assertions, just for run()/run_loop()
        # integration tests that need a valid cv2.imwrite()-able array.
        w, h = self.resolution  # resolution is (w, h), matching _write_raw_burst's unpacking
        return np.zeros((h, w), dtype=np.uint8)

    def get_camera_meta(self):
        return {"bit_depth": 8}

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


class _FakeMotion:
    """Minimal stand-in for the Marlin motion controller -- just enough
    surface for ExperimentRunner.run()/run_loop() to run end-to-end without
    real hardware. supports_profiles=False (the default) skips the
    ETA-estimate branch entirely (the simplest path for tests that don't
    care about estimation itself, just loop-mode mechanics); pass
    supports_profiles=True for tests that need estimate_cycle_duration_s()
    to actually return a value."""

    def __init__(self, supports_profiles=False):
        self.is_homed = True
        self.supports_profiles = supports_profiles
        self.X = self.Y = self.Z = 0.0
        self.command_delay = 0.0

    def home(self):
        self.is_homed = True

    def move_absolute(self, X, Y, Z):
        self.X, self.Y, self.Z = X, Y, Z

    def read_profiles(self):
        return {
            "max_feed_x": 100.0, "max_accel_x": 500.0,
            "max_feed_y": 100.0, "max_accel_y": 500.0,
            "max_feed_z": 20.0, "max_accel_z": 100.0,
        }

    def get_cached_profile(self):
        # estimate_cycle_duration_s() reads the cache by default (see its
        # docstring) rather than triggering a live read -- mirror
        # MotionController's real behavior (cache == last-read profile).
        return self.read_profiles()


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


def _make_runner(tmp_path, camera=None, motion=None):
    runner = ExperimentRunner(motion_controller=motion or _FakeMotion(), camera=camera or _FakeCamera())
    runner.out_dir = str(tmp_path)
    return runner


def _manifest_records(loop_dir):
    manifest = loop_dir / "loop_manifest.jsonl"
    if not manifest.exists():
        return []
    return [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]


class TestRunLoop:
    """Loop mode: repeat run() with an inter-cycle interval (0 = back-to-back
    "habituation"-style, N seconds = "growth imaging"-style) until a
    wall-clock duration elapses. Any capture mode is supported -- there's
    no separate growth-imaging flag, just interval_s/duration_s params."""

    def test_habituation_back_to_back_nested_folders(self, tmp_path):
        runner = _make_runner(tmp_path)
        runner.run_loop(
            name="hab", positions=[(0, 0, 0), (1, 1, 0)], labels=["A1", "A2"],
            delay_per_well=0.1, mode="image",
            interval_s=0.0, duration_s=1.0,
        )

        loop_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.endswith("_hab_loop")]
        assert len(loop_dirs) == 1
        loop_dir = loop_dirs[0]

        records = _manifest_records(loop_dir)
        ok_records = [r for r in records if r["status"] == "ok"]
        assert len(ok_records) >= 2, "expected at least 2 back-to-back cycles in 1s"

        cycle_dirs = sorted(d for d in loop_dir.iterdir() if d.is_dir())
        assert len(cycle_dirs) == len(ok_records)
        for d in cycle_dirs:
            assert list(d.glob("*_points.csv")), f"{d} missing points.csv -- not a normal experiment dir"

    def test_loop_deadline_set_during_run_and_cleared_after(self, tmp_path):
        runner = _make_runner(tmp_path)
        seen_deadline = {}

        def _capture_deadline(msg):
            if runner.loop_deadline is not None and "deadline" not in seen_deadline:
                seen_deadline["deadline"] = runner.loop_deadline

        before = datetime.now()
        runner.run_loop(
            name="deadline", positions=[(0, 0, 0)], labels=["A1"],
            delay_per_well=0.0, mode="image", callback=_capture_deadline,
            interval_s=0.0, duration_s=1.0,
        )
        after = datetime.now()

        assert "deadline" in seen_deadline, "loop_deadline should be set while looping"
        # Deadline should be ~1s (duration_s) after the loop started.
        assert before <= seen_deadline["deadline"] <= after + timedelta(seconds=1.0)
        assert runner.loop_deadline is None, "loop_deadline should be cleared once the loop ends"

    def test_interval_spacing(self, tmp_path):
        runner = _make_runner(tmp_path)
        interval_s = 2.0
        runner.run_loop(
            name="growth", positions=[(0, 0, 0)], labels=["A1"],
            delay_per_well=0.0, mode="image",
            interval_s=interval_s, duration_s=3.5,
        )

        loop_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.endswith("_growth_loop")]
        loop_dir = loop_dirs[0]
        ok_records = [r for r in _manifest_records(loop_dir) if r["status"] == "ok"]
        assert len(ok_records) >= 2

        starts = [datetime.fromisoformat(r["start"]) for r in ok_records[:2]]
        gap = (starts[1] - starts[0]).total_seconds()
        # Cycle work itself is near-instant (1 well, no dwell), so the gap
        # should track the configured interval, not be back-to-back.
        assert interval_s - 1.0 < gap < interval_s + 1.5

    def test_raw_mode_allowed_in_loop(self, tmp_path):
        # Growth imaging's old still-image-only restriction is gone -- any
        # capture mode works in loop mode now, including raw bursts.
        camera = _FakeCamera(exposure_us=5_000)
        runner = _make_runner(tmp_path, camera=camera)
        runner.run_loop(
            name="rawloop", positions=[(0, 0, 0)], labels=["A1"],
            mode="raw", pre_duration=0.05,
            interval_s=0.0, duration_s=1.0,
        )

        loop_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.endswith("_rawloop_loop")]
        assert len(loop_dirs) == 1
        ok_records = [r for r in _manifest_records(loop_dirs[0]) if r["status"] == "ok"]
        assert len(ok_records) >= 1
        for d in loop_dirs[0].iterdir():
            if not d.is_dir():
                continue
            assert list((d / "raw").glob("*_stack.npy")), f"{d} missing raw stack"

    def test_retry_once_then_abort(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)
        call_count = {"n": 0}

        def fake_run(**kwargs):
            call_count["n"] += 1
            runner.status_msg = "Experiment error: forced failure"
            runner.last_run_ok = False
            runner.last_cycle_estimate_s = None

        monkeypatch.setattr(runner, "run", fake_run)
        runner.run_loop(
            name="fail", positions=[(0, 0, 0)], labels=["A1"],
            interval_s=0.0, duration_s=60.0,
        )

        assert call_count["n"] == 2, "should retry exactly once, not loop forever"
        assert runner.looping is False

        loop_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.endswith("_fail_loop")]
        records = _manifest_records(loop_dirs[0])
        statuses = [r["status"] for r in records]
        assert statuses == ["error_retrying", "aborted_after_retry_failure"]

    def test_stop_during_inter_cycle_sleep_is_immediate(self, tmp_path):
        runner = _make_runner(tmp_path)
        thread = threading.Thread(
            target=runner.run_loop,
            kwargs=dict(
                name="stopme", positions=[(0, 0, 0)], labels=["A1"],
                delay_per_well=0.0, mode="image",
                interval_s=600.0, duration_s=600.0,
            ),
        )
        thread.start()
        # Wait for cycle 1 to complete and enter the (long) inter-cycle sleep.
        deadline = time.time() + 5.0
        while time.time() < deadline and runner.current_cycle < 1:
            time.sleep(0.02)
        time.sleep(0.1)  # let it settle into the sleep loop

        runner.stop()
        thread.join(timeout=3.0)
        assert not thread.is_alive(), "stop() during inter-cycle sleep must not wait out the interval"

        loop_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.endswith("_stopme_loop")]
        ok_records = [r for r in _manifest_records(loop_dirs[0]) if r["status"] == "ok"]
        assert len(ok_records) == 1

    def test_disk_space_check_aborts_before_new_cycle(self, tmp_path, monkeypatch):
        runner = _make_runner(tmp_path)

        class _FakeUsage:
            free = 1  # far below MIN_FREE_DISK_BYTES

        monkeypatch.setattr(
            "robocam.experiment.shutil.disk_usage", lambda path: _FakeUsage()
        )
        runner.run_loop(
            name="full", positions=[(0, 0, 0)], labels=["A1"],
            interval_s=0.0, duration_s=60.0,
        )

        assert runner.current_cycle == 0
        loop_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.endswith("_full_loop")]
        records = _manifest_records(loop_dirs[0])
        assert len(records) == 1
        assert records[0]["status"] == "aborted_disk_full"
        assert [d for d in loop_dirs[0].iterdir() if d.is_dir()] == []


class TestEstimateCycleDuration:
    """estimate_cycle_duration_s() is the single source of truth run()'s own
    ETA and loop mode's pre-flight interval-vs-duration check both use."""

    def test_returns_none_without_motion_profile(self, tmp_path):
        runner = _make_runner(tmp_path)  # default _FakeMotion: supports_profiles=False
        result = runner.estimate_cycle_duration_s([(0, 0, 0)], delay_per_well=1.0, mode="image")
        assert result is None

    def test_returns_estimate_with_motion_profile(self, tmp_path):
        motion = _FakeMotion(supports_profiles=True)
        runner = _make_runner(tmp_path, motion=motion)
        result = runner.estimate_cycle_duration_s([(10.0, 0.0, 0.0)], delay_per_well=1.0, mode="image")
        assert result is not None
        total_s, move_estimates = result
        assert total_s > 1.0  # at least the dwell time
        assert len(move_estimates) == 1

    def test_passed_in_profile_skips_hardware_read(self, tmp_path):
        # A caller recomputing this live on every UI change (e.g. well
        # selection) must not trigger a fresh motion.read_profiles() call --
        # for Marlin that's a real serial round-trip. supports_profiles is
        # left False here specifically so a fallback read would return None,
        # proving the profile= value is what's actually used.
        motion = _FakeMotion(supports_profiles=False)
        runner = _make_runner(tmp_path, motion=motion)
        cached_profile = {
            "max_feed_x": 100.0, "max_accel_x": 500.0,
            "max_feed_y": 100.0, "max_accel_y": 500.0,
            "max_feed_z": 20.0, "max_accel_z": 100.0,
        }
        result = runner.estimate_cycle_duration_s(
            [(10.0, 0.0, 0.0)], delay_per_well=1.0, mode="image", profile=cached_profile,
        )
        assert result is not None
        total_s, _ = result
        assert total_s > 1.0

    def test_default_path_never_calls_read_profiles(self, tmp_path):
        # run() calls this once per cycle in loop mode -- profile=None (the
        # default) must go through the cache (get_cached_profile()), never
        # a fresh read_profiles() call, or a multi-day loop would hammer
        # the printer with hundreds of unnecessary M503 round-trips.
        class _AssertNoLiveRead(_FakeMotion):
            def read_profiles(self):
                raise AssertionError("read_profiles() must not be called by the default path")

            def get_cached_profile(self):
                # Must NOT delegate to read_profiles() -- that's the whole
                # point of a cache: available with zero hardware I/O.
                return {
                    "max_feed_x": 100.0, "max_accel_x": 500.0,
                    "max_feed_y": 100.0, "max_accel_y": 500.0,
                    "max_feed_z": 20.0, "max_accel_z": 100.0,
                }

        motion = _AssertNoLiveRead(supports_profiles=True)
        runner = _make_runner(tmp_path, motion=motion)
        result = runner.estimate_cycle_duration_s([(10.0, 0.0, 0.0)], delay_per_well=1.0, mode="image")
        assert result is not None


class TestEstimateLoopCycleCount:
    def test_back_to_back_uses_pass_time_as_period(self):
        # interval_s=0 -> cycles run back-to-back, period is just pass_s.
        assert estimate_loop_cycle_count(pass_s=10.0, interval_s=0.0, duration_s=95.0) == 10

    def test_interval_longer_than_pass_time_dominates(self):
        assert estimate_loop_cycle_count(pass_s=5.0, interval_s=30.0, duration_s=95.0) == 4

    def test_pass_time_longer_than_interval_dominates(self):
        # A too-short interval doesn't speed anything up -- period is still
        # bounded by how long a pass actually takes.
        assert estimate_loop_cycle_count(pass_s=30.0, interval_s=5.0, duration_s=95.0) == 4

    def test_at_least_one_cycle_if_duration_positive(self):
        assert estimate_loop_cycle_count(pass_s=1000.0, interval_s=0.0, duration_s=1.0) == 1

    def test_zero_duration_or_pass_time_gives_zero(self):
        assert estimate_loop_cycle_count(pass_s=10.0, interval_s=0.0, duration_s=0.0) == 0
        assert estimate_loop_cycle_count(pass_s=0.0, interval_s=0.0, duration_s=60.0) == 0
