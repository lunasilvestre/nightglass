"""Config tests — the AOI layer is a hard requirement (§3.1), so it gets tests
at M0 rather than at whichever milestone first trips over it.

The bbox axis-order cases are not padding. An inverted aisstream bbox produces a
silently empty stream rather than an error, and that failure mode cost real time
during pre-dev.
"""

from __future__ import annotations

import pytest

from nightglass.config import AOI, BBox, ConfigError

LISBON = {
    "AOI_LISBON_BBOX": "-10.5,38.0,-8.5,39.5",
    "AOI_LISBON_AIS_SOURCE": "aisstream",
    "AOI_LISBON_PASS_DESCENDING": "06:33-06:51",
}
KATTEGAT = {
    "AOI_KATTEGAT_BBOX": "10.5,55.5,12.5,57.5",
    "AOI_KATTEGAT_AIS_SOURCE": "dma",
}


def test_bbox_parses_in_gis_order():
    b = BBox.parse("-10.5,38.0,-8.5,39.5", origin="test")
    assert b.as_list() == [-10.5, 38.0, -8.5, 39.5]


def test_aisstream_axis_order_is_inverted():
    """aisstream takes [[lat, lon], [lat, lon]] — the opposite of everything else."""
    b = BBox.parse("-11.5,38.0,-8.0,42.5", origin="test")
    assert b.as_aisstream() == [[38.0, -11.5], [42.5, -8.0]]


def test_transposed_bbox_is_rejected_not_silently_empty():
    with pytest.raises(ConfigError, match="degenerate or transposed"):
        BBox.parse("-8.5,39.5,-10.5,38.0", origin="test")


def test_range_check_catches_a_swap_that_exceeds_90():
    """A latitude past ±90 is unambiguous, so it is rejected."""
    with pytest.raises(ConfigError, match="latitude out of range"):
        BBox.parse("10.0,100.0,12.0,120.0", origin="test")


def test_a_swapped_aoi_is_NOT_generally_detectable():
    """The uncomfortable one, asserted so nobody later assumes validation covers it.

    Lisbon's bbox with lat and lon transposed is a legitimate box off Somalia.
    It parses clean, passes every range check, and then returns nothing —
    exactly the silent-empty failure that cost time during pre-dev. Validation
    cannot save us here, which is why `as_aisstream()` is the single place the
    axis order is ever converted.
    """
    swapped = BBox.parse("38.0,-10.5,39.5,-8.5", origin="test")
    assert swapped.as_list() == [38.0, -10.5, 39.5, -8.5]  # valid, and wrong


def test_bbox_rejects_wrong_arity():
    with pytest.raises(ConfigError, match="expected 4"):
        BBox.parse("-10.5,38.0,-8.5", origin="test")


def test_aoi_resolves_from_naming_convention():
    aoi = AOI.from_env("lisbon", env=LISBON)
    assert aoi.name == "lisbon"
    assert aoi.ais_source == "aisstream"
    assert aoi.pass_descending == "06:33-06:51"
    assert aoi.pass_ascending is None


def test_only_dma_counts_as_ground_truth():
    """aisstream was measured at ~17% of DMA's vessels over an identical bbox and
    window, so a dark *rate* from it would be fiction. §7."""
    assert AOI.from_env("kattegat", env=KATTEGAT).is_ground_truth is True
    assert AOI.from_env("lisbon", env=LISBON).is_ground_truth is False


def test_unknown_aoi_names_the_configured_ones():
    with pytest.raises(ConfigError, match="lisbon"):
        AOI.from_env("azores", env=LISBON)


def test_bad_ais_source_is_rejected():
    with pytest.raises(ConfigError, match="dma\\|aisstream\\|gfw"):
        AOI.from_env("x", env={"AOI_X_BBOX": "1,1,2,2", "AOI_X_AIS_SOURCE": "magic"})
