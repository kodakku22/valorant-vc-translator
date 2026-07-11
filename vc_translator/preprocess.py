"""Light audio pre-processing before Whisper (P2).

Two conservative steps that measurably help messy VC audio:
  1. High-pass at ~80 Hz -- removes rumble/hum that wastes model attention.
  2. Peak normalization -- quiet teammates get boosted to a sane level.

Only the STT input is processed; the saved clip keeps the original audio.
"""

from __future__ import annotations

import numpy as np

HIGHPASS_HZ = 80.0
TARGET_PEAK = 0.9
MIN_PEAK = 1e-4  # below this it's silence; don't amplify noise


def preprocess(audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Return a cleaned copy of `audio` (float32 mono in [-1, 1])."""
    if audio.size == 0:
        return audio
    x = audio.astype(np.float32, copy=True)

    # -- high-pass via rFFT (segments are short, so this is cheap and exact) --
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, d=1.0 / sample_rate)
    # smooth ramp 0->1 between 0.5*fc and fc to avoid ringing at the edge
    fc = HIGHPASS_HZ
    ramp = np.clip((freqs - fc * 0.5) / (fc * 0.5), 0.0, 1.0)
    spec *= ramp
    x = np.fft.irfft(spec, n=x.size).astype(np.float32)

    # -- peak normalize --
    peak = float(np.abs(x).max())
    if peak > MIN_PEAK:
        x *= TARGET_PEAK / peak
    return np.clip(x, -1.0, 1.0)
