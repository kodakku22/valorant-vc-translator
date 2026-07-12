"""Wave-2: translation style (P7), pitch-preserving stretch (P6), week stats (U7)."""
import numpy as np

from vc_translator.timestretch import time_stretch
from vc_translator.translate import Translator, _STYLES


# ---------------- P7 style ----------------
# Translator() construction does no network IO, so these are safe offline.

def test_style_in_system_prompt():
    tr = Translator("m", terms={"rotate": "ローテート"}, style="polite")
    assert "です・ます" in tr.system_prompt
    tr2 = Translator("m", terms={}, style="gamer")
    assert "口語" in tr2.system_prompt


def test_unknown_style_falls_back_to_casual():
    tr = Translator("m", terms={}, style="nonsense")
    assert tr.style == "casual"
    assert "常体" in tr.system_prompt


def test_all_styles_defined():
    assert set(_STYLES) == {"casual", "polite", "gamer"}


# ---------------- P6 time stretch ----------------

def _speech_like(seconds=2.0, sr=16000):
    t = np.arange(int(sr * seconds)) / sr
    x = (0.4 * np.sin(2 * np.pi * 180 * t) * (1 + 0.3 * np.sin(2 * np.pi * 3 * t)))
    return x.astype(np.float32)


def test_stretch_lengthens_at_half_speed():
    x = _speech_like()
    y = time_stretch(x, 0.5)
    assert 1.7 * len(x) < len(y) < 2.3 * len(x)     # ~2x longer


def test_stretch_preserves_pitch():
    sr = 16000
    x = _speech_like(sr=sr)
    y = time_stretch(x, 0.5)
    # dominant frequency must stay ~180 Hz (resampling would halve it to 90)
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    freqs = np.fft.rfftfreq(len(y), 1 / sr)
    peak = freqs[int(np.argmax(spec))]
    assert 150 < peak < 210, f"peak {peak:.1f} Hz"


def test_stretch_rate_one_noop():
    x = _speech_like()
    assert time_stretch(x, 1.0) is x


def test_stretch_short_input_safe():
    x = np.zeros(500, dtype=np.float32)
    assert time_stretch(x, 0.5) is x


# ---------------- U7 week stats ----------------

def test_week_stats(tmp_data):
    from vc_translator.history import HistoryStore
    h = HistoryStore(tmp_data)
    h.start_session("learning")
    u = h.add_utterance("watch the flank", np.zeros(16000, dtype=np.float32), 1.0)
    h.add_utterance("nice shot", np.zeros(16000, dtype=np.float32), 1.0)
    h.end_session()
    h.toggle_star(u)
    stats = h.week_stats()
    assert stats["sessions"] == 1 and stats["lines"] == 2 and stats["stars"] == 1
    assert stats["learned"] == 0                      # streak < 2 so far
    # two correct answers -> learned
    card = h.due_cards()[0]
    h.answer_card(card["card_id"], True)
    # force due again and answer once more
    with h._lock:
        h._conn.execute("UPDATE cards SET due_ts = '2000-01-01T00:00:00'")
        h._conn.commit()
    h.answer_card(card["card_id"], True)
    assert h.week_stats()["learned"] == 1
    h.close()
