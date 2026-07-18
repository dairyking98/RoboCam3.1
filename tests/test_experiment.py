"""Tests for the raw-burst .npy stack trim helper."""
import numpy as np
import pytest

from robocam.experiment import _trim_raw_stack


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
