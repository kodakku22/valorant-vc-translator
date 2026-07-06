"""Clip playback with speed control, pause and seek.

A single dedicated worker thread owns the sounddevice OutputStream (WASAPI
needs a COM-initialized thread on Windows). Commands go through a queue; a
progress callback reports position so the UI can animate waveforms.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading

import numpy as np

log = logging.getLogger("playback")

_BLOCK = 2048


class ClipPlayer:
    """play(path, speed) / toggle_pause / seek(ratio) / stop. One clip at a time."""

    def __init__(self, on_progress=None, on_done=None):
        """Callbacks fire on the worker thread: on_progress(ratio), on_done()."""
        self.on_progress = on_progress
        self.on_done = on_done
        self._cmd: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="playback", daemon=True)
        self._thread.start()

    # -- public API (any thread) --

    def play(self, path: str, speed: float = 1.0, span: tuple | None = None):
        """span=(start_s, end_s) plays only that slice (D10 word click)."""
        self._cmd.put(("play", path, speed, span))

    def toggle_pause(self):
        self._cmd.put(("pause", None, None, None))

    def seek(self, ratio: float):
        self._cmd.put(("seek", ratio, None, None))

    def stop(self):
        self._cmd.put(("stop", None, None, None))

    def _fire_done(self):
        if self.on_done:
            try:
                self.on_done()
            except Exception:
                pass

    # -- worker --

    def _run(self):
        if sys.platform == "win32":
            try:
                ctypes.windll.ole32.CoInitialize(None)
            except Exception:
                pass
        import sounddevice as sd

        audio = None       # resampled buffer at output rate
        pos = 0
        paused = False
        stream = None
        out_sr = 48000

        def close_stream():
            nonlocal stream
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                stream = None

        while True:
            # wait for a command while idle/paused; poll fast while playing
            block_mode = audio is None or paused
            try:
                cmd, a, b, c = self._cmd.get(timeout=None if block_mode else 0.001)
            except queue.Empty:
                cmd = None

            if cmd == "play":
                close_stream()
                try:
                    from vc_translator.audio import read_wav_mono
                    raw, sr = read_wav_mono(a)
                    if c is not None:  # D10: play only [start_s, end_s] with small padding
                        start, end = c
                        i0 = max(0, int((start - 0.05) * sr))
                        i1 = min(len(raw), int((end + 0.08) * sr))
                        if i1 > i0:
                            raw = raw[i0:i1]
                    speed = float(b or 1.0)
                    import soxr
                    # resample to output rate; dividing by speed slows it down
                    audio = soxr.resample(raw, sr * speed, out_sr).astype(np.float32)
                    pos = 0
                    paused = False
                    stream = sd.OutputStream(samplerate=out_sr, channels=1, dtype="float32")
                    stream.start()
                except Exception:
                    log.exception("playback failed: %s", a)
                    audio = None
                    self._fire_done()  # let the UI reset its play button
                continue
            if cmd == "pause":
                paused = not paused
                continue
            if cmd == "seek" and audio is not None:
                pos = int(len(audio) * max(0.0, min(1.0, float(a))))
                continue
            if cmd == "stop":
                close_stream()
                audio = None
                continue

            if audio is None or paused or stream is None:
                continue
            chunk = audio[pos:pos + _BLOCK]
            if len(chunk) == 0:
                close_stream()
                audio = None
                self._fire_done()
                continue
            try:
                stream.write(chunk)
            except Exception:
                log.exception("output stream write failed")
                close_stream()
                audio = None
                self._fire_done()
                continue
            pos += len(chunk)
            if self.on_progress:
                try:
                    self.on_progress(pos / len(audio))
                except Exception:
                    pass
