from codeguard.parsing.ids import make_chunk_id


def test_make_chunk_id_is_deterministic():
    assert make_chunk_id("a.py", "Foo.bar") == make_chunk_id("a.py", "Foo.bar")


def test_make_chunk_id_differs_by_file():
    assert make_chunk_id("a.py", "Foo.bar") != make_chunk_id("b.py", "Foo.bar")


def test_make_chunk_id_differs_by_qualified_name():
    assert make_chunk_id("a.py", "Foo.bar") != make_chunk_id("a.py", "Foo.baz")


def test_make_chunk_id_is_a_sha1_hex_digest():
    result = make_chunk_id("a.py", "Foo.bar")
    assert len(result) == 40
    int(result, 16)  # raises ValueError if not valid hex