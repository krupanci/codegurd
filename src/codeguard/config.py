"""
Project configuration, as a single typed, validated settings object -
loaded from environment variables / a .env file, following the same
pattern used across the rest of an enterprise-style stack (env -> pydantic
-> one object passed around, never loose module-level functions reaching
into globals).

Usage:
    settings = CodeGuardSettings.for_project()   # auto-discovers project root
    settings.db_path                              # -> Path to lancedb dir
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Files/dirs that, if found walking upward, mark a directory as the
# project root. Not user-configurable via env - this is discovery logic,
# not a setting.
_ROOT_MARKERS = (".git", "pyproject.toml")


class CodeGuardSettings(BaseSettings):
    """
    All configurable, environment-driven values for a codeguard run.

    Any value here can be overridden with an env var prefixed CODEGUARD_
    (e.g. CODEGUARD_DB_DIR_NAME=my_db) or via a .env file in the current
    directory. This is also where future settings land as new phases need
    them (embedding model name in Phase 6, an API key if a paid embedder
    is ever swapped in, etc.) - one object, one place, rather than new
    loose globals per phase.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEGUARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_root: Path
    codeguard_dir_name: str = Field(default=".codeguard")
    db_dir_name: str = Field(default="lancedb")

    @property
    def codeguard_dir(self) -> Path:
        path = self.project_root / self.codeguard_dir_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        return self.codeguard_dir / self.db_dir_name

    @classmethod
    def for_project(cls, start: Path | str | None = None, **overrides) -> "CodeGuardSettings":
        """
        Build settings for whatever project contains `start` (default: cwd),
        auto-discovering the project root by walking upward. Any explicit
        keyword still wins over env vars, which still win over defaults -
        standard pydantic-settings precedence.
        """
        root = overrides.pop("project_root", None) or _find_project_root(start)
        return cls(project_root=root, **overrides)


def _find_project_root(start: Path | str | None = None) -> Path:
    """
    Walk upward from `start` until a directory containing a root marker
    (.git or pyproject.toml) is found. Falls back to `start` itself if
    nothing is found, so the tool still works pointed at a bare folder.
    """
    current = Path(start or Path.cwd()).resolve()

    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate

    return current