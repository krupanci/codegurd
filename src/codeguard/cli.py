"""
Phase 8 - Unified CLI.

Ties all three features behind one consistent command-line tool:

    codeguard scan               -> dead code report            (Phase 4)
    codeguard impact <ref>        -> change-impact / blast radius (Phase 5)
    codeguard find "<query>"       -> scoped context retrieval     (Phase 7)

Every command shares the same two steps before doing its own thing:
  1. `_load_project`  - resolve the project root + open Storage, using the
     one settings object (config.py) instead of assuming `Path.cwd()` is
     always the project root.
  2. `_ensure_indexed` - bring LanceDB up to date with whatever's on disk
     right now, via Phase 6's `index_project`. This is the "staleness/
     re-indexing logic" the implementation plan calls for in this phase -
     it already existed as of Phase 6 (blob-hash comparison, skip
     unchanged files); Phase 8's job is just to make sure every command
     actually calls it first, instead of trusting stale data.
"""

from __future__ import annotations

from pathlib import Path

import typer

from codeguard.config import CodeGuardSettings
from codeguard.deadcode.finder import find_dead_code
from codeguard.deadcode.report import render_deadcode_report
from codeguard.graph.graph import build_graph_from_storage
from codeguard.impact.analyzer import analyze_impact
from codeguard.impact.report import render_report
from codeguard.indexing import index_project
from codeguard.retrieval.report import render_retrieval_result
from codeguard.retrieval.retriever import find_relevant_code
from codeguard.storage.db import Storage
from codeguard.context.bundle import get_context
from codeguard.context.models import ContextRequest
from codeguard.context.report import render_context_bundle

app = typer.Typer()


def _load_project() -> tuple[Path, Storage]:
    """Shared setup for every command: one settings object, auto-
    discovering the project root, feeding one Storage instance."""
    settings = CodeGuardSettings.for_project()
    storage = Storage.from_settings(settings)
    return settings.project_root, storage


def _ensure_indexed(project_root: Path, storage: Storage) -> None:
    """
    Re-index whatever changed since the last run, before answering any
    question. Cheap on an unchanged project: `index_project` skips every
    file whose blob hash hasn't changed, so a repeated run against a
    codebase you haven't touched only does a handful of hash comparisons,
    not a full re-parse/re-embed.
    """
    reindexed = index_project(project_root, storage)
    if reindexed:
        typer.echo(f"Indexed {reindexed} changed file(s).")


@app.command()
def hello():
    """Sanity check that the CLI works."""
    typer.echo("codeguard is alive")


@app.command()
def scan():
    """Report likely dead code - functions/classes with no callers."""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    graph = build_graph_from_storage(storage)
    findings = find_dead_code(graph, project_root)
    typer.echo(render_deadcode_report(findings))


@app.command()
def impact(
    ref: str = typer.Argument(..., help="Git ref to compare against, e.g. HEAD~1 or main"),
):
    """Show the blast radius of signature changes since `ref`."""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    report = analyze_impact(project_root, storage, ref)
    typer.echo(render_report(report))


@app.command()
def find(
    query: str = typer.Argument(
        ..., help="Plain-English description of the problem, e.g. \"login isn't working\""
    ),
    max_results: int = typer.Option(8, help="Maximum number of code chunks to return"),
):
    """Return a small, ranked bundle of code relevant to a plain-English query."""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    result = find_relevant_code(query, storage, max_results=max_results)
    typer.echo(render_retrieval_result(result))
    
@app.command()
def context(
    task: str = typer.Argument(
        ..., help="Plain-English description, e.g. \"I'm about to change how login validates passwords\""
    ),
    target_symbol: str = typer.Option(
        None, help="Qualified name of a specific symbol you're about to touch or remove, if known"
    ),
    ref: str = typer.Option(
        None, help="Git ref to diff against, if you already have a change to check"
    ),
    max_results: int = typer.Option(8, help="Maximum number of retrieval results to include"),
):
    """Single entry point: 'I'm about to do X, what do I need to know?'"""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    request = ContextRequest(
        task=task, target_symbol=target_symbol, ref=ref, max_results=max_results
    )
    bundle = get_context(request, project_root, storage)
    typer.echo(render_context_bundle(bundle))


if __name__ == "__main__":
    app()