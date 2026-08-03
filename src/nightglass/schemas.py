"""The §5 tool contracts, as types.

These signatures are load-bearing: the FastAPI layer, the MCP server and the
LangGraph agent all bind to them, so they are defined once here and imported
everywhere rather than restated per service.

The recurring shape is that **every result carries its provenance alongside its
value**, never a bare number. §7's reasoning: intelligence analysts grade each
input on source reliability and information credibility, and output that cannot
be traced cannot be graded, so it cannot enter the intelligence cycle however
fluent it reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MatchStatus = Literal["matched", "dark"]


class Provenance(BaseModel):
    """Where a value came from. Attached to results, not logged and forgotten."""

    source: str = Field(description="e.g. 'ASF', 'DMA', 'GFW', 'nightglass-detector'")
    retrieved_at: datetime
    source_url: str | None = None
    licence: str | None = Field(
        default=None,
        description="GFW is CC BY-NC 4.0; DMA requires a verbatim attribution line.",
    )
    note: str | None = None


class Scene(BaseModel):
    """A Sentinel-1 GRD granule. `stac_search` returns these."""

    id: str
    acquisition_time: datetime
    mode: str = Field(description="IW for everything used here")
    polarizations: list[str]
    footprint_wkt: str
    incidence_angle: float | None = None
    provenance: Provenance | None = None


class Detection(BaseModel):
    """One candidate vessel from our own detector, not from a published layer."""

    id: str
    scene_id: str
    lon: float
    lat: float
    length_m: float | None = None
    heading_deg: float | None = None
    confidence: float | None = None
    provenance: Provenance | None = None


class Match(BaseModel):
    """A detection paired against AIS — or explicitly not paired.

    `status="dark"` means *no AIS correspondence was found in this feed*, which
    is not the same as *no AIS was transmitted*. Innocent explanations abound:
    revisit gaps, terrestrial coverage limits, transponder failure, class B low
    power, vessels not required to carry AIS at all. `ais_source` and
    `source_is_ground_truth` travel with the match so a downstream report cannot
    quietly turn a thinned feed into a dark-vessel rate.
    """

    detection_id: str
    mmsi: str | None = None
    distance_m: float | None = None
    time_delta_s: float | None = None
    status: MatchStatus
    ais_source: str | None = None
    source_is_ground_truth: bool = False
    provenance: Provenance | None = None


class Chunk(BaseModel):
    """A retrieved document fragment. Classification propagates from the source."""

    doc_id: str
    chunk_id: str
    text: str
    score: float
    classification: str = Field(
        default="UNCLASSIFIED",
        description="Synthetic corpus documents are marked UNCLASSIFIED // SYNTHETIC.",
    )
    source_url: str | None = None
    title: str | None = None


class CorrelationResult(BaseModel):
    """What `correlate` returns: stac_search → detect_vessels → ais_match, with
    the full provenance chain preserved rather than flattened to a count."""

    aoi_name: str
    bbox: list[float]
    start: datetime
    end: datetime
    scenes: list[Scene] = Field(default_factory=list)
    detections: list[Detection] = Field(default_factory=list)
    matches: list[Match] = Field(default_factory=list)

    @property
    def dark(self) -> list[Match]:
        return [m for m in self.matches if m.status == "dark"]

    @property
    def rate_is_quotable(self) -> bool:
        """False unless every match came from a ground-truth feed.

        Guards §7's honesty requirement structurally: a dark *rate* may only be
        quoted over Denmark. Over Portugal the defensible claim is "here are N
        detections I matched, with the space–time reasoning shown".
        """
        return bool(self.matches) and all(m.source_is_ground_truth for m in self.matches)


class Claim(BaseModel):
    """One assertion in a report, with the references that support it.

    A claim with no refs is not a claim, it is a guess — validation belongs here
    rather than in a prompt instruction that the model may ignore.
    """

    text: str
    scene_ids: list[str] = Field(default_factory=list)
    detection_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)

    @property
    def is_supported(self) -> bool:
        return bool(self.scene_ids or self.detection_ids or self.chunk_ids)


class GroundedAnswer(BaseModel):
    """What §M2 returns: an answer whose every claim maps to retrieved chunks.

    Two fields carry the milestone's whole argument.

    `answered` is decided **after** the model has spoken, not by it. Every claim
    is checked against the set of chunk IDs actually retrieved, citations the
    model invented are moved to `dropped_citations`, and claims left with no
    surviving reference are discarded. If nothing survives, this is a refusal
    regardless of how confident the generated text was. A prompt instruction not
    to make things up is a request; this is a check.

    `classification` is the combined marking of the chunks actually cited, so a
    report drawing on synthetic material says `UNCLASSIFIED // SYNTHETIC`
    whether or not the drafter remembers to. §7's propagation requirement,
    computed rather than asserted.
    """

    question: str
    answered: bool
    claims: list[Claim] = Field(default_factory=list)
    refusal: str | None = None
    retrieved: list[Chunk] = Field(default_factory=list)
    classification: str = "UNCLASSIFIED"
    dropped_citations: list[str] = Field(default_factory=list)
    model: str = ""
    generated_at: datetime | None = None

    @property
    def cited_chunk_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for c in self.claims:
            for cid in c.chunk_ids:
                seen[cid] = None
        return list(seen)

    @property
    def cited_documents(self) -> list[str]:
        return list(dict.fromkeys(cid.split("#", 1)[0] for cid in self.cited_chunk_ids))


class INTREP(BaseModel):
    """The drafted report. Marked not-releasable until the M5 human gate passes."""

    title: str
    generated_at: datetime
    aoi_name: str
    classification: str = "UNCLASSIFIED"
    releasable: bool = False
    claims: list[Claim] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    @property
    def marking(self) -> str:
        base = self.classification
        return base if self.releasable else f"{base} // DRAFT — NOT RELEASABLE"

    @property
    def unsupported_claims(self) -> list[Claim]:
        return [c for c in self.claims if not c.is_supported]
