"""
Whole-repo orientation: ranks symbols by structural importance, using a
small hand-rolled PageRank-style pass directly over the existing call
graph (`graph/graph.py`) - no new dependency, matching this project's
existing "we hand-roll traversal ourselves" convention (BFS in graph.py,
the tiered resolver in graph/resolver.py).

A symbol is "important" here in the PageRank sense: it's called by other
important symbols. A one-off leaf utility scores low even if it exists;
a function called from many places - especially places that are
themselves widely called - rises to the top. This is a purely structural
signal, with no name/keyword heuristics to hardcode or keep in sync with
a project's own vocabulary.
"""

from __future__ import annotations

from codeguard.graph.graph import Graph
from codeguard.overview.models import RepoMapItem, RepoMapResult

# A repo map is about things you could actually go read - not every node
# the graph tracks internally (module-level pseudo-nodes are excluded
# from the *results*, though their outgoing calls still count towards
# the score of whatever they call - see `_pagerank` below).
_CALLABLE_KINDS = ("function", "method", "class")

_DAMPING = 0.85
_ITERATIONS = 20


def rank_symbols(
    graph: Graph, budget: int = 12, file_path: str | None = None
) -> RepoMapResult:
    """
    Rank symbols by structural importance and return the top `budget`.

    `file_path`, if given, scopes the candidate set to that file's own
    chunks plus their direct callers/callees - "what matters around this
    one file" rather than the whole project.
    """
    scores = _pagerank(graph)
    candidate_ids = _candidate_ids(graph, file_path)

    ranked_ids = sorted(
        (chunk_id for chunk_id in candidate_ids if chunk_id in scores),
        key=lambda chunk_id: scores[chunk_id],
        reverse=True,
    )

    items: list[RepoMapItem] = []
    for chunk_id in ranked_ids:
        chunk = graph.chunks_by_id.get(chunk_id)
        if chunk is None or chunk.kind not in _CALLABLE_KINDS:
            continue
        items.append(
            RepoMapItem(
                qualified_name=chunk.qualified_name,
                file_path=chunk.file_path,
                kind=chunk.kind,
                importance_score=scores[chunk_id],
            )
        )
        if len(items) == budget:
            break

    return RepoMapResult(items=items, budget=budget)


def _candidate_ids(graph: Graph, file_path: str | None) -> set[str]:
    """Every chunk id eligible for the result, before importance
    ranking and the callable-kind filter are applied."""
    if file_path is None:
        return set(graph.chunks_by_id.keys())

    candidates = {
        chunk_id
        for chunk_id, chunk in graph.chunks_by_id.items()
        if chunk.file_path == file_path
    }
    for chunk_id in list(candidates):
        candidates |= graph.callers(chunk_id)
        candidates |= graph.callees(chunk_id)
    return candidates


def _pagerank(graph: Graph) -> dict[str, float]:
    """
    One hand-rolled PageRank pass over every node the graph knows about -
    real chunks AND module-level pseudo-nodes (`<module>::file.py`). A
    pseudo-node's outgoing calls (e.g. a top-level `main()` call) are
    real rank-flow that the real function it calls should benefit from,
    even though the pseudo-node is never itself returned as a result
    (`rank_symbols` filters to `_CALLABLE_KINDS` above).

        importance[x] = (1 - d) + d * sum(
            importance[caller] / out_degree(caller)
            for caller in graph.callers(x)
        )
    """
    node_ids = set(graph.chunks_by_id.keys())
    for chunk_id in list(node_ids):
        node_ids |= graph.callers(chunk_id)
        node_ids |= graph.callees(chunk_id)

    if not node_ids:
        return {}

    base = 1.0 - _DAMPING
    importance = {node_id: 1.0 for node_id in node_ids}

    for _ in range(_ITERATIONS):
        next_importance = {}
        for node_id in node_ids:
            inflow = sum(
                importance[caller] / len(graph.callees(caller))
                for caller in graph.callers(node_id)
            )
            next_importance[node_id] = base + _DAMPING * inflow
        importance = next_importance

    return importance