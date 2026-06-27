"""Tests for the headless CLI argument parser (no hardware required)."""
import pytest

from robocam.__main__ import build_parser


@pytest.fixture
def parser():
    return build_parser()


class TestParserTopLevel:
    def test_requires_command(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_simulate_flag(self, parser):
        args = parser.parse_args(["--simulate", "status"])
        assert args.simulate is True

    def test_verbose_flag(self, parser):
        args = parser.parse_args(["--verbose", "status"])
        assert args.verbose is True

    def test_defaults_no_simulate(self, parser):
        args = parser.parse_args(["status"])
        assert args.simulate is False
        assert args.verbose is False


class TestStatusCommand:
    def test_status_parsed(self, parser):
        args = parser.parse_args(["status"])
        assert args.command == "status"


class TestMotionCommands:
    def test_motion_pos(self, parser):
        args = parser.parse_args(["motion", "pos"])
        assert args.command == "motion"
        assert args.motion_cmd == "pos"

    def test_motion_home(self, parser):
        args = parser.parse_args(["motion", "home"])
        assert args.motion_cmd == "home"

    def test_motion_move_xyz(self, parser):
        args = parser.parse_args(["motion", "move", "--x", "10", "--y", "20", "--z", "5"])
        assert args.x == pytest.approx(10.0)
        assert args.y == pytest.approx(20.0)
        assert args.z == pytest.approx(5.0)

    def test_motion_move_partial(self, parser):
        args = parser.parse_args(["motion", "move", "--x", "50"])
        assert args.x == pytest.approx(50.0)
        assert args.y is None
        assert args.z is None

    def test_motion_move_speed(self, parser):
        args = parser.parse_args(["motion", "move", "--x", "0", "--speed", "3000"])
        assert args.speed == pytest.approx(3000.0)

    def test_motion_gcode(self, parser):
        args = parser.parse_args(["motion", "gcode", "G28"])
        assert args.motion_cmd == "gcode"
        assert args.raw == "G28"

    def test_motion_missing_subcommand(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["motion"])


class TestCameraCommands:
    def test_camera_info(self, parser):
        args = parser.parse_args(["camera", "info"])
        assert args.command == "camera"
        assert args.camera_cmd == "info"

    def test_camera_capture_default_output(self, parser):
        args = parser.parse_args(["camera", "capture"])
        assert args.output is None

    def test_camera_capture_with_output(self, parser):
        args = parser.parse_args(["camera", "capture", "--output", "frame.png"])
        assert args.output == "frame.png"

    def test_camera_missing_subcommand(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["camera"])


class TestConfigCommands:
    def test_config_show(self, parser):
        args = parser.parse_args(["config", "show"])
        assert args.command == "config"
        assert args.config_cmd == "show"

    def test_config_set(self, parser):
        args = parser.parse_args(["config", "set", "hardware.laser.mode", "klipper"])
        assert args.config_cmd == "set"
        assert args.key == "hardware.laser.mode"
        assert args.value == "klipper"

    def test_config_missing_subcommand(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["config"])
