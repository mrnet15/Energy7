#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Energy 7 - a simple automatic DJ for Windows.  Made by mrnet15/claude.

Features
--------
* Add a list of MP3 files and reorder them.
* Analyzes each track: tempo (BPM), beat grid, and where the music actually
  starts (handles long/ambient intros that only kick in a minute or two in).
* Beat-aligned crossfades between tracks (original tempo is preserved), or
  tempo-matched beat-lock mode.
* Loudness normalization (LUFS) so no track jumps out in volume.
* Builds one continuous mixed set.
* Live transport: play/pause/stop, scrub (click the bar), skip +/-10s, and
  jump to the next / previous track.
* A trippy kaleidoscope visualizer that reacts to the music.
* Save the mix as a single MP3.
* Save and load playlists (.m3u) and full projects (.bmx = JSON).

Requires: Python 3.9+, ffmpeg on PATH, and the packages in requirements.txt.
"""

import os
import sys
import json
import math
import queue
import random
import shutil
import struct
import threading
import subprocess
import traceback
import webbrowser

import numpy as np

# GUI (standard library)
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

# Optional: drag-and-drop of files onto the window (pip install tkinterdnd2)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = None
    DND_FILES = None

# Audio analysis / loudness / playback (installed via pip)
try:
    import librosa
except Exception as e:  # pragma: no cover
    librosa = None
    _LIBROSA_ERR = e

try:
    import pyloudnorm as pyln
except Exception:
    pyln = None

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    from scipy.signal import butter, filtfilt
except Exception:
    butter = filtfilt = None


__version__ = "1.1"
REPO_URL = "https://github.com/mrnet15/Energy7"

# Native-looking fonts per platform.
if sys.platform == "darwin":
    UI_FAMILY, MONO_FAMILY = "Helvetica Neue", "Menlo"
elif sys.platform.startswith("win"):
    UI_FAMILY, MONO_FAMILY = "Segoe UI", "Consolas"
else:
    UI_FAMILY, MONO_FAMILY = "DejaVu Sans", "DejaVu Sans Mono"

SR = 44100                     # working sample rate
CHANNELS = 2                   # stereo
FRAME_HOP = 512                # analysis hop length


# --------------------------------------------------------------------------- #
#  ffmpeg helpers (reliable MP3 decode / encode)
# --------------------------------------------------------------------------- #
def _no_window_kwargs():
    """
    On Windows, stop child processes (ffmpeg) from flashing a console window.
    Returns kwargs to splat into subprocess calls; empty on other platforms.
    """
    if os.name == "nt":
        CREATE_NO_WINDOW = 0x08000000
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0                      # SW_HIDE
        return {"startupinfo": si, "creationflags": CREATE_NO_WINDOW}
    return {}


def _app_dir():
    """Folder the app is running from (works for a script or a PyInstaller exe)."""
    if getattr(sys, "frozen", False):          # packaged .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _ffmpeg_bin():
    """
    Locate ffmpeg. Search order:
      1. bundled next to the exe / script (ffmpeg.exe or ffmpeg)
      2. PyInstaller's temporary unpack folder (_MEIPASS)
      3. the system PATH
    """
    names = ("ffmpeg.exe", "ffmpeg")
    candidates = [_app_dir(), getattr(sys, "_MEIPASS", _app_dir())]
    for base in candidates:
        for nm in names:
            p = os.path.join(base, nm)
            if os.path.isfile(p):
                return p
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "ffmpeg was not found. Either put ffmpeg.exe next to the program "
            "or install it and make sure 'ffmpeg' works from a Command Prompt."
        )
    return exe


def load_audio(path, sr=SR):
    """
    Decode any audio file to a float32 stereo numpy array shaped (n_samples, 2)
    in the range [-1, 1] using ffmpeg. Very reliable for MP3s.
    """
    exe = _ffmpeg_bin()
    cmd = [
        exe, "-v", "error",
        "-i", path,
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ac", str(CHANNELS),
        "-ar", str(sr),
        "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          **_no_window_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg could not read:\n%s\n%s"
            % (path, proc.stderr.decode("utf-8", "ignore")[:400])
        )
    data = np.frombuffer(proc.stdout, dtype=np.float32)
    if data.size == 0:
        raise RuntimeError("No audio decoded from: %s" % path)
    audio = data.reshape(-1, CHANNELS).copy()
    return audio


def save_audio_mp3(path, audio, sr=SR, bitrate="320k"):
    """Encode a float32 stereo array to an MP3 file with ffmpeg."""
    exe = _ffmpeg_bin()
    audio = np.ascontiguousarray(audio.astype(np.float32))
    cmd = [
        exe, "-v", "error", "-y",
        "-f", "f32le",
        "-ar", str(sr),
        "-ac", str(CHANNELS),
        "-i", "-",
        "-codec:a", "libmp3lame",
        "-b:a", bitrate,
        path,
    ]
    proc = subprocess.run(cmd, input=audio.tobytes(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          **_no_window_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg could not write MP3:\n%s"
            % proc.stderr.decode("utf-8", "ignore")[:400]
        )


# --------------------------------------------------------------------------- #
#  Analysis
# --------------------------------------------------------------------------- #
def _to_mono(audio):
    return audio.mean(axis=1)


def find_music_start(mono, sr=SR, min_sustain=1.0):
    """
    Find the time (seconds) where the music really begins.

    Long/ambient dance intros can stay quiet or sparse for a minute or two.
    We look at the short-term energy (RMS) and return the first moment the
    energy climbs above a threshold and *stays* there for `min_sustain`
    seconds, so a single stray sound early on won't fool it.
    """
    rms = librosa.feature.rms(y=mono, frame_length=2048, hop_length=FRAME_HOP)[0]
    if rms.size == 0:
        return 0.0
    times = librosa.frames_to_time(np.arange(rms.size), sr=sr, hop_length=FRAME_HOP)

    peak = float(np.max(rms))
    if peak <= 0:
        return 0.0
    # Threshold relative to the loud part of the track.
    loud = float(np.percentile(rms, 90))
    thresh = max(loud * 0.30, peak * 0.12)

    frames_per_sec = sr / FRAME_HOP
    need = int(min_sustain * frames_per_sec)
    above = rms > thresh

    run = 0
    for i, flag in enumerate(above):
        if flag:
            run += 1
            if run >= need:
                start_idx = i - run + 1
                return float(max(0.0, times[start_idx]))
        else:
            run = 0
    return 0.0  # never clearly kicks in -> start at 0


def analyze_track(path, skip_long_intros=True, progress=None):
    """
    Return a dict describing a track:
        path, duration, tempo, beats (np.array of beat times),
        music_start (seconds).
    """
    if progress:
        progress("Loading %s" % os.path.basename(path))
    audio = load_audio(path)
    dur = audio.shape[0] / SR
    mono = _to_mono(audio)

    if progress:
        progress("Detecting beats: %s" % os.path.basename(path))
    tempo, beat_frames = librosa.beat.beat_track(y=mono, sr=SR, hop_length=FRAME_HOP)
    beats = librosa.frames_to_time(beat_frames, sr=SR, hop_length=FRAME_HOP)
    tempo = float(np.atleast_1d(tempo)[0])

    music_start = find_music_start(mono, SR) if skip_long_intros else 0.0

    if progress:
        progress("Detecting key: %s" % os.path.basename(path))
    pc, kmode = estimate_key(mono, SR)

    return {
        "path": path,
        "name": os.path.basename(path),
        "duration": float(dur),
        "tempo": round(tempo, 1),
        "beats": beats.astype(float),
        "music_start": float(music_start),
        "key_pc": pc,
        "key_mode": kmode,
        "camelot": camelot_str(pc, kmode),
        "key_name": key_name(pc, kmode),
    }


# --------------------------------------------------------------------------- #
#  Musical key + harmonic (Camelot) mixing
# --------------------------------------------------------------------------- #
_PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Camelot number for each pitch class.
_CAMELOT_MAJ = {0: 8, 1: 3, 2: 10, 3: 5, 4: 12, 5: 7, 6: 2, 7: 9, 8: 4, 9: 11, 10: 6, 11: 1}
_CAMELOT_MIN = {0: 5, 1: 12, 2: 7, 3: 2, 4: 9, 5: 4, 6: 11, 7: 6, 8: 1, 9: 8, 10: 3, 11: 10}

# Krumhansl-Schmuckler key profiles.
_MAJ_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MIN_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def estimate_key(mono, sr=SR):
    """Estimate (pitch_class 0-11, 'maj'|'min') from a mono signal."""
    try:
        chroma = librosa.feature.chroma_cqt(y=mono, sr=sr)
        vec = chroma.mean(axis=1)
        if not np.any(vec):
            return 0, "maj"
        best = None
        for i in range(12):
            for mode, prof in (("maj", _MAJ_PROFILE), ("min", _MIN_PROFILE)):
                score = float(np.corrcoef(np.roll(prof, i), vec)[0, 1])
                if best is None or score > best[2]:
                    best = (i, mode, score)
        return best[0], best[1]
    except Exception:
        return 0, "maj"


def camelot(pc, mode):
    """Return (number 1-12, letter 'A'/'B') on the Camelot wheel."""
    if mode == "min":
        return _CAMELOT_MIN[pc % 12], "A"
    return _CAMELOT_MAJ[pc % 12], "B"


def camelot_str(pc, mode):
    n, l = camelot(pc, mode)
    return "%d%s" % (n, l)


def key_name(pc, mode):
    return "%s%s" % (_PITCHES[pc % 12], "m" if mode == "min" else "")


def parse_camelot(text):
    """Parse a Camelot code like '8A' / '12b' -> (pitch_class, 'maj'|'min')."""
    t = str(text).strip().upper()
    if len(t) < 2 or t[-1] not in ("A", "B"):
        return None
    try:
        num = int(t[:-1])
    except ValueError:
        return None
    letter = t[-1]
    table = _CAMELOT_MIN if letter == "A" else _CAMELOT_MAJ
    for pc, n in table.items():
        if n == num:
            return pc, ("min" if letter == "A" else "maj")
    return None


def camelot_distance(a, b):
    """
    Harmonic distance between two Camelot codes. 0 = same key; 1 = a perfect
    DJ-compatible move (±1 on the wheel, or relative major/minor); larger = more
    dissonant.
    """
    (na, la), (nb, lb) = a, b
    ring = min((na - nb) % 12, (nb - na) % 12)
    if la == lb:
        return ring
    if na == nb:
        return 1                       # relative major/minor
    return ring + 2


def _tempo_gap(bpm_a, bpm_b):
    """BPM difference after folding one tempo to the other's octave (half/double)."""
    if bpm_a <= 0 or bpm_b <= 0:
        return 0.0
    b = bpm_b
    while b < bpm_a / 1.4142:
        b *= 2
    while b > bpm_a * 1.4142:
        b /= 2
    return abs(bpm_a - b)


def mixability(a, b):
    """Higher = the two tracks blend better (close tempo + compatible key)."""
    tempo_pen = _tempo_gap(a.get("tempo", 0) or 0, b.get("tempo", 0) or 0)
    key_pen = 0.0
    if a.get("camelot") and b.get("camelot"):
        ca = camelot(a["key_pc"], a["key_mode"])
        cb = camelot(b["key_pc"], b["key_mode"])
        key_pen = camelot_distance(ca, cb)
    return -(tempo_pen + 2.5 * key_pen)


def auto_order(tracks, start=None):
    """
    Reorder tracks so each flows into the most mixable next one, greedily
    chaining by mixability (tempo proximity + Camelot key compatibility).

    If `start` is given (a track to lock as the opener) the chain is built from
    it; otherwise it starts from the lowest-BPM track so energy tends to build.
    """
    items = list(tracks)
    if len(items) < 3:
        return items
    if start is not None and start in items:
        first = start
    else:
        first = min(items, key=lambda t: (t.get("tempo", 0) or 1e9))
    order = [first]
    remaining = [t for t in items if t is not first]
    while remaining:
        last = order[-1]
        nxt = max(remaining, key=lambda t: mixability(last, t))
        order.append(nxt)
        remaining.remove(nxt)
    return order


# --------------------------------------------------------------------------- #
#  Analysis cache (so re-adding a file doesn't re-analyze it)
# --------------------------------------------------------------------------- #
def _cache_path():
    return os.path.join(os.path.expanduser("~"), ".energy7_cache.json")


def _load_cache():
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass


def _file_sig(path):
    try:
        st = os.stat(path)
        return "%d:%d" % (int(st.st_mtime), st.st_size)
    except Exception:
        return "0:0"


_ANALYSIS_CACHE = _load_cache()


def analyze_cached(path, skip_long_intros=True, progress=None):
    """
    analyze_track() with a persistent cache. The music-start value is stored
    once (detected) and applied per-call, so toggling 'skip long intros' never
    forces a re-analysis.
    """
    sig = _file_sig(path)
    entry = _ANALYSIS_CACHE.get(path)
    if not entry or entry.get("sig") != sig:
        info = analyze_track(path, skip_long_intros=True, progress=progress)
        entry = {
            "sig": sig,
            "duration": info["duration"],
            "tempo": info["tempo"],
            "music_start": info["music_start"],
            "beats": list(map(float, info["beats"])),
            "key_pc": info.get("key_pc"),
            "key_mode": info.get("key_mode"),
            "camelot": info.get("camelot"),
            "key_name": info.get("key_name"),
        }
        _ANALYSIS_CACHE[path] = entry
        _save_cache(_ANALYSIS_CACHE)
    elif progress:
        progress("Cached: %s" % os.path.basename(path))

    return {
        "path": path,
        "name": os.path.basename(path),
        "duration": entry["duration"],
        "tempo": entry["tempo"],
        "beats": np.asarray(entry["beats"], dtype=float),
        "music_start": entry["music_start"] if skip_long_intros else 0.0,
        "key_pc": entry.get("key_pc"),
        "key_mode": entry.get("key_mode"),
        "camelot": entry.get("camelot"),
        "key_name": entry.get("key_name"),
    }


def _snap_to_beat(t, beats, floor=True):
    """Snap a time to the nearest beat (or the nearest beat at or below it)."""
    if beats is None or len(beats) == 0:
        return t
    beats = np.asarray(beats)
    if floor:
        earlier = beats[beats <= t]
        if earlier.size:
            return float(earlier[-1])
        return float(beats[0])
    idx = int(np.argmin(np.abs(beats - t)))
    return float(beats[idx])


# --------------------------------------------------------------------------- #
#  Loudness normalization
# --------------------------------------------------------------------------- #
def normalize_lufs(audio, target_lufs=-14.0):
    """Normalize a stereo segment to a target integrated loudness (LUFS)."""
    if pyln is None:
        # Fallback: simple peak normalize to about -1 dBFS.
        peak = float(np.max(np.abs(audio))) or 1.0
        return audio * (10 ** (-1.0 / 20.0) / peak)
    try:
        meter = pyln.Meter(SR)
        loudness = meter.integrated_loudness(audio)
        if not np.isfinite(loudness):
            return audio
        return pyln.normalize.loudness(audio, loudness, target_lufs)
    except Exception:
        return audio


def _limit(audio, ceiling_db=-0.5):
    """Prevent clipping: scale down if the peak exceeds the ceiling."""
    ceiling = 10 ** (ceiling_db / 20.0)
    peak = float(np.max(np.abs(audio)))
    if peak > ceiling and peak > 0:
        audio = audio * (ceiling / peak)
    return audio


# --------------------------------------------------------------------------- #
#  Mixing
# --------------------------------------------------------------------------- #
def _equal_power_fades(n):
    """Return (fade_out, fade_in) equal-power curves of length n, shape (n,1)."""
    t = np.linspace(0.0, 1.0, n, endpoint=True)
    fade_in = np.sin(t * (np.pi / 2.0))
    fade_out = np.cos(t * (np.pi / 2.0))
    return fade_out.reshape(-1, 1), fade_in.reshape(-1, 1)


def _split_low(x, fc=200.0, order=4):
    """Return the low-frequency band of a stereo clip (below ~fc Hz)."""
    if butter is None or filtfilt is None or len(x) < 32:
        return None
    b, a = butter(order, fc / (SR / 2.0), btype="low")
    return filtfilt(b, a, x, axis=0).astype(np.float32)


def eq_bass_swap_blend(a_tail, b_head, out_period):
    """
    Club-style transition: the mids/highs of the two tracks crossfade smoothly,
    but the BASS is swapped on a beat - the outgoing low end drops out exactly as
    the incoming kick/bassline arrives, so the two basslines never clash.
    Falls back to a plain equal-power crossfade if SciPy isn't available.
    """
    xf = len(a_tail)
    fade_out, fade_in = _equal_power_fades(xf)

    low_a = _split_low(a_tail)
    low_b = _split_low(b_head)
    if low_a is None or low_b is None:
        return a_tail * fade_out + b_head * fade_in

    high_a = a_tail - low_a
    high_b = b_head - low_b

    # Swap point: a beat near the middle of the crossfade.
    swap = int(round((xf / 2.0) / out_period) * out_period)
    swap = min(max(out_period, swap), xf - 1)
    ramp = max(1, min(int(out_period), xf // 6))     # short low-band crossover
    start = max(0, swap - ramp // 2)
    end = min(xf, start + ramp)

    env_out = np.ones(xf, dtype=np.float32)
    env_in = np.zeros(xf, dtype=np.float32)
    if end > start:
        s = np.linspace(0.0, 1.0, end - start, dtype=np.float32)
        env_out[start:end] = np.cos(s * (np.pi / 2.0))
        env_in[start:end] = np.sin(s * (np.pi / 2.0))
    env_out[end:] = 0.0
    env_in[end:] = 1.0

    low = low_a * env_out[:, None] + low_b * env_in[:, None]
    high = high_a * fade_out + high_b * fade_in
    return (low + high).astype(np.float32)


def apply_transition_fx(a_tail, out_period):
    """
    DJ transition effect for the OUTGOING track as it fades: a rising high-pass
    filter sweep (the low end thins out) plus a beat-timed decaying echo, so the
    old track dissolves into an airy, echoing tail instead of just fading.
    """
    xf = len(a_tail)
    out = a_tail.astype(np.float32).copy()

    low = _split_low(a_tail, fc=1200.0)
    if low is not None:                       # sweep out more bass over time
        k = np.linspace(0.0, 0.9, xf, dtype=np.float32)[:, None]
        out = (a_tail - low * k).astype(np.float32)

    echoed = out.copy()                       # decaying echo taps, one beat apart
    d = max(1, int(out_period))
    g = 0.5
    for tap in range(1, 4):
        delay = d * tap
        if delay < xf:
            echoed[delay:] += out[:xf - delay] * (g ** tap)
    return echoed.astype(np.float32)


def octave_rate(tempo, target):
    """
    Ratio to stretch `tempo` toward `target`, kept within one octave so a
    64-BPM track locks to a 128-BPM master by playing double-time (rate 2.0)
    instead of an ugly ~2x slowdown. Returns the speed factor to apply.
    """
    if tempo <= 0 or target <= 0:
        return 1.0
    ratio = target / tempo
    # Fold by factors of two (half / double time) into [1/sqrt2, sqrt2] so we
    # always apply the *smallest* stretch that still beat-locks to the master.
    hi, lo = 1.41421356, 0.70710678
    for _ in range(6):
        if ratio > hi:
            ratio /= 2.0
        elif ratio < lo:
            ratio *= 2.0
        else:
            break
    return ratio


def time_stretch_stereo(audio, rate):
    """
    Speed a stereo clip up/down by `rate` while preserving pitch
    (phase-vocoder time stretch). rate > 1 = faster/shorter.
    """
    if rate == 1.0 or abs(rate - 1.0) < 1e-3 or librosa is None:
        return audio
    chans = []
    for ch in range(audio.shape[1]):
        y = np.ascontiguousarray(audio[:, ch].astype(np.float32))
        try:
            ys = librosa.effects.time_stretch(y, rate=rate)
        except TypeError:                      # older librosa: positional arg
            ys = librosa.effects.time_stretch(y, rate)
        chans.append(ys)
    m = min(len(c) for c in chans)
    return np.stack([c[:m] for c in chans], axis=1).astype(np.float32)


def _beat_period_samples(beats, tempo):
    """
    Samples per beat, taken from the *actual* detected beat spacing when we have
    it (more reliable than the reported BPM, which librosa often reports at half
    or double time), otherwise from the tempo.
    """
    b = np.asarray(beats, dtype=float) if beats is not None else np.array([])
    if b.size >= 2:
        diffs = np.diff(b)
        diffs = diffs[(diffs > 0.2) & (diffs < 2.0)]   # 30-300 BPM sanity window
        if diffs.size:
            return max(1, int(round(float(np.median(diffs)) * SR)))
    if tempo and tempo > 0:
        return max(1, int(round((60.0 / tempo) * SR)))
    return int((60.0 / 128.0) * SR)


def _grid_anchor_sample(beats, music_start, period_samp):
    """
    Sample index treated as the incoming 'downbeat' (where the groove enters):
    the detected beat closest to the music-start time. The beat grid is then
    anchor + k*period, so both tracks can be locked to a common phase.
    """
    b = np.asarray(beats, dtype=float) if beats is not None else np.array([])
    if b.size:
        anchor_t = float(b[int(np.argmin(np.abs(b - music_start)))])
    else:
        anchor_t = float(music_start)
    return int(max(0, round(anchor_t * SR)))


def _downbeat_before(n_samples, anchor, period_samp, tail_samp):
    """
    Pick a bar boundary (anchor + m*4*period) a little before the end of a
    track, so the outgoing mix-out lands cleanly on a downbeat.
    """
    bar = 4 * period_samp
    target = n_samples - tail_samp
    if target <= anchor + bar:
        return n_samples
    m = int((target - anchor) // bar)
    end = anchor + m * bar
    while end > n_samples and m > 0:
        m -= 1
        end = anchor + m * bar
    if end <= anchor + bar:
        return n_samples
    return int(end)


def _crossfade_beats(crossfade_sec, out_period_samp, in_period_samp, mode):
    """
    Crossfade length in beats, rounded to whole bars. In keep-tempo mode we
    shorten the fade when the two tempos differ so the beats don't drift far
    enough apart to clash.
    """
    out_sec = out_period_samp / SR
    beats = max(4, int(round(crossfade_sec / max(1e-6, out_sec))))
    bars = max(1, int(round(beats / 4.0)))
    nbeats = bars * 4
    if mode != "match" and in_period_samp > 0:
        drift_per_beat = abs(1.0 - out_period_samp / in_period_samp)
        while nbeats > 4 and nbeats * drift_per_beat > 0.4:
            bars -= 1
            nbeats = bars * 4
    return nbeats


def _prep_track(tr, mode, target, target_lufs, progress):
    """Load, (optionally) tempo-match, and describe a track on its beat grid."""
    audio = load_audio(tr["path"])
    tempo = float(tr.get("tempo", 0) or 0)
    beats = tr.get("beats")
    music_start = float(tr.get("music_start", 0.0))

    if mode == "match" and tempo > 0 and target > 0:
        rate = octave_rate(tempo, target)
        if abs(rate - 1.0) > 1e-3:
            if progress:
                progress("  tempo-match %s -> %.0f BPM (x%.2f)"
                         % (tr.get("name", ""), target, rate))
            audio = time_stretch_stereo(audio, rate)
            if beats is not None and len(beats):
                beats = np.asarray(beats) / rate
            music_start = music_start / rate
            tempo = tempo * rate

    period = _beat_period_samples(beats, tempo)
    anchor = _grid_anchor_sample(beats, music_start, period)
    anchor = min(anchor, max(0, audio.shape[0] - 1))
    return {"audio": audio, "period": period, "anchor": anchor,
            "name": tr.get("name", "")}


def build_mix(tracks, crossfade_sec=8.0, target_lufs=-14.0,
              tail_trim_sec=6.0, mode="align", master_bpm=0.0, fx=False,
              progress=None):
    """
    Build one continuous, beat-locked mix from analyzed `tracks`.

    The transition is built on the beat grid: the outgoing track mixes out on a
    bar boundary (downbeat), the incoming track enters on its own downbeat at
    that exact spot, and the crossfade spans a whole number of bars. That means
    the first beat of the new track lands squarely on a beat of the old one
    instead of somewhere in between.

    mode="align"  - keep each track's tempo; downbeats are phase-locked at the
                    start of every transition and the fade is shortened when
                    tempos differ so they don't drift apart audibly.
    mode="match"  - time-stretch every track to a shared master BPM so the beats
                    stay locked all the way through each crossfade.
    mode="eqswap" - like "align", but the crossfade swaps the bass on a beat
                    (outgoing low end out as the incoming kick lands) while the
                    mids/highs blend - the clean, club-style transition.
    """
    if not tracks:
        raise RuntimeError("No tracks to mix.")

    target = 0.0
    if mode == "match":
        target = master_bpm if master_bpm and master_bpm > 0 else float(
            tracks[0].get("tempo", 0) or 0)
        if target <= 0:
            mode = "align"

    tail = int(max(0.0, tail_trim_sec) * SR)
    result = None
    out_period = None          # outgoing track's beat period (samples)
    cues = []                  # output sample offset where each track enters

    n_tracks = len(tracks)
    for i, tr in enumerate(tracks):
        if progress:
            progress("Mixing %d/%d: %s" % (i + 1, n_tracks, tr["name"]))
        info = _prep_track(tr, mode, target, target_lufs, progress)
        audio, period, anchor = info["audio"], info["period"], info["anchor"]

        seg = normalize_lufs(audio[anchor:], target_lufs)   # start on the downbeat
        last = (i == n_tracks - 1)

        # Where this track mixes out (bar boundary near the end), in seg coords.
        if last:
            seg_end = len(seg)
        else:
            seg_end = _downbeat_before(len(seg), 0, period, tail)
            seg_end = min(seg_end, len(seg))

        if result is None:
            result = seg[:seg_end].copy()
            cues.append(0)
            out_period = period
            continue

        # Crossfade length: whole bars of the OUTGOING track -> its end (a
        # downbeat) minus the fade is also a downbeat, and the incoming downbeat
        # (seg[0]) lines up with it exactly.
        nbeats = _crossfade_beats(crossfade_sec, out_period, period, mode)
        xf = nbeats * out_period
        xf = min(xf, len(result) - 1, len(seg) - 1)
        if xf < 1:
            cues.append(len(result))
            result = np.concatenate([result, seg[:seg_end]], axis=0)
            out_period = period
            continue

        # Keep the incoming tail ending on a downbeat too (whole bars after xf).
        if not last:
            bar = 4 * period
            body = seg_end - xf
            body = max(0, (body // bar) * bar)
            seg_end = xf + body
            seg_end = min(seg_end, len(seg))

        a_tail = result[-xf:]
        if fx:
            a_tail = apply_transition_fx(a_tail, out_period)
        if mode == "eqswap":
            blended = eq_bass_swap_blend(a_tail, seg[:xf], out_period)
        else:
            fade_out, fade_in = _equal_power_fades(xf)
            blended = a_tail * fade_out + seg[:xf] * fade_in
        cues.append(max(0, len(result) - xf))
        result = np.concatenate([result[:-xf], blended, seg[xf:seg_end]], axis=0)
        out_period = period

    result = _limit(result, ceiling_db=-0.5)
    return result.astype(np.float32), cues


# --------------------------------------------------------------------------- #
#  Live playback
# --------------------------------------------------------------------------- #
class Player:
    """Simple play/stop/seek playback of a numpy stereo buffer via sounddevice."""

    def __init__(self):
        self.buffer = None
        self.pos = 0
        self.stream = None
        self._lock = threading.Lock()

    def available(self):
        return sd is not None

    def load(self, audio):
        self.stop()
        self.buffer = np.ascontiguousarray(audio.astype(np.float32))
        self.pos = 0

    def _callback(self, outdata, frames, time_info, status):
        with self._lock:
            if self.buffer is None or self.pos >= len(self.buffer):
                outdata[:] = 0
                raise sd.CallbackStop()
            end = min(self.pos + frames, len(self.buffer))
            chunk = self.buffer[self.pos:end]
            outdata[:len(chunk)] = chunk
            if len(chunk) < frames:
                outdata[len(chunk):] = 0
            self.pos = end

    def play(self, start_frac=None):
        """Start playback. start_frac=None resumes from the current position."""
        if self.buffer is None or sd is None:
            return
        if self.stream is not None:
            self.stop()
        with self._lock:
            if start_frac is not None:
                self.pos = int(np.clip(start_frac, 0.0, 1.0) * len(self.buffer))
            if self.pos >= len(self.buffer):
                self.pos = 0
        # A generous block size + high latency gives PortAudio plenty of headroom
        # so the GUI/visualizer thread can never starve the audio -> no glitches.
        self.stream = sd.OutputStream(
            samplerate=SR, channels=CHANNELS, dtype="float32",
            blocksize=4096, latency="high", callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def is_playing(self):
        return self.stream is not None and self.stream.active

    def progress(self):
        if self.buffer is None or len(self.buffer) == 0:
            return 0.0
        with self._lock:
            return self.pos / len(self.buffer)

    # --- seeking (works whether playing or paused) --------------------- #
    def seek_fraction(self, frac):
        if self.buffer is None:
            return
        with self._lock:
            self.pos = int(np.clip(frac, 0.0, 1.0) * (len(self.buffer) - 1))

    def seek_sample(self, sample):
        if self.buffer is None:
            return
        with self._lock:
            self.pos = int(np.clip(sample, 0, len(self.buffer) - 1))

    def seek_relative(self, seconds):
        if self.buffer is None:
            return
        with self._lock:
            self.pos = int(np.clip(self.pos + seconds * SR, 0, len(self.buffer) - 1))

    def current_sample(self):
        with self._lock:
            return self.pos

    def total_seconds(self):
        if self.buffer is None:
            return 0.0
        return len(self.buffer) / SR

    def current_seconds(self):
        if self.buffer is None:
            return 0.0
        with self._lock:
            return self.pos / SR


# --------------------------------------------------------------------------- #
#  Visualizer
# --------------------------------------------------------------------------- #
class Visualizer(tk.Toplevel):
    """
    A trippy, live kaleidoscope visualizer driven by the playing mix.

    Each frame it runs an FFT on a small window of the audio at the current
    play position and paints a rotating, mirror-symmetric mandala of spectrum
    petals, a pulsing waveform ring, beat-triggered shockwave rings, and a
    particle burst system - all with continuously cycling colour.
    """

    N_BANDS = 24        # spectrum bands mapped into one kaleidoscope wedge
    SYM = 6             # fold symmetry (mandala arms)
    WIN = 2048          # samples analyzed per frame
    MAX_PARTICLES = 170

    def __init__(self, master, player, get_mix):
        super().__init__(master)
        self.title("Energy 7 - Visuals   (Esc to close, click for a burst)")
        self.configure(bg="black")
        self.geometry("960x600")
        self.player = player
        self.get_mix = get_mix
        self.running = True

        self._smooth = np.zeros(self.N_BANDS)
        self._pulse = 0.0
        self._prev_energy = 0.0
        self._hue = 0.0
        self._angle = 0.0
        self._particles = []      # [x, y, vx, vy, life, hue, size]
        self._rings = []          # [radius, life, hue]

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        self.canvas.bind("<Button-1>", self._click_burst)
        self._animate()

    def _close(self):
        self.running = False
        self.destroy()

    def _click_burst(self, event):
        self._spawn_particles(event.x, event.y, 26, strength=1.4)
        self._rings.append([10.0, 1.0, self._hue])

    @staticmethod
    def _hsv(h, s, v):
        """HSV (0..1) -> #rrggbb hex string."""
        h = h % 1.0
        i = int(h * 6) % 6
        f = h * 6 - int(h * 6)
        p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
        r, g, b = [(v, t, p), (q, v, p), (p, v, t),
                   (p, q, v), (t, p, v), (v, p, q)][i]
        return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))

    def _spectrum(self):
        """Return (bands, waveform) at the current play position, or None."""
        mix = self.get_mix()
        if mix is None or len(mix) == 0:
            return None
        pos = self.player.current_sample() if hasattr(self.player, "current_sample") \
            else int(self.player.progress() * len(mix))
        if not self.player.is_playing() and pos <= 0:
            return None
        start = int(max(0, min(pos, len(mix) - self.WIN)))
        window = mix[start:start + self.WIN].mean(axis=1)
        if len(window) < self.WIN:
            window = np.pad(window, (0, self.WIN - len(window)))
        wav = window[::self.WIN // 96][:96].copy()      # small waveform for the ring
        win = window * np.hanning(len(window))
        spec = np.abs(np.fft.rfft(win))
        idx = np.logspace(0, np.log10(len(spec) - 1), self.N_BANDS + 1).astype(int)
        idx = np.clip(idx, 1, len(spec) - 1)
        bands = np.array([spec[idx[i]:idx[i + 1] + 1].mean()
                          for i in range(self.N_BANDS)])
        bands = np.log1p(bands)
        m = bands.max() or 1.0
        return bands / m, wav

    def _spawn_particles(self, x, y, n, strength=1.0):
        for _ in range(n):
            a = random.uniform(0, 2 * math.pi)
            spd = random.uniform(1.5, 6.0) * strength
            self._particles.append([
                x, y, math.cos(a) * spd, math.sin(a) * spd,
                1.0, (self._hue + random.uniform(0, 0.25)) % 1.0,
                random.uniform(2, 5),
            ])
        if len(self._particles) > self.MAX_PARTICLES:
            self._particles = self._particles[-self.MAX_PARTICLES:]

    def _animate(self):
        if not self.running:
            return
        try:
            self._draw()
        except Exception:
            pass
        self.after(33, self._animate)   # ~30 fps

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width() or 960
        h = c.winfo_height() or 600
        cx, cy = w / 2.0, h / 2.0

        got = self._spectrum()
        # Always keep the colour + rotation alive so it never looks frozen.
        self._hue = (self._hue + 0.004) % 1.0

        if got is None:
            self._angle += 0.01
            for k in range(self.SYM):
                a = self._angle + k * (2 * math.pi / self.SYM)
                x2 = cx + math.cos(a) * 80
                y2 = cy + math.sin(a) * 80
                c.create_line(cx, cy, x2, y2, fill=self._hsv(self._hue, 0.6, 0.4),
                              width=2)
            c.create_text(cx, cy + 130, fill="#666",
                          text="Press Play in the main window",
                          font=(UI_FAMILY, 15))
            return

        bands, wav = got
        self._smooth = 0.55 * self._smooth + 0.45 * bands
        energy = float(bands[: self.N_BANDS // 3].mean())     # bass energy
        beat = energy > self._prev_energy * 1.22 and energy > 0.22
        self._prev_energy = energy
        if beat:
            self._pulse = 1.0
            self._rings.append([18.0, 1.0, self._hue])
            self._spawn_particles(cx, cy, 14, strength=1.0 + energy)
        self._pulse *= 0.90

        # Rotation speeds up with the music.
        self._angle += 0.012 + energy * 0.07

        maxlen = min(w, h) * 0.44
        core_r = 16 + 34 * self._pulse
        wedge = 2 * math.pi / self.SYM

        # Beat glow wash over the whole field.
        if self._pulse > 0.04:
            c.create_rectangle(0, 0, w, h, outline="",
                               fill=self._hsv(self._hue + 0.5, 0.5,
                                              0.05 + 0.13 * self._pulse))

        # ---- Kaleidoscope spectrum petals (mirror-symmetric mandala) ------ #
        N = self.N_BANDS
        for i in range(N):
            v = float(self._smooth[i])
            r_out = core_r + v * maxlen
            hue = self._hue + i / N * 0.6
            col = self._hsv(hue, 0.9, min(1.0, 0.45 + v))
            width = 1 + int(3 * v) + (2 if self._pulse > 0.3 else 0)
            ai = (i / N) * wedge
            for k in range(self.SYM):
                base = self._angle + k * wedge
                for a in (base + ai, base + wedge - ai):    # mirror in wedge
                    ca, sa = math.cos(a), math.sin(a)
                    c.create_line(cx + ca * core_r, cy + sa * core_r,
                                  cx + ca * r_out, cy + sa * r_out,
                                  fill=col, width=width)
                    if v > 0.28:                            # glowing tip
                        tr = 2 + 4 * v
                        c.create_oval(cx + ca * r_out - tr, cy + sa * r_out - tr,
                                      cx + ca * r_out + tr, cy + sa * r_out + tr,
                                      fill=self._hsv(hue + 0.12, 0.7, 1.0),
                                      outline="")

        # ---- Waveform ring ------------------------------------------------- #
        ring_r0 = min(w, h) * 0.20
        coords = []
        for j, samp in enumerate(wav):
            a = (j / len(wav)) * 2 * math.pi + self._angle * 0.5
            rr = ring_r0 + float(samp) * min(w, h) * 0.10
            coords.extend([cx + math.cos(a) * rr, cy + math.sin(a) * rr])
        if len(coords) >= 4:
            coords.extend(coords[:2])       # close the loop
            c.create_line(*coords, fill=self._hsv(self._hue + 0.33, 0.85, 1.0),
                          width=2, smooth=True)

        # ---- Shockwave rings ---------------------------------------------- #
        for ring in self._rings:
            ring[0] += 7 + 10 * ring[1]
            ring[1] -= 0.02
            rr = ring[0]
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                          outline=self._hsv(ring[2], 0.7, max(0.0, ring[1])),
                          width=max(1, int(4 * ring[1])))
        self._rings = [r for r in self._rings if r[1] > 0 and r[0] < max(w, h)]

        # ---- Particles ----------------------------------------------------- #
        for p in self._particles:
            p[0] += p[2]; p[1] += p[3]
            p[2] *= 0.985; p[3] *= 0.985
            p[4] -= 0.018
            if p[4] <= 0:
                continue
            s = p[6] * p[4]
            c.create_oval(p[0] - s, p[1] - s, p[0] + s, p[1] + s,
                          fill=self._hsv(p[5], 0.8, max(0.0, p[4])), outline="")
        self._particles = [p for p in self._particles if p[4] > 0]

        # ---- Pulsing core -------------------------------------------------- #
        r = core_r * (1.0 + 0.6 * self._pulse)
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill=self._hsv(self._hue, 0.65, 0.95), outline="")
        r2 = r * 0.55
        c.create_oval(cx - r2, cy - r2, cx + r2, cy + r2,
                      fill=self._hsv(self._hue + 0.15, 0.4, 1.0), outline="")


# --------------------------------------------------------------------------- #
#  Playlist / project files
# --------------------------------------------------------------------------- #
def save_m3u(path, tracks):
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for tr in tracks:
            dur = int(tr.get("duration", 0))
            f.write("#EXTINF:%d,%s\n" % (dur, tr.get("name", "")))
            f.write("%s\n" % tr["path"])


def load_m3u(path):
    paths = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            paths.append(line)
    return paths


def _mmss(seconds):
    s = int(seconds)
    return "%d:%02d" % (s // 60, s % 60)


def _cue_time(seconds):
    """MM:SS:FF where FF = frames (75 per second), as CUE sheets require."""
    total = int(seconds * 75)
    ff = total % 75
    s = total // 75
    return "%02d:%02d:%02d" % (s // 60, s % 60, ff)


def write_tracklist(path, tracks, cues, sr=SR):
    """Write a human-readable, timestamped tracklist."""
    n = min(len(tracks), len(cues))
    with open(path, "w", encoding="utf-8") as f:
        f.write("Energy 7 mix - tracklist\n")
        f.write("=" * 32 + "\n")
        for i in range(n):
            t = cues[i] / sr
            f.write("%02d.  %6s   %s\n" % (i + 1, _mmss(t),
                                           tracks[i].get("name", "?")))


def write_cue_sheet(path, mix_filename, tracks, cues, sr=SR):
    """Write a .cue sheet so media players show track boundaries in the mix."""
    n = min(len(tracks), len(cues))
    with open(path, "w", encoding="utf-8") as f:
        f.write('PERFORMER "Energy 7"\n')
        f.write('TITLE "Energy 7 Mix"\n')
        f.write('FILE "%s" MP3\n' % mix_filename)
        for i in range(n):
            name = str(tracks[i].get("name", "Track %d" % (i + 1))).replace('"', "'")
            f.write("  TRACK %02d AUDIO\n" % (i + 1))
            f.write('    TITLE "%s"\n' % name)
            f.write("    INDEX 01 %s\n" % _cue_time(cues[i] / sr))


def save_project(path, tracks, settings):
    data = {
        "settings": settings,
        "tracks": [
            {
                "path": t["path"],
                "name": t.get("name"),
                "duration": t.get("duration"),
                "tempo": t.get("tempo"),
                "music_start": t.get("music_start"),
                "key_pc": t.get("key_pc"),
                "key_mode": t.get("key_mode"),
                "camelot": t.get("camelot"),
                "key_name": t.get("key_name"),
                "beats": list(map(float, t.get("beats", []))) if t.get("beats") is not None else [],
            }
            for t in tracks
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_project(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for t in data.get("tracks", []):
        t["beats"] = np.asarray(t.get("beats", []), dtype=float)
    return data


# --------------------------------------------------------------------------- #
#  GUI
# --------------------------------------------------------------------------- #
# Modern dark palette (neon accents matching the logo / visualizer).
BG = "#0e1018"
PANEL = "#171a26"
PANEL2 = "#1f2333"
BORDER = "#2a2f45"
FG = "#e8eaf2"
MUTED = "#9aa0b8"
ACCENT = "#00e5ff"       # cyan
ACCENT2 = "#ff2bd6"      # magenta
ONACCENT = "#06121a"     # text drawn on top of the accent colour


def _asset(name):
    """Locate a bundled asset next to the script/exe or in PyInstaller's temp."""
    for base in (_app_dir(), getattr(sys, "_MEIPASS", _app_dir())):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return p
    return None


_BaseTk = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk


class App(_BaseTk):
    def __init__(self):
        super().__init__()
        self.title("Energy 7  v%s  -  automatic DJ" % __version__)
        self.geometry("880x680")
        self.minsize(760, 600)
        self.configure(bg=BG)

        self.tracks = []          # list of analyzed track dicts (or path-only)
        self.mix = None           # rendered mix numpy buffer
        self.cues = []            # sample offsets where each track enters the mix
        self.locked_start_path = None   # track pinned as the mix opener
        self.player = Player()
        self.msg_queue = queue.Queue()
        self.worker = None
        self._icon_img = None

        self._setup_style()
        self._set_icon()
        self._build_ui()
        self._enable_dnd()
        self._pump_messages()
        self._check_environment()

    def _enable_dnd(self):
        if TkinterDnD is None:
            return
        try:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = str(event.data).split()
        exts = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")
        added = 0
        for p in paths:
            p = p.strip("{}")
            if os.path.isfile(p) and os.path.splitext(p)[1].lower() in exts:
                self.tracks.append({"path": p, "name": os.path.basename(p)})
                added += 1
        if added:
            self._refresh_tree()
            self._log("Added %d file(s) via drag-and-drop." % added)

    # ----- theming --------------------------------------------------------- #
    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        base_font = (UI_FAMILY, 10)
        style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                        bordercolor=BORDER, font=base_font)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Time.TLabel", background=BG, foreground=ACCENT,
                        font=(MONO_FAMILY, 11))
        style.configure("TLabelframe", background=BG, bordercolor=BORDER,
                        relief="solid")
        style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)

        style.configure("TButton", background=PANEL2, foreground=FG,
                        bordercolor=BORDER, focuscolor=BG, relief="flat",
                        padding=(10, 6))
        style.map("TButton",
                  background=[("pressed", BORDER), ("active", "#2b3150")],
                  foreground=[("disabled", MUTED)])

        style.configure("Accent.TButton", background=ACCENT, foreground=ONACCENT,
                        relief="flat", padding=(12, 6), font=(UI_FAMILY, 10, "bold"))
        style.map("Accent.TButton",
                  background=[("pressed", "#00b8cc"), ("active", "#33ecff")])

        style.configure("TCheckbutton", background=BG, foreground=FG,
                        focuscolor=BG)
        style.map("TCheckbutton", foreground=[("active", ACCENT)])

        style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                        foreground=FG, arrowcolor=ACCENT, bordercolor=BORDER,
                        selectbackground=PANEL2, selectforeground=FG, padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL2)],
                  foreground=[("readonly", FG)])
        style.configure("TSpinbox", fieldbackground=PANEL2, foreground=FG,
                        arrowcolor=ACCENT, bordercolor=BORDER, padding=4)

        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, bordercolor=BORDER, rowheight=26,
                        borderwidth=0)
        style.configure("Treeview.Heading", background=PANEL2, foreground=MUTED,
                        relief="flat", padding=6, font=(UI_FAMILY, 9, "bold"))
        style.map("Treeview.Heading", background=[("active", BORDER)])
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", ONACCENT)])

        style.configure("TProgressbar", troughcolor=PANEL, background=ACCENT,
                        bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("Seek.Horizontal.TProgressbar", troughcolor=PANEL,
                        background=ACCENT2, bordercolor=BORDER,
                        lightcolor=ACCENT2, darkcolor=ACCENT2, thickness=10)
        style.configure("TScrollbar", background=PANEL2, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED)

    def _set_icon(self):
        try:
            ico = _asset("energy7.ico")
            if ico and os.name == "nt":
                self.iconbitmap(ico)
            png = _asset("energy7.png")
            if png:
                self._icon_img = tk.PhotoImage(file=png)
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    # ----- header logo (drawn on a canvas) --------------------------------- #
    @staticmethod
    def _hsv_hex(h, s, v):
        i = int(h * 6) % 6
        f = h * 6 - int(h * 6)
        p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
        r, g, b = [(v, t, p), (q, v, p), (p, v, t),
                   (p, q, v), (t, p, v), (v, p, q)][i]
        return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))

    def _draw_header(self, _evt=None):
        c = self.header
        c.delete("all")
        w = c.winfo_width() or 880
        h = 74
        cx, cy = 40, h / 2

        # Neon burst mark.
        spokes = 22
        for i in range(spokes):
            a = (i / spokes) * 2 * math.pi
            ln = 16 + 8 * abs(math.sin(i * 1.7))
            col = self._hsv_hex((i / spokes) % 1.0, 0.85, 1.0)
            c.create_line(cx, cy, cx + math.cos(a) * ln, cy + math.sin(a) * ln,
                          fill=col, width=2)
        c.create_oval(cx - 13, cy - 13, cx + 13, cy + 13, fill=BG, outline=ACCENT,
                      width=2)
        bolt = [(0.10, -0.42), (-0.14, 0.05), (0.02, 0.05), (-0.08, 0.42),
                (0.16, -0.05), (0.0, -0.05), (0.08, -0.42)]
        pts = [(cx + dx * 34, cy + dy * 34) for dx, dy in bolt]
        c.create_polygon(pts, fill="#ffffff", outline=ACCENT, width=1)

        # Wordmark + subtitle.
        c.create_text(74, cy - 10, anchor="w", text="ENERGY 7",
                      fill=FG, font=(UI_FAMILY, 20, "bold"))
        c.create_text(76, cy + 16, anchor="w", text="AUTOMATIC  DJ",
                      fill=ACCENT, font=(UI_FAMILY, 9, "bold"))
        c.create_text(w - 12, cy - 9, anchor="e", text="github.com/mrnet15/Energy7",
                      tags=("repo",), fill=ACCENT,
                      font=(UI_FAMILY, 9, "underline"))
        c.create_text(w - 12, cy + 9, anchor="e", text="v" + __version__,
                      fill=MUTED, font=(UI_FAMILY, 9))

    # ----- UI construction ------------------------------------------------- #
    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # Header banner with the logo.
        self.header = tk.Canvas(self, height=74, bg=BG, highlightthickness=0)
        self.header.pack(fill="x", padx=10, pady=(8, 0))
        self.header.bind("<Configure>", self._draw_header)
        self.header.tag_bind("repo", "<Button-1>",
                             lambda e: webbrowser.open(REPO_URL))
        self.header.tag_bind("repo", "<Enter>",
                             lambda e: self.header.config(cursor="hand2"))
        self.header.tag_bind("repo", "<Leave>",
                             lambda e: self.header.config(cursor=""))

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Button(top, text="+ Add MP3s", style="Accent.TButton",
                   command=self.add_files).pack(side="left")
        ttk.Button(top, text="Remove", command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(top, text="Up", command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(top, text="Down", command=lambda: self.move(1)).pack(side="left", padx=4)
        ttk.Button(top, text="Clear", command=self.clear_all).pack(side="left")
        ttk.Button(top, text="✨ Auto-Order", style="Accent.TButton",
                   command=self.auto_order_tracks).pack(side="right")
        ttk.Button(top, text="★ Lock Start",
                   command=self.lock_start).pack(side="right", padx=4)

        # Track list
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, **pad)
        cols = ("name", "bpm", "key", "start", "len")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for c, w, txt in (("name", 360, "Track"), ("bpm", 70, "BPM"),
                          ("key", 60, "Key"), ("start", 110, "Music starts"),
                          ("len", 80, "Length")):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w" if c == "name" else "center")
        self.tree.tag_configure("odd", background=PANEL)
        self.tree.tag_configure("even", background=PANEL2)
        self.tree.bind("<Double-1>", self._on_tree_edit)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        # Settings
        opt = ttk.LabelFrame(self, text="  Mix settings  ")
        opt.pack(fill="x", **pad)

        ttk.Label(opt, text="Crossfade (sec):", style="Muted.TLabel").grid(
            row=0, column=0, sticky="e", padx=6, pady=6)
        self.xfade = tk.DoubleVar(value=8.0)
        ttk.Spinbox(opt, from_=1, to=30, increment=1, width=6,
                    textvariable=self.xfade).grid(row=0, column=1, sticky="w")

        ttk.Label(opt, text="Loudness (LUFS):", style="Muted.TLabel").grid(
            row=0, column=2, sticky="e", padx=6)
        self.lufs = tk.DoubleVar(value=-14.0)
        ttk.Spinbox(opt, from_=-24, to=-6, increment=1, width=6,
                    textvariable=self.lufs).grid(row=0, column=3, sticky="w")

        self.skip_intros = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Skip long intros (start on the beat drop)",
                        variable=self.skip_intros).grid(row=0, column=4, padx=12)

        ttk.Label(opt, text="Mix mode:", style="Muted.TLabel").grid(
            row=1, column=0, sticky="e", padx=6, pady=6)
        self.mode = tk.StringVar(value="Beat-aligned (keep tempo)")
        self.mode_box = ttk.Combobox(
            opt, width=28, state="readonly", textvariable=self.mode,
            values=["Beat-aligned (keep tempo)",
                    "Tempo-matched (beat-lock)",
                    "EQ bass-swap (clean blend)"])
        self.mode_box.grid(row=1, column=1, columnspan=2, sticky="w")

        ttk.Label(opt, text="Master BPM (0 = auto):", style="Muted.TLabel").grid(
            row=1, column=3, sticky="e", padx=6)
        self.master_bpm = tk.DoubleVar(value=0.0)
        ttk.Spinbox(opt, from_=0, to=220, increment=1, width=6,
                    textvariable=self.master_bpm).grid(row=1, column=4, sticky="w")

        self.fx_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Filter + echo FX on transitions",
                        variable=self.fx_enabled).grid(
            row=2, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 4))

        # Actions
        act = ttk.Frame(self)
        act.pack(fill="x", **pad)
        ttk.Button(act, text="Analyze", command=self.analyze).pack(side="left")
        ttk.Button(act, text="Build Mix", style="Accent.TButton",
                   command=self.build).pack(side="left", padx=4)
        ttk.Button(act, text="Visuals", command=self.open_visuals).pack(side="left")
        ttk.Button(act, text="Save MP3", command=self.save_mp3).pack(side="left", padx=4)
        ttk.Button(act, text="Save Tracklist", command=self.save_tracklist).pack(side="left")
        ttk.Button(act, text="Save Playlist", command=self.save_playlist).pack(side="left", padx=4)
        ttk.Button(act, text="Load Playlist", command=self.load_playlist).pack(side="left")

        # Transport (playback + scrubbing + track skip)
        trans = ttk.Frame(self)
        trans.pack(fill="x", **pad)
        ttk.Button(trans, text="⏮ Prev", width=9,
                   command=self.prev_track).pack(side="left")
        ttk.Button(trans, text="⏪ 10s", width=8,
                   command=lambda: self.skip(-10)).pack(side="left", padx=4)
        self.play_btn = ttk.Button(trans, text="▶ Play", width=9,
                                   style="Accent.TButton", command=self.toggle_play)
        self.play_btn.pack(side="left")
        ttk.Button(trans, text="■ Stop", width=8,
                   command=self.stop_play).pack(side="left", padx=4)
        ttk.Button(trans, text="10s ⏩", width=8,
                   command=lambda: self.skip(10)).pack(side="left")
        ttk.Button(trans, text="Next ⏭", width=9,
                   command=self.next_track).pack(side="left", padx=4)
        self.time_lbl = ttk.Label(trans, text="0:00 / 0:00", style="Time.TLabel")
        self.time_lbl.pack(side="right")

        # Play position (click or drag to scrub)
        self.play_pos = ttk.Progressbar(self, mode="determinate", maximum=1000,
                                        style="Seek.Horizontal.TProgressbar")
        self.play_pos.pack(fill="x", padx=10)
        self.play_pos.bind("<Button-1>", self._scrub)
        self.play_pos.bind("<B1-Motion>", self._scrub)
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=10, pady=(3, 4))

        # Log
        logf = ttk.LabelFrame(self, text="  Status  ")
        logf.pack(fill="both", expand=False, padx=10, pady=5)
        self.log = tk.Text(logf, height=6, wrap="word", state="disabled",
                           bg=PANEL, fg=MUTED, insertbackground=FG, relief="flat",
                           highlightthickness=0, font=(MONO_FAMILY, 9), padx=8, pady=6)
        self.log.pack(fill="both", expand=True)

        self._update_play_pos()

    # ----- helpers --------------------------------------------------------- #
    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _check_environment(self):
        problems = []
        if librosa is None:
            problems.append("librosa is not installed (pip install -r requirements.txt).")
        try:
            _ffmpeg_bin()
        except Exception:
            problems.append("ffmpeg not found. Put ffmpeg.exe in this folder, "
                            "or add it to your PATH.")
        if sd is None:
            problems.append("sounddevice is not installed - live playback disabled.")
        if pyln is None:
            problems.append("pyloudnorm not installed - using simple peak normalize.")
        for p in problems:
            self._log("[warning] " + p)
        if TkinterDnD is None:
            self._log("Tip: 'pip install tkinterdnd2' to drag files onto the window.")
        if not problems:
            self._log("Ready. Add MP3s (or drag them in) to get started.")

    def _busy(self, on):
        if on:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _refresh_tree(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for i, tr in enumerate(self.tracks):
            bpm = tr.get("tempo", "")
            ms = tr.get("music_start", None)
            start = ("%d:%02d" % (int(ms) // 60, int(ms) % 60)) if ms else "-"
            dur = tr.get("duration", None)
            length = ("%d:%02d" % (int(dur) // 60, int(dur) % 60)) if dur else "-"
            key = tr.get("camelot", "") or ""
            name = tr.get("name", os.path.basename(tr["path"]))
            if tr.get("path") and tr.get("path") == self.locked_start_path:
                name = "★ " + name
            tag = "even" if i % 2 else "odd"
            self.tree.insert("", "end", tags=(tag,),
                             values=(name, bpm, key, start, length))

    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.index(sel[0])

    # ----- track list actions --------------------------------------------- #
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Add MP3 files",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.m4a *.aac *.ogg"), ("All files", "*.*")],
        )
        for p in paths:
            self.tracks.append({"path": p, "name": os.path.basename(p)})
        self._refresh_tree()
        if paths:
            self._log("Added %d file(s)." % len(paths))

    def remove_selected(self):
        i = self._selected_index()
        if i is None:
            return
        removed = self.tracks.pop(i)
        if removed.get("path") == self.locked_start_path:
            self.locked_start_path = None
        self._refresh_tree()

    def move(self, delta):
        i = self._selected_index()
        if i is None:
            return
        j = i + delta
        if 0 <= j < len(self.tracks):
            self.tracks[i], self.tracks[j] = self.tracks[j], self.tracks[i]
            self._refresh_tree()
            kids = self.tree.get_children()
            if kids:
                self.tree.selection_set(kids[j])

    def clear_all(self):
        self.stop_play()
        self.tracks = []
        self.mix = None
        self.locked_start_path = None
        self._refresh_tree()
        self._log("Cleared.")

    def _on_tree_edit(self, event):
        """Double-click the BPM or Key cell to correct a track's detected value."""
        col = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if not row:
            return
        i = self.tree.index(row)
        if not (0 <= i < len(self.tracks)):
            return
        tr = self.tracks[i]
        if col == "#2":          # BPM
            cur = tr.get("tempo", "")
            val = simpledialog.askstring("Set BPM",
                                         "BPM for '%s':" % tr.get("name", ""),
                                         initialvalue=str(cur), parent=self)
            if val:
                try:
                    tr["tempo"] = round(float(val), 1)
                    self._cache_override(tr)
                    self._refresh_tree()
                except ValueError:
                    messagebox.showerror("Invalid", "Enter a number, e.g. 128")
        elif col == "#3":        # Key (Camelot)
            val = simpledialog.askstring(
                "Set Key",
                "Camelot key for '%s' (e.g. 8A, 12B):" % tr.get("name", ""),
                initialvalue=tr.get("camelot", ""), parent=self)
            if val:
                parsed = parse_camelot(val)
                if not parsed:
                    messagebox.showerror("Invalid", "Use a Camelot code like 8A or 12B.")
                    return
                pc, mode = parsed
                tr["key_pc"] = pc
                tr["key_mode"] = mode
                tr["camelot"] = camelot_str(pc, mode)
                tr["key_name"] = key_name(pc, mode)
                self._cache_override(tr)
                self._refresh_tree()

    @staticmethod
    def _cache_override(tr):
        """Persist a manual BPM/key edit so it survives re-adding the file."""
        path = tr.get("path")
        entry = _ANALYSIS_CACHE.get(path) if path else None
        if entry:
            for k in ("tempo", "key_pc", "key_mode", "camelot", "key_name"):
                if k in tr:
                    entry[k] = tr[k]
            _save_cache(_ANALYSIS_CACHE)

    def lock_start(self):
        """Pin the selected track as the opener that Auto-Order builds around."""
        i = self._selected_index()
        if i is None:
            messagebox.showinfo("Lock Start",
                                "Select a track in the list first, then Lock Start.")
            return
        path = self.tracks[i].get("path")
        if self.locked_start_path == path:
            self.locked_start_path = None
            self._log("Unlocked start track.")
        else:
            self.locked_start_path = path
            self._log("Locked start track: %s" % self.tracks[i].get("name", "?"))
        self._refresh_tree()

    def auto_order_tracks(self):
        """Analyze (tempo + key) then reorder tracks for the smoothest mix."""
        if len(self.tracks) < 3:
            messagebox.showinfo("Auto-Order", "Add at least 3 tracks first.")
            return
        if librosa is None:
            messagebox.showerror("Missing library",
                                 "librosa is required for Auto-Order.")
            return
        skip = self.skip_intros.get()

        def job():
            for idx, tr in enumerate(self.tracks):
                if "camelot" not in tr or "beats" not in tr:
                    self.tracks[idx] = analyze_cached(tr["path"], skip_long_intros=skip,
                                                     progress=self._progress)
                    self.msg_queue.put(("tracks", None))
            start = None
            if self.locked_start_path:
                start = next((t for t in self.tracks
                              if t.get("path") == self.locked_start_path), None)
            self.tracks = auto_order(self.tracks, start=start)
            self.msg_queue.put(("tracks", None))
            if start is not None:
                self.msg_queue.put(("log", "Auto-ordered (locked opener: %s):"
                                    % start.get("name", "?")))
            else:
                self.msg_queue.put(("log", "Auto-ordered for smoothest mixing:"))
            for i, t in enumerate(self.tracks, 1):
                self.msg_queue.put(("log", "  %2d. %-28s  [%s, %d BPM]" % (
                    i, str(t.get("name", "?"))[:28], t.get("camelot", "?"),
                    int(t.get("tempo", 0) or 0))))

        self._run_bg(job)

    # ----- background worker ---------------------------------------------- #
    def _run_bg(self, fn):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Please wait for the current job to finish.")
            return
        self._busy(True)

        def wrapper():
            try:
                fn()
            except Exception as e:
                self.msg_queue.put(("log", "[error] " + str(e)))
                self.msg_queue.put(("log", traceback.format_exc().splitlines()[-1]))
            finally:
                self.msg_queue.put(("busy", False))

        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def _progress(self, text):
        self.msg_queue.put(("log", text))

    def _pump_messages(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "busy":
                    self._busy(payload)
                elif kind == "tracks":
                    self._refresh_tree()
                elif kind == "mix_done":
                    self.mix, self.cues = payload
                    self.player.load(self.mix)      # ready to scrub before Play
                    self.play_pos["value"] = 0
                    self._refresh_time()
                    self._log("Mix ready: %.1f minutes, %d tracks."
                              % (len(self.mix) / SR / 60.0, len(self.cues)))
        except queue.Empty:
            pass
        self.after(120, self._pump_messages)

    # ----- analyze / build ------------------------------------------------- #
    def analyze(self):
        if not self.tracks:
            messagebox.showinfo("No tracks", "Add some MP3s first.")
            return
        if librosa is None:
            messagebox.showerror("Missing library",
                                 "librosa is not installed. See requirements.txt.")
            return
        skip = self.skip_intros.get()

        def job():
            for idx, tr in enumerate(self.tracks):
                info = analyze_cached(tr["path"], skip_long_intros=skip,
                                     progress=self._progress)
                self.tracks[idx] = info
                self.msg_queue.put(("tracks", None))
            self.msg_queue.put(("log", "Analysis complete."))

        self._run_bg(job)

    def build(self):
        if not self.tracks:
            messagebox.showinfo("No tracks", "Add some MP3s first.")
            return
        # Auto-analyze anything not yet analyzed.
        skip = self.skip_intros.get()
        xf = float(self.xfade.get())
        lufs = float(self.lufs.get())
        sel = self.mode.get()
        if sel.startswith("Tempo"):
            mode = "match"
        elif sel.startswith("EQ"):
            mode = "eqswap"
        else:
            mode = "align"
        mbpm = float(self.master_bpm.get())
        fx = self.fx_enabled.get()

        def job():
            for idx, tr in enumerate(self.tracks):
                if "beats" not in tr:
                    self.tracks[idx] = analyze_cached(tr["path"], skip_long_intros=skip,
                                                     progress=self._progress)
                    self.msg_queue.put(("tracks", None))
            mix = build_mix(self.tracks, crossfade_sec=xf, target_lufs=lufs,
                            mode=mode, master_bpm=mbpm, fx=fx, progress=self._progress)
            self.msg_queue.put(("mix_done", mix))

        self._run_bg(job)

    # ----- playback -------------------------------------------------------- #
    @staticmethod
    def _fmt(t):
        t = max(0, int(t))
        return "%d:%02d" % (t // 60, t % 60)

    def _refresh_time(self):
        cur = self.player.current_seconds()
        tot = self.player.total_seconds()
        self.time_lbl.configure(text="%s / %s" % (self._fmt(cur), self._fmt(tot)))
        self.play_pos["value"] = self.player.progress() * 1000

    def toggle_play(self):
        if self.mix is None:
            messagebox.showinfo("No mix", "Build the mix first.")
            return
        if not self.player.available():
            messagebox.showwarning("Playback unavailable",
                                   "sounddevice is not installed, so live play is off. "
                                   "You can still Save MP3.")
            return
        if self.player.buffer is None:
            self.player.load(self.mix)
        if self.player.is_playing():
            self.player.stop()
            self.play_btn.configure(text="Play")
        else:
            self.player.play(None)          # resume from current position
            self.play_btn.configure(text="Pause")

    def stop_play(self):
        self.player.stop()
        self.player.seek_sample(0)
        self.play_btn.configure(text="Play")
        self._refresh_time()

    def skip(self, seconds):
        if self.mix is None:
            return
        self.player.seek_relative(seconds)
        self._refresh_time()

    def next_track(self):
        if self.mix is None or not self.cues:
            return
        cur = self.player.current_sample()
        later = [c for c in self.cues if c > cur + int(0.4 * SR)]
        target = later[0] if later else len(self.mix) - 1
        self.player.seek_sample(target)
        self._refresh_time()

    def prev_track(self):
        if self.mix is None or not self.cues:
            return
        cur = self.player.current_sample()
        # If we're more than 2s into a track, jump to its start; else previous.
        earlier = [c for c in self.cues if c <= cur - int(2 * SR)]
        target = earlier[-1] if earlier else 0
        self.player.seek_sample(target)
        self._refresh_time()

    def _scrub(self, event):
        if self.mix is None:
            return
        w = max(1, self.play_pos.winfo_width())
        frac = min(1.0, max(0.0, event.x / w))
        self.player.seek_fraction(frac)
        self._refresh_time()

    def open_visuals(self):
        if self.mix is None:
            messagebox.showinfo("No mix", "Build the mix first, then press Play.")
            return
        Visualizer(self, self.player, lambda: self.mix)

    def _update_play_pos(self):
        if self.player.is_playing():
            self._refresh_time()
        elif self.play_btn["text"] == "Pause" and not self.player.is_playing():
            # Playback reached the end on its own.
            self.player.stop()
            self.play_btn.configure(text="Play")
        self.after(200, self._update_play_pos)

    # ----- saving ---------------------------------------------------------- #
    def save_mp3(self):
        if self.mix is None:
            messagebox.showinfo("No mix", "Build the mix first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save mix as MP3", defaultextension=".mp3",
            filetypes=[("MP3", "*.mp3")])
        if not path:
            return

        def job():
            self._progress("Encoding MP3 ...")
            save_audio_mp3(path, self.mix)
            self.msg_queue.put(("log", "Saved: %s" % path))

        self._run_bg(job)

    def save_tracklist(self):
        if self.mix is None or not self.cues:
            messagebox.showinfo("No mix", "Build the mix first, then save the tracklist.")
            return
        path = filedialog.asksaveasfilename(
            title="Save tracklist", defaultextension=".txt",
            filetypes=[("Text tracklist", "*.txt"), ("Cue sheet", "*.cue")])
        if not path:
            return
        base = os.path.splitext(path)[0]
        mix_name = os.path.basename(base) + ".mp3"
        try:
            write_tracklist(base + ".txt", self.tracks, self.cues)
            write_cue_sheet(base + ".cue", mix_name, self.tracks, self.cues)
            self._log("Saved tracklist: %s.txt and %s.cue" %
                      (os.path.basename(base), os.path.basename(base)))
            self._log("(The .cue points at '%s' — name your exported MP3 that.)"
                      % mix_name)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def save_playlist(self):
        if not self.tracks:
            messagebox.showinfo("No tracks", "Nothing to save.")
            return
        path = filedialog.asksaveasfilename(
            title="Save playlist", defaultextension=".m3u",
            filetypes=[("Playlist", "*.m3u"), ("Energy 7 project", "*.bmx")])
        if not path:
            return
        settings = {"crossfade": self.xfade.get(), "lufs": self.lufs.get(),
                    "skip_intros": self.skip_intros.get(),
                    "mode": self.mode.get(), "master_bpm": self.master_bpm.get(),
                    "fx": self.fx_enabled.get()}
        try:
            if path.lower().endswith(".bmx"):
                save_project(path, self.tracks, settings)
            else:
                save_m3u(path, self.tracks)
            self._log("Saved playlist: %s" % path)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def load_playlist(self):
        path = filedialog.askopenfilename(
            title="Load playlist",
            filetypes=[("Playlist / project", "*.m3u *.bmx"), ("All files", "*.*")])
        if not path:
            return
        try:
            if path.lower().endswith(".bmx"):
                data = load_project(path)
                self.tracks = data["tracks"]
                s = data.get("settings", {})
                self.xfade.set(s.get("crossfade", 8.0))
                self.lufs.set(s.get("lufs", -14.0))
                self.skip_intros.set(s.get("skip_intros", True))
                self.mode.set(s.get("mode", "Beat-aligned (keep tempo)"))
                self.master_bpm.set(s.get("master_bpm", 0.0))
                self.fx_enabled.set(s.get("fx", False))
            else:
                paths = load_m3u(path)
                self.tracks = [{"path": p, "name": os.path.basename(p)}
                               for p in paths if os.path.exists(p)]
            self._refresh_tree()
            self._log("Loaded %d track(s) from %s" % (len(self.tracks), os.path.basename(path)))
        except Exception as e:
            messagebox.showerror("Load failed", str(e))


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
