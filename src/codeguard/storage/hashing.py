"""
Git-compatible blob hashing, computed directly in Python (no `git` process
involved). This is the exact algorithm git itself uses for `git hash-object`:

    sha1("blob " + <byte length of content> + "\0" + content)

Because this only depends on the file's raw bytes, it works identically
whether or not the file is inside a git repository, and it always matches
what `git hash-object <file>` would print for a tracked file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def git_blob_hash(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("utf-8")
    return hashlib.sha1(header + content).hexdigest()


def compute_file_blob_hash(path: Path | str) -> str:
    content = Path(path).read_bytes()
    return git_blob_hash(content)