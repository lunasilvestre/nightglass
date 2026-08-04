"""FastAPI application.

Health and readiness from M0; `doc_search` -- the first of §5's six tools --
from M2; the remaining five at M4.

This layer owns no logic. Every endpoint is the same four lines around a
function in `nightglass.tools`: run the blocking call in a worker thread,
translate a `ToolError` into a 503 carrying its remediation hint, and declare
the `response_model` from `nightglass.schemas` so the contract the MCP server
and the LangGraph agent bind to is enforced at the edge rather than trusted.
`doc_search` established that shape at M2 and the other five copy it exactly.

The worker thread is not incidental. These calls block -- a PostGIS join, an
embedding round trip, and once per scene a 14-second read of a SAR granule --
and on the event loop one of them would stall the healthcheck that keeps this
container marked up, turning a slow query into an unhealthy service.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException, Response, status

from nightglass import __version__
from nightglass.config import settings
from nightglass.schemas import INTREP, Chunk, CorrelationResult, Detection, Match, Scene
from nightglass.tools import ToolError

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


async def _tool(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a §5 tool off the event loop, and give a failure a fixable message.

    `ToolError` is the tools' way of saying "I cannot answer, and here is what
    would let me" -- an unmigrated schema, an uningested corpus, a scene that
    was never catalogued. It becomes a 503 rather than a 500 because every one
    of those is a dependency that is not ready yet, and the hint travels in the
    detail so the same sentence reaches an analyst, an HTTP client and a model
    deciding what to call next.
    """
    try:
        return await asyncio.to_thread(lambda: fn(*args, **kwargs))
    except ToolError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc


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
    """
    from nightglass.tools import doc_search as _doc_search

    return await _tool(_doc_search, query, k or settings.rag_top_k, filters)


@app.post("/tools/stac_search", response_model=list[Scene])
async def stac_search(
    bbox: list[float] = Body(..., embed=True),
    start: datetime = Body(..., embed=True),
    end: datetime = Body(..., embed=True),
) -> list[Scene]:
    """§5: `stac_search(bbox, start, end) -> list[Scene]`.

    bbox is `[min_lon, min_lat, max_lon, max_lat]` — GIS and STAC order.
    """
    from nightglass.tools import stac_search as _stac_search

    return await _tool(_stac_search, bbox, start, end)


@app.post("/tools/detect_vessels", response_model=list[Detection])
async def detect_vessels(
    scene_id: str = Body(..., embed=True),
    min_length_m: float = Body(default=15.0, embed=True),
    recompute: bool = Body(default=False, embed=True),
) -> list[Detection]:
    """§5: `detect_vessels(scene_id, min_length_m=15.0) -> list[Detection]`.

    Reuses an identical recorded detector run when one exists, so ids stay
    stable across calls; otherwise reads the pixels, which takes about 14 s.
    """
    from nightglass.tools import detect_vessels as _detect_vessels

    return await _tool(_detect_vessels, scene_id, min_length_m, recompute=recompute)


@app.post("/tools/ais_match", response_model=list[Match])
async def ais_match(
    detections: list[str] = Body(..., embed=True),
    time_window_min: int = Body(default=60, embed=True),
    radius_m: float = Body(default=500.0, embed=True),
) -> list[Match]:
    """§5: `ais_match(detections, time_window_min=60, radius_m=500.0) -> list[Match]`.

    Returns matched *and* unmatched in one result set, deliberately: a bare list
    of unmatched detections cannot be checked against a base rate, and §3.2's
    sanity check is the thing that says whether any of this is working.
    """
    from nightglass.tools import ais_match as _ais_match

    return await _tool(_ais_match, detections, time_window_min, radius_m)


@app.post("/tools/correlate", response_model=CorrelationResult)
async def correlate(
    bbox: list[float] = Body(..., embed=True),
    start: datetime = Body(..., embed=True),
    end: datetime = Body(..., embed=True),
    min_length_m: float = Body(default=15.0, embed=True),
    scene_id: str | None = Body(default=None, embed=True),
) -> CorrelationResult:
    """§5: `correlate(bbox, start, end, min_length_m=15.0) -> CorrelationResult`.

    Bounded to one scene per call — the detector takes ~14 s over a granule, and
    the alternative (return a run id, let the client poll) needs job state, which
    is the hidden state §5 rules out. Scenes the search found but did not
    correlate come back with a provenance note saying how to select them.
    """
    from nightglass.tools import correlate as _correlate

    return await _tool(_correlate, bbox, start, end, min_length_m, scene_id=scene_id)


@app.post("/tools/draft_intrep", response_model=INTREP)
async def draft_intrep(
    correlation: CorrelationResult = Body(..., embed=True),
    context_chunks: list[Chunk] = Body(default_factory=list, embed=True),
    narrative: bool = Body(default=True, embed=True),
) -> INTREP:
    """§5: `draft_intrep(correlation, context_chunks) -> INTREP`.

    Always returns `releasable=False`; §M5's human gate is the only thing that
    may flip it. Never states a proportion of unmatched detections — see
    `tools/intrep.py` for the two independent reasons it may not.
    """
    from nightglass.tools import draft_intrep as _draft_intrep

    return await _tool(
        _draft_intrep,
        correlation,
        context_chunks,
        narrative=narrative,
    )


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
