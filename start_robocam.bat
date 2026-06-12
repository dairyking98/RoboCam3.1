@echo off
REM RoboCam 3.1 Startup Script
REM Activates the virtual environment when available and starts the main application.

setlocal

REM Get the directory where this script is located and run from there.
cd /d "%~dp0"

echo === Starting RoboCam 3.1 ===

REM Check if .venv exists.
if exist ".venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call ".venv\Scripts\activate.bat"
) else (
    echo Warning: .venv directory not found. Running with system python.
)

REM Run the application.
echo Launching main application...
python robocam31.py

echo === RoboCam 3.1 Closed ===
pause

endlocal
