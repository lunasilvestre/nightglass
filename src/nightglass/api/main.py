"""FastAPI application.

M0 scaffold: health and readiness only. The six tools of §5 arrive at M4, on
top of the spatial layer (M3) and the document index (M2). Their contracts are
already fixed in `nightglass.schemas` so the MCP server and the agent can bind
to them without waiting for the implementations.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import FastAPI, Response, status

from nightglass import __version__
from nightglass.config import settings

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
