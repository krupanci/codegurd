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
    
    
EdgeKind = Literal["calls", "imports"]


@dataclass(frozen=True)
class Edge:
    """
    One raw, NOT-yet-resolved relationship found while walking a file.

    The source side is resolved immediately, because it is never ambiguous:
    while walking the tree we already know exactly which chunk we're
    standing inside (Phase 1 built that chunk's id one step earlier).

    The target side is deliberately left as a raw name, because resolving
    it (which actual chunk does "save" refer to, out of possibly several?)
    requires cross-file lookup and ranked-candidate matching — that
    judgment call is Phase 3's job, not this one's.
    """

    kind: EdgeKind                    # "calls" | "imports"
    file_path: str                     # file this edge was found in (relative to root)

    source_chunk_id: str | None        # resolved chunk id, or None if at module level
    source_qualified_name: str          # e.g. "LoginHandler.validate", or "<module>"

    target_name: str                    # bare rightmost name, e.g. "save", "Path"
    target_expression: str               # full text, e.g. "self.db.save", "os.path.join"

    # Only meaningful for kind == "imports"; empty string for "calls".
    imported_from_module: str            # e.g. "pathlib" for `from pathlib import Path`, else ""
    local_alias: str                      # e.g. "Opt" for `Optional as Opt`, else same as target_name

    def to_row(self) -> dict:
        """Dict shape matching storage/schema.py, ready to insert into LanceDB."""
        return asdict(self)
    