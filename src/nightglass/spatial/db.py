"""PostGIS access for the spatial layer.

Thin on purpose. §5 says the tools are *pure functions over the database* — no
hidden state, no caching that changes results between runs — so this module
opens connections, runs statements that live in `.sql` files next to it, and
returns rows. It does not hold a session, a pool, or a query builder.

The SQL is in files rather than in Python strings because §M3 asks for the
dark-vessel join to be "plain SQL, hand-checked". A query you can open in a
`.sql` file, read top to bottom, and run a CTE at a time is hand-checkable; the
same query assembled from f-strings is not.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from nightglass.config import BBox, settings
from nightglass.schemas import Detection

SQL_DIR = Path(__file__).with_name("sql")


def sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def connect(dsn: str | None = None) -> psycopg.Connection:
    """A connection with dict rows and autocommit off."""
    return psycopg.connect(dsn or settings.postgres_dsn, row_factory=dict_row)


# -- schema ------------------------------------------------------------------


def migrate(conn: psycopg.Connection, *, drop: bool = False) -> list[str]:
    """Apply every numbered migration in `sql/`, in order.

    `drop` exists for development and says what it does. It is not a
    down-migration and makes no attempt to be one — it removes the M3 schemas
    entirely, which is honest about the fact that nothing here is precious: the
    scenes and detections are recomputable from the granules on disk in seconds.
    """
    applied: list[str] = []
    with conn.cursor() as cur:
        if drop:
            cur.execute("DROP SCHEMA IF EXISTS detect CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS ais CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS stac CASCADE")
            applied.append("DROP SCHEMA detect, ais, stac")
        for path in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            cur.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
            applied.append(path.name)
    conn.commit()
    return applied


# -- scenes ------------------------------------------------------------------


def upsert_scene(conn: psycopg.Connection, item: dict, granule_path: str) -> None:
    """Store a STAC Item, keeping the Item itself and extracting what is indexed."""
    p = item["properties"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stac.scenes (
                id, collection, acquisition_time, start_time, end_time,
                platform, mode, product_type, polarizations, pass_direction,
                relative_orbit, absolute_orbit, incidence_angle,
                platform_heading_deg, platform_speed_ms,
                range_bearing_deg, azimuth_bearing_deg,
                footprint, granule_path, item
            ) VALUES (
                %(id)s, %(collection)s, %(dt)s, %(start)s, %(end)s,
                %(platform)s, %(mode)s, %(ptype)s, %(pols)s, %(pass)s,
                %(rel)s, %(abs)s, %(inc)s,
                %(heading)s, %(speed)s, %(rbrg)s, %(abrg)s,
                ST_GeomFromGeoJSON(%(geom)s), %(path)s, %(item)s
            )
            ON CONFLICT (id) DO UPDATE SET
                item = EXCLUDED.item,
                granule_path = EXCLUDED.granule_path,
                ingested_at = now()
            """,
            {
                "id": item["id"],
                "collection": item.get("collection", "sentinel-1-grd"),
                "dt": p["datetime"],
                "start": p.get("start_datetime"),
                "end": p.get("end_datetime"),
                "platform": p.get("platform"),
                "mode": p.get("sar:instrument_mode"),
                "ptype": p.get("sar:product_type"),
                "pols": p.get("sar:polarizations"),
                "pass": p.get("sat:orbit_state"),
                "rel": p.get("sat:relative_orbit"),
                "abs": p.get("sat:absolute_orbit"),
                "inc": p.get("view:incidence_angle"),
                "heading": p.get("nightglass:platform_heading_deg"),
                "speed": p.get("nightglass:platform_speed_ms"),
                "rbrg": p.get("nightglass:range_bearing_deg"),
                "abrg": p.get("nightglass:azimuth_bearing_deg"),
                "geom": json.dumps(item["geometry"]),
                "path": granule_path,
                "item": Jsonb(item),
            },
        )
    conn.commit()


def scene_row(conn: psycopg.Connection, scene_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM stac.scenes WHERE id = %s", (scene_id,))
        return cur.fetchone()


def stac_search(
    conn: psycopg.Connection, bbox: BBox, start: datetime, end: datetime
) -> list[dict]:
    """§5's `stac_search`, as a catalogue query rather than a directory listing."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, acquisition_time, mode, polarizations,
                   ST_AsText(footprint) AS footprint_wkt, incidence_angle,
                   granule_path, item
            FROM stac.scenes
            WHERE acquisition_time BETWEEN %(start)s AND %(end)s
              AND ST_Intersects(
                    footprint,
                    ST_MakeEnvelope(%(minx)s, %(miny)s, %(maxx)s, %(maxy)s, 4326))
            ORDER BY acquisition_time
            """,
            {
                "start": start,
                "end": end,
                "minx": bbox.min_lon,
                "miny": bbox.min_lat,
                "maxx": bbox.max_lon,
                "maxy": bbox.max_lat,
            },
        )
        return cur.fetchall()


# -- detections --------------------------------------------------------------


def insert_detections(
    conn: psycopg.Connection,
    run: Any,
    detections: list[Detection],
    measurements: list[Any],
) -> int:
    """Write one detector run and its detections, atomically.

    Re-running the detector over the same scene replaces the previous run rather
    than accumulating alongside it. Two runs of the same detector over the same
    granule are not two observations — they are one observation computed twice,
    and keeping both would double every count downstream.
    """
    by_id = {m.detection_id: m for m in measurements}
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM detect.runs WHERE scene_id = %s AND detector = %s AND polarization = %s",
            (run.scene_id, run.detector, run.polarization),
        )
        cur.execute(
            """
            INSERT INTO detect.runs (
                scene_id, detector, version, polarization, started_at, seconds,
                aoi_bbox, coastline, parameters, statistics
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                run.scene_id,
                run.detector,
                run.version,
                run.polarization,
                run.started_at,
                run.seconds,
                run.aoi_bbox,
                run.coastline,
                Jsonb(run.parameters),
                Jsonb(
                    {
                        "pixels_examined": run.pixels_examined,
                        "pixels_water": run.pixels_water,
                        "pixels_land_masked": run.pixels_land_masked,
                        "candidates": run.candidates,
                        "detections": run.detections,
                        "rejected_small": run.rejected_small,
                        "rejected_large": run.rejected_large,
                        "rejected_outside_aoi": run.rejected_outside_aoi,
                        "rejected_on_coastline": run.rejected_on_coastline,
                        "water_sigma0_db": run.water_sigma0_db,
                        "nesz_db": run.nesz_db,
                    }
                ),
            ),
        )
        row = cur.fetchone()
        run_id = row["id"]  # type: ignore[index]

        with cur.copy(
            """
            COPY detect.detections (
                id, run_id, scene_id, geom, length_m, width_m, heading_deg,
                confidence, px_row, px_col, incidence_deg, r_over_v_s, cfar_margin_db
            ) FROM STDIN
            """
        ) as copy:
            for d in detections:
                m = by_id.get(d.id)
                copy.write_row(
                    (
                        d.id,
                        run_id,
                        d.scene_id,
                        f"SRID=4326;POINT({d.lon} {d.lat})",
                        d.length_m,
                        m.width_m if m else None,
                        d.heading_deg,
                        d.confidence,
                        m.row if m else None,
                        m.col if m else None,
                        m.incidence_deg if m else None,
                        m.r_over_v_s if m else None,
                        m.cfar_margin_db if m else None,
                    )
                )
    conn.commit()
    return len(detections)


# -- AIS ---------------------------------------------------------------------


def insert_positions(
    conn: psycopg.Connection,
    source: Any,
    bbox: BBox,
    start: datetime,
    end: datetime,
    *,
    batch: int = 50_000,
) -> tuple[int, int]:
    """Stream a source's positions into `ais.positions`.

    `COPY` rather than executemany: a Danish daily file yields tens of thousands
    of rows inside a 22-minute window and millions if the window widens, and the
    round-trip per row is the whole cost at that scale.

    `ON CONFLICT DO NOTHING` cannot be used with COPY, so the load goes through
    an UNLOGGED staging table and one INSERT ... SELECT DISTINCT. The dedup is
    already done in the source adapter; this is the database enforcing the same
    rule rather than trusting it, which matters because the primary key IS the
    §3.2 dedup rule and a second source could load overlapping rows.
    """
    total = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE UNLOGGED TABLE IF NOT EXISTS ais._staging
                (LIKE ais.positions INCLUDING DEFAULTS)
            """
        )
        cur.execute("TRUNCATE ais._staging")

        def flush(rows: list[tuple]) -> None:
            nonlocal total
            if not rows:
                return
            with cur.copy(
                """
                COPY ais._staging (
                    mmsi, ts, lat, lon, geom, sog_ms, cog_deg, heading_deg,
                    name, ship_type, length_m, width_m, nav_status, source,
                    is_ground_truth
                ) FROM STDIN
                """
            ) as copy:
                for r in rows:
                    copy.write_row(r)
            total += len(rows)

        buffer: list[tuple] = []
        for p in source.positions(bbox, start, end):
            buffer.append(
                (
                    p.mmsi,
                    p.timestamp,
                    p.lat,
                    p.lon,
                    f"SRID=4326;POINT({p.lon} {p.lat})",
                    p.sog_ms,
                    p.cog_deg,
                    p.heading_deg,
                    p.name,
                    p.ship_type,
                    p.length_m,
                    p.width_m,
                    p.nav_status,
                    p.source,
                    source.is_ground_truth,
                )
            )
            if len(buffer) >= batch:
                flush(buffer)
                buffer = []
        flush(buffer)

        cur.execute(
            """
            INSERT INTO ais.positions
            SELECT DISTINCT ON (mmsi, ts, lat, lon) *
            FROM ais._staging
            ON CONFLICT (mmsi, ts, lat, lon) DO NOTHING
            """
        )
        cur.execute("SELECT count(DISTINCT mmsi) AS n FROM ais._staging")
        vessels = cur.fetchone()["n"]  # type: ignore[index]
        cur.execute("DROP TABLE ais._staging")
    conn.commit()
    return total, vessels


# -- the §M3 query -----------------------------------------------------------


def dark_query(
    conn: psycopg.Connection,
    *,
    scene_id: str,
    radius_m: float | None = None,
    window_min: float | None = None,
    correct_azimuth: bool = True,
) -> tuple[list[dict], dict[str, Any]]:
    """Run `sql/dark_vessels.sql` and summarise it.

    The summary is not decoration. §3.2 gives the sanity check that decides
    whether any of this is working — published work on Danish waters finds ~5% of
    detections unmatched, and "if your pipeline reports 40% dark, it's broken" —
    so the unmatched fraction is computed and shown every time, next to whether
    the source it came from may be quoted as a rate at all.
    """
    radius_m = settings.match_radius_m if radius_m is None else radius_m
    window_min = settings.match_window_min if window_min is None else window_min
    with conn.cursor() as cur:
        cur.execute(
            sql("dark_vessels.sql"),  # type: ignore[arg-type]
            {
                "scene_id": scene_id,
                "radius_m": radius_m,
                "window_s": window_min * 60.0,
                "correct_azimuth": correct_azimuth,
            },
        )
        rows = cur.fetchall()

    dark = [r for r in rows if r["status"] == "dark"]
    matched = [r for r in rows if r["status"] == "matched"]
    ground_truth = bool(matched) and all(r["source_is_ground_truth"] for r in matched)
    improved = [
        r for r in matched
        if r["distance_uncorrected_m"] is not None
        and r["distance_m"] < r["distance_uncorrected_m"]
    ]
    summary = {
        "detections": len(rows),
        "matched": len(matched),
        "dark": len(dark),
        "unmatched_fraction": f"{len(dark) / len(rows):.1%}" if rows else "n/a",
        "match radius (m)": radius_m,
        "time window (min)": f"±{window_min:g}",
        "azimuth correction": "applied" if correct_azimuth else "OFF",
        "azimuth correction helped": f"{len(improved)}/{len(matched)}" if matched else "n/a",
        "rate is quotable": ground_truth,
    }
    if not ground_truth:
        summary["rate is quotable"] = (
            "NO — source is not ground truth; report matched pairs, never a dark rate"
        )
    return rows, summary


def counts(conn: psycopg.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for label, table in (
            ("scenes", "stac.scenes"),
            ("runs", "detect.runs"),
            ("detections", "detect.detections"),
            ("ais positions", "ais.positions"),
        ):
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            out[label] = cur.fetchone()["n"]  # type: ignore[index]
    return out


def iter_rows(conn: psycopg.Connection, query: str, params: Iterable | None = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(query, params)  # type: ignore[arg-type]
        return cur.fetchall()
