"""
LanceDB table schemas. `chunks` was added in Phase 1; `edges` in Phase 2.

Phase 6 adds one column to `chunks`: `vector`, a fixed-size list of
float32 - the embedding of that chunk's `content`, produced by
embedding/embedder.py. This is the ONLY schema change Phase 6 needs,
because the design deliberately reused the existing `chunks` table
instead of standing up a second storage system just for vectors.

Two more tables were added later, each for the same reason `edges` was
split out from `chunks` originally - a genuinely different shape of data,
not a variant of an existing row:

  - `annotations` - notes a person or agent leaves on a symbol, so it
    survives across sessions (see storage/db.py's insert_annotation).
  - `repo_meta`   - one row recording which embedder/dimension the
    current index was built with, so a silent embedder swap can be
    detected instead of silently corrupting vector search (see
    indexing.py).
"""

import pyarrow as pa

# Dimensionality of the `all-MiniLM-L6-v2` sentence-transformers model.
# If the embedding model in config.py is ever swapped for one with a
# different output size, this constant (and a re-index of the project)
# needs to change with it - a mismatched dimension is a schema error,
# not a silent bug, which is the safer failure mode.
EMBEDDING_DIM = 384

CHUNKS_TABLE_NAME = "chunks"

CHUNKS_SCHEMA = pa.schema(
    [
        pa.field("chunk_id", pa.string()),
        pa.field("file_path", pa.string()),
        pa.field("symbol_name", pa.string()),
        pa.field("qualified_name", pa.string()),
        pa.field("kind", pa.string()),
        pa.field("start_line", pa.int64()),
        pa.field("end_line", pa.int64()),
        pa.field("content", pa.string()),
        pa.field("blob_hash", pa.string()),
        # Fixed-size list, required (not a variable-length list) so LanceDB
        # can build an ANN index over it later if the project grows large
        # enough to need one.
        pa.field("vector", pa.list_(pa.float32(), EMBEDDING_DIM)),
    ]
)

EDGES_TABLE_NAME = "edges"

EDGES_SCHEMA = pa.schema(
    [
        pa.field("kind", pa.string()),                  # "calls" | "imports"
        pa.field("file_path", pa.string()),
        pa.field("source_chunk_id", pa.string()),         # nullable: None at module level
        pa.field("source_qualified_name", pa.string()),
        pa.field("target_name", pa.string()),              # unresolved - Phase 3's job
        pa.field("target_expression", pa.string()),
        pa.field("imported_from_module", pa.string()),      # "" when kind == "calls"
        pa.field("local_alias", pa.string()),
    ]
)

ANNOTATIONS_TABLE_NAME = "annotations"

ANNOTATIONS_SCHEMA = pa.schema(
    [
        pa.field("annotation_id", pa.string()),   # uuid4, unique per note
        pa.field("chunk_id", pa.string()),          # matches Chunk.chunk_id
        pa.field("qualified_name", pa.string()),     # denormalized for display
        pa.field("file_path", pa.string()),           # denormalized for display
        pa.field("note", pa.string()),
        pa.field("author", pa.string()),
        pa.field("created_at", pa.string()),           # ISO 8601, UTC
    ]
)

REPO_META_TABLE_NAME = "repo_meta"

# A single-row table: whichever embedder/dimension the index currently on
# disk was built with. See indexing.py's staleness check.
REPO_META_SCHEMA = pa.schema(
    [
        pa.field("embedder_name", pa.string()),
        pa.field("embedding_dim", pa.int64()),
    ]
)