"""
hw_state.py — module-level shared hardware singletons.

All UI panels import from here so they share a single Camera,
MotionController, and ExperimentRunner instance.  The Setup panel
calls set_camera() / set_motion() when reconnecting hardware.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from robocam.camera import Camera
    from robocam.motion import MotionController
    from robocam.experiment import ExperimentRunner

_camera: "Camera | None" = None
_motion: "MotionController | None" = None
_runner: "ExperimentRunner | None" = None


def get_camera() -> "Camera | None":
    return _camera


def get_motion() -> "MotionController | None":
    return _motion


def get_runner() -> "ExperimentRunner | None":
    return _runner


def set_camera(camera: "Camera | None") -> None:
    global _camera
    _camera = camera


def set_motion(motion: "MotionController | None") -> None:
    global _motion, _runner
    _motion = motion
    # Rebuild the runner whenever the motion controller changes
    if motion is not None and _camera is not None:
        from robocam.experiment import ExperimentRunner
        _runner = ExperimentRunner(motion, _camera)
    else:
        _runner = None


def rebuild_runner() -> None:
    """Re-create the ExperimentRunner from current camera + motion instances."""
    global _runner
    if _motion is not None and _camera is not None:
        from robocam.experiment import ExperimentRunner
        _runner = ExperimentRunner(_motion, _camera)
    else:
        _runner = None
