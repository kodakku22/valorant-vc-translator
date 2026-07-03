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
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("playback")

_BLOCK = 2048


def _load_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width == 2:
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, sr


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

    def play(self, path: str, speed: float = 1.0):
        self._cmd.put(("play", path, speed))

    def toggle_pause(self):
        self._cmd.put(("pause", None, None))

    def seek(self, ratio: float):
        self._cmd.put(("seek", ratio, None))

    def stop(self):
        self._cmd.put(("stop", None, None))

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
                cmd, a, b = self._cmd.get(timeout=None if block_mode else 0.001)
            except queue.Empty:
                cmd = None

            if cmd == "play":
                close_stream()
                try:
                    raw, sr = _load_wav(a)
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
                if self.on_done:
                    try:
                        self.on_done()
                    except Exception:
                        pass
                continue
            try:
                stream.write(chunk)
            except Exception:
                log.exception("output stream write failed")
                close_stream()
                audio = None
                continue
            pos += len(chunk)
            if self.on_progress:
                try:
                    self.on_progress(pos / len(audio))
                except Exception:
                    pass
