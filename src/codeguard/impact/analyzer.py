"""
Ties git_ops + differ + the Phase 3 graph together: for a given ref,
produce a full BlastRadiusReport. This is the only function Phase 8's CLI
(and later, Phase 10's MCP wrapper) needs to call.
"""

from __future__ import annotations

from pathlib import Path

from codeguard.graph.graph import build_graph_from_storage
from codeguard.impact import differ, git_ops
from codeguard.impact.models import BlastRadiusNode, BlastRadiusReport
from codeguard.parsing.chunker import Chunker
from codeguard.storage.db import Storage


def analyze_impact(project_root: Path, storage: Storage, ref: str) -> BlastRadiusReport:
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

    # Build the graph fresh from whatever's currently indexed in LanceDB.
    # (Run `codeguard scan` or your indexing step first if the project
    # hasn't been (re)indexed since your last edits - Phase 8 will wire
    # this staleness check in automatically.)
    graph = build_graph_from_storage(storage)

    nodes = []
    for changed_symbol in all_changed_symbols:
        hop_distances = graph.walk_reverse(changed_symbol.chunk_id)
        nodes.append(BlastRadiusNode(changed_symbol=changed_symbol, affected=hop_distances))

    return BlastRadiusReport(ref=ref, nodes=nodes)