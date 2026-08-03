"""Charts for the validation numbers — the ones that go in the README.

Two figures, each answering a question a reader would otherwise have to take on
trust:

**Match-distance CDF.** "Does the azimuth-displacement correction help, and which
sign is right?" A cumulative curve rather than a bar chart of a few bands,
because the bands are arbitrary and the curve is not: it shows whether the
distribution is tight or whether 500 m merely happens to be where the eye lands.
Three variants — uncorrected, and the correction applied each way — so the sign
is settled by a picture rather than by the derivation that got it wrong.

**Detected vs AIS length.** "Are the detections real, and is the size estimate
meaningful?" AIS carries each vessel's true length, so for matched pairs the
detected extent can be plotted against a number nobody in this pipeline
estimated. It is the one check on the detector that owes nothing to the matcher.

Colours come from the validated categorical palette (dark steps, slots 1–3),
which passes lightness, chroma, CVD separation, normal-vision separation and
contrast against this surface under the all-pairs test — scatter and multi-line
forms need all-pairs, not just adjacent.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

SURFACE = "#1a1a19"
TEXT = "#ffffff"
MUTED = "#c3c2b7"
GRID = "#3a3a38"

# Categorical slots 1–3, dark steps. Validated all-pairs on this surface.
S1 = "#3987e5"  # blue    — uncorrected
S2 = "#d95926"  # orange  — correction, sign +1
S3 = "#199e70"  # aqua    — correction, sign −1

#: §5's match radius. Drawn as a rule on every distance axis so the reader can
#: see where the decision boundary falls relative to the distribution, rather
#: than being told the count on one side of it.
MATCH_RADIUS_M = 500.0


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    for side, spine in ax.spines.items():
        spine.set_visible(side in ("left", "bottom"))
        spine.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)
    ax.title.set_color(TEXT)
    ax.grid(True, color=GRID, lw=0.6, alpha=0.5)
    ax.set_axisbelow(True)


def plot_validation(report, *, det_pairs: list[tuple[float, float]], out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [_plot_cdf(report, out_dir / "azimuth_correction.png")]
    if det_pairs and len(det_pairs) >= 5:
        written.append(_plot_lengths(det_pairs, out_dir / "length_agreement.png"))
    return written


def _plot_cdf(report, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(9, 5.4))
    fig.patch.set_facecolor(SURFACE)
    _style(ax)

    colours = {}
    for name in report.by_variant:
        if "no correction" in name:
            colours[name] = S1
        elif "+1" in name:
            colours[name] = S2
        else:
            colours[name] = S3

    total = report.detections
    for name, r in report.by_variant.items():
        d = np.sort(r["nearest"])
        # Step rather than a smooth line: this is an empirical CDF over a finite
        # set of detections, and drawing it smooth would imply data between the
        # points that does not exist.
        ax.step(
            d,
            100.0 * np.arange(1, len(d) + 1) / max(total, 1),
            where="post",
            lw=2.0,
            color=colours[name],
            label=name.replace("interpolated to acquisition, ", ""),
        )

    ax.axvline(MATCH_RADIUS_M, color=MUTED, lw=1.2, ls="--", alpha=0.7)
    ax.text(
        MATCH_RADIUS_M * 1.06,
        4,
        f"match radius {MATCH_RADIUS_M:.0f} m",
        color=MUTED,
        fontsize=8,
        rotation=90,
        va="bottom",
    )

    ax.set_xscale("log")
    ax.set_xlim(50, 20_000)
    ax.set_ylim(0, 100)
    ax.set_xlabel("distance from detection to nearest AIS vessel (m, log scale)")
    ax.set_ylabel("% of detections")
    ax.set_title(
        f"Azimuth-displacement correction — {report.scene_id.split('_')[0]} "
        f"{report.scene_id[17:32]}\n"
        f"{report.detections} detections vs {report.ais_in_footprint} AIS vessels "
        f"inside the scene footprint",
        fontsize=11,
        loc="left",
    )
    legend = ax.legend(loc="lower right", fontsize=9, framealpha=0.9)
    legend.get_frame().set_facecolor("#232322")
    legend.get_frame().set_edgecolor(GRID)
    for t in legend.get_texts():
        t.set_color(TEXT)

    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path


def _plot_lengths(pairs: list[tuple[float, float]], path: Path) -> Path:
    det = np.array([a for a, _ in pairs], dtype=float)
    ais = np.array([b for _, b in pairs], dtype=float)

    fig, ax = plt.subplots(figsize=(6.6, 6.2))
    fig.patch.set_facecolor(SURFACE)
    _style(ax)

    lim = max(det.max(), ais.max()) * 1.1
    ax.plot([0, lim], [0, lim], color=MUTED, lw=1.2, ls="--", alpha=0.8, zorder=1)
    ax.text(lim * 0.80, lim * 0.76, "1 : 1", color=MUTED, fontsize=9, rotation=45)

    # One series, so no legend box — the title names it. 2 px surface ring on the
    # markers so overlapping points stay countable.
    ax.scatter(
        ais, det, s=46, color=S1, alpha=0.85, edgecolors=SURFACE, linewidths=2.0, zorder=3
    )

    ratio = float(np.median(det / ais))
    ax.plot([0, lim], [0, lim * ratio], color=S2, lw=2.0, zorder=2)
    ax.text(
        lim * 0.30,
        lim * 0.30 * ratio,
        f"median {ratio:.2f}×",
        color=S2,
        fontsize=9,
        va="bottom",
        ha="right",
    )

    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.set_aspect("equal")
    ax.set_xlabel("AIS reported length (m)")
    ax.set_ylabel("detected length (m)")
    ax.set_title(
        f"Detected extent vs AIS length\n{len(pairs)} matched vessels, "
        f"r = {np.corrcoef(det, ais)[0, 1]:.3f}",
        fontsize=11,
        loc="left",
    )
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path
