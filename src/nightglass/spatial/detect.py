"""The vessel detector — §5's `detect_vessels`, over real pixels.

This is our own detector, not a published detection layer. §3.1 is blunt about
why that distinction matters: GFW's SAR detections are a *reference* layer to
cross-check against, and presenting someone else's detections as your
computation is the kind of claim that unravels when someone asks how it works.

The method, in order, and the reason for each step:

**VH, not VV** (§3.2). Cross-polarised backscatter from a rough sea surface is
much weaker than co-polarised, while a ship's dihedral structure returns strongly
in both. The contrast is what is being thresholded, so VH gives several dB of it
for free. Every granule on disk is dual-pol VV+VH.

**DN is not backscatter** (§3.2). The measurement raster holds digital numbers.
``sigma0 = (DN² − noise) / A²`` with ``A`` the sigmaNought LUT and ``noise`` the
thermal-noise LUT, both from the product's own annotation. Skipping the noise
term leaves an across-track ramp in the background — VH over calm water sits
close to the noise floor — and a threshold that does not know about it becomes
quietly range-dependent, stricter in some parts of the swath than others.

**Clutter is estimated per block, with targets censored.** A two-parameter CFAR
needs a local mean and standard deviation of the sea. Estimating them over a
32×32 block (320 m) is ~1000 samples, plenty — but a bright ship inside the block
inflates its own background and can threshold itself away. So the statistics are
computed twice, the second time excluding pixels far above the first estimate.

**Land is masked twice, and it needs to be.** The first mask is derived from the
scene itself (`land_mask`): land backscatters 10–15 dB above calm sea at VH, so
thresholding a fine block mean finds it without any auxiliary data. That mask
handles the mainland and keeps the clutter statistics clean.

It cannot handle skerries, and not because it is badly tuned. It has to *open*
the mask — erode then dilate — before buffering, or every bright vessel becomes
its own island and the mask deletes the detections. Opening removes bright
objects smaller than the structuring element, and a 100 m rock is exactly that.
Run over the Kattegat with the data-derived mask alone, the detector drew a neat
line of "vessels" down the Swedish archipelago off Gothenburg with almost no AIS
anywhere near them. So the second mask is a real shoreline — GSHHG at full
resolution, fetched at provisioning time and clipped to the AOI, applied in
lon/lat after georeferencing. See `coastline.py`.

**Connected components come from `rasterio.features.shapes`.** Vectorising the
threshold mask gives polygons directly, so blob grouping, area and shape all come
out of one call with no image-labelling dependency at all. Length and heading are
then the major axis of each polygon's minimum rotated rectangle.

**Position comes from a thin-plate-spline GCP transform.** Measured on granule
S1D_20260717T052324: fitting an affine/polynomial through the 210 tie points
leaves a mean 40 m and worst-case **185 m** error, because the geolocation grid
of a 250 km swath is not a polynomial surface. TPS interpolates through the tie
points exactly — residual 0.000 m — for about 15× the transform cost, which is
0.15 s per 20,000 points and therefore irrelevant. At a 500 m match radius, 185 m
of avoidable geolocation error is over a third of the budget.

What this is not: an adaptive-sea-state CFAR, a wake analyser, or a classifier.
§8's limitations list says "no CFAR tuning" and that stays true.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import numpy as np
import rasterio
from rasterio.features import shapes as raster_shapes
from rasterio.transform import GCPTransformer
from rasterio.windows import Window
from shapely.geometry import shape as to_shape

from nightglass.config import BBox
from nightglass.schemas import Detection, Provenance
from nightglass.spatial.coastline import Coastline
from nightglass.spatial.geodesy import bearing_deg, haversine_m
from nightglass.spatial.safe import SafeProduct

DETECTOR_NAME = "nightglass-cfar"
DETECTOR_VERSION = "1.0"


@dataclass(frozen=True)
class DetectorConfig:
    """Every tunable in one place, and every one of them ends up in the run record.

    A detection whose parameters are not recorded cannot be reproduced or
    defended, and §7's whole argument is that an ungradeable output cannot enter
    the intelligence cycle. `DetectorRun.parameters` is this object.
    """

    polarization: str = "VH"

    #: CFAR block edge in pixels. 32 px = 320 m at IW GRDH's 10 m spacing, which
    #: is ~1000 samples per clutter estimate and small enough that sea state
    #: varies little across one.
    block: int = 32

    #: Threshold is ``mean + k·std`` of the censored block statistics. 8 is
    #: deliberately conservative: this pipeline's failure mode is false darks
    #: (§3.2's "if your pipeline reports 40% dark, it's broken"), and a false
    #: detection is always dark because nothing real is there to match it.
    k: float = 8.0

    #: Pixels more than this many standard deviations above the first-pass mean
    #: are excluded from the second, so a ship does not raise the background it
    #: is measured against. In sigmas, not in multiples of the mean — see
    #: `_block_stats`.
    censor: float = 4.0

    #: A detection must also sit this far above the local noise-equivalent
    #: sigma0. Without it, a block of near-noise-floor water has mean and std
    #: both near zero and the CFAR threshold collapses onto the noise.
    min_snr_db: float = 10.0

    #: The land mask gets its own, much finer block than the CFAR statistics.
    #: 8 px = 80 m. At the CFAR block size (320 m) a coastline is a smear of
    #: half-bright blocks and the mask edge lands hundreds of metres from the
    #: actual shore; the first overview render showed the consequence as a
    #: continuous string of "detections" tracing the entire Danish coast.
    land_block: int = 8

    #: Blocks whose mean sigma0 exceeds this are land. Measured over the
    #: Kattegat: open water sits near −29 dB VH, land at −13 to −18 dB, so the
    #: separation is wide and the threshold is not delicate.
    land_sigma0_db: float = -22.0

    #: Morphological opening, in blocks, applied to the land mask before it is
    #: buffered. Land is large and contiguous; a ship is neither. Without this,
    #: a fine-grained mask would flag every bright vessel as its own little
    #: island and then mask the detection away.
    land_open_blocks: int = 2

    #: Buffer grown around the opened coastline, in blocks. 10 × 80 m = 800 m.
    #: This is the honest cost of a data-derived mask: vessels genuinely inside
    #: 800 m of shore are not reported, and `DetectorRun.land_fraction` says how
    #: much of the AOI that removed.
    land_dilate_blocks: int = 10

    #: A block more than this fraction zero-fill (outside the swath) is dropped.
    max_fill_fraction: float = 0.1

    min_length_m: float = 15.0

    #: Above this, it is not a vessel this pipeline should be claiming — it is
    #: land leakage, a bridge, or a wind farm row. The largest ships afloat are
    #: ~400 m.
    max_length_m: float = 450.0

    #: Smallest blob accepted, in pixels. Single-pixel spikes are speckle.
    min_pixels: int = 4

    #: Detection and measurement use two different thresholds, on purpose.
    #:
    #: `k` has to be high or the scene fills with false alarms — and a blob cut
    #: at 8 sigma is the vessel's brightest structure, not its hull. Measured
    #: against AIS-reported lengths that gave a median ratio of **0.30×** with
    #: correlation **r = 0.015**: not merely a scale error, no relationship at
    #: all, because at that threshold the blob's extent tracks peak brightness
    #: rather than length. A number with r = 0.015 is not a measurement.
    #:
    #: So each accepted detection is re-grown locally at `k_grow` sigma, seeded
    #: from the blob that passed `k`. Detection keeps its low false-alarm rate;
    #: measurement gets a threshold near the sea-clutter tail where the hull
    #: actually is.
    k_grow: float = 2.5

    #: Bound on the re-grow window, in pixels either side of the blob. Caps how
    #: far a low threshold can bleed into a neighbouring target or an unmasked
    #: bright feature.
    grow_halfwidth_px: int = 40

    #: Heading is only reported for blobs at least this long and this elongated.
    #: Below it the minimum rotated rectangle snaps to the pixel grid and reports
    #: the image axis rather than the vessel.
    min_heading_px: float = 4.0
    min_heading_aspect: float = 1.6

    #: Detections closer together than this are one target, not several.
    #:
    #: Azimuth smear draws a moving vessel as a streak, and the region-growing
    #: sizer picks the streak up as separate blobs — so without this a single
    #: ship is reported several times, and every count downstream is inflated.
    #:
    #: Not a guess. Over the Kattegat, 45 matched detections resolved to **18
    #: distinct MMSIs**, one vessel accounting for six of them, and single-link
    #: clustering at 100 m, 200 m and 300 m produced **zero clusters containing
    #: more than one MMSI** — every group of nearby detections was the same ship,
    #: confirmed by transponder. 200 m sits in the middle of the range that AIS
    #: says is safe.
    #:
    #: Single-link rather than complete-link on purpose: smear is a *linear*
    #: streak, so a chain of fragments along the flight direction is exactly the
    #: shape that should collapse, and the MMSI check says the chaining does not
    #: over-reach at these radii.
    #:
    #: Set to 0 to disable and recover the pre-merge counts. Doing so does not
    #: make the numbers better, it makes them bigger — see NOTES finding 46.
    merge_radius_m: float = 200.0

    #: Rows read at once. 1024 × 26,564 × 4 bytes is ~109 MB per working array.
    window_lines: int = 1024

    #: Overlap between windows so a vessel straddling a boundary is measured
    #: whole in one of them. 64 px = 640 m, above `max_length_m`.
    overlap_lines: int = 64


@dataclass(frozen=True)
class Measurement:
    """Everything about a detection that §5's `Detection` has no field for.

    Image coordinates, blob width, and the beam geometry at that exact pixel.
    They travel beside the detection rather than inside it because §5 fixed the
    `Detection` contract and both the MCP server and the agent bind to it — but
    they are not optional extras: `incidence_deg` and `r_over_v_s` are the two
    inputs to the azimuth-displacement correction, and without them the dark
    query would have to reopen the granule to re-derive geometry already read.
    """

    detection_id: str
    row: float
    col: float
    width_m: float
    incidence_deg: float
    r_over_v_s: float
    cfar_margin_db: float


@dataclass
class DetectorRun:
    """One execution of the detector over one scene — the provenance of a detection set."""

    scene_id: str
    polarization: str
    detector: str = DETECTOR_NAME
    version: str = DETECTOR_VERSION
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    seconds: float = 0.0
    parameters: dict = field(default_factory=dict)
    aoi_bbox: list[float] | None = None
    coastline: str | None = None
    pixels_examined: int = 0
    pixels_water: int = 0
    pixels_land_masked: int = 0
    candidates: int = 0
    detections: int = 0
    rejected_small: int = 0
    rejected_large: int = 0
    rejected_outside_aoi: int = 0
    rejected_on_coastline: int = 0
    #: Detections folded into a brighter neighbour because they were the same
    #: vessel. Reported, not hidden: it is the difference between a count of
    #: blobs and a count of ships.
    merged_fragments: int = 0
    merged_targets: int = 0
    water_sigma0_db: float | None = None
    nesz_db: float | None = None
    px_above_cfar: int = 0
    px_above_floor: int = 0
    px_hot: int = 0

    @property
    def land_fraction(self) -> float:
        return self.pixels_land_masked / max(self.pixels_examined, 1)

    def render(self) -> str:
        lines = [
            f"scene         {self.scene_id}  [{self.polarization}]",
            f"detector      {self.detector} {self.version}",
            f"aoi bbox      {self.aoi_bbox}",
            f"coastline     {self.coastline or 'none — data-derived land mask only'}",
            (
                f"examined      {self.pixels_examined:,} px"
                f"   water {self.pixels_water:,}"
                f"   land-masked {self.pixels_land_masked:,} ({self.land_fraction:.1%})"
            ),
            f"water sigma0  {self.water_sigma0_db:+.1f} dB"
            f"   NESZ {self.nesz_db:+.1f} dB"
            f"   ({'sea is above the noise floor' if (self.water_sigma0_db or -99) > (self.nesz_db or 0) else 'sea is BELOW the noise floor — noise-limited scene'})"
            if self.water_sigma0_db is not None and self.nesz_db is not None
            else "water sigma0  n/a",
            (
                f"threshold      CFAR passes {self.px_above_cfar:,} px"
                f"   NESZ floor passes {self.px_above_floor:,} px"
                f"   both {self.px_hot:,} px"
                f"   (binding: "
                f"{'NESZ floor' if self.px_above_floor < self.px_above_cfar else 'CFAR'})"
            ),
            (
                f"candidates    {self.candidates}"
                f"   rejected <{self.parameters.get('min_length_m')} m: {self.rejected_small}"
                f"   >{self.parameters.get('max_length_m')} m: {self.rejected_large}"
                f"   outside AOI: {self.rejected_outside_aoi}"
                f"   on coastline: {self.rejected_on_coastline}"
            ),
            (
                f"merged        {self.merged_fragments} fragment(s) folded into a brighter "
                f"neighbour within {self.parameters.get('merge_radius_m')} m"
                if self.merged_fragments
                else "merged        none — no two detections within "
                f"{self.parameters.get('merge_radius_m')} m"
            ),
            f"detections    {self.detections}   (targets, not blobs)",
            f"elapsed       {self.seconds:.1f} s",
        ]
        return "\n".join(lines)


def _block_stats(
    values: np.ndarray, valid: np.ndarray, block: int, censor: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Censored per-block mean, standard deviation and valid-fraction.

    Two passes. The first is contaminated by whatever bright targets sit in the
    block; the second excludes everything more than ``censor`` standard
    deviations above it, which is what stops a ship from raising the background
    it is then compared against.

    Censoring is by **sigma above the mean, not by a multiple of the mean**, and
    the difference is not cosmetic. A multiplicative rule assumes the mean is
    positive and comfortably clear of zero. Over noise-limited water the
    noise-subtracted mean is near zero and often slightly negative — the S1 noise
    LUT mildly over-subtracts at VH — so ``censor × mean`` lands at or below
    zero, the kept set collapses to the most negative pixels in the block, and
    the standard deviation that comes back describes the bottom tail rather than
    the sea. The symptom was 1.2% of open water passing an 8-sigma CFAR test,
    which is several orders of magnitude too many, and it only became visible
    because the two threshold criteria were counted separately.
    """
    h, w = values.shape
    hb, wb = math.ceil(h / block), math.ceil(w / block)
    pad = ((0, hb * block - h), (0, wb * block - w))
    v = np.pad(values, pad, mode="edge").reshape(hb, block, wb, block)
    m = np.pad(valid, pad, mode="constant", constant_values=False).reshape(hb, block, wb, block)

    axes = (1, 3)
    n0 = m.sum(axis=axes)
    denom0 = np.maximum(n0, 1)
    mean0 = np.where(m, v, 0.0).sum(axis=axes) / denom0

    var0 = np.maximum(
        np.where(m, v.astype(np.float64) ** 2, 0.0).sum(axis=axes) / denom0 - mean0**2, 0.0
    )

    cut = mean0 + censor * np.sqrt(var0)
    keep = m & (v <= cut[:, None, :, None])
    n = keep.sum(axis=axes)
    # A block where censoring left almost nothing is not a clutter sample; fall
    # back to the uncensored pass rather than reporting statistics of four pixels.
    thin = n < max(block * block // 8, 1)
    denom = np.maximum(n, 1)
    total = np.where(keep, v, 0.0).sum(axis=axes)
    total_sq = np.where(keep, v.astype(np.float64) ** 2, 0.0).sum(axis=axes)
    mean = total / denom
    var = np.maximum(total_sq / denom - mean**2, 0.0)
    mean = np.where(thin, mean0, mean)
    var = np.where(thin, var0, var)
    return mean.astype(np.float32), np.sqrt(var).astype(np.float32), (n0 / (block * block))


def _block_mean(values: np.ndarray, valid: np.ndarray, block: int) -> np.ndarray:
    """Plain per-block mean over valid pixels — no censoring, no second pass.

    Used for the noise floor, which has no targets in it to censor.
    """
    h, w = values.shape
    hb, wb = math.ceil(h / block), math.ceil(w / block)
    pad = ((0, hb * block - h), (0, wb * block - w))
    v = np.pad(values, pad, mode="edge").reshape(hb, block, wb, block)
    m = np.pad(valid, pad, mode="constant", constant_values=False).reshape(hb, block, wb, block)
    return np.where(m, v, 0.0).sum(axis=(1, 3)) / np.maximum(m.sum(axis=(1, 3)), 1)


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grow a boolean block mask by ``radius`` blocks, by shifting and OR-ing.

    A max filter without a filtering dependency. The masks are block-resolution
    (a few hundred by a few hundred), so the naive form costs nothing.
    """
    out = mask.copy()
    for _ in range(max(radius, 0)):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _erode(mask: np.ndarray, radius: int) -> np.ndarray:
    """Shrink a boolean block mask. Erosion is dilation of the complement."""
    return ~_dilate(~mask, radius)


def land_mask(
    sigma0: np.ndarray, valid: np.ndarray, cfg: DetectorConfig
) -> np.ndarray:
    """The land mask, at full window resolution. Shared by the detector and the renderer.

    Four steps, each earning its place:

    1. **Mean sigma0 over fine blocks** (80 m). Land backscatters 10–15 dB above
       calm sea at VH, so the threshold is not a delicate one — but it has to be
       evaluated at a scale that resolves a shoreline, not at the 320 m scale the
       CFAR statistics use.
    2. **Threshold.** Above `land_sigma0_db` is land.
    3. **Opening** (erode, then dilate). Land is large and contiguous; a ship is
       a bright speck. Without this step a fine-grained mask flags each vessel as
       its own island and then masks the detection away — the mask would remove
       exactly what the detector is looking for.
    4. **Buffer.** Grow the surviving coastline outward. Harbour walls, moored
       vessels, breakwaters and the bright surf line all sit just seaward of the
       shore, and they are the false positives that a coarse mask leaves behind.

    Exported rather than inlined because the overview render draws this mask, and
    a picture of a *different* mask from the one the detector applied would be
    worse than no picture at all.
    """
    land = _block_mean(sigma0, valid, cfg.land_block) > _from_db(cfg.land_sigma0_db)
    # Fill (DN == 0, outside the swath) is not water and must not be treated as
    # a clutter sample.
    fill = _block_mean(valid.astype(np.float32), valid | ~valid, cfg.land_block) < (
        1.0 - cfg.max_fill_fraction
    )
    land = _erode(land, cfg.land_open_blocks)
    land = _dilate(land, cfg.land_open_blocks + cfg.land_dilate_blocks)
    return _upsample(land | fill, cfg.land_block, sigma0.shape)


def _upsample(blocks: np.ndarray, block: int, shape: tuple[int, int]) -> np.ndarray:
    return np.repeat(np.repeat(blocks, block, axis=0), block, axis=1)[: shape[0], : shape[1]]


def _to_db(x: np.ndarray | float) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(np.asarray(x, dtype=np.float64), 1e-12))


def _from_db(db: float) -> float:
    return float(10.0 ** (db / 10.0))


def _window_for_bbox(
    product: SafeProduct, bbox: BBox, transformer: GCPTransformer
) -> tuple[int, int, int, int]:
    """Row *and* column range of the image covering ``bbox``, generously padded.

    Restricting to the AOI is not an optimisation detail — §3.1 makes the AOI a
    config input, and a detector that scanned the whole 250 km swath would report
    vessels outside the area anyone asked about.

    Both axes, and the reason is worth stating: this granule spans 11.3–16.2°E
    while the Kattegat AOI stops at 12.5°E, so bounding rows alone still drags in
    every column across Sweden. The first run did exactly that and reported 82%
    of the scene land-masked, which read like a broken land mask and was in fact
    a correctly-masked country nobody had asked about.

    The window is a rectangle in image space and the AOI is a rectangle in
    geographic space; on a descending pass neither is the other, so this only
    bounds the work. Detections are filtered against the AOI properly, in
    lon/lat, once they have been georeferenced.
    """
    acq = product.acquisition
    lons = [bbox.min_lon, bbox.max_lon, bbox.min_lon, bbox.max_lon]
    lats = [bbox.min_lat, bbox.min_lat, bbox.max_lat, bbox.max_lat]
    rows, cols = transformer.rowcol(lons, lats, op=lambda v: v)
    rows = [r for r in np.atleast_1d(rows) if np.isfinite(r)]
    cols = [c for c in np.atleast_1d(cols) if np.isfinite(c)]
    if not rows or not cols:
        return 0, acq.height, 0, acq.width
    pad = 512
    return (
        int(max(0, math.floor(min(rows)) - pad)),
        int(min(acq.height, math.ceil(max(rows)) + pad)),
        int(max(0, math.floor(min(cols)) - pad)),
        int(min(acq.width, math.ceil(max(cols)) + pad)),
    )


def detect_scene(
    product: SafeProduct,
    *,
    config: DetectorConfig | None = None,
    bbox: BBox | None = None,
    coastline: Coastline | None = None,
    source_url: str | None = None,
    progress: bool = False,
) -> tuple[list[Detection], list[Measurement], DetectorRun]:
    """Run the detector over one granule.

    Returns the §5 detections, the parallel per-detection measurements, and the
    run record that makes the set reproducible.
    """
    cfg = config or DetectorConfig()
    acq = product.acquisition
    started = time.time()
    run = DetectorRun(
        scene_id=acq.scene_id,
        polarization=cfg.polarization,
        parameters=asdict(cfg),
        aoi_bbox=bbox.as_list() if bbox else None,
        coastline=None if coastline is None else f"{coastline.source} +{coastline.buffer_m:.0f} m",
    )

    path = product.raster_path(cfg.polarization)
    sigma_lut = product.sigma_lut(cfg.polarization)
    min_snr = _from_db(cfg.min_snr_db)

    blobs: list[tuple[float, float, float, float, float, float, float]] = []
    water_means: list[tuple[float, float]] = []

    with rasterio.open(path) as src:
        gcps, _ = src.gcps
        transformer = GCPTransformer(gcps, tps=True)
        row0, row1, col0, col1 = (
            _window_for_bbox(product, bbox, transformer)
            if bbox
            else (0, src.height, 0, src.width)
        )
        width = col1 - col0
        if progress:
            print(
                f"  lines {row0}–{row1} of {src.height}, "
                f"samples {col0}–{col1} of {src.width}",
                flush=True,
            )

        step = cfg.window_lines
        for top in range(row0, row1, step):
            # Read with overlap on both sides; keep only detections whose centre
            # lands in the core, so a vessel split by a window edge is measured
            # whole exactly once.
            read_top = max(0, top - cfg.overlap_lines)
            read_bottom = min(src.height, top + step + cfg.overlap_lines)
            core_top, core_bottom = top, min(top + step, row1)
            if core_top >= core_bottom:
                continue

            dn = src.read(
                1, window=Window(col0, read_top, width, read_bottom - read_top)
            ).astype(np.float32)
            lines = np.arange(read_top, read_bottom, dtype=np.float64)

            valid = dn > 0
            power = dn * dn
            del dn
            noise = product.noise_at(lines, col0, col1, cfg.polarization)
            gain = sigma_lut.at(lines, col0, col1) ** 2
            nesz = noise / gain
            # NOT clipped at zero, and this is load-bearing. VH over calm sea in
            # this scene sits *below* the noise floor -- measured water sigma0 is
            # under NESZ -- so `DN² − noise` is genuinely negative for roughly
            # half the water pixels. Clamping those to zero turns a well-behaved
            # random background into a half-black, half-grey binary field, and
            # the block mean and standard deviation computed from it are the
            # statistics of the clamp rather than of the sea. The first render of
            # the chips showed it immediately as salt-and-pepper noise where
            # water should have been smooth.
            sigma0 = (power - noise) / gain
            del power, noise, gain

            land_full = land_mask(sigma0, valid, cfg)
            # Clutter statistics are computed with land already excluded. A block
            # straddling a shoreline otherwise carries a mean and a standard
            # deviation dominated by the land half, which raises the threshold
            # over the water half and hides real vessels near shore.
            mean, std, _ = _block_stats(sigma0, valid & ~land_full, cfg.block, cfg.censor)
            land = _block_mean(land_full.astype(np.float32), valid | ~valid, cfg.block) > 0.5

            water = mean[~land]
            if water.size:
                # Reported as the *total* measured backscatter (residual plus the
                # noise that was subtracted from it) against NESZ, because the
                # residual alone is near zero and its dB value is meaningless.
                # Seeing the two side by side is what says "this is a
                # noise-limited scene", which explains the whole shape of the
                # threshold below.
                nesz_blocks = _block_mean(nesz, valid, cfg.block)
                water_means.append(
                    (float(np.median(water)), float(np.median(nesz_blocks[~land])))
                )

            # Two criteria, deliberately both. `mean + k·std` is the relative
            # CFAR test and adapts to sea state; `min_snr × NESZ` is an absolute
            # floor that stops a block of noise-floor water — where mean and std
            # are both near zero — from having its threshold collapse onto the
            # noise. Which one binds is scene-dependent, so both are counted.
            threshold = mean + cfg.k * std
            thr_full = _upsample(threshold, cfg.block, sigma0.shape)

            open_water = valid & ~land_full
            above_cfar = sigma0 > thr_full
            above_floor = sigma0 > nesz * min_snr
            hot = open_water & above_cfar & above_floor

            core = slice(core_top - read_top, core_bottom - read_top)
            run.pixels_examined += int(valid[core].sum())
            run.pixels_land_masked += int((valid & land_full)[core].sum())
            run.pixels_water += int(open_water[core].sum())
            run.px_above_cfar += int((open_water & above_cfar)[core].sum())
            run.px_above_floor += int((open_water & above_floor)[core].sum())
            run.px_hot += int(hot[core].sum())
            del above_cfar, above_floor, open_water

            if hot.any():
                blobs.extend(
                    _measure_window(
                        hot, sigma0, thr_full, read_top, col0, core_top, core_bottom, cfg
                    )
                )
            del sigma0, thr_full, land_full, hot, valid, nesz

        run.candidates = len(blobs)
        detections, measurements = _finalise(
            blobs, product, transformer, cfg, run, source_url, bbox, coastline
        )

    run.seconds = time.time() - started
    run.detections = len(detections)
    if water_means:
        residual = float(np.median([w for w, _ in water_means]))
        nesz_level = float(np.median([n for _, n in water_means]))
        run.water_sigma0_db = float(_to_db(max(residual + nesz_level, 1e-12)))
        run.nesz_db = float(_to_db(nesz_level))
    return detections, measurements, run


def _grow(poly, sigma0: np.ndarray, threshold: np.ndarray, cfg: DetectorConfig):
    """Re-grow one blob at the lower measurement threshold, seeded from itself.

    Bounded to a window around the blob and restricted to the grown region that
    actually contains the seed, so a low threshold cannot walk off into a
    neighbouring vessel or an unmasked bright feature. Returns ``None`` when the
    growth finds nothing better, and the caller falls back to the detection blob.
    """
    pad = cfg.grow_halfwidth_px
    r0 = max(int(poly.bounds[1]) - pad, 0)
    c0 = max(int(poly.bounds[0]) - pad, 0)
    r1 = min(math.ceil(poly.bounds[3]) + pad, sigma0.shape[0])
    c1 = min(math.ceil(poly.bounds[2]) + pad, sigma0.shape[1])
    if r1 - r0 < 2 or c1 - c0 < 2:
        return None

    patch = sigma0[r0:r1, c0:c1]
    # `threshold` is mean + k·std; recover the pair so the lower cut is the same
    # clutter statistics at a different multiplier rather than a second estimate.
    thr_hi = threshold[r0:r1, c0:c1]
    lower = thr_hi * (cfg.k_grow / cfg.k)
    mask = patch > lower
    if not mask.any():
        return None

    seed = poly.centroid
    from shapely.affinity import translate

    for geom, value in raster_shapes(mask.astype(np.uint8), mask=mask, connectivity=8):
        if not value:
            continue
        # Shift the candidate back into window coordinates before testing.
        candidate = translate(to_shape(geom), xoff=c0, yoff=r0)
        if candidate.contains(seed) or candidate.intersects(poly):
            return candidate if candidate.area >= poly.area else None
    return None


def _measure_window(
    hot: np.ndarray,
    sigma0: np.ndarray,
    threshold: np.ndarray,
    read_top: int,
    col0: int,
    core_top: int,
    core_bottom: int,
    cfg: DetectorConfig,
) -> list[tuple[float, float, float, float, float, float, float]]:
    """Vectorise the threshold mask and measure each connected blob.

    ``rasterio.features.shapes`` is doing the connected-component labelling here.
    It returns one polygon per 4-connected region of equal value, which is
    exactly a blob, and it does it without adding an image-processing dependency
    to an image that has to be bundled offline at M7.
    """
    out = []
    mask = hot.astype(np.uint8)
    for geom, value in raster_shapes(mask, mask=hot, connectivity=8):
        if not value:
            continue
        poly = to_shape(geom)
        if poly.area < cfg.min_pixels:
            continue
        cx, cy = poly.centroid.x, poly.centroid.y
        row = cy + read_top
        if not (core_top <= row < core_bottom):
            continue

        # Detect at `k`, measure at `k_grow`. See DetectorConfig for the reason;
        # the short version is that a blob cut at 8 sigma is the superstructure,
        # and measuring it gave lengths with r = 0.015 against AIS.
        grown = _grow(poly, sigma0, threshold, cfg)
        shape_for_size = grown if grown is not None else poly

        rect = shape_for_size.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)[:4] if hasattr(rect, "exterior") else []
        if len(coords) < 4:
            continue
        edges = [
            (coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1]) for i in range(3)
        ]
        lengths = [math.hypot(dx, dy) for dx, dy in edges[:2]]
        major_i = int(np.argmax(lengths))
        length_px, width_px = max(lengths), min(lengths)
        dx, dy = edges[major_i]

        r0, r1 = int(max(poly.bounds[1], 0)), math.ceil(poly.bounds[3])
        c0, c1 = int(max(poly.bounds[0], 0)), math.ceil(poly.bounds[2])
        patch = sigma0[r0:r1, c0:c1]
        peak = float(patch.max()) if patch.size else 0.0
        local_thr = float(threshold[r0:r1, c0:c1].max()) if patch.size else 1.0
        out.append((cx + col0, row, length_px, width_px, dx, dy, peak / max(local_thr, 1e-12)))
    return out


def _merge_fragments(
    kept: list[tuple],
    lons: np.ndarray,
    lats: np.ndarray,
    cfg: DetectorConfig,
    run: DetectorRun,
) -> tuple[list[tuple], np.ndarray, np.ndarray]:
    """Collapse detections that are the same vessel seen more than once.

    The representative is the **brightest** member, kept whole rather than
    averaged. A synthesised centroid would be a position no pixel supports, and
    with lengths already weakly correlated to truth (r = 0.198) inventing a
    merged extent would add error rather than remove it. The smear tail is
    dimmer than the hull, so the highest-margin blob is the best single answer
    the data contains, and the fragment count travels on the provenance instead
    of being lost.
    """
    n = len(kept)
    if cfg.merge_radius_m <= 0 or n < 2:
        return kept, lons, lats

    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        near = np.flatnonzero(
            haversine_m(np.full(lons.shape, lons[i]), np.full(lats.shape, lats[i]), lons, lats)
            <= cfg.merge_radius_m
        )
        for j in near:
            ra, rb = find(i), int(find(int(j)))
            if ra != rb:
                parent[ra] = rb

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    keep_idx = [max(members, key=lambda i: kept[i][6]) for members in clusters.values()]
    keep_idx.sort()
    run.merged_fragments = n - len(keep_idx)
    run.merged_targets = len(keep_idx)
    return [kept[i] for i in keep_idx], lons[keep_idx], lats[keep_idx]


def _finalise(
    blobs: list[tuple[float, float, float, float, float, float, float]],
    product: SafeProduct,
    transformer: GCPTransformer,
    cfg: DetectorConfig,
    run: DetectorRun,
    source_url: str | None,
    bbox: BBox | None,
    coastline: Coastline | None,
) -> tuple[list[Detection], list[Measurement]]:
    """Georeference, size-filter and package the blobs as §5 `Detection`s."""
    acq = product.acquisition
    spacing = (acq.range_pixel_spacing_m + acq.azimuth_pixel_spacing_m) / 2.0

    kept = []
    for cx, row, length_px, width_px, dx, dy, margin in blobs:
        length_m = length_px * spacing
        if length_m < cfg.min_length_m:
            run.rejected_small += 1
            continue
        if length_m > cfg.max_length_m:
            run.rejected_large += 1
            continue
        kept.append((cx, row, length_m, width_px * spacing, dx, dy, margin))
    if not kept:
        return [], []

    cols = [b[0] for b in kept]
    rows = [b[1] for b in kept]
    # offset="ul" because the blob centroids are already continuous pixel
    # coordinates. The default "center" adds half a pixel in each axis, which
    # showed up as a uniform 7.07 m (= √2 × 5 m) bias against the tie points.
    lons, lats = transformer.xy(rows, cols, offset="ul")
    lons = np.atleast_1d(lons)
    lats = np.atleast_1d(lats)

    # The read window is a rectangle in image space; the AOI is a rectangle in
    # geographic space. On a descending pass they are two different quadrilaterals
    # and the window is the larger, so the AOI is enforced here, after
    # georeferencing, rather than assumed from the window bounds.
    if bbox is not None or coastline is not None:
        inside = np.ones(len(lons), dtype=bool)
        if bbox is not None:
            inside &= (
                (lons >= bbox.min_lon)
                & (lons <= bbox.max_lon)
                & (lats >= bbox.min_lat)
                & (lats <= bbox.max_lat)
            )
            run.rejected_outside_aoi += int((~inside).sum())
        if coastline is not None:
            # The second land test, and the one that catches what the first
            # structurally cannot: skerries, rocks and harbour moles, which are
            # bright compact targets indistinguishable from hulls in the pixels
            # alone. Applied here, in lon/lat, after georeferencing — a vector
            # test on a few hundred points is trivial, and rasterising a
            # shoreline into radar geometry to do it earlier would be work spent
            # to reach the same answer.
            on_land = np.asarray(coastline.contains(lons, lats), dtype=bool)
            run.rejected_on_coastline += int((on_land & inside).sum())
            inside &= ~on_land
        keep_idx = np.flatnonzero(inside)
        kept = [kept[i] for i in keep_idx]
        lons, lats = lons[keep_idx], lats[keep_idx]
        if not kept:
            return [], []
        cols = [b[0] for b in kept]
        rows = [b[1] for b in kept]

    # One vessel, one detection. Applied here — after the AOI and coastline
    # filters, before ids are assigned — so an id names a target rather than a
    # fragment, and `ais_match(["…:det_00007"])` means one ship.
    kept, lons, lats = _merge_fragments(kept, lons, lats, cfg, run)
    cols = [b[0] for b in kept]
    rows = [b[1] for b in kept]

    # Heading is the major axis of the blob mapped from pixel space into compass
    # degrees, by transforming a short step along that axis. It is the hull
    # orientation and is ambiguous by 180° — a SAR blob has no bow.
    #
    # Reported only when the blob is actually elongated. A 30 m vessel is 3 × 1
    # pixels, and the minimum rotated rectangle of a near-square blob snaps to the
    # pixel grid, so its "heading" is the image axis rather than the ship. The
    # first run made that obvious by returning 8.8° or 98.6° for almost every
    # detection — two values 90° apart, which is a picture of the raster, not of
    # a fleet. `heading_deg` is `float | None` in §5 precisely so this can be
    # left unanswered instead of answered wrongly.
    span = 40.0
    tip_rows = [r + span * dy / max(math.hypot(dx, dy), 1e-9) for _, r, _, _, dx, dy, _ in kept]
    tip_cols = [c + span * dx / max(math.hypot(dx, dy), 1e-9) for c, _, _, _, dx, dy, _ in kept]
    tip_lons, tip_lats = transformer.xy(tip_rows, tip_cols, offset="ul")

    headings = bearing_deg(lons, lats, np.atleast_1d(tip_lons), np.atleast_1d(tip_lats)) % 180.0
    resolvable = [
        (length_m / spacing) >= cfg.min_heading_px
        and (length_m / max(width_m, spacing)) >= cfg.min_heading_aspect
        for _, _, length_m, width_m, _, _, _ in kept
    ]

    provenance = Provenance(
        source=f"{DETECTOR_NAME} {DETECTOR_VERSION}",
        retrieved_at=datetime.now(UTC),
        source_url=source_url,
        licence=None,
        note=(
            f"own detector over {acq.scene_id} {cfg.polarization}; "
            f"two-parameter CFAR k={cfg.k}, block={cfg.block} px, "
            f"sigma0 calibrated and thermal-noise subtracted; TPS geolocation"
        ),
    )

    # The beam geometry at each detection is what the azimuth-displacement
    # correction needs, and re-deriving it would mean reopening the archive.
    # Evaluated in one batch here and carried in a parallel `Measurement` rather
    # than bolted onto `Detection`, whose shape §5 fixed and which both the MCP
    # server and the agent bind to.
    incidence = product.grid.incidence_at(np.array(rows), np.array(cols))
    r_over_v = product.r_over_v(np.array(rows), np.array(cols))

    detections: list[Detection] = []
    measurements: list[Measurement] = []
    for i, (cx, row, length_m, width_m, _dx, _dy, margin) in enumerate(kept):
        det = Detection(
            id=f"{acq.scene_id}:det_{i:05d}",
            scene_id=acq.scene_id,
            lon=float(lons[i]),
            lat=float(lats[i]),
            length_m=round(float(length_m), 1),
            heading_deg=round(float(headings[i]), 1) if resolvable[i] else None,
            confidence=round(float(np.clip(_to_db(margin) / 10.0, 0.0, 1.0)), 3),
            provenance=provenance,
        )
        detections.append(det)
        measurements.append(
            Measurement(
                detection_id=det.id,
                row=float(row),
                col=float(cx),
                width_m=round(float(width_m), 1),
                incidence_deg=round(float(incidence[i]), 4),
                r_over_v_s=round(float(r_over_v[i]), 3),
                cfar_margin_db=round(float(_to_db(margin)), 2),
            )
        )
    return detections, measurements
