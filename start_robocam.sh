#!/bin/bash

# RoboCam 3.1 Startup Script
# This script activates the virtual environment and starts the main application.

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "=== Starting RoboCam 3.1 ==="

# Check if .venv exists
if [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
else
    echo "Warning: .venv directory not found. Running with system python."
fi

# Run the application
echo "Launching main application..."
python3 robocam31.py

echo "=== RoboCam 3.1 Closed ==="
