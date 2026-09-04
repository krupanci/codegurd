"""
Turns a list of OrphanFinding into plain text for the CLI.

Kept separate from finder.py so the display format can change without
touching any detection logic - the same split already used for
impact/report.py. Phase 4 never had a renderer of its own (it only had to
produce OrphanFinding objects for tests); Phase 8 adds this because `scan`
is the first place that actually needs to print a report to a person.
"""

from __future__ import annotations

from codeguard.deadcode.models import OrphanFinding, Tier
from codeguard.rendering import format_reason

# Ordered most to least trustworthy, same order as the Tier type itself -
# a person reading the report sees the findings worth acting on first.
_TIER_LABELS: dict[Tier, str] = {
    "high_confidence_dead": "HIGH CONFIDENCE - likely dead",
    "test_only": "TEST-ONLY - only called from test files",
    "possibly_dynamic_usage": "POSSIBLY DYNAMIC - decorator suggests framework use",
    "possibly_used_outside_python": "POSSIBLY USED - name also found outside Python",
}


def render_deadcode_report(findings: list[OrphanFinding]) -> str:
    if not findings:
        return "No dead code candidates found."

    by_tier: dict[Tier, list[OrphanFinding]] = {}
    for finding in findings:
        by_tier.setdefault(finding.tier, []).append(finding)

    lines = [f"Dead code report - {len(findings)} finding(s)", ""]

    for tier, label in _TIER_LABELS.items():
        tier_findings = by_tier.get(tier)
        if not tier_findings:
            continue

        lines.append(f"{label} ({len(tier_findings)})")
        for finding in sorted(tier_findings, key=lambda f: (f.file_path, f.start_line)):
            lines.append(
                f"  {finding.file_path}:{finding.start_line}  "
                f"{finding.qualified_name}  [{finding.kind}]"
            )
            lines.append(format_reason(finding.reason, label="reason"))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"