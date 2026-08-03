"""FastAPI application.

Health and readiness from M0; `doc_search` -- the first of §5's six tools -- from
M2. The remaining five arrive at M4 on top of the spatial layer. Their contracts
are already fixed in `nightglass.schemas` so the MCP server and the agent can
bind to them without waiting for the implementations.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException, Response, status

from nightglass import __version__
from nightglass.config import settings
from nightglass.schemas import Chunk

app = FastAPI(
    title="NIGHTGLASS",
    version=__version__,
    summary="Air-gapped SAR intelligence assistant",
)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness. Deliberately dependency-free.

    A healthcheck that reaches out to other services turns one slow dependency
    into a cascade of unhealthy containers and a compose file that never
    converges. Dependency state is reported by `/ready` instead.
    """
    return {"status": "ok", "service": "api", "version": __version__}


@app.get("/ready")
async def ready(response: Response) -> dict[str, Any]:
    """Readiness — can this service actually reach the enclave it needs?"""
    checks = dict(
        zip(
            ("ollama", "qdrant", "postgis"),
            await asyncio.gather(
                _check_http(f"{settings.ollama_host.rstrip('/')}/api/tags"),
                _check_http(f"{settings.qdrant_url}/readyz"),
                _check_tcp(settings.postgres_host, settings.postgres_port),
            ),
        )
    )
    ok = all(c["ok"] for c in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ok, "checks": checks}


@app.get("/config")
async def config() -> dict[str, Any]:
    """The resolved AOI. No secrets — nothing here is one.

    Useful in the demo: it shows the deployment is genuinely parameterised
    rather than carrying a hardcoded bbox (§3.1).
    """
    aoi = settings.aoi
    return {
        "aoi": aoi.name,
        "bbox": aoi.bbox.as_list(),
        "ais_source": aoi.ais_source,
        "ais_source_is_ground_truth": aoi.is_ground_truth,
        "overpass_utc": {
            "descending": aoi.pass_descending,
            "ascending": aoi.pass_ascending,
        },
        "min_length_m": settings.min_length_m,
        "models": {
            "chat": settings.ollama_chat_model,
            "embed": settings.ollama_embed_model,
        },
    }


@functools.lru_cache(maxsize=1)
def _document_index() -> Any:
    """Built on first use, not at import.

    Constructing it at module scope would make the API's start-up depend on
    Qdrant and Ollama being up, which is exactly the cascade `/health` is
    dependency-free to avoid. Imported lazily too, so the M0 health path does
    not pull in the vector client at all.
    """
    from nightglass.rag.embed import Embedder
    from nightglass.rag.index import DocumentIndex

    return DocumentIndex(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedder=Embedder(host=settings.ollama_host, model=settings.ollama_embed_model),
    )


@app.post("/tools/doc_search", response_model=list[Chunk])
async def doc_search(
    query: str = Body(..., embed=True),
    k: int = Body(default=0, embed=True),
    filters: dict[str, Any] | None = Body(default=None, embed=True),
) -> list[Chunk]:
    """§5: `doc_search(query, k=8, filters) -> list[Chunk]`.

    Returns chunks carrying doc_id, chunk_id, classification and source URL —
    not bare text. Everything downstream needs those fields to cite, and a
    retriever that dropped them would push the work onto its callers until one
    of them forgot.

    Runs the blocking Qdrant and Ollama clients in a worker thread rather than
    on the event loop, so one slow embedding call cannot stall the healthcheck
    that keeps this container marked up.
    """
    try:
        return await asyncio.to_thread(
            _document_index().search,
            query,
            k=k or settings.rag_top_k,
            filters=filters,
            min_score=settings.rag_min_score,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{type(exc).__name__}: {exc}. Has `make ingest` been run?",
        ) from exc


async def _check_http(url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
        return {"ok": r.status_code < 500, "detail": f"HTTP {r.status_code}"}
    except Exception as exc:  # noqa: BLE001 — a readiness probe reports, never raises
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


async def _check_tcp(host: str, port: int) -> dict[str, Any]:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
        writer.close()
        await writer.wait_closed()
        return {"ok": True, "detail": f"{host}:{port} accepting"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
