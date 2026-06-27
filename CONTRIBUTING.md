# Contributing to RoboCam

Thank you for your interest in contributing. RoboCam is scientific software
used for well-plate imaging on Raspberry Pi; contributions that improve
hardware compatibility, robustness, or reusability are especially welcome.

## Quick start

```bash
git clone https://github.com/dairyking98/RoboCam3.1.git
cd RoboCam3.1
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Running the tests

The test suite runs without any physical hardware:

```bash
pytest tests/ -v
```

Tests cover bilinear interpolation, config persistence, and the CLI argument
parser. If you add a new feature, please add corresponding tests.

## Code style

- Python 3.10+ with type hints where practical.
- No formatter is enforced, but keep lines under 100 characters.
- Prefer explicit imports over `from module import *`.

## Submitting changes

1. Fork the repository and create a branch from `master`.
2. Make your changes, add or update tests, and confirm `pytest` passes.
3. Open a pull request with a clear description of what the change does and why.
4. Reference any related issues with `Fixes #N` or `Related to #N`.

## Reporting bugs

Open an issue at https://github.com/dairyking98/RoboCam3.1/issues and include:
- OS and Python version
- Hardware (printer backend, camera model)
- Steps to reproduce
- Relevant log output or error traceback

## Hardware-specific contributions

Because most contributors won't have a Player One camera or a Marlin printer,
hardware-specific PRs are especially welcome even without a live test run —
include a description of what you tested manually and what you could not.

The `--simulate` flag and `SimulationBackend` (`robocam/motion.py`) let you
exercise most of the application logic without any physical hardware attached.
