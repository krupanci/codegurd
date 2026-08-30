"""
LanceDB table schemas. `chunks` was added in Phase 1 - no vector column yet
(that's added in Phase 6). `edges` is added in Phase 2.
"""

import pyarrow as pa

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