@echo off
REM ================================================================
REM  get_ffmpeg.bat  -  downloads ffmpeg.exe for Energy 7
REM
REM  Run this ONCE. It fetches ffmpeg for Windows and puts
REM  ffmpeg.exe in this folder (and next to Energy7.exe if built),
REM  so Energy 7 can read/write MP3s with nothing else installed.
REM ================================================================
setlocal
cd /d "%~dp0"

if exist "ffmpeg.exe" (
    echo ffmpeg.exe is already in this folder - nothing to do.
    goto :place
)

echo Downloading ffmpeg for Windows (about 80 MB, please wait)...
set "URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

curl -L --fail -o "ffmpeg_tmp.zip" "%URL%"
if errorlevel 1 (
    echo curl did not work, trying PowerShell instead...
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%URL%' -OutFile 'ffmpeg_tmp.zip' } catch { exit 1 }"
)

if not exist "ffmpeg_tmp.zip" (
    echo.
    echo Automatic download failed. Please do it manually:
    echo   1. Open https://www.gyan.dev/ffmpeg/builds/
    echo   2. Download "ffmpeg-release-essentials.zip"
    echo   3. Unzip it and copy  bin\ffmpeg.exe  next to this file.
    goto :done
)

echo Extracting ffmpeg.exe...
powershell -NoProfile -Command "Expand-Archive -Force 'ffmpeg_tmp.zip' 'ffmpeg_tmp'"
for /r "ffmpeg_tmp" %%F in (ffmpeg.exe) do copy /y "%%F" "ffmpeg.exe" >nul

del /q "ffmpeg_tmp.zip" >nul 2>&1
rmdir /s /q "ffmpeg_tmp" >nul 2>&1

if not exist "ffmpeg.exe" (
    echo.
    echo Extraction failed - please grab ffmpeg.exe manually (see link above).
    goto :done
)
echo SUCCESS - ffmpeg.exe is now in this folder.

:place
REM If the exe has already been built, drop a copy beside it so the
REM CURRENT Energy7.exe works right away without rebuilding.
if exist "dist\Energy7.exe" (
    copy /y "ffmpeg.exe" "dist\ffmpeg.exe" >nul
    echo Also copied ffmpeg.exe next to dist\Energy7.exe.
)

:done
if /I not "%~1"=="nopause" pause
endlocal
