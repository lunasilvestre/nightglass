"""Pictures of what the detector did, because numbers do not validate a map.

A detection list is a table of coordinates. A table of coordinates cannot show
that the land mask leaked, that the blobs are speckle rather than hulls, that the
detections cluster on a tile seam, or that everything is offset 300 m north of
where the ships actually are. Every one of those failures produces a perfectly
reasonable-looking table.

So this module renders three things, and each is aimed at a specific way the
detector could be lying:

**Chips** — the raw pixels around each detection, at native resolution. Answers
"is this a ship?". A vessel is a compact bright core against dark water, often
with a wake; speckle is a single hot pixel with nothing around it; a land leak
has bright texture filling the frame.

**Scene overview** — the whole AOI in radar geometry, decimated, with the land
mask and detections drawn on it. Answers "is the detector firing where it should
be?" and catches the failures that only exist at scale: a grid of detections
along block boundaries, a cluster on a window seam, a coastline the mask missed.

**Map view with AIS overlaid** — detections and ground-truth AIS in lon/lat.
Answers the only question that actually matters, which is whether the detections
land on top of the vessels that were really there. This is the one that catches a
geolocation bias, and it is the reason a north-up reprojection is worth the
trouble: in radar geometry two layers can look aligned while being kilometres
apart on the ground.

Rendering happens inside the enclave, on the same mounted granule the detector
read, and writes to the single writable mount. Nothing is fetched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # No display inside a container. Set before pyplot is imported.

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.patches import Circle
from rasterio.transform import GCPTransformer
from rasterio.windows import Window

from nightglass.config import BBox
from nightglass.schemas import Detection
from nightglass.spatial.detect import (
    DetectorConfig,
    Measurement,
    _window_for_bbox,
)
from nightglass.spatial.detect import (
    land_mask as detect_land_mask,
)
from nightglass.spatial.safe import SafeProduct

# Dark background throughout: SAR over water is a dark image, and a white page
# around it destroys the contrast the eye needs to judge a chip.
# Shared with `plots.py` so the imagery and the charts read as one system.
# Slot 1 (blue) for AIS ground truth, slot 3 (aqua) for a detection that found
# its AIS, and the reserved status red for one that did not -- which is a
# finding, not a fourth series.
#
# The first attempt used slot 2 (orange) for matched detections against that same
# red, and it FAILED validation: normal-vision separation ΔE 7.1 against a floor
# of 15, i.e. genuinely hard to tell apart with full colour vision, which is
# exactly how it read on the first map. This triple passes the normal-vision
# floor at 20.9. Its worst CVD pair is ΔE 6.5 (protan), inside the band that is
# only legal with secondary encoding -- so unmatched detections also carry a
# different marker shape and a heavier stroke, and identity never rests on
# colour alone.
INK = "#ffffff"
PAPER = "#1a1a19"
MUTED = "#c3c2b7"
ACCENT = "#199e70"   # detections that found their AIS
TRUTH = "#3987e5"    # AIS ground truth
DARK_HIT = "#e66767"  # unmatched — what the whole pipeline is looking for


def _style(fig: plt.Figure, axes: list[plt.Axes]) -> None:
    fig.patch.set_facecolor(PAPER)
    for ax in axes:
        ax.set_facecolor(PAPER)
        for spine in ax.spines.values():
            spine.set_color("#3a3a38")
        ax.tick_params(colors=MUTED, labelsize=7)
        ax.xaxis.label.set_color(MUTED)
        ax.yaxis.label.set_color(MUTED)
        ax.title.set_color(INK)


@dataclass
class Patch:
    """A rectangle of calibrated sigma0 in dB, with its image origin."""

    db: np.ndarray
    row0: int
    col0: int
    decim: int = 1


def read_sigma0_db(
    product: SafeProduct,
    row0: int,
    row1: int,
    col0: int,
    col1: int,
    *,
    pol: str = "VH",
    decim: int = 1,
    subtract_noise: bool = False,
) -> Patch:
    """Calibrated sigma0 in dB over an image rectangle.

    ``subtract_noise`` defaults to **off** for display, and that is a considered
    choice rather than a shortcut. This scene is noise-limited: VH backscatter
    from calm sea is below NESZ, so the noise-subtracted residual is a
    near-zero-mean field that is negative about half the time. It is the right
    quantity to threshold — the detector uses it — but ``log10`` of it is not an
    image. Rendering it produced a salt-and-pepper field where water should have
    been smooth, which is a picture of the sign of a residual and not of the sea.

    The displayed quantity is therefore total calibrated ``DN²/A²``: what the
    instrument measured, noise included. Ships stand above it by the same margin
    either way, so nothing about the detection is flattered — the contrast that
    justifies a detection is visible in exactly the image a SAR analyst would
    expect to be shown.
    """
    row0, col0 = max(0, row0), max(0, col0)
    row1 = min(row1, product.acquisition.height)
    col1 = min(col1, product.acquisition.width)
    height, width = row1 - row0, col1 - col0
    if height <= 0 or width <= 0:
        return Patch(np.zeros((1, 1), dtype=np.float32), row0, col0, decim)

    out_h, out_w = max(1, height // decim), max(1, width // decim)
    with rasterio.open(product.raster_path(pol)) as src:
        dn = src.read(
            1, window=Window(col0, row0, width, height), out_shape=(out_h, out_w)
        ).astype(np.float32)

    # The LUTs are evaluated on the decimated lattice, at the image coordinates
    # each output pixel actually came from.
    lines = row0 + (np.arange(out_h) + 0.5) * (height / out_h)
    noise = product.noise_lut(pol)[0].at(lines, col0, col1)
    gain = product.sigma_lut(pol).at(lines, col0, col1)
    idx = np.clip(((np.arange(out_w) + 0.5) * (width / out_w)).astype(int), 0, width - 1)
    noise, gain = noise[:, idx], gain[:, idx]

    power = dn * dn - noise if subtract_noise else dn * dn
    sigma0 = power / (gain**2)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(np.maximum(sigma0, 1e-9))
    db[dn <= 0] = np.nan  # outside the swath
    return Patch(db.astype(np.float32), row0, col0, decim)


def _rescale_mask_cfg(cfg: DetectorConfig, decim: int) -> DetectorConfig:
    """The land-mask parameters, re-expressed for a decimated image.

    Everything is in blocks, and a block is a number of pixels — so at 1/N scale
    the block counts have to change to keep the same distance on the ground.
    """
    block = max(1, cfg.land_block // decim)
    scale = cfg.land_block / (block * decim)
    return replace(
        cfg,
        land_block=block,
        land_open_blocks=max(1, round(cfg.land_open_blocks * scale)),
        land_dilate_blocks=max(1, round(cfg.land_dilate_blocks * scale)),
    )


def _stretch(db: np.ndarray, lo_pct: float = 2.0, hi_pct: float = 99.9) -> tuple[float, float]:
    finite = db[np.isfinite(db)]
    if finite.size == 0:
        return -35.0, -5.0
    return float(np.percentile(finite, lo_pct)), float(np.percentile(finite, hi_pct))


def contact_sheet(
    product: SafeProduct,
    detections: list[Detection],
    measurements: list[Measurement],
    out_path: str | Path,
    *,
    cols: int = 8,
    rows: int = 6,
    half: int = 40,
    pol: str = "VH",
    order: str = "confidence",
    title: str = "",
    labels: dict[str, str] | None = None,
) -> Path:
    """A grid of native-resolution chips — the "are these ships?" render.

    Ordered by confidence descending by default, but `order="spread"` samples
    evenly across the confidence range instead. That matters: a sheet of the
    top 48 detections proves only that the best ones are good, and the ones
    worth eyeballing are the marginal ones just above the threshold.
    """
    by_id = {m.detection_id: m for m in measurements}
    pairs = [(d, by_id[d.id]) for d in detections if d.id in by_id]
    pairs.sort(key=lambda p: -(p[0].confidence or 0.0))
    n = cols * rows
    if order == "spread" and len(pairs) > n:
        pairs = [pairs[round(i * (len(pairs) - 1) / (n - 1))] for i in range(n)]
    else:
        pairs = pairs[:n]

    fig, axes = plt.subplots(rows, cols, figsize=(cols * 1.5, rows * 1.62))
    axes = np.atleast_1d(axes).ravel()
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
    _style(fig, list(axes))

    for ax, (det, m) in zip(axes, pairs, strict=False):
        patch = read_sigma0_db(
            product,
            int(m.row) - half,
            int(m.row) + half,
            int(m.col) - half,
            int(m.col) + half,
            pol=pol,
        )
        lo, hi = _stretch(patch.db, 5.0, 99.5)
        ax.imshow(patch.db, cmap="bone", vmin=lo, vmax=max(hi, lo + 1.0), interpolation="nearest")
        cy = int(m.row) - patch.row0
        cx = int(m.col) - patch.col0
        ax.add_patch(
            Circle((cx, cy), radius=max(6.0, (det.length_m or 20) / 20.0),
                   fill=False, ec=ACCENT, lw=0.9)
        )
        tag = (labels or {}).get(det.id, "")
        ax.set_title(
            f"{det.length_m:.0f} m  c{det.confidence:.2f}{'  ' + tag if tag else ''}",
            fontsize=6,
            pad=2,
        )
    for ax in axes[len(pairs) :]:
        ax.axis("off")

    if title:
        fig.suptitle(title, color=INK, fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.97 if title else 1.0))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, facecolor=PAPER)
    plt.close(fig)
    return out


def scene_overview(
    product: SafeProduct,
    bbox: BBox,
    detections: list[Detection],
    measurements: list[Measurement],
    out_path: str | Path,
    *,
    config: DetectorConfig | None = None,
    decim: int = 16,
    pol: str = "VH",
    title: str = "",
) -> Path:
    """The AOI in radar geometry, with the land mask and every detection drawn.

    Radar geometry on purpose. This is the view that shows detector artefacts as
    artefacts: window seams run horizontally, CFAR blocks are axis-aligned, and
    the land mask is a blocky overlay you can see through. Reprojecting to north
    up would rotate all of that into diagonal noise nobody can read.
    """
    cfg = config or DetectorConfig()
    with rasterio.open(product.raster_path(pol)) as src:
        gcps, _ = src.gcps
    transformer = GCPTransformer(gcps, tps=True)
    row0, row1, col0, col1 = _window_for_bbox(product, bbox, transformer)
    patch = read_sigma0_db(product, row0, row1, col0, col1, pol=pol, decim=decim)
    lo, hi = _stretch(patch.db, 2.0, 99.5)

    fig, ax = plt.subplots(figsize=(11, 11 * patch.db.shape[0] / max(patch.db.shape[1], 1)))
    _style(fig, [ax])
    ax.imshow(patch.db, cmap="bone", vmin=lo, vmax=hi, interpolation="nearest")

    # The land mask, drawn by calling the detector's own `land_mask` — not by
    # re-deriving something similar. A picture of a *different* mask from the one
    # that was applied is worse than no picture, because it would show a leak
    # that had been masked or hide one that had not.
    #
    # Block counts are rescaled so the mask drawn here covers the same distance
    # on the ground as the one computed at full resolution: a 10-block buffer of
    # 80 m blocks is 800 m, and it has to stay 800 m at 1/16 scale.
    with np.errstate(invalid="ignore"):
        linear = 10 ** (patch.db / 10.0)
    land = detect_land_mask(linear, np.isfinite(patch.db), _rescale_mask_cfg(cfg, decim))
    ax.imshow(
        np.ma.masked_where(~land, np.ones_like(patch.db)),
        cmap="autumn",
        alpha=0.30,
        interpolation="nearest",
    )

    by_id = {m.detection_id: m for m in measurements}
    xs = [(by_id[d.id].col - col0) / decim for d in detections if d.id in by_id]
    ys = [(by_id[d.id].row - row0) / decim for d in detections if d.id in by_id]
    ax.scatter(xs, ys, s=42, facecolors="none", edgecolors=ACCENT, linewidths=0.8)

    # Window seams, drawn so a line of detections along one is unmissable.
    for top in range(row0, row1, cfg.window_lines):
        ax.axhline((top - row0) / decim, color="#5a5a66", lw=0.4, ls=":")

    ax.set_title(
        title or f"{product.scene_id}  {pol}  ·  {len(detections)} detections  ·  radar geometry",
        fontsize=9,
    )
    ax.set_xlabel(f"range sample / {decim}   (dotted lines: read-window seams)")
    ax.set_ylabel(f"azimuth line / {decim}")
    fig.tight_layout()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140, facecolor=PAPER)
    plt.close(fig)
    return out


def map_view(
    product: SafeProduct,
    bbox: BBox,
    detections: list[Detection],
    out_path: str | Path,
    *,
    ais: list[tuple[float, float]] | None = None,
    ais_apparent: list[tuple[float, float]] | None = None,
    dark_ids: set[str] | None = None,
    decim: int = 16,
    pol: str = "VH",
    title: str = "",
    zoom: BBox | None = None,
) -> Path:
    """Detections and AIS ground truth in lon/lat, over the SAR backdrop.

    The backdrop is drawn with `pcolormesh` on a curvilinear lon/lat mesh built
    from the TPS transform, rather than by warping the raster. Same result, and
    it keeps the georeferencing in exactly one place — the transform the
    detections themselves were positioned with. If that transform were wrong,
    the image and the detections would be wrong together and the AIS would be the
    thing that disagreed, which is precisely the error this view exists to catch.
    """
    with rasterio.open(product.raster_path(pol)) as src:
        gcps, _ = src.gcps
    transformer = GCPTransformer(gcps, tps=True)
    row0, row1, col0, col1 = _window_for_bbox(product, bbox, transformer)
    patch = read_sigma0_db(product, row0, row1, col0, col1, pol=pol, decim=decim)
    h, w = patch.db.shape

    # Mesh corners, so pcolormesh gets (h+1, w+1) as it wants.
    rr = row0 + np.arange(h + 1) * decim
    cc = col0 + np.arange(w + 1) * decim
    grid_r, grid_c = np.meshgrid(rr, cc, indexing="ij")
    lon, lat = transformer.xy(grid_r.ravel().tolist(), grid_c.ravel().tolist(), offset="ul")
    lon = np.asarray(lon).reshape(grid_r.shape)
    lat = np.asarray(lat).reshape(grid_r.shape)

    lo, hi = _stretch(patch.db, 2.0, 99.5)
    fig, ax = plt.subplots(figsize=(12, 11))
    _style(fig, [ax])
    ax.pcolormesh(lon, lat, patch.db, cmap="bone", vmin=lo, vmax=hi, shading="flat")

    if ais:
        ax.scatter(
            [p[0] for p in ais], [p[1] for p in ais],
            s=26, marker="+", c=TRUTH, linewidths=1.0, label=f"AIS at acquisition ({len(ais)})",
        )
    if ais_apparent:
        ax.scatter(
            [p[0] for p in ais_apparent], [p[1] for p in ais_apparent],
            s=10, marker="x", c="#9ad9ff", linewidths=0.6,
            label="AIS + azimuth-displacement correction",
        )
    dark_ids = dark_ids or set()
    matched = [d for d in detections if d.id not in dark_ids]
    dark = [d for d in detections if d.id in dark_ids]
    if matched:
        ax.scatter(
            [d.lon for d in matched], [d.lat for d in matched],
            s=52, facecolors="none", edgecolors=ACCENT, linewidths=0.9,
            label=f"detections, AIS found ({len(matched)})",
        )
    if dark:
        ax.scatter(
            [d.lon for d in dark], [d.lat for d in dark],
            s=150, marker="D", facecolors="none", edgecolors=DARK_HIT, linewidths=1.8,
            label=f"detections, NO AIS correspondence ({len(dark)})",
        )

    view = zoom or bbox
    ax.set_xlim(view.min_lon, view.max_lon)
    ax.set_ylim(view.min_lat, view.max_lat)
    ax.set_aspect(1.0 / math.cos(math.radians((view.min_lat + view.max_lat) / 2)))
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title(title or f"{product.scene_id}  ·  detections vs AIS", fontsize=10)
    legend = ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
    legend.get_frame().set_facecolor("#232322")
    for text in legend.get_texts():
        text.set_color(INK)
    fig.tight_layout()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140, facecolor=PAPER)
    plt.close(fig)
    return out
