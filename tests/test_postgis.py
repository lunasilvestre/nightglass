"""§M3's SQL against a real PostGIS, on hand-built rows.

These are a **regression guard for the query**, not a validation of the physics.
Every expected number below is computed in the test from the same formula the
SQL uses, with the formula written out, so agreement proves the query implements
that formula over PostGIS's geography type — nothing more. The claim that the
formula is right, and in particular that its sign is right, is
`make validate-shift` against DMA ground truth, and `dark_vessels.sql:41-44`
records that the sign was derived one way and measured the other.

Run them with a throwaway container; the four lines are in docs/testing.md. They
skip when `NIGHTGLASS_TEST_POSTGIS` is unset and fail — never skip — when it is
set and the database cannot be reached.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from nightglass.config import BBox
from nightglass.schemas import Detection
from nightglass.spatial import db as DB
from nightglass.spatial.ais import AisPosition
from nightglass.spatial.geodesy import offset_m

pytestmark = pytest.mark.postgis

SCENE = "S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13"
ACQ = datetime(2026, 7, 17, 5, 23, 36, tzinfo=UTC)

# The Kattegat granule's own geometry, as `stac.scenes` holds it.
RANGE_BEARING = 286.842
AZIMUTH_BEARING = 196.842
INCIDENCE = 38.7
R_OVER_V = 115.0

DET_LON, DET_LAT = 11.0, 56.0
RADIUS_M = 500.0
WINDOW_MIN = 11.0


def shift_m(sog_ms: float, cog_deg: float) -> float:
    """The azimuth displacement, written out.

        v_ground_range = SOG · cos(COG − range_bearing)
        v_los          = v_ground_range · sin(incidence)
        shift          = −(R/V) · v_los

    A vessel opening the range is drawn backward along the flight path.
    """
    v_ground_range = sog_ms * math.cos(math.radians(cog_deg - RANGE_BEARING))
    v_los = v_ground_range * math.sin(math.radians(INCIDENCE))
    return -R_OVER_V * v_los


def true_position_for(shift: float) -> tuple[float, float]:
    """Where a vessel must be for its apparent position to land on the detection.

    The SQL walks the vessel `shift` metres along the azimuth bearing, so the
    truth is the detection walked back the other way.
    """
    lon, lat = offset_m(
        np.array(DET_LON), np.array(DET_LAT), np.array(AZIMUTH_BEARING), np.array(-shift)
    )
    return float(lon), float(lat)


def _item(scene_id: str = SCENE, acq: datetime = ACQ) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": scene_id,
        "collection": "sentinel-1-grd",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[10.0, 55.0], [12.0, 55.0], [12.0, 57.0], [10.0, 57.0], [10.0, 55.0]]],
        },
        "properties": {
            "datetime": acq.isoformat(),
            "start_datetime": (acq - timedelta(seconds=12)).isoformat(),
            "end_datetime": (acq + timedelta(seconds=12)).isoformat(),
            "platform": "S1D",
            "sar:instrument_mode": "IW",
            "sar:product_type": "GRD",
            "sar:polarizations": ["VV", "VH"],
            "sat:orbit_state": "descending",
            "sat:relative_orbit": 168,
            "sat:absolute_orbit": 3709,
            "view:incidence_angle": INCIDENCE,
            "nightglass:platform_heading_deg": AZIMUTH_BEARING,
            "nightglass:platform_speed_ms": 7588.0,
            "nightglass:range_bearing_deg": RANGE_BEARING,
            "nightglass:azimuth_bearing_deg": AZIMUTH_BEARING,
        },
    }


class _Run:
    def __init__(self, scene_id: str = SCENE, detector: str = "nightglass-cfar") -> None:
        self.scene_id, self.detector = scene_id, detector
        self.version, self.polarization = "0.3.1", "VH"
        self.started_at, self.seconds = ACQ, 14.0
        self.aoi_bbox = [10.5, 55.5, 12.5, 57.5]
        self.coastline = "gshhg-h-L1"
        self.parameters = {"threshold_db": 6.0}
        self.pixels_examined = self.pixels_water = 10
        self.pixels_land_masked = self.candidates = 0
        self.detections = self.rejected_small = self.rejected_large = 0
        self.rejected_outside_aoi = self.rejected_on_coastline = 0
        self.water_sigma0_db, self.nesz_db = -22.0, -24.0


class _Measurement:
    def __init__(self, detection_id: str) -> None:
        self.detection_id = detection_id
        self.width_m, self.row, self.col = 12.0, 8000.0, 13000.0
        self.incidence_deg, self.r_over_v_s = INCIDENCE, R_OVER_V
        self.cfar_margin_db = 7.5


class _Feed:
    """A source adapter over hand-built positions."""

    def __init__(self, positions: list[AisPosition], *, ground_truth: bool = True) -> None:
        self._positions, self.is_ground_truth = positions, ground_truth

    def positions(self, _bbox: Any, _start: Any, _end: Any):
        yield from self._positions


def _position(
    mmsi: str,
    lon: float,
    lat: float,
    when: datetime,
    *,
    sog: float | None = 0.0,
    cog: float | None = None,
) -> AisPosition:
    return AisPosition(
        mmsi=mmsi, timestamp=when, lon=lon, lat=lat, sog_ms=sog, cog_deg=cog, source="dma"
    )


def _bracket(
    mmsi: str, lon: float, lat: float, *, sog: float | None = 0.0, cog: float | None = None
) -> list[AisPosition]:
    """Two reports either side of the acquisition instant, at one position.

    The interpolation is then exact: `truth` is that position. A vessel with only
    one side is dropped rather than extrapolated, which is a case of its own
    below."""
    return [
        _position(mmsi, lon, lat, ACQ - timedelta(seconds=60), sog=sog, cog=cog),
        _position(mmsi, lon, lat, ACQ + timedelta(seconds=60), sog=sog, cog=cog),
    ]


def _load(conn, detections: list[tuple[str, float, float]], feed: _Feed | None = None) -> None:
    DB.upsert_scene(conn, _item(), "/data/raw/sar/x.zip")
    objects = [
        Detection(id=i, scene_id=SCENE, lon=lon, lat=lat, length_m=40.0, confidence=0.9)
        for i, lon, lat in detections
    ]
    DB.insert_detections(conn, _Run(), objects, [_Measurement(d.id) for d in objects])
    if feed is not None:
        DB.insert_positions(
            conn, feed, BBox.parse("9,54,14,59", origin="test"), ACQ - timedelta(hours=1),
            ACQ + timedelta(hours=1),
        )


def _dark(conn, **kw: Any):
    return DB.dark_query(
        conn, scene_id=SCENE, radius_m=RADIUS_M, window_min=WINDOW_MIN, **kw
    )


# -- schema --------------------------------------------------------------------


def test_the_migration_is_re_runnable(postgis):
    """It is a migration, not an init hook: applying it twice must be a no-op."""
    first = DB.migrate(postgis)
    second = DB.migrate(postgis)
    assert first == second == ["001_schema.sql"]
    assert DB.counts(postgis) == {"scenes": 0, "runs": 0, "detections": 0, "ais positions": 0}


def test_drop_clears_the_data_and_leaves_the_schema_usable(postgis):
    _load(postgis, [("s1:0", DET_LON, DET_LAT)])
    assert DB.counts(postgis)["detections"] == 1

    DB.migrate(postgis, drop=True)
    assert DB.counts(postgis) == {"scenes": 0, "runs": 0, "detections": 0, "ais positions": 0}


# -- the catalogue -------------------------------------------------------------


def test_cataloguing_the_same_granule_twice_updates_it_rather_than_failing(postgis):
    DB.upsert_scene(postgis, _item(), "/data/raw/sar/first.zip")
    DB.upsert_scene(postgis, _item(), "/data/raw/sar/second.zip")

    row = DB.scene_row(postgis, SCENE)
    assert DB.counts(postgis)["scenes"] == 1
    assert row["granule_path"] == "/data/raw/sar/second.zip"
    assert row["item"]["id"] == SCENE  # the Item is kept whole beside the columns
    assert row["range_bearing_deg"] == pytest.approx(RANGE_BEARING)


def test_stac_search_is_a_real_spatial_and_temporal_query(postgis):
    DB.upsert_scene(postgis, _item(), "/x.zip")
    DB.upsert_scene(postgis, _item("OTHER", ACQ + timedelta(days=3)), "/y.zip")

    inside = DB.stac_search(
        postgis, BBox.parse("10.5,55.5,12.5,57.5", origin="t"),
        ACQ - timedelta(hours=1), ACQ + timedelta(hours=1),
    )
    assert [r["id"] for r in inside] == [SCENE]
    assert inside[0]["footprint_wkt"].startswith("POLYGON")

    elsewhere = DB.stac_search(
        postgis, BBox.parse("-10.5,38.0,-8.5,39.5", origin="t"),
        ACQ - timedelta(days=7), ACQ + timedelta(days=7),
    )
    assert elsewhere == []

    both = DB.stac_search(
        postgis, BBox.parse("10.5,55.5,12.5,57.5", origin="t"),
        ACQ - timedelta(days=7), ACQ + timedelta(days=7),
    )
    assert [r["id"] for r in both] == [SCENE, "OTHER"]  # ordered by acquisition time


# -- writes --------------------------------------------------------------------


def test_re_running_the_detector_replaces_the_previous_run(postgis):
    """Two runs over the same granule are one observation computed twice."""
    _load(postgis, [("s1:0", DET_LON, DET_LAT), ("s1:1", 11.1, 56.1)])
    _load(postgis, [("s1:0", DET_LON, DET_LAT)])

    counts = DB.counts(postgis)
    assert counts["runs"] == 1
    assert counts["detections"] == 1


def test_a_second_detector_keeps_its_own_run(postgis):
    _load(postgis, [("s1:0", DET_LON, DET_LAT)])
    other = _Run(detector="other-detector")
    detection = Detection(id="s1:other", scene_id=SCENE, lon=11.2, lat=56.2)
    DB.insert_detections(postgis, other, [detection], [_Measurement(detection.id)])

    assert DB.counts(postgis) == {
        "scenes": 1, "runs": 2, "detections": 2, "ais positions": 0
    }


def test_the_primary_key_enforces_the_dedup_rule_the_loader_already_applies(postgis):
    """71% of raw DMA rows are rebroadcast duplicates. The key is the rule, so a
    second source loading overlapping rows cannot double-count them either."""
    duplicated = _bracket("219000000", DET_LON, DET_LAT) * 3
    _load(postgis, [("s1:0", DET_LON, DET_LAT)], _Feed(duplicated))

    assert DB.counts(postgis)["ais positions"] == 2  # six rows in, two distinct


# -- the dark-vessel join ------------------------------------------------------


def test_a_stationary_vessel_at_the_detection_matches_with_no_shift(postgis):
    _load(
        postgis,
        [("s1:0", DET_LON, DET_LAT)],
        _Feed(_bracket("219000000", DET_LON, DET_LAT, sog=0.0, cog=90.0)),
    )
    rows, summary = _dark(postgis)

    assert len(rows) == 1
    assert rows[0]["status"] == "matched"
    assert rows[0]["mmsi"] == "219000000"
    assert rows[0]["azimuth_shift_m"] == pytest.approx(0.0, abs=1e-6)
    assert rows[0]["distance_m"] == pytest.approx(0.0, abs=1.0)
    assert summary["dark"] == 0


def test_a_moving_vessel_is_matched_where_the_radar_drew_it_and_dark_without_the_correction(
    postgis,
):
    """The whole point of the correction. At 10 m/s straight along the range
    direction the shift is −(115 s)·(10 m/s)·sin(38.7°) = −719 m: more than the
    match radius, so the same vessel is matched with the correction on and dark
    with it off. A flipped sign would put the apparent position 1.4 km the other
    way, which is well outside the radius too."""
    sog, cog = 10.0, RANGE_BEARING
    shift = shift_m(sog, cog)
    assert shift == pytest.approx(-719.0, abs=1.0)
    assert abs(shift) > RADIUS_M

    lon, lat = true_position_for(shift)
    _load(
        postgis,
        [("s1:0", DET_LON, DET_LAT)],
        _Feed(_bracket("219000001", lon, lat, sog=sog, cog=cog)),
    )

    corrected, summary = _dark(postgis)
    assert corrected[0]["status"] == "matched"
    assert corrected[0]["azimuth_shift_m"] == pytest.approx(shift, abs=1.0)
    assert corrected[0]["distance_m"] == pytest.approx(0.0, abs=5.0)
    assert corrected[0]["distance_uncorrected_m"] == pytest.approx(abs(shift), abs=5.0)
    assert summary["azimuth correction helped"] == "1/1"

    uncorrected, off_summary = _dark(postgis, correct_azimuth=False)
    assert uncorrected[0]["status"] == "dark"
    assert off_summary["azimuth correction"] == "OFF"


def test_a_vessel_steaming_along_track_is_not_displaced_at_all(postgis):
    """No range rate, no Doppler offset, no shift — the case a symmetric radius
    gets right by accident."""
    sog, cog = 10.0, AZIMUTH_BEARING
    assert shift_m(sog, cog) == pytest.approx(0.0, abs=1e-9)

    _load(
        postgis,
        [("s1:0", DET_LON, DET_LAT)],
        _Feed(_bracket("219000002", DET_LON, DET_LAT, sog=sog, cog=cog)),
    )
    rows, _summary = _dark(postgis)
    assert rows[0]["status"] == "matched"
    assert rows[0]["azimuth_shift_m"] == pytest.approx(0.0, abs=1e-6)


def test_a_vessel_reporting_no_course_still_gets_a_chance_to_match(postgis):
    """A NULL course means "no correction", not "no candidate". Filtering on
    `cog_deg IS NOT NULL` dropped 322 of 907 vessels — a third of the fleet —
    and turned every one of them into a spurious dark detection."""
    _load(
        postgis,
        [("s1:0", DET_LON, DET_LAT)],
        _Feed(_bracket("219000003", DET_LON, DET_LAT, sog=3.0, cog=None)),
    )
    rows, _summary = _dark(postgis)

    assert rows[0]["status"] == "matched"
    assert rows[0]["azimuth_shift_m"] == pytest.approx(0.0)


def test_a_vessel_reported_only_before_the_acquisition_is_dropped_not_extrapolated(postgis):
    """An extrapolated position that then fails to match manufactures a dark
    detection out of a gap in the feed."""
    one_sided = [
        _position("219000004", DET_LON, DET_LAT, ACQ - timedelta(seconds=120)),
        _position("219000004", DET_LON, DET_LAT, ACQ - timedelta(seconds=60)),
    ]
    _load(postgis, [("s1:0", DET_LON, DET_LAT)], _Feed(one_sided))
    rows, summary = _dark(postgis)

    assert DB.counts(postgis)["ais positions"] == 2  # the reports are loaded, just one-sided
    assert rows[0]["status"] == "dark"
    assert rows[0]["mmsi"] is None
    assert summary["dark"] == 1


def test_no_ais_anywhere_near_is_a_dark_detection(postgis):
    _load(
        postgis,
        [("s1:0", DET_LON, DET_LAT)],
        _Feed(_bracket("219000005", 11.5, 56.5)),  # ~40 km away
    )
    rows, summary = _dark(postgis)

    assert rows[0]["status"] == "dark"
    assert summary["unmatched_fraction"] == "100.0%"


def test_a_vessel_too_far_out_to_survive_the_pre_filter_is_dark_however_fast_it_was(postgis):
    """`dark_vessels.sql` pre-filters candidates with `ST_DWithin(..., radius_m +
    2000)` before the shift is applied, so a vessel whose *true* position is
    further out than that can never match even when its apparent position lands
    exactly on the detection. That caps the correctable line-of-sight speed; the
    envelope is a property of the join and docs/limitations.md states it."""
    sog, cog = 44.5, RANGE_BEARING  # ~86 kn: fast enough to fall outside the envelope
    shift = shift_m(sog, cog)
    assert abs(shift) > RADIUS_M + 2000.0

    lon, lat = true_position_for(shift)
    _load(
        postgis,
        [("s1:0", DET_LON, DET_LAT)],
        _Feed(_bracket("219000006", lon, lat, sog=sog, cog=cog)),
    )
    assert _dark(postgis)[0][0]["status"] == "dark"

    # The row is there and the shift is right: widening the radius widens the
    # pre-filter with it, and the same vessel then matches.
    wide, _summary = DB.dark_query(
        postgis, scene_id=SCENE, radius_m=3000.0, window_min=WINDOW_MIN
    )
    assert wide[0]["status"] == "matched"
    assert wide[0]["distance_m"] == pytest.approx(0.0, abs=20.0)


def test_the_nearest_apparent_position_wins_and_each_detection_appears_once(postgis):
    near = _bracket("219000007", *true_position_for(0.0))
    far_lon, far_lat = offset_m(
        np.array(DET_LON), np.array(DET_LAT), np.array(90.0), np.array(300.0)
    )
    far = _bracket("219000008", float(far_lon), float(far_lat))
    _load(postgis, [("s1:0", DET_LON, DET_LAT)], _Feed(near + far))

    rows, _summary = _dark(postgis)
    assert len(rows) == 1
    assert rows[0]["mmsi"] == "219000007"
    assert rows[0]["distance_m"] < 300.0


def test_every_detection_comes_back_matched_or_not_so_the_base_rate_is_computable(postgis):
    """A bare list of unmatched detections cannot be checked against §3.2's ~5%.
    The LEFT JOIN is what makes the denominator available."""
    _load(
        postgis,
        [("s1:0", DET_LON, DET_LAT), ("s1:1", 11.2, 56.2), ("s1:2", 11.3, 56.3)],
        _Feed(_bracket("219000009", DET_LON, DET_LAT)),
    )
    rows, summary = _dark(postgis)

    assert len(rows) == 3
    assert summary["detections"] == 3 and summary["matched"] == 1 and summary["dark"] == 2
    assert summary["unmatched_fraction"] == "66.7%"
    assert rows[0]["status"] == "matched"  # matched first, then the darks


def test_a_rate_is_quotable_only_when_every_matched_row_is_ground_truth(postgis):
    _load(
        postgis,
        [("s1:0", DET_LON, DET_LAT)],
        _Feed(_bracket("219000010", DET_LON, DET_LAT), ground_truth=True),
    )
    _rows, summary = _dark(postgis)
    assert summary["rate is quotable"] is True

    DB.migrate(postgis, drop=True)
    _load(
        postgis,
        [("s1:0", DET_LON, DET_LAT)],
        _Feed(_bracket("263000000", DET_LON, DET_LAT), ground_truth=False),
    )
    _rows, thinned = _dark(postgis)
    assert "never a dark rate" in thinned["rate is quotable"]


def test_a_report_outside_the_time_window_is_not_a_bracket(postgis):
    """The window is what gets fed to the interpolator; a vessel whose only
    reports sit outside it has no bracket and is not a candidate."""
    far_apart = [
        _position("219000011", DET_LON, DET_LAT, ACQ - timedelta(minutes=30)),
        _position("219000011", DET_LON, DET_LAT, ACQ + timedelta(minutes=30)),
    ]
    _load(postgis, [("s1:0", DET_LON, DET_LAT)], _Feed(far_apart))
    rows, _summary = _dark(postgis)

    assert rows[0]["status"] == "dark"


def test_the_interpolated_position_is_between_the_two_reports(postgis):
    """A vessel at 12 kn covers 3.7 km inside the ±5.5 min window, so "nearest
    report in time" is a position up to kilometres stale. Here the two reports
    straddle the instant 600 m apart and only the interpolated midpoint matches."""
    before_lon, before_lat = offset_m(
        np.array(DET_LON), np.array(DET_LAT), np.array(AZIMUTH_BEARING), np.array(-600.0)
    )
    after_lon, after_lat = offset_m(
        np.array(DET_LON), np.array(DET_LAT), np.array(AZIMUTH_BEARING), np.array(600.0)
    )
    track = [
        _position("219000012", float(before_lon), float(before_lat), ACQ - timedelta(seconds=60)),
        _position("219000012", float(after_lon), float(after_lat), ACQ + timedelta(seconds=60)),
    ]
    _load(postgis, [("s1:0", DET_LON, DET_LAT)], _Feed(track))
    rows, _summary = _dark(postgis)

    assert rows[0]["status"] == "matched"
    assert rows[0]["distance_m"] == pytest.approx(0.0, abs=5.0)
    assert rows[0]["time_delta_s"] == pytest.approx(60.0, abs=1.0)


def test_the_query_is_scoped_to_the_scene_it_was_asked_about(postgis):
    _load(postgis, [("s1:0", DET_LON, DET_LAT)], _Feed(_bracket("219000013", DET_LON, DET_LAT)))
    DB.upsert_scene(postgis, _item("OTHER", ACQ), "/y.zip")
    other = Detection(id="OTHER:0", scene_id="OTHER", lon=DET_LON, lat=DET_LAT)
    DB.insert_detections(postgis, _Run(scene_id="OTHER"), [other], [_Measurement(other.id)])

    rows, _summary = _dark(postgis)
    assert [r["detection_id"] for r in rows] == ["s1:0"]
