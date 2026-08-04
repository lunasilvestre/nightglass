"""Unit tests for the spatial layer.

These run with `--network none` and no database, like the rest of the suite, so
they cover the parts that are pure functions of their inputs: the LUT
interpolation, the geodesy, the azimuth-displacement physics, the block
statistics, the AIS parsing and dedup rule.

What they deliberately do NOT cover is whether the detector finds ships. No unit
test can answer that, and pretending otherwise is how a pipeline ends up with a
green suite and a coastline full of phantom vessels — which is exactly what
happened here before the output was rendered and looked at. That question is
answered by `make validate-shift` against DMA ground truth and by opening the
images `make render` writes. The tests below protect the machinery underneath it.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest

from nightglass.spatial import geodesy
from nightglass.spatial.ais import _parse_dma_row
from nightglass.spatial.detect import DetectorConfig, _block_stats, _dilate, _erode, land_mask
from nightglass.spatial.safe import Lut

# -- geodesy -----------------------------------------------------------------


def test_haversine_matches_known_distance():
    # One degree of latitude is ~111.2 km anywhere.
    d = geodesy.haversine_m(np.array(0.0), np.array(0.0), np.array(0.0), np.array(1.0))
    assert 111_000 < float(d) < 111_400


def test_bearing_cardinal_directions():
    north = geodesy.bearing_deg(np.array(10.0), np.array(56.0), np.array(10.0), np.array(57.0))
    east = geodesy.bearing_deg(np.array(10.0), np.array(56.0), np.array(11.0), np.array(56.0))
    assert float(north) == pytest.approx(0.0, abs=0.01)
    assert float(east) == pytest.approx(90.0, abs=0.5)


def test_offset_round_trips():
    lon, lat = geodesy.offset_m(np.array(11.0), np.array(56.5), np.array(37.0), np.array(4200.0))
    back = geodesy.haversine_m(np.array(11.0), np.array(56.5), lon, lat)
    assert float(back) == pytest.approx(4200.0, rel=1e-3)


def test_negative_offset_walks_backwards():
    """The dark query relies on this: a negative shift walks along the same
    bearing in reverse, so the correction needs no separate case for its sign."""
    fwd = geodesy.offset_m(np.array(11.0), np.array(56.5), np.array(200.0), np.array(1000.0))
    back = geodesy.offset_m(np.array(11.0), np.array(56.5), np.array(20.0), np.array(-1000.0))
    assert float(fwd[0]) == pytest.approx(float(back[0]), abs=1e-6)
    assert float(fwd[1]) == pytest.approx(float(back[1]), abs=1e-6)


# -- azimuth displacement ----------------------------------------------------


def test_line_of_sight_velocity_is_zero_along_azimuth():
    """A vessel steaming exactly along-track has no range rate, so it is not
    displaced at all. This is the case a symmetric match radius gets right by
    accident and the case that proves the projection is the right way round."""
    v = geodesy.line_of_sight_velocity_ms(
        sog_ms=np.array(6.0),
        cog_deg=np.array(196.84),  # the platform heading
        incidence_deg=np.array(38.7),
        range_bearing_deg=286.84,  # 90° away
    )
    assert float(v) == pytest.approx(0.0, abs=1e-9)


def test_line_of_sight_velocity_includes_the_incidence_projection():
    """Forgetting sin(incidence) overstates the shift by 1/sin(38.7°) ≈ 1.6×."""
    v = geodesy.line_of_sight_velocity_ms(
        sog_ms=np.array(10.0),
        cog_deg=np.array(286.84),  # straight along range
        incidence_deg=np.array(38.7),
        range_bearing_deg=286.84,
    )
    assert float(v) == pytest.approx(10.0 * math.sin(math.radians(38.7)), rel=1e-6)


def test_azimuth_displacement_is_hundreds_of_metres_at_realistic_speed():
    """The number that justifies doing this at all: a 12-knot vessel crossing the
    range direction is drawn ~450 m from where it was — most of a 500 m radius."""
    shift = geodesy.azimuth_displacement_m(
        sog_ms=np.array(12.0 * geodesy.KNOTS_TO_MS),
        cog_deg=np.array(286.84),
        incidence_deg=np.array(38.7),
        r_over_v_s=np.array(115.3),
        range_bearing_deg=286.84,
        sign=1,
    )
    assert 400 < abs(float(shift)) < 500


def test_azimuth_displacement_sign_flips_with_the_parameter():
    kw = {
        "sog_ms": np.array(6.0),
        "cog_deg": np.array(286.84),
        "incidence_deg": np.array(38.7),
        "r_over_v_s": np.array(115.3),
        "range_bearing_deg": 286.84,
    }
    assert float(geodesy.azimuth_displacement_m(**kw, sign=1)) == pytest.approx(
        -float(geodesy.azimuth_displacement_m(**kw, sign=-1))
    )


def test_apparent_position_moves_along_the_flight_direction():
    """The displacement is along-track, never across it — that is what makes it
    correctable rather than just noise."""
    lon, lat = geodesy.apparent_position(
        lon=np.array(11.0), lat=np.array(56.5),
        sog_ms=np.array(8.0), cog_deg=np.array(286.84),
        incidence_deg=np.array(38.7), r_over_v_s=np.array(115.3),
        range_bearing_deg=286.84, azimuth_bearing_deg=196.84, sign=-1,
    )
    moved = geodesy.bearing_deg(np.array(11.0), np.array(56.5), lon, lat)
    # sign=-1 with a receding target walks backward along 196.84°, i.e. 16.84°.
    assert float(moved) == pytest.approx(16.84, abs=1.0)


# -- calibration LUTs --------------------------------------------------------


def test_lut_interpolates_between_vectors_and_samples():
    lut = Lut(
        lines=np.array([0.0, 100.0]),
        pixels=np.array([0.0, 10.0]),
        values=np.array([[1.0, 3.0], [5.0, 7.0]]),
    )
    out = lut.at(np.array([0.0, 50.0, 100.0]), 0, 11)
    assert out.shape == (3, 11)
    assert out[0, 0] == pytest.approx(1.0)
    assert out[0, 10] == pytest.approx(3.0)
    assert out[2, 0] == pytest.approx(5.0)
    assert out[1, 5] == pytest.approx(4.0)  # midway in both axes


def test_lut_column_window_matches_the_full_width_slice():
    """`at(lines, col0, col1)` must equal the same columns of the full-width
    evaluation. The detector restricts columns to the AOI, and a mismatch here
    would shift the whole calibration across the swath."""
    lut = Lut(
        lines=np.array([0.0, 50.0]),
        pixels=np.array([0.0, 40.0, 80.0]),
        values=np.array([[2.0, 4.0, 9.0], [3.0, 5.0, 11.0]]),
    )
    full = lut.at(np.array([10.0, 20.0]), 0, 81)
    window = lut.at(np.array([10.0, 20.0]), 30, 60)
    assert np.allclose(window, full[:, 30:60])


# -- block statistics --------------------------------------------------------


def test_block_stats_censors_a_bright_target_out_of_its_own_background():
    """A ship must not raise the background it is then compared against."""
    rng = np.random.default_rng(0)
    field = rng.normal(1.0, 0.1, size=(32, 32)).astype(np.float32)
    valid = np.ones_like(field, dtype=bool)
    clean_mean, clean_std, _ = _block_stats(field, valid, 32, 4.0)

    field[15:17, 15:17] = 500.0  # a bright target
    mean, std, _ = _block_stats(field, valid, 32, 4.0)
    assert float(mean[0, 0]) == pytest.approx(float(clean_mean[0, 0]), rel=0.05)
    assert float(std[0, 0]) == pytest.approx(float(clean_std[0, 0]), rel=0.5)


def test_block_stats_survives_a_negative_mean():
    """Over noise-limited water the noise-subtracted mean is near zero and often
    negative — the S1 noise LUT mildly over-subtracts at VH. Censoring by a
    MULTIPLE of the mean collapses there and reports the bottom tail as the sea;
    censoring by sigmas does not. This is the bug the first render exposed."""
    rng = np.random.default_rng(1)
    field = rng.normal(-0.02, 1.0, size=(32, 32)).astype(np.float32)
    valid = np.ones_like(field, dtype=bool)
    mean, std, _ = _block_stats(field, valid, 32, 4.0)
    assert float(mean[0, 0]) == pytest.approx(-0.02, abs=0.3)
    assert float(std[0, 0]) == pytest.approx(1.0, rel=0.3)
    # The threshold it feeds must sit ABOVE the field, not inside it.
    threshold = float(mean[0, 0] + 8.0 * std[0, 0])
    assert (field > threshold).sum() == 0


def test_block_stats_handles_a_ragged_final_block():
    field = np.ones((70, 70), dtype=np.float32)
    valid = np.ones_like(field, dtype=bool)
    mean, _, _fill = _block_stats(field, valid, 32, 4.0)
    assert mean.shape == (3, 3)  # ceil(70/32)
    assert float(mean[0, 0]) == pytest.approx(1.0)


# -- morphology --------------------------------------------------------------


def test_dilate_grows_and_erode_shrinks_symmetrically():
    mask = np.zeros((9, 9), dtype=bool)
    mask[4, 4] = True
    assert _dilate(mask, 1).sum() == 5  # 4-connected cross
    assert _erode(_dilate(mask, 2), 2).sum() == 1


def test_opening_removes_a_ship_sized_speck_but_keeps_land():
    """The whole reason the data-derived land mask cannot catch skerries, stated
    as a test: opening is what stops a ship masking itself, and it removes
    anything ship-sized — including a rock."""
    cfg = DetectorConfig(land_block=1, land_open_blocks=2, land_dilate_blocks=0,
                         land_sigma0_db=-22.0, max_fill_fraction=0.5)
    bright = 10 ** (-10.0 / 10.0)  # well above the land threshold
    dark = 10 ** (-30.0 / 10.0)

    field = np.full((40, 40), dark, dtype=np.float32)
    field[2:6, 2:6] = bright        # a ship-sized speck
    field[20:40, 20:40] = bright    # a landmass
    valid = np.ones_like(field, dtype=bool)

    mask = land_mask(field, valid, cfg)
    assert not mask[3, 3], "a ship-sized bright object must NOT become land"
    assert mask[30, 30], "a large bright region must be land"


# -- AIS parsing -------------------------------------------------------------

ROW = ["17/07/2026 05:13:00", "Class B", "257843840", "56.489370", "10.951233", "Unknown value", "", "7.0", "354.6", "", "Unknown", "LL6727", "BORREGUTT", "Sailing", "", "4", "12", "Undefined", "", "Unknown", "", "AIS", "8", "4", "3", "1"]


def test_dma_row_parses_the_verified_schema():
    p = _parse_dma_row(ROW, "dma")
    assert p is not None
    assert p.mmsi == "257843840"
    assert p.timestamp == datetime(2026, 7, 17, 5, 13, 0, tzinfo=UTC)
    assert p.lat == pytest.approx(56.489370)
    assert p.lon == pytest.approx(10.951233)
    assert p.name == "BORREGUTT"
    assert p.length_m == 12
    assert p.width_m == 4


def test_dma_sog_is_converted_from_knots_at_the_boundary():
    """7.0 knots is 3.60 m/s. Converting here means nothing downstream — the SQL
    included — has to know which unit it is holding."""
    p = _parse_dma_row(ROW, "dma")
    assert p.sog_ms == pytest.approx(7.0 * 0.514444, rel=1e-6)


def test_dma_row_rejects_the_ais_not_available_sentinels():
    row = list(ROW)
    row[3], row[4] = "91.0", "181.0"
    assert _parse_dma_row(row, "dma") is None


def test_cog_and_heading_sentinels_become_none():
    row = list(ROW)
    row[8], row[9] = "360.5", "511"
    p = _parse_dma_row(row, "dma")
    assert p.cog_deg is None
    assert p.heading_deg is None


@pytest.mark.parametrize("mobile", ["Base Station", "AtoN", "SAR Airborne", ""])
def test_only_vessels_become_positions(mobile):
    """A shore transmitter and a navigation buoy are not ships.

    Found by M6 rather than designed. The acquisition window used through M3–M5
    was a CSV cut by hand during pre-dev, and that cut had quietly kept only
    Class A and Class B; the code had no such rule, so reading the same window
    out of the daily file the manifest fetches produced 7,857 extra rows —
    5.5% base stations, plus navigation aids. Matching a detection to one would
    report a buoy as a vessel that had declared itself.
    """
    row = list(ROW)
    row[1] = mobile
    assert _parse_dma_row(row, "dma") is None
    assert _parse_dma_row(ROW, "dma") is not None


def test_dedup_key_is_the_spec_rule():
    """§3.2: 71% of raw DMA rows are exact rebroadcast duplicates, worst case 21
    identical copies. The key is (mmsi, timestamp, lat, lon)."""
    a = _parse_dma_row(ROW, "dma")
    b = _parse_dma_row(list(ROW), "dma")
    assert a.dedup_key == b.dedup_key
    moved = list(ROW)
    moved[3] = "56.489371"
    assert _parse_dma_row(moved, "dma").dedup_key != a.dedup_key


# -- source honesty ----------------------------------------------------------


def test_only_dma_is_ground_truth():
    """§7's honesty requirement as a check rather than a sentence. aisstream was
    measured at ~17% of DMA's vessels over an identical bbox and window; quoting
    a dark rate from it would report ~83% dark."""
    from nightglass.spatial.ais import CustomerFeedSource, DMAFileSource, GFWDetectionSource

    assert DMAFileSource.is_ground_truth is True
    assert CustomerFeedSource.is_ground_truth is False
    assert GFWDetectionSource.is_ground_truth is False


def test_every_source_carries_an_attribution():
    from nightglass.spatial.ais import SOURCES

    for name, cls in SOURCES.items():
        assert cls.attribution, f"{name} must carry an attribution line (§8.6)"


def test_gfw_source_refuses_to_pose_as_an_ais_feed():
    """GFW detections are already AIS-matched upstream by someone else. Feeding
    them to `ais_match` would be claiming their correlation as ours."""
    from nightglass.config import BBox
    from nightglass.spatial.ais import GFWDetectionSource, NotConfigured

    with pytest.raises(NotConfigured, match="not AIS positions"):
        list(
            GFWDetectionSource().positions(
                BBox.parse("10,55,12,57", origin="t"),
                datetime(2026, 7, 17, tzinfo=UTC),
                datetime(2026, 7, 18, tzinfo=UTC),
            )
        )


# -- the SQL is real SQL -----------------------------------------------------


def test_dark_query_sql_is_shipped_and_parameterised():
    """The §M3 join lives in a .sql file so it can be hand-checked. If it stops
    being shipped, `dark` fails at runtime rather than at import."""
    from nightglass.spatial.db import sql

    text = sql("dark_vessels.sql")
    for token in ("%(scene_id)s", "%(radius_m)s", "%(window_s)s", "%(correct_azimuth)s"):
        assert token in text
    assert "LEFT JOIN" in text, "must return matched AND dark, so the base rate is computable"
    assert "ST_Project" in text, "the azimuth correction must happen in the query"
