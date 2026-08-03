"""`doc_search` (§5) — the M2 retriever, behind the same tool boundary as the rest.

The work was done at M2 and lives in `rag/index.py`. What moves here is only the
index's construction, which was previously private to the FastAPI app: the MCP
server needs the identical object, and two services each building their own
would be two places for a collection name or a score floor to drift apart.
"""

from __future__ import annotations

import functools
from typing import Any

from nightglass.config import settings
from nightglass.schemas import Chunk
from nightglass.tools.base import ToolError


@functools.lru_cache(maxsize=1)
def document_index() -> Any:
    """Built on first use, not at import.

    Constructing it at module scope would make every service's start-up depend
    on Qdrant and Ollama being up, which is the cascade `/health` is
    dependency-free to avoid. The import is lazy too, so a health probe never
    pulls in the vector client.

    Cached because it holds an HTTP client, not because it holds results —
    `search` below queries Qdrant every call, so §5's "no caching that changes
    results between runs" is untouched.
    """
    from nightglass.rag.embed import Embedder
    from nightglass.rag.index import DocumentIndex

    return DocumentIndex(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedder=Embedder(host=settings.ollama_host, model=settings.ollama_embed_model),
    )


def doc_search(
    query: str,
    k: int = 8,
    filters: dict[str, Any] | None = None,
    *,
    min_score: float | None = None,
) -> list[Chunk]:
    """§5: `doc_search(query, k=8, filters=None) -> list[Chunk]`.

    Chunks carry doc_id, chunk_id, classification and source URL, not bare text.
    Everything downstream needs those to cite, and a retriever that dropped them
    would push the work onto its callers until one of them forgot.
    """
    try:
        return document_index().search(
            query,
            k=k or settings.rag_top_k,
            filters=filters,
            min_score=settings.rag_min_score if min_score is None else min_score,
        )
    except Exception as exc:
        raise ToolError(
            f"{type(exc).__name__}: {exc}. Has `make ingest` been run? "
            "`make docs-stats` reports what is actually in the index."
        ) from exc
