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
from codeguard.parsing.models import Chunk
from codeguard.storage.schema import CHUNKS_SCHEMA, CHUNKS_TABLE_NAME


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

    def insert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        table = self._chunks_table()
        table.add([c.to_row() for c in chunks])

    def delete_chunks_for_file(self, file_path: str) -> None:
        table = self._chunks_table()
        # Escape single quotes defensively; file paths won't normally have
        # them, but don't build a broken filter string if one ever does.
        safe_path = file_path.replace("'", "''")
        table.delete(f"file_path = '{safe_path}'")

    def get_stored_blob_hash(self, file_path: str) -> str | None:
        """
        Return the blob_hash currently stored for this file, or None if the
        file has no rows yet. Since every chunk from the same file carries
        the same file-level blob_hash, we only need to look at one row.
        """
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
        """True if this file has never been indexed, or has changed since."""
        stored = self.get_stored_blob_hash(file_path)
        return stored != current_blob_hash

    def all_chunks_for_file(self, file_path: str) -> list[dict]:
        table = self._chunks_table()
        safe_path = file_path.replace("'", "''")
        return table.search().where(f"file_path = '{safe_path}'").to_list()