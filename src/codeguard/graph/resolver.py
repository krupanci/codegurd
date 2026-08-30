"""
Name resolution: turns a raw `calls` Edge's bare `target_name` (Phase 2)
into zero or more actual candidate chunks.

Kept as pure functions with no I/O and no dependency on Storage/LanceDB -
everything here operates on plain lists of Chunk/Edge already loaded into
memory, so it can be unit-tested with hand-built lists and no database
involved at all.

Resolution is a ranked-candidate priority chain, in this order:
  1. same_file       - a chunk with this name in the SAME file as the call.
  2. imported_module  - the call's name matches something this file
                         explicitly imported; look for that name inside
                         the file the import actually points to.
  3. global            - any chunk anywhere in the project with this exact
                         name, tried only if neither tier above found
                         anything.

The first tier that produces ANY match wins - we never mix tiers together.
If that tier produces more than one match, all of them are kept as ranked
candidates rather than guessing a single "best" one (see
PHASE3_DECISIONS.md, Decisions B and C).

Only `calls` edges are resolved here. `imports` edges are still real facts
sitting in LanceDB from Phase 2, but they describe "this file imports that
name" rather than a call relationship, so they aren't fed through this
resolver or into the call graph itself (see PHASE3_DECISIONS.md,
Decision A) - they're only *used* here, as the source of import
information for tier 2 above.
"""

from __future__ import annotations

from dataclasses import dataclass

from codeguard.graph.models import ResolvedCandidate, ResolvedEdge
from codeguard.parsing.models import Chunk, Edge

# A call can target a function, a method (e.g. `self.db.save()`), or a
# class being instantiated (e.g. `LoginHandler()`) - all three are valid
# call targets, so all three are matched here rather than special-cased.
_CALLABLE_KINDS = ("function", "method", "class")


@dataclass(frozen=True)
class _ImportInfo:
    """What a given name, as used inside one file, actually refers to -
    built from that file's own `imports` Edges (Phase 2)."""

    original_name: str   # the name as it exists in the module it came from
    module: str           # e.g. "myapp.auth" for `from myapp.auth import authenticate`


def build_file_index(chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    """file_path -> every chunk defined in that file."""
    index: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        index.setdefault(chunk.file_path, []).append(chunk)
    return index


def build_symbol_index(chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    """symbol_name -> every chunk anywhere in the project with that name.
    Used only as the last-resort "global" tier."""
    index: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        index.setdefault(chunk.symbol_name, []).append(chunk)
    return index


def build_import_index(edges: list[Edge]) -> dict[str, dict[str, _ImportInfo]]:
    """file_path -> {local_alias: _ImportInfo}, built from every `imports`
    Edge. `local_alias` is the name as actually used inside that file -
    e.g. "Opt" for `from typing import Optional as Opt` - which is exactly
    the name a `calls` Edge's `target_name` would use at a call site."""
    index: dict[str, dict[str, _ImportInfo]] = {}
    for edge in edges:
        if edge.kind != "imports":
            continue
        per_file = index.setdefault(edge.file_path, {})
        per_file[edge.local_alias] = _ImportInfo(
            original_name=edge.target_name,
            module=edge.imported_from_module,
        )
    return index


def _guess_file_for_module(module_name: str, known_files: set[str]) -> str | None:
    """
    Turn a dotted module name (e.g. "myapp.auth.handlers") into a project
    file path (e.g. "myapp/auth/handlers.py"), checked against files we
    actually parsed. Returns None for anything that isn't part of this
    project (e.g. stdlib/third-party imports like "os" or "typing") -
    those simply have no chunk to resolve to, which is the correct outcome,
    not a bug.
    """
    if not module_name:
        return None

    as_module_file = module_name.replace(".", "/") + ".py"
    if as_module_file in known_files:
        return as_module_file

    as_package_init = module_name.replace(".", "/") + "/__init__.py"
    if as_package_init in known_files:
        return as_package_init

    # Fallback for projects laid out under a "src/" root or similar, where
    # the dotted module path doesn't line up exactly with the file's path
    # relative to the project root - match on the tail instead.
    for known_file in known_files:
        if known_file == as_module_file or known_file.endswith("/" + as_module_file):
            return known_file

    return None


def _candidates_from(chunks: list[Chunk]) -> tuple[ResolvedCandidate, ...]:
    callable_chunks = [c for c in chunks if c.kind in _CALLABLE_KINDS]
    # Sorted for determinism: re-running the resolver on unchanged input
    # must always produce candidates in the same order, so downstream
    # reports don't flicker between runs for no reason.
    callable_chunks.sort(key=lambda c: c.chunk_id)
    return tuple(
        ResolvedCandidate(
            chunk_id=c.chunk_id,
            qualified_name=c.qualified_name,
            file_path=c.file_path,
        )
        for c in callable_chunks
    )


def resolve_call_edge(
    edge: Edge,
    symbol_index: dict[str, list[Chunk]],
    file_index: dict[str, list[Chunk]],
    import_index: dict[str, dict[str, _ImportInfo]],
    known_files: set[str],
) -> ResolvedEdge:
    """
    Resolve one raw `calls` Edge. Assumes `edge.kind == "calls"` - callers
    are expected to filter to calls edges before invoking this (see
    `build_graph` in graph.py).
    """

    # Tier 1: same file.
    same_file = [
        c for c in file_index.get(edge.file_path, [])
        if c.symbol_name == edge.target_name
    ]
    if same_file:
        return ResolvedEdge(edge, _candidates_from(same_file), "same_file")

    # Tier 2: explicitly imported into this file.
    import_info = import_index.get(edge.file_path, {}).get(edge.target_name)
    if import_info is not None:
        target_file = _guess_file_for_module(import_info.module, known_files)
        if target_file is not None:
            imported = [
                c for c in file_index.get(target_file, [])
                if c.symbol_name == import_info.original_name
            ]
            if imported:
                return ResolvedEdge(edge, _candidates_from(imported), "imported_module")

    # Tier 3: any exact name match anywhere in the project.
    global_matches = symbol_index.get(edge.target_name, [])
    if global_matches:
        return ResolvedEdge(edge, _candidates_from(global_matches), "global")

    # Nothing matched at all - e.g. a call to a builtin like `len()` or
    # `print()`, or to a third-party library function. Not an error - just
    # nothing in this project for the graph to point at.
    return ResolvedEdge(edge, (), "unresolved")