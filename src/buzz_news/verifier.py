import logging
import re

log = logging.getLogger("buzz_news.verifier")

_PROPER_NOUN = re.compile(
    r"\b(?!The\b|A\b|An\b|This\b|That\b|These\b|Those\b)"
    r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,4})\b"
)
_STOPWORDS = {
    "The", "A", "An", "It", "He", "She", "They", "We", "I", "And", "Or",
    "But", "Reuters", "BBC", "AP", "PTI", "AFP", "BN", "PTI",
}


def extract_entities(text: str) -> set[str]:
    found = set()
    for m in _PROPER_NOUN.finditer(text):
        ent = m.group(1).strip()
        if ent not in _STOPWORDS and len(ent) >= 3:
            found.add(ent)
    return found


def verify_en(article_body: str, source_corpus: str, max_unverified: int = 1) -> tuple[bool, list[str]]:
    entities = extract_entities(article_body)
    corpus_lc = source_corpus.lower()
    unverified = [e for e in entities if e.lower() not in corpus_lc]
    return (len(unverified) <= max_unverified, unverified)


def verify_hi(
    hi_body: str,
    en_body: str,
    source_corpus: str,
) -> tuple[bool, list[str]]:
    corpus_lc = source_corpus.lower()

    hi_unverified_en_tokens = []
    for m in _PROPER_NOUN.finditer(hi_body):
        ent = m.group(1).strip()
        if ent not in _STOPWORDS and len(ent) >= 3:
            if ent.lower() not in corpus_lc:
                hi_unverified_en_tokens.append(ent)

    return (len(hi_unverified_en_tokens) == 0, hi_unverified_en_tokens)
