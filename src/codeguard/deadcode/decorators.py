"""
On-demand decorator lookup for Phase 4.

Chunk.content (Phase 1) starts at the `function_definition`/`class_definition`
node itself, NOT at a wrapping `decorated_definition` - chunker.py's _walk
only matches on the two definition node types directly, so a decorator like
`@app.route(...)` sitting above a function is never captured anywhere in
the Chunk.

Two ways to fix this were considered:
  (a) change chunker.py / the Chunk schema to also store decorator names.
  (b) look it up here, on demand, by re-reading the source file at report
      time, using the file_path + start_line the Chunk already has.

(b) is what's implemented below - it needs zero changes to the already-
tested Phase 1 schema and LanceDB table, since decorator info is only ever
a Phase-4 judgment call, not a raw fact worth persisting for every chunk.
Same reasoning as Phase 2's raw-fact/judgment-call split.
"""

from __future__ import annotations

from pathlib import Path

from codeguard.parsing.models import Chunk


def get_decorator_names(chunk: Chunk, project_root: Path) -> list[str]:
    """
    Returns the decorator names (e.g. ["app.route"], bare - not called)
    written directly above `chunk` in its source file, or [] if the file
    can't be read or the chunk has none.

    Limitation, stated plainly rather than silently: this only recognizes
    decorators written as plain lines starting with "@" directly above the
    definition, with no blank/comment lines skipped past a certain point.
    A decorator call that spans multiple lines (e.g. a long argument list
    wrapped across several lines) will only have its LAST line detected
    correctly, since we walk upward line-by-line looking for a leading
    "@". This is a deliberate simplification, not a silent gap - Phase 9
    should record it as a known limitation, the same way the plan already
    tracks vulture/PyCG-style blind spots for the rest of Phase 4.
    """
    file_path = project_root / chunk.file_path
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    decorator_lines: list[str] = []
    # chunk.start_line is 1-indexed and points at the def/class line itself,
    # so the line directly above it is list index (start_line - 2).
    line_index = chunk.start_line - 2

    while line_index >= 0:
        stripped = lines[line_index].strip()
        if stripped.startswith("@"):
            decorator_lines.append(stripped)
            line_index -= 1
        elif stripped == "":
            # Blank lines between decorators/def are unusual but harmless -
            # keep looking upward past them.
            line_index -= 1
        else:
            # Hit real code (or the top of the file) - stop, whatever we
            # collected below this point is the full decorator stack.
            break

    decorator_lines.reverse()
    return [_decorator_name(line) for line in decorator_lines]


def _decorator_name(decorator_line: str) -> str:
    """'@app.route("/users")' -> 'app.route'. '@pytest.fixture' -> 'pytest.fixture'."""
    body = decorator_line[1:].strip()  # drop the leading "@"
    return body.split("(", 1)[0].strip()