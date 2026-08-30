"""
Phase 3 models: the result of resolving one raw "calls" Edge (from Phase 2)
against every chunk in the project.

Kept as its own frozen dataclass, separate from Edge itself, for the same
reason Phase 2's Decision C split raw extraction from resolution into two
steps: a resolved edge is a *judgment call* built on top of a raw *fact*,
and keeping them as two distinct types means the original Edge rows in
LanceDB are never touched or reinterpreted - ResolvedEdge is a new object
built fresh in memory, every run, from unchanged raw data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from codeguard.parsing.models import Edge

MatchTier = Literal["same_file", "imported_module", "global", "unresolved"]


@dataclass(frozen=True)
class ResolvedCandidate:
    """One possible target chunk for a call. Plural because a call to
    `save` might genuinely match more than one real chunk - see MatchTier
    and ResolvedEdge below."""

    chunk_id: str
    qualified_name: str
    file_path: str


@dataclass(frozen=True)
class ResolvedEdge:
    """
    The result of trying to resolve one raw `calls` Edge's `target_name`
    to actual chunk(s).

    `candidates` holds every chunk that matched at the *first* tier that
    produced any match at all (same_file, then imported_module, then
    global) - never a mix of tiers. More than one candidate means the
    match was genuinely ambiguous at that tier (e.g. two `save` methods
    both imported into the same file); this is preserved rather than
    silently guessing one, per the project's Phase 3 design (see
    PHASE3_DECISIONS.md, Decision C).

    `confidence == "unresolved"` means no chunk anywhere matched the name
    at all (e.g. a call to a builtin like `len()`, or to a third-party
    library function) - `candidates` is empty in that case.
    """

    edge: Edge
    candidates: tuple[ResolvedCandidate, ...]
    confidence: MatchTier