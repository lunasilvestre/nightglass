-- NIGHTGLASS §M3 — the spatial layer.
--
-- Deliberately NOT in docker/postgis/initdb/. That hook fires exactly once, on
-- an empty data directory, and then silently never again — which is fine for
-- CREATE EXTENSION and wrong for anything that will be revised. This file is a
-- migration: idempotent, re-runnable, and reviewable in a diff.
--
-- Three schemas, and the split answers a question §9 says to expect in the
-- hiring-manager round — "why is the AIS table derived?":
--
--   stac    the scene catalogue. One row per granule, the STAC item kept whole.
--   detect  our own detector's output, plus the run that produced it.
--   ais     ingested positions. Derived, deduplicated, and never authoritative.
--
-- Everything is EPSG:4326. Distances are computed with `geography` casts, so
-- they come back in metres on the spheroid rather than in degrees — a 500 m
-- match radius expressed in degrees would be 500/111320 north-south and
-- 500/(111320·cos φ) east-west, and at 57°N those differ by a factor of 1.8.
-- Getting that wrong yields a match tolerance that is directional, which is
-- exactly the sort of bug that looks like a detector problem.

CREATE SCHEMA IF NOT EXISTS stac;
CREATE SCHEMA IF NOT EXISTS detect;
CREATE SCHEMA IF NOT EXISTS ais;


-- ---------------------------------------------------------------------------
-- stac.scenes — the granule catalogue
-- ---------------------------------------------------------------------------
-- §M3 says "scene as a STAC item", and it is worth honouring literally: §5's
-- `stac_search` is a catalogue query, so modelling the catalogue as STAC keeps
-- the door open to pointing that same tool at a real STAC API — which is what a
-- customer deployment would have — instead of at a bespoke table only this
-- project understands. `item` holds the Item verbatim; the columns beside it are
-- extracted for indexing, not as a replacement.
CREATE TABLE IF NOT EXISTS stac.scenes (
    id                   text PRIMARY KEY,
    collection           text        NOT NULL DEFAULT 'sentinel-1-grd',
    acquisition_time     timestamptz NOT NULL,
    start_time           timestamptz,
    end_time             timestamptz,
    platform             text,
    mode                 text,
    product_type         text,
    polarizations        text[],
    pass_direction       text,
    relative_orbit       integer,
    absolute_orbit       integer,
    incidence_angle      double precision,

    -- The acquisition geometry the azimuth-displacement correction needs. These
    -- live on the scene rather than being re-read from the archive per query:
    -- the dark-vessel SQL below is trigonometry over these three numbers, and a
    -- join that had to reopen a 900 MB zip would not be a SQL query.
    platform_heading_deg double precision,
    platform_speed_ms    double precision,
    range_bearing_deg    double precision,
    azimuth_bearing_deg  double precision,

    footprint            geometry(Polygon, 4326) NOT NULL,
    granule_path         text,
    item                 jsonb       NOT NULL,
    ingested_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scenes_footprint_gix ON stac.scenes USING GIST (footprint);
CREATE INDEX IF NOT EXISTS scenes_time_ix       ON stac.scenes (acquisition_time);

COMMENT ON TABLE stac.scenes IS
  'Sentinel-1 granules as STAC Items. `item` is the Item verbatim; the columns '
  'beside it are extracted for indexing. Granules are read in place from the '
  'archive via /vsizip — `granule_path` points at a .zip, not at an extracted SAFE.';


-- ---------------------------------------------------------------------------
-- detect.runs — the provenance of a detection set
-- ---------------------------------------------------------------------------
-- §7: output that cannot be traced cannot be graded, so it cannot enter the
-- intelligence cycle. A detection whose threshold, block size, land mask and
-- polarisation are not recorded cannot be reproduced or defended, so every run
-- writes its complete parameter set and every detection points at one.
CREATE TABLE IF NOT EXISTS detect.runs (
    id           bigserial PRIMARY KEY,
    scene_id     text        NOT NULL REFERENCES stac.scenes(id) ON DELETE CASCADE,
    detector     text        NOT NULL,
    version      text        NOT NULL,
    polarization text        NOT NULL,
    started_at   timestamptz NOT NULL,
    seconds      double precision,
    aoi_bbox     double precision[],
    coastline    text,
    parameters   jsonb       NOT NULL,
    statistics   jsonb       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS runs_scene_ix ON detect.runs (scene_id, started_at DESC);


-- ---------------------------------------------------------------------------
-- detect.detections — what our detector found
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS detect.detections (
    id            text PRIMARY KEY,
    run_id        bigint      NOT NULL REFERENCES detect.runs(id) ON DELETE CASCADE,
    scene_id      text        NOT NULL REFERENCES stac.scenes(id) ON DELETE CASCADE,
    geom          geometry(Point, 4326) NOT NULL,
    length_m      double precision,
    width_m       double precision,
    heading_deg   double precision,   -- hull axis, ambiguous by 180°; NULL when unresolvable
    confidence    double precision,

    -- Image coordinates and the beam geometry at this exact pixel. Carried so
    -- the dark query can undo the azimuth displacement without reopening the
    -- granule. `r_over_v_s` is slant range over platform speed, ~115 s here.
    px_row        double precision,
    px_col        double precision,
    incidence_deg double precision,
    r_over_v_s    double precision,
    cfar_margin_db double precision,

    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS detections_geom_gix ON detect.detections USING GIST (geom);
CREATE INDEX IF NOT EXISTS detections_scene_ix ON detect.detections (scene_id);

COMMENT ON TABLE detect.detections IS
  'Our own detector''s output — NOT a published detection layer. GFW SAR '
  'detections are a reference layer for cross-checking and are never loaded here.';


-- ---------------------------------------------------------------------------
-- ais.positions — derived, deduplicated, never authoritative
-- ---------------------------------------------------------------------------
-- The primary key IS the deduplication rule. 71% of raw DMA rows are exact
-- multi-station rebroadcast duplicates, worst case 21 identical copies of one
-- message (measured, NOTES.md), and duplicate-weighted nearest-in-time logic is
-- distorted by them. Making (mmsi, ts, lat, lon) the key means the database
-- enforces it rather than the loader remembering to.
--
-- lat/lon are stored as their own columns *as well as* geom precisely because
-- they are part of that key: a geometry is not usefully comparable for equality
-- and rounding it into one would let two "identical" messages differ.
CREATE TABLE IF NOT EXISTS ais.positions (
    mmsi        text        NOT NULL,
    ts          timestamptz NOT NULL,
    lat         double precision NOT NULL,
    lon         double precision NOT NULL,
    geom        geometry(Point, 4326) NOT NULL,
    sog_ms      double precision,
    cog_deg     double precision,
    heading_deg double precision,
    name        text,
    ship_type   text,
    length_m    double precision,
    width_m     double precision,
    nav_status  text,
    source      text        NOT NULL,

    -- Travels with the row, and downstream must gate rate language on it.
    -- aisstream measured at ~17% of DMA's vessels over an identical bbox and
    -- window; quoting a dark-vessel rate from that would report ~83% dark.
    is_ground_truth boolean NOT NULL DEFAULT false,

    PRIMARY KEY (mmsi, ts, lat, lon)
);

CREATE INDEX IF NOT EXISTS positions_geom_gix ON ais.positions USING GIST (geom);
CREATE INDEX IF NOT EXISTS positions_mmsi_ts_ix ON ais.positions (mmsi, ts);

-- The space–time index §M3 actually needs, and the reason btree_gist is in the
-- initdb extensions. A GiST index over (geom, ts) together lets one index serve
-- "near this point AND within this time window" — the dark query's whole shape.
-- Two separate indexes would force the planner to pick one and filter the rest.
CREATE INDEX IF NOT EXISTS positions_geom_ts_gix
    ON ais.positions USING GIST (geom, ts);

COMMENT ON TABLE ais.positions IS
  'Derived, not authoritative. The primary key (mmsi, ts, lat, lon) IS the '
  'deduplication rule from §3.2 — 71% of raw DMA rows are rebroadcast duplicates.';
