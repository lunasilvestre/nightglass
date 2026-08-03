"""The six §5 tools, defined once.

    stac_search      catalogue query over stac.scenes            (M3, wrapped here)
    detect_vessels   our own CFAR detector over a granule        (M3, wrapped here)
    ais_match        the hand-checked space-time join            (M3, wrapped here)
    doc_search       Qdrant retrieval with citations             (M2, wrapped here)
    correlate        the three above, chained with provenance    (M4)
    draft_intrep     the report, and the guard on its numbers    (M4)

Both service surfaces import from here and neither owns a tool: `api/main.py`
puts them behind HTTP for anything that speaks REST, `mcp/server.py` puts the
same functions behind MCP for Claude Desktop and the local model. A tool that
existed twice would be a tool that behaved differently depending on who asked,
and §M4's whole point is that the same surface works connected and disconnected.

The functions are synchronous and block. That is deliberate: they are database
calls and, once per scene, a 14-second read of a SAR granule. FastAPI wraps them
in `asyncio.to_thread` so a slow one cannot stall the healthcheck that keeps the
container marked up; FastMCP runs sync tools in its own worker thread already.
"""

from __future__ import annotations

from nightglass.tools.base import ToolError
from nightglass.tools.documents import doc_search, document_index
from nightglass.tools.intrep import (
    DETECTOR_PRECISION_VALIDATED,
    RateVerdict,
    draft_intrep,
    rate_verdict,
    scrub_rate_claims,
    states_a_rate,
)
from nightglass.tools.spatial import ais_match, correlate, detect_vessels, stac_search

__all__ = [
    "DETECTOR_PRECISION_VALIDATED",
    "RateVerdict",
    "ToolError",
    "ais_match",
    "correlate",
    "detect_vessels",
    "doc_search",
    "document_index",
    "draft_intrep",
    "rate_verdict",
    "scrub_rate_claims",
    "stac_search",
    "states_a_rate",
]
