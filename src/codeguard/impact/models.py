"""
Plain data shapes for the blast-radius change-impact feature (Phase 5 v1).
No behavior here - matches the models.py pattern used in deadcode/ and
parsing/.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChangedSymbol:
    """A function/method whose signature differs between old ref and now."""
    chunk_id: str
    qualified_name: str
    file_path: str
    change_type: str          # "signature_changed" or "removed"
    old_signature: str | None
    new_signature: str | None


@dataclass
class BlastRadiusNode:
    """One changed symbol plus everything reachable from it via the reverse graph."""
    changed_symbol: ChangedSymbol
    # chunk_id -> hop distance (1 = direct caller, 2 = caller-of-caller, ...)
    affected: dict[str, int] = field(default_factory=dict)


@dataclass
class BlastRadiusReport:
    ref: str
    nodes: list[BlastRadiusNode] = field(default_factory=list)