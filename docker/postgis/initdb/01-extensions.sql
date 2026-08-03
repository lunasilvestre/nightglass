-- NIGHTGLASS — PostGIS bootstrap.
--
-- Runs exactly once, on an empty data directory. The M3 schema (scenes,
-- detections, ais_positions) is deliberately NOT here: it belongs in a
-- migration that can be re-run and reviewed, not in an init hook that fires
-- once and then silently never again. This file establishes extensions only.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- Space-time correlation is the substance of the fusion problem (§5). btree_gist
-- lets a single index cover a geometry and a timestamp range together, which is
-- what the dark-vessel query in M3 actually needs.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Namespaces, so the scene catalog, the derived AIS tables and the detector
-- output stay visibly distinct. "Why is the AIS table derived?" is on the list
-- of questions expected in the hiring-manager round (§9) — the schema should
-- answer it without commentary.
CREATE SCHEMA IF NOT EXISTS stac;      -- scene catalog, one row per granule
CREATE SCHEMA IF NOT EXISTS detect;    -- our detector's output
CREATE SCHEMA IF NOT EXISTS ais;       -- ingested positions, derived + deduped

COMMENT ON SCHEMA ais IS
  'Derived, not authoritative. Rows are deduplicated on (mmsi, timestamp, lat, lon) '
  'at ingest — 71% of raw DMA rows measured as exact multi-station rebroadcast '
  'duplicates, worst case 21 identical copies of one message. See NOTES.md.';
