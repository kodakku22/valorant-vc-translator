from vc_translator.normalize import normalize_callout


def test_spelled_function_word_rejoined():
    assert normalize_callout("H E is one shot") == "he is one shot"


def test_site_letter_preserved():
    assert normalize_callout("they push A site") == "they push A site"


def test_ok_rejoined():
    assert normalize_callout("O K rotate now") == "ok rotate now"


def test_stutter_trimmed():
    assert normalize_callout("push push push push push") == "push push push"


def test_idempotent():
    once = normalize_callout("H E is low")
    assert normalize_callout(once) == once == "he is low"


def test_empty():
    assert normalize_callout("") == ""
