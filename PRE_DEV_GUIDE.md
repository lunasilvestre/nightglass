# NIGHTGLASS — Pre-Dev Guide

Get the environment and real data ready before Claude Code starts building.

> **Corrections applied 2026-08-03** after executing this guide against live services.
> Marked **[VERIFIED]** (checked by running it) or **[CORRECTED]** (the guide was wrong).
> Measurements and decision log in `NOTES.md`.
>
> The three that would have cost the most time:
> 1. **`web.ais.dk` is dead** — §5's DMA URL no longer works. Replacement endpoint below.
> 2. **ASF downloads need a browser-accepted EULA** on top of valid credentials, or every download 401s.
> 3. **DMA timestamps are UTC** — settled empirically, so §5's DST test is done.
>
> ⚠️ **One open risk against the recorder plan:** measured aisstream throughput is ~1.4
> messages per vessel per 4 min, against ~40 expected from Class A's 2–10 s reporting
> interval — roughly **3% of expected volume**. See the box in Step 3b before committing to
> Friday.

---

## The plan, in one page

**No open historical AIS exists for Portuguese waters.** Not a paperwork problem — a licensing structure. Global Fishing Watch's ORBCOMM sublicence forbids redistributing AIS "or any portion or derivative thereof," so GFW publishes gridded effort and event summaries, never raw positions, at any tier. DGRM runs the national VTS and is the SafeSeaNet authority but publishes nothing. EMSA restricts SafeSeaNet to "EU and national administrations only." Satellite AIS free tiers vanished in the 2025 Kpler/S&P consolidation.

**So you collect it yourself.** With an aisstream.io key you get live point-level position reports over a bounding box — free, legally clean, no favours owed. The catch is no archive: you record forward, then pair with a Sentinel-1 acquisition that falls inside your recording window.

That constraint sets the whole schedule:

| | **Portugal — the mission** | **Denmark — the proving ground** |
|---|---|---|
| SAR | Sentinel-1 GRD, own detector | Sentinel-1 GRD, own detector |
| AIS | **self-collected via aisstream** | historical DMA point-level |
| Available | **Fri 7 Aug** (next overpass) | **today** |
| Role | the demo, real independent fusion | validates the matcher while you wait |

**The narrative this buys is better than any dataset.** "There's no open AIS for Portuguese waters, so I stood up my own collection" is a forward-deployed story in itself — the data you needed didn't exist in convenient form, so you built the pipeline. That's the job description.

GFW SAR detections remain useful as an **independent cross-check** on your detector, but you no longer depend on them for the AIS side.

---

## Your machine

Checked, and it's strong:

| | |
|---|---|
| GPU | **RTX 3090, 24 GB VRAM** |
| CPU / RAM | 32 cores, 62 GB |
| Disk free | 606 GB |
| Docker | 29.7.1, Compose **v5.3.1** |
| GPU passthrough | `nvidia-ctk` **present** |
| GDAL | 3.10.3 |
| Python | 3.13.5 — rasterio 1.5.0, pandas 3.0.3, geopandas 1.1.3, shapely 2.1.2 all installed |

**Missing: only `ollama`** (and `go`, if you do the M7 bundler).

### Model sizing

With 24 GB, run **14B**, not the 7B in the original spec:

| Model | VRAM (q4_K_M) |
|---|---|
| `qwen2.5:14b-instruct-q4_K_M` | ~9 GB |
| `bge-m3` (embeddings) | ~1.2 GB |
| **Total** | **~10 GB**, leaving ~13 GB for KV cache and context |

> **[CORRECTED 2026-08-03 — measured with both models resident]** The table counts *weights
> only*. Real footprint is larger, because KV cache is allocated on load:
>
> | | table says | `ollama ps` actual |
> |---|---|---|
> | qwen2.5:14b @ 32k ctx | ~9 GB | **15 GB** (weights + KV cache) |
> | bge-m3 @ 8k ctx | ~1.2 GB | **0.66 GB** |
> | **total** | ~10 GB | **~15.7 GB** |
>
> `nvidia-smi` with both loaded: **17.5 GB used, 6.5 GB free** of 24 GB — not the ~13 GB
> headroom the table implies. Still comfortable, but budget ~6 GB spare, not 13. If context
> is pushed past 32k the KV cache grows and that margin shrinks fast.
>
> The conclusion "32B at q4 would crowd out the embedding model" is **right**, and by a wider
> margin than the original arithmetic suggested.

14B is materially better at tool-calling and drafting structured reports. 32B at q4 (~20 GB) would crowd out the embedding model.

> **[VERIFIED 2026-08-03] Both models must be pinned resident, or RAG thrashes.**
> `OLLAMA_KEEP_ALIVE` defaults to **`5m`** (confirmed against ollama 0.32.5), so alternating
> embed and chat calls will evict and reload a 15 GB model mid-session — surfacing as
> unexplained latency, never as an error. Fix via `sudo systemctl edit ollama`:
>
> ```
> [Service]
> Environment="OLLAMA_KEEP_ALIVE=-1"
> Environment="OLLAMA_MAX_LOADED_MODELS=2"
> ```
>
> **Two systemd traps:** the `[Service]` header is mandatory (without it systemd errors
> `Assignment outside of section`), and systemd does **not** support trailing inline
> comments — only whole lines beginning with `#`. Verify with
> `systemctl show ollama -p Environment`, then confirm `ollama ps` reports `UNTIL: Forever`
> for both models.

---

## Step 1 — Install (5 min)

```bash
curl -fsSL https://ollama.com/install.sh | sh

ollama pull qwen2.5:14b-instruct-q4_K_M
ollama pull bge-m3

ollama list && nvidia-smi
```

Note `q4_K_M`, not `q4` — plain `q4` isn't a valid tag and the pull 404s.

Optional, only for the M7 bundler: `sudo apt install golang-go`

---

## Step 2 — The AOI and the overpass calendar (already verified)

I queried ASF directly. Real acquisitions over both candidate AOIs, 24 Jul – 3 Aug 2026:

**Lisbon approaches** (-9.4, 38.7) — 2 paths:

| When (UTC) | Path | Direction | Sat |
|---|---|---|---|
| 26 Jul 06:42 | 125 | descending | S1D |
| 26 Jul 18:34 | 45 | ascending | S1C |
| 1 Aug 06:42 | 125 | descending | S1C |
| 1 Aug 18:34 | 45 | ascending | S1D |

**Leixões / Porto approaches** (-8.85, 41.2) — **3 paths**:

| When (UTC) | Path | Direction | Sat |
|---|---|---|---|
| 26 Jul 06:41 | 125 | descending | S1D |
| 26 Jul 18:35 | 45 | ascending | S1C |
| 27 Jul 18:27 | 147 | ascending | S1D |
| 1 Aug 06:41 | 125 | descending | S1C |
| 1 Aug 18:35 | 45 | ascending | S1D |
| 2 Aug 18:26 | 147 | ascending | S1C |

**Use Leixões.** Three overlapping paths instead of two means 50% more acquisitions, and it aligns with your APDL contact if the domain conversation goes anywhere.

### The next windows — put these in your calendar

Clean 6-day repeat, so:

- **Fri 7 Aug ~06:41 UTC** — path 125, descending
- **Fri 7 Aug ~18:35 UTC** — path 45, ascending
- **Sat 8 Aug ~18:27 UTC** — path 147, ascending (Leixões only)

**There is no acquisition between now and Wednesday's interview.** That's fine — NIGHTGLASS isn't for Wednesday. But it means the recorder has to be running and *proven stable* well before Friday morning, and that's the one thing with a hard deadline attached.

Scene size from ASF: **780–940 MB** for dual-pol IW GRDH (smaller than CDSE's original SAFE, which runs 1.7–2.1 GB).

### Recording bounding box

Cover both Friday footprints with margin. Path 125 descending spans roughly lon −7.3 to −10.8, lat 40.8–42.8; path 45 ascending spans lon −8.2 to −11.6, lat 39.6–41.5.

> **[DECIDED 2026-08-03] Record the union of both Portuguese AOIs: lat 38.0–42.5,
> lon −11.5 to −8.0.** This spans Leixões *and* Lisbon/Tagus, so the AOI choice stays open
> until Friday's scenes are actually on disk rather than being locked now. Costs a larger
> footprint and some offshore dead space; buys the ability to change your mind after seeing
> real data. Mirrored in `.env` as `NIGHTGLASS_RECORD_BBOX_LAT/LON`.

**Record: lat 38.0–42.5, lon −11.5 to −8.0.** Wider than either AOI on purpose — you can always filter down, never back-fill.

Expect terrestrial AIS coverage to fade 40–60 nm offshore. The Leixões approaches and the north–south coastal lane are well inside that; the western edge of the footprint will be empty. Worth knowing, and worth saying — thin coverage from shore-based receivers is exactly the argument for space-based AIS and for a sovereign tasking capability.

### Constellation note

**Sentinel-1A ended operations 29 June 2026.** S1C and S1D are the operational pair and their repeat is shifted a day from the old A/B cycle. Any pre-2026 revisit calendar is wrong — the table above is queried, not inferred.

---

## Step 3 — Sentinel-1 scenes (no auth to search)

```bash
# Full granule list with download URLs
curl -s -G "https://api.daac.asf.alaska.edu/services/search/param" \
  --data-urlencode "intersectsWith=polygon((-10.5 38.0,-8.5 38.0,-8.5 39.5,-10.5 39.5,-10.5 38.0))" \
  --data-urlencode "start=2026-06-01" --data-urlencode "end=2026-06-15" \
  --data-urlencode "processingLevel=GRD_HD" \
  --data-urlencode "output=jsonlite" --data-urlencode "maxResults=20" | python3 -m json.tool
```

From each record you want **`startTime`**, **`downloadUrl`**, and the footprint WKT.

Download needs a free NASA Earthdata account (instant, <https://urs.earthdata.nasa.gov/>):

```bash
# ~/.netrc  (chmod 600)
machine urs.earthdata.nasa.gov login YOUR_USER password YOUR_PASS

wget --no-check-certificate --auth-no-challenge -c "<downloadUrl>"
```

> **[CORRECTED] A valid Earthdata account is NOT sufficient.** Downloads 401 with
> `"Could Not Login. Be sure to agree to the EULA."` even when the credentials are perfectly
> good. Diagnostic: if a bearer token returns **200** against `cmr.earthdata.nasa.gov` but the
> datapool still 401s, it is the EULA — not the credentials, and not token expiry.
>
> Fix, once, in a browser: <https://urs.earthdata.nasa.gov/profile> → Applications →
> Authorized Apps → authorize **Alaska Satellite Facility Data Access** and accept the
> Sentinel EULA. **This gates every scene on both AOIs** — do it before anything else.
>
> **[CORRECTED] Two netrc traps.**
> 1. curl needs `--location-trusted` to carry credentials across the redirect to
>    `sentinel1.asf.alaska.edu`; plain `-L` drops them and you get a confusing 401.
> 2. A `~/.netrc` carrying a trailing `token <JWT>` line is tolerated by `curl -n` but
>    **crashes Python's stdlib netrc module** with
>    `NetrcParseError: bad follower token 'token'`. Any Python download helper calling
>    `netrc.netrc()` dies on it. Drop the line or read credentials another way.

> **[CORRECTED]** **~640–990 MB** per dual-pol IW GRDH scene, not 1.7–2.1 GB. Measured across
> 153 granules over both AOIs: Portuguese June scenes ran 640–850 MB, Danish July scenes
> 780–960 MB. (Step 2's "780–940 MB" is the right order of magnitude but slightly narrow —
> the Portuguese scenes go lower.) As Step 2 notes, the 1.7–2.1 GB figure describes CDSE's
> original SAFE, not what ASF serves.

**Why ASF over CDSE:** ASF serves classic `.zip` SAFE. CDSE defaults Sentinel-1 GRD to `_COG.SAFE`, and original SAFE older than one year is now "Deferred Available Data" requiring an order step with a 0.1 TB/month quota. ASF sidesteps all of it.

---

## Step 3b — Start the AIS recorder TODAY

**This is the only task with a real deadline.** It must be running and proven stable before Friday 07:00 UTC. Every hour not recording is data you cannot recover.

Endpoint: `wss://stream.aisstream.io/v0/stream`. Subscribe with your key and the bounding box:

```json
{
  "APIKey": "...",
  "BoundingBoxes": [[[40.0, -11.5], [42.5, -8.0]]],
  "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
}
```

Capture **both** message types. `PositionReport` gives `UserID` (MMSI), `Latitude`, `Longitude`, plus SOG/COG/heading and `MetaData.time_utc`. `ShipStaticData` gives name, callsign, IMO, ship type and dimensions — you need those to report vessel length in the INTREP, and they arrive far less often, so start collecting early.

**Robustness requirements — this runs unattended for four days:**

- Reconnect with exponential backoff on drop. aisstream is flagged BETA with no SLA; assume it will drop.
- Append-only, one file per UTC day, NDJSON or Parquet. Never rewrite.
- Record the raw message plus your own receive timestamp — don't trust a single clock.
- Run under `systemd --user` or tmux so it survives terminal close and logout.
- Log a heartbeat with message count every 5 minutes so you can prove coverage after the fact.

You already have the patterns for this from the Claude fleet work — idempotency, cursor files, incremental append. Same problem.

**Validate within the first hour:** count distinct MMSIs and plot positions. If you're seeing a few hundred vessels along the coastal lane, it's working. If it's near-empty, the bounding box format is likely wrong — aisstream takes `[[lat, lon], [lat, lon]]`, which is the opposite order from most GIS tooling and the most common mistake.

> ⚠️ **[MEASURED 2026-08-03] That check proves the recorder runs. It does NOT prove the feed
> is complete enough to call anything dark.** Those are different claims and only the first is
> testable on Friday morning.
>
> **Controlled head-to-head.** Same Kattegat bbox, same 22-minute clock window
> (15:29–15:51 UTC), one source against the other:
>
> | source | messages | unique vessels |
> |---|---|---|
> | **DMA** (17 Jul 2026) | 80,388 raw / ~23,300 deduped | **770** |
> | **aisstream** (3 Aug 2026, live) | **609** | **132** |
>
> **aisstream sees ~17% of the vessels — it misses roughly 5 in 6.**
>
> And this is a **ceiling, not a warm-up**: vessel discovery saturated. Per-minute new-vessel
> counts ran 32, 21, 14, 13, 11, 4, 4, 4, 3, 3, 3, 0, 4, 3, 0, 3, 3, 3, 0, 3, 0, 1 — flat from
> about minute 10. Recording for four days will not fix it, because the vessels are not being
> received at all, not merely reported slowly.
>
> Caveats, stated honestly: different days (17 Jul vs 3 Aug), so traffic differs — but not by
> 6×. Same bbox, same hour. Denmark is also DMA's home receiver network, i.e. best case for
> the reference side.
>
> **What this means for Friday.** If ~83% of real vessels are absent from your feed, then
> feeding it to the matcher marks ~83% of detections dark. That is not the 40%-dark failure
> §7 warns about — it is worse.
>
> **The uncomfortable part:** completeness over Portugal **cannot be measured**, because the
> reason you need aisstream there — no DMA equivalent — is exactly the reason there is no
> ground truth to check it against. Denmark is the only place this gap is observable, which is
> precisely why the Danish AOI earns its place.
>
> **So report matched pairs, not a dark rate.** "Here are N detections I matched to
> self-collected AIS, with the space–time reasoning shown" is fully defensible and is the
> interesting engineering. "X% were dark" is not supportable from this source, and Denmark
> remains the AOI where a rate can honestly be quoted.
>
> Two things worth doing while recording: log a per-minute unique-MMSI count so completeness
> can be characterised after the fact, and capture `ShipStaticData` from the start — it
> arrives rarely and you need it for vessel length in the INTREP.

**On licensing:** check aisstream's terms before committing anything to a public repo. Safest default — commit the derived detections and match results, not the raw feed. That's better practice anyway and it's the answer you'd want to give a defence customer asking about data handling.

---

## Step 4 — Global Fishing Watch, as an independent cross-check

Free API token: <https://globalfishingwatch.org/our-apis/tokens>. Licence **CC BY-NC 4.0** — non-commercial, attribution required.

The relevant product is **`gfw_sar_vessel_detections()`** (R package `gfwr` 3.0) or the equivalent in the Python client. It's built on Paolo et al. 2024, *Nature* 625:85–91 — Sentinel-1 detections with AIS matching already performed.

```r
# Unmatched detections = dark vessels, Portuguese mainland EEZ
gfw_sar_vessel_detections(
  spatial_resolution  = "HIGH",        # 0.01° ≈ 1.1 km
  temporal_resolution = "HOURLY",
  group_by            = "MMSI",
  filter_by           = "matched='false'",
  region              = gfw_region_id(region = "Portug", region_source = "EEZ")
)
```

GFW ships Marine Regions v12 with three separate Portuguese EEZ polygons: mainland, **Azores (MRGID 8361)**, **Madeira (MRGID 8363)**. Matched detections additionally carry MMSI, IMO, callsign, flag, gear type and vessel name; unmatched carry entry/exit timestamps at second precision.

**Two caveats, both important:**

1. **Currently in outage.** GFW's SAR detection dataset has been down since 3 July 2026 following S1A's retirement and the 1C/1D pipeline migration — they flagged "a data gap of at least one month," with no resolution notice found. **Use a historical date, before July 2026.** The 2017–June 2026 archive should be intact.

2. **This is GFW's answer, not your computation.** You can use it as a reference and validation layer — "my detector found N, GFW found M, here's the overlap" — but you cannot claim to have independently correlated SAR against AIS over Portugal. Be explicit about that in the README. Claiming otherwise is the kind of thing that unravels badly in a technical round.

What you *can* legitimately claim over Portugal: your own detector, run on real Sentinel-1 scenes, cross-checked against an independent published detection layer. That's a real result.

---

## Step 5 — Denmark, where the matcher gets proven

This is where genuine independent SAR↔AIS correlation happens, because the AIS is real and point-level.

> **[CORRECTED]** `web.ais.dk` is **dead**. Its TLS cert (`CN=*.govcloud.dk`) expired
> **12 Jun 2025**; with `curl -k` the handshake completes and the server then hangs, and port
> 80 is silent. Per dma.dk, *"the Danish Land Based AIS-system is now under the jurisdiction
> of the Danish Emergency Management Agency"* — the service moved.
>
> The official page `http://aisdata.ais.dk/` is a JS-rendered S3 listing widget, so scraping
> its HTML returns **zero** filenames. Hit the bucket's S3 REST API instead — public,
> unauthenticated, paginates properly. Note **`http://`**; `https://` fails on this host too.

```bash
# [VERIFIED 2026-08-03] no auth of any kind
B=http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com

# list recent dailies
curl -s "$B/?list-type=2&delimiter=/&max-keys=1000" \
  | grep -oE '<Key>[^<]+</Key>' | sed 's|</\?Key>||g'

curl -O "$B/aisdk-2026-07-17.zip"
unzip aisdk-2026-07-17.zip
```

**Bucket layout** — dailies live at bucket **root** on a rolling ~17-month window (verified
`aisdk-2025-02-27` → `aisdk-2026-07-31`). The `YYYY/` prefixes (2006–2025) hold older
**monthly** archives, e.g. `2006/aisdk-2006-03.zip`. So the guide's `aisdk-YYYY-MM.zip`
extrapolation was half right — monthly exists, but only for older years.

> **[CORRECTED]** Size is **~850–990 MB zipped → 3.1–5.5 GB**, not the 300–500 MB → 3–5 GB
> stated. Measured: 848 MB (07-17, 5.45 GB unzipped), 956 MB (07-15), 988 MB (07-16).
> Stream-filter; don't load a day whole.

**AOI:** Kattegat / the Belts, roughly lat 55.5–57.5, lon 10.5–12.5. Dense traffic, reliable S1 coverage, squarely inside DMA's receiver network.

> **[CORRECTED]** Publish lag is **~3 days, not 48 h**. `aisdk-2026-07-15.zip` carries
> `Last-Modified: Sat, 18 Jul 2026`; on 3 Aug the newest available was 31 Jul.

**Overpass windows over Denmark:** descending ≈ 05:23–05:40 UTC, ascending ≈ **16:44**–17:09 UTC. Slice the AIS day to those, then to ±5–10 min of the granule `startTime`.

> **[CORRECTED]** The ascending window started at 16:52 in the original. Measured over 72
> July 2026 granules, ascending passes cluster at 16:44 (×10), 16:52 (×12), 17:00, 17:01,
> 17:08, 17:09 — so a 16:52 lower bound **silently drops about a third** of them. Descending
> was near-correct (05:23–05:40 measured vs 05:24–05:41 stated).
>
> **[NEW]** Portugal overpass windows, absent from the original — measured over 81 June 2026
> granules on the Lisbon box: **descending 06:33–06:51 UTC, ascending 18:26–18:43 UTC.**

**Licence** — Danish PSI act, open re-use including commercial. Required attribution:

> "Contains data from the Danish Maritime Authority that is used in accordance with the conditions for the use of Danish public data."

### Schema — 26 columns, three traps

```
# Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,ROT,SOG,COG,
Heading,IMO,Callsign,Name,Ship type,Cargo type,Width,Length,
Type of position fixing device,Draught,Destination,ETA,Data source type,A,B,C,D
```

1. **The first column is literally `# Timestamp`** — leading hash and space. Naive readers treat the header as a comment and mangle everything.
2. Timestamp is `%d/%m/%Y %H:%M:%S` — **day first**, not ISO.
3. Filter non-vessels: `Type of mobile = Base Station` and AtoN, `MMSI = 0`, SOG sentinels 1022/1023, and `'Unknown'`/`'Undefined'` placeholders.

> **[VERIFIED]** All three confirmed against the real file. Header keeps `# Timestamp` intact,
> 26 comma-separated fields, dates day-first. SOG sentinels were absent from the sampled
> window (0 rows ≥ 102.2), but keep the filter.
>
> **[NOT a trap]** The bucket README writes coordinates with a **decimal comma** (`57,8794`),
> which suggests the file cannot be comma-delimited. It can — that is Danish prose, not the
> format. Real rows use decimal points: `17/07/2026 05:13:00,Class B,257843840,56.489370,10.951233,...`

### The timezone check — ✅ RESOLVED: it is UTC

> **[VERIFIED]** DMA documents this nowhere — their own bucket README gives the schema and the
> day-first format but states no timezone. Settled empirically with the DST histogram below on
> `aisdk-2026-03-29.csv` (last Sunday of March 2026; Europe/Copenhagen springs forward
> 02:00→03:00 local):
>
> ```
> 00:00  709855    01:00  711596    02:00  712161    03:00  696666
> ```
>
> Smooth across the transition, **no one-hour hole at `02:xx`** → **timestamps are UTC.**
> Parse as UTC and slice acquisition windows directly. No conversion needed.

The test, for reference, or to re-run on a future file:

Pull the last Sunday in March and histogram messages per minute across 02:00–03:00:

- Smooth → **UTC**
- One-hour hole → **local time**

---

## Step 6 — Opening the GRD without SNAP

```python
import rasterio
with rasterio.open("measurement/s1a-iw-grd-vv-...-001.tiff") as src:
    dn = src.read(1)     # uint16 digital numbers
```

**Two things that trip everyone:**

**1. The TIFF has no CRS and no geotransform.** GRD is ground-range-projected but not map-projected. Georeferencing lives in `annotation/*.xml` as a geolocation grid (`<geolocationGridPointList>` — lat/lon/line/pixel tie points). Build GCPs from it. Assume north-up and every position is silently wrong, nothing matches AIS, and it looks like a broken matcher rather than a projection bug.

**2. DN is not backscatter.** Calibrate with the sigma-nought LUT in `annotation/calibration/calibration-*.xml`:

```
sigma0 = DN² / A²          # A = sigmaNought interpolated to (line, pixel)
dB     = 10 * log10(sigma0)
```

Subtract the thermal noise LUT (`noise-*.xml`) first over water — it matters when you're thresholding against a dark background.

**Use VH for detection.** Cross-pol has much lower background over water, so ship-to-sea contrast is better. VV characterises sea state.

---

## Step 7 — Two physics gotchas to design around now

**Azimuth displacement.** A moving ship is smeared and shifted **along-track** in SAR, by hundreds of metres proportional to its range-direction velocity. A symmetric match radius will mismatch fast movers and manufacture false darks. Make the tolerance asymmetric along azimuth, or velocity-correct using AIS SOG/COG before matching.

**AIS hygiene manufactures phantom darks.** Duplicated MMSIs, stale retransmissions, multi-station rebroadcast delays. Take last-known-position per MMSI in the window and interpolate to the acquisition instant rather than nearest-in-time raw.

> **[VERIFIED — and it is worse than "some duplicates"]** Measured on the 22-minute Kattegat
> acquisition window of 17 Jul 2026:
>
> | | |
> |---|---|
> | raw rows | 134,851 |
> | after exact-duplicate removal | 38,945 — **−71%** |
> | worst rebroadcast multiplicity | **21 identical copies** of one message |
> | after dedup | 42.8 distinct timestamps per vessel, max 544 |
>
> **Dedup on `(MMSI, timestamp, lat, lon)` before matching.** Otherwise duplicate-weighted
> nearest-in-time logic is skewed by whichever vessel happened to be heard by the most
> receivers. 42.8 positions per vessel across 22 min is ample to interpolate each track to the
> acquisition instant, which is what this section asks for.

### Base rates for sanity-checking

Danish waters, 2020–2025 (Nielsen, SSRN Feb 2026): 45,441 detections → 43,175 AIS-matched → 2,266 unmatched → **185 confirmed dark**. Roughly **5% unmatched, 0.4% genuinely dark** after review. GFW's Brazil example: ~21% unmatched.

If your pipeline reports 40% dark, it's broken. This number is the best debugging signal you have.

---

## Step 8 — Verification checklist

```
TODAY — hard deadline, must be stable before Fri 07:00 UTC
[ ] aisstream recorder running, lat 40.0-42.5 / lon -11.5 to -8.0
[ ] validated: few hundred distinct MMSIs in the first hour
[ ] running under systemd/tmux, survives logout
[ ] reconnect-on-drop tested (kill the network, watch it recover)

ENVIRONMENT
[ ] ollama list → qwen2.5:14b-instruct-q4_K_M and bge-m3 present
[ ] nvidia-smi  → 3090 visible, ~24 GB free
[ ] docker compose version → v5.3.1
[ ] Earthdata account, ~/.netrc written, chmod 600

DENMARK — available now, unblocks matcher development
[ ] One S1 GRD scene over Kattegat downloaded
[ ] DMA zip for the same date downloaded and unzipped
[ ] AIS CSV parses with '# Timestamp' intact
[ ] Timezone DST test run — UTC confirmed or corrected
[ ] AIS filtered to acquisition ±10 min → hundreds of positions, not zero

PORTUGAL — detector work now, fusion on Fri 7 Aug
[ ] 2 Aug Leixões scene downloaded (detector development, no AIS pairing)
[ ] GFW API token obtained, cross-check layer pulled
[ ] Fri 7 Aug 06:41 + 18:35 UTC in the calendar
[ ] After Friday: scene downloaded, paired with recorded AIS, matcher runs
```

The Danish line is the near-term gate — SAR pixels plus a few hundred AIS positions inside the footprint at acquisition time, and the matcher is unblocked. The Portuguese line on Friday is what makes it the mission demo.

---

## Prior art

- **Magalhães, Falcão & Barbosa (2025)** — "Vessel detection leveraging satellite imagery and YOLO in maritime surveillance," *Remote Sensing Applications: Society and Environment* 40:101730, <https://doi.org/10.1016/j.rsase.2025.101730>. **IST Lisbon.** YOLOv8 vs YOLOv10 on **Sentinel-2 optical**, with a medium-resolution-trained model transferred to high-resolution **GEOSAT** imagery. Motivated explicitly by AIS non-reporting and manipulation — the same problem NIGHTGLASS addresses, approached from the optical side. Free extended abstract (MSc thesis): <https://fenix.tecnico.ulisboa.pt/downloadFile/1970719973971750/95967extendedabstract.pdf>
  - **Cite this in the README as related work.** It's the local research landscape, it's recent, and it sets up the optical-vs-SAR contrast cleanly: their approach fails at night and under cloud, which is precisely ICEYE's value proposition. Positioning NIGHTGLASS as the SAR complement to active Portuguese optical work is a much better framing than presenting it in a vacuum.
- **SatShipAI** — Nasios & Vogklis, *Electronics* 2025, 14(18), 3648 ([open access](https://doi.org/10.3390/electronics14183648)). Six years of operational Sentinel-1 with **DMA AIS specifically**. Documents real failure modes: orbit-file delays, wind farms as false positives. Closest published work to the Danish half. Live at <https://satshipai.eu/live/>
- **Paolo et al. 2024**, *Nature* 625:85–91 — the paper behind GFW's SAR detections. Read this; it's the reference your Portuguese layer rests on.
- **OpenOceanWatch** — Heiselberg, DTU Space. <https://www.openoceanwatch.com/>, method paper <https://doi.org/10.3390/rs16244719>
- **CDSE official notebook** — `ship_detection_and_ais_identification.ipynb` in <https://github.com/eu-cdse/notebook-samples>
- **Global Fishing Watch matcher** — <https://github.com/GlobalFishingWatch/paper-industrial-activity> — the only open *probabilistic* SAR↔AIS matcher. Apache-2.0 but frozen mid-2024 and BigQuery-coupled. Port the algorithm, don't run the repo.
- **Detector with weights** — `allenai/vessel-detection-sentinels`, Apache-2.0, <https://arxiv.org/abs/2312.03207>

There is **no maintained production-quality open-source SAR↔AIS correlation library.** You will write the matcher — which is exactly why it's worth having built.

---

## The talking point this buys you

Expect "why did you use Danish data?" — and it's a gift of a question:

> "Denmark is the only European state that publishes free point-level AIS, so that's where I could validate the correlation engine honestly. The mission AOI is the Portuguese EEZ, where there's no open AIS at all — DGRM runs the national VTS and is the SafeSeaNet authority, and EMSA restricts SafeSeaNet to national administrations. So in a real deployment the customer brings the feed and I bring the system. That gap between where the open data is and where the mission is, is basically the forward-deployed job."

Two details that make it land harder: **EMSA's Copernicus Maritime Surveillance service does exactly this SAR-plus-AIS fusion, is restricted to national authorities, and is run out of Lisbon.** You're building the open-source shadow of a service that already operates from the city you'd be deployed in. And **DGRM's MONICAP VMS has a scientific-research access provision** — which is the legitimate route to real Portuguese positional data, and a sensible thing to say you'd pursue.

---

## Verified vs unverified

*Updated 2026-08-03 after executing the guide. Full measurements in `NOTES.md`.*

**Verified by running it:**

- **Sentinel-1 density over Portuguese waters** — was the "highest-priority unknown". **Retired.**
  ASF `GRD_HD` counts, 1–15 Jun 2026: Lisbon/Tagus **46**, deep Atlantic **34**, Porto **31**,
  Algarve **28**, Azores 5, Madeira 4. Coastal *and* open ocean are healthy. Full June over
  Lisbon: 81 granules, **100% dual-pol VV+VH**, so VH is available on every scene.
- **DMA timestamps are UTC** — DST histogram, no hole at the spring-forward.
- **DMA schema** — `# Timestamp` intact, 26 comma-separated fields, day-first dates,
  decimal *points* not commas.
- **S1A retirement 29 Jun 2026** — Portugal June 2026 has S1A ×31 / S1C ×18 / S1D ×32;
  Denmark July 2026 has S1C ×36 / S1D ×36 and **zero** S1A. Post-June is S1C/S1D only, so any
  regex hardcoding `S1A|S1B` matches nothing.
- **ASF search is unauthenticated**; download is not, and additionally needs the EULA.
- **DMA bucket layout** — dailies at root, rolling ~17 months (`2025-02-27` → `2026-07-31`);
  `YYYY/` prefixes hold older monthly archives.
- **DMA daily size** — 848–988 MB zipped (the ~300–500 MB figure was wrong), ~3-day publish lag.
- Hardware and tooling, unchanged.

**Corrected — the guide was wrong:**

- `web.ais.dk` is dead; the service moved to an S3 bucket (Step 5).
- Ascending Denmark overpass starts **16:44**, not 16:52.
- Scene size ~780–990 MB, not 1.7–2.1 GB.
- **"No free point-level AIS exists for Portuguese waters" was too strong** — aisstream.io
  serves free real-time point-level AIS covering Portuguese waters. The accurate claim is
  *no free **historical, complete** point-level AIS*. See the Step 3b box for why the
  completeness qualifier matters more than the historical one.

**Still unverified — check as you go:**

- **aisstream completeness over the recording bbox** — the open risk against the Friday plan.
  Measured ~3% of expected message volume; see Step 3b.
- Whether GFW's SAR outage has resolved — check before relying on recent dates.
- aisstream's licence terms for redistribution — commit derived detections, not the raw feed.
- Terrestrial AIS coverage falloff offshore on the Leixões footprint's western edge.
