"""The GFW reference layer: tile maths, the id scrape, and the comparison.

Nothing here needs a token or a network. `_ids` reads granule ids straight out
of vector-tile bytes with no protobuf decoder, which is a deliberate shortcut and
therefore worth pinning against bytes shaped like the real thing. `compare()` is
the part that decides what our detector's excess *is* — a second vessel or a
second detection of the same hull — and it was entirely untested.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from nightglass.config import BBox
from nightglass.schemas import Detection
from nightglass.spatial import gfw as G

KATTEGAT = BBox.parse("10.5,55.5,12.5,57.5", origin="test")
GRANULE = "S1A_IW_GRDH_1SDV_20260613T064316_20260613T064341_064947_082F4F_F72E"


def _mvt(*records: tuple[str, float, float]) -> bytes:
    """Bytes shaped like a tile's value table: ids as contiguous ASCII in binary."""
    body = b"\x1a\x08position\x12\x04"
    for granule, lon, lat in records:
        body += b"\x0a" + f"{granule};{lon};{lat}".encode("latin-1") + b"\x18\x02"
    return body


def _det(i: int, lon: float, lat: float, scene: str = GRANULE) -> Detection:
    return Detection(id=f"{scene}:{i}", scene_id=scene, lon=lon, lat=lat, length_m=40.0)


# -- tile maths ----------------------------------------------------------------


def test_tiles_cover_both_corners_of_the_box():
    tiles = G.tiles_for(KATTEGAT)
    assert (G.ZOOM, *G._tile(KATTEGAT.min_lon, KATTEGAT.max_lat, G.ZOOM)) in tiles
    assert (G.ZOOM, *G._tile(KATTEGAT.max_lon, KATTEGAT.min_lat, G.ZOOM)) in tiles
    assert len(tiles) == len(set(tiles))


def test_a_box_inside_one_tile_costs_one_request():
    tiny = BBox.parse("11.00,56.00,11.01,56.01", origin="test")
    assert len(G.tiles_for(tiny)) == 1


def test_tile_indices_increase_east_and_south():
    """Slippy y grows southward; getting that backwards fetches the wrong ocean
    and returns cleanly empty."""
    x_west, y_north = G._tile(10.0, 57.0, 9)
    x_east, y_south = G._tile(12.0, 55.0, 9)
    assert x_east > x_west
    assert y_south > y_north


def test_latitudes_beyond_the_mercator_limit_are_clamped_into_the_grid():
    n = 2**9
    for lat in (89.9, -89.9):
        _x, y = G._tile(0.0, lat, 9)
        assert 0 <= y <= n - 1


# -- the id scrape -------------------------------------------------------------


def test_ids_are_read_out_of_tile_bytes_without_decoding_the_tile():
    payload = _mvt((GRANULE, -9.123456, 38.654321), (GRANULE, -9.2, 38.7))
    assert G._ids(payload) == {
        (GRANULE, -9.123456, 38.654321),
        (GRANULE, -9.2, 38.7),
    }


def test_the_same_detection_appearing_twice_in_a_tile_is_one_id():
    payload = _mvt((GRANULE, -9.1, 38.6), (GRANULE, -9.1, 38.6))
    assert len(G._ids(payload)) == 1


def test_text_that_is_not_a_granule_id_is_not_matched():
    """The pattern is a Sentinel-1 granule name followed by two signed decimals,
    so a false positive would have to be a granule name that is not one."""
    assert G._ids(b"vessel_id;1.0;2.0 and SOMETHING_ELSE;3.0;4.0") == set()
    assert G._ids(b"") == set()


# -- fetching ------------------------------------------------------------------


def test_fetching_without_a_token_says_where_one_comes_from():
    with pytest.raises(G.GfwError, match="load-env.sh"):
        G.fetch_reference(
            KATTEGAT,
            datetime(2026, 6, 13, tzinfo=UTC),
            datetime(2026, 6, 14, tzinfo=UTC),
            token="",
        )


def _client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_tile_request_carries_the_dataset_window_and_filter():
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, content=_mvt((GRANULE, 11.0, 56.0)))

    with _client_for(handler) as client:
        G._tile_bytes(client, 9, 1, 2, "2026-06-13,2026-06-14", True)
        G._tile_bytes(client, 9, 1, 2, "2026-06-13,2026-06-14", None)

    assert seen[0].params["filters[0]"] == "matched='true'"
    assert "filters[0]" not in seen[1].params
    assert seen[0].params["format"] == "MVT"
    assert seen[0].params["date-range"] == "2026-06-13,2026-06-14"
    assert seen[0].path.endswith("/9/1/2")


def test_an_empty_tile_is_not_a_failure():
    """Most tiles over an AOI are open water or land. The first run treated 204
    as an error and failed on the first empty tile."""
    with _client_for(lambda _r: httpx.Response(204)) as client:
        assert G._tile_bytes(client, 9, 1, 2, "w", None) == b""


def test_any_other_status_is_raised_with_its_body():
    with _client_for(lambda _r: httpx.Response(401, text="token expired")) as client, pytest.raises(
        G.GfwError, match="HTTP 401 token expired"
    ):
        G._tile_bytes(client, 9, 1, 2, "w", None)


def _fetch_with(monkeypatch, tiles: dict[tuple[int, int, int, Any], bytes], bbox=KATTEGAT):
    """Serve canned tile bytes through the client `fetch_reference` builds."""

    def handler(request: httpx.Request) -> httpx.Response:
        z, x, y = (int(p) for p in request.url.path.rsplit("/", 3)[-3:])
        flag = request.url.params.get("filters[0]")
        matched = None if flag is None else flag.endswith("'true'")
        return httpx.Response(200, content=tiles.get((z, x, y, matched), b""))

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    return G.fetch_reference(
        bbox, datetime(2026, 6, 13, tzinfo=UTC), datetime(2026, 6, 14, tzinfo=UTC), token="tok"
    )


def test_the_two_filtered_halves_must_partition_the_unfiltered_tile(monkeypatch):
    """A free consistency check on every tile: if GFW ever changes the filter
    semantics this fails loudly rather than silently halving a published count."""
    tiles: dict[tuple[int, int, int, Any], bytes] = {}
    z, x, y = G.tiles_for(KATTEGAT)[0]
    tiles[(z, x, y, True)] = _mvt((GRANULE, 11.0, 56.0))
    tiles[(z, x, y, False)] = b""
    tiles[(z, x, y, None)] = _mvt((GRANULE, 11.0, 56.0), (GRANULE, 11.1, 56.1))

    with pytest.raises(G.GfwError, match="does not partition the unfiltered set"):
        _fetch_with(monkeypatch, tiles)


def test_detections_outside_the_box_are_dropped_and_the_flag_is_kept(monkeypatch):
    """z9 tiles over-cover an AOI by design, so the box is applied after the
    fetch — and `matched` travels with each detection because it is GFW's
    assessment, not ours."""
    tiles: dict[tuple[int, int, int, Any], bytes] = {}
    for z, x, y in G.tiles_for(KATTEGAT):
        tiles[(z, x, y, True)] = b""
        tiles[(z, x, y, False)] = b""
        tiles[(z, x, y, None)] = b""
    z, x, y = G.tiles_for(KATTEGAT)[0]
    inside = (GRANULE, 11.0, 56.0)
    outside = (GRANULE, 4.0, 56.0)  # over-covered by the tile, outside the AOI
    tiles[(z, x, y, True)] = _mvt(inside)
    tiles[(z, x, y, False)] = _mvt(outside)
    tiles[(z, x, y, None)] = _mvt(inside, outside)

    found = _fetch_with(monkeypatch, tiles)

    assert [(d.lon, d.lat, d.matched) for d in found] == [(11.0, 56.0, True)]


# -- on disk -------------------------------------------------------------------


def test_a_reference_round_trips_with_its_provenance(tmp_path):
    detections = [
        G.GfwDetection(GRANULE, 11.0, 56.0, True),
        G.GfwDetection(GRANULE, 11.1, 56.1, False),
    ]
    path = G.write_reference(
        detections,
        G.reference_path(tmp_path, "Kattegat"),
        aoi="kattegat",
        bbox=KATTEGAT,
        start=datetime(2026, 6, 13, tzinfo=UTC),
        end=datetime(2026, 6, 14, tzinfo=UTC),
    )
    back, doc = G.load_reference(path)

    assert path.name == "gfw_kattegat.json"
    assert back == detections
    assert doc["licence"] == G.LICENCE
    assert doc["granules"] == [GRANULE]
    assert doc["bbox"] == KATTEGAT.as_list()
    assert doc["count"] == 2


def test_a_missing_reference_names_the_provisioning_step(tmp_path):
    with pytest.raises(G.GfwError, match="make fetch-gfw"):
        G.load_reference(tmp_path / "absent.json")


# -- the comparison ------------------------------------------------------------


def test_pairing_is_greedy_and_each_of_ours_is_claimed_once():
    """Two of theirs near one of ours: the first takes it, the second is theirs
    only. An optimal matcher would be more code defending a difference that does
    not arise at 500 m over open water."""
    ours = [_det(0, 11.0000, 56.0000)]
    theirs = [
        G.GfwDetection(GRANULE, 11.0005, 56.0000, True),
        G.GfwDetection(GRANULE, 11.0010, 56.0000, False),
    ]
    report = G.compare(ours, theirs, granule_id=GRANULE)

    assert report.agreed == 1
    assert report.theirs_only == 1
    assert report.ours_only == 0
    assert report.their_matched_on_agreed == 1
    assert report.their_unmatched_on_agreed == 0
    assert report.recall == 0.5


def test_a_detection_beyond_the_radius_is_not_a_pairing():
    ours = [_det(0, 11.0, 56.0)]
    theirs = [G.GfwDetection(GRANULE, 11.05, 56.0, True)]  # ~3 km away
    report = G.compare(ours, theirs, granule_id=GRANULE, radius_m=500.0)

    assert report.agreed == 0
    assert (report.ours_only, report.theirs_only) == (1, 1)
    assert report.median_distance_m == 0.0
    assert report.recall == 0.0


def test_detections_from_another_granule_are_not_compared():
    """The comparison is only meaningful over the identical granule; a neighbour
    from the same pass would report a detector that misses almost everything."""
    ours = [_det(0, 11.0, 56.0, scene="OTHER_GRANULE")]
    theirs = [G.GfwDetection(GRANULE, 11.0, 56.0, True)]
    report = G.compare(ours, theirs, granule_id=GRANULE)

    assert report.ours == 0 and report.theirs == 1
    assert report.theirs_only == 1 and report.agreed == 0


def test_an_extra_detection_on_top_of_an_agreed_one_reads_as_a_fragment():
    """The diagnostic that turns 'we saw more than they did' into a statement
    about what the excess is. A hull fragment sits on top of a detection we both
    found; a second vessel does not."""
    ours = [_det(0, 11.0, 56.0), _det(1, 11.0005, 56.0), _det(2, 11.05, 56.0)]
    theirs = [G.GfwDetection(GRANULE, 11.0, 56.0, True)]
    report = G.compare(ours, theirs, granule_id=GRANULE)

    assert report.agreed == 1 and report.ours_only == 2
    assert report.likely_fragments == 1  # only the one within 200 m
    assert report.distinct_targets == 2  # 3 detections, 2 distinct targets
    text = report.render()
    assert "one vessel is being counted" in text
    assert "Raise DetectorConfig.merge_radius_m." in text


def test_an_isolated_excess_is_reported_as_sensitivity_not_double_counting():
    ours = [_det(0, 11.0, 56.0), _det(1, 11.05, 56.0)]
    theirs = [G.GfwDetection(GRANULE, 11.0, 56.0, False)]
    report = G.compare(ours, theirs, granule_id=GRANULE)

    assert report.likely_fragments == 0
    assert report.distinct_targets == report.ours
    assert "residue is genuinely isolated" in report.render()


def test_an_empty_side_short_circuits_without_claiming_agreement():
    report = G.compare([], [G.GfwDetection(GRANULE, 11.0, 56.0, True)], granule_id=GRANULE)
    assert (report.agreed, report.ours_only, report.theirs_only) == (0, 0, 1)
    assert report.ours_only_to_agreed_m == []

    report = G.compare([_det(0, 11.0, 56.0)], [], granule_id=GRANULE)
    assert (report.agreed, report.ours_only, report.theirs_only) == (0, 1, 0)


def test_the_report_refuses_to_call_either_column_ground_truth():
    ours = [_det(0, 11.0, 56.0)]
    theirs = [G.GfwDetection(GRANULE, 11.0002, 56.0, False)]
    text = G.compare(ours, theirs, granule_id=GRANULE).render()

    assert "Neither column is ground truth" in text
    assert "their computation, cited not reproduced (CC BY-NC 4.0)" in text
    assert "unmatched   1" in text


def test_the_median_separation_is_the_middle_of_an_even_set():
    assert G._median([]) == 0.0
    assert G._median([3.0, 1.0, 2.0]) == 2.0
    assert G._median([1.0, 2.0, 3.0, 4.0]) == 2.5
