# NIGHTGLASS — Notes

Decision log, things tried that failed, open questions. Per EXECUTION_SPEC §9.

---

## Pre-dev guide run — 2026-08-03

Executed `PRE_DEV_GUIDE.md` §1–§8. Results below, **including corrections to the guide.**
Everything marked VERIFIED was checked by running the command, not inferred.

### Environment — VERIFIED, matches guide

| | Guide said | Actual |
|---|---|---|
| GPU | RTX 3090 24 GB | ✅ RTX 3090, 24576 MiB (22321 MiB free) |
| Docker | 29.7.1 / Compose v5.3.1 | ✅ exact match |
| `nvidia-ctk` | present | ✅ `/usr/bin/nvidia-ctk` |
| GDAL | 3.10.3 | ✅ 3.10.3 |
| Python | 3.13.5 + geo stack | ✅ rasterio 1.5.0, pandas 3.0.3, geopandas 1.1.3, shapely 2.1.2, pyproj 3.7.1, requests 2.32.3 |
| Disk | 606 GB free | ✅ 606 GB |
| `ollama` | missing | ✅ **0.32.5 installed**, both models pinned resident (`UNTIL: Forever`) |
| `go` | missing | ❌ still missing (M7 only, ignore for now) |

**Ollama, verified 2026-08-03.** Real VRAM is higher than the guide's weights-only table:
`ollama ps` reports **15 GB** for qwen2.5:14b @ 32k ctx (weights *plus* KV cache) and 0.66 GB
for bge-m3, so `nvidia-smi` shows **17.5 GB used / 6.5 GB free** — not the ~13 GB headroom the
table implies. Pinned via a systemd drop-in (`OLLAMA_KEEP_ALIVE=-1`,
`OLLAMA_MAX_LOADED_MODELS=2`) because the default `5m` keep-alive would evict a 15 GB model
between RAG embed and chat calls, showing up as latency rather than an error.

Two systemd traps hit while doing it: the `[Service]` header is mandatory, and systemd does
**not** accept trailing inline comments — only whole lines starting with `#`.

**M4 tool-chaining already passes** (dry-run, stub tool results, §6's Portuguese query):
`stac_search → detect_vessels → ais_match →` Portuguese answer. Three distinct tools chained
unprompted; bbox parsed from prose; *"últimas 72 horas"* resolved against a stated today-date.
It even hedged unprompted — *"pode não ser declarada"*, which is §7's framing for free.

⚠️ **But build a loop-breaker into M5.** When a tool result contradicted the request (a 17 Jul
scene fed back after it asked for 31 Jul–3 Aug), it re-called `stac_search` **four times** with
tweaked dates instead of advancing. Defensible reasoning, but unbounded it burns the context
window. Needs max-iterations plus a same-tool-same-args repeat detector.

**bge-m3 cross-lingual retrieval verified** — PT↔EN same meaning **0.842**, PT↔unrelated PT
0.283. It keys on meaning, not language, so the Portuguese demo query will retrieve an English
corpus. This choice is one-way: switching embedders means re-embedding everything.

### Sentinel-1 coverage over Portugal — VERIFIED, risk retired

The guide called this "the one real risk" and "highest-priority unknown". **It is not a risk.**
ASF `GRD_HD` counts, 2026-06-01 → 2026-06-15:

| AOI | Count |
|---|---|
| **Lisbon / Tagus** `(-10.5 38.0, -8.5 39.5)` | **46** |
| Deep Atlantic `(-13.0 37.0, -11.0 39.0)` | 34 |
| Porto / Leixões | 31 |
| Algarve / Gulf of Cádiz | 28 |
| Azores (point -28.0 38.5) | 5 |
| Madeira (point -16.9 32.7) | 4 |

Coastal **and** open-ocean coverage are both healthy. Even the deep Atlantic box returns 34.
Azores/Madeira are sparse but non-zero — the guide's worry about open ocean was overcautious.

Full June 2026 over the Lisbon box: **81 granules, 100% dual-pol VV+VH.** VH is available for
detection everywhere, so the guide's §6 "use VH" advice is actionable on every scene.

### Overpass windows — NEW, not in the guide

Guide gave Denmark windows only, and they are slightly wrong.

| AOI | Descending (UTC) | Ascending (UTC) |
|---|---|---|
| Denmark / Kattegat — guide | 05:24–05:41 | 16:52–17:09 |
| Denmark / Kattegat — **measured** | **05:23–05:40** ✅ close | **16:44–17:09** ⚠️ guide misses the 16:44 pass |
| **Portugal / Lisbon — measured** | **06:33–06:51** | **18:26–18:43** |

⚠️ The guide's ascending Denmark window would silently drop ~1/3 of ascending passes
(16:44 appears 10× in July 2026). Widen it.

### Constellation — VERIFIED, guide correct

S1A retirement 29 Jun 2026 confirmed empirically from the catalogue:

- Portugal, **June** 2026: Sentinel-1A ×31, 1C ×18, 1D ×32 → S1A still flying
- Denmark, **July** 2026: Sentinel-1C ×36, 1D ×36, **zero S1A** → retired

Post-June scenes are S1C/S1D only. Granule prefixes are `S1C_`/`S1D_`; any regex hardcoding
`S1A|S1B` will match nothing. Query the catalogue, never a pre-2026 revisit table.

### Scene size — CORRECTION

Guide says 1.7–2.1 GB per dual-pol IW GRDH. **Actual: 780–990 MB**, roughly half.
Disk budget is far more comfortable than planned.

---

## ⚠️ Corrections that would have cost hours

### 1. `web.ais.dk` is DEAD — the guide's DMA URL does not work

`PRE_DEV_GUIDE.md` §5 and `EXECUTION_SPEC.md` §3.1 both give
`https://web.ais.dk/aisdata/`. Current state:

- TLS certificate `CN=*.govcloud.dk` **expired 12 Jun 2025** — over a year stale
- Even with `curl -k`, the handshake completes and the server then **hangs** with no response
- Port 80 does not answer

Cause: per dma.dk, *"the Danish Land Based AIS-system is now under the jurisdiction of the
Danish Emergency Management Agency."* The service moved.

**Working replacement — VERIFIED:**

```
http://aisdata.ais.dk/          ← official page, but a JS-rendered S3 listing (no links in HTML)
http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/   ← the actual bucket, plain S3 REST
```

Note **`http://`, not `https://`** — the HTTPS variant fails on this host too.

The pretty page is a `s3-bucket-listing` JS widget; scraping its HTML returns zero filenames.
Hit the bucket's S3 REST API instead — it is public, unauthenticated, and paginates properly:

```bash
# list recent dailies
curl -s "http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/?list-type=2&delimiter=/&max-keys=1000" \
  | grep -oE '<Key>[^<]+</Key>' | sed 's|</\?Key>||g'

# fetch one day
curl -O "http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/aisdk-2026-07-17.zip"
```

**Bucket layout** (the guide's `aisdk-YYYY-MM.zip` monthly guess was half right):
- **root** — daily files, rolling window `aisdk-2025-02-27.zip` → `aisdk-2026-07-31.zip`
- `YYYY/` prefixes (2006–2025) — older **monthly** archives, e.g. `2006/aisdk-2006-03.zip`

So only ~17 months of dailies are retained. Anything older is monthly-only.

### 2. Daily file size — CORRECTION, ~2× the guide

Guide says ~300–500 MB zipped. **Measured:** 848 MB (07-17), 956 MB (07-15), 988 MB (07-16),
593 MB (03-29). Budget ~1 GB zipped and (by the guide's 10× ratio) **8–12 GB unzipped** per day.
Stream-filter — do not unzip a whole day to disk casually.

### 3. Publish lag is ~3 days, not 48 h

`aisdk-2026-07-15.zip` has `Last-Modified: Sat, 18 Jul 2026`. Latest available today
(2026-08-03) is `2026-07-31`. Plan for 3 days, not 2.

### 4. `~/.netrc` has a `token` line that breaks Python's stdlib parser

The existing netrc is:

```
machine urs.earthdata.nasa.gov
login <redacted>
password <redacted>
token <JWT>
```

`curl -n` tolerates the trailing `token` key. **Python's `netrc` module does not:**

```
netrc.NetrcParseError: bad follower token 'token' (/home/nls/.netrc, line 4)
```

Anything in the ingest path calling `netrc.netrc()` will crash. Either drop the `token` line,
or read credentials another way. Worth knowing before it surfaces as a mystery failure inside a
download helper.

### 5. DMA timezone — RESOLVED: it is UTC

DMA documents this nowhere; their own bucket README gives the schema and the day-first format
but states no timezone. Settled empirically with the guide's DST histogram test on
`aisdk-2026-03-29.csv` (last Sunday of March 2026; Europe/Copenhagen springs forward
02:00→03:00 local):

```
00:00   709855   01:00   711596   02:00   712161   03:00   696666
```

Smooth across the transition — no one-hour hole at `02:xx`. **Timestamps are UTC.**
A local-time file would have shown a gap. Slice acquisition windows in UTC directly.

### 6. Decimal-comma worry — NOT a real trap

The README writes coordinates as `57,8794` / `17,9125`, which suggested the CSV might not be
comma-delimited. Checked against the real file: **that is Danish prose, not the format.**
Actual rows use decimal points and plain comma delimiting:

```
# Timestamp,Type of mobile,MMSI,Latitude,Longitude,...          ← 26 fields, '# Timestamp' intact
17/07/2026 05:13:00,Class B,257843840,56.489370,10.951233,...
```

Guide's §5 schema description is accurate in every respect.

### 7. ~71% of AIS rows are exact duplicates — NEW, quantifies the guide's warning

The guide warns that "AIS hygiene manufactures phantom darks" but gives no magnitude. Measured
on the 22-minute Kattegat acquisition window:

| | |
|---|---|
| raw rows | 134,851 |
| after exact-duplicate removal | 38,945 (**−71%**) |
| unique `(MMSI, timestamp)` | 38,850 |
| worst rebroadcast multiplicity | **21 identical copies** of one message |
| after dedup | 42.8 distinct timestamps per vessel, max 544 |

Multi-station rebroadcast, exactly as the guide predicts. **Dedup on
`(MMSI, timestamp, lat, lon)` before matching** — otherwise duplicate-weighted
nearest-in-time logic is distorted. 42.8 positions per vessel across 22 minutes is ample to
interpolate each track to the acquisition instant rather than taking nearest-raw, which is what
the guide's §7 asks for.

---

---

## aisstream.io — the "no free AIS for Portugal" claim needs amending

`PRE_DEV_GUIDE.md` and `EXECUTION_SPEC.md` §3.1 both assert flatly:
**"No free point-level AIS exists for Portuguese waters."** That is too strong.

[aisstream.io](https://aisstream.io) gives free real-time global AIS over WebSocket, API key
only, bbox subscription. Pulled live from the Tagus estuary on 2026-08-03:

```json
{"MMSI": 263701390, "name": "GIL VICENTE", "lat": 38.6526, "lon": -9.08095,
 "sog": 0, "cog": 341, "utc": "2026-08-03 15:12:39 UTC"}
```

MMSI `263…` is a Portuguese flag. Free, point-level, Portuguese waters.

**But it does not rescue the Portuguese correlation story, for two measured reasons.**

### 1. Real-time only — no archive

There is no historical endpoint. It cannot serve the 13 June Lisbon scenes, or any past
acquisition. It only works by recording *forward* and matching a *future* overpass.

### 2. The feed is incomplete — measured against ground truth

**The controlled test.** Same Kattegat bbox, same 22-minute clock window (15:29–15:51 UTC),
one source against the other:

| source | messages | unique vessels |
|---|---|---|
| **DMA** (17 Jul 2026) | 80,388 raw / ~23,300 deduped | **770** |
| **aisstream** (3 Aug 2026, live) | **609** | **132** |

**aisstream sees ~17% of vessels — it misses roughly 5 in 6.**

**This is a ceiling, not a warm-up.** Per-minute new-vessel counts: 32, 21, 14, 13, 11, 4, 4,
4, 3, 3, 3, 0, 4, 3, 0, 3, 3, 3, 0, 3, 0, 1 — flat from ~minute 10. Recording for four days
will not close it. Those vessels are not being received at all, not merely reported slowly.

Caveats, honestly: different days (17 Jul vs 3 Aug) so traffic differs — but not by 6×. Same
bbox, same hour. Denmark is DMA's home receiver network, so best case for the reference side.

**Consequence:** if ~83% of real vessels are absent from the feed, the matcher marks ~83% of
detections dark. Worse than the 40%-dark failure the guide warns about.

**The structural trap:** completeness over Portugal *cannot be measured*, because the absence
of a DMA equivalent is both why aisstream is needed there and why there is no reference to
check it against. Denmark is the only AOI where this gap is observable — which is a large part
of what the Danish AOI is actually for.

### 2b. Earlier throughput samples (superseded by the above, kept for the reasoning)

Message throughput is the robust test, because it needs no cross-source comparison. Class A
transmits every 2–10 s, so a fully-observed vessel yields **~40 messages per 4 minutes**:

| AOI | window | messages | unique vessels | msgs/vessel |
|---|---|---|---|---|
| Lisbon | 4 min | 39 | 29 | **1.3** |
| Kattegat | 4 min | 114 | 79 | **1.4** |

~1.4 against an expected ~40 is roughly **3% of expected message volume**. The feed is sampled,
not complete.

Cross-checked against ground truth in the one place we have it — the same Kattegat bbox:

| source | window | unique vessels |
|---|---|---|
| **DMA** (17 Jul, 05:13–05:34 UTC) | 22 min | **907** |
| **aisstream** (3 Aug, ~15:20 UTC) | 4 min | 79 (still climbing: 27→50→67→79) |

Not a controlled comparison — different day, different hour, and aisstream had not saturated
(increments were decaying 27→23→17→12, extrapolating to roughly 130–200 at 22 min). Even
generously, that is a **4–7× shortfall**. The independent msgs/vessel figure points the same way.

### Consequence — do NOT use it as ground truth

Absence from a thinned feed is not absence of AIS transmission. Feeding this to the matcher
would mark large numbers of ordinary vessels "dark" and blow straight past the guide's ~5%
unmatched / 0.4% genuinely-dark base rate — the exact "if your pipeline reports 40% dark, it's
broken" failure mode.

### What it IS good for

- **Makes `CustomerFeedSource` real.** EXECUTION_SPEC §3.1 wants it as a stub raising "not
  configured". A live WebSocket adapter turns "in a real deployment the customer brings the
  feed" from a talking point into working code — the third implementation of the source
  adapter interface, exercising it properly.
- **A live demo layer** over Portugal, clearly labelled as indicative coverage, not ground truth.
- **Honest framing:** *aisstream demonstrates the mechanism over Portugal; Denmark remains the
  rigorous validation.* Stronger than either "no free AIS exists" (false) or "I independently
  correlated Portugal" (unsupportable). It also shows the claim was tested rather than assumed.

**Amend both documents** to: *no free **historical, complete** point-level AIS exists for
Portuguese waters; a free real-time feed exists but is too sparse for dark-vessel rate
estimation.*

---

## Blocked — needs Nelson

### A. ASF downloads return 401 — Earthdata EULA not accepted

Scene *search* needs no auth and works fine. *Download* fails:

```
$ curl -n --location-trusted -r 0-1023 <downloadUrl>
401 — "Could Not Login. Be sure to agree to the EULA."
```

Diagnosis:
- Earthdata account `lunasilvestre` exists; netrc present, `chmod 600` ✅
- Bearer token is **valid** — issued 2026-06-30, expires 2026-08-29, and returns
  `200` against `cmr.earthdata.nasa.gov` ✅
- So this is **not** bad credentials and **not** an expired token

It is the ASF EULA / application authorization, which must be accepted once in a browser:
<https://urs.earthdata.nasa.gov/profile> → *Applications* → *Authorized Apps* → authorize
**Alaska Satellite Facility Data Access**, and accept the Sentinel EULA.

**Nothing SAR-side can proceed until this is done.** It gates both AOIs.

### B. `ollama` not installed

```bash
curl -fsSL https://ollama.com/install.sh | sh     # needs sudo
ollama pull qwen2.5:14b-instruct-q4_K_M
ollama pull bge-m3
```

### C. GFW API token — ✅ CLEARED 2026-08-03

Registered as an individual, intended use "Non Commercial". Token issued (798 chars) and
verified working: **HTTP 200** against `v3/vessels/search`. Stored in
`~/.config/eo-credentials.env` as `GFW_TOKEN` — the name `gfwr`'s `gfw_auth()` expects — and
**not** in the repo. Licence CC BY-NC 4.0; attribution owed in the README (EXECUTION_SPEC §8.6).

**GFW SAR reference layer — VERIFIED WORKING on the scene dates, outage confirmed still live.**

Dataset `public-global-sar-presence:v4.0` ("Radar vessel detections (SAR)"), status `done`,
coverage from 2017-01-01. Queried via `4wings/report` over the Lisbon AOI:

| date range | detections |
|---|---|
| **2026-06-13 → 06-14** (our scene date) | **69** ✅ |
| 2026-06-01 → 06-30 | 302 ✅ |
| 2026-07-25 → 08-02 | **0** ⚠️ outage still ongoing |

So the Portuguese reference layer **exists for exactly the scenes we downloaded**, and the
3 Jul 2026 outage is confirmed unresolved as of today. The spec's instruction — use a
historical date before July 2026 — is not caution, it is required.

API gotcha: `4wings/report` **requires** `group-by` (one of `VESSEL_ID, FLAG, GEARTYPE,
FLAGANDGEARTYPE, MMSI`) or it 422s. Also `/v3/datasets` returns `total: 45535` with an empty
`entries` array unless paginated properly — query a dataset by ID directly instead.

⚠️ **Still unverified, and it matters at M3:** the above returns **gridded aggregate counts**,
not per-detection records carrying `matched=true/false`. EXECUTION_SPEC §3.1 wants *unmatched*
detections specifically. Whether per-detection matched flags are retrievable through the
public API (vs only through the paper's BigQuery tables) is the open question. If they are
not, the Portuguese "dark" layer degrades to "GFW saw N detections here, my detector saw M" —
still a real cross-check, but weaker than the spec assumes. **Resolve this early in M3.**

---

## → HANDOFF TO M0 (read this first in a fresh session)

Pre-dev is complete. Everything below is state a new session cannot infer from the specs.

### Files that already exist — do NOT recreate or clobber

| path | what it is |
|---|---|
| `.env` | 0600, gitignored. Real config. **Not** a template. |
| `.env.example` | committed template; keep the two in sync (all keys must match) |
| `.gitignore` | covers `.env`, `data/`, `*.zip`, `*.tif` |
| `scripts/load-env.sh` | `source` it (don't execute). Loads `~/.config/eo-credentials.env` then `.env`. Provides `nightglass_env_status`. |
| `data/sar_manifest.txt` | 4 pinned granules — ⚠️ its **Portuguese** entries are path 23, offshore, **superseded** |
| `data/sar_manifest_pt_fix.txt` | the correct Portuguese scenes (path 125, over the approaches) |

**Credentials live outside the repo**, in `~/.config/eo-credentials.env` (0600):
`GFW_TOKEN`, `AISSTREAM_API_KEY`, `CDS_API_KEY`, `EUMETSAT_*`. Earthdata stays in `~/.netrc`.

### ✅ RESOLVED AT M0 — ollama runs in compose. See "M0 — built" below.

**This was a real conflict and M0 resolved it deliberately.** The reasoning as it stood
before the decision is kept intact below, because the argument is the answer.

`EXECUTION_SPEC.md` §2 lists `ollama` as a **compose service**. But it is currently installed
as a **host systemd service on port 11434**, with models pulled and keep-alive pinned.

- **Pointing compose at the host** is tempting (models already there, ~10 GB not re-downloaded)
  but it **breaks the entire demonstration**: app services run on `internal: true` with no
  egress, and reaching the host needs `host-gateway`/`host.docker.internal` — a deliberate hole
  in the exact boundary M1 exists to prove. It also breaks M6's "clone and `make up`".
- **Recommended: ollama as a compose service**, per the spec. The host install stays a dev
  convenience. Notes: don't publish 11434 from the container (only `api`/`agent` need it on the
  internal network, so there's no clash with the host service), and put model blobs on a named
  volume with a documented pull step so a cloner can reproduce. Bind-mounting the host's
  `OLLAMA_MODELS` dir avoids re-downloading 10 GB locally but must not be the documented path.

Whatever is chosen, **write it into `NOTES.md` and the README design table** — "why is the
model server inside the enclave rather than beside it" is exactly a hiring-manager question.

---

## M0 — built 2026-08-03. `make up` → five containers healthy.

```
NAME                 IMAGE                    STATUS
nightglass-api       nightglass/app:dev       Up (healthy)
nightglass-mcp       nightglass/app:dev       Up (healthy)
nightglass-ollama    ollama/ollama:0.32.5     Up (healthy)   11434/tcp
nightglass-postgis   postgis/postgis:17-3.5   Up (healthy)   5432/tcp
nightglass-qdrant    qdrant/qdrant:v1.18.3    Up (healthy)   6333-6334/tcp
```

Those `PORTS` values are container-exposed ports with **no host binding** — see finding 8.

### Decision: ollama is a compose service

Taken as the handoff recommended, for the handoff's reason: reaching the host service needs
`host.docker.internal:host-gateway`, which is a deliberate hole in the exact boundary M1
exists to prove, and it breaks M6's "clone and `make up`". `.env.example` had already
committed to it with `OLLAMA_HOST=http://ollama:11434`.

The two real costs, and what was done about each:

| cost | mitigation |
|---|---|
| ~10 GB of model blobs re-downloaded | `make pull-models` — a **profile-gated** service on a **separate network**, so the enclave itself never has egress. Plus `make seed-models`, which copies the blobs already on this host into the volume. That one needs sudo and is explicitly *not* the documented path. |
| VRAM contention | The host service pins ~15 GB with `OLLAMA_KEEP_ALIVE=-1`, leaving 6.5 GB of 24 GB. `scripts/preflight.sh` detects it and names the fix (`sudo systemctl stop ollama`). |

### 8. `internal: true` silently drops published ports — NEW, and it shaped the topology

Verified directly on Docker 29.7.1 before writing the compose file:

```
$ docker run -d --network <internal-net> -p 18099:80 nginx:alpine   # starts fine
$ docker ps        →  80/tcp          ← no host mapping. None.
$ curl 127.0.0.1:18099  →  HTTP 000
$ # identical container on a normal bridge  →  HTTP 200
```

**No warning is emitted anywhere.** The mapping is simply never created.

So no NIGHTGLASS service publishes a port — not as a style choice, but because it would not
work and the compose file would be lying. Consequences: host access is `docker compose exec`
(which is what §6's demo already uses), and MCP reaches Claude Desktop over **stdio through
`docker exec`** rather than SSE over a socket. A pipe crosses the boundary without opening it.

The tempting workaround is a second bridge network with
`com.docker.network.bridge.enable_ip_masquerade=false` — published ports work, egress does
not. **Rejected.** It weakens the proof from *"no route exists"* to *"packets leave and never
come back"*, Docker's embedded DNS still resolves external names on such a network, and it is
a tunable flag rather than a structural property. Default-deny by construction survives a
future careless edit; a flag does not.

### The air gap is real, and it fails in the strong way

```
$ docker compose exec api curl -m 5 https://example.com
curl: (6) Could not resolve host: example.com
$ docker compose exec api getent hosts ollama qdrant postgis
172.21.0.4  ollama      172.21.0.3  qdrant      172.21.0.2  postgis
```

Not a timeout — **name resolution itself is refused**, because an internal network's embedded
DNS does not forward externally. That is the difference between "firewalled" and "air-gapped",
and it is the M1 capture worth recording. Internal service discovery still works.

Corollary that bit immediately: **`make test` originally pip-installed pytest at run time and
failed** with `Temporary failure in name resolution`. Correct behaviour, useless target. Test
deps moved to a `dev` build stage; tests now run with `--network none`. Anything that assumes
a package index at run time has to move to build time — worth remembering at M2 and M7.

### 9. A transposed bbox is NOT generally detectable — validation cannot save us

Assumed at first that a lat/lon swap could be caught by range-checking. It cannot. Lisbon's
box swapped — `38.0,-10.5,39.5,-8.5` — is a **perfectly valid box off Somalia**; Kattegat's
lands in the Arabian Sea. Both parse clean, pass every check, and return nothing.

Only a swap that pushes a latitude past ±90 is catchable, and none of our AOIs do.

So the defence is structural, not validational: axis order is converted in exactly one place,
`BBox.as_aisstream()`, and `tests/test_config.py` asserts the undetectable case explicitly so
nobody later assumes the validator covers it. This is the same failure the handoff flagged as
"the most common way to get a silently empty stream".

### What M0 built

```
docker-compose.yml          one internal network, no egress, pinned tags
docker/Dockerfile           runtime + dev stages; curl is present because M1's proof needs it
docker/postgis/initdb/      postgis, postgis_topology, btree_gist; schemas stac/detect/ais
Makefile                    up, down, preflight, pull-models, seed-models, air-gap-proof, test, lint
scripts/preflight.sh        docker, nvidia runtime, VRAM, AOI config
scripts/air-gap-proof.sh    §M1's check, both halves, as a repeatable command
scripts/seed-models-from-host.sh
src/nightglass/config.py    AOI resolution — the only place a bbox is named
src/nightglass/schemas.py   §5 contracts, provenance attached to every result
src/nightglass/api/         FastAPI: /health /ready /config
src/nightglass/mcp/         FastMCP, stdio + sse, one probe tool
src/nightglass/agent/       placeholder; the graph is M5
tests/test_config.py        10 tests, all passing
README.md                   §8 skeleton — air-gap capture slot still empty, that is M1's
```

### Design calls made along the way (all reversible, per §"momentum rules")

- **The agent is a one-shot `run --rm`, not a long-running service.** It runs to a human gate
  and halts, so it has nothing to serve between invocations; a daemon wrapper would exist only
  to satisfy a healthcheck. It sits behind `profiles: ["cli"]` so `make up` never starts it.
- **`/health` is dependency-free; `/ready` does the dependency checks.** A healthcheck that
  reaches sideways turns one slow service into a cascade of unhealthy containers.
- **`pg_isready -h 127.0.0.1`, not the default socket.** Over the unix socket it answers
  *during* initdb while the TCP listener is still closed, so the container reports healthy
  before anything can connect to it.
- **Qdrant's image has no curl and no wget.** Healthcheck uses bash's `/dev/tcp`.
- **Ground truth is enforced in the type system, not in prose.** `Match.source_is_ground_truth`
  travels with every match and `CorrelationResult.rate_is_quotable` is false unless all matches
  came from a ground-truth feed. §7's "rates only over Denmark" should not depend on remembering.
- **Overpass windows moved into `.env`** as `AOI_<NAME>_PASS_DESCENDING/ASCENDING`, since §3.1
  lists "time window" among the things that must come from config. Kattegat's ascending bound is
  the corrected **16:44**, not the guide's 16:52.
- **`POSTGRES_PASSWORD` in `.env` was still the literal `change_me_locally`** — it is now a
  generated value. `.env.example` keeps the placeholder. Key parity between the two is intact.

### 10. `/usr/share/ollama` is mode 700 — existence checks must run under sudo

`seed-models-from-host.sh` first guarded with a plain `[[ -d "$HOST_MODELS" ]]`. That guard is
useless: the ollama home is **mode 700 owned by `ollama`**, so an unprivileged test on any path
beneath it returns false whether or not the path exists, and the script would have reported
"no host model store" on a store sitting right there. Every existence check in it now runs as
`sudo test -d`. Generalises: a permission-denied traversal and a genuine absence are
indistinguishable to `test`, and only one of them is worth an error message.

---

## M1 — done 2026-08-03. Both halves captured.

Models seeded from the host (9.5 GB, 8 blobs, both manifests), host ollama stopped, and
`make air-gap-proof` passes end to end. The capture is in the README.

```
$ docker compose exec api curl -m 5 https://example.com
curl: (6) Could not resolve host: example.com          ← not a timeout. no route at all.
$ chat completion against qwen2.5:14b, inside the enclave
Radar de Abertura Sintética (SAR) é uma tecnologia que permite capturar imagens
detalhadas da superfície da Terra usando ondas de rádio, funcionando bem de noite
porque não depende da luz solar para operar.                              ← correct
```

### 11. ⭐ The model has NO prior for "embarcação escura" — this is the case FOR M2

The first proof run asked, in Portuguese, *"o que é uma embarcação escura?"*. Ungrounded, with
no retrieved context, qwen2.5:14b answered:

> *"pode ser qualquer tipo de barco ou navio que seja **pintado em cores escuras** ou esteja
> situada em um fundo que a faz parecer escura visualmente."*

It read the intelligence term as a description of **paint colour**. Fluent, confident, entirely
domain-wrong — and it would have read as authoritative to anyone who did not already know better.

This is the single most useful thing to come out of M1, for three reasons:

1. **It is the concrete argument for M2.** The operational meaning of "dark vessel" has to come
   from the retrieved corpus, never from model priors. That is no longer an assertion in a spec;
   there is a transcript.
2. **It is the README's RAG before/after.** Same question, ungrounded vs grounded-with-citations,
   is a far stronger demonstration than any retrieval-hit-rate number. Save it for §M2.
3. **It was the wrong prompt for the M1 capture** — a reader sees a wrong answer directly beneath
   the words "inference works". The air-gap script now asks about SAR instead, which the base
   model answers correctly. The question is kept here deliberately, not discarded.

Generalises to §7: a fluent, confident, wrong answer from an ungrounded local model is *exactly*
the failure the provenance requirement exists to catch. Worth saying out loud in the hiring
manager round — the honest version is "I found my own model doing this and built around it."

### 13. ⭐ `OLLAMA_HOST` means two different things — and it broke `make pull-models`

Running the documented provisioning path for the first time failed immediately:

```
$ make pull-models
Error: listen tcp: lookup ollama on 127.0.0.11:53: no such host
ollama did not come up
```

**`OLLAMA_HOST` is read by `ollama serve` as the address to BIND to, and by every ollama client
as the URL to CONNECT to.** `.env` sets it to `http://ollama:11434`, which is correct for the
enclave's clients (`api`, `agent`, `mcp`). The `model-puller` inherited that same value via
`env_file`, so its `ollama serve` tried to bind to a hostname that does not exist on the
provision network, and died.

Worse, the *enclave's* ollama had the identical misconfiguration and **worked by accident** —
the hostname `ollama` resolves to that container itself on the enclave network, so binding to it
succeeded. A latent fragility that looked like a working system.

Both now set the bind address explicitly: `0.0.0.0:11434` for the enclave server,
`127.0.0.1:11434` for the puller, which serves nobody and only needs a local endpoint to pull
through. Re-verified afterwards: five containers healthy, air-gap proof still passes both halves.

**This is the argument for exercising the documented path rather than the convenient one.**
`make seed-models` worked fine and hid this completely; a cloner following the README would have
hit it on their first command. Found because the path a stranger takes was actually run.

### 14. Ollama has its own outbound paths — the network was the only thing stopping them

Reading the `model-puller` startup log properly (rather than just noting it succeeded):

```
OLLAMA_NO_CLOUD:false   OLLAMA_REMOTES:[ollama.com]
msg="Ollama cloud disabled: false"
msg="model recommendations cache sleep scheduled" wait=3h20m42s
```

Ollama 0.32.5 ships **cloud-hosted model routing** via ollama.com and a periodic
**model-recommendations refresh**. Inside the enclave both fail — there is no DNS to resolve
ollama.com with — so nothing leaked. But *"it fails because the network stops it"* is a weaker
claim than *"it was never enabled"*, and for a project whose entire thesis is the boundary,
leaning on one layer is the wrong instinct.

`OLLAMA_NO_CLOUD=true` now set on the enclave service; verified in its log as
`Ollama cloud disabled: true`. Deliberately **not** set on `model-puller`, which legitimately
reaches the registry. The boundary and the application now agree rather than one covering for
the other.

Also from that log, and correct rather than a bug: the puller reports `library=cpu`,
`total_vram="0 B"`. It has no GPU because only the enclave `ollama` service carries the device
reservation. Pulling is a download, not inference. Nobody should "fix" this by adding a GPU.

Generalises, and it is the honest answer to §9's *"what breaks first in a real air-gapped
deployment"*: not the model, not the database — **some dependency's built-in telemetry, update
check or cloud fallback**, which nobody declared and which works fine in every environment where
it was tested.

### 12. Free-VRAM preflight blocked the thing it existed to enable

`make air-gap-proof` refused to run with *"only 7412 MiB free — inference needs ~16000 MiB"* —
because the enclave's ollama had **already loaded the model**. The VRAM was consumed by precisely
the process that needed it, and free-VRAM arithmetic had become the wrong question.

Passed the first time only because nothing was loaded yet. Now the check asks *is the model
resident?* before *is there room to load it?*, and short-circuits when `ollama ps` inside the
enclave lists anything. A resource precondition has to be written against the goal state, not
the free-resource count, or it starts failing the moment it succeeds.

### Still open after M0

- ~~`make pull-models` is still unproven~~ → **run and passing** (see finding 13 — it was broken,
  and running it is what found the bug). Residual gap: it was exercised against a volume that
  already held the blobs, so it verified manifests and digests rather than downloading 9.5 GB
  cold. Everything of *ours* is now proven — network, volume, serve-and-wait, env, the loop —
  and what remains untested is ollama's own downloader. Worth one genuinely cold run before M6,
  but it is no longer a guess.
- ~~`make air-gap-proof`'s chat half is unrun~~ → **both halves pass**, capture in the README.
- FastMCP 3.4.5 still accepts `transport="sse"`, but `http`/`streamable-http` is the modern
  path. Worth revisiting at M4 when Claude Desktop attaches for real.

### Two findings that save real time at M3

**1. Don't hand-parse the geolocation grid.** `PRE_DEV_GUIDE.md` §6 says georeferencing lives
in `annotation/*.xml` and to "build GCPs from it". **GDAL already does this.** `rasterio` exposes
all 210 tie points directly:

```python
with rasterio.open(path) as src:
    src.crs        # None      — expected, GRD is not map-projected
    src.transform  # identity  — expected
    src.gcps       # (list of 210 GCP objects, CRS EPSG:4326)  ← use these
```

**2. Read scenes in place — no extraction.** A SAFE zip is 5.45 GB unpacked; GDAL's `/vsizip/`
reads the measurement TIFF straight out of it:

```python
vh = f"/vsizip/{zip_path}/{safe_name}.SAFE/measurement/{vh_tiff_name}"
```

Verified on all six granules. Saves ~30 GB of disk and the unzip time.

### Verified working, don't re-litigate

- ASF download needs **EULA + `--location-trusted` + a cookie jar** (`-c`/`-b`). Without the
  cookie jar it redirect-loops ~50 times; that is not an auth failure.
- Ollama tool-chaining passes M4's bar (3 tools) — but **M5 needs a loop-breaker**; it re-called
  one tool 4× when a result contradicted its request.
- bge-m3 cross-lingual retrieval works (PT↔EN 0.842 vs 0.283 unrelated).
- GFW SAR reference layer returns **69 detections on 2026-06-13** over Lisbon; outage still
  live for late July. `4wings/report` **requires** `group-by`.

### Data inventory — 6.2 GB on disk

```
data/raw/sar/   6 granules, 4.7 GB   4× S1D (2 DK 17 Jul, 2 PT offshore) + 2× S1A (PT approaches 13 Jun)
data/raw/ais/   2 days, 1.5 GB       aisdk-2026-07-17 (validation) · aisdk-2026-03-29 (DST test)
data/interim/   23 MB                ais_kattegat_20260717_0513-0534.csv — the matched-window slice
```

---

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Portuguese demo AOI** | **Deliberately still open** — recorder covers both | Two viable candidates and no need to choose yet. **Lisbon/Tagus** `-10.5 38.0 → -8.5 39.5` has the best measured coverage (46 in 2 weeks, 81 in June, all VV+VH) and the EMSA-runs-from-Lisbon narrative. **Leixões** `-9.8 40.9 → -8.6 41.6` has three overlapping S1 paths (125/45/147) instead of two, so ~50% more acquisitions. Decide after Friday's scenes are on disk, not before. |
| **AIS recorder bbox** | **Union: lat 38.0–42.5, lon −11.5 → −8.0** | Spans both Portuguese candidates with margin. Costs a larger footprint and some offshore dead space; buys the ability to change AOI after seeing real data. You can always filter down, never back-fill. `NIGHTGLASS_RECORD_BBOX_LAT/LON` in `.env`. **Axis order is `[[lat,lon],[lat,lon]]`** — opposite to most GIS tooling, and the most common way to get a silently empty stream. |
| **Danish validation date** | **2026-07-17** | S1D descending, granules 05:23:24 + 05:23:49 UTC. AIS day was the smallest candidate (848 MB) and the overpass sits earliest in the descending window. |
| **AIS ingest transport** | S3 REST against `aisdata.ais.dk.s3.eu-central-1.amazonaws.com` | Guide's `web.ais.dk` host is dead; official page is JS-rendered and unscrapeable. S3 REST is public, paginates, and needs no auth. See correction 1. |
| **AIS dedup key** | `(MMSI, timestamp, lat, lon)` | 71% of rows are rebroadcast duplicates (correction 7). Must happen before matching. |
| **AIS time handling** | Parse as UTC, no conversion | Settled by DST test (correction 5). |

### Data on disk

```
data/raw/ais/aisdk-2026-07-17.zip    849 MB  (5.45 GB unzipped)  validation day
data/raw/ais/aisdk-2026-03-29.zip    593 MB  (3.13 GB unzipped)  DST test file
data/interim/ais_kattegat_20260717_0513-0534.csv   23 MB         acquisition-window slice
```

`data/` must be gitignored at M0.

---

## Checklist status

```
ENVIRONMENT
[x] nvidia-smi → 3090 visible, 22.3 GB free
[x] docker compose version → v5.3.1
[x] ollama 0.32.5 + both models, pinned resident (UNTIL=Forever)

PORTUGAL (demo AOI)
[x] ASF count query >0 for chosen bbox → 46, risk retired
[x] Portuguese AOI fixed → Lisbon/Tagus -10.5 38.0 → -8.5 39.5
[x] S1 GRD over Portugal downloaded, VH opens (path 125, covers approaches)
[x] GFW API token obtained + verified (HTTP 200)
[ ] gfw_sar_vessel_detections() returns rows              ← check outage

DENMARK (validation AOI)
[x] Earthdata + EULA accepted + cookie jar → downloads work (206, PK header)
[x] Kattegat S1 coverage confirmed → 72 granules in July 2026
[x] S1 GRD over Kattegat downloaded, VH opens, 210 GCPs
[x] DMA source located (guide's URL is dead — see correction 1)
[x] DMA zip downloaded and verified → aisdk-2026-07-17.zip, 5.45 GB unzipped
[x] AIS CSV parses with '# Timestamp' intact → 26 comma-separated fields
[x] Timezone DST test run → UTC confirmed
[x] AIS filtered to acquisition ±10 min → 907 vessels / 134,851 positions ✅ GATE PASSED
```

**The Danish half of the pre-dev gate is fully cleared.** The guide's closing line —
*"SAR pixels plus a few hundred AIS positions inside the footprint at acquisition time, and
everything downstream is code"* — is half satisfied: 907 unique vessels (not merely a few
hundred positions) sit inside the Kattegat AOI at the S1D acquisition instant. The only
missing ingredient on both AOIs is the SAR pixels, which is blocker A alone.
