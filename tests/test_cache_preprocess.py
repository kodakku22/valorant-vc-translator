"""P1 translation cache + P2 audio preprocessing."""
import numpy as np

from vc_translator.preprocess import preprocess, HIGHPASS_HZ
from vc_translator.translate import TranslationCache, Translator, _cache_key


# ---------------- P1 cache ----------------

def test_stock_phrase_hit():
    c = TranslationCache()
    assert c.get("Nice shot!") == "ナイスショット"
    assert c.get("NICE") == "ナイス"
    assert c.hits == 2


def test_lru_roundtrip_and_normalization():
    c = TranslationCache()
    c.put("Rotate B, now!", "Bへローテ、急いで")
    assert c.get("rotate b now") == "Bへローテ、急いで"


def test_long_utterances_not_cached():
    c = TranslationCache()
    long = "they are saving this round so play for picks and time"
    c.put(long, "訳")
    assert c.get(long) is None


def test_failed_translation_not_cached():
    c = TranslationCache()
    c.put("push him", "(翻訳失敗)")
    assert c.get("push him") is None


def test_lru_eviction():
    c = TranslationCache()
    for i in range(TranslationCache.LRU_SIZE + 10):
        c.put(f"call {i}", f"訳{i}")
    assert c.get("call 0") is None                       # evicted
    assert c.get(f"call {TranslationCache.LRU_SIZE+9}")  # newest kept


def test_translator_uses_cache_without_llm():
    tr = Translator.__new__(Translator)
    tr.terms = {}
    tr.cache = TranslationCache()
    tr.system_prompt = "sys"

    class Boom:  # any LLM call would raise
        def chat(self, *a, **k):
            raise AssertionError("LLM should not be called on cache hit")
    tr.client = Boom()
    assert tr.translate("nice shot") == "ナイスショット"


def test_cache_key():
    assert _cache_key("He's ONE!!") == "he's one"


# ---------------- P2 preprocess ----------------

def _sine(freq, seconds=1.0, sr=16000, amp=0.5):
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_highpass_removes_rumble_keeps_speech_band():
    sr = 16000
    rumble = _sine(40, amp=0.5)          # below the 80 Hz cutoff
    speech = _sine(440, amp=0.1)         # in-band, quiet
    out = preprocess(rumble + speech, sr)
    spec = np.abs(np.fft.rfft(out))
    freqs = np.fft.rfftfreq(out.size, 1 / sr)
    low = spec[freqs < HIGHPASS_HZ * 0.6].max()
    band = spec[(freqs > 400) & (freqs < 500)].max()
    assert band > low * 20               # rumble crushed, speech dominant


def test_normalize_boosts_quiet_audio():
    quiet = _sine(300, amp=0.02)
    out = preprocess(quiet)
    assert 0.85 <= np.abs(out).max() <= 0.91


def test_silence_not_amplified():
    silence = np.zeros(16000, dtype=np.float32)
    out = preprocess(silence)
    assert np.abs(out).max() < 1e-6


def test_empty_input_safe():
    assert preprocess(np.zeros(0, dtype=np.float32)).size == 0
