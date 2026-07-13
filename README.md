# Energy 7 — a simple automatic DJ for Windows

*Made by mrnet15/claude.*

Energy 7 takes a folder of MP3s and turns them into one continuous, beat-aligned,
volume-matched mix. It understands long dance intros (the ones where the track
doesn't really "start" until a minute or two in) and mixes from where the music
actually kicks in. You can play the mix live, watch a reactive visualizer, and
save both the mixed MP3 and your playlist.

## What it does

- **Add & reorder MP3s** in a simple window.
- **Analyzes each track** — detects tempo (BPM), the beat grid, and where the
  music truly begins (skips long/ambient intros).
- **Two mix modes**:
  - *Beat-aligned (keep tempo)* — transitions start on a beat of both tracks;
    original tempo is kept, nothing sounds warped.
  - *Tempo-matched (beat-lock)* — every track is time-stretched (pitch kept) to
    a shared master BPM so the beats stay locked all the way through each
    crossfade, like a real DJ syncing decks. Half/double-time is handled
    automatically, so a 174-BPM track locks to a 128-BPM set with the smallest
    possible stretch instead of a jarring one.
- **Loudness normalization (LUFS)** — every track sits at the same volume, so
  the set never jumps loud or quiet between songs.
- **Modern dark interface** with a neon logo, and lossless, glitch-free
  playback (the mix plays straight from the rendered audio at full quality).
- **Live transport** — play / pause / stop, **scrub** by clicking the position
  bar, skip **±10 seconds**, and jump to the **next / previous track**.
- **Trippy Visuals** — a rotating kaleidoscope that reacts to the music, with
  beat shockwaves and particle bursts (click the window for an extra burst).
- **Save MP3** — render the whole set to a single 320 kbps MP3.
- **Playlists** — save/load `.m3u` playlists, or `.bmx` projects that also
  remember the analysis and your settings.

## Requirements

- **Windows** with **Python 3.9+** and **ffmpeg** on your PATH.
- Python packages in `requirements.txt` (installed automatically on first run).

Check ffmpeg works by opening Command Prompt and running: `ffmpeg -version`

## How to run

1. Put all the files in one folder.
2. Double-click **`run.bat`** (first launch installs the packages, then opens
   the app). After that it starts instantly.

Or from a terminal:

```
pip install -r requirements.txt
python energy7.py
```

## Make a standalone .exe (optional)

If you'd rather have a single double-clickable `Energy7.exe` (no Python needed
to launch it, and you can copy it to other PCs):

1. On your Windows machine, double-click **`build_exe.bat`**.
2. Wait a few minutes. When it finishes, your program is at **`dist\Energy7.exe`**.
3. Double-click that exe to run. You can move/copy that one file anywhere.

ffmpeg is bundled automatically. `build_exe.bat` now checks for `ffmpeg.exe`
in this folder and, if it's missing, downloads it for you (via `get_ffmpeg.bat`)
before baking it into the exe. The result is fully self-contained — it runs on
any Windows PC with nothing else installed.

Already built the exe and just need ffmpeg without rebuilding? Run
**`get_ffmpeg.bat`** — it downloads `ffmpeg.exe` and also drops a copy into the
`dist` folder next to your existing `Energy7.exe`, so it works immediately.

Notes:
- The exe is large (roughly 300–500 MB) because it packs in the audio/analysis
  libraries. That's normal for this kind of app.
- Building must be done on Windows (an exe can't be built from Mac/Linux).

## How to use it

1. Click **Add MP3s** and pick your tracks. Reorder with **Up / Down**.
2. (Optional) Click **Analyze** to see BPM and detected music-start times.
3. Adjust **Mix settings**:
   - *Crossfade (sec)* — how long each transition is (8s is a good start).
   - *Loudness (LUFS)* — target volume; -14 is the streaming standard.
   - *Skip long intros* — on = start each track where the beat drops.
   - *Mix mode* — "Beat-aligned (keep tempo)" or "Tempo-matched (beat-lock)".
   - *Master BPM* — only used in beat-lock mode. Leave at 0 to auto-use the
     first track's BPM, or type a number (e.g. 128) to force the whole set to
     that tempo.
4. Click **Build Mix**. When it says "Mix ready", press **Play**.
5. Use the transport row to **scrub** (click/drag the bar), **skip ±10s**, or
   jump **Prev / Next** track. The time shows as `elapsed / total`.
6. Click **Visuals** for the kaleidoscope (Esc closes it; click it for a burst).
7. **Save MP3** to export the set, **Save Playlist** to keep the track list.

## Notes & tips

- "Skip long intros" uses energy detection to find where a track really gets
  going, which is exactly the case you described — old long dance mixes that
  idle for a couple of minutes before the groove starts.
- *Beat-aligned* keeps tempos as-is but now locks transitions to the beat grid:
  the outgoing track mixes out on a bar line, the incoming track drops in on its
  own downbeat at that exact spot, and the fade spans whole bars. When two
  tracks are close in tempo they stay locked; when they differ a lot the fade is
  automatically shortened so the beats don't drift apart and clash. For tracks
  with very different BPMs, use *Tempo-matched* for a fully locked blend.
- *Tempo-matched* locks the beats through every transition, but big tempo jumps
  mean more time-stretching, which can add a slight "phasey" texture. For sets
  of similar-tempo tracks it sounds tight and seamless. Tip: pick a Master BPM
  close to the average of your tracks to minimise stretching.
- If a track is very short it's used whole (no trimming).
- Everything runs locally on your PC. Nothing is uploaded.

## Troubleshooting

- **"ffmpeg not found"** — just run **`get_ffmpeg.bat`**. It downloads
  `ffmpeg.exe` into this folder (and next to `Energy7.exe` if you've built it),
  which is all Energy 7 needs. Alternatively, copy an existing `ffmpeg.exe` into
  the same folder as the program, or add ffmpeg to your PATH.
- **No sound / "playback unavailable"** — `sounddevice` failed to install; you
  can still Save MP3 and play it in any player.
- **A track fails to load** — it may be DRM-protected or corrupt; try
  re-exporting it as a normal MP3.

## License

Energy 7 is released under the MIT License — see the `LICENSE` file. You're free
to use, modify, and share it; just keep the copyright notice.

**ffmpeg note:** ffmpeg is a separate program that Energy 7 calls; it is *not*
included in this repository. Run `get_ffmpeg.bat` to download it locally. This
keeps the repo cleanly MIT-licensed (ffmpeg has its own license). For the same
reason, the built `Energy7.exe` (which bundles ffmpeg) is not committed either.
