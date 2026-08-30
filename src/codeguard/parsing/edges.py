"""
Edge extraction: pure functions that look at ONE tree-sitter node already
known to be a `call`, `import_statement`, or `import_from_statement`, and
turn it into zero or more Edge objects.

Deliberately kept separate from chunker.py's tree-walking loop: the walk
(deciding WHERE to look, tracking scope) stays in chunker.py; the
interpretation (deciding WHAT a given node means) lives here. chunker.py
calls into these functions from inside its single existing walk - this
file does not walk the tree itself.
"""

from __future__ import annotations

from tree_sitter import Node

from codeguard.parsing.models import Edge


def _current_scope_name(scope_stack) -> str:
    """"<module>" if we're not inside any function/class, else the dotted
    qualified name of whatever we're currently inside."""
    if not scope_stack:
        return "<module>"
    return ".".join(s.name for s in scope_stack)


def _current_scope_chunk_id(scope_stack) -> str | None:
    """The chunk_id of the innermost enclosing function/method, or None if
    we're at module level (module-level code has no Phase 1 chunk)."""
    if not scope_stack:
        return None
    return scope_stack[-1].chunk_id


def extract_call_edge(node: Node, source_bytes: bytes, rel_path: str, scope_stack) -> Edge | None:
    """
    `node` must be a `call` node. Returns None only if the call's target
    couldn't be read as a simple identifier or attribute chain (e.g. calling
    the result of another call, like `get_handler()()` - rare, safely skipped).
    """
    function_node = node.child_by_field_name("function")
    if function_node is None:
        return None

    if function_node.type == "identifier":
        bare_name = _text(function_node, source_bytes)
        full_expression = bare_name
    elif function_node.type == "attribute":
        full_expression = _text(function_node, source_bytes)
        attr_node = function_node.child_by_field_name("attribute")
        if attr_node is None:
            return None
        bare_name = _text(attr_node, source_bytes)
    else:
        # e.g. calling a subscript or another call's result - not a named
        # symbol we could usefully resolve later, so skip it.
        return None

    return Edge(
        kind="calls",
        file_path=rel_path,
        source_chunk_id=_current_scope_chunk_id(scope_stack),
        source_qualified_name=_current_scope_name(scope_stack),
        target_name=bare_name,
        target_expression=full_expression,
        imported_from_module="",
        local_alias=bare_name,
    )


def extract_import_edges(node: Node, source_bytes: bytes, rel_path: str, scope_stack) -> list[Edge]:
    """
    `node` must be an `import_statement` or `import_from_statement`.
    Returns one Edge per imported name (a single `from x import a, b`
    statement produces two edges, one per name).
    """
    source_chunk_id = _current_scope_chunk_id(scope_stack)
    source_qualified_name = _current_scope_name(scope_stack)
    edges: list[Edge] = []

    if node.type == "import_statement":
        # e.g. "import os" or "import os.path"
        for child in node.children:
            if child.type == "dotted_name":
                module_name = _text(child, source_bytes)
                edges.append(
                    Edge(
                        kind="imports",
                        file_path=rel_path,
                        source_chunk_id=source_chunk_id,
                        source_qualified_name=source_qualified_name,
                        target_name=module_name,
                        target_expression=module_name,
                        imported_from_module="",
                        local_alias=module_name,
                    )
                )

    elif node.type == "import_from_statement":
        # e.g. "from pathlib import Path" or "from typing import List, Optional as Opt"
        module_node = node.child_by_field_name("module_name")
        module_name = _text(module_node, source_bytes) if module_node is not None else ""
        module_span = (module_node.start_byte, module_node.end_byte) if module_node is not None else None

        for child in node.children:
            # tree-sitter returns a fresh Node wrapper object on every access,
            # so `child is not module_node` never works even for the same
            # underlying node - compare by byte span instead.
            child_span = (child.start_byte, child.end_byte)
            if child.type == "dotted_name" and child_span != module_span:
                imported_name = _text(child, source_bytes)
                edges.append(_make_import_edge(rel_path, source_chunk_id, source_qualified_name,
                                                module_name, imported_name, imported_name))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is None or alias_node is None:
                    continue
                imported_name = _text(name_node, source_bytes)
                alias = _text(alias_node, source_bytes)
                edges.append(_make_import_edge(rel_path, source_chunk_id, source_qualified_name,
                                                module_name, imported_name, alias))

    return edges


def _make_import_edge(rel_path, source_chunk_id, source_qualified_name,
                       module_name, imported_name, local_alias) -> Edge:
    return Edge(
        kind="imports",
        file_path=rel_path,
        source_chunk_id=source_chunk_id,
        source_qualified_name=source_qualified_name,
        target_name=imported_name,
        target_expression=f"{module_name}.{imported_name}" if module_name else imported_name,
        imported_from_module=module_name,
        local_alias=local_alias,
    )


def _text(node: Node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8")