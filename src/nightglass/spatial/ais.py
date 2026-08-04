"""AIS source adapters — §3.1's interface requirement, and why it has teeth.

> *"The AIS layer needs a source adapter interface with at least two
> implementations: `DMAFileSource` (point-level CSV) and `GFWDetectionSource`
> (reference detections, already AIS-matched upstream). A third stub,
> `CustomerFeedSource`, that raises "not configured" is worth having — it makes
> the deployment story concrete."*

The interface exists because the two AOIs differ in **data availability, not in
code**. Denmark has free historical point-level AIS; Portugal does not. Swapping
between them is a config change (`AOI_<NAME>_AIS_SOURCE`), and that is also what
"deploy into customer environments" means in practice.

The one thing that must never be swappable is the honesty of the result. Every
source declares `is_ground_truth`, it travels onto every `Match`, and
`CorrelationResult.rate_is_quotable` is false unless every match came from a
ground-truth feed. Measured reason: aisstream.io was compared against DMA over an
identical bbox and clock window and returned **132 vessels against DMA's 770** —
it sees roughly one vessel in six, and vessel discovery had saturated by minute
ten, so recording longer does not close it. Feeding that to the matcher would
mark ~83% of detections dark. §3.2 warns that a pipeline reporting 40% dark is
broken; this would be twice as broken, and it would look like a finding.

So: **matched pairs over Portugal, rates only over Denmark.** Enforced by a field
the code checks, not by a sentence someone has to remember.
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nightglass.config import BBox
from nightglass.spatial.geodesy import KNOTS_TO_MS

# §3.2, measured during pre-dev: 71% of raw DMA rows are exact multi-station
# rebroadcast duplicates, worst case 21 identical copies of one message. Dedup
# happens on this key, before anything downstream sees a position, because
# duplicate-weighted nearest-in-time logic is distorted by it.
DEDUP_KEY = ("mmsi", "timestamp", "lat", "lon")

#: DMA's "Type of mobile" column, restricted to the ones that are a *vessel*.
#: The feed also carries `Base Station` (shore transmitters, 5.5% of rows in the
#: Kattegat window) and `AtoN` (aids to navigation — buoys, beacons, platforms).
#: Neither is a ship, and letting one match a detection would report a hull that
#: turned out to be a navigation buoy as a vessel that had declared itself. The
#: right treatment for a fixed installation is to exclude it as a *vessel* and
#: deal with it as structure; matching it is the one option that is wrong.
VESSEL_CLASSES = frozenset({"Class A", "Class B"})


@dataclass(frozen=True, slots=True)
class AisPosition:
    """One vessel report. Slots because there are tens of millions of these."""

    mmsi: str
    timestamp: datetime
    lon: float
    lat: float
    sog_ms: float | None = None
    cog_deg: float | None = None
    heading_deg: float | None = None
    name: str | None = None
    ship_type: str | None = None
    length_m: float | None = None
    width_m: float | None = None
    nav_status: str | None = None
    source: str = "unknown"

    @property
    def dedup_key(self) -> tuple[str, datetime, float, float]:
        return (self.mmsi, self.timestamp, self.lat, self.lon)


class AisSource(ABC):
    """Where vessel positions come from, and whether they may be quoted as a rate."""

    name: str = "unknown"

    #: True only for a feed complete enough to support a dark-vessel *rate*.
    #: See the module docstring: this is the field `Match.source_is_ground_truth`
    #: and `CorrelationResult.rate_is_quotable` are computed from.
    is_ground_truth: bool = False

    #: What the README has to say about this source, verbatim where a licence
    #: demands it. DMA requires an attribution line; GFW is CC BY-NC 4.0.
    attribution: str | None = None

    @abstractmethod
    def positions(self, bbox: BBox, start: datetime, end: datetime) -> Iterator[AisPosition]:
        """Deduplicated positions inside ``bbox`` between ``start`` and ``end``."""


class NotConfigured(RuntimeError):
    """Raised by a source that has no data configured for this deployment."""


# ---------------------------------------------------------------------------


def _parse_dma_row(row: list[str], source: str) -> AisPosition | None:
    """One DMA CSV row as a **vessel** position, or None if it is not one.

    The schema was verified against the real file during pre-dev: 26
    comma-separated fields, the first column literally named ``# Timestamp``
    (hash included), dates ``%d/%m/%Y``, and lat/lon written with decimal
    **points** despite the DMA README's decimal-comma prose examples.

    Column 1 is "Type of mobile" and it is a filter, not a label — see
    `VESSEL_CLASSES`. This was found by M6 rather than designed: the acquisition
    window used through M3–M5 was a CSV cut by hand during pre-dev, and that cut
    had silently kept only Class A and Class B. Reading the same window out of
    the daily file the way the code actually does it produced 7,857 extra rows
    of base stations and navigation aids. The hand cut was right and the code
    was wrong, and nothing would have said so until someone reproduced the
    demo from the manifest.
    """
    try:
        if row[1] not in VESSEL_CLASSES:
            return None
        stamp = datetime.strptime(row[0], "%d/%m/%Y %H:%M:%S").replace(tzinfo=UTC)
        lat, lon = float(row[3]), float(row[4])
    except (ValueError, IndexError):
        return None
    # 91/181 are the AIS "not available" sentinels and appear in the real file.
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None

    def num(i: int) -> float | None:
        try:
            return float(row[i])
        except (ValueError, IndexError):
            return None

    def text(i: int) -> str | None:
        try:
            v = row[i].strip()
        except IndexError:
            return None
        return v or None

    sog = num(7)
    cog = num(8)
    # 511 is the AIS "heading not available" sentinel and appears in the real
    # file; 360+ on COG likewise. Both must become NULL rather than a bearing.
    heading = num(9)
    if heading is not None and heading > 360.0:
        heading = None
    return AisPosition(
        mmsi=row[2].strip(),
        timestamp=stamp,
        lon=lon,
        lat=lat,
        # DMA reports SOG in knots. Converted at the boundary so nothing
        # downstream has to know or remember which unit it is holding.
        sog_ms=None if sog is None else sog * KNOTS_TO_MS,
        cog_deg=None if cog is None or cog > 360.0 else cog,
        heading_deg=heading,
        name=text(12),
        ship_type=text(13),
        length_m=num(16),
        width_m=num(15),
        nav_status=text(5),
        source=source,
    )


class DMAFileSource(AisSource):
    """Danish Maritime Authority daily AIS, read from the zip without unpacking.

    Denmark is the only European state publishing free point-level *historical*
    AIS, which is the entire reason the validation AOI is Danish (§3.1).

    A daily file is ~5.5 GB of CSV inside a ~900 MB zip, 18–24 M rows. It is
    streamed through `zipfile` rather than extracted, for the same reason the
    granules are read through ``/vsizip``: the acquisition window is ~20 minutes
    out of 24 hours, so more than 99% of the file is discarded, and writing it
    all to disk first would be work done purely to throw away.
    """

    name = "dma"
    is_ground_truth = True
    attribution = (
        "Danish Maritime Authority — AIS data. "
        "The Danish Maritime Authority accepts no liability for any errors or "
        "omissions, nor for the correctness or completeness of the data."
    )

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise NotConfigured(f"DMA file not found: {self.path}")

    def _rows(self) -> Iterator[list[str]]:
        if self.path.suffix == ".zip":
            with zipfile.ZipFile(self.path) as z:
                members = [n for n in z.namelist() if n.lower().endswith(".csv")]
                if len(members) != 1:
                    raise NotConfigured(f"{self.path.name}: expected 1 CSV, got {members}")
                with z.open(members[0]) as raw:
                    yield from csv.reader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"))
        else:
            with self.path.open(encoding="utf-8", errors="replace") as fh:
                yield from csv.reader(fh)

    def positions(self, bbox: BBox, start: datetime, end: datetime) -> Iterator[AisPosition]:
        seen: set[tuple[str, datetime, float, float]] = set()
        for row in self._rows():
            if not row or row[0].startswith("#"):
                continue  # the header line, which is literally "# Timestamp,..."
            pos = _parse_dma_row(row, self.name)
            if pos is None:
                continue
            if not (start <= pos.timestamp <= end):
                continue
            if not (
                bbox.min_lon <= pos.lon <= bbox.max_lon
                and bbox.min_lat <= pos.lat <= bbox.max_lat
            ):
                continue
            key = pos.dedup_key
            if key in seen:
                continue
            seen.add(key)
            yield pos


class GFWDetectionSource(AisSource):
    """Global Fishing Watch published SAR detections — a reference layer, not AIS.

    This is deliberately *not* an AIS feed and the class is honest about it:
    `is_ground_truth` is False, because GFW's detections have already been
    matched against AIS upstream by someone else. Treating them as our own
    correlation would be claiming their work, which §3.1 says plainly would
    unravel in a technical round.

    What it *is* good for is a genuine independent cross-check, and it turned out
    to be a much better one than the spec assumed. The public API exposes
    per-detection records — not merely gridded counts — through
    ``4wings/tile/position``, each carrying a ``matched`` flag and, decisively,
    the **source granule id**. So the comparison over Portugal is detection-for
    -detection against a published layer computed over the identical Sentinel-1
    granule, rather than "they saw N in this box and I saw M".

    Fetching needs the network, so it belongs to provisioning, never to the
    enclave. The adapter here reads what provisioning wrote.
    """

    name = "gfw"
    is_ground_truth = False
    attribution = "Global Fishing Watch SAR vessel detections, CC BY-NC 4.0."

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None

    def positions(self, bbox: BBox, start: datetime, end: datetime) -> Iterator[AisPosition]:
        raise NotConfigured(
            "GFWDetectionSource holds reference detections, not AIS positions. "
            "Use `nightglass-spatial gfw-reference` to fetch them at provisioning "
            "time and compare them against our detections directly — they are not "
            "an input to `ais_match`."
        )


class CustomerFeedSource(AisSource):
    """A live customer feed. Real, via aisstream.io — and never ground truth.

    §3.1 originally wanted this as a stub raising "not configured". It does not
    have to be: aisstream.io serves free real-time global point-level AIS over
    WebSocket and does cover Iberian waters. Making it a real adapter
    exercises the interface far harder than a second CSV reader would — a
    long-lived connection with reconnect-and-backoff, no SLA, and arrival time
    rather than report time.

    But it is a **demonstration source**. Measured against DMA over an identical
    bbox and clock window it returned 132 vessels to DMA's 770, and discovery had
    saturated by minute ten. `is_ground_truth` stays False, permanently.

    Recording is a provisioning-time activity — it needs egress — so the enclave
    reads recorded files rather than opening a socket. That the socket code lives
    outside the enclave is the same boundary the corpus fetcher and the model
    puller sit on.
    """

    name = "aisstream"
    is_ground_truth = False
    attribution = "aisstream.io live AIS — demonstration coverage, not ground truth."

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None

    def positions(self, bbox: BBox, start: datetime, end: datetime) -> Iterator[AisPosition]:
        if self.path is None or not self.path.exists():
            raise NotConfigured(
                "No recorded aisstream capture configured. Set "
                "NIGHTGLASS_AISSTREAM_CAPTURE to a recording, or run the recorder "
                "at provisioning time. This source is real-time only — it has no "
                "archive, so it cannot serve a past acquisition."
            )
        seen: set[tuple[str, datetime, float, float]] = set()
        with self.path.open(encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                try:
                    stamp = datetime.fromisoformat(row["utc"].replace(" UTC", "+00:00"))
                    lon, lat = float(row["lon"]), float(row["lat"])
                except (KeyError, ValueError):
                    continue
                if not (start <= stamp <= end):
                    continue
                if not (
                    bbox.min_lon <= lon <= bbox.max_lon and bbox.min_lat <= lat <= bbox.max_lat
                ):
                    continue
                sog = row.get("sog")
                cog = row.get("cog")
                pos = AisPosition(
                    mmsi=str(row.get("MMSI") or row.get("mmsi") or ""),
                    timestamp=stamp,
                    lon=lon,
                    lat=lat,
                    sog_ms=float(sog) * KNOTS_TO_MS if sog else None,
                    cog_deg=float(cog) if cog else None,
                    name=row.get("name"),
                    source=self.name,
                )
                if pos.dedup_key in seen:
                    continue
                seen.add(pos.dedup_key)
                yield pos


SOURCES: dict[str, type[AisSource]] = {
    "dma": DMAFileSource,
    "gfw": GFWDetectionSource,
    "aisstream": CustomerFeedSource,
}


def acquisition_window(centre: datetime, minutes: float) -> tuple[datetime, datetime]:
    """``±minutes`` around the acquisition instant.

    The window is symmetric in *time* and generous on purpose: it is what gets
    fed to the interpolator, not the match tolerance. Being wide here costs a
    slightly longer scan of the daily file and buys bracketing reports on both
    sides of the acquisition for as many vessels as possible, which is the
    difference between interpolating a track and extrapolating one.
    """
    delta = timedelta(minutes=minutes)
    return centre - delta, centre + delta


# -- provisioning (ONLINE upstream, offline here) -----------------------------


def slice_path(root: str | Path, aoi: str, start: datetime, end: datetime) -> Path:
    """The slice's name, derived from what is in it rather than chosen."""
    return Path(root) / f"ais_{aoi.lower()}_{start:%Y%m%d}_{start:%H%M}-{end:%H%M}.csv"


def slice_day(
    day_file: str | Path,
    out: str | Path,
    bbox: BBox,
    start: datetime,
    end: datetime,
    *,
    progress: bool = True,
) -> tuple[int, int]:
    """Cut the acquisition window out of a DMA daily file. Returns (kept, scanned).

    A daily file is ~24 M rows and the window is ~20 minutes of it — 0.6%. The
    matcher can read the zip directly and does, but it reads it *twice* (once for
    `load-ais`, once for `validate-shift`), and each pass costs a full scan of
    5.45 GB of CSV. Cutting the slice once at provisioning time is the same
    trade as clipping the coastline online: do the expensive filtering where the
    input already is, and let the enclave mount only what its AOI needs.

    Rows are copied **verbatim**, including the multi-station duplicates that
    make up 71% of the raw feed. Deduplication belongs to `DMAFileSource`, which
    is what reads this file back, and a slice that had already deduplicated
    would be a different input silently pretending to be the same one.
    """
    day_file, out = Path(day_file), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # The cheap path: one day, so the date is constant and `HH:MM:SS` orders
    # lexicographically. That skips `strptime` on ~99% of rows, which is the
    # difference between a two-minute slice and a ten-minute one. A window that
    # crosses midnight cannot use it, so it falls back to parsing every row.
    same_day = start.strftime("%d/%m/%Y") == end.strftime("%d/%m/%Y")
    day, lo, hi = start.strftime("%d/%m/%Y"), start.strftime("%H:%M:%S"), end.strftime("%H:%M:%S")

    source = DMAFileSource(day_file)
    kept = scanned = 0
    tty = progress and sys.stdout.isatty()
    tmp = out.with_suffix(".part")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        # `\n`, not csv's `\r\n` default: the DMA file is LF and the slice is a
        # cut of it, so a re-encoded line ending would make a byte-level diff
        # against the source report every row as changed.
        writer = csv.writer(fh, lineterminator="\n")
        # `_rows`, not `positions`: this is a byte-level cut of the file and must
        # not parse, dedup or reorder anything on the way through.
        for row in source._rows():
            scanned += 1
            if progress and scanned % 2_000_000 == 0:
                line = f"   scanned {scanned / 1e6:5.1f} M rows, kept {kept:,}"
                print(f"\r{line}" if tty else line, end="" if tty else "\n", flush=True)
            if not row or row[0].startswith("#"):
                continue
            stamp = row[0]
            if same_day:
                if stamp[:10] != day or not (lo <= stamp[11:19] <= hi):
                    continue
            else:
                pos = _parse_dma_row(row, source.name)
                if pos is None or not (start <= pos.timestamp <= end):
                    continue
            try:
                lat, lon = float(row[3]), float(row[4])
            except (ValueError, IndexError):
                continue
            if not (bbox.min_lon <= lon <= bbox.max_lon and bbox.min_lat <= lat <= bbox.max_lat):
                continue
            writer.writerow(row)
            kept += 1
    if tty:
        print(f"\r   scanned {scanned / 1e6:5.1f} M rows, kept {kept:,}      ")
    tmp.rename(out)
    return kept, scanned
