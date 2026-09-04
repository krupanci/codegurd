"""
BFS is tested by building the Graph's adjacency dicts directly rather
than going through Chunk/Edge/ResolvedEdge and the resolver - BFS only
ever reads `_forward`/`_reverse`, so this exercises exactly the pure
logic in isolation, per the project's own "cheapest to test in isolation"
note.
"""

from codeguard.graph.graph import Graph


def _graph_with_edges(edges: dict[str, list[str]]) -> Graph:
    graph = Graph()
    for source, targets in edges.items():
        for target in targets:
            graph._forward.setdefault(source, set()).add(target)
            graph._reverse.setdefault(target, set()).add(source)
    return graph


def test_walk_forward_finds_direct_and_transitive_targets():
    graph = _graph_with_edges({"a": ["b"], "b": ["c"]})
    assert graph.walk_forward("a") == {"b": 1, "c": 2}


def test_walk_forward_respects_max_hops():
    graph = _graph_with_edges({"a": ["b"], "b": ["c"]})
    assert graph.walk_forward("a", max_hops=1) == {"b": 1}


def test_walk_reverse_finds_callers():
    graph = _graph_with_edges({"a": ["c"], "b": ["c"]})
    assert graph.walk_reverse("c") == {"a": 1, "b": 1}


def test_walk_handles_cycles_without_hanging():
    graph = _graph_with_edges({"a": ["b"], "b": ["a"]})
    assert graph.walk_forward("a") == {"b": 1}


def test_walk_does_not_include_start_node():
    graph = _graph_with_edges({"a": ["b"]})
    assert "a" not in graph.walk_forward("a")


def test_walk_from_isolated_node_returns_empty():
    graph = _graph_with_edges({})
    assert graph.walk_forward("solo") == {}


def test_walk_reverse_with_no_limit_is_full_transitive_closure():
    graph = _graph_with_edges({"a": ["b"], "b": ["c"], "c": ["d"]})
    assert graph.walk_reverse("d", max_hops=None) == {"c": 1, "b": 2, "a": 3}