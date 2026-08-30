"""
The chunker: turns a single .py file into a list of Chunk objects, and
(as of Phase 2) also the raw call/import Edges found in the same file.

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
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language, Node, Parser
import tree_sitter_python as tspython

from codeguard.parsing.edges import extract_call_edge, extract_import_edges
from codeguard.parsing.ids import make_chunk_id
from codeguard.parsing.models import Chunk, Edge
from codeguard.storage.hashing import compute_file_blob_hash

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
        Parse one Python file and return BOTH the chunks (Phase 1) and the
        raw calls/imports found in it (Phase 2), from a single tree walk.
        """
        file_path = Path(file_path)
        project_root = Path(project_root)
        rel_path = str(file_path.resolve().relative_to(project_root.resolve()))

        source_bytes = file_path.read_bytes()
        blob_hash = compute_file_blob_hash(file_path)

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
                # Keep walking INTO the call (e.g. its arguments) so a call
                # nested inside another call's arguments is still found.
                self._walk(child, source_bytes, rel_path, blob_hash, scope_stack,
                           chunks_out, edges_out)
            elif child.type in IMPORT_NODE_TYPES:
                edges_out.extend(
                    extract_import_edges(child, source_bytes, rel_path, scope_stack)
                )
                # Import statements don't contain calls - no need to recurse.
            else:
                # Not a definition, call, or import itself, but any of those
                # could still be nested inside it (e.g. inside an
                # `if __name__ == ...:` block, a loop, a try block) - keep
                # looking.
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

        # A "function_definition" directly inside a class body is a method;
        # otherwise it's a plain function (this also covers nested functions,
        # whose enclosing scope kind is "function", not "class").
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

        # Recurse into the body to find methods (if this was a class),
        # nested functions (if this was a function), and any calls/imports
        # inside it - extending the scope so they're attributed correctly.
        body = node.child_by_field_name("body")
        if body is not None:
            new_scope = scope_stack + [_Scope(name=symbol_name, kind=node_kind, chunk_id=chunk_id)]
            self._walk(body, source_bytes, rel_path, blob_hash, new_scope, chunks_out, edges_out)