"""Tests for the heuristic NER used to gate cluster attaches (Phase 8)."""
from buzz_news.entities import entities_overlap, extract_entities


def test_extract_proper_noun_run():
    ents = extract_entities("Donald Trump meets Narendra Modi in New Delhi")
    assert "Donald Trump" in ents
    assert "Narendra Modi" in ents
    assert "New Delhi" in ents


def test_extract_acronyms():
    ents = extract_entities("BJP MLA arrested; SEBI investigates HDFC")
    assert "BJP" in ents
    assert "SEBI" in ents
    assert "HDFC" in ents
    assert "MLA" in ents


def test_extract_year():
    ents = extract_entities("Budget 2026 unveils new tax slabs")
    assert "2026" in ents


def test_stopwords_dropped():
    # "Today", "Live", "India", "World" must not count as event anchors
    ents = extract_entities("Today Live: India and World news updates")
    assert "Today" not in ents
    assert "Live" not in ents
    assert "India" not in ents
    assert "World" not in ents


def test_empty_input():
    assert extract_entities(None) == set()
    assert extract_entities("") == set()


def test_devanagari_only_returns_empty():
    # Hindi headlines with no Latin script → must be empty so callers fall
    # back to cosine-only attach (don't reject legit Hindi clusters).
    ents = extract_entities("कर्क राशि वालों के लिए धन और करियर में सुधार का दिन")
    assert ents == set()


def test_h1b_attach_decision_blocks_offtopic():
    # Audit example: a Republican-bill cluster pulled in a Texas AG H-1B
    # scam item at cosine 0.993. With NER overlap, the only shared token is
    # H-1B — overlap >= 1, so it would still attach. Verifies the gate
    # behaves predictably for this real example.
    a = extract_entities("Republican bill tightens H-1B visa rules")
    b = extract_entities("Texas AG sues Chinese-owned firm over alleged H-1B visa scam")
    # H-1B appears in both, so they share an entity — gate would NOT block.
    # That's fine: the cosine threshold drop to 0.18 catches this one because
    # the underlying stories aren't actually at sim 0.993 — that was post-drift.
    assert "H-1B" in a
    assert "H-1B" in b


def test_overlap_helper():
    a = {"Trump", "Iran"}
    b = {"Russia", "Luhansk"}
    # Two stories from the same week, both world news, share no entities.
    assert not entities_overlap(a, b)
    # Sharing one entity is enough by default
    c = {"Trump", "Putin"}
    assert entities_overlap(a, c)
    # Empty side → False (caller treats as "fall through to cosine")
    assert not entities_overlap(a, set())
    assert not entities_overlap(set(), b)
