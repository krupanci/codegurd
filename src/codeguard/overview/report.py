"""
Turns a RepoMapResult into plain text for the CLI - same convention as
deadcode/report.py, retrieval/report.py, impact/report.py, and
context/report.py: display format lives here, ranking logic stays in
ranker.py.
"""

from __future__ import annotations

from codeguard.overview.models import RepoMapResult


def render_repo_map(result: RepoMapResult) -> str:
    if not result.items:
        return "No symbols found (is the project indexed yet?)"

    lines = [f"Repo map - top {len(result.items)} symbol(s) by structural importance", ""]
    for item in result.items:
        lines.append(
            f"  {item.file_path}  {item.qualified_name}  [{item.kind}]  "
            f"(importance={item.importance_score:.2f})"
        )
    return "\n".join(lines)