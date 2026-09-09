"""The MCP surface, called as plain functions.

`@mcp.tool` registers a function and hands it back unchanged, so every tool here
is exercised without a transport, a client or a subprocess. Two of them are more
than delegation and those are what this file is about: `nightglass_status`
composes the deployment's "may I state a rate" answer out of two independent
facts, and `draft_intrep` re-derives a correlation from identifiers and only
retrieves documents when it was given a query — the branch `make intrep` relies
on to make zero model calls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from nightglass import __version__
from nightglass import tools as T
from nightglass.config import settings
from nightglass.mcp import server as S
from nightglass.schemas import INTREP, Chunk, CorrelationResult, Detection, Match, Scene


@pytest.fixture
def kattegat(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AOI_KATTEGAT_BBOX", "10.5,55.5,12.5,57.5")
    monkeypatch.setenv("AOI_KATTEGAT_AIS_SOURCE", "dma")
    monkeypatch.setattr(settings, "aoi_name", "kattegat")


@pytest.fixture
def lisbon(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AOI_LISBON_BBOX", "-10.5,38.0,-8.5,39.5")
    monkeypatch.setenv("AOI_LISBON_AIS_SOURCE", "aisstream")
    monkeypatch.setattr(settings, "aoi_name", "lisbon")


def _correlation(aoi: str = "kattegat") -> CorrelationResult:
    return CorrelationResult(
        aoi_name=aoi,
        bbox=[10.5, 55.5, 12.5, 57.5],
        start=datetime(2026, 7, 17, tzinfo=UTC),
        end=datetime(2026, 7, 18, tzinfo=UTC),
        scenes=[
            Scene(
                id="s1",
                acquisition_time=datetime(2026, 7, 17, 5, 23, tzinfo=UTC),
                mode="IW",
                polarizations=["VH"],
                footprint_wkt="POLYGON((10 55, 12 55, 12 57, 10 57, 10 55))",
            )
        ],
        detections=[Detection(id="s1:0", scene_id="s1", lon=11.0, lat=56.0)],
        matches=[Match(detection_id="s1:0", status="dark")],
    )


def _intrep() -> INTREP:
    return INTREP(
        title="INTREP", generated_at=datetime(2026, 7, 17, tzinfo=UTC), aoi_name="kattegat"
    )


# -- status --------------------------------------------------------------------


def test_ground_truth_ais_alone_does_not_make_a_rate_quotable(kattegat):
    """Both halves of the fraction have to be sound. Denmark's feed is ground
    truth and the rate is still not quotable, because detector precision is not
    validated — the composite, not either flag on its own."""
    status = S.nightglass_status()
    assert status["ais_source_is_ground_truth"] is True
    assert status["detector_precision_validated"] is False
    assert status["dark_rate_quotable"] is False
    assert "precision is not validated" in status["caveat"]
    assert status["version"] == __version__
    assert status["bbox"] == [10.5, 55.5, 12.5, 57.5]


def test_a_demonstration_feed_is_not_quotable_either(lisbon):
    status = S.nightglass_status()
    assert status["ais_source_is_ground_truth"] is False
    assert status["dark_rate_quotable"] is False


def test_the_server_instructions_forbid_the_two_things_that_matter():
    """A model reads these instead of the docs. Both hedges the project cares
    about have to survive being read by something that would otherwise write
    'suspicious vessel'."""
    text = S.mcp.instructions or ""
    assert "never a conclusion" in text
    assert "Never state a rate, percentage or proportion" in text


# -- delegation ----------------------------------------------------------------


def test_stac_search_forwards_its_arguments_unchanged(monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        T, "stac_search", lambda bbox, start, end: seen.update(bbox=bbox, start=start, end=end) or []
    )
    start, end = datetime(2026, 7, 17, tzinfo=UTC), datetime(2026, 7, 18, tzinfo=UTC)
    assert S.stac_search([1.0, 2.0, 3.0, 4.0], start, end) == []
    assert seen == {"bbox": [1.0, 2.0, 3.0, 4.0], "start": start, "end": end}


def test_detect_vessels_defaults_to_the_documented_threshold(monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        T, "detect_vessels", lambda scene_id, min_length: seen.update(
            scene_id=scene_id, min_length=min_length
        ) or []
    )
    S.detect_vessels("s1")
    assert seen == {"scene_id": "s1", "min_length": 15.0}


def test_the_ais_match_description_names_the_tolerance_as_fixed():
    """The docstring is the tool's interface for a model, and it is the only
    place this surface says the radius is not the model's to choose."""
    doc = " ".join((S.ais_match.__doc__ or "").split())
    assert "500 m is the validated setting" in doc
    assert "never as a proportion of the total" in doc


def test_doc_search_passes_the_filter_dict_through(monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        T, "doc_search", lambda q, k, f: seen.update(q=q, k=k, f=f) or [
            Chunk(doc_id="d", chunk_id="d#1", text="t", score=0.4)
        ]
    )
    out = S.doc_search("dark vessel", filters={"language": "pt"})
    assert seen == {"q": "dark vessel", "k": 8, "f": {"language": "pt"}}
    assert out[0].chunk_id == "d#1"


# -- draft_intrep: the branch `make intrep` depends on -------------------------


def test_no_query_means_no_retrieval_and_no_model_call(monkeypatch):
    """`draft_intrep` with no query passes an empty chunk list, and
    `tools/intrep.py`'s `if narrative and chunks:` gate then makes no ollama
    call. That is what makes `make intrep` deterministic, so it is pinned here."""
    seen: dict[str, Any] = {}

    def no_search(*_a: Any, **_k: Any):
        raise AssertionError("doc_search must not be called without a query")

    monkeypatch.setattr(T, "correlate", lambda *a, **k: _correlation())
    monkeypatch.setattr(T, "doc_search", no_search)
    monkeypatch.setattr(
        T, "draft_intrep", lambda c, chunks: seen.update(chunks=chunks) or _intrep()
    )

    out = S.draft_intrep([10.5, 55.5, 12.5, 57.5], datetime(2026, 7, 17, tzinfo=UTC),
                         datetime(2026, 7, 18, tzinfo=UTC))
    assert seen["chunks"] == []
    assert out.releasable is False


def test_a_query_adds_documentary_context(monkeypatch):
    seen: dict[str, Any] = {}
    chunk = Chunk(doc_id="d", chunk_id="d#1", text="t", score=0.7)
    monkeypatch.setattr(T, "correlate", lambda *a, **k: _correlation())
    monkeypatch.setattr(T, "doc_search", lambda q: seen.setdefault("query", q) and [chunk])
    monkeypatch.setattr(
        T, "draft_intrep", lambda c, chunks: seen.update(chunks=chunks) or _intrep()
    )

    S.draft_intrep(
        [10.5, 55.5, 12.5, 57.5],
        datetime(2026, 7, 17, tzinfo=UTC),
        datetime(2026, 7, 18, tzinfo=UTC),
        query="AIS carriage requirements",
    )
    assert seen["query"] == "AIS carriage requirements"
    assert seen["chunks"] == [chunk]


def test_the_scene_selection_reaches_correlate(monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        T,
        "correlate",
        lambda bbox, start, end, min_length_m, scene_id=None: seen.update(
            min_length_m=min_length_m, scene_id=scene_id
        )
        or _correlation(),
    )
    monkeypatch.setattr(T, "draft_intrep", lambda c, chunks: _intrep())
    S.draft_intrep(
        [1.0, 2.0, 3.0, 4.0],
        datetime(2026, 7, 17, tzinfo=UTC),
        datetime(2026, 7, 18, tzinfo=UTC),
        min_length_m=25.0,
        scene_id="s2",
    )
    assert seen == {"min_length_m": 25.0, "scene_id": "s2"}


# -- main() --------------------------------------------------------------------


def test_the_default_transport_is_stdio(monkeypatch):
    """Claude Desktop attaches over a pipe through `docker compose exec -T`, so
    the no-argument invocation must be stdio and must not register an HTTP route."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(S.mcp, "run", lambda **kw: calls.append(kw))
    monkeypatch.setattr(S, "_add_health_route", lambda: calls.append({"health": True}))

    assert S.main([]) == 0
    assert calls == [{"transport": "stdio"}]


def test_sse_registers_the_health_route_first(monkeypatch):
    """The container healthcheck needs an endpoint that does not speak MCP, and
    only the served transport has anywhere to put one."""
    calls: list[Any] = []
    monkeypatch.setattr(S.mcp, "run", lambda **kw: calls.append(kw))
    monkeypatch.setattr(S, "_add_health_route", lambda: calls.append("health"))

    assert S.main(["sse", "--port", "9001"]) == 0
    assert calls[0] == "health"
    assert calls[1] == {"transport": "sse", "host": "0.0.0.0", "port": 9001}


def test_an_unknown_transport_is_refused():
    with pytest.raises(SystemExit):
        S.main(["grpc"])


def test_a_fastmcp_without_custom_route_degrades_to_a_warning(monkeypatch, capsys):
    """The decorator's name has moved between fastmcp releases. Missing it costs
    the health route, not the server."""
    monkeypatch.setattr(S.mcp, "custom_route", None, raising=False)
    S._add_health_route()
    assert "custom_route" in capsys.readouterr().err


def test_the_health_route_is_registered_when_the_decorator_exists(monkeypatch):
    registered: list[tuple[str, list[str]]] = []

    def decorator(path: str, methods: list[str]):
        registered.append((path, methods))
        return lambda fn: fn

    monkeypatch.setattr(S.mcp, "custom_route", decorator, raising=False)
    S._add_health_route()
    assert registered == [("/health", ["GET"])]
