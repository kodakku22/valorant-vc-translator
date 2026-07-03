"""Audio sources: live capture from a (virtual) input device, or a WAV file for testing.

Both yield mono float32 blocks at the target sample rate (16 kHz for Whisper/VAD).
"""

from __future__ import annotations

import logging
import queue
import threading
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("audio")


def list_input_devices() -> str:
    import sounddevice as sd

    hostapis = sd.query_hostapis()
    lines = ["input devices:"]
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] <= 0:
            continue
        api = hostapis[dev["hostapi"]]["name"]
        lines.append(f"  [{i:3d}] {dev['name']}  ({api}, {int(dev['default_samplerate'])} Hz, "
                     f"{dev['max_input_channels']} ch)")
    return "\n".join(lines)


class AudioCapture:
    """Captures from an input device whose name contains `device_name` (e.g. "CABLE Output")."""

    def __init__(self, device_name: str, target_sr: int = 16000, block_ms: int = 32):
        import sounddevice as sd

        self._sd = sd
        self.target_sr = target_sr
        self.device_index, dev = self._find_device(device_name)
        self.native_sr = int(dev["default_samplerate"])
        self.channels = min(2, max(1, int(dev["max_input_channels"])))
        self.blocksize = max(1, int(self.native_sr * block_ms / 1000))
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=512)
        self._dropped = 0
        self._stream = None

        self._resampler = None
        if self.native_sr != target_sr:
            import soxr
            self._resampler = soxr.ResampleStream(self.native_sr, target_sr, 1, dtype="float32")

        api = sd.query_hostapis(dev["hostapi"])["name"]
        log.info("capture device: [%d] %s (%s, %d Hz -> %d Hz, %d ch)",
                 self.device_index, dev["name"], api, self.native_sr, target_sr, self.channels)

    def _find_device(self, name_substr: str):
        sd = self._sd
        devices = sd.query_devices()
        candidates = [(i, d) for i, d in enumerate(devices)
                      if d["max_input_channels"] > 0
                      and name_substr.lower() in d["name"].lower()]
        if not candidates:
            raise RuntimeError(
                f'input device matching "{name_substr}" not found. '
                "Is VB-Cable installed? Use --list-devices to see available devices.")
        hostapis = sd.query_hostapis()
        # Prefer WASAPI over MME/DirectSound (lower latency, full device names).
        candidates.sort(key=lambda item: 0 if "WASAPI" in hostapis[item[1]["hostapi"]]["name"] else 1)
        return candidates[0]

    def _callback(self, indata, frames, time_info, status):
        if status and status.input_overflow:
            self._dropped += 1
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
        try:
            self._q.put_nowait(mono.astype(np.float32, copy=True))
        except queue.Full:
            self._dropped += 1  # never block the audio thread

    def start(self):
        import ctypes
        import sys
        import time

        if sys.platform == "win32":
            # PortAudio's WASAPI backend needs a COM-initialized thread; without
            # this, starting the stream from a worker thread fails (PaError -9999).
            try:
                ctypes.windll.ole32.CoInitialize(None)
            except Exception:
                pass

        last_exc = None
        for attempt in range(3):  # device can be transiently busy right after boot/reconnect
            try:
                self._stream = self._sd.InputStream(
                    device=self.device_index,
                    samplerate=self.native_sr,
                    channels=self.channels,
                    dtype="float32",
                    blocksize=self.blocksize,
                    callback=self._callback,
                )
                self._stream.start()
                log.info("audio capture started")
                return
            except Exception as exc:
                last_exc = exc
                log.warning("stream start failed (attempt %d/3): %s", attempt + 1, exc)
                if self._stream is not None:
                    try:
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None
                time.sleep(1.5)
        raise last_exc

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._dropped:
            log.warning("audio blocks dropped/overflowed: %d", self._dropped)

    def blocks(self, stop_event: threading.Event):
        """Yield mono float32 blocks at target_sr until stop_event is set."""
        while not stop_event.is_set():
            try:
                block = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._resampler is not None:
                block = self._resampler.resample_chunk(block)
            if len(block):
                yield np.asarray(block, dtype=np.float32)


class FileCapture:
    """Feeds a WAV file through the pipeline (for testing without Valorant/VB-Cable)."""

    def __init__(self, path: str | Path, target_sr: int = 16000, block_ms: int = 32,
                 realtime: bool = False):
        self.path = Path(path)
        self.target_sr = target_sr
        self.block_samples = max(1, int(target_sr * block_ms / 1000))
        self.realtime = realtime
        self._audio = self._load()
        log.info("test file: %s (%.1f s)", self.path, len(self._audio) / target_sr)

    def _load(self) -> np.ndarray:
        with wave.open(str(self.path), "rb") as wf:
            sr = wf.getframerate()
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())
        if width == 2:
            data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif width == 4:
            data = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif width == 1:
            data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            raise RuntimeError(f"unsupported WAV sample width: {width} bytes")
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        if sr != self.target_sr:
            import soxr
            data = soxr.resample(data, sr, self.target_sr).astype(np.float32)
        return data

    def start(self):
        pass

    def stop(self):
        pass

    def blocks(self, stop_event: threading.Event):
        import time

        step = self.block_samples
        for pos in range(0, len(self._audio), step):
            if stop_event.is_set():
                return
            yield self._audio[pos:pos + step]
            if self.realtime:
                time.sleep(step / self.target_sr)
