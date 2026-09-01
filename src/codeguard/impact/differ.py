"""
Compares old vs new chunks (already parsed by Chunker.parse_source /
parse_file) and decides which functions/methods had their SIGNATURE
change - not their body. See Phase 5, Decision C/D/F in the decision log
for why this is scoped the way it is.
"""

from __future__ import annotations

from tree_sitter import Language, Parser
import tree_sitter_python as tspython

from codeguard.impact.models import ChangedSymbol
from codeguard.parsing.models import Chunk

PY_LANGUAGE = Language(tspython.language())
_parser = Parser(PY_LANGUAGE)

_SIGNATURE_KINDS = ("function", "method")


def extract_signature_text(chunk: Chunk) -> str | None:
    """
    Re-parses this one chunk's own stored source text to pull out just its
    parameter-list text, e.g. "(self, user, password)". Returns None for
    chunk kinds that don't have a signature (e.g. "class").

    Deliberately re-parses `chunk.content` instead of adding a new column
    to the chunks table - this info is only needed here, on demand
    (Decision C), same reasoning as Phase 4's decorator lookup.
    """
    if chunk.kind not in _SIGNATURE_KINDS:
        return None

    source_bytes = chunk.content.encode("utf-8")
    tree = _parser.parse(source_bytes)
    # chunk.content starts at the function_definition node itself.
    func_node = tree.root_node.children[0] if tree.root_node.children else None
    if func_node is None or func_node.type != "function_definition":
        return None

    params_node = func_node.child_by_field_name("parameters")
    if params_node is None:
        return None

    raw = source_bytes[params_node.start_byte:params_node.end_byte].decode("utf-8")
    # Normalize whitespace so a purely cosmetic reformat isn't reported as
    # a real signature change.
    return " ".join(raw.split())


def diff_signatures(old_chunks: list[Chunk], new_chunks: list[Chunk]) -> list[ChangedSymbol]:
    """
    Matches old and new chunks by chunk_id (Phase 1, Decision B - this is
    exactly the case that ID was designed for) and returns one
    ChangedSymbol per function/method whose signature text differs, plus
    one per function/method that existed before and is now gone entirely.
    """
    old_by_id = {c.chunk_id: c for c in old_chunks if c.kind in _SIGNATURE_KINDS}
    new_by_id = {c.chunk_id: c for c in new_chunks if c.kind in _SIGNATURE_KINDS}

    changed: list[ChangedSymbol] = []

    for chunk_id, old_chunk in old_by_id.items():
        new_chunk = new_by_id.get(chunk_id)

        if new_chunk is None:
            changed.append(
                ChangedSymbol(
                    chunk_id=chunk_id,
                    qualified_name=old_chunk.qualified_name,
                    file_path=old_chunk.file_path,
                    change_type="removed",
                    old_signature=extract_signature_text(old_chunk),
                    new_signature=None,
                )
            )
            continue

        old_sig = extract_signature_text(old_chunk)
        new_sig = extract_signature_text(new_chunk)
        if old_sig != new_sig:
            changed.append(
                ChangedSymbol(
                    chunk_id=chunk_id,
                    qualified_name=new_chunk.qualified_name,
                    file_path=new_chunk.file_path,
                    change_type="signature_changed",
                    old_signature=old_sig,
                    new_signature=new_sig,
                )
            )

    return changed