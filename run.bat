@echo off
REM ==== Energy 7 launcher for Windows  (made by mrnet15/claude) ====
REM First run: installs the Python packages, then starts the app.
REM After that, just double-click this file to launch.

cd /d "%~dp0"

echo Checking Python packages (first run may take a minute)...
python -m pip install --disable-pip-version-check -q -r requirements.txt

echo Starting Energy 7...
python energy7.py

if errorlevel 1 (
    echo.
    echo Something went wrong. Make sure Python and ffmpeg are installed and on PATH.
    pause
)
