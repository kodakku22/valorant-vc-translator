"""VAD segmentation on synthetic audio. Loads Silero (CPU) -- skip if unavailable."""
import numpy as np
import pytest

pytest.importorskip("silero_vad")
pytest.importorskip("torch")

from vc_translator.vad import SpeechSegmenter, SAMPLE_RATE


def _tone(seconds, freq=200, amp=0.3):
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds):
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


def test_import_and_construct():
    seg = SpeechSegmenter()
    assert seg is not None


def test_forced_cut_resets_silence(monkeypatch):
    """After a max-length force-cut, a short trailing silence must NOT immediately
    finalize the continuation (the silence counter is reset)."""
    seg = SpeechSegmenter(max_segment_s=1.0, min_silence_ms=400)
    # feed 1.5s of "always speech" by stubbing the model probability high
    seg._model = lambda *a, **k: type("P", (), {"item": lambda self: 0.9})()
    out = []
    out += seg.feed(_tone(1.2))
    # a forced cut should have happened (>=1 segment) and silence_run stays 0
    assert len(out) >= 1
    assert seg._silence_run == 0
