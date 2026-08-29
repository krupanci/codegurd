"""
The Chunk model: one row of this is exactly one function, class, or method
found in a Python file.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal

ChunkKind = Literal["function", "class", "method"]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str        # stable id - see parsing/ids.py
    file_path: str        # path relative to the project root
    symbol_name: str      # e.g. "validate"
    qualified_name: str   # e.g. "LoginHandler.validate"
    kind: ChunkKind        # "function" | "class" | "method"
    start_line: int        # 1-indexed, inclusive
    end_line: int          # 1-indexed, inclusive
    content: str            # raw source text of the chunk
    blob_hash: str          # git blob hash of the WHOLE FILE this chunk came from

    def to_row(self) -> dict:
        """Dict shape matching storage/schema.py, ready to insert into LanceDB."""
        return asdict(self)