"""
Single entry point for "I'm about to do X, what do I need to know?".

Which engines run is decided from what the caller actually gave us — a
target symbol, a git ref to diff against — never from guessing the KIND
of question out of the wording of `task`. That keeps this router free of
a hardcoded, ever-growing list of "intents": a caller that has a diff
says so explicitly (`ref=...`) and gets a deterministic blast-radius
answer; a caller that only has a plain-English description gets the same
adaptive semantic+graph retrieval regardless of what that description
happens to say. Retrieval itself already generalizes to any free-text
task with no keyword list to maintain (see retriever.py's adaptive
profile), so it is always run and never bypassed in favor of a guessed
category.
"""

from __future__ import annotations

from pathlib import Path

from codeguard.context.models import ContextBundle, ContextItem, ContextRequest
from codeguard.deadcode.finder import find_dead_code
from codeguard.graph.graph import build_graph_from_storage
from codeguard.impact.analyzer import analyze_impact
from codeguard.retrieval.retriever import find_relevant_code
from codeguard.storage.db import Storage


def get_context(request: ContextRequest, project_root: Path, storage: Storage) -> ContextBundle:
    engines_used: list[str] = []
    items: list[ContextItem] = []

    # Always run: handles any free-text task, adapts itself per query
    # (see retriever._derive_profile) — no fixed category needed here.
    retrieval_result = find_relevant_code(
        request.task, storage, max_results=request.max_results
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
            )
        )

    # Only runs when the caller actually has a diff to compare — a real
    # signal, not a guess. Deterministic: either there's a ref or there isn't.
    if request.ref:
        impact_report = analyze_impact(project_root, storage, request.ref)
        engines_used.append("impact")
        graph = build_graph_from_storage(storage)
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
                    )
                )

    # Only runs when the caller names a specific symbol it's considering
    # touching or removing — again a real signal, not text-guessed.
    if request.target_symbol:
        graph = build_graph_from_storage(storage)
        findings = find_dead_code(graph, project_root)
        engines_used.append("deadcode")
        for finding in findings:
            if finding.qualified_name != request.target_symbol:
                continue
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
                )
            )

    return ContextBundle(task=request.task, items=items, engines_used=engines_used)