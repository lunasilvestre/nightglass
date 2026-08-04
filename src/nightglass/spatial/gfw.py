"""Global Fishing Watch detections as a reference layer — the fifth provisioning input.

§3.1 is explicit that GFW's SAR detections are a layer to *cross-check against*,
never our own correlation, and `ais.GFWDetectionSource` refuses to serve them to
`ais_match` for that reason: they have already been matched against AIS upstream
by someone else, and claiming that work as ours would unravel in a technical
round. This module keeps that line by keeping the two questions apart.

**What we may claim.** Do our detections and theirs find the same vessels? That
is a geometric comparison between two independent detectors over the *identical*
granule, and it is ours to state.

**What we may only cite.** Each GFW detection carries a `matched` flag — their
assessment against their AIS. Over Portugal we have no AIS at all, so that flag
is information we cannot otherwise obtain, and it is reported with their name on
it and their licence attached, never merged into a `Match`.

The comparison is stronger than the spec assumed it would be. `4wings/report`
returns gridded aggregates, which would only support "they saw N in this box and
we saw M". But `4wings/tile/position` returns individual detections whose feature
id is `<granule_id>;<lon>;<lat>` — and that granule is one of the ones
`make fetch-granules` puts on disk. So
this is detection-for-detection over the same pixels, not a comparison of counts.

Two API details that cost time and are not documented anywhere:

* **The tiles are MVT and there is no JSON.** `format=JSON` returns HTTP 422;
  only `format=MVT` is served. Rather than take a protobuf dependency into the
  fetcher image to decode geometry we do not need, the ids are read straight out
  of the tile bytes — they are contiguous ASCII in the layer's value table, and
  they already carry the position, so the geometry is redundant. That is a
  deliberate shortcut and `_ids` says so.
* **`filters[0]=matched='true'|'false'` works and partitions cleanly.** Verified
  over Lisbon z9/242/196 on 2026-06-13: 13 detections unfiltered, 10 matched,
  3 unmatched. Because both halves are fetched, their sum is a free consistency
  check on every tile, and `fetch_reference` raises if it ever fails.

ONLINE. This module runs on the provision network and never inside the enclave —
the same posture as the model weights, the corpus and the coastline.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from nightglass.config import BBox
from nightglass.schemas import Detection

BASE = "https://gateway.api.globalfishingwatch.org/v3/4wings/tile/position"
DATASET = "public-global-sar-presence:latest"
LICENCE = "Global Fishing Watch SAR vessel detections, CC BY-NC 4.0 (non-commercial)."

#: Zoom for the tile requests. 9 is the level the M3 probe verified, and it is a
#: reasonable trade: one z9 tile is ~78 km across at this latitude, so a typical
#: AOI is a dozen requests rather than hundreds, and the tiles comfortably
#: over-cover the box (detections outside it are filtered afterwards).
ZOOM = 9

#: `<granule_id>;<lon>;<lat>`, as GFW writes it into each feature's id.
_ID = re.compile(r"(S1[AB]_[A-Z0-9_]+);(-?\d+\.\d+);(-?\d+\.\d+)")


class GfwError(RuntimeError):
    """Raised with the HTTP status, because the failure modes here are specific."""


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


@dataclass(frozen=True)
class GfwDetection:
    """One published detection. `matched` is GFW's assessment, not ours."""

    granule_id: str
    lon: float
    lat: float
    matched: bool


# -- fetching (ONLINE) --------------------------------------------------------


def tiles_for(bbox: BBox, zoom: int = ZOOM) -> list[tuple[int, int, int]]:
    """Every slippy tile covering `bbox`, as (z, x, y)."""
    x0, y0 = _tile(bbox.min_lon, bbox.max_lat, zoom)
    x1, y1 = _tile(bbox.max_lon, bbox.min_lat, zoom)
    return [
        (zoom, x, y)
        for x in range(min(x0, x1), max(x0, x1) + 1)
        for y in range(min(y0, y1), max(y0, y1) + 1)
    ]


def _tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    lat = max(min(lat, 85.05112878), -85.05112878)
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def fetch_reference(
    bbox: BBox,
    start: datetime,
    end: datetime,
    *,
    token: str,
    zoom: int = ZOOM,
    timeout: float = 60.0,
    progress: bool = False,
) -> list[GfwDetection]:
    """Fetch every published detection over `bbox` in the window. ONLINE."""
    if not token:
        raise GfwError(
            "no GFW_TOKEN. It lives in ~/.config/eo-credentials.env, not in this "
            "repo's .env — run `source scripts/load-env.sh` first, or get one at "
            "https://globalfishingwatch.org/our-apis/tokens"
        )

    window = f"{start:%Y-%m-%d},{end:%Y-%m-%d}"
    found: dict[tuple[str, float, float], GfwDetection] = {}

    with httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {token}"}) as client:
        for z, x, y in tiles_for(bbox, zoom):
            per_flag = {
                flag: _ids(_tile_bytes(client, z, x, y, window, flag))
                for flag in (True, False)
            }
            everything = _ids(_tile_bytes(client, z, x, y, window, None))

            # Free consistency check on every tile: the two filtered halves must
            # partition the unfiltered set. If GFW ever changes the filter
            # semantics this fails loudly here rather than silently halving a
            # count that ends up in the README.
            partitioned = per_flag[True] | per_flag[False]
            if partitioned != everything:
                raise GfwError(
                    f"tile {z}/{x}/{y}: matched + unmatched ({len(partitioned)}) does "
                    f"not partition the unfiltered set ({len(everything)}). The "
                    "filter semantics have changed; do not trust the flags."
                )

            for flag, ids in per_flag.items():
                for granule, lon, lat in ids:
                    if bbox.min_lon <= lon <= bbox.max_lon and bbox.min_lat <= lat <= bbox.max_lat:
                        found[(granule, lon, lat)] = GfwDetection(granule, lon, lat, flag)
            if progress:
                print(f"  {z}/{x}/{y}  {len(everything):>4} detections", flush=True)

    return sorted(found.values(), key=lambda d: (d.granule_id, d.lat, d.lon))


def _tile_bytes(
    client: httpx.Client, z: int, x: int, y: int, window: str, matched: bool | None
) -> bytes:
    params = [
        ("datasets[0]", DATASET),
        ("date-range", window),
        ("format", "MVT"),
    ]
    if matched is not None:
        params.append(("filters[0]", f"matched='{str(matched).lower()}'"))
    r = client.get(f"{BASE}/{z}/{x}/{y}", params=params)
    # 204 No Content is the ordinary answer for a tile with no detections in it,
    # not a failure — most tiles over an AOI are open water or land. Treating it
    # as an error makes the fetcher fail on the first empty tile, which is what
    # the first run did.
    if r.status_code == 204:
        return b""
    if r.status_code != 200:
        raise GfwError(f"tile {z}/{x}/{y}: HTTP {r.status_code} {r.text[:200]}")
    return r.content


def _ids(payload: bytes) -> set[tuple[str, float, float]]:
    """Read `<granule>;<lon>;<lat>` out of a vector tile without decoding it.

    A deliberate shortcut, and worth being explicit about. A full MVT decoder
    would mean a protobuf dependency in the fetcher image to recover geometry
    that is already in the id string — the id *is* the position, to eight
    decimal places. The ids are contiguous printable ASCII inside the layer's
    value table, and the pattern is specific enough (a Sentinel-1 granule name
    followed by two signed decimals) that a false positive would have to be a
    granule name that is not a granule name.
    """
    text = payload.decode("latin-1")
    return {(m.group(1), float(m.group(2)), float(m.group(3))) for m in _ID.finditer(text)}


# -- on disk ------------------------------------------------------------------


def reference_path(root: str | Path, aoi_name: str) -> Path:
    return Path(root) / f"gfw_{aoi_name.lower()}.json"


def write_reference(
    detections: list[GfwDetection],
    path: str | Path,
    *,
    aoi: str,
    bbox: BBox,
    start: datetime,
    end: datetime,
    zoom: int = ZOOM,
) -> Path:
    """Write the reference with its provenance beside it, not in a commit message."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source": "Global Fishing Watch",
                "dataset": DATASET,
                "endpoint": f"{BASE}/{{z}}/{{x}}/{{y}}",
                "licence": LICENCE,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "aoi": aoi,
                "bbox": bbox.as_list(),
                "window": [start.isoformat(), end.isoformat()],
                "zoom": zoom,
                "count": len(detections),
                "granules": sorted({d.granule_id for d in detections}),
                "detections": [asdict(d) for d in detections],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def load_reference(path: str | Path) -> tuple[list[GfwDetection], dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise GfwError(
            f"no GFW reference at {path}. It is a provisioning-time fetch, like "
            "the coastline and the model weights: run `make fetch-gfw`."
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [GfwDetection(**d) for d in doc["detections"]], doc


# -- the comparison (OFFLINE) -------------------------------------------------


@dataclass
class Comparison:
    """Two independent detectors over one granule, and what each saw.

    `their_unmatched_on_agreed` is kept separate from everything else because it
    is the one number here that is not ours. It is GFW's AIS assessment of the
    detections we both found, cited, not computed.
    """

    granule_id: str
    radius_m: float
    ours: int = 0
    theirs: int = 0
    agreed: int = 0
    ours_only: int = 0
    theirs_only: int = 0
    distances_m: list[float] = None  # type: ignore[assignment]
    their_matched_on_agreed: int = 0
    their_unmatched_on_agreed: int = 0

    #: For each detection only we found, how far to the nearest one we both
    #: found. The diagnostic that turns "we saw 84 more than they did" into a
    #: statement about what those 84 are — a second vessel is hundreds of
    #: metres away, a fragment of the same vessel is not.
    ours_only_to_agreed_m: list[float] = None  # type: ignore[assignment]
    fragment_radius_m: float = 200.0

    def __post_init__(self) -> None:
        if self.distances_m is None:
            self.distances_m = []
        if self.ours_only_to_agreed_m is None:
            self.ours_only_to_agreed_m = []

    @property
    def likely_fragments(self) -> int:
        return sum(1 for d in self.ours_only_to_agreed_m if d <= self.fragment_radius_m)

    @property
    def distinct_targets(self) -> int:
        """Our detection count with the likely fragments folded back in."""
        return self.ours - self.likely_fragments

    @property
    def recall(self) -> float:
        """Fraction of GFW's detections we also found. Not a true recall — GFW is
        a detector, not ground truth — but it is the comparable number."""
        return self.agreed / self.theirs if self.theirs else 0.0

    @property
    def median_distance_m(self) -> float:
        return _median(self.distances_m)

    def render(self) -> str:
        lines = [
            f"granule       {self.granule_id}",
            f"match radius  {self.radius_m:.0f} m",
            f"ours          {self.ours} detections   (nightglass-cfar)",
            f"GFW           {self.theirs} detections   (published layer, same granule)",
            "",
            (
                f"both saw      {self.agreed}   ({self.recall:.0%} of GFW's, "
                f"median separation {self.median_distance_m:.0f} m)"
            ),
            f"GFW only      {self.theirs_only}   they found, we did not",
            f"ours only     {self.ours_only}   we found, they did not",
        ]
        if self.ours_only_to_agreed_m:
            frag = self.likely_fragments
            lines += [
                "",
                f"of those {self.ours_only}, how far to the nearest detection we BOTH saw:",
                (
                    f"  within {self.fragment_radius_m:.0f} m   {frag}   "
                    f"({frag / self.ours_only:.0%})   — too close to be a second vessel"
                ),
                f"  median      {_median(self.ours_only_to_agreed_m):.0f} m",
            ]
            lines += (
                [
                    "",
                    (
                        f"So this granule holds nearer {self.distinct_targets} distinct "
                        f"targets than {self.ours} — one vessel is being counted"
                    ),
                    "several times. Raise DetectorConfig.merge_radius_m.",
                ]
                if frag
                else [
                    "",
                    "None of them is a fragment of something we already counted: the",
                    "residue is genuinely isolated, so it is extra sensitivity or extra",
                    "false alarms, not double-counting.",
                ]
            )
        if self.agreed:
            lines += [
                "",
                "GFW's own AIS assessment of the detections we both found —",
                "their computation, cited not reproduced (CC BY-NC 4.0):",
                f"  matched     {self.their_matched_on_agreed}",
                f"  unmatched   {self.their_unmatched_on_agreed}",
            ]
        lines += [
            "",
            "Neither column is ground truth. Two detectors agreeing is weaker",
            "evidence than the AIS validation banked over Denmark — it says the",
            "detector generalises, not that either of them is right.",
        ]
        return "\n".join(lines)


def compare(
    ours: list[Detection],
    theirs: list[GfwDetection],
    *,
    granule_id: str,
    radius_m: float = 500.0,
) -> Comparison:
    """Greedy nearest-neighbour pairing between two detection sets over one granule.

    Greedy rather than optimal: at a 500 m radius over open water the vessels are
    far enough apart that the assignment is unambiguous, and an optimal matcher
    would be more code defending a difference that does not arise. Where it could
    arise — two hulls within 500 m of each other, as in a harbour — the AOI is
    already dominated by the coastal clutter this project does not claim.
    """
    import numpy as np

    from nightglass.spatial.geodesy import haversine_m

    mine = [d for d in ours if d.scene_id == granule_id]
    yours = [d for d in theirs if d.granule_id == granule_id]
    report = Comparison(granule_id=granule_id, radius_m=radius_m, ours=len(mine), theirs=len(yours))
    if not mine or not yours:
        report.ours_only = len(mine)
        report.theirs_only = len(yours)
        return report

    my_lon = np.array([d.lon for d in mine])
    my_lat = np.array([d.lat for d in mine])
    taken: set[int] = set()

    for t in yours:
        d_m = haversine_m(
            np.full(my_lon.shape, t.lon), np.full(my_lat.shape, t.lat), my_lon, my_lat
        )
        order = np.argsort(d_m)
        hit = next((int(i) for i in order if int(i) not in taken and d_m[i] <= radius_m), None)
        if hit is None:
            report.theirs_only += 1
            continue
        taken.add(hit)
        report.agreed += 1
        report.distances_m.append(float(d_m[hit]))
        if t.matched:
            report.their_matched_on_agreed += 1
        else:
            report.their_unmatched_on_agreed += 1

    report.ours_only = len(mine) - len(taken)

    # How isolated is each detection only we found? A genuine extra vessel sits
    # hundreds of metres from anything; a fragment of a vessel we both found
    # sits on top of it. Without this the excess reads as "our detector is more
    # sensitive", which is the flattering interpretation and the wrong one.
    if taken and report.ours_only:
        agreed_lon = np.array([mine[i].lon for i in sorted(taken)])
        agreed_lat = np.array([mine[i].lat for i in sorted(taken)])
        for i, d in enumerate(mine):
            if i in taken:
                continue
            gap = haversine_m(
                np.full(agreed_lon.shape, d.lon),
                np.full(agreed_lat.shape, d.lat),
                agreed_lon,
                agreed_lat,
            )
            report.ours_only_to_agreed_m.append(float(gap.min()))

    return report
