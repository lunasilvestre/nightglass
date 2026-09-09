"""`SafeProduct` against a real Sentinel-1 product's own annotation.

The fixture is the manifest and the three VH annotation members of the Kattegat
validation granule, kept at their real paths inside a zip
(`tests/fixtures/README.md` has the build command and the Copernicus
attribution). Everything asserted below is read out of ESA's XML, not out of
something written to make a test pass: the acquisition geometry the azimuth
correction multiplies together, the incidence spread across the swath, both
LUTs, the footprint and the STAC item.

Pixel-level detection is deliberately not tested here — the measurement TIFF is
886 MB and is not in the fixture. Whether the detector finds ships is answered by
`make validate-shift` against DMA ground truth and by looking at what `make
render` writes; see docs/testing.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from nightglass.spatial.safe import SafeError, SafeProduct, find_granules

SCENE = "S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13"
FIXTURE = Path(__file__).parent / "fixtures" / f"{SCENE}.annotation.zip"


@pytest.fixture(scope="module")
def product() -> SafeProduct:
    return SafeProduct(FIXTURE)


def test_the_fixture_is_committed_and_small_enough_to_be(tmp_path):
    """812 KB of annotation XML is what makes these tests possible at all; the
    measurement member is a zero-byte stand-in, so nothing here can grow."""
    assert FIXTURE.exists()
    assert FIXTURE.stat().st_size < 1_000_000


def test_a_missing_granule_says_which_path_it_looked_at(tmp_path):
    with pytest.raises(SafeError, match="no such granule"):
        SafeProduct(tmp_path / "absent.zip")


def test_a_zip_that_is_not_one_safe_product_is_refused(tmp_path):
    import zipfile

    path = tmp_path / "two.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("A.SAFE/manifest.safe", "")
        z.writestr("B.SAFE/manifest.safe", "")
    with pytest.raises(SafeError, match="expected one .SAFE root"):
        SafeProduct(path)


# -- layout --------------------------------------------------------------------


def test_polarizations_are_discovered_from_the_measurement_members(product):
    """The fixture keeps only the VH half of a dual-pol product, so this is the
    discovery working rather than the granule being single-pol."""
    assert product.polarizations == ("VH",)
    assert product.scene_id == SCENE


def test_asking_for_a_polarization_the_product_does_not_have_says_why_vh(product):
    with pytest.raises(SafeError, match="use VH for detection"):
        product.polarization("VV")


def test_each_polarizations_four_members_resolve_to_exactly_one_file_each(product):
    pol = product.polarization("vh")
    assert pol.name == "VH"
    assert pol.annotation.endswith(".xml") and "/annotation/s1d-iw-grd-vh-" in pol.annotation
    assert "/calibration/calibration-s1d-iw-grd-vh-" in pol.calibration
    assert "/calibration/noise-s1d-iw-grd-vh-" in pol.noise
    assert pol.measurement.endswith(".tiff")


def test_the_raster_path_reads_the_tiff_in_place(product):
    """~900 MB on disk against ~5.5 GB unpacked, six granules at 4.7 GB instead
    of 33 GB — the whole "read in place" decision is this one string."""
    assert product.raster_path("VH") == f"/vsizip/{FIXTURE}/{SCENE}.SAFE/measurement/" + (
        "s1d-iw-grd-vh-20260717t052324-20260717t052349-003709-006a36-002.tiff"
    )


# -- acquisition geometry ------------------------------------------------------


def test_the_acquisition_is_read_out_of_the_products_own_annotation(product):
    a = product.acquisition
    assert (a.platform, a.mode, a.product_type) == ("S1D", "IW", "GRD")
    assert a.pass_direction == "DESCENDING"
    assert a.start_time == datetime(2026, 7, 17, 5, 23, 24, 31878, tzinfo=UTC)
    assert a.stop_time == datetime(2026, 7, 17, 5, 23, 49, 30821, tzinfo=UTC)
    assert (a.absolute_orbit, a.relative_orbit) == (3709, 168)
    assert (a.width, a.height) == (26564, 16669)
    assert a.range_pixel_spacing_m == 10.0 and a.azimuth_pixel_spacing_m == 10.0


def test_ais_is_matched_to_the_middle_of_the_25_second_acquisition(product):
    a = product.acquisition
    assert a.acquisition_time == datetime(2026, 7, 17, 5, 23, 36, 531350, tzinfo=UTC)
    assert (a.stop_time - a.start_time).total_seconds() == pytest.approx(25.0, abs=0.1)


def test_range_points_ninety_degrees_right_of_the_flight_direction(product):
    """Sentinel-1 looks right, always. Deriving the range bearing from the look
    side rather than writing a number down is what stops the azimuth correction's
    sign from being flipped, and the flip is not visible in the output."""
    a = product.acquisition
    assert a.azimuth_bearing_deg == pytest.approx(196.8422652, abs=1e-6)
    assert a.range_bearing_deg == pytest.approx(286.8422652, abs=1e-6)
    assert (a.range_bearing_deg - a.azimuth_bearing_deg) % 360.0 == pytest.approx(90.0)


def test_the_platform_speed_is_orbital_not_ground(product):
    """~7.6 km/s is the spacecraft's speed in the Earth-fixed frame, averaged
    over the state vectors bracketing the scene. A ground-track speed (~6.7 km/s)
    here would bias every R/V by more than a tenth."""
    assert product.acquisition.platform_speed_ms == pytest.approx(7588.0, abs=5.0)


def test_the_mid_swath_incidence_matches_the_grid_it_is_computed_over(product):
    a = product.acquisition
    grid = product.grid
    assert a.incidence_mid_deg == pytest.approx(38.67, abs=0.01)
    assert grid.incidence_deg.min() == pytest.approx(29.92, abs=0.01)
    assert grid.incidence_deg.max() == pytest.approx(45.97, abs=0.01)
    assert grid.incidence_deg.min() < a.incidence_mid_deg < grid.incidence_deg.max()


# -- the geolocation grid ------------------------------------------------------


def test_the_grid_is_a_full_lattice(product):
    grid = product.grid
    assert grid.incidence_deg.shape == (len(grid.lines), len(grid.pixels))
    assert grid.incidence_deg.shape == (10, 21)
    assert np.all(np.diff(grid.lines) > 0) and np.all(np.diff(grid.pixels) > 0)


def test_incidence_rises_across_the_swath_not_along_it(product):
    """It varies by roughly a fifth across a 250 km swath, which is why the SQL
    joins incidence per detection rather than using one scene-wide value."""
    grid = product.grid
    line = np.full(3, grid.lines[len(grid.lines) // 2])
    near, mid, far = grid.incidence_at(line, np.array([0.0, 13000.0, 26000.0]))

    assert near < mid < far
    assert far - near == pytest.approx(15.0, abs=2.0)


def test_r_over_v_at_mid_swath_is_the_hundred_and_fifteen_seconds_the_docs_quote(product):
    """R/V is the scale of the azimuth displacement: at 115 s a ship making 12 kn
    across the range direction is drawn ~450 m from where it was."""
    a = product.acquisition
    mid = product.r_over_v(np.array([a.height / 2.0]), np.array([a.width / 2.0]))
    assert float(mid[0]) == pytest.approx(115.3, abs=0.5)


def test_slant_range_is_derived_from_the_two_way_time(product):
    """R = c·t/2. Halving is the easy thing to forget and it doubles every R/V."""
    grid = product.grid
    line = np.array([grid.lines[0]])
    pixel = np.array([grid.pixels[0]])
    seconds = grid._bilinear(grid.slant_range_time_s, line, pixel)
    metres = grid.slant_range_at(line, pixel)

    assert float(metres[0]) == pytest.approx(float(seconds[0]) * 299_792_458.0 / 2.0)
    assert 780_000 < float(metres[0]) < 1_000_000  # a plausible slant range for IW


def test_r_over_v_grows_toward_the_far_edge_of_the_swath(product):
    a = product.acquisition
    lines = np.full(2, a.height / 2.0)
    near, far = product.r_over_v(lines, np.array([0.0, float(a.width - 1)]))
    assert far > near


# -- the LUTs ------------------------------------------------------------------


def test_the_sigma_lut_is_the_products_own_calibration_vectors(product):
    lut = product.sigma_lut("VH")
    assert lut.values.shape == (len(lut.lines), len(lut.pixels))
    assert lut.values.shape == (27, 666)
    assert 500 < float(lut.values.min()) < float(lut.values.max()) < 800
    assert np.all(np.diff(lut.lines) > 0)


def test_the_sigma_lut_is_cached_rather_than_reparsed(product):
    assert product.sigma_lut("VH") is product.sigma_lut("VH")


def test_the_noise_luts_cover_the_three_iw_sub_swaths_end_to_end(product):
    """Noise is a range LUT times a per-swath azimuth LUT, and the swath blocks
    have to tile the image: a gap would leave a column with no noise estimate."""
    rng, blocks = product.noise_lut("VH")
    assert rng.values.shape == (26, 669)
    assert len(blocks) == 3

    firsts = [b[0] for b in blocks]
    lasts = [b[1] for b in blocks]
    assert firsts[0] == 0
    assert lasts[-1] == product.acquisition.width - 1
    assert firsts[1:] == [last + 1 for last in lasts[:-1]]  # contiguous, no overlap


def test_noise_over_water_is_a_small_positive_power_in_dn_squared(product):
    noise = product.noise_at(np.array([1000.0, 8000.0]), 10_000, 10_010, "VH")
    assert noise.shape == (2, 10)
    assert np.all(noise > 0)
    assert float(noise.max()) < 1e5


def test_the_azimuth_block_scales_the_range_noise_rather_than_replacing_it(product):
    """`noise = noiseRangeLut(line, pixel) · noiseAzimuthLut(swath, line)`. A
    range-only estimate leaves a visible across-track ramp in VH over calm water,
    and a CFAR threshold that does not know about it becomes range-dependent."""
    rng, _blocks = product.noise_lut("VH")
    lines = np.array([1000.0, 8000.0])
    range_only = rng.at(lines, 10_000, 10_010)
    combined = product.noise_at(lines, 10_000, 10_010, "VH")

    ratio = combined / range_only
    assert np.all(ratio > 0)
    assert not np.allclose(ratio, 1.0)  # the azimuth LUT is doing something
    assert np.allclose(ratio[0], ratio[0][0])  # and it is constant across a block


def test_a_lut_interpolates_between_its_vectors(product):
    lut = product.sigma_lut("VH")
    lo, hi = float(lut.lines[0]), float(lut.lines[1])
    values = lut.at(np.array([lo, (lo + hi) / 2.0, hi]), 100, 101)

    assert values.shape == (3, 1)
    assert min(values[0][0], values[2][0]) <= values[1][0] <= max(values[0][0], values[2][0])


# -- footprint and STAC --------------------------------------------------------


def test_the_footprint_is_transposed_out_of_the_manifests_lat_lon_pairs(product):
    """The manifest writes `lat,lon`, the opposite order to everything else here.
    Converted once, in the one place that reads it."""
    wkt = product.footprint_wkt
    assert wkt.startswith("POLYGON((") and wkt.endswith("))")
    points = [tuple(float(v) for v in p.split()) for p in wkt[9:-2].split(", ")]

    assert points[0] == points[-1]  # closed ring
    assert len(points) == 5
    for lon, lat in points:
        assert 10.0 < lon < 17.0 and 55.0 < lat < 59.0  # the Kattegat, not its transpose


def test_the_stac_item_carries_the_geometry_the_dark_query_multiplies(product):
    """These four properties are read straight back out of `stac.scenes` by the
    dark-vessel SQL; a catalogue that dropped them would force the join to reopen
    a 1 GB zip."""
    item = product.stac_item(source_url="https://datapool.asf.alaska.edu/x")
    props = item["properties"]
    a = product.acquisition

    assert item["id"] == SCENE and item["stac_version"] == "1.0.0"
    assert props["nightglass:platform_heading_deg"] == a.platform_heading_deg
    assert props["nightglass:platform_speed_ms"] == a.platform_speed_ms
    assert props["nightglass:range_bearing_deg"] == a.range_bearing_deg
    assert props["nightglass:azimuth_bearing_deg"] == a.azimuth_bearing_deg
    assert props["sar:polarizations"] == ["VH"]
    assert props["sat:orbit_state"] == "descending"
    assert props["proj:shape"] == [a.height, a.width]
    assert item["links"] == [{"rel": "via", "href": "https://datapool.asf.alaska.edu/x"}]


def test_the_stac_bbox_bounds_the_footprint(product):
    item = product.stac_item()
    ring = item["geometry"]["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]

    assert item["bbox"] == [min(lons), min(lats), max(lons), max(lats)]
    assert item["bbox"] == pytest.approx([11.321791, 56.251587, 16.154522, 58.168587])


def test_an_item_with_no_source_url_carries_no_link(product):
    assert product.stac_item()["links"] == []


def test_only_the_polarizations_present_get_an_asset(product):
    assets = product.stac_item()["assets"]
    assert list(assets) == ["VH"]
    assert assets["VH"]["href"].startswith("/vsizip/")
    assert assets["VH"]["roles"] == ["data"]


# -- discovery -----------------------------------------------------------------


def test_granules_are_found_by_the_naming_convention_and_sorted(tmp_path):
    for name in ("S1B_IW_GRDH_b.zip", "S1A_IW_GRDH_a.zip", "notes.txt", "S1A_EW_GRDM_x.zip"):
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in find_granules(tmp_path)] == ["S1A_IW_GRDH_a.zip", "S1B_IW_GRDH_b.zip"]
