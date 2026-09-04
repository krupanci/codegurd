"""
Turns a ContextBundle into plain text for the CLI — same split as
retrieval/report.py, impact/report.py, deadcode/report.py.
"""

from __future__ import annotations

from codeguard.context.models import ContextBundle
from codeguard.rendering import format_reason


def render_context_bundle(bundle: ContextBundle) -> str:
    if not bundle.items:
        return f"No context found for: '{bundle.task}'"

    lines = [
        f"Context for: '{bundle.task}'",
        f"(engines used: {', '.join(bundle.engines_used)})",
        "",
    ]
    for item in bundle.items:
        conf = f", tier={item.confidence}" if item.confidence else ""
        lines.append(
            f"[{item.source}/{item.relation}{conf}] "
            f"{item.file_path}  {item.qualified_name}  (score={item.score:.2f})"
        )
        lines.append(format_reason(item.reason))
        for note in item.notes:
            lines.append(format_reason(note, label="note"))

    return "\n".join(lines)