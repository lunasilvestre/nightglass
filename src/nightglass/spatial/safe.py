"""Reading a Sentinel-1 GRD product, without unpacking it.

Two decisions from M1's pre-dev work are baked in here, both of which save real
time and are easy to get wrong (docs/NOTES.md, "Two findings that save real time at
M3"):

**Read the scene in place.** A SAFE zip is ~5.5 GB unpacked and ~900 MB on disk.
GDAL's ``/vsizip/`` reads the measurement TIFF straight out of the archive, so
six granules cost 4.7 GB instead of 33 GB and nothing has to be cleaned up
afterwards. Verified on all six.

**Do not hand-parse the geolocation grid for georeferencing.** PRE_DEV_GUIDE §6
says to build GCPs from ``annotation/*.xml``; GDAL has already done it, and
``rasterio`` hands over all 210 tie points as ``src.gcps``. ``src.crs`` is
``None`` and ``src.transform`` is the identity — both expected, because a GRD
product is not map-projected.

What this module *does* parse out of the annotation is everything georeferencing
alone does not give you: the calibration and thermal-noise LUTs (§3.2, "DN is not
backscatter"), and the acquisition geometry the azimuth-displacement correction
needs — slant range, incidence angle, platform heading and orbital velocity.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path

import numpy as np

SPEED_OF_LIGHT = 299_792_458.0

# Sentinel-1 is right-looking, always, on every mode and both remaining
# platforms. It is a property of the spacecraft rather than of the product, so
# it is a constant here instead of something parsed and second-guessed.
LOOK_SIDE = "right"


class SafeError(RuntimeError):
    """Something about the product is not what the reader expects."""


def _text(node: ET.Element | None, path: str) -> str:
    if node is None:
        raise SafeError(f"missing element while looking for {path!r}")
    found = node.find(path)
    if found is None or found.text is None:
        raise SafeError(f"annotation has no {path!r}")
    return found.text.strip()


def _floats(node: ET.Element, path: str) -> np.ndarray:
    return np.fromstring(_text(node, path), sep=" ", dtype=np.float64)


def _parse_time(raw: str) -> datetime:
    """S1 annotation times are ISO-ish and always UTC, but carry no offset."""
    return datetime.fromisoformat(raw).replace(tzinfo=UTC)


@dataclass(frozen=True)
class Lut:
    """A LUT sampled on a coarse (line, pixel) lattice, bilinear in between.

    Both the calibration and the thermal-noise annotations use this shape: a
    list of vectors, each pinned to one image line, each holding values at the
    same set of pixel positions. Interpolation is separable, so it is done as
    two 1-D passes rather than a general scattered interpolation.
    """

    lines: np.ndarray  # (n_vectors,)
    pixels: np.ndarray  # (n_samples,)
    values: np.ndarray  # (n_vectors, n_samples)

    def at(self, lines: np.ndarray, col0: int, col1: int) -> np.ndarray:
        """Evaluate over ``lines`` × ``arange(col0, col1)``.

        Resampling along the pixel axis happens once per vector rather than once
        per output row: the pixel lattice is shared by every vector, so the
        expensive axis is done ``n_vectors`` times (27, typically) instead of
        ``len(lines)`` times (thousands).
        """
        cols = np.arange(col0, col1, dtype=np.float64)
        wide = np.empty((self.values.shape[0], col1 - col0), dtype=np.float32)
        for i, row in enumerate(self.values):
            wide[i] = np.interp(cols, self.pixels, row)

        upper = np.clip(np.searchsorted(self.lines, lines, side="right") - 1, 0, len(self.lines) - 2)
        span = self.lines[upper + 1] - self.lines[upper]
        # A degenerate span would only arise from a duplicated vector line; guard
        # rather than emit a warning-free NaN field that surfaces much later.
        frac = np.where(span > 0, (lines - self.lines[upper]) / np.maximum(span, 1), 0.0)
        frac = frac.astype(np.float32)[:, None]
        return wide[upper] * (1.0 - frac) + wide[upper + 1] * frac


@dataclass(frozen=True)
class Grid:
    """The geolocation grid, as regular arrays.

    The 210 tie points sit on a proper 10 × 21 lattice, so anything defined on
    them — incidence angle, slant range time, height — is bilinearly
    interpolable without scattered-data machinery. Position is *not* taken from
    here; that is what the GCP transformer is for.
    """

    lines: np.ndarray
    pixels: np.ndarray
    incidence_deg: np.ndarray  # (n_lines, n_pixels)
    slant_range_time_s: np.ndarray
    latitude: np.ndarray
    longitude: np.ndarray

    def _bilinear(self, field: np.ndarray, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        i = np.clip(np.searchsorted(self.lines, line, side="right") - 1, 0, len(self.lines) - 2)
        j = np.clip(np.searchsorted(self.pixels, pixel, side="right") - 1, 0, len(self.pixels) - 2)
        ti = (line - self.lines[i]) / (self.lines[i + 1] - self.lines[i])
        tj = (pixel - self.pixels[j]) / (self.pixels[j + 1] - self.pixels[j])
        return (
            field[i, j] * (1 - ti) * (1 - tj)
            + field[i + 1, j] * ti * (1 - tj)
            + field[i, j + 1] * (1 - ti) * tj
            + field[i + 1, j + 1] * ti * tj
        )

    def incidence_at(self, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        return self._bilinear(self.incidence_deg, line, pixel)

    def slant_range_at(self, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        """Slant range in metres. ``R = c · t / 2`` — t is the two-way time."""
        return self._bilinear(self.slant_range_time_s, line, pixel) * SPEED_OF_LIGHT / 2.0


@dataclass(frozen=True)
class Acquisition:
    """The scene's identity and geometry, in the units the rest of M3 wants."""

    scene_id: str
    platform: str
    product_type: str
    mode: str
    polarizations: tuple[str, ...]
    start_time: datetime
    stop_time: datetime
    absolute_orbit: int
    relative_orbit: int
    pass_direction: str
    platform_heading_deg: float
    platform_speed_ms: float
    incidence_mid_deg: float
    range_pixel_spacing_m: float
    azimuth_pixel_spacing_m: float
    width: int
    height: int
    footprint_wkt: str

    @property
    def acquisition_time(self) -> datetime:
        """Scene mid-time. A granule spans ~25 s; AIS is matched to the middle.

        Per-detection azimuth time would be more exact (the along-track position
        of a detection tells you when it was actually imaged), and that is a
        refinement worth making, but 25 s at 10 kn is 130 m — inside the
        match radius and well inside the azimuth displacement being corrected
        for below.
        """
        return self.start_time + (self.stop_time - self.start_time) / 2

    @property
    def range_bearing_deg(self) -> float:
        """Compass bearing of increasing range (sample index), on the ground.

        Sentinel-1 looks right, so ground range points 90° clockwise from the
        platform heading. Getting this backwards flips the sign of every
        azimuth-displacement correction, which is why it is derived from the
        look side rather than written down as a number.
        """
        offset = 90.0 if LOOK_SIDE == "right" else -90.0
        return (self.platform_heading_deg + offset) % 360.0

    @property
    def azimuth_bearing_deg(self) -> float:
        """Compass bearing of increasing azimuth (line index) — the flight direction."""
        return self.platform_heading_deg % 360.0


@dataclass(frozen=True)
class Polarization:
    """Where one polarization's three files live inside the archive."""

    name: str
    measurement: str
    annotation: str
    calibration: str
    noise: str


class SafeProduct:
    """One Sentinel-1 SAFE archive, read in place.

    Nothing here extracts, and nothing writes. The measurement raster is reached
    through a ``/vsizip/`` path handed to rasterio; the XML is read from the zip
    directly, since GDAL has no reason to care about calibration LUTs.
    """

    def __init__(self, zip_path: str | Path) -> None:
        self.zip_path = Path(zip_path)
        if not self.zip_path.exists():
            raise SafeError(f"no such granule: {self.zip_path}")
        with zipfile.ZipFile(self.zip_path) as z:
            names = z.namelist()
        roots = {n.split("/", 1)[0] for n in names if n.split("/", 1)[0].endswith(".SAFE")}
        if len(roots) != 1:
            raise SafeError(f"{self.zip_path.name}: expected one .SAFE root, found {sorted(roots)}")
        self.safe_name = roots.pop()
        self._names = names

    # -- layout ---------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SafeProduct {self.scene_id} {'+'.join(self.polarizations)}>"

    @property
    def scene_id(self) -> str:
        """The granule name without the ``.SAFE`` suffix — the STAC item id."""
        return self.safe_name[: -len(".SAFE")]

    def _one(self, pattern: str, pol: str) -> str:
        want = re.compile(pattern.format(pol=pol.lower()))
        hits = [n for n in self._names if want.search(n)]
        if len(hits) != 1:
            raise SafeError(f"{self.scene_id}: expected 1 match for {pattern} ({pol}), got {hits}")
        return hits[0]

    @cached_property
    def polarizations(self) -> tuple[str, ...]:
        found = {
            m.group(1).upper()
            for n in self._names
            if (m := re.search(r"/measurement/s1[abcd]-iw-grd-(\w\w)-", n))
        }
        return tuple(sorted(found))

    def polarization(self, pol: str) -> Polarization:
        if pol.upper() not in self.polarizations:
            raise SafeError(
                f"{self.scene_id} has {self.polarizations}, not {pol.upper()}. "
                "§3.2: use VH for detection — lower background over water, better ship contrast."
            )
        return Polarization(
            name=pol.upper(),
            measurement=self._one(r"/measurement/s1[abcd]-iw-grd-{pol}-.*\.tiff$", pol),
            annotation=self._one(r"/annotation/s1[abcd]-iw-grd-{pol}-.*\.xml$", pol),
            calibration=self._one(r"/annotation/calibration/calibration-s1[abcd]-iw-grd-{pol}-", pol),
            noise=self._one(r"/annotation/calibration/noise-s1[abcd]-iw-grd-{pol}-", pol),
        )

    def raster_path(self, pol: str = "VH") -> str:
        """The GDAL path to the measurement TIFF *inside* the zip.

        This is the whole "read in place" decision in one line. Verified on all
        six granules in `data/sources.yaml`.
        """
        return f"/vsizip/{self.zip_path}/{self.polarization(pol).measurement}"

    def _xml(self, member: str) -> ET.Element:
        with zipfile.ZipFile(self.zip_path) as z:
            return ET.fromstring(z.read(member))

    # -- metadata -------------------------------------------------------------

    @cached_property
    def _manifest(self) -> str:
        with zipfile.ZipFile(self.zip_path) as z:
            return z.read(f"{self.safe_name}/manifest.safe").decode("utf-8", "replace")

    @cached_property
    def footprint_wkt(self) -> str:
        """Scene footprint from the manifest's ``gml:coordinates``.

        Those are ``lat,lon`` pairs — the opposite order to everything else in
        this codebase, and exactly the transposition `config.BBox` exists to
        stop being repeated. Converted here, once.
        """
        m = re.search(r"<gml:coordinates>([^<]+)</gml:coordinates>", self._manifest)
        if not m:
            raise SafeError(f"{self.scene_id}: manifest has no gml:coordinates footprint")
        pairs = [p.split(",") for p in m.group(1).split()]
        pts = [(float(lon), float(lat)) for lat, lon in pairs]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        body = ", ".join(f"{lon} {lat}" for lon, lat in pts)
        return f"POLYGON(({body}))"

    @cached_property
    def acquisition(self) -> Acquisition:
        pol = self.polarizations[-1] if "VH" not in self.polarizations else "VH"
        root = self._xml(self.polarization(pol).annotation)
        info = root.find("generalAnnotation/productInformation")
        image = root.find("imageAnnotation/imageInformation")

        rel = re.search(r"<safe:relativeOrbitNumber[^>]*>(\d+)<", self._manifest)
        return Acquisition(
            scene_id=self.scene_id,
            platform=_text(root, "adsHeader/missionId"),
            product_type=_text(root, "adsHeader/productType"),
            mode=_text(root, "adsHeader/mode"),
            polarizations=self.polarizations,
            start_time=_parse_time(_text(root, "adsHeader/startTime")),
            stop_time=_parse_time(_text(root, "adsHeader/stopTime")),
            absolute_orbit=int(_text(root, "adsHeader/absoluteOrbitNumber")),
            relative_orbit=int(rel.group(1)) if rel else 0,
            pass_direction=_text(info, "pass").upper(),
            platform_heading_deg=float(_text(info, "platformHeading")),
            platform_speed_ms=self._platform_speed(root),
            incidence_mid_deg=float(_text(image, "incidenceAngleMidSwath")),
            range_pixel_spacing_m=float(_text(image, "rangePixelSpacing")),
            azimuth_pixel_spacing_m=float(_text(image, "azimuthPixelSpacing")),
            width=int(_text(image, "numberOfSamples")),
            height=int(_text(image, "numberOfLines")),
            footprint_wkt=self.footprint_wkt,
        )

    @staticmethod
    def _platform_speed(root: ET.Element) -> float:
        """Orbital speed, averaged over the state vectors bracketing the scene.

        The annotation carries ~17 Earth-fixed state vectors at 10 s spacing
        around a 25 s acquisition, and |v| varies by well under 1 m/s across
        them, so the mean is as good as an interpolation and has no edge cases.
        """
        speeds = []
        for orbit in root.findall("generalAnnotation/orbitList/orbit"):
            v = orbit.find("velocity")
            if v is None:
                continue
            speeds.append(
                float(np.linalg.norm([float(_text(v, ax)) for ax in ("x", "y", "z")]))
            )
        if not speeds:
            raise SafeError("annotation has no orbit state vectors")
        return float(np.mean(speeds))

    # -- grids and LUTs -------------------------------------------------------

    @cached_property
    def grid(self) -> Grid:
        pol = "VH" if "VH" in self.polarizations else self.polarizations[-1]
        root = self._xml(self.polarization(pol).annotation)
        pts = root.findall("geolocationGrid/geolocationGridPointList/geolocationGridPoint")
        if not pts:
            raise SafeError("annotation has no geolocation grid")

        line = np.array([float(_text(p, "line")) for p in pts])
        pixel = np.array([float(_text(p, "pixel")) for p in pts])
        lines = np.unique(line)
        pixels = np.unique(pixel)
        if len(pts) != len(lines) * len(pixels):
            raise SafeError(
                f"geolocation grid is not a full lattice: {len(pts)} points for "
                f"{len(lines)}×{len(pixels)}"
            )

        def field(tag: str) -> np.ndarray:
            out = np.empty((len(lines), len(pixels)))
            li = {v: i for i, v in enumerate(lines)}
            pi = {v: i for i, v in enumerate(pixels)}
            for p in pts:
                out[li[float(_text(p, "line"))], pi[float(_text(p, "pixel"))]] = float(
                    _text(p, tag)
                )
            return out

        return Grid(
            lines=lines,
            pixels=pixels,
            incidence_deg=field("incidenceAngle"),
            slant_range_time_s=field("slantRangeTime"),
            latitude=field("latitude"),
            longitude=field("longitude"),
        )

    def r_over_v(self, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        """``R/V`` in seconds at given image coordinates — the displacement scale.

        Slant range comes from the geolocation grid's measured ``slantRangeTime``
        rather than from reconstructing the geometry with an Earth model. The
        product already knows the answer; recomputing it would only add a way to
        be wrong.
        """
        return self.grid.slant_range_at(line, pixel) / self.acquisition.platform_speed_ms

    @cached_property
    def _lut_cache(self) -> dict[str, Lut]:
        return {}

    def sigma_lut(self, pol: str = "VH") -> Lut:
        """The ``sigmaNought`` denominator: ``sigma0 = DN² / A²`` (§3.2)."""
        key = f"sigma:{pol}"
        if key not in self._lut_cache:
            root = self._xml(self.polarization(pol).calibration)
            vectors = root.findall("calibrationVectorList/calibrationVector")
            self._lut_cache[key] = Lut(
                lines=np.array([float(_text(v, "line")) for v in vectors]),
                pixels=_floats(vectors[0], "pixel"),
                values=np.array([_floats(v, "sigmaNought") for v in vectors]),
            )
        return self._lut_cache[key]

    def noise_lut(self, pol: str = "VH") -> tuple[Lut, list[tuple[int, int, np.ndarray, np.ndarray]]]:
        """Thermal noise, as the IPF splits it: a range LUT and a per-swath azimuth LUT.

        For a GRD product the noise power in DN² units is the product of the two:
        ``noise = noiseRangeLut(line, pixel) · noiseAzimuthLut(swath, line)``.
        §3.2 asks for thermal noise to be subtracted over water, and it matters
        more than it sounds — VH over calm water sits close enough to the noise
        floor that the residual noise pattern is a visible across-track ramp,
        and a CFAR threshold that does not know about it becomes range-dependent.

        Returns the range LUT and a list of ``(first_sample, last_sample, lines,
        values)`` azimuth blocks, one per IW sub-swath.
        """
        key = f"noise:{pol}"
        if key not in self._lut_cache:
            root = self._xml(self.polarization(pol).noise)
            vectors = root.findall("noiseRangeVectorList/noiseRangeVector")
            self._lut_cache[key] = Lut(
                lines=np.array([float(_text(v, "line")) for v in vectors]),
                pixels=_floats(vectors[0], "pixel"),
                values=np.array([_floats(v, "noiseRangeLut") for v in vectors]),
            )
            blocks = []
            for v in root.findall("noiseAzimuthVectorList/noiseAzimuthVector"):
                blocks.append(
                    (
                        int(_text(v, "firstRangeSample")),
                        int(_text(v, "lastRangeSample")),
                        _floats(v, "line"),
                        _floats(v, "noiseAzimuthLut"),
                    )
                )
            self._lut_cache[f"noise-az:{pol}"] = blocks  # type: ignore[assignment]
        return self._lut_cache[key], self._lut_cache[f"noise-az:{pol}"]  # type: ignore[return-value]

    def noise_at(self, lines: np.ndarray, col0: int, col1: int, pol: str = "VH") -> np.ndarray:
        """Thermal noise power in DN² over ``lines`` × ``arange(col0, col1)``."""
        rng, az_blocks = self.noise_lut(pol)
        noise = rng.at(lines, col0, col1)
        if not az_blocks:
            # IPF versions before 2.90 ship no azimuth vectors. Range-only noise
            # is still a large improvement over none, so degrade rather than fail.
            return noise
        scale = np.ones_like(noise)
        for first, last, az_lines, az_values in az_blocks:
            # The azimuth LUT is per IW sub-swath, so its range extent has to be
            # clipped into the window rather than applied at absolute columns.
            lo, hi = max(first, col0), min(last + 1, col1)
            if lo >= hi:
                continue
            column = np.interp(lines, az_lines, az_values).astype(np.float32)
            scale[:, lo - col0 : hi - col0] = column[:, None]
        return noise * scale

    # -- STAC -----------------------------------------------------------------

    def stac_item(self, *, source_url: str | None = None) -> dict:
        """The granule as a STAC 1.0.0 Item.

        §M3 says "scene as a STAC item", and the reason to honour that literally
        rather than inventing a scene table is that `stac_search` (§5) is a
        catalogue query. Modelling the catalogue as STAC keeps the door open to
        pointing the same tool at a real STAC API — which is what a customer
        deployment would have — instead of at a bespoke table only this project
        understands.
        """
        a = self.acquisition
        pairs = [
            tuple(float(v) for v in pt.split())
            for pt in self.footprint_wkt[len("POLYGON((") : -2].split(", ")
        ]
        lons = [p[0] for p in pairs]
        lats = [p[1] for p in pairs]
        return {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": a.scene_id,
            "collection": "sentinel-1-grd",
            "bbox": [min(lons), min(lats), max(lons), max(lats)],
            "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in pairs]]},
            "properties": {
                "datetime": a.acquisition_time.isoformat(),
                "start_datetime": a.start_time.isoformat(),
                "end_datetime": a.stop_time.isoformat(),
                "platform": a.platform,
                "constellation": "sentinel-1",
                "instruments": ["c-sar"],
                "sar:instrument_mode": a.mode,
                "sar:product_type": a.product_type,
                "sar:polarizations": list(a.polarizations),
                "sar:frequency_band": "C",
                "sar:observation_direction": LOOK_SIDE,
                "sat:orbit_state": a.pass_direction.lower(),
                "sat:absolute_orbit": a.absolute_orbit,
                "sat:relative_orbit": a.relative_orbit,
                "view:incidence_angle": a.incidence_mid_deg,
                "proj:shape": [a.height, a.width],
                # Not part of any STAC extension. Kept because every one of them
                # is an input to the azimuth-displacement correction, and a
                # catalogue that drops them forces the detector to reopen the
                # archive to re-derive geometry it already read once.
                "nightglass:platform_heading_deg": a.platform_heading_deg,
                "nightglass:platform_speed_ms": a.platform_speed_ms,
                "nightglass:range_bearing_deg": a.range_bearing_deg,
                "nightglass:azimuth_bearing_deg": a.azimuth_bearing_deg,
                "nightglass:pixel_spacing_m": [
                    a.range_pixel_spacing_m,
                    a.azimuth_pixel_spacing_m,
                ],
            },
            "assets": {
                pol: {
                    "href": self.raster_path(pol),
                    "type": "image/tiff; application=geotiff",
                    "title": f"{pol} measurement (read in place from the SAFE zip)",
                    "roles": ["data"],
                }
                for pol in self.polarizations
            },
            "links": ([{"rel": "via", "href": source_url}] if source_url else []),
        }


def find_granules(root: str | Path) -> list[Path]:
    """Every SAFE zip under ``root``, sorted. No recursion into archives."""
    return sorted(Path(root).glob("S1*_IW_GRDH_*.zip"))
