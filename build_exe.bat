@echo off
REM ================================================================
REM  Build Energy7.exe  (run this ON your Windows machine)
REM  Energy 7 - made by mrnet15/claude
REM
REM  Produces:  dist\Energy7.exe   (a single double-clickable file)
REM
REM  Optional: drop an "ffmpeg.exe" into THIS folder before building
REM  and it will be bundled inside the exe, so the program is fully
REM  self-contained and needs nothing installed on other PCs.
REM ================================================================

cd /d "%~dp0"

echo Installing build tools and dependencies...
python -m pip install --disable-pip-version-check -q --upgrade pip
python -m pip install --disable-pip-version-check -q pyinstaller -r requirements.txt

REM enum34 is an obsolete backport that breaks PyInstaller - remove it if present.
python -m pip uninstall -y enum34 >nul 2>&1

REM Make sure ffmpeg.exe is here so it can be baked into the exe.
REM If it's missing, download it automatically (needs internet).
if not exist "ffmpeg.exe" (
    echo No ffmpeg.exe found - fetching it so it can be bundled...
    if exist "get_ffmpeg.bat" call get_ffmpeg.bat nopause
)

set FFMPEG_ADD=
if exist "ffmpeg.exe" (
    echo Found ffmpeg.exe - it will be BUNDLED inside Energy7.exe.
    set FFMPEG_ADD=--add-binary "ffmpeg.exe;."
) else (
    echo WARNING: no ffmpeg.exe to bundle. The exe will need ffmpeg on PATH.
)

REM Bundle the logo/icon so the window and taskbar show it.
set ICON_ADD=
if exist "energy7.ico" set ICON_ADD=--icon energy7.ico --add-data "energy7.ico;."
if exist "energy7.png" set ICON_ADD=%ICON_ADD% --add-data "energy7.png;."

echo.
echo Building Energy7.exe  (this can take a few minutes)...
pyinstaller --noconfirm --clean --onefile --windowed --name Energy7 ^
  --collect-all librosa ^
  --collect-all sklearn ^
  --collect-all soundfile ^
  --collect-all sounddevice ^
  --collect-all numba ^
  --collect-all pyloudnorm ^
  --collect-all tkinterdnd2 ^
  --hidden-import scipy.special.cython_special ^
  %ICON_ADD% ^
  --exclude-module numba.cuda ^
  --exclude-module numba.tests ^
  --exclude-module numba.cuda.tests ^
  --exclude-module sklearn.tests ^
  --exclude-module sklearn.datasets ^
  --exclude-module pytest ^
  --exclude-module matplotlib ^
  --exclude-module IPython ^
  --exclude-module tkinter.test ^
  --exclude-module torch ^
  %FFMPEG_ADD% ^
  energy7.py

if errorlevel 1 (
    echo.
    echo Build failed. Scroll up to see the error.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done!  Your program is:  dist\Energy7.exe
echo  Double-click it to run. You can copy that single file
echo  anywhere on your PC.
echo ============================================================
pause
