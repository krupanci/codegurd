"""
Turns a BlastRadiusReport into plain text for the CLI. Kept separate from
analyzer.py so the display format can change without touching any logic -
same split used for finder.py vs. whatever renders Phase 4's report.
"""

from __future__ import annotations

from codeguard.impact.models import BlastRadiusReport


def render_report(report: BlastRadiusReport) -> str:
    if not report.nodes:
        return f"No signature changes detected against '{report.ref}'."

    lines = [f"Change-impact report against '{report.ref}'", ""]

    for node in report.nodes:
        cs = node.changed_symbol
        lines.append(f"Changed symbol: {cs.qualified_name}  ({cs.file_path})")
        if cs.change_type == "removed":
            lines.append(f"  status: REMOVED (old signature: {cs.old_signature})")
        else:
            lines.append(f"  old signature: {cs.old_signature}")
            lines.append(f"  new signature: {cs.new_signature}")

        if not node.affected:
            lines.append("  Blast radius: none - no callers found.")
        else:
            lines.append(f"  Blast radius ({len(node.affected)} affected):")
            by_hop: dict[int, list[str]] = {}
            for chunk_id, hop in node.affected.items():
                by_hop.setdefault(hop, []).append(chunk_id)
            for hop in sorted(by_hop):
                for chunk_id in sorted(by_hop[hop]):
                    lines.append(f"    {hop} hop(s): {chunk_id}")
        lines.append("")

    return "\n".join(lines)