# Acknowledgements

Energy 7 (MIT License — see `LICENSE`) is built with these open-source projects.
Thanks to their authors and communities.

| Project | Used for | License |
| --- | --- | --- |
| [FFmpeg](https://ffmpeg.org) | Decoding / encoding audio (called as a separate program) | LGPL/GPL |
| [librosa](https://librosa.org) | Tempo, beat, and onset analysis | ISC |
| [NumPy](https://numpy.org) | Numerical processing | BSD-3-Clause |
| [SciPy](https://scipy.org) | Signal processing helpers | BSD-3-Clause |
| [scikit-learn](https://scikit-learn.org) | Used internally by librosa | BSD-3-Clause |
| [numba](https://numba.pydata.org) / [llvmlite](https://llvmlite.readthedocs.io) | Speeds up analysis | BSD-2-Clause |
| [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) | LUFS loudness normalization | MIT |
| [sounddevice](https://python-sounddevice.readthedocs.io) | Live audio playback | MIT |
| [soundfile](https://github.com/bastibe/python-soundfile) | Audio file support | BSD-3-Clause |
| [PyInstaller](https://pyinstaller.org) | Building the standalone `.exe` | GPL + bootloader exception |
| Python / Tkinter | Language and GUI toolkit | PSF / BSD-style |

Each project is used under its own license.

> Note: FFmpeg is not included in this repository — it is downloaded locally by
> `get_ffmpeg.bat`. If you ever distribute a build that bundles `ffmpeg.exe`,
> include FFmpeg's license and a link to that build's source.
