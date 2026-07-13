#!/bin/bash
# ===== Get ffmpeg on macOS / Linux for Energy 7 =====
# Double-click (macOS) or run: bash get_ffmpeg.command

if command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg is already installed:  $(command -v ffmpeg)"
    exit 0
fi

if command -v brew >/dev/null 2>&1; then
    echo "Installing ffmpeg via Homebrew..."
    brew install ffmpeg
elif command -v apt-get >/dev/null 2>&1; then
    echo "Installing ffmpeg via apt..."
    sudo apt-get update && sudo apt-get install -y ffmpeg
else
    echo "Could not find Homebrew or apt."
    echo "macOS: install Homebrew from https://brew.sh  then run: brew install ffmpeg"
    echo "Or download a static build from https://evermeet.cx/ffmpeg/ and put 'ffmpeg' on your PATH."
fi
