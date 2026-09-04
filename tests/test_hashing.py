from codeguard.storage.hashing import compute_file_blob_hash, git_blob_hash

# Independently verified against git's own "blob <len>\0<content>" sha1
# algorithm - not re-derived from the implementation under test.
_HELLO_NEWLINE_HASH = "ce013625030ba8dba906f756967f9e9ca394464a"
_EMPTY_BLOB_HASH = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def test_git_blob_hash_matches_known_value():
    assert git_blob_hash(b"hello\n") == _HELLO_NEWLINE_HASH


def test_git_blob_hash_empty_content():
    assert git_blob_hash(b"") == _EMPTY_BLOB_HASH


def test_git_blob_hash_changes_with_content():
    assert git_blob_hash(b"hello\n") != git_blob_hash(b"goodbye\n")


def test_compute_file_blob_hash_reads_file_bytes(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"hello\n")
    assert compute_file_blob_hash(file_path) == _HELLO_NEWLINE_HASH