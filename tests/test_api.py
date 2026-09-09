"""The FastAPI edge: readiness aggregation, and the failure translation.

The routes own no logic, so what is worth pinning is the two things they do own.
`/ready` decides one boolean from three independent probes and must set 503 on
the response as well as reporting it in the body — a readiness endpoint that
answers 200 with `"ready": false` is one an orchestrator will believe. And
`_tool` turns every failure into a 503: a `ToolError` keeps its remediation
sentence, and an unexpected exception becomes `Type: message` rather than a 500
with a traceback the caller cannot act on.

The tools themselves are replaced. The assertions are about status codes and
response shapes, which are the API's own decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nightglass import __version__
from nightglass import tools as T
from nightglass.api import main as A
from nightglass.config import settings
from nightglass.schemas import INTREP, Chunk, Claim, CorrelationResult, Detection, Match, Scene
from nightglass.tools.base import ToolError


@pytest.fixture
def client() -> TestClient:
    return TestClient(A.app)


@pytest.fixture
def kattegat(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AOI_KATTEGAT_BBOX", "10.5,55.5,12.5,57.5")
    monkeypatch.setenv("AOI_KATTEGAT_AIS_SOURCE", "dma")
    monkeypatch.setattr(settings, "aoi_name", "kattegat")


def _scene() -> Scene:
    return Scene(
        id="s1",
        acquisition_time=datetime(2026, 7, 17, 5, 23, tzinfo=UTC),
        mode="IW",
        polarizations=["VH"],
        footprint_wkt="POLYGON((10 55, 12 55, 12 57, 10 57, 10 55))",
    )


def _correlation() -> CorrelationResult:
    return CorrelationResult(
        aoi_name="kattegat",
        bbox=[10.5, 55.5, 12.5, 57.5],
        start=datetime(2026, 7, 17, tzinfo=UTC),
        end=datetime(2026, 7, 18, tzinfo=UTC),
        scenes=[_scene()],
        detections=[Detection(id="s1:0", scene_id="s1", lon=11.0, lat=56.0)],
        matches=[Match(detection_id="s1:0", status="dark")],
    )


# -- health, config ------------------------------------------------------------


def test_health_answers_without_touching_a_dependency(client):
    """Liveness is dependency-free on purpose: one slow service must not make
    every container unhealthy. Nothing is monkeypatched here and it still answers."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "api", "version": __version__}


def test_config_reports_the_resolved_aoi_and_whether_it_is_ground_truth(client, kattegat):
    body = client.get("/config").json()
    assert body["aoi"] == "kattegat"
    assert body["bbox"] == [10.5, 55.5, 12.5, 57.5]
    assert body["ais_source_is_ground_truth"] is True
    assert set(body["overpass_utc"]) == {"descending", "ascending"}
    assert set(body["models"]) == {"chat", "embed"}


def test_config_reports_a_demonstration_feed_as_not_ground_truth(client, monkeypatch):
    monkeypatch.setenv("AOI_LISBON_BBOX", "-10.5,38.0,-8.5,39.5")
    monkeypatch.setenv("AOI_LISBON_AIS_SOURCE", "aisstream")
    monkeypatch.setattr(settings, "aoi_name", "lisbon")
    assert client.get("/config").json()["ais_source_is_ground_truth"] is False


# -- readiness -----------------------------------------------------------------


async def _ok(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {"ok": True, "detail": "HTTP 200"}


async def _down(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {"ok": False, "detail": "ConnectError: [Errno -2] Name or service not known"}


def test_ready_is_200_only_when_every_check_passes(client, monkeypatch):
    monkeypatch.setattr(A, "_check_http", _ok)
    monkeypatch.setattr(A, "_check_tcp", _ok)
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["ready"] is True
    assert sorted(r.json()["checks"]) == ["ollama", "postgis", "qdrant"]


def test_one_failing_dependency_makes_the_status_code_503(client, monkeypatch):
    """The body and the status code must agree — an orchestrator reads the code."""
    monkeypatch.setattr(A, "_check_http", _ok)
    monkeypatch.setattr(A, "_check_tcp", _down)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["ready"] is False
    assert r.json()["checks"]["postgis"]["ok"] is False
    assert r.json()["checks"]["ollama"]["ok"] is True


def test_a_probe_reports_a_failure_instead_of_raising():
    """`_check_tcp` against a port nothing is listening on: a readiness probe
    that raised would take the endpoint down instead of describing it."""
    import asyncio

    out = asyncio.run(A._check_tcp("127.0.0.1", 1))
    assert out["ok"] is False
    assert ":" in out["detail"]  # "ExceptionType: message"


# -- the tool routes -----------------------------------------------------------


def test_doc_search_forwards_the_configured_k_when_none_is_given(client, monkeypatch):
    """`k=0` in the body means "use the deployment's setting" — the route must not
    ask the index for zero chunks."""
    seen: dict[str, Any] = {}

    def fake(query: str, k: int, filters: Any) -> list[Chunk]:
        seen.update(query=query, k=k, filters=filters)
        return [Chunk(doc_id="d", chunk_id="d#1", text="t", score=0.9)]

    monkeypatch.setattr(T, "doc_search", fake)
    monkeypatch.setattr(settings, "rag_top_k", 8)

    r = client.post("/tools/doc_search", json={"query": "dark vessel"})
    assert r.status_code == 200
    assert seen == {"query": "dark vessel", "k": 8, "filters": None}
    assert r.json()[0]["chunk_id"] == "d#1"

    client.post("/tools/doc_search", json={"query": "q", "k": 3, "filters": {"language": "pt"}})
    assert seen["k"] == 3 and seen["filters"] == {"language": "pt"}


def test_stac_search_parses_the_window_into_datetimes(client, monkeypatch):
    seen: dict[str, Any] = {}

    def fake(bbox: list[float], start: datetime, end: datetime) -> list[Scene]:
        seen.update(bbox=bbox, start=start, end=end)
        return [_scene()]

    monkeypatch.setattr(T, "stac_search", fake)
    r = client.post(
        "/tools/stac_search",
        json={
            "bbox": [10.5, 55.5, 12.5, 57.5],
            "start": "2026-07-17T00:00:00Z",
            "end": "2026-07-18T00:00:00Z",
        },
    )
    assert r.status_code == 200
    assert isinstance(seen["start"], datetime) and seen["start"].tzinfo is not None
    assert seen["bbox"] == [10.5, 55.5, 12.5, 57.5]
    assert r.json()[0]["id"] == "s1"


def test_detect_vessels_passes_recompute_as_a_keyword(client, monkeypatch):
    seen: dict[str, Any] = {}

    def fake(scene_id: str, min_length_m: float, recompute: bool = False) -> list[Detection]:
        seen.update(scene_id=scene_id, min_length_m=min_length_m, recompute=recompute)
        return []

    monkeypatch.setattr(T, "detect_vessels", fake)
    client.post("/tools/detect_vessels", json={"scene_id": "s1", "recompute": True})
    assert seen == {"scene_id": "s1", "min_length_m": 15.0, "recompute": True}


def test_ais_match_returns_matched_and_dark_together(client, monkeypatch):
    """§3.2's sanity check needs both halves in one result set; a route that
    returned only the darks would make the base rate uncheckable."""
    monkeypatch.setattr(
        T,
        "ais_match",
        lambda ids, window, radius: [
            Match(detection_id=ids[0], status="matched", mmsi="219000000", distance_m=120.0),
            Match(detection_id=ids[1], status="dark"),
        ],
    )
    r = client.post("/tools/ais_match", json={"detections": ["s1:0", "s1:1"]})
    assert [m["status"] for m in r.json()] == ["matched", "dark"]


def test_draft_intrep_narrative_flag_reaches_the_tool(client, monkeypatch):
    """The declared way to turn the model off. `make intrep` relies on the other
    one (no query, no chunks); this route has an explicit switch and it must not
    be dropped on the way through."""
    seen: dict[str, Any] = {}

    def fake(correlation: CorrelationResult, chunks: list[Chunk], narrative: bool = True) -> INTREP:
        seen.update(narrative=narrative, chunks=len(chunks), aoi=correlation.aoi_name)
        return INTREP(
            title="INTREP",
            generated_at=datetime(2026, 7, 17, tzinfo=UTC),
            aoi_name=correlation.aoi_name,
            claims=[Claim(text="1 detection.", scene_ids=["s1"])],
        )

    monkeypatch.setattr(T, "draft_intrep", fake)
    r = client.post(
        "/tools/draft_intrep",
        json={"correlation": _correlation().model_dump(mode="json"), "narrative": False},
    )
    assert r.status_code == 200
    assert seen == {"narrative": False, "chunks": 0, "aoi": "kattegat"}
    assert r.json()["releasable"] is False


def test_correlate_returns_the_full_chain_not_a_count(client, monkeypatch):
    monkeypatch.setattr(T, "correlate", lambda *a, **k: _correlation())
    body = client.post(
        "/tools/correlate",
        json={
            "bbox": [10.5, 55.5, 12.5, 57.5],
            "start": "2026-07-17T00:00:00Z",
            "end": "2026-07-18T00:00:00Z",
        },
    ).json()
    assert body["scenes"][0]["id"] == "s1"
    assert body["detections"][0]["id"] == "s1:0"
    assert body["matches"][0]["status"] == "dark"


# -- the failure translation ---------------------------------------------------

ROUTES = [
    ("/tools/doc_search", {"query": "q"}, "doc_search"),
    (
        "/tools/stac_search",
        {"bbox": [1, 2, 3, 4], "start": "2026-07-17T00:00:00Z", "end": "2026-07-18T00:00:00Z"},
        "stac_search",
    ),
    ("/tools/detect_vessels", {"scene_id": "s1"}, "detect_vessels"),
    ("/tools/ais_match", {"detections": ["s1:0"]}, "ais_match"),
    (
        "/tools/correlate",
        {"bbox": [1, 2, 3, 4], "start": "2026-07-17T00:00:00Z", "end": "2026-07-18T00:00:00Z"},
        "correlate",
    ),
]


@pytest.mark.parametrize(("path", "body", "tool"), ROUTES)
def test_a_tool_error_becomes_a_503_carrying_its_remediation(client, monkeypatch, path, body, tool):
    def refuse(*_a: Any, **_k: Any):
        raise ToolError("the corpus is not ingested; run `make ingest`")

    monkeypatch.setattr(T, tool, refuse)
    r = client.post(path, json=body)
    assert r.status_code == 503
    assert r.json()["detail"] == "the corpus is not ingested; run `make ingest`"


@pytest.mark.parametrize(("path", "body", "tool"), ROUTES)
def test_an_unexpected_exception_is_a_503_never_a_500(client, monkeypatch, path, body, tool):
    """A dependency that is not ready is not a bug in this service, and a 500
    with a traceback tells the caller nothing it can act on."""

    def boom(*_a: Any, **_k: Any):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(T, tool, boom)
    r = client.post(path, json=body)
    assert r.status_code == 503
    assert r.json()["detail"] == "ZeroDivisionError: division by zero"


def test_a_malformed_body_is_rejected_by_the_contract_not_the_tool(client, monkeypatch):
    """The response and request models are the enforcement point §5 asks for:
    the tool is never reached, so it cannot be handed a bbox of strings."""
    called = False

    def fake(*_a: Any, **_k: Any):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(T, "stac_search", fake)
    r = client.post("/tools/stac_search", json={"bbox": [1, 2, 3, 4], "start": "not-a-time"})
    assert r.status_code == 422
    assert called is False
