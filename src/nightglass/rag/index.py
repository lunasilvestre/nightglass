"""The Qdrant document index: ingest, and `doc_search`.

Why Qdrant rather than pgvector, given PostGIS is already in the stack: a single
binary with no external dependencies is a materially easier thing to ship into
an air-gapped environment than an extension that has to match a server build.
Recorded in the README design table; repeated here because this is the file
where the choice actually lands.

Two properties this module is built around:

**Ingest is idempotent.** A point's ID is `uuid5` of its chunk ID, and chunk IDs
are positional, so re-ingesting an unchanged document overwrites the same points
rather than duplicating them. `make ingest` is safe to run repeatedly, which
matters because it will be -- every corpus edit is followed by one.

**Retrieval returns provenance, not text.** `doc_search` returns `Chunk`
objects carrying doc_id, chunk_id, classification and source URL, because §5
fixed that contract and §7 needs every one of those fields to reach the report.
A retriever that returned bare strings would push that work onto whatever called
it, and something would eventually forget.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient, models

from nightglass.rag.chunking import MAX_CHARS, MIN_CHARS, OVERLAP_CHARS, chunk_document
from nightglass.rag.documents import Document
from nightglass.rag.embed import DIMENSIONS, Embedder
from nightglass.schemas import Chunk

# Fields worth a payload index: everything `doc_search`'s `filters` argument is
# likely to be given. Unindexed filtering in Qdrant still works, it just scans.
_INDEXED_FIELDS = ("doc_id", "classification", "origin", "publisher", "doc_type", "language", "aoi")

_POINT_NAMESPACE = uuid.UUID("6f1c9b2e-0f3a-4a1e-9b7d-2c9a41f0e5c8")


@dataclass
class IngestReport:
    """What an ingest did, in enough detail to put in a commit message."""

    documents: int = 0
    chunks: int = 0
    characters: int = 0
    collection: str = ""
    by_publisher: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"collection   {self.collection}",
            f"documents    {self.documents}",
            f"chunks       {self.chunks}",
            f"characters   {self.characters:,}",
        ]
        if self.documents:
            lines.append(f"mean chunk   {self.characters // max(self.chunks, 1)} chars")
        for pub, n in sorted(self.by_publisher.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {pub:<45} {n:>5} chunks")
        for s in self.skipped:
            lines.append(f"  skipped: {s}")
        return "\n".join(lines)


class DocumentIndex:
    """Everything that touches Qdrant."""

    def __init__(
        self,
        *,
        url: str,
        embedder: Embedder,
        collection: str = "nightglass_docs",
        timeout: float = 60.0,
    ) -> None:
        self.collection = collection
        self.embedder = embedder
        self.client = QdrantClient(url=url, timeout=int(timeout))

    # -- ingest ---------------------------------------------------------------

    def ensure_collection(self, *, recreate: bool = False) -> None:
        if recreate and self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=DIMENSIONS, distance=models.Distance.COSINE
                ),
            )
        for fld in _INDEXED_FIELDS:
            # Idempotent in practice: creating an index that exists is a no-op
            # server-side, and swallowing the error is cheaper than a round trip
            # to ask first.
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=fld,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception:  # noqa: BLE001, S110 -- index already present
                pass

    def ingest(
        self,
        documents: list[Document],
        *,
        max_chars: int = MAX_CHARS,
        min_chars: int = MIN_CHARS,
        overlap_chars: int = OVERLAP_CHARS,
        progress: bool = False,
    ) -> IngestReport:
        report = IngestReport(collection=self.collection)
        self.ensure_collection()

        for doc in documents:
            chunks = chunk_document(
                doc, max_chars=max_chars, min_chars=min_chars, overlap_chars=overlap_chars
            )
            if not chunks:
                report.skipped.append(f"{doc.doc_id} (no text after chunking)")
                continue
            if progress:
                print(f"  {doc.doc_id}  ({len(chunks)} chunks)", flush=True)

            vectors = self.embedder.embed(
                [c.embedding_input(title=doc.title) for c in chunks],
                progress=progress and len(chunks) > 64,
            )
            base = doc.payload()
            points = [
                models.PointStruct(
                    id=str(uuid.uuid5(_POINT_NAMESPACE, c.chunk_id)),
                    vector=vec,
                    payload={
                        **base,
                        "chunk_id": c.chunk_id,
                        "ordinal": c.ordinal,
                        "heading": c.heading,
                        "text": c.text,
                    },
                )
                for c, vec in zip(chunks, vectors, strict=True)
            ]
            self.client.upsert(collection_name=self.collection, points=points, wait=True)

            report.documents += 1
            report.chunks += len(chunks)
            report.characters += sum(len(c.text) for c in chunks)
            report.by_publisher[doc.publisher] = report.by_publisher.get(doc.publisher, 0) + len(
                chunks
            )
        return report

    # -- retrieval ------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        k: int = 8,
        filters: dict[str, Any] | None = None,
        min_score: float | None = None,
    ) -> list[Chunk]:
        """§5's `doc_search`. Returns Chunks, score-ordered, best first."""
        vector = self.embedder.embed_one(query)
        result = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=k,
            query_filter=_build_filter(filters),
            with_payload=True,
            score_threshold=min_score,
        )
        return [_to_chunk(p) for p in result.points]

    def count(self) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        return self.client.count(self.collection, exact=True).count

    def stats(self) -> dict[str, Any]:
        if not self.client.collection_exists(self.collection):
            return {"collection": self.collection, "exists": False, "chunks": 0}
        info = self.client.get_collection(self.collection)
        return {
            "collection": self.collection,
            "exists": True,
            "chunks": self.count(),
            "status": str(info.status),
            "dimensions": DIMENSIONS,
        }

    def facet(self, key: str) -> dict[str, int]:
        """Distinct values of a payload key and their counts.

        Used by `stats` to show what is actually indexed. Deliberately exact --
        the corpus is a few thousand chunks, and an approximate answer to
        "what is in the index?" is not worth having.
        """
        counts: dict[str, int] = {}
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                limit=512,
                offset=offset,
                with_payload=[key],
                with_vectors=False,
            )
            for p in points:
                value = (p.payload or {}).get(key)
                for v in value if isinstance(value, list) else [value]:
                    counts[str(v)] = counts.get(str(v), 0) + 1
            if offset is None:
                break
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _build_filter(filters: dict[str, Any] | None) -> models.Filter | None:
    """`{"language": "pt", "origin": ["real", "synthetic"]}` -> a Qdrant filter.

    A scalar means "must equal"; a list means "must be one of". Keeping the
    caller's side of this a plain dict matters because §5 fixed `doc_search`'s
    signature as `filters: dict | None` and both the MCP server and the agent
    bind to it -- neither should have to import Qdrant types.
    """
    if not filters:
        return None
    must: list[models.Condition] = []
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            must.append(
                models.FieldCondition(key=key, match=models.MatchAny(any=[str(v) for v in value]))
            )
        else:
            must.append(models.FieldCondition(key=key, match=models.MatchValue(value=str(value))))
    return models.Filter(must=must) if must else None


def _to_chunk(point: Any) -> Chunk:
    p = point.payload or {}
    return Chunk(
        doc_id=str(p.get("doc_id", "")),
        chunk_id=str(p.get("chunk_id", "")),
        text=str(p.get("text", "")),
        score=float(point.score),
        classification=str(p.get("classification", "UNCLASSIFIED")),
        source_url=p.get("source_url"),
        title=p.get("title"),
    )
