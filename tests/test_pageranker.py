from codeguard.graph.graph import Graph
from codeguard.overview.ranker import rank_symbols
from codeguard.parsing.models import Chunk


def _chunk(chunk_id: str, qualified_name: str, file_path: str = "a.py") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        file_path=file_path,
        symbol_name=qualified_name,
        qualified_name=qualified_name,
        kind="function",
        start_line=1,
        end_line=1,
        content="",
        blob_hash="deadbeef",
    )


def _graph_with_edges(chunks, edges: dict[str, list[str]]) -> Graph:
    graph = Graph()
    for chunk in chunks:
        graph.add_chunk(chunk)
    for source, targets in edges.items():
        for target in targets:
            graph._forward.setdefault(source, set()).add(target)
            graph._reverse.setdefault(target, set()).add(source)
    return graph


def test_rank_symbols_ranks_widely_called_symbol_highest():
    chunks = [_chunk("a", "a"), _chunk("b", "b"), _chunk("popular", "popular")]
    graph = _graph_with_edges(chunks, {"a": ["popular"], "b": ["popular"]})

    result = rank_symbols(graph, budget=3)

    assert result.items[0].qualified_name == "popular"


def test_rank_symbols_respects_budget():
    chunks = [_chunk(str(i), str(i)) for i in range(5)]
    graph = _graph_with_edges(chunks, {})

    result = rank_symbols(graph, budget=2)

    assert len(result.items) == 2


def test_rank_symbols_file_scoping_includes_direct_neighbors_only():
    a = _chunk("a", "a", file_path="x.py")
    b = _chunk("b", "b", file_path="y.py")
    c = _chunk("c", "c", file_path="z.py")
    graph = _graph_with_edges([a, b, c], {"a": ["b"], "b": ["c"]})

    result = rank_symbols(graph, budget=10, file_path="x.py")

    names = {item.qualified_name for item in result.items}
    assert names == {"a", "b"}  # b is a's direct callee; c is 2 hops away


def test_rank_symbols_excludes_non_callable_kinds():
    chunks = [_chunk("a", "a")]
    graph = _graph_with_edges(chunks, {})
    result = rank_symbols(graph, budget=10)
    assert all(item.kind in ("function", "method", "class") for item in result.items)