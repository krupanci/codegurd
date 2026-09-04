"""
Single entry point for "I'm about to do X, what do I need to know?".

Which engines run is decided from what the caller actually gave us — a
target symbol, a git ref to diff against, an explicit request for
whole-repo orientation — never from guessing the KIND of question out of
the wording of `task`. That keeps this router free of a hardcoded,
ever-growing list of "intents": a caller that has a diff says so
explicitly (`ref=...`) and gets a deterministic blast-radius answer; a
caller that only has a plain-English description gets the same adaptive
semantic+graph retrieval regardless of what that description happens to
say. Retrieval itself already generalizes to any free-text task with no
keyword list to maintain (see retriever.py's adaptive profile), so it is
always run and never bypassed in favor of a guessed category.

CHANGE (perf/dedup): this function used to call `build_graph_from_storage`
up to three times in a single call — once implicitly inside
`find_relevant_code`, once explicitly for the impact branch, and once
explicitly for the dead-code branch — even though all three read the
exact same, unchanged LanceDB rows. The graph is now built once, up
front, and passed into every engine that needs it.

CHANGE (perf/dedup): the `target_symbol` branch used to call
`find_dead_code`, which classifies EVERY callable chunk in the whole
project (including, for genuine orphan candidates, a full rglob scan over
every non-Python file), just to throw away every result except the one
matching `target_symbol`. It now calls `deadcode.finder.check_symbol`,
which runs the identical classification logic against only the one named
chunk.

CHANGE (purpose fix): every item now carries any persisted notes for its
symbol (see storage/db.py's annotations table), and the combined bundle
is run through the same token-budget cutoff retriever.py uses (see
token_budget.py) before being returned — previously this was the one
place downstream consumers (CLI today, MCP later) could receive an
unbounded amount of raw code with no cap at all.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from codeguard.context.models import ContextBundle, ContextItem, ContextRequest
from codeguard.deadcode.finder import check_symbol
from codeguard.graph.graph import build_graph_from_storage
from codeguard.impact.analyzer import analyze_impact
from codeguard.overview.ranker import rank_symbols
from codeguard.retrieval.retriever import find_relevant_code
from codeguard.storage.db import Storage
from codeguard.token_budget import fit_to_budget


def get_context(request: ContextRequest, project_root: Path, storage: Storage) -> ContextBundle:
    engines_used: list[str] = []
    items: list[ContextItem] = []

    # Built once, up front, and reused by every engine below that needs a
    # graph — none of retrieval, impact, dead-code checking, or overview
    # touches the underlying LanceDB rows differently, so there's no
    # reason to rebuild it per engine within a single get_context() call.
    graph = build_graph_from_storage(storage)

    # Always run: handles any free-text task, adapts itself per query
    # (see retriever._derive_profile) — no fixed category needed here.
    retrieval_result = find_relevant_code(
        request.task,
        storage,
        max_results=request.max_results,
        graph=graph,
        max_tokens=request.max_tokens,
    )
    engines_used.append("retrieval")
    for chunk in retrieval_result.chunks:
        items.append(
            ContextItem(
                chunk_id=chunk.chunk_id,
                file_path=chunk.file_path,
                qualified_name=chunk.qualified_name,
                kind=chunk.kind,
                content=chunk.content,
                source="retrieval",
                relation=chunk.relation,
                hop_distance=chunk.hop_distance,
                confidence=None,
                score=chunk.final_score,
                reason=chunk.reason,
                notes=_notes_for(chunk.chunk_id, storage),
            )
        )

    # Only runs when the caller actually has a diff to compare — a real
    # signal, not a guess. Deterministic: either there's a ref or there isn't.
    if request.ref:
        impact_report = analyze_impact(project_root, storage, request.ref, graph=graph)
        engines_used.append("impact")
        for node in impact_report.nodes:
            changed = node.changed_symbol
            for affected_id, hop in node.affected.items():
                affected_chunk = graph.chunks_by_id.get(affected_id)
                if affected_chunk is None:
                    continue
                items.append(
                    ContextItem(
                        chunk_id=affected_chunk.chunk_id,
                        file_path=affected_chunk.file_path,
                        qualified_name=affected_chunk.qualified_name,
                        kind=affected_chunk.kind,
                        content=affected_chunk.content,
                        source="impact",
                        relation="blast_radius",
                        hop_distance=hop,
                        confidence=None,
                        score=1.0 / (1 + hop),
                        reason=(
                            f"Calls {changed.qualified_name}, whose signature "
                            f"was {changed.change_type} — will need updating."
                        ),
                        notes=_notes_for(affected_chunk.chunk_id, storage),
                    )
                )

    # Only runs when the caller names a specific symbol it's considering
    # touching or removing — again a real signal, not text-guessed. Checks
    # only that one symbol (see module docstring) instead of scanning the
    # whole project and filtering down to it afterward.
    if request.target_symbol:
        engines_used.append("deadcode")
        finding = check_symbol(graph, project_root, request.target_symbol)
        if finding is not None:
            chunk = graph.chunks_by_id.get(finding.chunk_id)
            items.append(
                ContextItem(
                    chunk_id=finding.chunk_id,
                    file_path=finding.file_path,
                    qualified_name=finding.qualified_name,
                    kind=finding.kind,
                    content=chunk.content if chunk else "",
                    source="deadcode",
                    relation="dead_code_candidate",
                    hop_distance=0,
                    confidence=finding.tier,
                    score=0.0,
                    reason=finding.reason,
                    notes=_notes_for(finding.chunk_id, storage),
                )
            )

    # Only runs when the caller explicitly asks for whole-repo
    # orientation — again a real signal, not a guess. Answers "where do I
    # even start" independently of whatever `task` says, since a task
    # description isn't a substitute for actually naming this intent.
    if request.orient:
        engines_used.append("overview")
        repo_map = rank_symbols(graph, budget=request.max_results)
        for rank, map_item in enumerate(repo_map.items, start=1):
            chunk = graph.find_by_qualified_name(map_item.qualified_name)
            if chunk is None:
                continue
            items.append(
                ContextItem(
                    chunk_id=chunk.chunk_id,
                    file_path=map_item.file_path,
                    qualified_name=map_item.qualified_name,
                    kind=map_item.kind,
                    content=chunk.content,
                    source="overview",
                    relation="structurally_important",
                    hop_distance=-1,
                    confidence=None,
                    score=map_item.importance_score,
                    reason=f"#{rank} most structurally important symbol in the project.",
                    notes=_notes_for(chunk.chunk_id, storage),
                )
            )

    # This is the single place every downstream consumer (CLI today, MCP
    # later) goes through — enforcing the budget here protects both,
    # regardless of which engines above actually ran. Items are kept in
    # the order they were collected (retrieval, then impact, then
    # dead-code, then overview) rather than re-sorted by score, since
    # that grouping is itself meaningful to a reader of the report.
    items = fit_to_budget(
        items,
        request.max_tokens,
        content_of=lambda item: item.content,
        replace_content=lambda item, new_content: replace(item, content=new_content),
    )

    return ContextBundle(task=request.task, items=items, engines_used=engines_used)


def _notes_for(chunk_id: str, storage: Storage) -> list[str]:
    """Persisted notes for one symbol (see storage/db.py's annotations
    table and `codeguard note`) — a cheap lookup, not a new engine."""
    return [row["note"] for row in storage.get_annotations_for_chunk(chunk_id)]