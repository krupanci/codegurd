"""
The chunker: turns a single .py file into a list of Chunk objects.

Deliberately hand-walks the tree-sitter syntax tree ourselves (no `.scm`
query files) - the point of this project is to actually understand the
tree shape, not lean on tree-sitter's query engine to find things for us.

Walk logic in plain terms:
  - We recurse through the tree keeping a "scope stack": the list of
    names/kinds of everything we're currently nested inside
    (e.g. [("LoginHandler", "class")] means we're inside that class body).
  - Every time we hit a `class_definition` node, that's a "class" chunk.
  - Every time we hit a `function_definition` node, it's a "method" if the
    immediately enclosing scope is a class, otherwise a "function"
    (this also correctly handles a function nested inside another function).
  - The qualified name is built by joining the scope stack's names with ".".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language, Node, Parser
import tree_sitter_python as tspython

from codeguard.parsing.ids import make_chunk_id
from codeguard.parsing.models import Chunk
from codeguard.storage.hashing import compute_file_blob_hash

PY_LANGUAGE = Language(tspython.language())


@dataclass
class _Scope:
    name: str
    kind: str  # "class" or "function"


class Chunker:
    def __init__(self) -> None:
        self._parser = Parser(PY_LANGUAGE)

    def chunk_file(self, file_path: Path | str, project_root: Path | str) -> list[Chunk]:
        """
        Parse one Python file and return every function/class/method chunk
        found in it. `file_path` is stored relative to `project_root`.
        """
        file_path = Path(file_path)
        project_root = Path(project_root)
        rel_path = str(file_path.resolve().relative_to(project_root.resolve()))

        source_bytes = file_path.read_bytes()
        blob_hash = compute_file_blob_hash(file_path)

        tree = self._parser.parse(source_bytes)

        chunks: list[Chunk] = []
        self._walk(tree.root_node, source_bytes, rel_path, blob_hash, scope_stack=[], out=chunks)
        return chunks

    def _walk(
        self,
        node: Node,
        source_bytes: bytes,
        rel_path: str,
        blob_hash: str,
        scope_stack: list[_Scope],
        out: list[Chunk],
    ) -> None:
        for child in node.children:
            if child.type == "function_definition":
                self._handle_definition(
                    child, source_bytes, rel_path, blob_hash, scope_stack, out,
                    node_kind="function",
                )
            elif child.type == "class_definition":
                self._handle_definition(
                    child, source_bytes, rel_path, blob_hash, scope_stack, out,
                    node_kind="class",
                )
            else:
                # Not a definition itself, but definitions could still be
                # nested inside it (e.g. inside an `if __name__ == ...:`
                # block at module level) - keep looking.
                self._walk(child, source_bytes, rel_path, blob_hash, scope_stack, out)

    def _handle_definition(
        self,
        node: Node,
        source_bytes: bytes,
        rel_path: str,
        blob_hash: str,
        scope_stack: list[_Scope],
        out: list[Chunk],
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

        out.append(
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

        # Recurse into the body to find methods (if this was a class) or
        # nested functions (if this was a function), extending the scope.
        body = node.child_by_field_name("body")
        if body is not None:
            new_scope = scope_stack + [_Scope(name=symbol_name, kind=node_kind)]
            self._walk(body, source_bytes, rel_path, blob_hash, new_scope, out)