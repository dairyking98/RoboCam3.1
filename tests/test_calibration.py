"""Tests for WellPlate bilinear interpolation and path generation."""
import json
import os
import pytest

from robocam.calibration import WellPlate, CalibrationManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UNIT_CORNERS = [
    (0.0, 0.0, 0.0),   # upper-left
    (0.0, 1.0, 0.0),   # lower-left
    (1.0, 0.0, 0.0),   # upper-right
    (1.0, 1.0, 0.0),   # lower-right
]

REAL_CORNERS = [
    (10.0, 20.0, 5.0),   # upper-left
    (10.0, 100.0, 5.0),  # lower-left
    (90.0, 20.0, 5.0),   # upper-right
    (90.0, 100.0, 5.0),  # lower-right
]


# ---------------------------------------------------------------------------
# Bilinear interpolation
# ---------------------------------------------------------------------------

class TestInterpolation:
    def test_single_well_returns_upper_left(self):
        plate = WellPlate(1, 1, UNIT_CORNERS)
        assert plate.path == [(0.0, 0.0, 0.0)]

    def test_corners_exact(self):
        plate = WellPlate(2, 2, UNIT_CORNERS)
        positions = plate.path
        assert len(positions) == 4
        assert positions[0] == pytest.approx((0.0, 0.0, 0.0))  # UL
        assert positions[1] == pytest.approx((1.0, 0.0, 0.0))  # UR
        assert positions[2] == pytest.approx((0.0, 1.0, 0.0))  # LL
        assert positions[3] == pytest.approx((1.0, 1.0, 0.0))  # LR

    def test_centre_well(self):
        """Centre of a 3x3 plate on unit square should be (0.5, 0.5, 0.0)."""
        plate = WellPlate(3, 3, UNIT_CORNERS)
        centre = plate._interpolate(1, 1)
        assert centre == pytest.approx((0.5, 0.5, 0.0))

    def test_z_constant(self):
        """Z should be constant when all corners share the same Z."""
        plate = WellPlate(4, 3, REAL_CORNERS)
        for pos in plate.path:
            assert pos[2] == pytest.approx(5.0)

    def test_x_range(self):
        """X values should stay within the corner X range."""
        plate = WellPlate(12, 8, REAL_CORNERS)
        xs = [p[0] for p in plate.path]
        assert min(xs) == pytest.approx(10.0)
        assert max(xs) == pytest.approx(90.0)

    def test_y_range(self):
        """Y values should stay within the corner Y range."""
        plate = WellPlate(12, 8, REAL_CORNERS)
        ys = [p[1] for p in plate.path]
        assert min(ys) == pytest.approx(20.0)
        assert max(ys) == pytest.approx(100.0)

    def test_wrong_corner_count_raises(self):
        with pytest.raises(ValueError):
            WellPlate(2, 2, UNIT_CORNERS[:3])


# ---------------------------------------------------------------------------
# Path generation — raster
# ---------------------------------------------------------------------------

class TestRasterPath:
    def test_total_wells(self):
        plate = WellPlate(12, 8, UNIT_CORNERS, pattern=WellPlate.PATTERN_RASTER)
        assert len(plate.path) == 96

    def test_row_order_left_to_right(self):
        """Every row in raster order should go left-to-right (increasing X)."""
        plate = WellPlate(4, 3, UNIT_CORNERS, pattern=WellPlate.PATTERN_RASTER)
        for row in range(3):
            row_positions = plate.path[row * 4 : (row + 1) * 4]
            xs = [p[0] for p in row_positions]
            assert xs == sorted(xs), f"Row {row} is not left-to-right: {xs}"


# ---------------------------------------------------------------------------
# Path generation — snake
# ---------------------------------------------------------------------------

class TestSnakePath:
    def test_total_wells(self):
        plate = WellPlate(12, 8, UNIT_CORNERS, pattern=WellPlate.PATTERN_SNAKE)
        assert len(plate.path) == 96

    def test_even_rows_left_to_right(self):
        plate = WellPlate(4, 4, UNIT_CORNERS, pattern=WellPlate.PATTERN_SNAKE)
        row0 = plate.path[0:4]
        xs = [p[0] for p in row0]
        assert xs == sorted(xs)

    def test_odd_rows_right_to_left(self):
        plate = WellPlate(4, 4, UNIT_CORNERS, pattern=WellPlate.PATTERN_SNAKE)
        row1 = plate.path[4:8]
        xs = [p[0] for p in row1]
        assert xs == sorted(xs, reverse=True)

    def test_snake_visits_same_set_of_positions_as_raster(self):
        raster = WellPlate(4, 3, UNIT_CORNERS, pattern=WellPlate.PATTERN_RASTER)
        snake = WellPlate(4, 3, UNIT_CORNERS, pattern=WellPlate.PATTERN_SNAKE)
        assert sorted(raster.path) == sorted(snake.path)


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

class TestLabels:
    def test_first_label_a1(self):
        plate = WellPlate(12, 8, UNIT_CORNERS)
        labelled = plate.get_path_with_labels()
        assert labelled[0][0] == "A1"

    def test_standard_96_well_labels(self):
        plate = WellPlate(12, 8, UNIT_CORNERS)
        labels = [item[0] for item in plate.get_path_with_labels()]
        assert labels[0] == "A1"
        assert labels[11] == "A12"
        assert labels[12] == "B1"
        assert labels[95] == "H12"

    def test_label_count_matches_well_count(self):
        plate = WellPlate(6, 4, UNIT_CORNERS)
        labelled = plate.get_path_with_labels()
        assert len(labelled) == 24


# ---------------------------------------------------------------------------
# CalibrationManager save / load round-trip
# ---------------------------------------------------------------------------

class TestCalibrationManagerRoundTrip:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mgr = CalibrationManager()
        mgr.upper_left = REAL_CORNERS[0]
        mgr.lower_left = REAL_CORNERS[1]
        mgr.upper_right = REAL_CORNERS[2]
        mgr.lower_right = REAL_CORNERS[3]
        mgr.width = 12
        mgr.depth = 8
        mgr.pattern = WellPlate.PATTERN_RASTER

        mgr.save("test_plate")

        cal_file = os.path.join(mgr.cal_dir, "test_plate.json")
        assert os.path.exists(cal_file)

        mgr2 = CalibrationManager()
        positions, labels = mgr2.load(cal_file)

        assert len(positions) == 96
        assert len(labels) == 96
        assert labels[0] == "A1"
        assert labels[95] == "H12"

    def test_saved_json_has_required_keys(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mgr = CalibrationManager()
        mgr.upper_left = REAL_CORNERS[0]
        mgr.lower_left = REAL_CORNERS[1]
        mgr.upper_right = REAL_CORNERS[2]
        mgr.lower_right = REAL_CORNERS[3]
        mgr.save("check_keys")

        with open(os.path.join(mgr.cal_dir, "check_keys.json")) as f:
            data = json.load(f)

        for key in ("name", "interpolated_positions", "labels", "upper_left",
                    "lower_left", "upper_right", "lower_right"):
            assert key in data, f"Missing key: {key}"
