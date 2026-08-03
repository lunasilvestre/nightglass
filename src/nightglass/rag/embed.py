"""bge-m3 embeddings, from the Ollama instance inside the enclave.

§"Two things worth keeping", item 1: *the embedding model runs locally. Calling
a hosted embedding API is the most common way an air gap gets broken in
practice.* So this module talks to `http://ollama:11434` on the internal network
and there is no code path in it that could reach anywhere else -- the host comes
from config, and config resolves to a service name that only exists on a network
with no route out.

bge-m3 needs no instruction prefix on either side (unlike the English bge
models, which want "Represent this sentence..." on queries only). Query and
document go through the same call, which is why cross-lingual retrieval works
symmetrically: measured PT<->EN same-meaning cosine 0.842 against 0.283 for
same-language different-meaning.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

DIMENSIONS = 1024  # bge-m3, dense vector


class EmbeddingError(RuntimeError):
    """The embedder could not be reached, or returned something unusable."""


@dataclass
class Embedder:
    """Batched embeddings with retries.

    Ollama serialises requests, so batching is not about parallelism -- it is
    about not paying HTTP and scheduling overhead per chunk. Batch size is
    modest because the whole batch shares one timeout, and one slow batch
    failing is cheaper to retry than one slow corpus.
    """

    host: str
    model: str
    timeout: float = 180.0
    batch_size: int = 16
    retries: int = 3

    def embed(self, texts: list[str], *, progress: bool = False) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        with httpx.Client(timeout=self.timeout) as client:
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start : start + self.batch_size]
                out.extend(self._embed_batch(client, batch))
                if progress:
                    print(
                        f"    embedded {min(start + len(batch), len(texts))}/{len(texts)}",
                        flush=True,
                    )
        return out

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def _embed_batch(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        url = f"{self.host.rstrip('/')}/api/embed"
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = client.post(url, json={"model": self.model, "input": batch})
                r.raise_for_status()
                vectors = r.json().get("embeddings")
                if not isinstance(vectors, list) or len(vectors) != len(batch):
                    raise EmbeddingError(
                        f"{url}: expected {len(batch)} embeddings, got "
                        f"{len(vectors) if isinstance(vectors, list) else type(vectors).__name__}"
                    )
                if vectors and len(vectors[0]) != DIMENSIONS:
                    # Worth failing loudly on: a different dimension means a
                    # different model, and a collection built at one dimension
                    # cannot accept vectors of another. Better here than as an
                    # opaque Qdrant rejection halfway through an ingest.
                    raise EmbeddingError(
                        f"{self.model} returned {len(vectors[0])}-dim vectors, "
                        f"expected {DIMENSIONS}. Wrong embedding model?"
                    )
                return vectors
            except (httpx.HTTPError, EmbeddingError) as exc:
                last = exc
                if attempt < self.retries - 1:
                    time.sleep(2**attempt)
        raise EmbeddingError(
            f"embedding failed after {self.retries} attempts against {url}: {last}"
        ) from last
