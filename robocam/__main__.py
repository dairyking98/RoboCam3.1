"""
CLI entry point for the RoboCam hardware layer.

Usage:
    python -m robocam [--simulate] [--verbose] <command> [args]

Commands:
    status               Probe and report available hardware
    motion pos           Print current position
    motion home          Home all axes (G28)
    motion move          Move to absolute position (--x --y --z --speed)
    motion gcode CMD     Send a raw G-code command
    camera info          Show camera backend and resolution
    camera capture       Capture one frame and save it
    config show          Dump the active config as JSON
    config set KEY VALUE Write a config key (dot-path notation)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logging.basicConfig(
        format="%(levelname)s %(name)s: %(message)s",
        level=level,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(os.path.join(project_root, "robocam.log"), mode="w"),
        ],
    )


def _get_motion(simulate: bool):
    from robocam.motion import MotionController
    return MotionController(simulate=simulate)


def _get_camera(simulate: bool, resolution: tuple[int, int] = (1280, 720)):
    from robocam.camera import Camera
    return Camera(resolution=resolution, simulate=simulate)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    from robocam.config import get_config
    from robocam.camera import get_playerone_camera_count, PICAM2_AVAILABLE

    cfg = get_config()
    print("=== RoboCam 3.1 status ===")
    print(f"Config file : {cfg.config_file}")
    print(f"Motion backend : {cfg.get('hardware.motion_backend', 'marlin')}")
    print(f"Laser mode  : {cfg.get('hardware.laser.mode', 'disabled')}")
    print()

    # Camera probe
    print("--- Camera ---")
    poa_count = get_playerone_camera_count()
    print(f"  PlayerOne cameras detected : {poa_count}")
    print(f"  Picamera2 available        : {PICAM2_AVAILABLE}")
    try:
        import cv2  # noqa: F401
        print("  OpenCV (cv2) available     : yes")
    except ImportError:
        print("  OpenCV (cv2) available     : no")

    if args.simulate:
        print("  [simulate] camera backend  : simulate")
    elif poa_count > 0:
        print("  Active backend             : playerone")
    elif PICAM2_AVAILABLE:
        print("  Active backend             : picamera2")
    else:
        print("  Active backend             : cv2 (fallback)")

    # Motion probe
    print()
    print("--- Motion ---")
    if args.simulate:
        print("  [simulate] motion backend  : simulate")
    else:
        backend_type = cfg.get("hardware.motion_backend", "marlin")
        if backend_type == "klipper":
            host = cfg.get("hardware.klipper.host", "127.0.0.1")
            port = cfg.get("hardware.klipper.port", 7125)
            print(f"  Trying Klipper @ {host}:{port}")
            try:
                import requests
                resp = requests.get(f"http://{host}:{port}/printer/info", timeout=3.0)
                state = resp.json().get("result", {}).get("state", "unknown")
                print(f"  Klipper state : {state}")
            except Exception as e:
                print(f"  Klipper unreachable : {e}")
        else:
            import serial.tools.list_ports
            ports = list(serial.tools.list_ports.comports())
            if ports:
                for p in ports:
                    print(f"  Serial port : {p.device}  ({p.description})")
            else:
                print("  No serial ports detected")

            print()
            print("--- Motion connect probe ---")
            try:
                from robocam.motion import MotionController
                mc = MotionController(simulate=False)
                x, y, z = mc.X, mc.Y, mc.Z
                homed = mc.is_homed
                mc.disconnect()
                print(f"  Position    : X={x:.3f}  Y={y:.3f}  Z={z:.3f}")
                print(f"  Homed       : {'yes' if homed else 'NO — home required before experiments'}")
            except Exception as e:
                print(f"  Connect failed: {e}")

    return 0


# ---------------------------------------------------------------------------
# motion subcommands
# ---------------------------------------------------------------------------

def cmd_motion_pos(args: argparse.Namespace) -> int:
    mc = _get_motion(args.simulate)
    try:
        x, y, z = mc.update_position()
        print(f"X={x:.3f}  Y={y:.3f}  Z={z:.3f}")
    finally:
        mc.disconnect()
    return 0


def cmd_motion_home(args: argparse.Namespace) -> int:
    print("Homing… (this may take up to 90 s)")
    mc = _get_motion(args.simulate)
    try:
        mc.home()
        x, y, z = mc.X, mc.Y, mc.Z
        print(f"Homed. Position: X={x:.3f}  Y={y:.3f}  Z={z:.3f}")
    finally:
        mc.disconnect()
    return 0


def cmd_motion_move(args: argparse.Namespace) -> int:
    mc = _get_motion(args.simulate)
    try:
        kwargs: dict = {}
        if args.x is not None: kwargs["X"] = args.x
        if args.y is not None: kwargs["Y"] = args.y
        if args.z is not None: kwargs["Z"] = args.z
        if args.speed is not None: kwargs["speed"] = args.speed
        if not kwargs:
            print("Error: specify at least one of --x, --y, --z", file=sys.stderr)
            return 1
        mc.move_absolute(**kwargs)
        print(f"Moved. Position: X={mc.X:.3f}  Y={mc.Y:.3f}  Z={mc.Z:.3f}")
    finally:
        mc.disconnect()
    return 0


def cmd_motion_gcode(args: argparse.Namespace) -> int:
    mc = _get_motion(args.simulate)
    try:
        response = mc.backend.send_gcode(args.raw)
        print(response or "(no response)")
    finally:
        mc.disconnect()
    return 0


# ---------------------------------------------------------------------------
# camera subcommands
# ---------------------------------------------------------------------------

def cmd_camera_info(args: argparse.Namespace) -> int:
    cam = _get_camera(args.simulate)
    try:
        print(f"Backend    : {cam.backend or 'none'}")
        print(f"Resolution : {cam.resolution[0]}x{cam.resolution[1]}")
        print(f"Running    : {cam.running}")
        if cam.running:
            print(f"Exposure   : {cam.get_exposure()} µs")
            print(f"Gain       : {cam.get_gain()}")
            resolutions = cam.get_supported_resolutions()
            print(f"Supported resolutions: {[f'{w}x{h}' for w, h in resolutions]}")
    finally:
        cam.stop()
    return 0


def cmd_camera_capture(args: argparse.Namespace) -> int:
    import numpy as np

    cam = _get_camera(args.simulate)
    try:
        if not cam.running:
            print("Error: camera did not open", file=sys.stderr)
            return 1

        import time
        time.sleep(0.5)  # let camera stabilise

        frame = cam.get_frame()
        if frame is None:
            print("Error: got no frame from camera", file=sys.stderr)
            return 1

        out = args.output or "capture.jpg"
        import cv2
        cv2.imwrite(out, frame)
        h, w = frame.shape[:2]
        print(f"Saved {w}x{h} frame → {out}")
    finally:
        cam.stop()
    return 0


# ---------------------------------------------------------------------------
# config subcommands
# ---------------------------------------------------------------------------

def cmd_config_show(args: argparse.Namespace) -> int:
    from robocam.config import get_config
    cfg = get_config()
    print(json.dumps(cfg.config, indent=2))
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    from robocam.config import get_config
    cfg = get_config()
    raw = args.value
    # Try to coerce to int/float/bool before storing as string
    try:
        value: object = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    cfg.set(args.key, value)
    print(f"Set {args.key} = {value!r}")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m robocam",
        description="RoboCam 3.1 headless hardware CLI",
    )
    root.add_argument("--simulate", "-s", action="store_true",
                      help="Use simulation backends (no real hardware)")
    root.add_argument("--verbose", "-v", action="store_true",
                      help="Enable debug logging")

    sub = root.add_subparsers(dest="command", required=True)

    # --- status ---
    sub.add_parser("status", help="Probe and report available hardware")

    # --- motion ---
    motion_p = sub.add_parser("motion", help="Motion controller commands")
    motion_sub = motion_p.add_subparsers(dest="motion_cmd", required=True)

    motion_sub.add_parser("pos", help="Print current position")
    motion_sub.add_parser("home", help="Home all axes (G28)")

    move_p = motion_sub.add_parser("move", help="Absolute move")
    move_p.add_argument("--x", type=float, default=None)
    move_p.add_argument("--y", type=float, default=None)
    move_p.add_argument("--z", type=float, default=None)
    move_p.add_argument("--speed", type=float, default=None, help="Feed rate (mm/min)")

    gcode_p = motion_sub.add_parser("gcode", help="Send raw G-code")
    gcode_p.add_argument("raw", help="G-code string, e.g. 'G28'")

    # --- camera ---
    camera_p = sub.add_parser("camera", help="Camera commands")
    camera_sub = camera_p.add_subparsers(dest="camera_cmd", required=True)

    camera_sub.add_parser("info", help="Show camera backend info")

    cap_p = camera_sub.add_parser("capture", help="Capture one frame")
    cap_p.add_argument("--output", "-o", default=None,
                       help="Output file path (default: capture.jpg)")

    # --- config ---
    config_p = sub.add_parser("config", help="Configuration commands")
    config_sub = config_p.add_subparsers(dest="config_cmd", required=True)

    config_sub.add_parser("show", help="Dump active config as JSON")

    set_p = config_sub.add_parser("set", help="Set a config key")
    set_p.add_argument("key", help="Dot-path key, e.g. hardware.laser.mode")
    set_p.add_argument("value", help="Value (JSON or string)")

    return root


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    _setup_logging(args.verbose)

    try:
        if args.command == "status":
            return cmd_status(args)

        elif args.command == "motion":
            if args.motion_cmd == "pos":
                return cmd_motion_pos(args)
            elif args.motion_cmd == "home":
                return cmd_motion_home(args)
            elif args.motion_cmd == "move":
                return cmd_motion_move(args)
            elif args.motion_cmd == "gcode":
                return cmd_motion_gcode(args)

        elif args.command == "camera":
            if args.camera_cmd == "info":
                return cmd_camera_info(args)
            elif args.camera_cmd == "capture":
                return cmd_camera_capture(args)

        elif args.command == "config":
            if args.config_cmd == "show":
                return cmd_config_show(args)
            elif args.config_cmd == "set":
                return cmd_config_set(args)

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        logging.getLogger(__name__).debug("Unhandled exception", exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
