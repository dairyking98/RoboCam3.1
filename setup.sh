#!/usr/bin/env bash
# =============================================================================
# RoboCam 3.1 — Setup Script (Linux / macOS)
# =============================================================================
# Creates a virtual environment named .venv and installs all dependencies.
# Run once after cloning:
#   bash setup.sh
#
# To launch afterwards:
#   source .venv/bin/activate && python robocam31.py
#   -- or --
#   bash start_robocam.sh
# =============================================================================

set -e

VENV_DIR=".venv"
PYTHON="${PYTHON:-python3}"

echo "==> Checking Python version..."
$PYTHON --version

# ---------------------------------------------------------------------------
# Raspberry Pi detection — checks /proc/device-tree/model (most reliable),
# then /etc/os-release, then /etc/rpi-issue as fallbacks.
# ---------------------------------------------------------------------------
is_raspberry_pi() {
    if [ -f /proc/device-tree/model ] && grep -qi "raspberry pi" /proc/device-tree/model; then
        return 0
    fi
    if [ -f /etc/os-release ] && grep -qi "raspberry pi" /etc/os-release; then
        return 0
    fi
    if [ -f /etc/rpi-issue ]; then
        return 0
    fi
    return 1
}

ON_PI=false
if is_raspberry_pi; then
    ON_PI=true
    PI_MODEL=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || echo "Raspberry Pi")
    echo "==> Raspberry Pi detected: $PI_MODEL"
fi

# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------
echo "==> Creating virtual environment in '$VENV_DIR'..."
if [ "$ON_PI" = true ]; then
    # --system-site-packages lets the venv reach libcamera / picamera2
    # which can only be installed system-wide via apt on Pi OS.
    echo "    Using --system-site-packages for Raspberry Pi compatibility."
    $PYTHON -m venv --system-site-packages "$VENV_DIR"
else
    $PYTHON -m venv "$VENV_DIR"
fi

echo "==> Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo "==> Upgrading pip..."
pip install --upgrade pip

# ---------------------------------------------------------------------------
# Core dependencies
# ---------------------------------------------------------------------------
echo "==> Installing core dependencies..."
pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Raspberry Pi extras
# ---------------------------------------------------------------------------
if [ "$ON_PI" = true ]; then
    echo "==> Installing Raspberry Pi extras..."

    # RPi.GPIO — GPIO control for laser/stimulus output
    echo "    Installing RPi.GPIO..."
    pip install RPi.GPIO || echo "WARNING: RPi.GPIO install failed."

    # picamera2 — Raspberry Pi HQ camera support
    echo "    Checking libcamera system packages..."
    if ! dpkg -l 2>/dev/null | grep -q "python3-libcamera"; then
        echo "WARNING: python3-libcamera not found. Run:"
        echo "         sudo apt update && sudo apt install -y python3-libcamera python3-kms++ libcap-dev"
    fi
    echo "    Installing picamera2 Python wrapper..."
    pip install picamera2 || echo "WARNING: picamera2 install failed. Ensure libcamera-python is installed via apt."
fi

# ---------------------------------------------------------------------------
# Player One Camera SDK
# ---------------------------------------------------------------------------
echo "==> Installing Player One Camera SDK..."
echo "    Downloads into PlayerOne_Camera_SDK_Linux_V3.10.0/"
echo "    Safe to skip if you don't have a Player One camera."
python scripts/install_playerone_sdk.py || {
    echo "WARNING: Player One SDK install failed or was skipped."
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Setup complete!"
if [ "$ON_PI" = true ]; then
    echo " Platform : $PI_MODEL"
fi
echo ""
echo " To launch:"
echo "   source .venv/bin/activate"
echo "   python robocam31.py"
echo ""
echo " Or use the shortcut:"
echo "   bash start_robocam.sh"
echo "============================================================"
