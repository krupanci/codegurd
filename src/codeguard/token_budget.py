"""
Shared token-budget enforcement.

Every engine in this project already hands back its results ranked
best-first (retriever.py's `final_score` sort, context/bundle.py's merged
item list). This module is the one place that turns an unbounded ranked
list into something that actually fits inside a caller's token budget,
using the same "priority chain, first cutoff wins" pattern this project
already uses for dead-code tiers (deadcode/finder.py) and call-resolution
tiers (graph/resolver.py): walk the ranked list in order, and once the
budget runs out, stop - never re-sort, never drop the best match to make
room for a worse one.

Token counts are estimated with `len(text) // 4` - a simple, fast
approximation, not a real tokenizer. This is deliberately swappable later
(pass a different `content_of` / estimator if a real tokenizer is ever
wired in) without touching the budgeting logic itself.
"""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")

_CHARS_PER_TOKEN = 4
_TRUNCATION_MARKER = "\n...[truncated, fetch by chunk_id for full content]"


def estimate_tokens(text: str) -> int:
    """Rough, fast token estimate - good enough for a budget cutoff, not
    for billing. See module docstring."""
    return len(text) // _CHARS_PER_TOKEN


def fit_to_budget(
    items: list[T],
    max_tokens: int,
    content_of: Callable[[T], str],
    replace_content: Callable[[T, str], T],
) -> list[T]:
    """
    Trim an already best-first-ranked list of items down to `max_tokens`.

    Every item up to the budget is kept unchanged. The first item that
    would push the running total over budget is kept too, but with its
    content truncated (via `replace_content`) to exactly fill the
    remaining space, plus a short marker noting it was cut - a
    high-scoring match never silently disappears, it just loses some body
    text. Everything after that item is dropped outright, since the list
    is already sorted best-first and nothing further would fit anyway.
    """
    if max_tokens <= 0:
        return []

    kept: list[T] = []
    used = 0

    for item in items:
        text = content_of(item)
        tokens = estimate_tokens(text)

        if used + tokens <= max_tokens:
            kept.append(item)
            used += tokens
            continue

        remaining_chars = (max_tokens - used) * _CHARS_PER_TOKEN - len(_TRUNCATION_MARKER)
        if remaining_chars > 0:
            truncated = text[:remaining_chars] + _TRUNCATION_MARKER
            kept.append(replace_content(item, truncated))
        break  # first cutoff wins - everything after this is dropped

    return kept