@echo off
REM =============================================================================
REM RoboCam 3.1 — Setup Script (Windows)
REM =============================================================================
REM Creates a virtual environment named .venv and installs all dependencies.
REM Run once after cloning:
REM   setup.bat
REM
REM To activate the environment afterwards:
REM   .venv\Scripts\activate
REM =============================================================================

echo =^> Checking Python version...
python --version
IF ERRORLEVEL 1 (
    echo ERROR: Python not found. Please install Python 3.10+ and add it to PATH.
    exit /b 1
)

echo =^> Creating virtual environment in '.venv'...
python -m venv .venv

echo =^> Activating virtual environment...
call .venv\Scripts\activate

echo =^> Upgrading pip...
pip install --upgrade pip

echo =^> Installing dependencies...
pip install -r requirements.txt

echo =^> Installing Player One Camera SDK (pyPOACamera + PlayerOneCamera.dll)...
echo    Downloads SDK from player-one-astronomy.com into PlayerOne_Camera_SDK_Linux_V3.10.0\
echo    Safe to skip if you don't have a Player One camera.
python scripts\install_playerone_sdk.py
IF ERRORLEVEL 1 (
    echo WARNING: Player One SDK install failed or was skipped.
    echo          To install manually later:  python scripts\install_playerone_sdk.py
)

echo.
echo ============================================================
echo  Setup complete!
echo.
echo  To activate the environment, run:
echo    .venv\Scripts\activate
echo.
echo  To launch the application:
echo    python robocam31.py
echo ============================================================
