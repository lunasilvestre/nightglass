"""MCP server — two transports over one set of functions.

    nightglass-mcp stdio                  # what Claude Desktop attaches to
    nightglass-mcp sse --host 0.0.0.0 --port 8001

stdio is the transport that matters for the demo. Claude Desktop runs on the
host, the server runs in the enclave, and they meet over a pipe:

    docker compose exec -T mcp nightglass-mcp stdio

which needs no published port — and cannot have one, since a container on an
`internal: true` network silently gets no host port mapping at all (verified;
see docker-compose.yml). A pipe through `docker exec` crosses the boundary
without opening one, which is the honest way to do it.

M0 registers one tool, `nightglass_status`. It exists to prove the transport
end-to-end before there is anything interesting to serve. The §5 six land here
at M4, importing the same functions the FastAPI layer calls.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from fastmcp import FastMCP

from nightglass import __version__
from nightglass.config import settings

mcp: FastMCP = FastMCP(
    name="nightglass",
    # Without this, serverInfo reports FastMCP's own version, which is what
    # Claude Desktop shows the user.
    version=__version__,
    instructions=(
        "Air-gapped SAR intelligence tools for dark-vessel detection. "
        "Every result carries provenance. A detection without an AIS match is a "
        "lead for an analyst to adjudicate, never a conclusion — do not describe "
        "one as an illegal, evading or suspicious vessel."
    ),
)


@mcp.tool
def nightglass_status() -> dict[str, Any]:
    """Report the active AOI, the models in use, and what this deployment may claim."""
    aoi = settings.aoi
    return {
        "version": __version__,
        "aoi": aoi.name,
        "bbox": aoi.bbox.as_list(),
        "ais_source": aoi.ais_source,
        "dark_rate_quotable": aoi.is_ground_truth,
        "caveat": (
            "Dark-vessel rates are only supportable over the Danish validation AOI, "
            "where point-level DMA AIS provides ground truth."
        ),
        "models": {
            "chat": settings.ollama_chat_model,
            "embed": settings.ollama_embed_model,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nightglass-mcp", description=__doc__)
    parser.add_argument("transport", choices=["stdio", "sse"], nargs="?", default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        # Nothing may write to stdout on this path — stdout *is* the protocol.
        mcp.run(transport="stdio")
        return 0

    _add_health_route()
    mcp.run(transport="sse", host=args.host, port=args.port)
    return 0


def _add_health_route() -> None:
    """Attach a plain `/health` beside the MCP routes.

    The container healthcheck needs an endpoint that answers without speaking
    MCP. FastMCP's decorator name has moved between releases, so resolve it at
    runtime rather than pinning the scaffold to one version.
    """
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    decorator = getattr(mcp, "custom_route", None)
    if decorator is None:
        print("fastmcp exposes no custom_route; /health not registered", file=sys.stderr)
        return

    @decorator("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "mcp", "version": __version__})


if __name__ == "__main__":
    raise SystemExit(main())
