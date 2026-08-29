"""
Stable chunk IDs.

Deliberately built from (file_path, qualified_name) ONLY - never from line
numbers and never from content. This is what makes an ID survive:
  - unrelated edits elsewhere in the file (which shift line numbers)
  - the symbol's own body changing (Phase 5 needs old vs new content to be
    matched under the SAME id, otherwise "what changed" can't be computed)

An ID intentionally changes if the symbol is renamed or moved to another
file - from codeguard's point of view that is a new identity, which is the
correct behavior.
"""

from __future__ import annotations

import hashlib


def make_chunk_id(file_path: str, qualified_name: str) -> str:
    key = f"{file_path}::{qualified_name}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()