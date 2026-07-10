"""Translator glossary-enforcement logic (B7), no server needed."""
from vc_translator.translate import Translator, _THINK_RE


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, system, user, **kw):
        self.calls += 1
        return self.reply


def _translator(terms, reply):
    tr = Translator.__new__(Translator)
    tr.terms = terms
    tr.system_prompt = "sys"
    tr.client = FakeClient(reply)
    return tr


def test_enforce_retries_when_term_missing():
    tr = _translator({"rotate": "ローテート"}, "Bにローテートして")
    out = tr._enforce_terms("rotate to B", "Bに移動して")   # initial missing term
    assert out == "Bにローテートして"
    assert tr.client.calls == 1


def test_enforce_no_retry_when_present():
    tr = _translator({"rotate": "ローテート"}, "SHOULD-NOT-RUN")
    out = tr._enforce_terms("rotate to B", "Bにローテートして")
    assert out == "Bにローテートして"
    assert tr.client.calls == 0


def test_enforce_keeps_original_if_retry_still_missing():
    tr = _translator({"rotate": "ローテート"}, "またダメな訳")   # retry also lacks term
    out = tr._enforce_terms("rotate to B", "Bに移動して")
    assert out == "Bに移動して"                                  # falls back to original


def test_term_not_in_source_ignored():
    tr = _translator({"spike": "スパイク"}, "SHOULD-NOT-RUN")
    out = tr._enforce_terms("rotate to B", "Bに移動")
    assert out == "Bに移動" and tr.client.calls == 0


def test_think_block_stripped():
    assert _THINK_RE.sub("", "<think>x</think>訳").strip() == "訳"
