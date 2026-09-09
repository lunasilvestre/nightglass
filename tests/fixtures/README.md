# tests/fixtures

## `S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13.annotation.zip`

The annotation half of the Kattegat validation granule — the manifest and the
three VH XML members `SafeProduct` reads — so the tests parse a real Sentinel-1
product rather than XML someone wrote to make an assertion pass. 812 KB.

What is in it, member paths kept exactly as they are in the granule:

| member | why |
|---|---|
| `…SAFE/manifest.safe` | footprint (`gml:coordinates`), relative orbit |
| `…SAFE/annotation/s1d-iw-grd-vh-….xml` | acquisition times, orbit state vectors, platform heading, incidence, geolocation grid |
| `…SAFE/annotation/calibration/calibration-s1d-iw-grd-vh-….xml` | the `sigmaNought` LUT |
| `…SAFE/annotation/calibration/noise-s1d-iw-grd-vh-….xml` | the range and per-swath azimuth noise LUTs |
| `…SAFE/measurement/s1d-iw-grd-vh-….tiff` | **zero bytes** — a stand-in, see below |

The measurement member is present at its real path with an empty body.
`SafeProduct.polarizations` discovers polarizations from the `measurement/`
member names (`spatial/safe.py`), so without an entry there the reader finds no
polarization at all and every accessor fails. The 886 MB of pixels are not
redistributed here and no test opens that member: pixel-level detection is out
of scope for the unit suite, and `docs/testing.md` says why. The VV members are
left out entirely, which is what makes `polarizations() == ("VH",)` a real
assertion about member discovery rather than a restatement of the granule.

### How it was built

From the granule `make fetch-granules` puts on disk, in the repo root:

```
.venv/bin/python - <<'PY'
import zipfile
src = "data/raw/sar/S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13.zip"
scene = "S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13"
dst = f"tests/fixtures/{scene}.annotation.zip"
keep = lambda n: n.endswith("manifest.safe") or (
    "/annotation/" in n and "-vh-" in n and n.endswith(".xml") and "/rfi/" not in n)
with zipfile.ZipFile(src) as z, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
    for n in [n for n in z.namelist() if keep(n)]:
        out.writestr(n, z.read(n))
    stub = next(n for n in z.namelist() if "/measurement/" in n and "-vh-" in n)
    out.writestr(stub, b"")
PY
```

### Attribution and licence

Contains modified Copernicus Sentinel data 2026, processed by ESA. Staged via
the Alaska Satellite Facility (ASF DAAC). Copernicus Sentinel data are free,
full and open, and may be redistributed with attribution; the same line is in
the README and travels on every `Scene` this system returns
(`SENTINEL_LICENCE`, `src/nightglass/tools/base.py`).
