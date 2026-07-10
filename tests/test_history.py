import numpy as np
import pytest

from vc_translator.history import HistoryStore


@pytest.fixture
def store(tmp_data):
    h = HistoryStore(tmp_data)
    yield h
    h.close()


def _clip():
    return np.random.randn(16000).astype(np.float32) * 0.1


def test_session_lifecycle(store):
    store.start_session("learning")
    u = store.add_utterance("rotate B", _clip(), 1.0)
    store.set_translation(u, "B回れ")
    store.end_session()
    sess = store.list_sessions()
    assert len(sess) == 1 and sess[0]["lines"] == 1


def test_empty_session_discarded(store):
    store.start_session("learning")
    store.end_session()
    assert store.list_sessions() == []


def test_star_creates_and_removes_card(store):
    store.start_session("learning")
    u = store.add_utterance("he's one shot", _clip(), 1.0)
    store.end_session()
    assert store.toggle_star(u) is True
    assert store.due_count() == 1
    assert store.toggle_star(u) is False
    assert store.due_count() == 0


def test_srs_answer_intervals(store):
    store.start_session("learning")
    u = store.add_utterance("save your ults", _clip(), 1.0)
    store.end_session()
    store.toggle_star(u)
    card = store.due_cards()[0]
    store.answer_card(card["card_id"], True)
    assert store.due_count() == 0            # pushed into the future


def test_add_card_nullable_utt(store):
    assert store.add_card("nice one", "ナイス", utt_id=None) is True
    assert store.due_count() == 1
    assert isinstance(store.due_cards(), list)   # join tolerates NULL utt_id


def test_missed_only_slow_or_repeat_review(store):
    store.start_session("learning")
    u1 = store.add_utterance("aaaa bbbb cccc", _clip(), 1.0)
    u2 = store.add_utterance("dddd eeee ffff", _clip(), 1.0)
    u3 = store.add_utterance("gggg hhhh iiii", _clip(), 1.0)
    store.end_session()
    sid = store.list_sessions()[0]["id"]
    store.log_play(u1, 0.75, "review")   # single normal -> NOT missed
    store.log_play(u2, 0.5, "review")    # slow -> missed
    store.log_play(u3, 0.75, "card")     # flashcard -> ignored
    store.log_play(u3, 0.75, "card")
    lines = {l["id"]: l for l in store.get_session(sid)}
    assert lines[u1]["missed"] is False
    assert lines[u2]["missed"] is True
    assert lines[u3]["missed"] is False


def test_low_confidence_and_refine(store):
    store.start_session("learning")
    u = store.add_utterance("jiggle peek mid", _clip(), 1.0, avg_logprob=-1.6)
    store.end_session()
    sid = store.list_sessions()[0]["id"]
    assert store.get_session(sid)[0]["low_conf"] is True
    store.refine_utterance(u, "jiggle peek mid don't wide swing",
                           words=[["jiggle", 0.0, 0.4]])
    line = store.get_session(sid)[0]
    assert line["low_conf"] is False            # refined clears it
    assert line["words"][0][0] == "jiggle"
    assert store.get_utterance(u)["refined"] is True


def test_search_returns_session_id(store):
    store.start_session("learning")
    store.add_utterance("rotate to B", _clip(), 1.0)
    store.end_session()
    sid = store.list_sessions()[0]["id"]
    res = store.search("rotate")
    assert res and res[0]["session_id"] == sid


def test_migration_idempotent(tmp_data):
    h1 = HistoryStore(tmp_data)
    h1.start_session("learning")
    h1.add_utterance("test line", _clip(), 1.0)
    h1.end_session()
    h1.close()
    h2 = HistoryStore(tmp_data)     # reopen -> migrations must not fail
    assert h2.list_sessions()[0]["lines"] == 1
    h2.close()
