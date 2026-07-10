"""Hallucination filter + flashcard masking -- no model load required."""
from vc_translator.stt import Transcriber
from vc_translator.bridge import _mask_sentence, _norm_words


def test_junk_filter():
    junk = ["you, you, you, you", "Thank you for watching!", "", "uh", "yeah."]
    real = ["push push push", "he's one shot", "rotate B now"]
    assert all(Transcriber._is_junk(t) for t in junk)
    assert not any(Transcriber._is_junk(t) for t in real)


def test_mask_hides_target_words():
    m = _mask_sentence("jiggle peek mid, don't wide swing")
    assert "●" in m
    assert "jiggle" not in m       # a long content word is masked


def test_mask_keeps_short_words():
    m = _mask_sentence("go to B")
    # nothing >=4 chars except none -> falls back to masking the longest token
    assert isinstance(m, str) and m


def test_norm_words():
    assert _norm_words("He's ONE, shot!") == ["he's", "one", "shot"]
