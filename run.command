#!/bin/bash
# ===== Energy 7 launcher for macOS / Linux  (made by mrnet15/claude) =====
# Double-click this file (macOS) or run: bash run.command
# First run creates a local environment and installs the packages.

cd "$(dirname "$0")" || exit 1

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg not found. Run get_ffmpeg.command first (or: brew install ffmpeg)."
fi

if [ ! -d ".venv" ]; then
    echo "Setting up (first run only)..."
    python3 -m venv .venv || { echo "Could not create venv. Is Python 3 installed?"; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "Checking Python packages (first run may take a minute)..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "Starting Energy 7..."
python energy7.py
