"""Bearings, distances, and the one piece of physics that makes the match work.

The design contract: *"`ais_match` should account for the offset between AIS report
time and image acquisition — a vessel moves in between. That's the substance of
the fusion problem, and collapsing it to a naive point-in-polygon throws away the
interesting part."* And the harder half of the same problem:

> **Azimuth displacement** — moving ships are shifted along-track by hundreds of
> metres. A symmetric match radius manufactures false darks. Make the tolerance
> asymmetric or velocity-correct from AIS SOG/COG.

There are two distinct offsets and they are worth keeping apart:

1. **Time.** AIS reports at some instant, the image is taken at another. Fixed by
   interpolating the track to the acquisition time — cheap, and the DMA feed
   gives ~43 distinct positions per vessel across a 22-minute window, so it is
   interpolation rather than extrapolation.

2. **Geometry.** A moving ship is not drawn where it is. SAR places a target in
   azimuth by its Doppler, and a target with line-of-sight velocity carries a
   Doppler offset indistinguishable from being somewhere else along-track. The
   ship is drawn displaced along the flight direction by ``(R/V)·v_los``.

For Sentinel-1 IW, ``R/V ≈ 105 s``. A ship making 10 knots straight across the
range direction has ``v_los ≈ 3.2 m/s``, so it is drawn **~340 m** from where it
was — most of a 500 m match radius, spent before the matcher has done anything.

The naive fix is to widen the radius. That is worse than it looks: the radius is
what separates "matched" from "dark", so inflating it to absorb a systematic,
*predictable* offset buys false matches at exactly the rate it avoids false
darks. The offset is computable from data already in the product (slant range and
incidence from the geolocation grid, platform heading and speed from the orbit
state vectors) and from AIS itself (SOG, COG). So compute it.

Direction of the shift, since the sign is the part that is easy to get wrong and
silently doubles the error instead of removing it. For a stationary target the
processor focuses at zero Doppler. A target with range rate ``v_los`` shows the
Doppler a stationary target would have at azimuth-time offset ``Δt = v_los·R/V²``,
so it lands ``Δx = V·Δt = (R/V)·v_los`` further along the flight direction. Range
rate is positive **away** from the sensor. Hence: *a ship opening the range is
drawn forward along the flight path; a ship closing it is drawn back.*

That is a derivation, not a measurement, so `nightglass-spatial validate-shift`
checks it against DMA ground truth over the Kattegat and reports whether the
correction actually reduces match distance — and whether the sign is right.
"""

from __future__ import annotations

import numpy as np

EARTH_RADIUS_M = 6_371_008.8  # IUGG mean radius
KNOTS_TO_MS = 0.514444


def haversine_m(
    lon1: np.ndarray, lat1: np.ndarray, lon2: np.ndarray, lat2: np.ndarray
) -> np.ndarray:
    """Great-circle distance in metres. Vectorised over any broadcastable shapes."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlam = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def bearing_deg(
    lon1: np.ndarray, lat1: np.ndarray, lon2: np.ndarray, lat2: np.ndarray
) -> np.ndarray:
    """Initial great-circle bearing from point 1 to point 2, compass degrees."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dlam = np.radians(np.asarray(lon2) - np.asarray(lon1))
    y = np.sin(dlam) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dlam)
    return np.degrees(np.arctan2(y, x)) % 360.0


def offset_m(
    lon: np.ndarray, lat: np.ndarray, bearing: np.ndarray, distance_m: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Move ``distance_m`` along ``bearing`` from (lon, lat). Signed distance is fine.

    Spherical rather than ellipsoidal on purpose: the displacements involved are
    at most a couple of kilometres, where the two disagree by well under a metre
    — far below the ~7 m geolocation floor and three orders below the match
    radius. Using pyproj's geodesic here would be more precise in a way nothing
    downstream could observe, at the cost of a per-point Python call.
    """
    d = np.asarray(distance_m) / EARTH_RADIUS_M
    theta = np.radians(bearing)
    p1 = np.radians(lat)
    lam1 = np.radians(lon)
    sin_p2 = np.sin(p1) * np.cos(d) + np.cos(p1) * np.sin(d) * np.cos(theta)
    p2 = np.arcsin(np.clip(sin_p2, -1.0, 1.0))
    lam2 = lam1 + np.arctan2(
        np.sin(theta) * np.sin(d) * np.cos(p1), np.cos(d) - np.sin(p1) * sin_p2
    )
    return (np.degrees(lam2) + 540.0) % 360.0 - 180.0, np.degrees(p2)


def line_of_sight_velocity_ms(
    sog_ms: np.ndarray,
    cog_deg: np.ndarray,
    incidence_deg: np.ndarray,
    range_bearing_deg: float,
) -> np.ndarray:
    """The component of a vessel's velocity along the radar line of sight.

    Positive means opening the range — moving away from the sensor.

    Two projections, in order: the ship's horizontal velocity onto the ground
    range direction, then the ground range direction onto the slant line of
    sight, which is the ``sin(incidence)`` factor. Forgetting the second one
    overstates the displacement by 1/sin(38°) ≈ 1.6×.
    """
    ground_range = np.asarray(sog_ms) * np.cos(np.radians(np.asarray(cog_deg) - range_bearing_deg))
    return ground_range * np.sin(np.radians(incidence_deg))


def azimuth_displacement_m(
    sog_ms: np.ndarray,
    cog_deg: np.ndarray,
    incidence_deg: np.ndarray,
    r_over_v_s: np.ndarray,
    range_bearing_deg: float,
    sign: int = 1,
) -> np.ndarray:
    """How far along the flight direction a moving vessel is drawn from its truth.

    Positive is along the azimuth bearing (the platform heading). ``sign`` exists
    so the derived convention can be flipped and *measured* rather than argued
    about; `validate-shift` does exactly that against DMA ground truth.
    """
    v_los = line_of_sight_velocity_ms(sog_ms, cog_deg, incidence_deg, range_bearing_deg)
    return sign * np.asarray(r_over_v_s) * v_los


def apparent_position(
    lon: np.ndarray,
    lat: np.ndarray,
    sog_ms: np.ndarray,
    cog_deg: np.ndarray,
    incidence_deg: np.ndarray,
    r_over_v_s: np.ndarray,
    range_bearing_deg: float,
    azimuth_bearing_deg: float,
    sign: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Where a vessel of known position and velocity will be *drawn* in the image.

    Forward-modelling AIS into image space, rather than back-projecting each
    detection into the world, because the direction with the information in it is
    this one: AIS knows the vessel's velocity and a detection does not. A single
    bright blob carries no usable radial-velocity estimate, so undoing the shift
    from the detection side would mean guessing the very quantity that sets its
    size.
    """
    shift = azimuth_displacement_m(
        sog_ms, cog_deg, incidence_deg, r_over_v_s, range_bearing_deg, sign
    )
    return offset_m(lon, lat, np.full_like(np.asarray(shift, dtype=float), azimuth_bearing_deg), shift)
