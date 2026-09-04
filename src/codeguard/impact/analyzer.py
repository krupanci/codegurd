"""
Ties git_ops + differ + the Phase 3 graph together: for a given ref,
produce a full BlastRadiusReport. This is the only function Phase 8's CLI
(and later, Phase 10's MCP wrapper) needs to call.

CHANGE (bugfix): `graph.walk_reverse(changed_symbol.chunk_id)` used to be
called with no `max_hops`, but that parameter had no default - this raised
a TypeError on every single call, meaning `codeguard impact` never
actually worked. `Graph.walk_reverse` now accepts `max_hops=None` for "no
limit", which is what PHASE1_DECISIONS.md Decision E always intended
("full transitive closure") - that's what's passed here explicitly.

CHANGE (perf/dedup): `graph` is now an optional parameter. Standalone
callers (the CLI) still get one built for them automatically. Callers
that already have a Graph for this run - context/bundle.py, which may
also be running retrieval and dead-code detection in the same call - can
pass it in directly instead of forcing a second full rebuild from
storage.

CHANGE (small win): `max_hops` is now exposed here too, threaded straight
through to `graph.walk_reverse`. Every check previously did the most
expensive possible walk (full transitive closure) with no way to ask for
a shallower one - the graph already supported a hop limit, this was just
never wired up past `walk_reverse` itself.
"""

from __future__ import annotations

from pathlib import Path

from codeguard.graph.graph import Graph, build_graph_from_storage
from codeguard.impact import differ, git_ops
from codeguard.impact.models import BlastRadiusNode, BlastRadiusReport
from codeguard.parsing.chunker import Chunker
from codeguard.storage.db import Storage


def analyze_impact(
    project_root: Path,
    storage: Storage,
    ref: str,
    graph: Graph | None = None,
    max_hops: int | None = None,
) -> BlastRadiusReport:
    chunker = Chunker()
    changed_files = git_ops.get_changed_python_files(project_root, ref)

    all_changed_symbols = []
    for rel_path in changed_files:
        current_file = project_root / rel_path
        if not current_file.exists():
            # File deleted since `ref` - out of scope for v1 (Decision "Other notes").
            continue

        old_source = git_ops.get_file_content_at_ref(project_root, ref, rel_path)
        if old_source is None:
            # New file since `ref` - nothing to diff against.
            continue

        old_chunks, _ = chunker.parse_source(old_source.encode("utf-8"), rel_path)
        new_chunks, _ = chunker.parse_file(current_file, project_root)

        all_changed_symbols.extend(differ.diff_signatures(old_chunks, new_chunks))

    # Build the graph fresh from whatever's currently indexed in LanceDB,
    # unless the caller already has one for this run (see docstring above).
    # (Run `codeguard scan` or your indexing step first if the project
    # hasn't been (re)indexed since your last edits - Phase 8 will wire
    # this staleness check in automatically.)
    if graph is None:
        graph = build_graph_from_storage(storage)

    nodes = []
    for changed_symbol in all_changed_symbols:
        # max_hops=None (the default) -> full transitive closure, per
        # Decision E: every caller, however many hops away, belongs in
        # the blast radius. A caller that only wants a shallower walk can
        # now ask for one explicitly.
        hop_distances = graph.walk_reverse(changed_symbol.chunk_id, max_hops=max_hops)
        nodes.append(BlastRadiusNode(changed_symbol=changed_symbol, affected=hop_distances))

    return BlastRadiusReport(ref=ref, nodes=nodes)