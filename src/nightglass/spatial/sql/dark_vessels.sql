-- §M3's "done when": one SQL query returning detections with no AIS
-- correspondence inside a space–time window.
--
-- Kept as readable SQL rather than assembled in Python, because §M3 says so and
-- the reason is good: "worth getting this right as plain SQL, hand-checked,
-- before the agent touches it — debugging a spatial join through an LLM is
-- miserable." Every CTE below can be run on its own and eyeballed.
--
-- Parameters: %(scene_id)s  %(radius_m)s  %(window_s)s  %(correct_azimuth)s
--
-- The query is four steps. The middle two are the substance of the fusion
-- problem (§5) and the reason this is not a point-in-polygon:
--
--   scene      the acquisition instant and the beam geometry
--   bracket    each vessel's two AIS reports either side of that instant
--   truth      its interpolated position AT the instant  ← fixes the time offset
--   apparent   where SAR would have DRAWN it             ← fixes the geometry offset
--   nearest    each detection's closest apparent position within the radius
--
-- Two offsets, kept apart deliberately:
--
-- 1. TIME. AIS reports at some instant, the image is taken at another. A vessel
--    at 12 kn covers 3.7 km in the ±5.5 min window, so "nearest report in time"
--    is not a position — it is a position up to kilometres stale. After dedup
--    the DMA feed gives ~43 distinct reports per vessel across 22 minutes, so
--    this is interpolation between bracketing fixes, not extrapolation.
--
-- 2. GEOMETRY. SAR places a target in azimuth by its Doppler, and a target with
--    line-of-sight velocity carries a Doppler offset indistinguishable from
--    being elsewhere along-track. It is drawn (R/V)·v_los from the truth. Here
--    R/V ≈ 115 s, so a ship making 12 kn across the range direction is drawn
--    ~450 m from where it was — most of the match radius, spent before the
--    matcher does anything.
--
--    The naive fix is a wider radius. That is worse than it looks: the radius is
--    the whole boundary between "matched" and "dark", so widening it to absorb a
--    systematic and *predictable* offset buys false matches at the same rate it
--    avoids false darks. The offset is computable from the product's own
--    annotation and from AIS SOG/COG, so it is computed.
--
--    Direction: derived one way, measured the other. The sign below is the one
--    `nightglass-spatial validate-shift` confirms against DMA ground truth —
--    a ship OPENING the range is drawn BACKWARD along the flight path. Pass
--    correct_azimuth = false to turn the correction off and see the difference.

WITH scene AS (
    SELECT
        s.id,
        s.acquisition_time,
        s.range_bearing_deg,
        s.azimuth_bearing_deg
    FROM stac.scenes s
    WHERE s.id = %(scene_id)s
),

-- Every AIS report inside the time window, tagged with the report immediately
-- before and after the acquisition instant. LEAD/LAG over the per-vessel track
-- is what makes the interpolation expressible in SQL at all.
bracket AS (
    SELECT
        p.mmsi,
        p.ts,
        p.lat,
        p.lon,
        p.sog_ms,
        p.cog_deg,
        p.name,
        p.ship_type,
        p.length_m,
        p.is_ground_truth,
        sc.acquisition_time,
        sc.range_bearing_deg,
        sc.azimuth_bearing_deg,
        EXTRACT(EPOCH FROM (p.ts - sc.acquisition_time))          AS dt,
        LEAD(p.lat)     OVER w AS next_lat,
        LEAD(p.lon)     OVER w AS next_lon,
        LEAD(p.sog_ms)  OVER w AS next_sog,
        LEAD(p.cog_deg) OVER w AS next_cog,
        EXTRACT(EPOCH FROM (LEAD(p.ts) OVER w - sc.acquisition_time)) AS next_dt
    FROM ais.positions p
    CROSS JOIN scene sc
    WHERE p.ts BETWEEN sc.acquisition_time - make_interval(secs => %(window_s)s)
                   AND sc.acquisition_time + make_interval(secs => %(window_s)s)
    WINDOW w AS (PARTITION BY p.mmsi ORDER BY p.ts)
),

-- The one row per vessel whose bracket straddles the acquisition instant,
-- linearly interpolated onto it. A vessel with no report on both sides is
-- dropped rather than extrapolated: an extrapolated position that then fails to
-- match would be manufacturing a dark detection out of a gap in the feed, which
-- is precisely §7's "innocent explanations" failure.
truth AS (
    SELECT
        b.mmsi,
        b.name,
        b.ship_type,
        b.length_m,
        b.is_ground_truth,
        b.acquisition_time,
        b.range_bearing_deg,
        b.azimuth_bearing_deg,
        -- Fraction of the way from this report to the next, at dt = 0.
        (0 - b.dt) / NULLIF(b.next_dt - b.dt, 0)                        AS f,
        b.lat + ((0 - b.dt) / NULLIF(b.next_dt - b.dt, 0)) * (b.next_lat - b.lat) AS lat,
        b.lon + ((0 - b.dt) / NULLIF(b.next_dt - b.dt, 0)) * (b.next_lon - b.lon) AS lon,
        COALESCE(b.sog_ms, b.next_sog, 0)                               AS sog_ms,
        COALESCE(b.cog_deg, b.next_cog)                                 AS cog_deg,
        LEAST(ABS(b.dt), ABS(b.next_dt))                                AS report_gap_s
    FROM bracket b
    WHERE b.dt <= 0 AND b.next_dt >= 0
),

-- Where the SAR image would have DRAWN each of those vessels.
--
--   v_ground_range = SOG · cos(COG − range_bearing)     ship velocity across the swath
--   v_los          = v_ground_range · sin(incidence)    projected onto the slant line of sight
--   shift          = −(R/V) · v_los                     metres along the flight direction
--
-- Incidence and R/V come from the DETECTION's own pixel, not from a scene-wide
-- average — both vary by roughly a fifth across a 250 km swath, and using a mid-swath value
-- would leave a systematic residual that grows toward the edges. That is why
-- this CTE is joined per detection rather than computed once per vessel.
apparent AS (
    SELECT
        d.id   AS detection_id,
        d.geom AS detection_geom,
        d.length_m AS det_length_m,
        d.confidence,
        d.heading_deg,
        t.mmsi,
        t.name,
        t.ship_type,
        t.length_m AS ais_length_m,
        t.is_ground_truth,
        t.report_gap_s,
        t.sog_ms,
        -- A NULL course means "no correction", NOT "no candidate". An earlier
        -- version filtered `cog_deg IS NOT NULL` in the WHERE clause and thereby
        -- dropped 322 of 907 vessels — over a third of the fleet — from ever
        -- being matched, every one of which turned into a spurious dark
        -- detection. A vessel reports no course precisely when it is moored or
        -- drifting, which is exactly when its azimuth displacement is zero, so
        -- the right handling is a zero shift and a full chance to match.
        CASE WHEN %(correct_azimuth)s AND t.cog_deg IS NOT NULL
             THEN -d.r_over_v_s
                  * COALESCE(t.sog_ms, 0)
                  * cos(radians(t.cog_deg - t.range_bearing_deg))
                  * sin(radians(d.incidence_deg))
             ELSE 0.0
        END AS shift_m,
        t.lon,
        t.lat,
        t.azimuth_bearing_deg
    FROM detect.detections d
    CROSS JOIN truth t
    WHERE d.scene_id = %(scene_id)s
      -- Cheap pre-filter so the cross join never materialises: nothing more than
      -- radius + the largest possible shift away can match, and this one is
      -- index-assisted. The exact test is below, after the shift is applied.
      AND ST_DWithin(
              d.geom::geography,
              ST_SetSRID(ST_MakePoint(t.lon, t.lat), 4326)::geography,
              %(radius_m)s + 2000.0
          )
),

-- Move each vessel along the flight direction by its own shift, then measure.
-- ST_Project takes an azimuth in radians and a distance in metres on the
-- spheroid, so a negative shift walks backward along the same bearing — no
-- separate case for the sign.
scored AS (
    SELECT
        a.*,
        ST_Project(
            ST_SetSRID(ST_MakePoint(a.lon, a.lat), 4326)::geography,
            a.shift_m,
            radians(a.azimuth_bearing_deg)
        )::geometry AS apparent_geom
    FROM apparent a
),

nearest AS (
    SELECT DISTINCT ON (s.detection_id)
        s.detection_id,
        s.mmsi,
        s.name,
        s.ship_type,
        s.ais_length_m,
        s.is_ground_truth,
        s.report_gap_s,
        s.sog_ms,
        s.shift_m,
        ST_Distance(s.detection_geom::geography, s.apparent_geom::geography) AS distance_m,
        ST_Distance(
            s.detection_geom::geography,
            ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326)::geography
        ) AS distance_uncorrected_m
    FROM scored s
    WHERE ST_Distance(s.detection_geom::geography, s.apparent_geom::geography) <= %(radius_m)s
    ORDER BY s.detection_id,
             ST_Distance(s.detection_geom::geography, s.apparent_geom::geography)
)

-- LEFT JOIN, not an anti-join, and that is the point: §M3 asks for the
-- unmatched detections, but a bare list of them cannot be checked. Returning
-- every detection with its match or its NULL makes the ~5-in-100 base rate (§3.2)
-- computable from the same result set that produced the darks.
SELECT
    d.id                        AS detection_id,
    d.scene_id,
    ST_Y(d.geom)                AS lat,
    ST_X(d.geom)                AS lon,
    d.length_m,
    d.heading_deg,
    d.confidence,
    n.mmsi,
    n.name                      AS vessel_name,
    n.ship_type,
    n.ais_length_m,
    n.distance_m,
    n.distance_uncorrected_m,
    n.shift_m                   AS azimuth_shift_m,
    n.sog_ms,
    n.report_gap_s              AS time_delta_s,
    COALESCE(n.is_ground_truth, false) AS source_is_ground_truth,
    CASE WHEN n.mmsi IS NULL THEN 'dark' ELSE 'matched' END AS status
FROM detect.detections d
LEFT JOIN nearest n ON n.detection_id = d.id
WHERE d.scene_id = %(scene_id)s
ORDER BY status DESC, n.distance_m NULLS LAST, d.id;
