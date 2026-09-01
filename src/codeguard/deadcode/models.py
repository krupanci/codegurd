"""
Phase 4 models: the result of checking one candidate orphan (a chunk with
zero incoming call edges) against the confidence rules in rules.py.

Kept as its own frozen dataclass, separate from Chunk/Edge/ResolvedEdge,
for the same reason those phases split raw facts from judgment calls: an
OrphanFinding is CodeGuard's OPINION about a chunk, built on top of the
Phase 3 graph - it is never written back into the graph or into LanceDB,
and re-running Phase 4 on unchanged data always rebuilds it fresh.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Ordered here from most to least trustworthy - report.py (or whatever
# renders the final output) can rely on this order rather than re-deriving
# it, since Python dicts/lists preserve insertion order.
Tier = Literal[
    "high_confidence_dead",
    "test_only",
    "possibly_dynamic_usage",
    "possibly_used_outside_python",
]


@dataclass(frozen=True)
class OrphanFinding:
    """
    One reported symbol, plus WHY it landed in its tier.

    `reason` is a short, human-readable sentence (e.g. "decorator '@app.route'
    matches known dynamic-invocation pattern") - the whole point of tiering
    instead of a flat list is that a person reading the report shouldn't
    have to re-derive why something is only "possibly" dead.
    """

    chunk_id: str
    qualified_name: str
    file_path: str
    kind: str  # "function" | "method" | "class" - copied from Chunk for display
    start_line: int
    tier: Tier
    reason: str