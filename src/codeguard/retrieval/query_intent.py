"""
Classifies a plain-English query as "bug_fix" or "explore", so the
retriever (retriever.py) can apply different selection rules for each -
see Pass 2 in the Phase 7 plan.

Deliberately simple keyword matching, not a model call: the whole point of
this classification is to decide how WIDE the graph walk should be and how
much to trust graph vs. semantic signal. A wrong guess here just shifts the
bundle slightly wider or narrower - it never breaks retrieval outright - so
a cheap, fully explainable heuristic is the right tool, not a second model
in the critical path.
"""

from __future__ import annotations

from typing import Literal

QueryIntent = Literal["bug_fix", "explore"]

_BUG_FIX_SIGNALS = (
    "bug", "fix", "broken", "not working", "isn't working", "doesn't work",
    "error", "exception", "fails", "failing", "fail", "crash", "crashing",
    "issue", "wrong", "incorrect", "regression", "throws", "raises",
)


def classify_intent(query: str) -> QueryIntent:
    """"login isn't working" -> bug_fix. "how does auth work" -> explore."""
    lowered = query.lower()
    if any(signal in lowered for signal in _BUG_FIX_SIGNALS):
        return "bug_fix"
    return "explore"