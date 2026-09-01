"""
Confidence rules for Phase 4: pure functions, no file I/O and no LanceDB -
same split as graph/resolver.py, which is pure judgment-call logic that
operates on plain data already loaded into memory. Kept separate from
finder.py (which does the orchestration and the on-demand file reads) so
each rule can be unit-tested with hand-built strings and no real project
on disk at all.

Each function here answers exactly one narrow question and returns a
plain bool or reason string - finder.py is the only place that decides
what a "yes" from one of these actually means for the final tier.
"""

from __future__ import annotations

# Decorator names (just the rightmost part, e.g. "route" from "@app.route",
# "task" from "@celery.task") that are well-known to cause a function to be
# invoked by a framework rather than by any direct call CodeGuard's graph
# could ever see. Deliberately a small, hand-picked, hardcoded list rather
# than a user-editable config file for now (see PHASE4_DECISIONS.md,
# Decision A) - matches the project's own "don't build machinery a real
# need hasn't shown up for yet" pattern from earlier phases.
KNOWN_DYNAMIC_DECORATORS = frozenset(
    {
        "route",  # Flask/FastAPI: @app.route(...), @router.get(...)
        "get", "post", "put", "delete", "patch",  # FastAPI/Flask verb decorators
        "task",  # Celery: @celery.task
        "command",  # Typer/Click: @app.command()
        "fixture",  # pytest: @pytest.fixture
        "hookimpl",  # pluggy-based plugin systems
        "app",  # generic catch-all for framework-registered handlers
        "register",  # generic plugin/handler registration pattern
        "abstractmethod",  # never truly "dead" - required by a subclass contract
        "staticmethod",  # not dynamic per se, but frequently called via the
                          # class rather than an instance in ways the resolver
                          # may not catch - safer to flag than assert dead
    }
)

# Path fragments/patterns that mark a file as test-only. Simple substring
# checks are enough here - real project structures for test files are
# consistent enough (tests/, test_*.py, *_test.py) that a regex would just
# be the same three checks wearing extra syntax.
_TEST_PATH_MARKERS = ("tests/", "test_", "_test.py")


def is_test_path(file_path: str) -> bool:
    """True if `file_path` looks like a test file, by simple pattern match
    against common conventions - not a parse, just a string check."""
    normalized = file_path.replace("\\", "/")
    filename = normalized.rsplit("/", 1)[-1]
    return (
        "tests/" in normalized
        or filename.startswith("test_")
        or filename.endswith("_test.py")
    )


def all_callers_are_tests(caller_file_paths: list[str]) -> bool:
    """
    True only if there is at least one caller AND every one of them lives
    in a test file. An empty list returns False deliberately - "zero
    callers" is a different situation (a true orphan candidate) from
    "callers exist, but only in tests", and finder.py is responsible for
    telling those two apart before ever calling this function.
    """
    return bool(caller_file_paths) and all(is_test_path(p) for p in caller_file_paths)


def matching_dynamic_decorator(decorator_names: list[str]) -> str | None:
    """
    Returns the first decorator name (bare, e.g. "route") that matches
    KNOWN_DYNAMIC_DECORATORS, or None if none of them do. Returning the
    matched name (not just True/False) lets finder.py build a specific,
    readable `reason` string instead of a generic one.
    """
    for name in decorator_names:
        bare = name.rsplit(".", 1)[-1]  # "app.route" -> "route"
        if bare in KNOWN_DYNAMIC_DECORATORS:
            return bare
    return None