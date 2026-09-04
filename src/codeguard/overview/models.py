"""
Plain data shapes for whole-repo orientation (`codeguard map`) - answers
"I've never seen this repo, where do I even start?" without requiring a
specific query first. Matches the models.py pattern used by every other
package (deadcode/, impact/, graph/, retrieval/, context/).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RepoMapItem:
    """One symbol worth knowing about, plus how structurally important
    it is (see overview/ranker.py)."""

    qualified_name: str
    file_path: str
    kind: str
    importance_score: float


@dataclass
class RepoMapResult:
    items: list[RepoMapItem] = field(default_factory=list)
    budget: int = 12