"""
Small, shared text-formatting helpers used by the per-package report
renderers (deadcode/report.py, retrieval/report.py, context/report.py,
impact/report.py).

This module is deliberately tiny. The four reports display genuinely
different shapes — a tier-grouped list, a flat ranked list, a hop-grouped
tree — and forcing them all through one generic "renderer" would trade
real, checkable duplication for a fake-generic abstraction that's harder
to read for very little saved code. So this only factors out the one line
that was copy-pasted verbatim across three of the four files: the indented
"why this is here" reason line under each reported item.
"""

from __future__ import annotations


def format_reason(reason: str, *, label: str | None = None) -> str:
    """
    The indented reason line shown under a reported item's summary line.

    `label`, if given, prefixes the reason (e.g. label="reason" produces
    "      reason: <text>", matching the deadcode report's existing
    format). Left as None for reports that show the reason text alone.
    """
    prefix = f"{label}: " if label else ""
    return f"      {prefix}{reason}"