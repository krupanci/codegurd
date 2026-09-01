"""
Thin wrapper around gitpython for exactly what Phase 5 needs: which .py
files changed relative to a ref, and what a file's content looked like at
that ref. No diffing logic lives here - just fetching raw material for
differ.py to work with.
"""

from __future__ import annotations

from pathlib import Path

from git import GitCommandError, Repo


def get_changed_python_files(project_root: Path, ref: str) -> list[str]:
    """
    Returns paths (relative to project_root) of every .py file that
    differs between `ref` and the current working tree.
    """
    repo = Repo(project_root)
    diff_output = repo.git.diff(ref, "--name-only")
    return [line for line in diff_output.splitlines() if line.endswith(".py")]


def get_file_content_at_ref(project_root: Path, ref: str, rel_path: str) -> str | None:
    """
    Returns the file's content as it existed at `ref`, or None if the file
    didn't exist at that ref (i.e. it's newly added since then - nothing
    to diff against, handled by the caller).
    """
    repo = Repo(project_root)
    try:
        return repo.git.show(f"{ref}:{rel_path}")
    except GitCommandError:
        return None