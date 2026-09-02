"""
Minimal project indexing pass.

Phases 1-5 all assumed chunks/edges were "already indexed" in LanceDB, but
nothing in the codebase so far actually walks a project's files and puts
them there - that wiring was left for Phase 8's unified CLI. Phase 6 can't
demonstrate "embed all chunks and query them" without SOME version of that
step existing first, so this module adds the minimal version now: walk the
project, re-chunk anything whose blob hash changed (Phase 1's staleness
check), embed the new/changed chunks (Phase 6), and store everything.

Phase 8 will likely wrap this exact function behind `codeguard scan` -
nothing here is thrown away later, it's just not yet exposed as a command.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from codeguard.embedding.embedder import Embedder, get_default_embedder
from codeguard.parsing.chunker import Chunker
from codeguard.storage.db import Storage
from codeguard.storage.hashing import compute_file_blob_hash

# Directories that are never source we want to index, even though they may
# contain .py files (virtual envs, git internals, codeguard's own db dir).
_SKIP_DIR_NAMES = {".git", ".codeguard", "__pycache__", ".venv", "venv", "node_modules"}


def _iter_python_files(project_root: Path):
    for path in project_root.rglob("*.py"):
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def index_project(
    project_root: Path,
    storage: Storage,
    embedder: Embedder | None = None,
) -> int:
    """
    Re-index every .py file under `project_root` whose content has changed
    since the last run. Returns how many files were actually re-chunked
    (an unchanged file is skipped entirely via the blob-hash check, so
    repeated runs on a mostly-unchanged codebase stay fast).
    """
    embedder = embedder or get_default_embedder()
    chunker = Chunker()
    reindexed = 0

    for file_path in _iter_python_files(project_root):
        rel_path = str(file_path.relative_to(project_root))
        current_hash = compute_file_blob_hash(file_path)

        if not storage.is_stale(rel_path, current_hash):
            continue

        # Same delete-then-reinsert pattern used everywhere else in this
        # project for staleness (Phase 1/2's Storage methods) - stale rows
        # for this file are thrown away before the fresh ones go in, so a
        # symbol that was deleted from the file doesn't linger in storage.
        storage.delete_chunks_for_file(rel_path)
        storage.delete_edges_for_file(rel_path)

        chunks, edges = chunker.parse_file(file_path, project_root)

        if chunks:
            vectors = embedder.embed_documents([chunk.content for chunk in chunks])
            chunks = [
                dataclasses.replace(chunk, vector=tuple(vector))
                for chunk, vector in zip(chunks, vectors)
            ]

        storage.insert_chunks(chunks)
        storage.insert_edges(edges)
        reindexed += 1

    return reindexed