"""Post-processing of raw Whisper output for Valorant voice comms.

Conservative, targeted fixes only -- must never mangle a legitimate callout.
The big one: large-v3 often spells short function words out as separate
capital letters ("H E is one shot" for "he's one shot"). We rejoin ONLY
letter runs whose lowercased join is a known word, so single site letters
(A / B / C) and real acronyms are left untouched.
"""

from __future__ import annotations

import re

# Letter-runs Whisper tends to spell out -> the intended word. Kept to an
# explicit allowlist so we never collapse meaningful single letters.
_LETTER_WORDS = {
    "he", "we", "ok", "gg", "hp", "ez", "af", "id", "us", "im", "ur",
    "hes", "wes", "okay",
}

# "H E" / "H.E." / "H-E" style runs of 2-4 single letters.
_LETTER_RUN = re.compile(r"\b(?:[A-Za-z][ .\-]){1,3}[A-Za-z]\b")


def _rejoin_letters(text: str) -> str:
    def repl(m: re.Match) -> str:
        letters = re.sub(r"[ .\-]", "", m.group(0))
        joined = letters.lower()
        if joined in _LETTER_WORDS:
            # "H E's" handled by the apostrophe staying outside the run
            return "he's" if joined == "hes" else joined
        return m.group(0)  # leave real acronyms / site letters alone
    return _LETTER_RUN.sub(repl, text)


def normalize_callout(text: str) -> str:
    """Clean up a transcribed line. Idempotent; safe on already-clean text."""
    if not text:
        return text
    out = _rejoin_letters(text)
    # "H E's" -> the run matched "H E" -> "he", leaving "'s" -> "he's"
    out = re.sub(r"\bhe 's\b", "he's", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out).strip()
    # collapse stutter repeats beyond 3 ("push push push push" -> keep 3)
    out = re.sub(r"\b(\w+)(\s+\1\b){3,}", r"\1 \1 \1", out, flags=re.IGNORECASE)
    return out
