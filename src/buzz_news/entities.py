"""Heuristic NER for cluster event-anchor gating.

Pure regex — no spaCy, no LLM, no model load. Catches:
  - Capitalized name runs ("New Delhi", "Donald Trump", "BJP MLA")
  - All-caps acronyms of length >= 2 ("BJP", "H-1B", "WHO")
  - 4-digit years ("2026")

Devanagari headlines have no Latin-script proper nouns and return an empty
set — callers must treat empty entity sets as "fall through to cosine-only".
"""
import re

# 1-4 cap words optionally joined by space or hyphen ("H-1B" still matches via
# the acronym pattern below, but "New-Delhi" or "Donald Trump" works here too).
_NAME_RUN = r"\b[A-Z][a-z]{2,}(?:[\s\-][A-Z][a-z]+){0,3}"
# All-caps tokens >= 2 chars, optional digits/hyphens ("BJP", "H-1B", "COP-29").
_ACRONYM = r"\b[A-Z][A-Z0-9\-]{1,}\b"
# Four-digit years 1900-2099.
_YEAR = r"\b(?:19|20)\d{2}\b"

_NER_RE = re.compile(f"{_NAME_RUN}|{_ACRONYM}|{_YEAR}")

# Sentence starters and generic geo/topic tokens that show up across unrelated
# stories and would over-merge clusters if kept. Anything in here is NOT an
# event anchor. Tune by observation, not by speculation.
_STOPWORDS = frozenset({
    "The", "This", "That", "These", "Those", "When", "Where", "What", "Who",
    "Why", "How", "Here", "There", "Today", "Tomorrow", "Yesterday", "After",
    "Before", "During", "First", "Last", "Next", "New", "Old", "Live", "Update",
    "Updates", "Report", "Reports", "News", "Says", "Said", "Hindi", "English",
    "India", "World", "Live", "Year", "Day", "Week", "Month", "Now", "While",
    "However", "Also", "Just", "Even", "Some", "Many", "Most", "All", "All-",
    "BREAKING", "EXCLUSIVE", "LIVE", "VIDEO", "PHOTOS", "NEWS",
})


def extract_entities(text: str | None) -> set[str]:
    """Return the set of capitalized-name / acronym / year tokens in `text`.

    Caller must treat the empty set as "no entity signal" — do NOT use empty
    overlap as a reason to reject a cluster attach (Hindi-only horoscope
    headlines have no Latin tokens but cluster legitimately by cosine).
    """
    if not text:
        return set()
    out: set[str] = set()
    for m in _NER_RE.findall(text):
        token = m.strip()
        if len(token) < 3:
            continue
        if token in _STOPWORDS:
            continue
        out.add(token)
    return out


def entities_overlap(a: set[str], b: set[str], *, min_shared: int = 1) -> bool:
    """True if both sides have entities AND share at least `min_shared`.

    If either side is empty, this returns False — callers should use that as
    a signal to fall back to cosine-only attach, NOT as a rejection.
    """
    if not a or not b:
        return False
    return len(a & b) >= min_shared
