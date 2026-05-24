from buzz_news.verifier import extract_entities, verify_en, verify_hi


def test_extract_entities_basic():
    text = "Prime Minister Narendra Modi met with President Biden in Washington D.C."
    entities = extract_entities(text)
    # The regex matches contiguous capitalized runs
    assert "Prime Minister Narendra Modi" in entities
    assert "President Biden" in entities
    assert "Washington" in entities


def test_extract_entities_filters_stopwords():
    text = "The United States and India signed a new trade agreement"
    entities = extract_entities(text)
    assert "The" not in entities
    assert "United States" in entities
    assert "India" in entities


def test_extract_entities_short_words():
    text = "The AI company released a new model called Gemini Ultra"
    entities = extract_entities(text)
    assert "Gemini Ultra" in entities


def test_verify_en_all_entities_found():
    article = "Narendra Modi visited Washington to meet President Biden"
    corpus = "Narendra Modi visited Washington. President Biden met with Narendra Modi in Washington."
    passed, unverified = verify_en(article, corpus)
    assert passed is True
    assert len(unverified) == 0


def test_verify_en_one_unverified_ok():
    article = "President Obama visited the moon"
    corpus = "President Obama visited Washington. The moon landing was historic."
    passed, unverified = verify_en(article, corpus)
    assert passed is True


def test_verify_en_too_many_unverified():
    article = "President Obama and Elon Musk visited Mars together"
    corpus = "President Obama visited Washington."
    passed, unverified = verify_en(article, corpus)
    assert passed is False
    assert len(unverified) == 2


def test_verify_hi_no_extra_english_tokens():
    hi_body = "प्रधानमंत्री मोदी ने वाशिंगटन में राष्ट्रपति बाइडन से मुलाकात की"
    en_body = "Prime Minister Modi met President Biden in Washington"
    corpus = "modi met president biden in washington"
    passed, unverified = verify_hi(hi_body, en_body, corpus)
    assert passed is True


def test_verify_hi_english_token_in_hi_fails():
    hi_body = "प्रधानमंत्री ने Google के सीईओ से मुलाकात की"
    en_body = "Prime Minister met the CEO of Microsoft"
    corpus = "prime minister met microsoft"  # Google not in corpus
    passed, unverified = verify_hi(hi_body, en_body, corpus)
    assert passed is False
    assert "Google" in unverified
