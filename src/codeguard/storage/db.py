"""
Storage: one instance per CLI invocation, owns the LanceDB connection.

Deliberately a class instead of a module-level global connection, so tests
can point it at a throwaway temp directory instead of touching real project
data, and so nothing about "which database am I talking to" is hidden state.
"""

from __future__ import annotations

from pathlib import Path

import lancedb

from codeguard.config import CodeGuardSettings
from codeguard.parsing.models import Chunk, Edge
from codeguard.storage.schema import (
    CHUNKS_SCHEMA,
    CHUNKS_TABLE_NAME,
    EDGES_SCHEMA,
    EDGES_TABLE_NAME,
)


class Storage:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self.db_path))

    @classmethod
    def from_settings(cls, settings: CodeGuardSettings) -> "Storage":
        """Preferred entry point from CLI/app code: build Storage off the
        one validated settings object instead of a raw path string."""
        return cls(settings.db_path)

    def _chunks_table(self):
        if CHUNKS_TABLE_NAME in self._db.list_tables().tables:
            return self._db.open_table(CHUNKS_TABLE_NAME)
        return self._db.create_table(CHUNKS_TABLE_NAME, schema=CHUNKS_SCHEMA)

    def _edges_table(self):
        if EDGES_TABLE_NAME in self._db.list_tables().tables:
            return self._db.open_table(EDGES_TABLE_NAME)
        return self._db.create_table(EDGES_TABLE_NAME, schema=EDGES_SCHEMA)

    def insert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return

        # Phase 6: the `chunks` table's schema now has a required `vector`
        # column. Catching a missing vector HERE, with a clear message,
        # beats letting a confusing Arrow/LanceDB type error surface from
        # deep inside `table.add()` - a chunk that skipped the embedding
        # step is a caller bug, not a storage bug, so say so plainly.
        missing = [c.chunk_id for c in chunks if c.vector is None]
        if missing:
            raise ValueError(
                f"{len(missing)} chunk(s) have no embedding vector and "
                "cannot be inserted (e.g. chunk_id="
                f"{missing[0]!r}). Run chunks through an Embedder first - "
                "see codeguard.indexing.index_project."
            )

        table = self._chunks_table()
        table.add([c.to_row() for c in chunks])

    def delete_chunks_for_file(self, file_path: str) -> None:
        table = self._chunks_table()
        safe_path = file_path.replace("'", "''")
        table.delete(f"file_path = '{safe_path}'")

    def get_stored_blob_hash(self, file_path: str) -> str | None:
        table = self._chunks_table()
        if table.count_rows() == 0:
            return None

        safe_path = file_path.replace("'", "''")
        rows = (
            table.search()
            .where(f"file_path = '{safe_path}'")
            .limit(1)
            .to_list()
        )
        return rows[0]["blob_hash"] if rows else None

    def is_stale(self, file_path: str, current_blob_hash: str) -> bool:
        stored = self.get_stored_blob_hash(file_path)
        return stored != current_blob_hash

    def all_chunks_for_file(self, file_path: str) -> list[dict]:
        table = self._chunks_table()
        safe_path = file_path.replace("'", "''")
        return table.search().where(f"file_path = '{safe_path}'").to_list()

    def all_chunks(self) -> list[dict]:
        """
        Every chunk row in the project, unfiltered - added in Phase 3.

        Phase 3 builds the call graph in memory from a full snapshot of
        both tables (not a per-file slice like the staleness-check methods
        above use), so this is a plain unfiltered scan. No `.where()`
        clause, matching the same `table.search()` pattern already used
        elsewhere in this class - `search()` here is just "read rows",
        since we're not passing a query vector.
        """
        table = self._chunks_table()
        if table.count_rows() == 0:
            return []
        return table.search().to_list()

    # --- semantic search (Phase 6) ----------------------------------------

    def semantic_search(self, query_vector: list[float], limit: int = 10) -> list[dict]:
        """
        Nearest-neighbor search over the `chunks` table's `vector` column.

        Returns the same row shape as `all_chunks()`, plus a `_distance`
        field LanceDB adds automatically (cosine distance: 0 = identical
        meaning, 2 = opposite meaning - closer to 0 is a better match).

        This is a brute-force scan, which is exactly right at the scale
        this tool targets (a single project's functions/classes - typically
        thousands of rows, not millions). A real ANN index
        (`table.create_index(...)`) only pays for itself at a much larger
        scale, and would be a Phase 8+ concern if this tool ever needed to
        search across many large projects at once.
        """
        table = self._chunks_table()
        if table.count_rows() == 0:
            return []
        return (
            table.search(query_vector, vector_column_name="vector")
            .metric("cosine")
            .limit(limit)
            .to_list()
        )

    # --- edges (Phase 2) --------------------------------------------------
    # Deliberately mirrors the chunk methods above exactly - same delete +
    # re-insert pattern for staleness, same class, same style. No new
    # concepts here, just a second table.

    def insert_edges(self, edges: list[Edge]) -> None:
        if not edges:
            return
        table = self._edges_table()
        table.add([e.to_row() for e in edges])

    def delete_edges_for_file(self, file_path: str) -> None:
        table = self._edges_table()
        safe_path = file_path.replace("'", "''")
        table.delete(f"file_path = '{safe_path}'")

    def all_edges_for_file(self, file_path: str) -> list[dict]:
        table = self._edges_table()
        safe_path = file_path.replace("'", "''")
        return table.search().where(f"file_path = '{safe_path}'").to_list()

    def all_edges(self) -> list[dict]:
        """Every edge row in the project, unfiltered - added in Phase 3,
        same reasoning as `all_chunks()` above."""
        table = self._edges_table()
        if table.count_rows() == 0:
            return []
        return table.search().to_list()