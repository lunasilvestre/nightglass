"""`stac_search`, `detect_vessels`, `ais_match` and `correlate` (§5).

The first three wrap work that M3 already did and validated — a PostGIS
catalogue query, the CFAR detector, and the hand-checked space–time join in
`spatial/sql/dark_vessels.sql`. Nothing here reimplements any of it. What this
module adds is the two things M3 left to M4: the orchestration in `correlate`,
and the provenance plumbing that keeps a result gradeable once it has crossed a
tool boundary and lost its Python objects.

Two decisions are made here rather than discovered later, both of them forced by
§5's "tools stay pure functions over the database — no hidden state, no caching
that changes results between runs".

**`correlate` does not re-run the detector when an identical run already
exists.** The tempting reading of "no caching" is that every call must recompute.
It is the wrong one, and the code says why: `_finalise` in `spatial/detect.py`
assigns detection ids (`…:det_00007`) *after* the length and AOI filters, so
recomputing at a different `min_length_m` renumbers every detection. Reuse is
what keeps `ais_match(["…:det_00007"])` meaning the same thing twice. So the
rule is identity, not recency — a run is reused only when the detector, version,
polarisation, AOI box, coastline descriptor and every entry in
`DetectorConfig` match what a fresh run would use. `detect.runs.parameters` is
jsonb precisely so that comparison is possible; `REUSE_EXPLAINED` below is the
sentence that ends up on the detection's provenance saying which happened.

**`correlate` is bounded to one scene per call.** The detector takes ~14 s over
a granule, and the alternative — return a run id and let the client poll — needs
a job table, a status endpoint and a lifecycle, which is exactly the hidden
state §5 rules out. One scene fits inside an MCP call with margin; six do not.
Every scene the search found is still returned, each carrying a provenance note
saying whether it was correlated and, if not, how to select it.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import psycopg

from nightglass.config import BBox, settings
from nightglass.schemas import CorrelationResult, Detection, Match, Provenance, Scene
from nightglass.tools.base import (
    DMA_ATTRIBUTION,
    ToolError,
    now,
    scene_provenance,
    session,
)

#: The seaward shoreline buffer every detector run in this deployment uses.
#: Not a default pulled out of the air — measured over the Kattegat: 300 m
#: leaves 35% of detections unmatched, 1 km leaves 25%, 3 km leaves 14%, and
#: going from 300 m to 3 km drops 21 detections of which 18 were unmatched and
#: only 3 matched. 1 km is where buying precision stops being nearly free.
#: It must equal what `nightglass-spatial detect` uses, or every run this tool
#: looks for will be a miss and it will recompute the pixels every call.
COASTLINE_BUFFER_M = 1000.0

REUSE_EXPLAINED = (
    "read from detect.runs #{run_id} ({started:%Y-%m-%d %H:%M} UTC) — every "
    "recorded parameter is identical to what a fresh run would use, so "
    "recomputing would return the same detections under different ids"
)

_FLOAT_TOL = 1e-9


# -- stac_search --------------------------------------------------------------


def stac_search(
    bbox: list[float] | BBox,
    start: datetime,
    end: datetime,
    *,
    conn: psycopg.Connection | None = None,
) -> list[Scene]:
    """§5: `stac_search(bbox, start, end) -> list[Scene]`.

    A catalogue query, not a directory listing — `stac.scenes` holds each
    granule as a whole STAC Item, so this same tool could be pointed at a real
    STAC API in a customer deployment without the callers noticing.
    """
    box = _as_bbox(bbox)
    from nightglass.spatial.db import stac_search as _query

    with session(conn) as db:
        rows = _query(db, box, start, end)
    return [_scene(row) for row in rows]


def _scene(row: dict[str, Any], *, note: str | None = None) -> Scene:
    return Scene(
        id=row["id"],
        acquisition_time=row["acquisition_time"],
        mode=row.get("mode") or "IW",
        polarizations=list(row.get("polarizations") or []),
        footprint_wkt=row["footprint_wkt"],
        incidence_angle=row.get("incidence_angle"),
        provenance=scene_provenance(row, note=note),
    )


# -- detect_vessels -----------------------------------------------------------


def detect_vessels(
    scene_id: str,
    min_length_m: float = 15.0,
    *,
    conn: psycopg.Connection | None = None,
    recompute: bool = False,
) -> list[Detection]:
    """§5: `detect_vessels(scene_id, min_length_m=15.0) -> list[Detection]`.

    Returns the detections our own CFAR detector finds over the deployment's
    configured AOI. Reuses an identical recorded run if there is one — see the
    module docstring for why that is the spec-compliant branch rather than a
    shortcut — and otherwise reads the pixels, which takes about 14 s and writes
    the run so the next call is a database read.

    `recompute=True` forces the pixels to be read again. It exists so the reuse
    path can be checked against the thing it claims to be equal to.
    """
    aoi = settings.aoi
    with session(conn) as db:
        row = _scene_row(db, scene_id)
        wanted = _detector_config(min_length_m)
        run = None if recompute else _equivalent_run(db, scene_id, wanted, aoi.bbox)

        if run is None:
            run_id, started = _run_detector(db, row, wanted, aoi.bbox)
            note = (
                f"computed now from {row['granule_path'] and 'the granule on disk'}; "
                f"written to detect.runs #{run_id} so the next call reads it back"
            )
        else:
            run_id, started = run["id"], run["started_at"]
            note = REUSE_EXPLAINED.format(run_id=run_id, started=started)

        return _detections_of_run(db, run_id, min_length_m, note=note)


def _scene_row(db: psycopg.Connection, scene_id: str) -> dict[str, Any]:
    from nightglass.spatial.db import scene_row

    row = scene_row(db, scene_id)
    if row is None:
        raise ToolError(
            f"no scene {scene_id!r} in the catalogue. Run `make scenes` to "
            "catalogue every granule on disk, then call stac_search to see "
            "which ids exist."
        )
    return row


def _detector_config(min_length_m: float) -> Any:
    from nightglass.spatial.detect import DetectorConfig

    return DetectorConfig(min_length_m=float(min_length_m))


def _equivalent_run(
    db: psycopg.Connection, scene_id: str, wanted: Any, bbox: BBox
) -> dict[str, Any] | None:
    """The most recent recorded run whose every input matches `wanted`.

    "Matches" is exact on everything except `min_length_m`, where a run at a
    *smaller* threshold qualifies: the length filter is the last thing
    `_finalise` applies, after detection and after measurement, so its
    detections at ≥ 30 m are precisely the run-at-30 m detections. Nothing
    upstream of that filter reads `min_length_m`, which is what makes this
    a provable equality rather than an approximation.
    """
    from dataclasses import asdict

    from nightglass.spatial.detect import DETECTOR_NAME, DETECTOR_VERSION

    target = asdict(wanted)
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, started_at, aoi_bbox, coastline, parameters
            FROM detect.runs
            WHERE scene_id = %s AND detector = %s AND version = %s AND polarization = %s
            ORDER BY started_at DESC
            """,
            (scene_id, DETECTOR_NAME, DETECTOR_VERSION, wanted.polarization),
        )
        candidates = cur.fetchall()

    for run in candidates:
        if not _same_bbox(run["aoi_bbox"], bbox):
            continue
        if run["coastline"] != _coastline_descriptor():
            continue
        if _same_parameters(run["parameters"] or {}, target):
            return run
    return None


def _same_bbox(stored: list[float] | None, bbox: BBox) -> bool:
    if not stored or len(stored) != 4:
        return False
    return all(_close(a, b) for a, b in zip(stored, bbox.as_list(), strict=True))


def _same_parameters(stored: dict[str, Any], target: dict[str, Any]) -> bool:
    """Every tunable equal, except a `min_length_m` the stored run can be filtered to."""
    if set(stored) != set(target):
        # A run recorded by a different build of DetectorConfig. Not comparable,
        # so not reusable — silently treating an absent parameter as agreement
        # is how a "cache" starts changing results.
        return False
    for key, want in target.items():
        got = stored[key]
        if key == "min_length_m":
            if not (isinstance(got, (int, float)) and float(got) <= float(want) + _FLOAT_TOL):
                return False
        elif isinstance(want, (int, float)) and isinstance(got, (int, float)):
            if not _close(float(got), float(want)):
                return False
        elif got != want:
            return False
    return True


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def _coastline_descriptor() -> str:
    """The string `insert_detections` records for a run using this AOI's shoreline.

    Built from the same two values the detector writes — source and buffer — so
    a coastline that was refetched at a different resolution stops matching and
    the run is recomputed rather than quietly reused against a different mask.
    """
    return f"GSHHG f/L1 +{COASTLINE_BUFFER_M:.0f} m"


def _run_detector(
    db: psycopg.Connection, row: dict[str, Any], cfg: Any, bbox: BBox
) -> tuple[int, datetime]:
    """Read the pixels. ~14 s, and the reason `correlate` is bounded to one scene."""
    from nightglass.spatial.coastline import Coastline, CoastlineError, clipped_path
    from nightglass.spatial.db import insert_detections
    from nightglass.spatial.detect import detect_scene
    from nightglass.spatial.safe import SafeProduct

    granule = row.get("granule_path")
    if not granule:
        raise ToolError(
            f"scene {row['id']} is catalogued but has no granule path, so the "
            "detector cannot read it. Re-run `make scenes` against the archive."
        )

    try:
        coastline = Coastline.load(
            clipped_path(settings.coastline_dir, settings.aoi.name),
            buffer_m=COASTLINE_BUFFER_M,
        )
    except CoastlineError as exc:
        # Loud by design. Without a shoreline the data-derived land mask cannot
        # tell a 100 m skerry from a 100 m hull, and every coastal detection
        # becomes an unmatched one — which is the exact number this project is
        # careful about. Degrading silently here would corrupt it.
        raise ToolError(
            f"{exc} Until then this scene cannot be detected over without "
            "producing coastal false alarms that would be reported as leads."
        ) from exc

    try:
        product = SafeProduct(granule)
    except Exception as exc:
        raise ToolError(
            f"cannot open granule {granule}: {type(exc).__name__}: {exc}. "
            "The archive is mounted read-only at /app/data/raw/sar."
        ) from exc

    detections, measurements, run = detect_scene(
        product, config=cfg, bbox=bbox, coastline=coastline
    )
    insert_detections(db, run, detections, measurements)

    with db.cursor() as cur:
        cur.execute(
            "SELECT id, started_at FROM detect.runs WHERE scene_id = %s AND detector = %s "
            "AND polarization = %s ORDER BY started_at DESC LIMIT 1",
            (run.scene_id, run.detector, run.polarization),
        )
        fresh = cur.fetchone()
    if fresh is None:  # pragma: no cover — insert_detections just wrote it
        raise ToolError(f"detector ran over {run.scene_id} but no run row was recorded")
    return fresh["id"], fresh["started_at"]


def _detections_of_run(
    db: psycopg.Connection, run_id: int, min_length_m: float, *, note: str
) -> list[Detection]:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.scene_id, ST_X(d.geom) AS lon, ST_Y(d.geom) AS lat,
                   d.length_m, d.heading_deg, d.confidence,
                   r.detector, r.version, r.parameters, r.started_at
            FROM detect.detections d
            JOIN detect.runs r ON r.id = d.run_id
            WHERE d.run_id = %s AND d.length_m >= %s
            ORDER BY d.id
            """,
            (run_id, float(min_length_m)),
        )
        rows = cur.fetchall()

    return [
        Detection(
            id=r["id"],
            scene_id=r["scene_id"],
            lon=r["lon"],
            lat=r["lat"],
            length_m=r["length_m"],
            heading_deg=r["heading_deg"],
            confidence=r["confidence"],
            provenance=Provenance(
                source=f"{r['detector']} {r['version']}",
                retrieved_at=now(),
                licence=None,
                note=(
                    f"own detector, two-parameter CFAR k={(r['parameters'] or {}).get('k')}, "
                    f"block={(r['parameters'] or {}).get('block')} px, "
                    f"{(r['parameters'] or {}).get('polarization')}; {note}"
                ),
            ),
        )
        for r in rows
    ]


# -- ais_match ----------------------------------------------------------------


def ais_match(
    detections: list[str],
    time_window_min: int = 60,
    radius_m: float = 500.0,
    *,
    conn: psycopg.Connection | None = None,
    correct_azimuth: bool = True,
) -> list[Match]:
    """§5: `ais_match(detections, time_window_min=60, radius_m=500.0) -> list[Match]`.

    Wraps `spatial/sql/dark_vessels.sql` via `db.dark_query`, which is where the
    substance is: each vessel's position is *interpolated onto the acquisition
    instant* from the AIS reports bracketing it, and then displaced along-track
    by the Doppler shift SAR imposes on anything with line-of-sight velocity.
    Neither offset is absorbed into a wider radius, because the radius is the
    whole boundary between "matched" and "dark" and widening it buys false
    matches at the same rate it avoids false darks.

    §5's default window is 60 min and the deployment's measured one is 11. The
    wider default is safe here and not merely tolerated: the SQL brackets each
    vessel with `LEAD` over consecutive reports, so it always takes the tightest
    straddling pair available and a wider window can only help a vessel whose
    reports are sparse. `time_delta_s` carries the staleness of the pair that
    was actually used, so a match interpolated across a long gap is visible
    rather than implied.
    """
    ids = list(dict.fromkeys(detections))
    if not ids:
        return []

    with session(conn) as db:
        by_scene = _scenes_of(db, ids)
        wanted = set(ids)
        out: list[Match] = []
        for scene_id in by_scene:
            rows, _summary = _dark_rows(db, scene_id, radius_m, time_window_min, correct_azimuth)
            feed = _feed_in_window(db, scene_id, time_window_min)
            sources = _sources_by_mmsi(db, [r["mmsi"] for r in rows if r["mmsi"]])
            out.extend(
                _match(r, sources, feed, radius_m, time_window_min, correct_azimuth)
                for r in rows
                if r["detection_id"] in wanted
            )

    order = {d: i for i, d in enumerate(ids)}
    return sorted(out, key=lambda m: order.get(m.detection_id, len(order)))


def _dark_rows(
    db: psycopg.Connection,
    scene_id: str,
    radius_m: float,
    time_window_min: float,
    correct_azimuth: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from nightglass.spatial.db import dark_query

    return dark_query(
        db,
        scene_id=scene_id,
        radius_m=radius_m,
        window_min=float(time_window_min),
        correct_azimuth=correct_azimuth,
    )


def _scenes_of(db: psycopg.Connection, ids: list[str]) -> list[str]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, scene_id FROM detect.detections WHERE id = ANY(%s)", (ids,)
        )
        rows = cur.fetchall()
    known = {r["id"]: r["scene_id"] for r in rows}
    missing = [i for i in ids if i not in known]
    if missing:
        raise ToolError(
            f"{len(missing)} detection id(s) are not in detect.detections, "
            f"starting with {missing[0]!r}. Call detect_vessels for the scene "
            "first — ids are assigned by the detector run and are not guessable."
        )
    return list(dict.fromkeys(known[i] for i in ids))


def _feed_in_window(
    db: psycopg.Connection, scene_id: str, window_min: float
) -> tuple[str, bool]:
    """Which AIS feed was actually searched, and whether it is ground truth.

    A dark detection is dark *against a named feed at a named time*, and a
    `Match` that says only "dark" has thrown away the half of that statement
    which carries the caveat. §7's innocent explanations all live in the part
    that would have been dropped.

    The ground-truth flag has to come from here rather than from the join's
    output row, and getting that wrong is subtle: `dark_vessels.sql` returns
    `COALESCE(n.is_ground_truth, false)`, which is false for every unmatched
    detection simply because there is no matched vessel to read it from. Copying
    that onto the `Match` would make `CorrelationResult.rate_is_quotable` false
    the moment a single detection went unmatched — i.e. exactly when the
    question is asked — and the honesty guard would read as working while
    actually being stuck off. The flag describes the *feed that was searched*,
    not whether the search succeeded.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT p.source, bool_and(p.is_ground_truth) AS ground_truth
            FROM ais.positions p
            JOIN stac.scenes s ON s.id = %s
            WHERE p.ts BETWEEN s.acquisition_time - make_interval(secs => %s)
                           AND s.acquisition_time + make_interval(secs => %s)
            GROUP BY p.source
            """,
            # secs, not mins: make_interval's named arguments are integers apart
            # from `secs`, so a fractional window silently fails to resolve the
            # function rather than rounding. The same reason dark_vessels.sql
            # takes a window in seconds.
            (scene_id, float(window_min) * 60.0, float(window_min) * 60.0),
        )
        rows = sorted(cur.fetchall(), key=lambda r: r["source"] or "")
    if not rows:
        return "none loaded", False
    # Mixed feeds are only as trustworthy as their weakest member, so a single
    # non-ground-truth source disqualifies the set rather than averaging with it.
    return "+".join(r["source"] for r in rows), all(bool(r["ground_truth"]) for r in rows)


def _sources_by_mmsi(db: psycopg.Connection, mmsis: list[str]) -> dict[str, str]:
    if not mmsis:
        return {}
    with db.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT mmsi, source FROM ais.positions WHERE mmsi = ANY(%s)",
            (list({m for m in mmsis if m}),),
        )
        out: dict[str, str] = {}
        for r in cur.fetchall():
            out[r["mmsi"]] = (
                r["source"] if r["mmsi"] not in out else f"{out[r['mmsi']]}+{r['source']}"
            )
    return out


def _match(
    row: dict[str, Any],
    sources: dict[str, str],
    feed: tuple[str, bool],
    radius_m: float,
    window_min: float,
    correct_azimuth: bool,
) -> Match:
    feeds, feed_is_ground_truth = feed
    mmsi = row["mmsi"]
    ground_truth = bool(row["source_is_ground_truth"]) if mmsi else feed_is_ground_truth
    source = sources.get(mmsi, feeds) if mmsi else feeds

    if mmsi:
        bits = [f"MMSI {mmsi}"]
        if row.get("vessel_name"):
            bits.append(str(row["vessel_name"]).strip())
        if row.get("ship_type"):
            bits.append(str(row["ship_type"]).strip())
        detail = ", ".join(bits)
        shift = row.get("azimuth_shift_m")
        uncorrected = row.get("distance_uncorrected_m")
        geometry = (
            f"azimuth displacement {shift:+.0f} m applied "
            f"(separation before correction {uncorrected:.0f} m)"
            if correct_azimuth and shift is not None and uncorrected is not None
            else "azimuth correction OFF"
        )
        note = (
            f"{detail}; position interpolated onto the acquisition instant, {geometry}; "
            f"matched inside {radius_m:.0f} m / ±{window_min:g} min"
        )
    else:
        note = (
            f"no AIS correspondence in {feeds} within {radius_m:.0f} m and "
            f"±{window_min:g} min of acquisition. This is a statement about one "
            "feed at one instant, not about the vessel: revisit gaps, terrestrial "
            "coverage limits, transponder failure, class B low power and vessels "
            "not required to carry AIS all produce it. A lead, not a conclusion."
        )

    return Match(
        detection_id=row["detection_id"],
        mmsi=mmsi,
        distance_m=row.get("distance_m"),
        time_delta_s=row.get("time_delta_s"),
        status=row["status"],
        ais_source=source,
        source_is_ground_truth=ground_truth,
        provenance=Provenance(
            source=f"AIS/{source}",
            retrieved_at=now(),
            licence=DMA_ATTRIBUTION if "dma" in source.lower() else None,
            note=note,
        ),
    )


# -- correlate ----------------------------------------------------------------


def correlate(
    bbox: list[float] | BBox,
    start: datetime,
    end: datetime,
    min_length_m: float = 15.0,
    *,
    scene_id: str | None = None,
    radius_m: float | None = None,
    time_window_min: float | None = None,
    conn: psycopg.Connection | None = None,
) -> CorrelationResult:
    """§5: `correlate(bbox, start, end, min_length_m=15.0) -> CorrelationResult`.

    `stac_search → detect_vessels → ais_match`, with the chain preserved rather
    than flattened to a count. Bounded to one scene per call — see the module
    docstring. Pass `scene_id` to choose which; the default is the most recent
    acquisition in the window, and every other candidate comes back in `scenes`
    with a provenance note saying it was not correlated and how to select it.

    The detector always runs over the *deployment's* AOI, never over the bbox
    given here, so there is exactly one canonical detection set per scene and
    detection ids stay stable across calls. This bbox then filters which of
    those detections are reported. That split is deliberate: CFAR statistics are
    computed per block over the read window, so a smaller window is not a subset
    of a larger one's result, and pretending otherwise would make the tool's
    answer depend on how the question was framed.
    """
    box = _as_bbox(bbox)
    aoi = settings.aoi
    if not _intersects(box, aoi.bbox):
        raise ToolError(
            f"the requested bbox {box} lies outside this deployment's AOI "
            f"({aoi.name}, {aoi.bbox}). The detector only ever runs over the "
            "configured AOI, so there is nothing here to correlate. Set "
            f"NIGHTGLASS_AOI to an AOI covering it, or query inside {aoi.bbox}."
        )

    radius = settings.match_radius_m if radius_m is None else radius_m
    window = settings.match_window_min if time_window_min is None else time_window_min

    with session(conn) as db:
        from nightglass.spatial.db import stac_search as _query

        rows = _query(db, box, start, end)
        if not rows:
            return CorrelationResult(
                aoi_name=_aoi_label(box, aoi),
                bbox=box.as_list(),
                start=start,
                end=end,
            )

        ranked = _rank(rows, box)
        chosen = _choose_scene(ranked, scene_id)
        scenes = [_scene(r, note=_scene_note(r, chosen, len(ranked))) for r in ranked]

        detections = detect_vessels(chosen["id"], min_length_m, conn=db)
        inside = [d for d in detections if _within(d, box)]
        matches = ais_match(
            [d.id for d in inside], int(window), radius, conn=db
        )

    return CorrelationResult(
        aoi_name=_aoi_label(box, aoi),
        bbox=box.as_list(),
        start=start,
        end=end,
        scenes=scenes,
        detections=inside,
        matches=matches,
    )


def _rank(rows: list[dict[str, Any]], box: BBox) -> list[dict[str, Any]]:
    """Best coverage of the requested area first, most recent breaking ties.

    The obvious rule — take the newest scene — is the wrong one when consecutive
    granules from a single pass both clip the AOI, which is the normal case: the
    Kattegat window returns two granules 25 s apart, and the newer one sees less
    of the area than the older. Ranking by how much of the requested box each
    footprint actually covers answers the question the caller asked.

    Deliberately *not* "prefer a scene that already has a detector run". That
    would make the answer depend on what happened to be computed earlier, which
    is the caching-changes-results failure §5 rules out — and it is a failure
    that would be invisible, because the wrong answer would still be a real
    correlation over a real scene.
    """
    from shapely import wkt as _wkt
    from shapely.geometry import box as _box

    aoi = _box(box.min_lon, box.min_lat, box.max_lon, box.max_lat)

    def coverage(row: dict[str, Any]) -> float:
        try:
            return _wkt.loads(row["footprint_wkt"]).intersection(aoi).area
        except Exception:  # noqa: BLE001 — an unparseable footprint ranks last
            return 0.0

    return sorted(rows, key=lambda r: (-coverage(r), -r["acquisition_time"].timestamp()))


def _choose_scene(rows: list[dict[str, Any]], scene_id: str | None) -> dict[str, Any]:
    if scene_id is None:
        return rows[0]
    for row in rows:
        if row["id"] == scene_id:
            return row
    found = ", ".join(r["id"] for r in rows)
    raise ToolError(
        f"scene {scene_id!r} is not among the {len(rows)} scene(s) this search "
        f"returned. Available: {found}"
    )


def _scene_note(row: dict[str, Any], chosen: dict[str, Any], total: int) -> str:
    if row["id"] == chosen["id"]:
        return (
            "correlated"
            if total == 1
            else f"correlated — covers most of the requested area of the {total} "
            "scenes in this window"
        )
    return (
        "found by the catalogue search but NOT correlated: correlate reads one "
        "scene per call, because reading a granule takes tens of seconds. Call "
        f"correlate again with scene_id={row['id']!r} to correlate this one."
    )


def _within(detection: Detection, box: BBox) -> bool:
    return (
        box.min_lon <= detection.lon <= box.max_lon
        and box.min_lat <= detection.lat <= box.max_lat
    )


def _intersects(a: BBox, b: BBox) -> bool:
    return not (
        a.max_lon < b.min_lon
        or a.min_lon > b.max_lon
        or a.max_lat < b.min_lat
        or a.min_lat > b.max_lat
    )


def _aoi_label(box: BBox, aoi: Any) -> str:
    if all(_close(a, b) for a, b in zip(box.as_list(), aoi.bbox.as_list(), strict=True)):
        return aoi.name
    return f"{aoi.name} (sub-area {box})"


def _as_bbox(bbox: list[float] | BBox) -> BBox:
    if isinstance(bbox, BBox):
        return bbox
    values = list(bbox)
    if len(values) != 4:
        raise ToolError(
            f"bbox needs 4 numbers as min_lon,min_lat,max_lon,max_lat — got {values!r}"
        )
    return BBox.parse(",".join(str(v) for v in values), origin="bbox")
