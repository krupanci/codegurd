"""
Turns a RetrievalResult into plain text for the CLI - same split as
impact/report.py and deadcode/report.py: display format lives here,
ranking/selection logic stays in retriever.py.
"""

from __future__ import annotations

from codeguard.rendering import format_reason
from codeguard.retrieval.models import RetrievalResult


def render_retrieval_result(result: RetrievalResult) -> str:
    if result.entry_point_chunk_id is None:
        return (
            f"No relevant code found for: '{result.query}'\n"
            "(is the project indexed yet? run any codeguard command once "
            "to index it first)"
        )

    lines = [f"Relevant code for: '{result.query}'", ""]

    for chunk in result.chunks:
        marker = "-> " if chunk.chunk_id == result.entry_point_chunk_id else "   "
        lines.append(
            f"{marker}{chunk.file_path}  {chunk.qualified_name}  "
            f"[{chunk.relation}, score={chunk.final_score:.2f}]"
        )
        lines.append(format_reason(chunk.reason))

    return "\n".join(lines)