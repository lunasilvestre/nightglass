"""A real coastline, because a rock and a ship are the same object at 100 m.

The detector's own land mask is derived from the scene (`detect.land_mask`), and
for large land it works well. It cannot work for skerries, and the reason is
structural rather than a tuning failure.

That mask has to apply a morphological *opening* before it buffers the shore,
otherwise every bright vessel becomes its own little island and the mask deletes
exactly what the detector is looking for. Opening removes bright objects smaller
than the structuring element. A 100 m rock is a bright object smaller than the
structuring element. So the very step that stops ships masking themselves is the
step that lets skerries through, and no threshold moves that trade-off — at VH,
a wet rock and a hull are both compact, bright and small.

It showed up exactly as theory predicts. Over the Kattegat the detections drew a
neat line down the Swedish archipelago off Gothenburg, three to ten kilometres
offshore, with almost no AIS anywhere near them. Every one of those would have
been reported as a dark vessel, which is §3.2's "if your pipeline reports 40%
dark, it's broken" arriving on schedule.

So the fix is data the scene does not contain: **GSHHG**, Wessel & Smith's
shoreline, at full resolution — roughly 100 m, which does resolve skerries.

Two properties make this fit the architecture rather than fight it:

**It is fetched at provisioning time, never by the enclave.** Weights, documents,
granules and now the shoreline are the four things this system needs from the
outside world, and all four are acquired by an explicit, separate, online step on
the provision network. That the list is four items long rather than three is a
better answer to "what does an air-gapped deployment actually have to ship with"
than pretending it was three.

**The enclave gets the AOI, not the planet.** The global archive is 149 MB;
clipped to a configured AOI it is a few hundred kilobytes. Clipping happens
online, so the offline bundle carries only what its AOIs need.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from nightglass.config import BBox

GSHHG_URL = "https://www.soest.hawaii.edu/pwessel/gshhg/gshhg-shp-2.3.7.zip"
GSHHG_LICENCE = "GSHHG (Wessel & Smith), LGPL — https://www.soest.hawaii.edu/pwessel/gshhg/"

#: Resolutions, coarse to fine. `f` is ~100 m and is the only one that resolves a
#: skerry; the others exist so a low-detail AOI can carry less.
RESOLUTIONS = ("c", "l", "i", "h", "f")

#: L1 is the land/ocean boundary. L2 is lakes, L3 islands in lakes — irrelevant
#: to a maritime picture, and including them would mask inland water that no
#: vessel in this AOI is on anyway.
LEVEL = 1


class CoastlineError(RuntimeError):
    pass


def clipped_path(root: str | Path, aoi_name: str) -> Path:
    return Path(root) / f"coastline_{aoi_name.lower()}.geojson"


# -- provisioning (ONLINE) ---------------------------------------------------


def fetch_and_clip(
    aois: dict[str, BBox],
    out_root: str | Path,
    *,
    resolution: str = "f",
    margin_deg: float = 0.5,
    cache: str | Path | None = None,
    force: bool = False,
    keep_archive: bool = False,
) -> list[Path]:
    """Download GSHHG once, clip it per AOI, write one small GeoJSON each.

    ONLINE. Runs in the `fetcher` image on the provision network, like the corpus
    fetcher — running it inside the enclave fails at DNS resolution, which is the
    correct outcome.
    """
    import httpx

    if resolution not in RESOLUTIONS:
        raise CoastlineError(f"resolution must be one of {RESOLUTIONS}, got {resolution!r}")

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    archive = Path(cache or out_root) / "gshhg-shp-2.3.7.zip"

    if force or not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        print(f">> downloading {GSHHG_URL}", flush=True)
        tmp = archive.with_suffix(".part")
        with httpx.stream("GET", GSHHG_URL, follow_redirects=True, timeout=120.0) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done = 0
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r   {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
        print()
        tmp.rename(archive)
    else:
        print(f">> cached {archive} ({archive.stat().st_size / 1e6:.0f} MB)", flush=True)

    member = f"GSHHS_shp/{resolution}/GSHHS_{resolution}_L{LEVEL}"
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if f"{member}.shp" not in names:
            raise CoastlineError(f"{member}.shp not in the archive")
        extract_to = out_root / "_gshhg"
        extract_to.mkdir(parents=True, exist_ok=True)
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            if f"{member}{ext}" in names:
                (extract_to / f"GSHHS_{resolution}_L{LEVEL}{ext}").write_bytes(
                    z.read(f"{member}{ext}")
                )

    import geopandas as gpd

    shp = extract_to / f"GSHHS_{resolution}_L{LEVEL}.shp"
    written = []
    for name, bbox in aois.items():
        window = (
            bbox.min_lon - margin_deg,
            bbox.min_lat - margin_deg,
            bbox.max_lon + margin_deg,
            bbox.max_lat + margin_deg,
        )
        # bbox= pushes the filter down into the driver, so the 149 MB shapefile
        # is never fully materialised in memory.
        gdf = gpd.read_file(shp, bbox=window)
        if gdf.empty:
            print(f"   {name}: no land in window — writing an empty coastline", flush=True)
        gdf = gdf.clip(window) if not gdf.empty else gdf
        out = clipped_path(out_root, name)
        gdf.to_file(out, driver="GeoJSON")
        size = out.stat().st_size
        print(f"   {name}: {len(gdf)} polygons -> {out.name} ({size / 1024:.0f} KB)", flush=True)
        written.append(out)

    # The enclave mounts this directory. It should hold the clipped AOIs and
    # nothing else — a 149 MB archive and an unpacked global shapefile sitting
    # beside them would be 335 MB of provisioning residue inside the boundary,
    # and M6's deliverable is a bundle someone else can reproduce, not a bundle
    # that happens to work.
    if not keep_archive:
        for stale in extract_to.iterdir():
            stale.unlink()
        extract_to.rmdir()
        archive.unlink(missing_ok=True)
        print("   cleaned up the archive and the unpacked shapefile", flush=True)
    return written


# -- enclave (OFFLINE) -------------------------------------------------------


@dataclass
class Coastline:
    """Land polygons for one AOI, with a seaward buffer, ready to test points against."""

    geometry: object  # a shapely geometry (union of buffered land)
    polygons: int
    buffer_m: float
    source: str = "GSHHG f/L1"

    @classmethod
    def load(cls, path: str | Path, *, buffer_m: float = 300.0) -> Coastline:
        """Read the clipped GeoJSON and buffer it, in metres.

        Buffering happens in a projected CRS, not in degrees. At 57°N a degree of
        longitude is 60 km and a degree of latitude is 111 km, so a "0.003 degree"
        buffer would be 180 m across and 333 m along — an anisotropy that would
        quietly make the mask directional.
        """
        import geopandas as gpd

        path = Path(path)
        if not path.exists():
            raise CoastlineError(
                f"no coastline at {path}. Run `make fetch-coastline` — it is a "
                "provisioning-time download, like the corpus and the model weights."
            )
        gdf = gpd.read_file(path)
        if gdf.empty:
            from shapely.geometry import GeometryCollection

            return cls(GeometryCollection(), 0, buffer_m)
        utm = gdf.estimate_utm_crs()
        buffered = gdf.to_crs(utm).buffer(buffer_m).to_crs(4326)
        from shapely.ops import unary_union

        return cls(unary_union(buffered.values), len(gdf), buffer_m)

    def contains(self, lons, lats) -> list[bool]:
        """Which of these points fall on (buffered) land. Vectorised."""
        import numpy as np
        from shapely import STRtree
        from shapely.geometry import Point

        lons = np.atleast_1d(lons)
        if len(lons) == 0:
            return []
        pts = [Point(x, y) for x, y in zip(lons, np.atleast_1d(lats), strict=True)]
        if self.polygons == 0:
            return [False] * len(pts)
        tree = STRtree([self.geometry])
        hits = tree.query(pts, predicate="intersects")
        on_land = np.zeros(len(pts), dtype=bool)
        # `query` on a list of geometries returns (input_index, tree_index) pairs.
        if hits.size:
            on_land[np.atleast_2d(hits)[0]] = True
        return on_land.tolist()
