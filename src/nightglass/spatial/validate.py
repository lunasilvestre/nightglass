"""Measuring the detector against ground truth, instead of asserting it works.

Denmark exists in this project for exactly this (§3.1): it is the only AOI with
free point-level historical AIS, so it is the only place where a claim about the
matcher can be checked rather than stated. Everything here runs over the Danish
AOI and produces numbers that go in the README.

Three questions, in the order they have to be answered:

**1. Which way does the azimuth displacement go?** Derived one way, measured the
other — and the measurement won. The derivation in `geodesy.py` originally put a
receding target *forward* along the flight path; run against DMA truth, the
opposite sign was the one that halved match distances. The error was in the
derivation (a SAR processor focuses at the target's Doppler *zero crossing*, not
at the azimuth time whose Doppler equals the observed one), and the reason it was
caught is that the sign was left as a parameter to be measured.

**2. Does the correction actually help?** Reported as the change in match count
and median distance, with the correction on and off, over the same detections.

**3. Are the detections real?** Precision against AIS is only meaningful over
water the detector was allowed to look at, inside the scene footprint. Comparing
139 detections against every AIS vessel in a 2°×2° AOI — most of them outside the
imaged strip entirely — measures the footprint, not the detector.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from nightglass.config import BBox
from nightglass.spatial.ais import DMAFileSource, acquisition_window
from nightglass.spatial.coastline import Coastline
from nightglass.spatial.detect import DetectorConfig, detect_scene
from nightglass.spatial.geodesy import apparent_position, haversine_m
from nightglass.spatial.safe import SafeProduct

#: Distances at which agreement is reported. 500 m is §5's match radius; the
#: others bracket it so a reader can see whether the distribution is tight or
#: whether 500 m merely happens to be where the curve is.
BANDS = (100, 200, 300, 500, 1000, 2000)


@dataclass
class ShiftReport:
    scene_id: str
    detections: int
    ais_vessels: int
    ais_in_footprint: int
    predicted_shift_median_m: float
    predicted_shift_p90_m: float
    predicted_shift_max_m: float
    by_variant: dict[str, dict] = field(default_factory=dict)
    best_sign: int = 0
    length_agreement: dict | None = None

    def render(self) -> str:
        lines = [
            f"scene              {self.scene_id}",
            f"detections         {self.detections}",
            (
                f"AIS vessels        {self.ais_vessels} in AOI, "
                f"{self.ais_in_footprint} inside the scene footprint"
            ),
            "",
            (
                f"predicted azimuth displacement   median "
                f"{self.predicted_shift_median_m:6.0f} m"
                f"   p90 {self.predicted_shift_p90_m:6.0f} m"
                f"   max {self.predicted_shift_max_m:6.0f} m"
            ),
            "",
            f"  {'variant':<38} " + "  ".join(f"<{b}m" for b in BANDS) + "   median",
        ]
        for name, r in self.by_variant.items():
            cells = "  ".join(f"{r['within'][b]:>5d}" for b in BANDS)
            lines.append(f"  {name:<38} {cells}   {r['median']:7.0f} m")
        lines += [
            "",
            f"  sign confirmed by measurement: {self.best_sign:+d}",
        ]
        if self.length_agreement:
            la = self.length_agreement
            lines += [
                "",
                f"  detected vs AIS length, {la['n']} matched vessels:",
                (
                    f"    median detected {la['median_det']:.0f} m   "
                    f"median AIS {la['median_ais']:.0f} m   "
                    f"median ratio {la['median_ratio']:.2f}×"
                ),
                f"    correlation r = {la['r']:.3f}",
            ]
        return "\n".join(lines)


def _interpolate_tracks(positions, t0):
    """One position per vessel, linearly interpolated onto the acquisition instant.

    Vessels with no report on both sides of the instant are dropped rather than
    extrapolated. Extrapolating produces a position that then fails to match,
    which manufactures a dark detection out of a gap in the feed — the exact
    "innocent explanation" §7 says the system must not turn into a conclusion.
    """
    tracks = defaultdict(list)
    for p in positions:
        tracks[p.mmsi].append(p)

    out = []
    for mmsi, pts in tracks.items():
        pts.sort(key=lambda q: q.timestamp)
        ts = np.array([(q.timestamp - t0).total_seconds() for q in pts])
        if ts.min() > 0 or ts.max() < 0:
            continue
        lon = float(np.interp(0.0, ts, [q.lon for q in pts]))
        lat = float(np.interp(0.0, ts, [q.lat for q in pts]))
        j = int(np.argmin(np.abs(ts)))
        out.append(
            {
                "mmsi": mmsi,
                "lon": lon,
                "lat": lat,
                "sog_ms": pts[j].sog_ms or 0.0,
                "cog_deg": pts[j].cog_deg,
                "length_m": pts[j].length_m,
                "name": pts[j].name,
            }
        )
    return out


def validate_azimuth_shift(
    *,
    granule: str | Path,
    ais_file: str | Path,
    bbox: BBox,
    window_min: float = 11.0,
    coastline: Coastline | None = None,
    config: DetectorConfig | None = None,
    out_dir: Path | None = None,
) -> ShiftReport:
    product = SafeProduct(granule)
    acq = product.acquisition
    detections, _measurements, _run = detect_scene(
        product, config=config, bbox=bbox, coastline=coastline
    )

    start, end = acquisition_window(acq.acquisition_time, window_min)
    truth = _interpolate_tracks(
        DMAFileSource(ais_file).positions(bbox, start, end), acq.acquisition_time
    )
    usable = [t for t in truth if t["cog_deg"] is not None]

    from shapely import wkt as shapely_wkt
    from shapely.geometry import Point

    footprint = shapely_wkt.loads(acq.footprint_wkt)
    in_footprint = [t for t in usable if footprint.contains(Point(t["lon"], t["lat"]))]

    dlon = np.array([d.lon for d in detections])
    dlat = np.array([d.lat for d in detections])
    alon = np.array([t["lon"] for t in in_footprint])
    alat = np.array([t["lat"] for t in in_footprint])
    sog = np.array([t["sog_ms"] for t in in_footprint])
    cog = np.array([t["cog_deg"] for t in in_footprint])

    # Incidence and R/V at each vessel's own position, not a scene-wide mean:
    # both vary ~20% across the swath, and a mid-swath constant would leave a
    # residual that grows toward the edges and look like a sign error.
    with_geom = _beam_geometry_at(product, alon, alat)
    inc, rv = with_geom["incidence_deg"], with_geom["r_over_v_s"]

    report = ShiftReport(
        scene_id=acq.scene_id,
        detections=len(detections),
        ais_vessels=len(truth),
        ais_in_footprint=len(in_footprint),
        predicted_shift_median_m=0.0,
        predicted_shift_p90_m=0.0,
        predicted_shift_max_m=0.0,
    )
    if not len(detections) or not len(alon):
        return report

    shift = np.abs(
        rv * sog * np.cos(np.radians(cog - acq.range_bearing_deg)) * np.sin(np.radians(inc))
    )
    report.predicted_shift_median_m = float(np.median(shift))
    report.predicted_shift_p90_m = float(np.percentile(shift, 90))
    report.predicted_shift_max_m = float(shift.max())

    def score(label: str, blon, blat):
        d = haversine_m(dlon[:, None], dlat[:, None], blon[None, :], blat[None, :])
        nearest = d.min(axis=1)
        report.by_variant[label] = {
            "within": {b: int((nearest < b).sum()) for b in BANDS},
            "median": float(np.median(nearest)),
            "nearest": nearest,
            "argmin": d.argmin(axis=1),
        }

    score("interpolated to acquisition, no correction", alon, alat)
    for sign in (+1, -1):
        ap_lon, ap_lat = apparent_position(
            alon, alat, sog, cog, inc, rv,
            acq.range_bearing_deg, acq.azimuth_bearing_deg, sign=sign,
        )
        score(f"+ azimuth-displacement correction (sign {sign:+d})", ap_lon, ap_lat)

    best = max(
        (k for k in report.by_variant if "sign" in k),
        key=lambda k: report.by_variant[k]["within"][500],
    )
    report.best_sign = 1 if "+1" in best else -1

    # Length agreement is an independent check on the detector that owes nothing
    # to the matcher: AIS carries each vessel's real length, so for matched pairs
    # the detected extent can be compared against a number nobody estimated.
    v = report.by_variant[best]
    pairs = [
        (detections[i].length_m, in_footprint[int(v["argmin"][i])]["length_m"])
        for i in range(len(detections))
        if v["nearest"][i] < 500
    ]
    pairs = [(a, b) for a, b in pairs if a and b and b > 0]
    if len(pairs) >= 5:
        det = np.array([a for a, _ in pairs])
        ais = np.array([b for _, b in pairs])
        report.length_agreement = {
            "n": len(pairs),
            "median_det": float(np.median(det)),
            "median_ais": float(np.median(ais)),
            "median_ratio": float(np.median(det / ais)),
            "r": float(np.corrcoef(det, ais)[0, 1]),
        }

    if out_dir is not None:
        from nightglass.spatial.plots import plot_validation

        plot_validation(report, det_pairs=pairs, out_dir=out_dir)
    return report


def _beam_geometry_at(product: SafeProduct, lons: np.ndarray, lats: np.ndarray) -> dict:
    """Incidence angle and R/V at given ground positions.

    Inverting the geolocation with the TPS transformer rather than assuming a
    mid-swath value — see the class docstring for why that matters more than it
    sounds.
    """
    import rasterio
    from rasterio.transform import GCPTransformer

    with rasterio.open(product.raster_path("VH")) as src:
        gcps, _ = src.gcps
    tr = GCPTransformer(gcps, tps=True)
    rows, cols = tr.rowcol(lons.tolist(), lats.tolist(), op=lambda v: v)
    rows = np.clip(np.atleast_1d(rows).astype(float), 0, product.acquisition.height - 1)
    cols = np.clip(np.atleast_1d(cols).astype(float), 0, product.acquisition.width - 1)
    return {
        "incidence_deg": product.grid.incidence_at(rows, cols),
        "r_over_v_s": product.r_over_v(rows, cols),
    }
