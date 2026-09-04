"""
Phase 8 - Unified CLI.

Ties every feature behind one consistent command-line tool:

    codeguard scan                 -> dead code report              (Phase 4)
    codeguard impact <ref>          -> change-impact / blast radius  (Phase 5)
    codeguard find "<query>"         -> scoped context retrieval      (Phase 7)
    codeguard context "<task>"        -> unified "what do I need to know" entry point
    codeguard map                      -> whole-repo orientation
    codeguard note <symbol> "<text>"    -> leave a persisted note on a symbol
    codeguard callers <symbol>           -> who directly calls this symbol
    codeguard callees <symbol>            -> what this symbol directly calls

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

import getpass
from pathlib import Path

import typer

from codeguard.config import CodeGuardSettings
from codeguard.context.bundle import get_context
from codeguard.context.models import ContextRequest
from codeguard.context.report import render_context_bundle
from codeguard.deadcode.finder import find_dead_code
from codeguard.deadcode.report import render_deadcode_report
from codeguard.graph.graph import build_graph_from_storage
from codeguard.impact.analyzer import analyze_impact
from codeguard.impact.report import render_report
from codeguard.indexing import index_project
from codeguard.overview.ranker import rank_symbols
from codeguard.overview.report import render_repo_map
from codeguard.retrieval.report import render_retrieval_result
from codeguard.retrieval.retriever import find_relevant_code
from codeguard.storage.db import Storage

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


def _require_symbol(graph, qualified_name: str):
    """Shared lookup for the symbol-scoped commands (`note`, `callers`,
    `callees`) - exits cleanly with a clear message instead of every
    command re-implementing the same not-found check."""
    chunk = graph.find_by_qualified_name(qualified_name)
    if chunk is None:
        typer.echo(f"No symbol found matching '{qualified_name}'.")
        raise typer.Exit(code=1)
    return chunk


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
    max_hops: int = typer.Option(
        None,
        "--max-hops",
        help="Limit how many hops out the blast-radius walk goes (default: no limit)",
    ),
):
    """Show the blast radius of signature changes since `ref`."""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    report = analyze_impact(project_root, storage, ref, max_hops=max_hops)
    typer.echo(render_report(report))


@app.command()
def find(
    query: str = typer.Argument(
        ..., help="Plain-English description of the problem, e.g. \"login isn't working\""
    ),
    max_results: int = typer.Option(8, help="Maximum number of code chunks to return"),
    max_tokens: int = typer.Option(6000, "--max-tokens", help="Cap the total content size returned"),
):
    """Return a small, ranked bundle of code relevant to a plain-English query."""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    result = find_relevant_code(query, storage, max_results=max_results, max_tokens=max_tokens)
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
    orient: bool = typer.Option(
        False, "--orient", help="Also include whole-repo orientation (top structurally-important symbols)"
    ),
    max_results: int = typer.Option(8, help="Maximum number of retrieval/overview results to include"),
    max_tokens: int = typer.Option(6000, "--max-tokens", help="Cap the total content size of the bundle"),
):
    """Single entry point: 'I'm about to do X, what do I need to know?'"""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    request = ContextRequest(
        task=task,
        target_symbol=target_symbol,
        ref=ref,
        orient=orient,
        max_results=max_results,
        max_tokens=max_tokens,
    )
    bundle = get_context(request, project_root, storage)
    typer.echo(render_context_bundle(bundle))


@app.command(name="map")
def map_command(
    file: str = typer.Option(
        None, "--file", help="Restrict the map to one file plus its direct callers/callees"
    ),
    top: int = typer.Option(12, "--top", help="How many symbols to show"),
):
    """Whole-repo orientation: the most structurally important symbols -
    for getting your bearings in a codebase you've never seen before."""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    graph = build_graph_from_storage(storage)
    result = rank_symbols(graph, budget=top, file_path=file)
    typer.echo(render_repo_map(result))


@app.command()
def note(
    symbol: str = typer.Argument(..., help="Qualified name of the symbol to annotate"),
    text: str = typer.Argument(..., help="Note text"),
):
    """Leave a note on a symbol - it shows up automatically in future
    `find`/`context` results for that symbol."""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    graph = build_graph_from_storage(storage)
    chunk = _require_symbol(graph, symbol)

    storage.insert_annotation(
        chunk_id=chunk.chunk_id,
        qualified_name=chunk.qualified_name,
        file_path=chunk.file_path,
        note=text,
        author=getpass.getuser(),
    )
    typer.echo(f"Noted on {chunk.qualified_name}.")


@app.command()
def callers(symbol: str = typer.Argument(..., help="Qualified name of the symbol")):
    """List everything that directly calls this symbol."""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    graph = build_graph_from_storage(storage)
    chunk = _require_symbol(graph, symbol)

    caller_ids = graph.callers(chunk.chunk_id)
    if not caller_ids:
        typer.echo(f"No callers found for {chunk.qualified_name}.")
        return

    for caller_id in sorted(caller_ids):
        caller_chunk = graph.chunks_by_id.get(caller_id)
        if caller_chunk is not None:
            typer.echo(f"  {caller_chunk.file_path}  {caller_chunk.qualified_name}")
        elif caller_id.startswith("<module>::"):
            typer.echo(f"  {caller_id.split('::', 1)[1]}  <module-level code>")


@app.command()
def callees(symbol: str = typer.Argument(..., help="Qualified name of the symbol")):
    """List everything this symbol directly calls."""
    project_root, storage = _load_project()
    _ensure_indexed(project_root, storage)

    graph = build_graph_from_storage(storage)
    chunk = _require_symbol(graph, symbol)

    callee_ids = graph.callees(chunk.chunk_id)
    if not callee_ids:
        typer.echo(f"No callees found for {chunk.qualified_name}.")
        return

    for callee_id in sorted(callee_ids):
        callee_chunk = graph.chunks_by_id.get(callee_id)
        if callee_chunk is not None:
            typer.echo(f"  {callee_chunk.file_path}  {callee_chunk.qualified_name}")


if __name__ == "__main__":
    app()