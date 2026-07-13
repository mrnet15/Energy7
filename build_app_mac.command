#!/bin/bash
# ===== Build Energy7.app  (run this ON a Mac) =====
# Produces:  dist/Energy7.app
# Note: an app can only be built on macOS. ffmpeg is expected on the user's PATH
# (brew install ffmpeg) rather than bundled.

cd "$(dirname "$0")" || exit 1

if [ ! -d ".venv" ]; then
    python3 -m venv .venv || { echo "Need Python 3."; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installing build tools and dependencies..."
pip install -q --upgrade pip
pip install -q pyinstaller -r requirements.txt

ICON=""
if [ -f "energy7.icns" ]; then ICON="--icon energy7.icns"; fi

echo "Building Energy7.app (this can take a few minutes)..."
pyinstaller --noconfirm --clean --windowed --name Energy7 \
  --collect-all librosa \
  --collect-all sklearn \
  --collect-all soundfile \
  --collect-all sounddevice \
  --collect-all numba \
  --collect-all pyloudnorm \
  --collect-all tkinterdnd2 \
  --hidden-import scipy.special.cython_special \
  --exclude-module numba.cuda \
  --exclude-module numba.tests \
  --exclude-module sklearn.tests \
  --exclude-module sklearn.datasets \
  --exclude-module pytest \
  --exclude-module matplotlib \
  $ICON \
  energy7.py

echo ""
echo "Done -> dist/Energy7.app"
echo "First launch: right-click the app > Open (to bypass Gatekeeper's unsigned-app warning)."
