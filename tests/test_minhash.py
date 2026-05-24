from buzz_news.minhash import create_minhash, is_duplicate, MinHashLSH, deduplicate_texts


def test_create_minhash_produces_valid_minhash():
    text1 = "This is a test article about technology and innovation"
    text2 = "This is a test article about technology and innovation"
    m1 = create_minhash(text1)
    m2 = create_minhash(text2)
    assert m1.jaccard(m2) == 1.0


def test_create_minhash_different_texts():
    text1 = "Apple releases new iPhone"
    text2 = "Microsoft announces new Azure services"
    m1 = create_minhash(text1)
    m2 = create_minhash(text2)
    jaccard = m1.jaccard(m2)
    assert jaccard < 0.5


def test_is_duplicate_detects_near_copy():
    lsh = MinHashLSH(threshold=0.7, num_perm=128)
    minhashes = {}
    text1 = "Breaking news major earthquake hits Japan with major tsunami warning"
    m1 = create_minhash(text1)
    lsh.insert("item1", m1)
    minhashes["item1"] = m1

    text2 = "Breaking news major earthquake hits Japan with major tsunami warning issued"
    dup = is_duplicate(text2, lsh, minhashes, threshold=0.7)
    assert dup is not None
    assert dup == "item1"


def test_is_duplicate_no_false_positive():
    lsh = MinHashLSH(threshold=0.85, num_perm=128)
    minhashes = {}
    text1 = "Apple releases new MacBook Pro with M4 chip"
    m1 = create_minhash(text1)
    lsh.insert("item1", m1)
    minhashes["item1"] = m1

    text2 = "Microsoft announces new Surface laptop with Intel processor"
    dup = is_duplicate(text2, lsh, minhashes)
    assert dup is None


def test_deduplicate_texts_groups_similar():
    texts = [
        "Breaking news major earthquake hits Japan",
        "Breaking news major earthquake hits Japan warning",
        "Apple releases new iPhone with great camera",
        "Microsoft announces new Surface laptop",
    ]
    ids = ["1", "2", "3", "4"]
    clusters = deduplicate_texts(texts, ids, threshold=0.7)
    assert len(clusters) == 3
    cluster_ids = [set(c) for c in clusters]
    assert {"1", "2"} in cluster_ids
    assert {"3"} in cluster_ids
    assert {"4"} in cluster_ids
