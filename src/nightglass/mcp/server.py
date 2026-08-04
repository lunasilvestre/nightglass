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

The tools are `nightglass.tools`, imported, not reimplemented — the FastAPI app
serves the same six functions. What this file adds is the descriptions, and they
are load-bearing in a way an HTTP route's docstring is not: a model chooses what
to call from them, and every hedge this project cares about has to survive
being read by something that will otherwise write "suspicious vessel". Hence
`instructions` on the server and the framing sentence in each tool.

One tool's argument shape deliberately differs from its §5 Python signature.
`draft_intrep(correlation, context_chunks)` takes objects; over MCP it takes the
identifiers of a correlation and re-derives it, because asking a model to paste
a 60-detection result back into its own next call is a way to lose the result.
Re-deriving is honest here only because `detect_vessels` reuses the recorded
run: the second correlation is the first one, not a recomputation that renumbers
everything. The Python contract §M5's agent binds to is untouched.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Any

from fastmcp import FastMCP

from nightglass import __version__
from nightglass.config import settings
from nightglass.schemas import INTREP, Chunk, CorrelationResult, Detection, Match, Scene

mcp: FastMCP = FastMCP(
    name="nightglass",
    # Without this, serverInfo reports FastMCP's own version, which is what
    # Claude Desktop shows the user.
    version=__version__,
    instructions=(
        "Air-gapped SAR intelligence tools for dark-vessel detection. "
        "Every result carries provenance. A detection without an AIS match is a "
        "lead for an analyst to adjudicate, never a conclusion — do not describe "
        "one as an illegal, evading or suspicious vessel. Never state a rate, "
        "percentage or proportion of detections lacking AIS: this deployment's "
        "detector precision is not validated, so counts are supportable and "
        "proportions are not. Typical chain: stac_search to find a scene, "
        "detect_vessels over it, ais_match on the detection ids — or correlate "
        "to do all three at once. doc_search for doctrine and regulation."
    ),
)


@mcp.tool
def nightglass_status() -> dict[str, Any]:
    """Report the active AOI, the models in use, and what this deployment may claim.

    Worth calling first: it names the area of interest every other tool operates
    over, so a query outside it can be recognised as out of scope rather than
    returned as an empty result.
    """
    from nightglass.tools.intrep import DETECTOR_PRECISION_VALIDATED, PRECISION_CAVEAT

    aoi = settings.aoi
    return {
        "version": __version__,
        "aoi": aoi.name,
        "bbox": aoi.bbox.as_list(),
        "ais_source": aoi.ais_source,
        "ais_source_is_ground_truth": aoi.is_ground_truth,
        "detector_precision_validated": DETECTOR_PRECISION_VALIDATED,
        "dark_rate_quotable": aoi.is_ground_truth and DETECTOR_PRECISION_VALIDATED,
        "caveat": PRECISION_CAVEAT,
        "models": {
            "chat": settings.ollama_chat_model,
            "embed": settings.ollama_embed_model,
        },
    }


@mcp.tool
def stac_search(bbox: list[float], start: datetime, end: datetime) -> list[Scene]:
    """Find Sentinel-1 SAR scenes covering an area during a time window.

    bbox is [min_lon, min_lat, max_lon, max_lat] in WGS84 degrees. start and end
    are ISO-8601 UTC instants. Returns the granules held in the local catalogue —
    this is an air-gapped deployment, so it searches what is on disk, not a
    remote archive, and an empty result means no scene was staged for that window
    rather than that no satellite passed.
    """
    from nightglass.tools import stac_search as _stac_search

    return _stac_search(bbox, start, end)


@mcp.tool
def detect_vessels(scene_id: str, min_length_m: float = 15.0) -> list[Detection]:
    """Run our own CFAR vessel detector over one scene and return the candidates.

    scene_id comes from stac_search. Detections are this system's own
    computation over the radar pixels, not a published detection layer. Each
    carries a position, an estimated length, an ambiguous-by-180° hull heading
    where the blob is elongated enough to support one, and the detector run that
    produced it.

    Reuses an identical recorded run when one exists, so detection ids are stable
    across calls and can be handed to ais_match. Reading the pixels afresh takes
    about 14 seconds.
    """
    from nightglass.tools import detect_vessels as _detect_vessels

    return _detect_vessels(scene_id, min_length_m)


@mcp.tool
def ais_match(
    detections: list[str], time_window_min: int = 60, radius_m: float = 500.0
) -> list[Match]:
    """Match detections against AIS, returning matched and unmatched together.

    detections are ids from detect_vessels. Each vessel's AIS position is
    interpolated onto the exact acquisition instant and then displaced along the
    satellite's flight direction to account for the Doppler shift SAR imposes on
    a moving target — a ship at 12 knots is drawn several hundred metres from
    where it was, which is most of the match radius.

    Pass every id from detect_vessels in one call. Matching a subset answers
    correctly about that subset and says nothing about the scene, and the two
    are indistinguishable in the result.

    radius_m is the entire boundary between "matched" and "dark" and moves the
    answer by a factor of three across plausible values — over the Danish
    validation scene, 45 matched at 500 m, 20 at 100 m, 8 at 50 m. 500 m is the
    validated setting. Change it only to sweep it deliberately, and never to
    make a result come out differently.

    status="dark" means no AIS correspondence was found in this feed at this
    instant. It does not mean the vessel was not transmitting. Report such a
    detection as a lead for adjudication, with its position, and never as a
    proportion of the total.
    """
    from nightglass.tools import ais_match as _ais_match

    return _ais_match(detections, time_window_min, radius_m)


@mcp.tool
def doc_search(
    query: str, k: int = 8, filters: dict[str, Any] | None = None
) -> list[Chunk]:
    """Search the local document corpus — doctrine, regulation, procedure.

    Returns passages with the chunk id to cite them by, their classification
    marking, and a source URL where one exists. Use it for what a term means,
    what a regulation requires, or how a finding should be handled — not for
    facts about a specific scene, which come from the spatial tools.

    filters narrows by payload field, e.g. {"language": "pt"} or
    {"origin": "synthetic"}. If nothing relevant comes back, say so: the corpus
    is deliberately narrow and an unsupported answer is worse than a refusal.
    """
    from nightglass.tools import doc_search as _doc_search

    return _doc_search(query, k, filters)


@mcp.tool
def correlate(
    bbox: list[float],
    start: datetime,
    end: datetime,
    min_length_m: float = 15.0,
    scene_id: str | None = None,
) -> CorrelationResult:
    """Chain scene search, vessel detection and AIS matching over one area and window.

    The one-call version of stac_search → detect_vessels → ais_match, returning
    every step's output with its provenance rather than a count.

    Bounded to one scene per call, because reading a granule takes about 14
    seconds. When the window contains several, the most recent is correlated and
    the others come back in `scenes` with a note naming them; pass scene_id to
    pick a different one.

    `rate_is_quotable` on the result is about the AIS feed only. Even when it is
    true, do not state a proportion of unmatched detections — call
    nightglass_status for why.
    """
    from nightglass.tools import correlate as _correlate

    return _correlate(bbox, start, end, min_length_m, scene_id=scene_id)


@mcp.tool
def draft_intrep(
    bbox: list[float],
    start: datetime,
    end: datetime,
    query: str | None = None,
    min_length_m: float = 15.0,
    scene_id: str | None = None,
) -> INTREP:
    """Draft a structured intelligence report over an area and window.

    Correlates the area, retrieves supporting documents for `query` if one is
    given, and returns an INTREP whose every claim carries the scene, detection
    and document-chunk ids that support it. The report is always marked
    DRAFT — NOT RELEASABLE; only a human review gate may release it.

    Takes the same arguments as correlate rather than a correlation object,
    because re-deriving one is a database read: detect_vessels reuses the
    recorded detector run, so this report is drawn from the same detections,
    under the same ids, as the correlate call you may already have made.

    """
    from nightglass.tools import correlate as _correlate
    from nightglass.tools import doc_search as _doc_search
    from nightglass.tools import draft_intrep as _draft_intrep

    correlation = _correlate(bbox, start, end, min_length_m, scene_id=scene_id)
    chunks = _doc_search(query) if query else []
    return _draft_intrep(correlation, chunks)


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
