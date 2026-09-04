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

CHANGE (index-corruption guard): before touching any files, the current
embedder's name is compared against whatever `repo_meta` says the index
on disk was last built with (see storage/db.py). A silent embedder swap
in config.py would otherwise leave query vectors and stored vectors in
two different, incompatible spaces - `semantic_search` would keep
returning rows with no error at all, just wrong ones. Same "fail loud,
not silently" principle `Storage.insert_chunks` already applies to a
missing embedding vector.

CHANGE (ignore rules): `_SKIP_DIR_NAMES` now also covers `build`/`dist`
(previously only virtualenvs and VCS/cache dirs), and an optional
`.codeguardignore` file at the project root (one fnmatch glob per line,
gitignore-flavored: blank lines and `#` comments skipped) lets a project
exclude anything else without a new dependency.
"""

from __future__ import annotations

import dataclasses
import fnmatch
from pathlib import Path

from codeguard.embedding.embedder import Embedder, get_default_embedder
from codeguard.parsing.chunker import Chunker
from codeguard.storage.db import Storage
from codeguard.storage.hashing import compute_file_blob_hash
from codeguard.storage.schema import EMBEDDING_DIM

# Directories that are never source we want to index, even though they may
# contain .py files (virtual envs, git internals, codeguard's own db dir,
# build output).
_SKIP_DIR_NAMES = {
    ".git", ".codeguard", "__pycache__", ".venv", "venv", "node_modules", "build", "dist",
}

_IGNORE_FILE_NAME = ".codeguardignore"


def _load_ignore_patterns(project_root: Path) -> list[str]:
    """
    Optional extra ignore patterns from a `.codeguardignore` file at the
    project root - one glob pattern per line, gitignore-flavored (blank
    lines and `#` comments skipped), matched with `fnmatch` against each
    file's project-relative path. No new dependency, same "build it
    ourselves" convention `_SKIP_DIR_NAMES` above already uses.
    """
    ignore_file = project_root / _IGNORE_FILE_NAME
    if not ignore_file.exists():
        return []
    return [
        line.strip()
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _is_ignored(rel_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in patterns)


def _iter_python_files(project_root: Path):
    patterns = _load_ignore_patterns(project_root)
    for path in project_root.rglob("*.py"):
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        rel_path = path.relative_to(project_root).as_posix()
        if patterns and _is_ignored(rel_path, patterns):
            continue
        yield path


def _check_embedder_consistency(embedder: Embedder, storage: Storage) -> None:
    """
    Compare the embedder about to be used against whatever `repo_meta`
    says the index on disk was last built with. First run against an
    index with no recorded metadata: write it and move on (this also
    covers upgrading an index built before this check existed). A
    mismatch against a non-empty index raises, rather than silently
    mixing vector spaces.
    """
    embedder_name = getattr(embedder, "model_name", embedder.__class__.__name__)
    meta = storage.get_repo_meta()

    if meta is None:
        storage.set_repo_meta(embedder_name, EMBEDDING_DIM)
        return

    if meta["embedder_name"] != embedder_name or meta["embedding_dim"] != EMBEDDING_DIM:
        raise RuntimeError(
            f"This index was built with embedder '{meta['embedder_name']}' "
            f"(dim={meta['embedding_dim']}), but the current embedder is "
            f"'{embedder_name}' (dim={EMBEDDING_DIM}). Mixing vector spaces "
            "silently corrupts search results - delete the project's "
            ".codeguard directory and re-run to fully re-index with the "
            "new embedder."
        )


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
    _check_embedder_consistency(embedder, storage)

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