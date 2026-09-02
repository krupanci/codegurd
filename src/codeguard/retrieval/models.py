"""
Plain data shapes for scoped context retrieval (Phase 7) - matches the
models.py pattern already used in deadcode/, impact/, and graph/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Why a chunk ended up in the bundle:
#   entry_point  - the single best semantic match for the query.
#   caller       - reaches the entry point by calling it (directly or
#                   transitively) - i.e. "who is affected if this changes".
#   dependency   - the entry point calls this (directly or transitively) -
#                   i.e. "where the root cause likely lives".
#   related      - a strong semantic match on its own, but not connected
#                   to the entry point anywhere in the call graph.
RelationType = Literal["entry_point", "caller", "dependency", "related"]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    file_path: str
    qualified_name: str
    kind: str
    content: str

    relation: RelationType
    hop_distance: int          # 0 = entry point itself, -1 = not graph-connected

    semantic_score: float      # 0..1, how close this chunk is to the query by meaning
    graph_score: float         # 0..1, how structurally important this chunk is
    final_score: float         # combined score used to rank/trim the bundle

    reason: str                # one-line, human-readable "why this is here"


@dataclass
class RetrievalResult:
    query: str
    entry_point_chunk_id: str | None
    chunks: list[RetrievedChunk] = field(default_factory=list)