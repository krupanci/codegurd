"""
The embedder: turns text (a chunk's content, or a plain-English query)
into a fixed-length vector of numbers that captures its MEANING, not its
exact wording - two pieces of text about the same idea end up as nearby
vectors, even if they don't share any words.

Kept behind a small `Embedder` interface, exactly like the design doc
calls for, so a paid/API embedder (OpenAI, Voyage, Cohere, ...) can be
dropped in later purely by adding a new class here - nothing in
storage/db.py, indexing.py, or retrieval/ ever needs to change, because
they only ever depend on the interface, never on
`sentence_transformers` directly.
"""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    """Anything that can turn text into vectors of the SAME fixed length
    as `codeguard.storage.schema.EMBEDDING_DIM`."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of chunk contents, for indexing."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single plain-English query, for searching."""
        ...


class LocalEmbedder:
    """
    Wraps a local `sentence-transformers` model (default: `all-MiniLM-L6-v2`
    - small, fast on CPU, and good enough for code+English text) so the
    whole tool runs free and offline, with no API key required.

    The model is loaded once, the first time it's actually needed (not at
    import time - importing `sentence_transformers` and pulling the model
    weights into memory is not free, and most CLI invocations of codeguard,
    like `codeguard impact`, never touch embeddings at all).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None  # lazy-loaded on first use, see `_get_model`

    def _get_model(self):
        if self._model is None:
            # Imported here, not at module level, so `import
            # codeguard.embedding.embedder` doesn't force-load a ~90MB
            # model file for every command, including ones that never
            # embed anything.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        # normalize_embeddings=True makes cosine similarity and dot-product
        # equivalent, and is what `Storage.semantic_search`'s
        # `.metric("cosine")` search assumes.
        vectors = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


# Module-level cache so repeated calls within one CLI run (or one retrieval
# call) reuse the same loaded model instead of reloading it from disk every
# time - loading the model is the slow part, running it on new text is fast.
_default_embedder: LocalEmbedder | None = None


def get_default_embedder(model_name: str = "all-MiniLM-L6-v2") -> LocalEmbedder:
    global _default_embedder
    if _default_embedder is None or _default_embedder.model_name != model_name:
        _default_embedder = LocalEmbedder(model_name)
    return _default_embedder