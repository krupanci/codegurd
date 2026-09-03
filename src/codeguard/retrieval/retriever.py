"""
Phase 7 — Scoped context retrieval.

Given a plain-English query, returns a small, ranked, purposeful bundle of
code — not a flat top-K semantic search, and not a flat "everything within
N hops" graph dump. Two signals are combined:

  1. Semantic search (Phase 6)  — finds the chunk that best matches what
     the query is actually ABOUT, by meaning rather than keyword overlap.
  2. Graph walk (Phase 3)        — from that entry point, follows the SAME
     forward/reverse adjacency maps the dead-code finder and the
     change-impact analyzer already use.

How much to trust each signal is decided per query by `_derive_profile`,
using numbers measured directly from this query's own results — how
decisively the top semantic hit stands out, and how many direct neighbors
the entry point has in the graph. This replaces an earlier version that
classified the query's wording into a fixed "bug_fix" / "explore" bucket
and looked weights up in a table. That approach needed a new hand-picked
keyword list every time a new KIND of question showed up, and a wrong
guess on unlisted phrasing silently fell back to a default. This version
has no buckets to run out of: any query, in any wording, gets a blend
computed from what its own retrieval actually found — accurate and
query-driven, with nothing to hardcode as new question types appear.
"""

from __future__ import annotations

from codeguard.embedding.embedder import Embedder, get_default_embedder
from codeguard.graph.graph import build_graph_from_storage
from codeguard.retrieval.models import RelationType, RetrievalResult, RetrievedChunk
from codeguard.storage.db import Storage

# How many semantic candidates to pull before any graph reasoning happens.
# Kept small on purpose — this is a shortlist, not the final bundle.
_SEMANTIC_CANDIDATES = 5

# How many hops the graph walk explores outward, in each direction, before
# stopping entirely (a hard ceiling regardless of what the query looks like).
_MAX_HOPS = 2

_DEFAULT_MAX_RESULTS = 8

# Baseline relation weights before any per-query adjustment. Every query
# starts here; _derive_profile below adjusts them using signals measured
# from THIS query's own results — never from matching the query's wording
# against a keyword list.
_BASE_RELATION_WEIGHT: dict[RelationType, float] = {
    "entry_point": 1.0,
    "caller": 0.80,
    "dependency": 0.75,
    "related": 0.55,
}


def find_relevant_code(
    query: str,
    storage: Storage,
    embedder: Embedder | None = None,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> RetrievalResult:
    """
    Given a plain-English problem description, return a small ranked
    bundle of chunks, each with a one-line reason it was included.

    Returns an empty result if the project hasn't been indexed yet.
    """
    embedder = embedder or get_default_embedder()

    # Step 1: semantic search — find the entry point (Phase 6).
    query_vector = embedder.embed_query(query)
    semantic_hits = storage.semantic_search(query_vector, limit=_SEMANTIC_CANDIDATES)
    if not semantic_hits:
        return RetrievalResult(query=query, entry_point_chunk_id=None, chunks=[])

    entry_id = semantic_hits[0]["chunk_id"]
    semantic_scores = {row["chunk_id"]: _similarity_from_distance(row) for row in semantic_hits}

    # Step 2: graph walk — who does the entry point depend on, who depends
    # on it (Phase 3's forward/reverse BFS, rebuilt fresh from storage).
    graph = build_graph_from_storage(storage)
    chunks_by_id = graph.chunks_by_id
    dependencies = graph.walk_forward(entry_id, _MAX_HOPS)  # what it calls
    callers = graph.walk_reverse(entry_id, _MAX_HOPS)        # what calls it

    # Step 2.5: derive this query's own blend of semantic vs. graph signal,
    # from what steps 1 and 2 actually found.
    profile = _derive_profile(semantic_scores, callers, dependencies)

    # Step 3: impact-aware ranking — merge everything into one scored bundle.
    candidates: dict[str, RetrievedChunk] = {}

    _consider(
        candidates, chunks_by_id, entry_id,
        relation="entry_point", hop_distance=0,
        semantic_score=semantic_scores.get(entry_id, 1.0),
        profile=profile,
        reason="Best semantic match for the query.",
    )

    for chunk_id, hop in callers.items():
        if hop > profile["graph_hop_cutoff"] and chunk_id not in semantic_scores:
            continue  # too far out, and nothing semantic backs it up — skip
        _consider(
            candidates, chunks_by_id, chunk_id,
            relation="caller", hop_distance=hop,
            semantic_score=semantic_scores.get(chunk_id, 0.0),
            profile=profile,
            reason=_caller_reason(hop),
        )

    for chunk_id, hop in dependencies.items():
        if hop > profile["graph_hop_cutoff"] and chunk_id not in semantic_scores:
            continue
        _consider(
            candidates, chunks_by_id, chunk_id,
            relation="dependency", hop_distance=hop,
            semantic_score=semantic_scores.get(chunk_id, 0.0),
            profile=profile,
            reason=_dependency_reason(hop),
        )

    for chunk_id, score in semantic_scores.items():
        if chunk_id in candidates or chunk_id == entry_id:
            continue
        _consider(
            candidates, chunks_by_id, chunk_id,
            relation="related", hop_distance=-1,
            semantic_score=score,
            profile=profile,
            reason="Semantically similar to the query, but not connected to "
                   "the entry point in the call graph.",
        )

    # Step 4: rank and trim — entry point always first, rest sorted by score.
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
    profile: dict,
    reason: str,
) -> None:
    """Score one candidate chunk and keep it only if it beats whatever is
    already in `candidates` for that chunk_id."""
    chunk = chunks_by_id.get(chunk_id)
    if chunk is None:
        return  # e.g. the "<module>::file" pseudo-node — nothing to show

    relation_weight = profile["relation_weight"][relation]
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


def _semantic_confidence(semantic_scores: dict[str, float]) -> float:
    """
    How decisively the top semantic hit stands out from the rest of the
    shortlist, for THIS query. A big margin between best and second-best
    means the query pointed at one clear place in the codebase; a small
    margin means several chunks read as equally relevant, so graph
    structure should be trusted more to break the tie. Computed fresh
    from this query's own results every time — nothing here is hardcoded
    or needs extending as new kinds of questions show up, because it
    never looks at the query's wording at all.
    """
    scores = sorted(semantic_scores.values(), reverse=True)
    if len(scores) < 2:
        return 1.0
    margin = scores[0] - scores[1]
    return max(0.0, min(1.0, margin * 2))


def _graph_density(callers: dict[str, int], dependencies: dict[str, int]) -> float:
    """
    How structurally busy the entry point is, measured directly from this
    query's own graph walk. Saturates at 1.0 around six direct neighbors.
    """
    direct = sum(1 for hop in callers.values() if hop == 1)
    direct += sum(1 for hop in dependencies.values() if hop == 1)
    return max(0.0, min(1.0, direct / 6.0))


def _derive_profile(
    semantic_scores: dict[str, float],
    callers: dict[str, int],
    dependencies: dict[str, int],
) -> dict:
    """
    Builds this query's semantic/graph blend and per-relation weights
    directly from signals measured in its own results, instead of
    classifying the query into a fixed set of hardcoded "intents".
    """
    confidence = _semantic_confidence(semantic_scores)
    density = _graph_density(callers, dependencies)

    # Confidently-matched, sparse area: trust meaning most. Busy,
    # heavily-connected area: trust structure more — "who else touches
    # this" is doing more of the real work of the answer.
    semantic_weight = 0.65 - (0.25 * density) + (0.10 * (confidence - 0.5))
    semantic_weight = max(0.35, min(0.75, semantic_weight))
    graph_weight = 1.0 - semantic_weight

    relation_weight = dict(_BASE_RELATION_WEIGHT)
    # Busier code -> a caller breaking is more consequential -> weight it
    # up, scaled continuously by how busy THIS entry point actually is.
    relation_weight["caller"] = min(1.0, _BASE_RELATION_WEIGHT["caller"] + 0.20 * density)

    # Sparse/ambiguous matches may roam a little further (2 hops); a
    # dense, confidently-matched area stays tight (1 hop) so the bundle
    # doesn't balloon with only-loosely-relevant neighbors.
    graph_hop_cutoff = 1 if density > 0.5 else 2

    return {
        "semantic_weight": semantic_weight,
        "graph_weight": graph_weight,
        "graph_hop_cutoff": graph_hop_cutoff,
        "relation_weight": relation_weight,
    }


def _similarity_from_distance(row: dict) -> float:
    """LanceDB's `.metric("cosine")` search adds a `_distance` field (0 =
    identical meaning, 2 = opposite meaning). Convert to a 0..1 similarity
    score so it mixes cleanly with graph_score in `_consider` above."""
    distance = row.get("_distance", 0.0)
    return max(0.0, 1.0 - (distance / 2.0))


def _caller_reason(hop: int) -> str:
    if hop == 1:
        return "Directly calls the entry point — will be affected by any change there."
    return f"Reaches the entry point through {hop} calls — part of its wider blast radius."


def _dependency_reason(hop: int) -> str:
    if hop == 1:
        return "Directly called by the entry point — a likely place the root cause lives."
    return f"{hop} calls deep from the entry point — a more distant dependency."