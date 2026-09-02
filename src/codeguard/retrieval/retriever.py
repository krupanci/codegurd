"""
Phase 7 - Scoped context retrieval.

Given a plain-English query, returns a small, ranked, purposeful bundle of
code - not a flat top-K semantic search, and not a flat "everything within
N hops" graph dump. Three signals are combined:

  1. Semantic search (Phase 6)  - finds the chunk that best matches what
     the query is actually ABOUT, by meaning rather than keyword overlap.
  2. Graph walk (Phase 3)        - from that entry point, follows the SAME
     forward/reverse adjacency maps the dead-code finder and the
     change-impact analyzer already use, to find what it calls (likely
     root cause) and what calls it (blast radius - literally the same
     reverse walk Phase 5 runs for change-impact).
  3. Impact-aware ranking          - merges both signals into one score per
     chunk, weighted differently depending on what KIND of question is
     being asked (see query_intent.py):
       - a bug-report-style query trusts the graph more, and specifically
         weights callers highly, because "who breaks if I touch this" is
         exactly what matters when debugging;
       - an exploratory query trusts semantic breadth more, and treats
         both directions of the graph roughly evenly, because the goal is
         understanding an area, not tracing a single failure.

This single function covers both passes from the implementation plan:
Pass 1 is the semantic-seed-plus-N-hop-walk below with no filtering;
Pass 2 is that same walk with the hop-cutoff/weighting rules layered on
top - kept as one function rather than two, since Pass 2 only adds scoring
and filtering on top of exactly the same walk Pass 1 already does.
"""

from __future__ import annotations

from codeguard.embedding.embedder import Embedder, get_default_embedder
from codeguard.graph.graph import build_graph_from_storage
from codeguard.retrieval.models import RelationType, RetrievalResult, RetrievedChunk
from codeguard.retrieval.query_intent import QueryIntent, classify_intent
from codeguard.storage.db import Storage

# How many semantic candidates to pull before any graph reasoning happens.
# Kept small on purpose - this is a shortlist, not the final bundle.
_SEMANTIC_CANDIDATES = 5

# How many hops the graph walk explores outward, in each direction, before
# stopping entirely (a hard ceiling regardless of intent).
_MAX_HOPS = 2

# Per-intent tuning:
#   graph_hop_cutoff  - hops beyond this are only kept if they ALSO showed
#                        up in the semantic shortlist (i.e. they need a
#                        second reason to be included, not just distance).
#   semantic_weight / graph_weight - how much each signal counts in the
#                        final ranking score (they sum to 1.0).
_INTENT_PROFILES: dict[QueryIntent, dict[str, float]] = {
    "bug_fix": {"graph_hop_cutoff": 1, "semantic_weight": 0.45, "graph_weight": 0.55},
    "explore": {"graph_hop_cutoff": 2, "semantic_weight": 0.65, "graph_weight": 0.35},
}

# Base importance of a relation type, before hop-distance decay. Callers
# are weighted highest under bug_fix specifically because a caller is
# exactly "what breaks if this changes" - the same idea Phase 5's blast
# radius report is built around, just reused here as a ranking signal
# instead of a report.
_RELATION_WEIGHT: dict[QueryIntent, dict[RelationType, float]] = {
    "bug_fix": {"entry_point": 1.0, "caller": 0.95, "dependency": 0.80, "related": 0.50},
    "explore": {"entry_point": 1.0, "caller": 0.75, "dependency": 0.75, "related": 0.60},
}

_DEFAULT_MAX_RESULTS = 8


def find_relevant_code(
    query: str,
    storage: Storage,
    embedder: Embedder | None = None,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> RetrievalResult:
    """
    Given a plain-English problem description, return a small ranked
    bundle of chunks, each with a one-line reason it was included.

    Returns an empty result if the project hasn't been indexed yet (no
    chunks with vectors in storage) - see codeguard.indexing.index_project.
    """
    embedder = embedder or get_default_embedder()
    intent = classify_intent(query)
    profile = _INTENT_PROFILES[intent]

    # Step 1: semantic search - find the entry point (Phase 6).
    query_vector = embedder.embed_query(query)
    semantic_hits = storage.semantic_search(query_vector, limit=_SEMANTIC_CANDIDATES)
    if not semantic_hits:
        return RetrievalResult(query=query, entry_point_chunk_id=None, chunks=[])

    entry_id = semantic_hits[0]["chunk_id"]
    semantic_scores = {row["chunk_id"]: _similarity_from_distance(row) for row in semantic_hits}

    # Step 2: graph walk - who does the entry point depend on, who depends
    # on it (Phase 3's forward/reverse BFS, rebuilt fresh from storage).
    graph = build_graph_from_storage(storage)
    chunks_by_id = graph.chunks_by_id
    dependencies = graph.walk_forward(entry_id, _MAX_HOPS)  # what it calls
    callers = graph.walk_reverse(entry_id, _MAX_HOPS)        # what calls it

    # Step 3: impact-aware ranking - merge everything into one scored bundle.
    candidates: dict[str, RetrievedChunk] = {}

    _consider(
        candidates, chunks_by_id, entry_id,
        relation="entry_point", hop_distance=0,
        semantic_score=semantic_scores.get(entry_id, 1.0),
        intent=intent, profile=profile,
        reason="Best semantic match for the query.",
    )

    for chunk_id, hop in callers.items():
        if hop > profile["graph_hop_cutoff"] and chunk_id not in semantic_scores:
            continue  # too far out, and nothing semantic backs it up - skip
        _consider(
            candidates, chunks_by_id, chunk_id,
            relation="caller", hop_distance=hop,
            semantic_score=semantic_scores.get(chunk_id, 0.0),
            intent=intent, profile=profile,
            reason=_caller_reason(hop),
        )

    for chunk_id, hop in dependencies.items():
        if hop > profile["graph_hop_cutoff"] and chunk_id not in semantic_scores:
            continue
        _consider(
            candidates, chunks_by_id, chunk_id,
            relation="dependency", hop_distance=hop,
            semantic_score=semantic_scores.get(chunk_id, 0.0),
            intent=intent, profile=profile,
            reason=_dependency_reason(hop),
        )

    for chunk_id, score in semantic_scores.items():
        if chunk_id in candidates or chunk_id == entry_id:
            continue
        _consider(
            candidates, chunks_by_id, chunk_id,
            relation="related", hop_distance=-1,
            semantic_score=score,
            intent=intent, profile=profile,
            reason="Semantically similar to the query, but not connected to "
                   "the entry point in the call graph.",
        )

    # Step 4: rank and trim - entry point always first, rest sorted by score.
    ranked = sorted(candidates.values(), key=lambda c: c.final_score, reverse=True)
    entry = next(c for c in ranked if c.chunk_id == entry_id)
    rest = [c for c in ranked if c.chunk_id != entry_id][: max(0, max_results - 1)]

    return RetrievalResult(query=query, entry_point_chunk_id=entry_id, chunks=[entry, *rest])


def _consider(
    candidates: dict[str, RetrievedChunk],
    chunks_by_id: dict,
    chunk_id: str,
    *,
    relation: RelationType,
    hop_distance: int,
    semantic_score: float,
    intent: QueryIntent,
    profile: dict[str, float],
    reason: str,
) -> None:
    """Score one candidate chunk and keep it only if it beats whatever is
    already in `candidates` for that chunk_id (a chunk can be reachable
    both as a caller AND a dependency in a graph with cycles - keep
    whichever path scored it higher)."""
    chunk = chunks_by_id.get(chunk_id)
    if chunk is None:
        return  # e.g. the "<module>::file" pseudo-node - nothing to show

    relation_weight = _RELATION_WEIGHT[intent][relation]
    hop_for_decay = max(hop_distance, 0)
    graph_score = relation_weight / (1 + hop_for_decay)
    final_score = (
        profile["semantic_weight"] * semantic_score
        + profile["graph_weight"] * graph_score
    )

    existing = candidates.get(chunk_id)
    if existing is not None and existing.final_score >= final_score:
        return

    candidates[chunk_id] = RetrievedChunk(
        chunk_id=chunk_id,
        file_path=chunk.file_path,
        qualified_name=chunk.qualified_name,
        kind=chunk.kind,
        content=chunk.content,
        relation=relation,
        hop_distance=hop_distance,
        semantic_score=semantic_score,
        graph_score=graph_score,
        final_score=final_score,
        reason=reason,
    )


def _similarity_from_distance(row: dict) -> float:
    """LanceDB's `.metric("cosine")` search adds a `_distance` field (0 =
    identical meaning, 2 = opposite meaning). Convert to a 0..1 similarity
    score so it mixes cleanly with graph_score in `_consider` above."""
    distance = row.get("_distance", 0.0)
    return max(0.0, 1.0 - (distance / 2.0))


def _caller_reason(hop: int) -> str:
    if hop == 1:
        return "Directly calls the entry point - will be affected by any change there."
    return f"Reaches the entry point through {hop} calls - part of its wider blast radius."


def _dependency_reason(hop: int) -> str:
    if hop == 1:
        return "Directly called by the entry point - a likely place the root cause lives."
    return f"{hop} calls deep from the entry point - a more distant dependency."