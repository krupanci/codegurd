"""
Phase 4: Dead Code / Orphan Finder.

Does NOT try to prove a symbol is truly unused - that's provably
impossible for a dynamic language without actually running the program
(see the project's own notes on Vulture/PyCG-style static-analysis
blind spots). Instead it answers a narrower, honest question per symbol:
"Python's own call graph shows no callers here. Given what else we can
cheaply check, how much should you trust that silence?"

Every check below only ever DOWNGRADES confidence or adds an explanation -
nothing is ever silently dropped from the report, matching the same
philosophy Phase 3's resolver already used for ambiguous call targets.
"""

from __future__ import annotations

from pathlib import Path

from codeguard.deadcode.decorators import get_decorator_names
from codeguard.deadcode.models import OrphanFinding, Tier
from codeguard.deadcode.rules import all_callers_are_tests, matching_dynamic_decorator
from codeguard.graph.graph import Graph

# Candidate orphans only ever get checked against non-Python files with
# these extensions - kept small and explicit rather than "everything that
# isn't .py", so an accidental match inside e.g. a .lock file or a binary
# asset can't happen.
_CROSS_LANGUAGE_EXTENSIONS = (".html", ".js", ".jsx", ".json", ".yaml", ".yml", ".toml")

_CALLABLE_KINDS = ("function", "method", "class")


def find_dead_code(graph: Graph, project_root: Path) -> list[OrphanFinding]:
    """
    Walk every chunk in `graph` and produce one OrphanFinding per symbol
    that's worth a human's attention - either a true zero-caller candidate
    (tiered by how much to trust it), or a symbol that technically has
    callers but only from test files.

    `project_root` is needed for two on-demand, file-reading checks that
    deliberately were NOT baked into Phase 1/2's schema: decorator lookup
    (decorators.py) and the cross-language name scan (below).
    """
    findings: list[OrphanFinding] = []

    for chunk_id, chunk in graph.chunks_by_id.items():
        if chunk.kind not in _CALLABLE_KINDS:
            continue

        caller_ids = graph.callers(chunk_id)

        if caller_ids:
            # Has real callers - not a dead-code candidate at all, UNLESS
            # every single caller is a test, which is a different, milder
            # finding worth surfacing separately (see rules.py docstring
            # for why an empty caller list is handled as a separate branch
            # rather than folded into this same check).
            caller_paths = [_caller_file_path(graph, cid) for cid in caller_ids]
            if all_callers_are_tests(caller_paths):
                findings.append(
                    _finding(chunk, "test_only", "every caller found is inside a test file")
                )
            continue

        # No callers at all - genuine orphan candidate. Run it through the
        # confidence checks in order; the first one that fires decides the
        # tier, since the plan's own tiers are ordered buckets, not scores
        # to combine (same "priority chain, not weighted score" reasoning
        # as Phase 3's resolver).
        decorators = get_decorator_names(chunk, project_root)
        matched_decorator = matching_dynamic_decorator(decorators)
        if matched_decorator:
            findings.append(
                _finding(
                    chunk,
                    "possibly_dynamic_usage",
                    f"decorator '@{matched_decorator}' matches a known dynamic-invocation pattern",
                )
            )
            continue

        match_file = _scan_non_python_files(chunk.symbol_name, project_root)
        if match_file is not None:
            findings.append(
                _finding(
                    chunk,
                    "possibly_used_outside_python",
                    f"symbol name also appears in {match_file} - may be invoked dynamically",
                )
            )
            continue

        findings.append(
            _finding(
                chunk,
                "high_confidence_dead",
                "no Python callers, no matching decorator pattern, "
                "no reference found in non-Python files",
            )
        )

    return findings


def _finding(chunk, tier: Tier, reason: str) -> OrphanFinding:
    return OrphanFinding(
        chunk_id=chunk.chunk_id,
        qualified_name=chunk.qualified_name,
        file_path=chunk.file_path,
        kind=chunk.kind,
        start_line=chunk.start_line,
        tier=tier,
        reason=reason,
    )


def _caller_file_path(graph: Graph, caller_id: str) -> str:
    """
    A caller id is either a real chunk_id (look it up in graph.chunks_by_id)
    or a module-level pseudo-id in the form "<module>::some/file.py"
    (see graph.py's module_pseudo_id) - handled here rather than inside
    rules.py, since knowing about the graph's internal id shapes is this
    module's job, not a pure rule's.
    """
    real_chunk = graph.chunks_by_id.get(caller_id)
    if real_chunk is not None:
        return real_chunk.file_path
    if caller_id.startswith("<module>::"):
        return caller_id.split("::", 1)[1]
    return ""  # shouldn't happen, but never crash the report over it


def _scan_non_python_files(symbol_name: str, project_root: Path) -> str | None:
    """
    Cheap, dumb safety net: a whole-word, case-sensitive text search for
    `symbol_name` across the project's non-Python files. Not a parse of
    JSON/HTML/JS structure - just a literal name search, because a
    string-based cross-language reference (a task-queue config, a Jinja
    template call, a JS fetch handled by a Python route) has no OTHER way
    to point at a Python symbol except by containing its exact name as
    text somewhere.

    Returns the first matching file's path (relative to project_root) or
    None. Deliberately kept inline here rather than a separate scanner.py
    module for now - if this needs to grow (e.g. per-extension logic), it
    can be split out later without touching the callers above it.
    """
    for path in project_root.rglob("*"):
        if not path.is_file() or path.suffix not in _CROSS_LANGUAGE_EXTENSIONS:
            continue
        if ".codeguard" in path.parts:  # never search our own working directory
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _whole_word_match(symbol_name, text):
            return str(path.relative_to(project_root))
    return None


def _whole_word_match(name: str, text: str) -> bool:
    """True if `name` appears in `text` as a standalone word - not as a
    substring of a longer identifier (e.g. "save" must not match inside
    "save_all" or "saved_at")."""
    import re

    return re.search(rf"\b{re.escape(name)}\b", text) is not None