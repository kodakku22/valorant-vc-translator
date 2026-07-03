"""Streaming speech segmentation with Silero VAD.

Feed 16 kHz mono float32 blocks; get back complete utterance segments.
Hysteresis state machine with pre-roll padding and a hard max-length cut so
subtitles keep flowing even when someone talks continuously.
"""

from __future__ import annotations

import logging
from collections import deque

import numpy as np

log = logging.getLogger("vad")

WINDOW = 512  # Silero VAD v5 requires 512-sample windows at 16 kHz
SAMPLE_RATE = 16000


class SpeechSegmenter:
    def __init__(self, threshold: float = 0.5, min_silence_ms: int = 400,
                 speech_pad_ms: int = 120, min_speech_ms: int = 250,
                 max_segment_s: float = 6.0):
        import torch
        from silero_vad import load_silero_vad

        self._torch = torch
        self._model = load_silero_vad()

        self.threshold = threshold
        self.neg_threshold = max(0.05, threshold - 0.15)
        self.min_silence_samples = int(SAMPLE_RATE * min_silence_ms / 1000)
        self.min_speech_samples = int(SAMPLE_RATE * min_speech_ms / 1000)
        self.max_segment_samples = int(SAMPLE_RATE * max_segment_s)
        pad_windows = max(1, int(SAMPLE_RATE * speech_pad_ms / 1000) // WINDOW)

        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll: deque[np.ndarray] = deque(maxlen=pad_windows)
        self._speaking = False
        self._speech: list[np.ndarray] = []
        self._speech_len = 0
        self._voiced_len = 0
        self._silence_run = 0

    def feed(self, block: np.ndarray) -> list[np.ndarray]:
        """Consume a block of audio; return zero or more finished segments."""
        self._pending = np.concatenate([self._pending, block]) if len(self._pending) else block
        segments: list[np.ndarray] = []
        while len(self._pending) >= WINDOW:
            window = self._pending[:WINDOW]
            self._pending = self._pending[WINDOW:]
            prob = self._model(self._torch.from_numpy(window), SAMPLE_RATE).item()
            seg = self._step(window, prob)
            if seg is not None:
                segments.append(seg)
        return segments

    def flush(self) -> list[np.ndarray]:
        """Finalize any in-progress speech (end of file / shutdown)."""
        if self._speaking and self._voiced_len >= self.min_speech_samples:
            seg = np.concatenate(self._speech)
            self._reset()
            return [seg]
        self._reset()
        return []

    def _step(self, window: np.ndarray, prob: float) -> np.ndarray | None:
        if not self._speaking:
            if prob >= self.threshold:
                self._speaking = True
                self._speech = list(self._preroll)
                self._speech.append(window)
                self._speech_len = sum(len(a) for a in self._speech)
                self._voiced_len = WINDOW
                self._silence_run = 0
                self._preroll.clear()
            else:
                self._preroll.append(window)
            return None

        self._speech.append(window)
        self._speech_len += WINDOW
        if prob >= self.threshold:
            self._voiced_len += WINDOW
            self._silence_run = 0
        elif prob < self.neg_threshold:
            self._silence_run += WINDOW
        # between neg_threshold and threshold: hysteresis - neither counts

        if self._silence_run >= self.min_silence_samples:
            seg = np.concatenate(self._speech)
            voiced = self._voiced_len
            self._reset()
            if voiced >= self.min_speech_samples:
                return seg
            log.debug("segment dropped (too short: %d voiced samples)", voiced)
            return None

        if self._speech_len >= self.max_segment_samples:
            # Hard cut: emit what we have, stay in speaking state.
            seg = np.concatenate(self._speech)
            self._speech = []
            self._speech_len = 0
            self._voiced_len = self.min_speech_samples  # continuation always counts as speech
            log.debug("segment force-cut at max length")
            return seg

        return None

    def _reset(self):
        self._speaking = False
        self._speech = []
        self._speech_len = 0
        self._voiced_len = 0
        self._silence_run = 0
        self._preroll.clear()
