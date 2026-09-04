"""
The hand-rolled call graph: assembled in memory from the plain chunk/edge
rows persisted in LanceDB during Phases 1-2. No networkx, no graph
library - `Graph` below is nothing more than two plain dicts of sets
(forward and reverse adjacency) plus the traversal logic that walks them.

Nothing here is persisted. `build_graph_from_storage` is meant to be
called once per CLI run - the graph is always rebuilt from the raw rows,
never cached to disk itself (see PHASE3_DECISIONS.md, Decision E and the
project's top-level architecture decision on this).

CHANGE (bugfix): `max_hops` on `walk_forward` / `walk_reverse` is now
optional. It previously had no default, but PHASE1_DECISIONS.md Decision E
explicitly calls for the blast-radius feature to walk `walk_reverse()`
"with no depth limit (full transitive closure)" - there was no way to
express "no limit" before, and impact/analyzer.py was calling it with only
one argument, which raised a TypeError on every run. `max_hops=None` now
means "walk until nothing new is reachable."
"""

from __future__ import annotations

from collections import deque

from codeguard.graph.models import ResolvedEdge
from codeguard.graph.resolver import (
    build_file_index,
    build_import_index,
    build_symbol_index,
    resolve_call_edge,
)
from codeguard.parsing.models import Chunk, Edge
from codeguard.storage.db import Storage


def module_pseudo_id(file_path: str) -> str:
    """
    Synthetic node id standing in for "top-level code in this file", used
    as the source of a call edge when a call happens outside any
    function/class - e.g. the `main()` call inside
    `if __name__ == "__main__": main()`.

    This isn't a real chunk (Phase 1 never creates a chunk for module-level
    code), but Phase 2 still records the edge with `source_chunk_id=None`
    specifically so this case isn't lost. Giving it a stable synthetic id
    here (rather than skipping it) means `main` correctly shows up with a
    real incoming edge in the reverse map, instead of looking like an
    orphan to Phase 4's dead-code finder.
    """
    return f"<module>::{file_path}"


class Graph:
    """
    Two adjacency structures over chunk ids (plus module-level pseudo-ids):

      - `_forward[X]`  = every chunk id that X calls.
      - `_reverse[X]`  = every chunk id that calls X.

    Both are `dict[str, set[str]]` - a set rather than a list, because for
    graph traversal we only care WHETHER an edge exists between two nodes,
    not how many times; de-duplicating multiple calls to the same function
    keeps traversal cheap and avoids double-visiting the same neighbor.
    """

    def __init__(self) -> None:
        self._forward: dict[str, set[str]] = {}
        self._reverse: dict[str, set[str]] = {}
        self.chunks_by_id: dict[str, Chunk] = {}

        # symbol_name -> chunk_id built lazily, on first use, by
        # `find_by_qualified_name`. Avoids a linear scan every time a
        # caller needs to go from a human-readable name (e.g. a CLI
        # `--target-symbol` argument) to the chunk_id the graph itself
        # is keyed on.
        self._by_qualified_name: dict[str, str] | None = None

        # Kept for visibility, not used by traversal itself - useful for
        # a future accuracy report (Phase 9) or just manual inspection.
        self.unresolved: list[ResolvedEdge] = []
        self.ambiguous: list[ResolvedEdge] = []

    def add_chunk(self, chunk: Chunk) -> None:
        self.chunks_by_id[chunk.chunk_id] = chunk
        self._by_qualified_name = None  # invalidate lazy index

    def add_resolved_call(self, resolved: ResolvedEdge) -> None:
        """
        Wire one resolved `calls` edge into the forward/reverse maps.

        If the edge was ambiguous (more than one candidate at the tier
        that matched), an edge is added to EVERY candidate rather than
        picking one - this is deliberately the safer failure mode for a
        dead-code finder: it's better to wrongly mark one of two `save`
        methods as "has a caller" than to wrongly mark one of them dead.
        """
        if resolved.confidence == "unresolved":
            self.unresolved.append(resolved)
            return

        if len(resolved.candidates) > 1:
            self.ambiguous.append(resolved)

        source_id = resolved.edge.source_chunk_id or module_pseudo_id(resolved.edge.file_path)

        for candidate in resolved.candidates:
            self._forward.setdefault(source_id, set()).add(candidate.chunk_id)
            self._reverse.setdefault(candidate.chunk_id, set()).add(source_id)

    def callees(self, chunk_id: str) -> set[str]:
        """Everything `chunk_id` directly calls (one hop out, forward)."""
        return self._forward.get(chunk_id, set())

    def callers(self, chunk_id: str) -> set[str]:
        """Everything that directly calls `chunk_id` (one hop out, reverse)."""
        return self._reverse.get(chunk_id, set())

    def find_by_qualified_name(self, qualified_name: str) -> Chunk | None:
        """
        Look up a single chunk by its human-readable qualified name (e.g.
        "LoginHandler.validate"), for callers that only know a symbol's
        name and not its internal chunk_id - e.g. a CLI flag or an agent
        naming a symbol it's about to touch.

        Built and cached lazily on first call, invalidated whenever a new
        chunk is added, so repeated lookups against the same graph don't
        each re-scan every chunk.
        """
        if self._by_qualified_name is None:
            self._by_qualified_name = {
                chunk.qualified_name: chunk_id
                for chunk_id, chunk in self.chunks_by_id.items()
            }
        chunk_id = self._by_qualified_name.get(qualified_name)
        return self.chunks_by_id.get(chunk_id) if chunk_id else None

    def walk_forward(self, start_id: str, max_hops: int | None = None) -> dict[str, int]:
        """Every chunk reachable by following calls OUTWARD from
        `start_id`, up to `max_hops` steps (or with no limit at all if
        `max_hops` is None). Returns {chunk_id: hop_distance}, not
        including `start_id` itself."""
        return self._bfs(start_id, max_hops, self._forward)

    def walk_reverse(self, start_id: str, max_hops: int | None = None) -> dict[str, int]:
        """Every chunk that can reach `start_id` by calling it (directly or
        transitively), up to `max_hops` steps (or with no limit at all if
        `max_hops` is None - this is what change-impact/blast-radius uses,
        per PHASE1_DECISIONS.md Decision E: full transitive closure).
        Returns {chunk_id: hop_distance}, not including `start_id` itself.
        """
        return self._bfs(start_id, max_hops, self._reverse)

    @staticmethod
    def _bfs(
        start_id: str,
        max_hops: int | None,
        adjacency: dict[str, set[str]],
    ) -> dict[str, int]:
        """
        Plain iterative breadth-first search with an explicit queue and a
        visited set.

        BFS (not DFS) because "N hops outward" is exactly what BFS measures
        for free - everything found at queue-depth 1 really is 1 hop away.
        Iterative (not recursive) because call graphs routinely have
        cycles (mutual recursion, or a function that calls itself) - an
        explicit visited set makes cycles a non-issue, whereas a recursive
        walk would need the same visited-set threaded through every call
        anyway, with the added risk of hitting Python's recursion limit on
        a deep chain. See PHASE3_DECISIONS.md, Decision D.

        `max_hops=None` walks until the whole reachable set has been
        visited (full transitive closure) instead of stopping at a fixed
        depth.
        """
        visited: dict[str, int] = {}
        seen = {start_id}
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if max_hops is not None and depth == max_hops:
                continue
            for neighbor in adjacency.get(current_id, ()):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                visited[neighbor] = depth + 1
                queue.append((neighbor, depth + 1))

        return visited


def build_graph(chunks: list[Chunk], edges: list[Edge]) -> Graph:
    """
    Assemble a Graph from plain, already-loaded chunks/edges. Does no I/O
    itself - this is the part that's easy to unit test with hand-built
    lists and no LanceDB involved at all.

    Only `calls` edges become forward/reverse graph edges. `imports` edges
    are resolved facts too (Phase 2), but they describe a name being
    brought into a file, not a call relationship, so they aren't modeled
    as graph edges here - see PHASE3_DECISIONS.md, Decision A.
    """
    graph = Graph()
    for chunk in chunks:
        graph.add_chunk(chunk)

    file_index = build_file_index(chunks)
    symbol_index = build_symbol_index(chunks)
    import_index = build_import_index(edges)
    known_files = set(file_index.keys())

    for edge in edges:
        if edge.kind != "calls":
            continue
        resolved = resolve_call_edge(edge, symbol_index, file_index, import_index, known_files)
        graph.add_resolved_call(resolved)

    return graph


def build_graph_from_storage(storage: Storage) -> Graph:
    """
    Convenience entry point for real use: pull every chunk/edge row back
    out of LanceDB (Phases 1-2's tables) and build a fresh Graph from them.
    Meant to be called once per CLI run - see `build_graph`'s docstring for
    why the actual graph-building logic lives there instead, decoupled
    from Storage entirely.

    Callers that need the graph for more than one purpose in the same
    logical operation (e.g. context/bundle.py running retrieval + impact +
    dead-code together) should call this ONCE and pass the resulting Graph
    into each engine, rather than each engine calling this again on its
    own - see the `graph=` parameters on `find_relevant_code` and
    `analyze_impact`.
    """
    chunks = [Chunk(**row) for row in storage.all_chunks()]
    edges = [Edge(**row) for row in storage.all_edges()]
    return build_graph(chunks, edges)