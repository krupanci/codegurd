"""
Unified context engine — plain data shapes for the single "I'm about to do
X, what do I need to know?" entry point that sits on top of retrieval
(Phase 7), impact analysis (Phase 5), and dead-code detection (Phase 4).
Matches the models.py pattern used by every other package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Source = Literal["retrieval", "impact", "deadcode"]


@dataclass
class ContextRequest:
    """
    What the caller (a human, or an agent) actually knows about the task.

    `task` is the only required field and is always free text — it is
    never parsed for keywords or sorted into a fixed set of "intents".
    `target_symbol` and `ref` are optional, EXPLICIT signals: if the
    caller already knows it's about to touch a specific symbol, or
    already has a diff to check, it says so directly instead of CodeGuard
    guessing that from the wording of `task`. Which engines run is
    decided from the presence of these fields (see context/bundle.py),
    not from classifying `task` itself.
    """
    task: str
    target_symbol: str | None = None
    ref: str | None = None
    max_results: int = 8


@dataclass(frozen=True)
class ContextItem:
    """
    One piece of evidence in the bundle, regardless of which engine
    produced it. Every engine's output is normalized into this same
    shape, so a caller only ever has to understand one structure.
    """
    chunk_id: str
    file_path: str
    qualified_name: str
    kind: str
    content: str

    source: Source           # which engine produced this item
    relation: str             # "entry_point" / "caller" / "dependency" /
                                # "related" / "blast_radius" / "dead_code_candidate"
    hop_distance: int          # -1 when not applicable / not graph-connected
    confidence: str | None     # deadcode tier, or None when not applicable
    score: float                 # 0..1, used to sort within the bundle

    reason: str                  # one-line, human-readable "why this is here"


@dataclass
class ContextBundle:
    task: str
    items: list[ContextItem] = field(default_factory=list)
    engines_used: list[str] = field(default_factory=list)