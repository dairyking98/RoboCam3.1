"""Tests for Config get/set/deep-update and persistence."""
import json
import pytest

from robocam.config import Config


@pytest.fixture
def cfg(tmp_path):
    """A fresh Config instance backed by a temp file."""
    return Config(config_file=str(tmp_path / "test_config.json"))


class TestConfigGet:
    def test_get_top_level_default(self, cfg):
        assert cfg.get("hardware") is not None

    def test_get_nested_key(self, cfg):
        assert cfg.get("hardware.laser.mode") == "disabled"

    def test_get_missing_key_returns_default(self, cfg):
        assert cfg.get("does.not.exist", "fallback") == "fallback"

    def test_get_missing_key_returns_none_by_default(self, cfg):
        assert cfg.get("does.not.exist") is None


class TestConfigSet:
    def test_set_existing_key(self, cfg):
        cfg.set("hardware.laser.mode", "rpi_gpio")
        assert cfg.get("hardware.laser.mode") == "rpi_gpio"

    def test_set_new_nested_key(self, cfg):
        cfg.set("custom.deeply.nested", 42)
        assert cfg.get("custom.deeply.nested") == 42

    def test_set_persists_to_disk(self, tmp_path):
        path = str(tmp_path / "persist.json")
        cfg = Config(config_file=path)
        cfg.set("hardware.laser.mode", "klipper")

        cfg2 = Config(config_file=path)
        assert cfg2.get("hardware.laser.mode") == "klipper"

    def test_set_integer_value(self, cfg):
        cfg.set("hardware.printer.baudrate", 9600)
        assert cfg.get("hardware.printer.baudrate") == 9600


class TestConfigDefaults:
    def test_default_motion_backend(self, cfg):
        assert cfg.get("hardware.motion_backend") == "marlin"

    def test_default_laser_pin(self, cfg):
        assert cfg.get("hardware.laser.rpi_pin") == 21

    def test_default_output_dir(self, cfg):
        assert cfg.get("paths.output_dir") == "outputs"


class TestConfigFileCreation:
    def test_creates_file_if_missing(self, tmp_path):
        path = tmp_path / "new_config.json"
        assert not path.exists()
        Config(config_file=str(path))
        assert path.exists()

    def test_created_file_is_valid_json(self, tmp_path):
        path = tmp_path / "new_config.json"
        Config(config_file=str(path))
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)
