"""Pitch-preserving time stretch (P6) — a compact WSOLA in pure numpy.

Slowing a clip by resampling drops the pitch (chipmunk-in-reverse); WSOLA
stretches time while keeping pitch by overlap-adding windows picked at the
best-correlating offset. Quality is plenty for speech listening practice.
"""

from __future__ import annotations

import numpy as np

_WIN = 1024          # analysis window (64 ms @ 16 kHz)
_HOP_OUT = _WIN // 2  # synthesis hop (50% overlap)
_SEARCH = 256         # ± samples searched for the best waveform alignment


def time_stretch(x: np.ndarray, rate: float) -> np.ndarray:
    """Return `x` played at `rate` (0.5 = half speed) with pitch preserved.

    rate >= ~0.4 and <= ~2.0 are sensible for speech; rate == 1 is a no-op.
    """
    if x.size < _WIN * 2 or abs(rate - 1.0) < 1e-3:
        return x
    rate = float(np.clip(rate, 0.25, 4.0))
    hop_in = int(round(_HOP_OUT * rate))
    window = np.hanning(_WIN).astype(np.float32)

    n_frames = max(1, int((len(x) - _WIN - _SEARCH) // hop_in))
    out_len = n_frames * _HOP_OUT + _WIN
    out = np.zeros(out_len, dtype=np.float32)
    norm = np.zeros(out_len, dtype=np.float32)

    # first frame: copy as-is
    prev_tail = x[:_WIN].astype(np.float32)
    out[:_WIN] += prev_tail * window
    norm[:_WIN] += window

    for i in range(1, n_frames):
        target = i * hop_in
        # search around the nominal position for the offset whose window best
        # continues the previous synthesis tail (avoids phasey artifacts)
        lo = max(0, target - _SEARCH)
        hi = min(len(x) - _WIN, target + _SEARCH)
        ref = prev_tail[_HOP_OUT:_HOP_OUT + _SEARCH]  # expected continuation
        best, best_score = target, -np.inf
        for cand in range(lo, hi + 1, 32):            # coarse scan is enough
            seg = x[cand:cand + _SEARCH]
            score = float(np.dot(seg, ref))
            if score > best_score:
                best_score, best = score, cand
        frame = x[best:best + _WIN].astype(np.float32)
        pos = i * _HOP_OUT
        out[pos:pos + _WIN] += frame * window
        norm[pos:pos + _WIN] += window
        prev_tail = frame

    norm[norm < 1e-6] = 1.0
    return (out / norm).astype(np.float32)
