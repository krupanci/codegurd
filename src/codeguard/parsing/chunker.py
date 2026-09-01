"""
The chunker: turns a single .py file (or raw source text) into a list of
Chunk objects, and (as of Phase 2) also the raw call/import Edges found in
the same source.

Deliberately hand-walks the tree-sitter syntax tree ourselves (no `.scm`
query files) - the point of this project is to actually understand the
tree shape, not lean on tree-sitter's query engine to find things for us.

Walk logic in plain terms:
  - We recurse through the tree keeping a "scope stack": the list of
    names/kinds/chunk_ids of everything we're currently nested inside
    (e.g. [("LoginHandler", "class", ...)] means we're inside that class).
  - Every time we hit a `class_definition` node, that's a "class" chunk.
  - Every time we hit a `function_definition` node, it's a "method" if the
    immediately enclosing scope is a class, otherwise a "function"
    (this also correctly handles a function nested inside another function).
  - Every time we hit a `call` node, or an import statement, we hand it to
    parsing/edges.py to turn into an Edge, attributing it to whatever scope
    we're currently inside (or "<module>" if we're not inside anything).

As of Phase 5, the walk is available on raw source text via `parse_source`,
not just on real files on disk via `parse_file` - needed to parse a file's
content as it existed at an old git ref, which isn't a file that exists
anywhere on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language, Node, Parser
import tree_sitter_python as tspython

from codeguard.parsing.edges import extract_call_edge, extract_import_edges
from codeguard.parsing.ids import make_chunk_id
from codeguard.parsing.models import Chunk, Edge
from codeguard.storage.hashing import compute_file_blob_hash, git_blob_hash
PY_LANGUAGE = Language(tspython.language())

IMPORT_NODE_TYPES = ("import_statement", "import_from_statement")


@dataclass
class _Scope:
    name: str
    kind: str        # "class" or "function"
    chunk_id: str      # the Chunk this scope corresponds to


class Chunker:
    def __init__(self) -> None:
        self._parser = Parser(PY_LANGUAGE)

    def chunk_file(self, file_path: Path | str, project_root: Path | str) -> list[Chunk]:
        """
        Parse one Python file and return every function/class/method chunk
        found in it. `file_path` is stored relative to `project_root`.

        Unchanged from Phase 1 - kept exactly as-is so existing callers and
        tests don't need to know Phase 2 exists. Internally it just discards
        the edges that `parse_file` also collects in the same walk.
        """
        chunks, _edges = self.parse_file(file_path, project_root)
        return chunks

    def parse_file(
        self, file_path: Path | str, project_root: Path | str
    ) -> tuple[list[Chunk], list[Edge]]:
        """
        Parse one Python file ON DISK and return BOTH the chunks (Phase 1)
        and the raw calls/imports found in it (Phase 2).

        Reads bytes + computes the file's blob hash, then hands off to
        `parse_source` for the actual tree walk - kept as a thin wrapper so
        the two concerns (I/O vs. walking logic) stay separate, the same
        split already used for `build_graph` / `build_graph_from_storage`.
        """
        file_path = Path(file_path)
        project_root = Path(project_root)
        rel_path = str(file_path.resolve().relative_to(project_root.resolve()))

        source_bytes = file_path.read_bytes()
        blob_hash = compute_file_blob_hash(file_path)

        return self.parse_source(source_bytes, rel_path, blob_hash=blob_hash)

    def parse_source(
        self,
        source_bytes: bytes,
        rel_path: str,
        blob_hash: str | None = None,
    ) -> tuple[list[Chunk], list[Edge]]:
        """
        Parse raw Python source text that is NOT necessarily a file on disk
        (e.g. a file's content as it existed at an old git ref) and return
        the same (chunks, edges) shape as `parse_file`.

        `rel_path` is used exactly as `parse_file` uses it - as the
        already-relative-to-project-root path to attribute chunks/edges to.
        If `blob_hash` isn't supplied, it's computed directly from the given
        bytes using the same git blob-hash formula (Phase 1, Decision C),
        so old-ref content still gets a valid, comparable hash.
        """
        if blob_hash is None:
            blob_hash = git_blob_hash(source_bytes)

        tree = self._parser.parse(source_bytes)

        chunks: list[Chunk] = []
        edges: list[Edge] = []
        self._walk(tree.root_node, source_bytes, rel_path, blob_hash, scope_stack=[],
                   chunks_out=chunks, edges_out=edges)
        return chunks, edges

    def _walk(
        self,
        node: Node,
        source_bytes: bytes,
        rel_path: str,
        blob_hash: str,
        scope_stack: list[_Scope],
        chunks_out: list[Chunk],
        edges_out: list[Edge],
    ) -> None:
        for child in node.children:
            if child.type == "function_definition":
                self._handle_definition(
                    child, source_bytes, rel_path, blob_hash, scope_stack,
                    chunks_out, edges_out, node_kind="function",
                )
            elif child.type == "class_definition":
                self._handle_definition(
                    child, source_bytes, rel_path, blob_hash, scope_stack,
                    chunks_out, edges_out, node_kind="class",
                )
            elif child.type == "call":
                edge = extract_call_edge(child, source_bytes, rel_path, scope_stack)
                if edge is not None:
                    edges_out.append(edge)
                self._walk(child, source_bytes, rel_path, blob_hash, scope_stack,
                           chunks_out, edges_out)
            elif child.type in IMPORT_NODE_TYPES:
                edges_out.extend(
                    extract_import_edges(child, source_bytes, rel_path, scope_stack)
                )
            else:
                self._walk(child, source_bytes, rel_path, blob_hash, scope_stack,
                           chunks_out, edges_out)

    def _handle_definition(
        self,
        node: Node,
        source_bytes: bytes,
        rel_path: str,
        blob_hash: str,
        scope_stack: list[_Scope],
        chunks_out: list[Chunk],
        edges_out: list[Edge],
        node_kind: str,
    ) -> None:
        name_node = node.child_by_field_name("name")
        symbol_name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")

        if node_kind == "function":
            enclosing = scope_stack[-1].kind if scope_stack else None
            kind = "method" if enclosing == "class" else "function"
        else:
            kind = "class"

        qualified_name = ".".join([s.name for s in scope_stack] + [symbol_name])
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        content = source_bytes[node.start_byte:node.end_byte].decode("utf-8")
        chunk_id = make_chunk_id(rel_path, qualified_name)

        chunks_out.append(
            Chunk(
                chunk_id=chunk_id,
                file_path=rel_path,
                symbol_name=symbol_name,
                qualified_name=qualified_name,
                kind=kind,  # type: ignore[arg-type]
                start_line=start_line,
                end_line=end_line,
                content=content,
                blob_hash=blob_hash,
            )
        )

        body = node.child_by_field_name("body")
        if body is not None:
            new_scope = scope_stack + [_Scope(name=symbol_name, kind=node_kind, chunk_id=chunk_id)]
            self._walk(body, source_bytes, rel_path, blob_hash, new_scope, chunks_out, edges_out)