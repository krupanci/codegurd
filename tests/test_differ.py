from codeguard.impact.differ import diff_signatures
from codeguard.parsing.models import Chunk


def _chunk(qualified_name: str, content: str, chunk_id: str = "id1") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        file_path="a.py",
        symbol_name=qualified_name.rsplit(".", 1)[-1],
        qualified_name=qualified_name,
        kind="function",
        start_line=1,
        end_line=1,
        content=content,
        blob_hash="deadbeef",
    )


def test_diff_signatures_detects_added_parameter():
    old = [_chunk("foo", "def foo(a):\n    pass\n")]
    new = [_chunk("foo", "def foo(a, b):\n    pass\n")]

    changed = diff_signatures(old, new)

    assert len(changed) == 1
    assert changed[0].change_type == "signature_changed"
    assert changed[0].qualified_name == "foo"


def test_diff_signatures_ignores_body_only_changes():
    old = [_chunk("foo", "def foo(a):\n    return a\n")]
    new = [_chunk("foo", "def foo(a):\n    return a + 1\n")]

    assert diff_signatures(old, new) == []


def test_diff_signatures_ignores_whitespace_only_reformat():
    # extract_signature_text collapses RUNS of whitespace into one space
    # (`" ".join(raw.split())`) - it doesn't insert or remove whitespace
    # next to punctuation, so this only covers reformatting that changes
    # how much whitespace already separates two tokens, not e.g. adding a
    # space after a comma (which is itself a real, if cosmetic, edit).
    old = [_chunk("foo", "def foo(a,   b):\n    pass\n")]
    new = [_chunk("foo", "def foo(a, b):\n    pass\n")]

    assert diff_signatures(old, new) == []


def test_diff_signatures_detects_removed_function():
    old = [_chunk("foo", "def foo(a):\n    pass\n")]
    new = []

    changed = diff_signatures(old, new)

    assert len(changed) == 1
    assert changed[0].change_type == "removed"
    assert changed[0].new_signature is None


def test_diff_signatures_matches_by_chunk_id_not_name():
    old = [_chunk("foo", "def foo(a):\n    pass\n", chunk_id="same-id")]
    new = [_chunk("bar", "def foo(a, b):\n    pass\n", chunk_id="same-id")]

    changed = diff_signatures(old, new)

    assert len(changed) == 1
    assert changed[0].qualified_name == "bar"  # reports under the NEW name