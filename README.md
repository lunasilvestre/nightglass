# NIGHTGLASS

**Air-gapped SAR intelligence assistant.** Finds vessels in Sentinel-1 radar imagery, checks
them against AIS, and drafts a cited intelligence report — with no route to the internet.

An analyst asks whether anything undeclared has moved through an area in the last 72 hours. NIGHTGLASS searches the SAR catalogue for scenes covering it, runs its own
detector over the radar amplitude, correlates each detection against vessel position reports in
space *and* time, pulls supporting context out of an intelligence document corpus, and produces
an INTREP in which every claim carries the scene, detection and document IDs it rests on. It
halts for human review before anything is marked releasable. The chat model, the embedding
model and the vector store all run inside the enclave; nothing calls out.

Deliberately shaped as the open-source shadow of ICEYE Ocean Vision Detect.

![How NIGHTGLASS works — six steps from radar echo to a cited, human-approved report](docs/how-it-works.svg)

> **A dark detection is a lead, not a conclusion.** A vessel with no AIS correspondence has
> plenty of innocent explanations — satellite revisit gaps, terrestrial receiver limits,
> transponder failure, low-power class B sets, vessels never required to carry AIS at all. The
> system surfaces candidates. The analyst adjudicates.

**Status: M6 — packaged, plus the first of §M7's optional items.** The enclave stands up and is sealed (M0), runs inference
offline (M1), answers from a 60-document corpus with every claim traced to a retrievable chunk
or refuses (M2), runs its own vessel detector over real Sentinel-1 pixels and correlates the
detections against real AIS in space *and* time (M3), serves all six [`EXECUTION_SPEC.md`](docs/EXECUTION_SPEC.md) §5
tools over both HTTP and MCP (M4), runs a LangGraph agent that halts at a human gate and
resumes in a different container (M5), and fetches every byte it needs from a checksummed
manifest, so a clone reproduces the demo rather than reading about it (M6) — and now carries the
whole of itself across an air gap in one tarball that a static Go binary refuses six ways before
it will restore (§M7). Over the Danish
validation AOI, 21 of 35 detections match a vessel that was actually there at a median 104 m,
and every AIS vessel over 200 m inside the scene footprint is recovered. The local 14B model
chains three tools unaided from a plain analyst question; Claude Code drives the same tools over a
pipe from outside the enclave. [`docs/NOTES.md`](docs/NOTES.md) is the running decision log.

---

## The demo

![NIGHTGLASS end to end](docs/demo.gif)

One command, `scripts/demo.sh`, and nothing in it is staged: the 14B model picks its own tools
while the recording runs, and every number comes out of PostGIS and the SAR pixels as you watch.
It takes **57 s live**, inside the spec's 90-second budget without being padded to fill it.

**▶ [`docs/demo.mp4`](docs/demo.mp4)** — 4.0 MB, H.264, 49 s, play and pause. This is the one to
watch.

The video is **retimed, not re-run**. A recording made by a machine is paced for a machine, and
this one had both failure modes at once: 30.5 s of frozen screen while the model chose its
tools, then 33 lines — a whole 34-row screen — arriving in a single event and scrolling away
before anyone could read them. `scripts/pace-demo.py` rewrites the timestamps and **not one byte
of the content**; it refuses to write a file whose output stream differs from the source. Long
waits are capped at 2.5 s so a slow step still reads as slow, and nothing is allowed to scroll
off the top until it has been on screen for 4.5 s.

That floor is measured rather than asserted. `scripts/check-demo.py` reports the dwell of every
one of the 167 rows, and independently slices the encoded video at 1 fps to see how much of the
screen is replaced from one second to the next:

```
per-row dwell     min 4.50 s   p05 4.50 s   median 8.81 s   max 16.48 s
one-second slice  median 14.1% of screen replaced, p95 21.0%, max 26.1%
```

It exits non-zero if any row drops under three seconds, so `make render-demo` cannot finish on a
video nobody can follow.

The GIF above is the same paced timeline at a lower frame rate for inline viewing.
[`docs/demo.cast`](docs/demo.cast) is the recording itself — 10 KB of JSON, untouched, real
time — and the artifact to check the other two against (`asciinema play docs/demo.cast`).

The spec says record over the Lisbon AOI. The Lisbon AOI has **no AIS**, and that is not an
oversight — Denmark is the only European state publishing free point-level *historical* AIS,
which is exactly why it is the validation AOI. So the recording shows both, in the order an
analyst meets them: Lisbon takes the question and produces 71 detections with the verdict
**withheld**, the corpus explains why a missing transponder is a lead rather than a finding, a
human releases the report from a different container, and the claim about the matcher is then
made over Denmark, where there is ground truth to make it against. Two AOIs on screen is also
the config-driven argument made visible instead of asserted: nothing in the code knows which AOI
it is serving.

---

## Architecture

```
                    ┌─────────────────────── HOST ───────────────────────┐
                    │                                                    │
                    │   ingest (ASF · DMA · GFW)      Claude Desktop      │
                    │   credentials live here                │           │
                    │          │                             │ stdio     │
                    │          │ loads data                  │ via       │
                    │          │ before sealing        docker exec       │
   ╔═══════════════ │ ═════════▼═════════════════════════════▼═══════════╡
   ║  nightglass_enclave        internal: true — NO ROUTE OUT             ║
   ║                                                                     ║
   ║   ┌──────────┐   ┌──────────┐   ┌──────────┐                        ║
   ║   │  ollama  │   │  qdrant  │   │ postgis  │                        ║
   ║   │  qwen2.5 │   │  intel   │   │  scenes  │                        ║
   ║   │  bge-m3  │   │  chunks  │   │  AIS     │                        ║
   ║   └────▲─────┘   └────▲─────┘   └────▲─────┘                        ║
   ║        └──────────────┼──────────────┘                              ║
   ║                  ┌────┴─────┐                                       ║
   ║                  │   api    │  FastAPI — the six tools              ║
   ║                  └────┬─────┘                                       ║
   ║              ┌────────┴────────┐                                    ║
   ║          ┌───┴───┐         ┌───┴───┐                                ║
   ║          │  mcp  │         │ agent │  LangGraph, one-shot,          ║
   ║          └───────┘         └───────┘  halts at the human gate       ║
   ╚═════════════════════════════════════════════════════════════════════╝

        nightglass_provision ─┬─ model-puller      ──► internet  (profile-gated;
                              ├─ corpus-fetcher    ──► internet   the ONLY services
                              ├─ granule-fetcher   ──► internet   with egress, and
                              ├─ ais-fetcher       ──► internet   none of them runs
                              ├─ coastline-fetcher ──► internet   during operation)
                              └─ gfw-fetcher       ──► internet
```

The boundary is one line of `docker-compose.yml`: the `enclave` network is declared
`internal: true`, so Docker attaches no default route and installs no NAT rule. There is no
egress path to misconfigure, and nothing to keep in sync as services are added. Default deny by
construction rather than by a firewall rule somebody has to maintain.

Two consequences follow, and both are deliberate:

- **No service publishes a port.** Verified on Docker 29.7.1: a container on an internal
  network started with `-p 18099:80` comes up fine, `docker ps` shows `80/tcp` with no host
  mapping, and the port is dead — with no warning emitted anywhere. So ports are not merely
  omitted for tidiness; they do not work. Access is `docker compose exec`, and MCP reaches
  Claude Desktop over stdio through `docker exec` rather than over a socket.
- **Nothing inside can fetch its own inputs at runtime.** Correct, and it applies to every
  input: weights via `model-puller`, documents via `corpus-fetcher`, the Sentinel-1 granules via
  `granule-fetcher`, the Danish AIS day via `ais-fetcher`, the shoreline via `coastline-fetcher`,
  the GFW cross-check layer via `gfw-fetcher`. Every one is a profile-gated service on the
  `provision` network, invoked explicitly by a `make fetch-*` target, and none is running during
  operation. Provisioning and operation are different security postures and the compose file says
  so out loud rather than blurring them.

  That the list is six items long and not three is itself a finding. The detector's own land
  mask structurally cannot separate a 100 m skerry from a 100 m hull, so a real air-gapped
  deployment has to ship a coastline with it; and validating a detector needs someone else's
  detections, so it has to ship those too. Both are clipped or filtered online — the enclave
  carries 713 KB of shoreline rather than a 149 MB archive, and 69 detections rather than a
  global layer.

  The two data fetchers were the last to be written, and their absence was the last thing
  standing between this repository and *reproducible*. Granules and AIS had been staged by hand
  during pre-dev; `data/` is gitignored; so every number below was true and none of it could be
  regenerated anywhere but on one machine. [`data/sources.yaml`](data/sources.yaml) is the
  committed half — a URL, a byte count and a sha256 for all eight external files — and the
  fetchers refuse anything that hashes differently. A number you cannot trace to a byte is a
  number you are asking to be taken on trust.

  Credentials follow the same split. `GFW_TOKEN` and the Earthdata login are provider identities,
  so they live outside the repository — `~/.config/eo-credentials.env` (chmod 600) and `~/.netrc`
  respectively — and are forwarded to the provision service by the invoking shell, never written
  into `.env`, and never reachable from the enclave, where they would be useless anyway. ASF's
  own documentation reaches for `curl --location-trusted`, which means *send the password to
  whatever host redirects you*; `spatial/archive.py` walks the redirect chain itself and attaches
  the credential only when the hop is `urs.earthdata.nasa.gov`.

---

## Quickstart

```bash
cp .env.example .env      # then set POSTGRES_PASSWORD
make preflight            # checks docker, the nvidia runtime, VRAM, the AOI config
make up                   # build + start, waits for every container healthy
make pull-models          # once, ~10 GB   ┐
make fetch-corpus         # once, ~35 MB   │
make fetch-granules       # once, 2.8 GB   ├ the only steps that touch the network
make fetch-ais            # once, 890 MB   │
make fetch-coastline      # once, 149 MB   │ (149 MB in, 713 KB kept)
make fetch-gfw            # once, ~70 detections ┘ the cross-check layer
make ingest               # chunk + embed the corpus into Qdrant (~1 min, offline)
make air-gap-proof        # §M1: no egress, inference works anyway
make rag-proof            # §M2: ungrounded vs grounded, and the refusal path
make dark-proof           # §M3: detector, AIS, the space-time join, and the renders
make tool-proof           # §M4: the tools over MCP, and the local model chaining them
make agent-proof          # §M5: halts at the human gate, resumes in a different container
make demo                 # §6:  the recording above, live, ~60 s
make bundle-proof         # §M7: the transfer bundle — four refusals, and a restore
```

`make` with no target lists everything.

Two of those need an account, both free: `fetch-granules` wants an
[Earthdata login](https://urs.earthdata.nasa.gov) **with the ASF EULA accepted** — a valid
password on its own produces a redirect loop rather than a 401 — and `fetch-gfw` wants a
[GFW API token](https://globalfishingwatch.org/our-apis/tokens). `fetch-ais` needs neither.
Every fetch is checksummed against [`data/sources.yaml`](data/sources.yaml), resumes a partial
download, and re-runs as a no-op; `make fetch-granules VERIFY=1` re-hashes what is already on
disk instead of trusting its size. The default set is 2.8 GB — everything the proofs and the
demo need. `ALL=1` adds the two granules of a first attempt that pointed at the wrong orbit,
which are kept in the manifest because the mistake is worth being able to look at.

**On this machine**, the host runs ollama as a systemd service with `OLLAMA_KEEP_ALIVE=-1`,
which pins ~15 GB of the 3090 permanently and leaves the enclave's own ollama unable to load a
14B model. `make preflight` detects this and tells you to `sudo systemctl stop ollama`. There is
also a `make seed-models` shortcut that copies the blobs already on the host into the volume
instead of re-downloading them — a local convenience, explicitly not the documented path.

---

## The air-gap proof

No route to the internet, and the model answers anyway — in the same session. Reproduce with
`make air-gap-proof`.

```
NIGHTGLASS — air-gap proof   2026-08-03T17:29:00Z

----------------------------------------------------------------------
$ docker compose exec api curl -m 5 https://example.com
curl: (6) Could not resolve host: example.com

✓ blocked (curl exit 6)

----------------------------------------------------------------------
$ docker compose exec api curl -m 5 http://ollama:11434/api/tags   # inside the enclave
models: bge-m3:latest, qwen2.5:14b-instruct-q4_K_M

----------------------------------------------------------------------
$ chat completion against the local model
Synthetic Aperture Radar (SAR) is a radar technique that creates high-resolution
images regardless of weather or lighting conditions by synthesizing a large
antenna aperture, which allows it to operate effectively both day and night.

----------------------------------------------------------------------
No route to the internet. Inference ran anyway.
```

The failure mode is worth reading precisely. It is **`Could not resolve host`, not a timeout** —
an internal network's embedded DNS does not forward external queries, so the name never resolves
and no packet is ever sent. A firewalled system drops your traffic; this one has nowhere to send
it. Meanwhile `ollama`, `qdrant` and `postgis` resolve normally on the same network, and both
models are loaded and serving.

---

## Grounded answers, and the refusal path

60 documents, 1,814 chunks, embedded locally with bge-m3 and stored in Qdrant. Reproduce with
`make rag-proof`.

The argument for the whole document layer is one comparison. **Same model, same question, same
machine — the only difference is whether retrieval is on.**

```
$ nightglass-corpus ask "what is a dark vessel?" --ungrounded

UNGROUNDED — no retrieval, model priors only
--------------------------------------------
A "dark vessel" isn't a standard term in common usage or in specific fields
like biology, chemistry, or astronomy, so its meaning can vary depending on
the context it's used in. Here are a few possible interpretations:

1. **Literature and Fiction**: ... an object with mysterious or ominous
   qualities, often associated with supernatural powers or evil forces.
2. **Philosophy and Metaphor**: ... something that holds negative emotions,
   secrets, or darkness within it.
3. **Art and Symbolism**: ... an abstract concept such as sorrow, despair, or
   the unknown.
4. **Technology (Retrocomputing)**: ... a black case or enclosure for hardware
   components, emphasizing aesthetics over functionality.
```

Fluent, confident, four confident readings, and **not one of them maritime**. The model states
outright that the term "isn't a standard term in common usage". Nothing in its priors holds the
operational meaning, and nothing in its tone signals the gap — which is the entire argument for
retrieval over a curated corpus rather than a bigger model.

```
$ nightglass-corpus ask "what is a dark vessel?" --sources

GROUNDED — UNCLASSIFIED // SYNTHETIC
----------------------------------------
1. A dark vessel is a vessel detected by a sensor for which no corresponding
   Automatic Identification System (AIS) report can be found in the reference
   feed within a stated distance and time tolerance of the detection's position
   and the image acquisition instant.
   [intrep-2026-014-dark-vessel-definition#0000]
   [intsum-2026-021-dark-vessel-doctrine#0000]

sources
----------------------------------------
[intrep-2026-014-dark-vessel-definition#0000]  (UNCLASSIFIED // SYNTHETIC)
[intsum-2026-021-dark-vessel-doctrine#0000]    (UNCLASSIFIED // SYNTHETIC)
```

Same model, same question, answered from retrieved sources — with the chunk ids to check it
against, and marked `UNCLASSIFIED // SYNTHETIC` because the sources it actually cited carry that
caveat. Nothing propagated that marking by hand.

```
$ nightglass-corpus ask "How many dark vessels were detected off Madeira in 2019?"

GROUNDED — UNCLASSIFIED
----------------------------------------
Not supported by available sources.

8 chunk(s) were retrieved but none supported an answer.
```

The refusal is worth more than either answer. Eight chunks came back and none of them supported
a claim, so the system declined rather than assembling something plausible out of adjacent
material. The gap is deliberate and documented in `corpus/README.md`: no document in the corpus
reports vessel detections off Madeira, and adding one would quietly delete this test.

**The corpus is entirely English, and `bge-m3` is still the right embedding model** — but for a
reason this repository no longer demonstrates. It was chosen because deployments are national
and a corpus and its queries will not always share a language; six memos were originally drafted
in another language to exercise that, and they have since been rewritten in English. The
measured cross-language separation that justified the choice is recorded in `docs/NOTES.md` (0.842
cosine against a translated equivalent, 0.283 against unrelated text in the same language), and
nothing in the shipped corpus exercises it today. Stated plainly rather than left as an
unsupported claim.

```
$ nightglass-corpus search "how should AIS duplicate messages be handled before matching?"

0.6163  [imo-msc74-69-performance-standards#0023]   Resolution MSC.74(69) — …shipborne AIS
0.6156  [imo-a1106-29-ais-operational-use#0017]     Resolution A.1106(29) — onboard use of AIS
```

### The corpus

| Publisher | Docs | Contributes |
|---|---|---|
| ICEYE Ltd | 17 | SAR imaging geometry, azimuth, radiometry, geolocation accuracy, product levels |
| International Maritime Organization | 10 | The AIS carriage and operation obligation, its exceptions, LRIT, port state control |
| European Union (EUR-Lex) | 8 | The AIS obligation in EU waters, STS notification, AIS interruption as conduct |
| Copernicus EMS | 4 | Dated environmental context over the Lisbon AOI |
| Synthetic INTREP/INTSUM | 21 | Doctrine, tradecraft, AOI baselines, reporting conventions |

Three real documents do the load-bearing work, and they are why "dark" is a deviation from a
stated norm rather than jargon: **IMO A.1106(29)** (*"AIS should always be in operation when
ships are underway or at anchor"*, with a narrow master's-discretion exception that must be
logged and reported), **Directive 2002/59/EC Article 6** (*"shall maintain it in operation at all
times"* inside EU waters), and **Council Regulation (EU) 2023/1214**, which treats switching AIS
off as conduct in its own right.

Nothing real is committed to this repository. `corpus/sources.yaml` is a manifest of URLs;
`make fetch-corpus` downloads each one and records a sha256. Sources whose publishers grant no
reuse licence are marked `redistributable: false`, and the fetcher refuses to write them unless
the destination directory carries a `.gitignore` of `*` — the constraint is enforced by the code
rather than trusted to a comment. Details in [`corpus/README.md`](corpus/README.md).

### Acquisition is online; operation is not

Documents are acquired exactly the way model weights are: a profile-gated service, on a separate
network, invoked explicitly. `corpus-fetcher` is to documents what `model-puller` is to weights.
The enclave then mounts the corpus **read-only** and cannot fetch, cannot write, and does not
even carry a PDF parser — `pdftotext` is installed only in the `fetcher` image stage, because
the enclave never sees a PDF, only normalised markdown.

Three independent barriers, and the error message names which one you hit:

```
$ docker compose exec api nightglass-corpus fetch
fetch failed: cannot write to /app/data/corpus: [Errno 30] Read-only file system
`fetch` is a PROVISIONING step: it runs in the corpus-fetcher service, on the provision
network, via `make fetch-corpus`. Inside the enclave this directory is mounted read-only
and no publisher is reachable.

$ docker compose exec api nightglass-corpus fetch --out /tmp/probe   # writable — so it is the network's turn
          FAILED: ConnectError: [Errno -3] Temporary failure in name resolution
```

---

## Dark vessels: detection, and a space–time join

`make dark-proof` runs the whole chain over the Danish AOI — schema, scene, detector, AIS, the
query, then the renders. **Denmark, not Portugal, and that is the point of having two AOIs:** it
is the only one with free point-level historical AIS, so it is the only place a claim about the
matcher can be *checked* rather than asserted.

```
detections    35          in the Kattegat AOI, from one Sentinel-1 GRD granule
matched       21          an AIS vessel within 500 m of where SAR would have drawn it
dark          14          no AIS correspondence — 40.0%
```

### The detector is ours

VH channel, read in place from the SAFE zip through `/vsizip/` — no extraction, so six granules
cost 4.7 GB instead of 33 GB. `sigma0 = (DN² − noise) / A²` using the product's own calibration
and thermal-noise LUTs, then a two-parameter CFAR against block-censored clutter statistics,
connected components via `rasterio.features.shapes`, and positions from a **thin-plate-spline**
GCP transform.

That last one is worth a number. Fitting a polynomial through the 210 tie points — the obvious
thing — leaves a **mean 40 m and worst-case 185 m** geolocation error, because the geolocation
grid of a 250 km swath is not a polynomial surface. TPS interpolates through them exactly:
residual **0.000 m**, for 15× the transform cost, which is 0.15 s per 20,000 points. At a 500 m
match radius, 185 m of avoidable error is over a third of the budget.

Measured against AIS, restricted to the scene footprint and open water:

| AIS-reported length | vessels | detected within 500 m | recall |
|---|---|---|---|
| ≥ 200 m | 6 | 6 | **100%** |
| 100–200 m | 7 | 5 | 71% |
| 50–100 m | 3 | 3 | **100%** |
| **≥ 30 m** | **17** | **15** | **88%** |
| < 25 m | 37 | 3 | 8% |

The 8% on sub-25 m craft is not a defect — it is 10 m pixels meeting a 20 m hull.

### The correction that makes the match work

§5 asks the matcher to account for the offset between AIS report time and image acquisition.
There are two offsets, and keeping them apart is the substance of the fusion problem:

**Time.** A vessel at 12 kn covers 3.7 km inside a ±11 min window, so "nearest report in time"
is not a position. Each track is linearly interpolated onto the acquisition instant, in SQL,
with `LEAD` over the per-vessel track. Vessels without reports on *both* sides are dropped
rather than extrapolated — an extrapolated position that then fails to match would manufacture
a dark detection out of a gap in the feed.

**Geometry.** SAR places a target in azimuth by its Doppler, so a moving ship is drawn
`(R/V)·v_los` from where it actually was. Here R/V ≈ 115 s, so 12 kn across the range direction
is **~450 m** — most of the match radius, spent before the matcher does anything. The naive fix
is a wider radius; that is worse than it looks, because the radius is the entire boundary
between "matched" and "dark", so widening it to absorb a *predictable* offset buys false matches
at the same rate it avoids false darks. So it is computed, from the product's own annotation and
from AIS SOG/COG.

**The sign was derived one way and measured the other way, and the measurement won.**

| | <100 m | <200 m | <500 m | median |
|---|---|---|---|---|
| interpolated, no correction | 8 | 17 | 43 | 330 m |
| correction, sign **+1** (as derived) | 0 | 3 | 23 | 630 m |
| correction, sign **−1** (as measured) | **19** | **33** | **45** | **173 m** |

![azimuth displacement correction](docs/evidence/azimuth_correction.png)

The wrong sign is about as far wrong as the right sign is right — the signature of a real
systematic offset being corrected backwards. The derivation had assumed the processor places a
target at the azimuth time whose Doppler equals the observed one; it actually focuses at the
target's Doppler *zero crossing*. Leaving the sign as a parameter to be measured is what caught
it.

### Land is masked twice, and it has to be

The first mask is derived from the scene: land backscatters 10–15 dB above calm sea at VH, so a
thresholded block mean finds it with no auxiliary data at all. It handles the mainland.

It cannot handle skerries, and not because it is badly tuned. It must *open* the mask — erode,
then dilate — or every bright vessel becomes its own island and the mask deletes the detections
it exists to protect. Opening removes bright objects smaller than the structuring element, and a
100 m rock is exactly that. Run with the data-derived mask alone, the detector drew a tidy line
of "vessels" down the Swedish archipelago off Gothenburg with almost no AIS anywhere near it.

So the second mask is a real shoreline: **GSHHG at full resolution**, fetched at provisioning
time and clipped to the AOI — 149 MB downloaded, 713 KB kept. The trade-off is measured, not
guessed:

| shoreline buffer | detections | matched | dark | unmatched |
|---|---|---|---|---|
| 300 m | 45 | 22 | 23 | 51% |
| **1000 m** *(default)* | **35** | **21** | **14** | **40%** |
| 2000 m | 29 | 21 | 8 | 28% |
| 3000 m | 26 | 20 | 6 | 23% |

Going from 300 m to 3 km removes 19 detections — **17 of them unmatched and only 2 matched**.
Coastal detections are overwhelmingly not vessels, and the buffer costs almost no real ones.
1 km is where that stops being nearly free.

Read the last column carefully: it is the fraction of *detections* with no AIS correspondence,
and it does not fall below 23% even three kilometres offshore. That is a statement about this
detector's false-alarm rate, not about how many ships were running dark.

### Looking at it

A detector that is only ever counted is a detector nobody has checked. Both failures above were
invisible in the numbers and obvious in the first render, so `make render` is part of the
pipeline rather than a debugging afterthought.

![detections vs AIS](docs/evidence/map_result.png)

Matched detections carry an AIS fix inside them and sit in open water. The unmatched ones
cluster on the coastal edge — with two genuine open-water exceptions, which is exactly the kind
of thing an analyst should be handed.

Every detection at native resolution, and the AOI in radar geometry with the land mask drawn on
it, are in [`docs/evidence/`](docs/evidence). `make dark-proof` regenerates all of it into
`data/out/`; the committed copies are the snapshot these numbers come from.

### It generalises

Every parameter above was set while looking at one Danish scene, which is a real overfitting
risk. Running the same configuration unchanged over a Lisbon-AOI granule — different platform
(S1A), different sea state, different beam geometry:

| | Kattegat (S1D, 17 Jul) | Lisbon (S1A, 13 Jun) |
|---|---|---|
| water sigma0 / NESZ | −29.1 / −29.4 dB | −26.5 / −26.5 dB |
| land-masked | 51.6% | 33.0% |
| binding criterion | CFAR | CFAR |
| detections | 60 | 133 |

No parameter touched, and the [chips](docs/evidence/pt_chips.png) are unambiguous vessels — most
of them showing the vertical azimuth smear of a moving target. That smear is also why lengths
run large on this scene, and it is the same mechanism behind the weak length correlation
measured over Denmark.

### A second detector, over the identical granule

Denmark validates the matcher against AIS. Nothing there validates the *detector*, so the
Lisbon scene is cross-checked against Global Fishing Watch's published SAR detections — and
because each GFW feature id carries its source granule, that granule is one `make fetch-granules`
puts on disk.
This is detection-for-detection over the same pixels, not "they saw N in this box and we saw M".
`make fetch-gfw` (provision network, `GFW_TOKEN` from `~/.config/eo-credentials.env`), then
`make gfw-compare`:

```
ours           71 detections   (nightglass-cfar)
GFW            66 detections   (published layer, same granule)

both saw       49   (74% of GFW's, median separation 78 m)
GFW only       17   they found, we did not
ours only      22   we found, they did not
```

**78 m median separation between two independent detectors** — tighter than the 104 m against
DMA AIS, as it should be, since both are measuring pixels rather than pixels against a
transponder.

This comparison is what first caught the detector counting one vessel several times. Before the
merge step it reported 133 detections here, and 61% of the 84 GFW did not share sat within 200 m
of one we had both found — fragments of the same hull, not extra vessels. With merging on,
agreement is unchanged at 49 and the residue drops to 22, of which **none** is within 200 m of
an agreed detection: median distance to the nearest, 26 km. What is left is genuinely isolated —
extra sensitivity or extra false alarms, not double counting.

Denmark settled the question properly afterwards, and cheaper: 45 matched detections there
resolved to **18 distinct MMSIs**, and clustering at 100–300 m produced zero groups containing
more than one vessel. See NOTES finding 46 — the ground truth was on the bench the whole time.

Two detectors agreeing is weaker evidence than the AIS validation banked over Denmark — it says
the detector generalises, not that either is right, and the tool prints that sentence every time
so the number is never quoted without it. GFW's own `matched` flags on the 49 agreed detections
(45 matched, 4 not) are reported as *their* computation under CC BY-NC 4.0, never merged into a
`Match`: they matched against their AIS upstream, and claiming that as our correlation would be
claiming their work.

## The agent, and a gate that actually stops

`parse → plan → tools → correlate → draft_intrep → HUMAN_GATE → release`, as a LangGraph state
machine against a **Postgres checkpointer**. `make agent-proof` runs it end to end.

The halt is demonstrated the only way that means anything — the drafting container *exits*, and
a different one picks the run up:

```
$ make ask Q="Were there any vessels with no AIS correspondence on 17 July 2026?"
⏸  HUMAN_GATE — the graph has stopped
  marking             UNCLASSIFIED // SYNTHETIC // DRAFT — NOT RELEASABLE
  claims              18
  unsupported claims  0

$ psql -c "select thread_id, count(*), sum(length(blob)) from checkpoint_blobs group by 1"
    thread_id    | channels | state
-----------------+----------+--------
 ng-cad785dbbc71 |       10 | 104 kB

$ make approve T=ng-cad785dbbc71          # a different container, minutes later
resumed from persisted state — RELEASED
```

`MemorySaver` would satisfy the same API and keep the state inside the process that is supposed
to have stopped; a bare `input()` would block a thread and lose everything if it died. Being
precise about what this buys, because the obvious stronger claim is false: plain SQL gives you
the run's existence, its thread, where it stopped and how much state it holds. The *values* are
msgpack, so reading the payload goes through the checkpointer — which is what
`nightglass-agent show` does for the reviewer.

### Where the model is allowed to act

The interesting decisions in this graph are about where the model is kept *out*.

| node | who decides | why |
|---|---|---|
| `parse` | model | language, lookback, whether documents are needed — all recoverable if wrong |
| | **not** the model | **the bbox.** §3.1 makes the AOI configuration. A model inventing one searches the wrong ocean and returns cleanly |
| `plan` | nobody | facts about config and the database, all checkable |
| `tools` | model | which documentary context the report needs — bounded, with the max-iteration and repeat guards |
| `correlate` | nobody | runs deterministically over the configured AOI whatever the model did |
| `draft_intrep` | mixed | findings templated from the `CorrelationResult`; assessment generated, cited, scrubbed |
| `HUMAN_GATE` | the human | the only path to `releasable=True` |
| `release` | nobody | prose assembled from the report's own fields |

That split is a fix for a measured failure, not caution for its own sake. At M4 the model got
every per-scene count right, listed thirty real detection ids, and still summarised them as "of
the 60 detections … 15 were not" — conflating two scenes. No prompt fixes that reliably, so the
released answer is not generated: every line comes from a `Claim` or a computed caveat.

On the first end-to-end run the rate guard earned its place unprompted. The model wrote a
proportion into the assessment section, and the draft came back carrying a caveat nobody typed:

```
1 generated claim(s) were removed before drafting because they stated a rate
of unmatched detections.
```

The prompt layer and the schema layer both let it through. The check caught it.

### What the numbers do *not* say

- **40% unmatched is not a 40% dark-vessel rate.** Published work on Danish waters finds ~5%
  unmatched and ~0.4% genuinely dark after review. The residual here concentrates near shore and
  is dominated by clutter and isolated false alarms this pipeline has not separated from vessels.
- **That 40% used to read 25%, and the correction made it worse.** 45 matched detections over
  the Kattegat resolved to **18 distinct MMSIs** — one ship accounting for six of them — so the
  old denominator was padded with duplicates of vessels that *did* match. Merging them (§
  `DetectorConfig.merge_radius_m`, validated by AIS: zero clusters mix MMSIs at 100–300 m)
  collapses matched detections 45 → 21 while unmatched barely moves, 15 → 14. Which is itself
  informative: the unmatched residue is *isolated*, not fragments of real ships.
- **No AIS is loaded for Portugal, and the tools refuse rather than guess.** `ais_match` raises
  instead of returning 133 unmatched detections, and `correlate` returns the detections with the
  verdict withheld. "We searched a feed and found nothing" and "there was no feed to search" are
  different statements, and only the first one is a dark detection.
- **Detected length is not a reliable size estimate.** Detecting at 8σ and measuring at the same
  threshold gave lengths with **r = 0.015** against AIS — no relationship at all, because at
  that threshold the blob tracks peak brightness rather than hull. Re-growing each detection at
  2.5σ fixed the *bias* (median ratio 0.30× → **1.05×**) but the per-vessel scatter stays wide
  (**r = 0.271**). The median is usable; an individual number is not — which is why the INTREP's
  per-detection extents should be read as "something of roughly this size was here".
- **`rate_is_quotable` is a field the code checks**, not a sentence someone has to remember. It
  is true here only because every match came from DMA. It is also only *half* the check — see
  below.

---

## Six tools, two consumers

`make tool-proof` runs the whole of it: the MCP transport Claude Desktop attaches with, a tool
called over that transport, the local 14B model chaining three tools unaided, and the report
refusing to state the one number it must not.

The six §5 tools live in `src/nightglass/tools/` and are defined once. FastAPI serves them over
HTTP, FastMCP serves the same functions over MCP, and the M5 agent will call them in-process. A
tool that existed twice would be a tool that behaved differently depending on who asked.

### The boundary is crossed by a pipe, not a port

Claude Desktop runs on the host; the server runs in the enclave. They meet over stdio:

```bash
docker exec -i nightglass-mcp nightglass-mcp stdio
```

There is no published port, and there could not be: a container on a network declared
`internal: true` silently gets no host port mapping at all, even if one is written. `docker exec`
crosses the boundary without opening it, which is the honest way to do it.

That command is committed as `.mcp.json`, so a clone gives Claude Code the tools after one trust
approval. Verified with a real MCP client — `claude mcp list` reports ✔ Connected, and a headless
session drove `nightglass_status` and `correlate` over the pipe. `make mcp-tools` speaks the same
JSON-RPC over the same command without needing a client at all:

```
serverInfo   nightglass 0.1.0   protocol 2025-06-18
tools/list   7
    nightglass_status  ()
    stac_search        (bbox, start, end)
    detect_vessels     (scene_id)
    ais_match          (detections)
    doc_search         (query)
    correlate          (bbox, start, end)
    draft_intrep       (bbox, start, end)
```

### Provenance travels with the value

Not in a log line — on the object, so it survives crossing a tool boundary and losing its Python
type. A matched detection carries the reasoning that matched it:

```
MMSI 246541000, THUN GAZELLE, Tanker; position interpolated onto the acquisition
instant, azimuth displacement -213 m applied (separation before correction 181 m);
matched inside 500 m / ±11 min
```

and an unmatched one carries the caveat rather than leaving it to the reader:

```
no AIS correspondence in dma within 500 m and ±11 min of acquisition. This is a
statement about one feed at one instant, not about the vessel: revisit gaps,
terrestrial coverage limits, transponder failure, class B low power and vessels
not required to carry AIS all produce it. A lead, not a conclusion.
```

### The same tools, driven by the 14B model inside the enclave

A plain analyst question, no bbox and no scene id in the prompt, tools chosen by the model:

```
1. stac_search(...)                                    -> 2 scenes
2. detect_vessels({"scene_id": "S1A_IW_SLC__…_NWJ.csv"})
   ERROR  no scene '…' in the catalogue. Run `make scenes` …
3. ais_match({"detections": ["det-20260717-0523-…-0", …]})
   ERROR  10 detection id(s) are not in detect.detections … ids are assigned by
          the detector run and are not guessable.
4. stac_search(...)   REPEAT — same tool, same arguments, told to advance
5. detect_vessels({"scene_id": "S1D_…_BC13"})          -> 35 detections
6. ais_match({"detections": [35 real ids]})            -> matched 21, unmatched 14
```

The model invented a scene id and ten detection ids on its first attempt. **Every one was
refused**, with a message naming the fix, and the repeat detector caught the identical retry that
followed. It then used the real ids and reached 21 and 14 — the hand-checked SQL's numbers. A
tool surface that accepts a plausible-looking invented id is one whose provenance chain means
nothing; this is what it looks like when it does not.

That pair — a frontier model over a pipe from outside, a 14B model from inside, one tool
surface — is the part worth having. A capability that only works with a frontier model on the
other end of it is not an air-gapped capability.

### The number the tools will not give you

`CorrelationResult.rate_is_quotable` asks whether the AIS feed is complete enough to be a
denominator. Over Denmark it is: DMA is ground truth, and the field is **true**.

The report still refuses to state a dark-vessel rate, because that field only guards one side of
the fraction. It says nothing about whether the things in the *numerator* are vessels — and 40%
unmatched against a ~5% published base rate says they are substantially clutter and false alarms.
A matcher being validated does not make a detector's precision validated.

So there are two independent conditions, checked separately, and `DETECTOR_PRECISION_VALIDATED`
in `tools/intrep.py` is a constant sitting at `False` with a test as its tripwire — flipping it
takes a measurement, in the same commit. Three layers enforce the consequence, in increasing
order of how much they can be trusted: the templated findings never compute a proportion, the
generation prompt forbids one, and `scrub_rate_claims` removes any claim that states one anyway.
Only the third is a check rather than a request.

What comes out instead is a draft that carries its references and computes its own caveats:

```
marking   UNCLASSIFIED // SYNTHETIC // DRAFT — NOT RELEASABLE
claims    18, of which unsupported: 0

  • 23 of those detections correspond to a vessel reporting AIS, after interpolating
    each vessel's position onto the acquisition instant and correcting for SAR
    azimuth displacement; median separation 85 m.                  [scene×1; det×23]

  - No proportion of unmatched detections is stated in this report.
    The detector's precision is not validated. …
  - Danish Maritime Authority — AIS data. …
  - DRAFT — NOT RELEASABLE until reviewed and released at the human gate.
```

The DMA attribution is reproduced verbatim: a reworded licence condition is not the licence
condition.

### Two decisions worth naming

**`correlate` reuses a recorded detector run rather than re-reading the pixels.** This looks like
an efficiency argument and is a correctness one: detection ids are assigned *after* the length
and AOI filters, so re-running renumbers them, and `ais_match(["…:det_00005"])` would silently
mean a different vessel. Reuse is gated on identity of every recorded input — detector, version,
polarisation, AOI box, coastline, and every field of `DetectorConfig`, which is checkable because
`detect.runs.parameters` is jsonb. Measured: reused and recomputed give 60 detections that are
byte-identical on id, position, length, heading and confidence, in 0.9 s against 13.9 s.

**`correlate` is bounded to one scene per call.** Reading a granule takes 14–20 s. The
alternative — return a run id and let the client poll — needs a job table and a lifecycle, which
is the hidden state §5 rules out. Scenes the search found but did not correlate come back
carrying a note saying so and how to select them, so the bound is visible in the result.

---

## Crossing the gap: one tarball, and the ways it refuses

Everything above assumes the enclave was *built* somewhere with a network. A real site has no
route to ASF, to the Danish Maritime Authority, to a container registry or to PyPI — and one of
those four is on a clock. The DMA serves daily AIS on a rolling ~18-month window, so on the day
`aisdk-2026-07-17` ages out, the Danish validation stops being reproducible from
[`data/sources.yaml`](data/sources.yaml) and no amount of code fixes it. A bundle is what
outlives that.

```bash
make bundler          # build the static binary — the host never needs Go
make bundle           # ~18 GB: images, model blobs, wheels, granules, the AIS day
make verify-bundle    # stream it, check every byte against its own manifest
make restore-bundle   # verify, then docker load and place the data
make bundle-proof     # §M7: the whole round trip on a 100 MB fixture, ~60 s
```

**It is Go, and not because Go is pleasant.** The thing that unpacks an air-gapped bundle cannot
itself need a Python environment to exist first. `nightglass-bundle` is 3.4 MB, `CGO_ENABLED=0`,
statically linked, and `make bundle-proof` runs it inside a `docker:dind` container that has no
Python and no Go in it — which is the only way to show that claim rather than assert it. The
host does not need a Go toolchain either: the binary is built in `golang:1.26-alpine` and copied
out, the same way `make test` runs pytest in a container. `scripts/preflight.sh` gained no line.

### The manifest is the first member

A bundle is a tar whose first entry is `MANIFEST.json`, and every entry after it is hashed as it
streams past. That ordering is the whole design: it means `verify` is one sequential pass — no
seeking, no staging, one megabyte of memory across 18 GB — so a bundle can be checked *on a
pipe*, as it comes off the medium, rather than after it has been copied somewhere first.

```bash
cat /media/usb/nightglass-bundle-0.1.0.tar | nightglass-bundle verify -
```

The cost lands on `create`, which cannot write the first member until it has read every other
one. That is the right side to put it on: create runs once per bundle and verify runs every time
one moves.

Both halves of what it carries are content-addressed already. `docker save` emits an OCI layout
whose files are named `blobs/sha256/<hex>`; the Ollama store is `models/blobs/sha256-<hex>`. So
the manifest is a join over two existing content-addressed stores rather than a new format, and
for model blobs the filename *is* the checksum — manifest, filename and content all have to
agree, which catches a corrupt blob one way and a doctored manifest the other.

### Six ways it refuses

The failure that matters is not corruption. It is a bundle that streams cleanly to EOF and is
quietly **missing** something — because every check that asks "is what is here correct?" passes
on it. That is [finding 55](docs/NOTES.md)'s shape, truncation that looks like completion, in
the one tool whose entire job is to say *this is complete*.

| | refuses |
|---|---|
| 1 | bytes differ — sha256 mismatch, both digests printed |
| 2 | a member short at EOF — reported as **truncated**, not as a mismatch, because a partial copy and a corrupted one need different next steps |
| 3 | **a manifest entry that never appeared** — set equality, the direction a naive verifier omits |
| 4 | a member present that the manifest does not list |
| 5 | `MANIFEST.json` not first — refused rather than falling back to staging 18 GB |
| 6 | a path twice, or a member that is not a regular file |

`make bundle-proof` demonstrates four of them, three by damaging a real bundle — one flipped
byte, a truncated copy, and a rewritten archive with one blob removed and the manifest left
untouched — and the fourth on the `create` side: a granule edited **in place**, so it is still
exactly the size `data/sources.yaml` declares. Right size, wrong bytes is the failure a byte
count cannot see, and it is what the committed sha256 exists for. Exit codes separate the two
outcomes that must never be conflated: **1** the check ran and the answer is no, **2** the check
could not be run.

`restore` is the same single pass with the bytes written down instead of discarded. Every member
lands as `<path>.part` while it is hashed, and nothing is renamed and nothing is `docker load`ed
until the entire archive has passed all six checks — the discipline
[`spatial/archive.py`](src/nightglass/spatial/archive.py) already uses for an interrupted
download. A half-restored bundle is worse than an unrestored one: the images would load, the
system would start, and whatever was missing would be found by the first thing that needed it.

### What it weighs, and what does not travel

Measured on the real thing — 144 entries, **18,079,023,616 bytes**, built in 4 m 50 s:

| | entries | bytes |
|---|---|---|
| `images/` — 6 × `docker save` | 6 | 4,105,874,944 |
| `models/` — 8 blobs + 2 manifests | 10 | 10,145,798,095 |
| `wheels/` — 123 wheels + `requirements.txt` | 124 | 179,589,836 |
| `data/` — 3 granules (`role: required`) + the DMA day | 4 | 3,647,612,054 |

`verify` reads all of it in **11.6 s**, 7.6 s of which is CPU — sha256 on x86-64 runs at about
2.4 GB/s with the SHA extensions, so this is I/O-bound rather than hash-bound, which is the
result the streaming design was for. Treat it as a floor: the archive had just been written and
some of it was still in page cache.

`docs/HANDOVER_M7.md` budgeted ~21 GB for this and named the ollama image as "the whole cost,
essentially". Both came from the `docker images` SIZE column, which reports the *unpacked*
snapshot; `docker save` writes the compressed layers, and the two differ by 2.9× — the five
stack images are 11.57 GB by that column and **4.02 GB** in an archive. The real cost centre is
the model blobs at **56%**, and nothing can be done about those because GGUF is already
quantised. Recorded as [finding 57](docs/NOTES.md) because a number copied out of a tool's
summary column is a measurement of that column.

Each image entry also records the ref, the local image ID and the `repo_digest` Docker reports.
That last field means *the digest Docker gave us*, not *a digest you can pull*: Docker fills it
in for a locally built image too, and `nightglass/app@sha256:dc851b20…` resolves against no
registry on earth. Nothing available at build time separates the two cases — separating them
means asking a registry, which is precisely what a tool for air-gapped sites must not do.

`alpine:3` is in the image list and is not part of the stack: it is what writes the model volume
at restore, since a named volume lives under the daemon's storage root and the only portable way
to fill one is from inside a container. Four megabytes, and the bundle stops depending on a tool
it does not carry. `create` refuses if it is missing from `--images`.

Three files in the model volume do **not** travel. `id_ed25519` is an OpenSSH private key, mode
600 — Ollama's instance identity; bundling it would ship one private key to every site that
restores this, and a fresh Ollama generates its own on first run. `id_ed25519.pub` goes with it,
and `cache/model-recommendations.json` is the residue of [finding 14](docs/NOTES.md)'s
ollama.com call — a cache, and a trace of the one outbound path the enclave exists to prevent.
`make bundle-proof` asserts the archive contains no `id_ed25519`, rather than trusting that the
exclusion stayed written.

---

## Design decisions

| Choice | Reason |
|---|---|
| **Ollama inside the enclave, not the host service** | The host has ollama on `:11434` with the models already pulled, and pointing compose at it would save re-downloading ~10 GB. It also needs `host.docker.internal:host-gateway` — a deliberate hole in the exact boundary this project exists to demonstrate — and it breaks "clone and `make up`". The model server belongs *inside* the enclave because in a real deployment there is no host to borrow from. Cost is paid once via `make pull-models`. |
| Ollama over vLLM / TGI+TEI | **One service serves chat *and* embeddings**; vLLM and TGI are one-model-per-process and would need two containers. Ollama supports fully air-gapped operation, while vLLM needs network configuration to reach full isolation — which is the whole milestone. Models are content-addressed blobs in one directory, so the offline bundle is a tar of a folder, not an untangling of a HuggingFace cache — of `models/` only, as it turned out, because the volume also holds the instance's own SSH private key and that must not travel. Honest limit: Ollama serialises concurrent requests and vLLM does ~3.2× the throughput. Irrelevant for one analyst. The line is *"Ollama for the enclave, vLLM if this became multi-tenant."* |
| Qdrant over pgvector | Single binary, trivial offline deploy, no external dependencies. pgvector is already familiar from production work; Qdrant shows breadth and is a common air-gapped default. |
| bge-m3 for embeddings | Genuinely multilingual. Measured: a query against its translated equivalent scores **0.842** cosine, against unrelated text in the same language **0.283** — a 0.56 separation, so it keys on meaning rather than language. Deployments are national and a corpus will not always share a language with its queries; the shipped corpus is now all English, so this is insurance rather than a demonstrated feature. One-way, since changing it means re-embedding everything. |
| Qwen2.5 **14B** at q4_K_M | Fits consumer VRAM (~9 GB weights, ~15 GB resident with a 32k KV cache, on a 24 GB card), strong tool-calling, permissive licence. Verified chaining three distinct tools unprompted from a plain analyst question. |
| LangGraph over CrewAI | An explicit state machine with a genuinely interruptible node, which the human-in-the-loop gate needs. State persists and is inspectable while halted — a bare `input()` only blocks a thread. |
| PostGIS for geometry | Spatial correlation belongs in a spatial database, not in Python. The dark-vessel join is one query in `src/nightglass/spatial/sql/dark_vessels.sql` — track interpolation via `LEAD`, the azimuth-displacement correction via `ST_Project`, distances via `geography` casts so they come back in metres rather than degrees. It stays a `.sql` file so it can be read top to bottom and run a CTE at a time. |
| Scene as a STAC Item, not a bespoke table | `stac_search` (§5) is a catalogue query. Modelling the catalogue as STAC keeps the door open to pointing the same tool at a real STAC API — which is what a customer deployment has — rather than at a table only this project understands. The Item is stored whole in `jsonb`; the columns beside it are extracted for indexing. |
| A shoreline is a fourth provisioning input | Weights, documents, granules — and now GSHHG. The detector's own land mask structurally cannot separate a 100 m skerry from a 100 m hull, so an air-gapped deployment genuinely has to ship a coastline with it. Saying that is a better answer to "what does this need bundled" than pretending the list was three items long. |
| A committed manifest, gitignored bytes | `data/sources.yaml` carries a URL, a size and a sha256 for every external input; `data/` carries none of them. The fetchers verify against it and refuse a mismatch, so "the numbers in this README" and "the bytes on your disk" are the same claim. It is also what makes the two data fetchers auditable rather than trusted: a reviewer can check what they are pointed at without running them. |
| The bundler is Go, and the manifest is the first member | A static `CGO_ENABLED=0` binary is *why* Go is here rather than a sixth Python entry point: the thing that unpacks an air-gapped bundle cannot itself need a Python environment to exist first, and `make bundle-proof` runs it inside a `docker:dind` container that has no Python and no Go in order to show that rather than assert it. The host needs no Go either — it is built in `golang:1.26-alpine` and copied out, the way `make test` runs pytest in a container. Putting `MANIFEST.json` first is what makes `verify` one sequential pass: no seeking, no staging, one megabyte of memory across 18 GB, so a bundle can be checked on a pipe as it comes off the medium. The cost lands on `create`, which must read everything before it can write the first member — the right side to put it on, since create runs once per bundle and verify runs every time one moves. |
| TPS over polynomial GCP fit | Measured on a real granule: polynomial leaves 40 m mean / 185 m worst-case geolocation error, TPS leaves 0.000 m. 15× slower on a cost of 0.15 s per 20,000 points. |
| GRD not SLC | Vessel detection needs amplitude only. Phase is for interferometry, which is out of scope. |
| Agent as a one-shot, not a service | It runs to a human gate and halts. It has nothing to serve between invocations, so a daemon wrapper would exist only to satisfy a healthcheck. |

---

## Data sources and licences

| Source | Use | Licence / terms |
|---|---|---|
| **Sentinel-1 GRD** (ESA, via ASF) | SAR imagery, both AOIs — `make fetch-granules` | Free and open. Requires a NASA Earthdata account and one-time ASF EULA acceptance. |
| **Danish Maritime Authority AIS** | Point-level ground truth, validation AOI — `make fetch-ais` | Public S3, no credentials. See attribution below. |
| **eo-credentials** | `GFW_TOKEN` and friends live in `~/.config/eo-credentials.env`, never in this repo — `scripts/load-env.sh` loads it before `.env`, and a blank value in `.env` means *defer to central* rather than *override with empty*. | — |
| **Global Fishing Watch** SAR detections | Independent reference layer, demo AOI | **CC BY-NC 4.0** — non-commercial, attribution required. |
| **aisstream.io** | Live demonstration feed, demo AOI | Free tier. **Not ground truth** — see Limitations. |
| **ICEYE product documentation** | Document corpus — SAR technical reference | © ICEYE Ltd. Public documentation, **no open licence stated**. Fetched locally, `redistributable: false`, never committed here. |
| **IMO resolutions** | Document corpus — the AIS obligation itself | © IMO. Published via the IMO Knowledge Centre, **no open licence stated**. Same handling. |
| **EUR-Lex** (EU directives and regulations) | Document corpus — EU maritime law | © European Union, 1998–2026. Reuse authorised under Commission Decision 2011/833/EU with attribution. Only legislation printed in the paper Official Journal is authentic. |
| **Copernicus EMS** activation reports | Document corpus — AOI environmental context | © European Union, Copernicus Emergency Management Service. Free, full and open with attribution. |
| Synthetic INTREP/INTSUM memos | Document corpus — doctrine and tradecraft | Written for this project. Marked `UNCLASSIFIED // SYNTHETIC` in both header and metadata, and the marking propagates into anything citing them. Named no real vessel, operator or flag; identifiers use the unallocated MMSI prefix 999. |

> Contains data from the Danish Maritime Authority that is used in accordance with the
> conditions for the use of Danish public data.

---

## Limitations

Stated before being asked, because each one was tested rather than assumed.

- **No free historical, complete point-level AIS exists for Iberian waters.** GFW's ORBCOMM
  sublicence forbids redistributing AIS or any derivative, DGRM publishes nothing, EMSA
  restricts SafeSeaNet to national administrations, and the free satellite-AIS tiers went away
  in the 2025 Kpler/S&P consolidation. So over Portugal, GFW detections are a **reference
  layer** — an independent published product to cross-check against — not an independent
  correlation this system performed.
- **The live aisstream feed is thinned, measured, not guessed.** Same Kattegat bbox, same
  22-minute clock window, against DMA as ground truth: **770 vessels vs 132** — aisstream sees
  about **17%**, missing five in six. Independently, message throughput runs ~1.4 messages per
  vessel per 4 minutes against ~40 expected for a fully-observed class A vessel, roughly **3% of
  expected volume**. Vessel discovery had saturated by minute 10, so recording for longer does
  not close the gap. Absence from a thinned feed is not absence of transmission, so this source
  can demonstrate the matching mechanism but **cannot support a dark-vessel rate**.
- **Therefore: matched pairs over Portugal, rates only over Denmark.** The schema enforces this
  rather than leaving it to prose — `Match.source_is_ground_truth` travels with every match and
  `CorrelationResult.rate_is_quotable` is false unless all of them came from a ground-truth
  feed.
- **Completeness over Portugal cannot be measured at all.** The absence of a DMA equivalent is
  simultaneously why a live feed is needed there and why there is no reference to validate it
  against. Denmark is the only AOI where this is observable, which is much of what the Danish
  AOI is for.
- Single scene per AOI. No CFAR tuning. No accuracy claims beyond what was measured.
- **Only Class A and Class B AIS is treated as a vessel.** The DMA feed also carries base
  stations and aids to navigation — 7,857 rows in this acquisition window, 5.7% of it. They are
  excluded, because matching a detection to a navigation buoy would report a fixed installation
  as a vessel that had declared itself. The consequence is the honest one and it runs the other
  way: a detection sitting on a lit beacon is reported **unmatched**, and is part of the 40%.
  This rule was missing from the code until M6 — the hand-cut CSV used through M3–M5 had it
  baked in and nothing said so, which is finding 50 and much of the argument for the fetchers.
- **`make fetch-ais` will eventually stop working, and the code cannot fix it.** The DMA S3
  bucket serves daily files on a rolling window of roughly eighteen months. `aisdk-2026-07-17`
  is inside it today; when it ages out, the Danish validation becomes unreproducible from this
  manifest and the archive would have to be re-pointed at a monthly file. The granules do not
  have this problem — the Sentinel-1 archive at ASF is permanent. Stated because a manifest
  invites the assumption that everything in it is permanently retrievable, and one half is not.
- **40% of detections unmatched is not a 40% dark-vessel rate.** Published work on Danish waters
  finds ~5% unmatched and ~0.4% genuinely dark after review. The excess concentrates near shore —
  the shoreline-buffer sweep above removes 17 unmatched detections against 2 matched between
  300 m and 3 km — and it does not fall below 23% even three kilometres offshore. The honest
  reading is that the *matcher* is validated (21 matches, median 104 m, every AIS vessel over
  200 m in the footprint recovered) and the *detector's* precision is not good enough to quote a
  rate from. This figure was 25% until duplicate detections of the same hull were merged; the
  correction moved it the wrong way, which is why it is stated rather than smoothed.
- **Detected length is a size band, not a measurement.** Median ratio against AIS-reported length
  is 1.05×, but per-vessel correlation is only **r = 0.271**. It is used as a filter and
  reported as an attribute; it is not evidence about a specific vessel.
- **Heading is reported only when the blob is genuinely elongated**, and is ambiguous by 180°
  even then — a SAR blob has no bow. The first version returned a heading for every detection
  and the values clustered on two figures 90° apart, which was a picture of the pixel grid
  rather than of a fleet.
- **The land mask is a coastline plus a buffer, not a hydrographic product.** Vessels genuinely
  within 1 km of shore are not reported at all. Harbour traffic is outside what this sees.
- **Azimuth displacement is corrected; range migration and wake effects are not.** The
  correction uses one scene-mean platform speed and per-detection slant range and incidence; it
  does not model the vessel's own acceleration or a squinted geometry.
- **A third of the document corpus is synthetic, and it is the third that defines the terms.**
  The doctrine, the AOI baselines and the reporting conventions are memos written for this
  project; the regulatory grounding underneath them is real. Answers built on the synthetic
  material are marked `UNCLASSIFIED // SYNTHETIC` automatically, so the distinction is visible
  in the output rather than buried here — but it is a real limitation, not just a marking.
- **Retrieval quality is demonstrated, not measured.** There is no labelled question set and no
  recall@k number, because building an honest one over a corpus I wrote half of would mostly
  measure my own phrasing. What is verified is that every citation resolves to a real chunk and
  that unsupported questions refuse; what is shown is a handful of transcripts.
- **The refusal path catches unsupported claims, not wrong ones.** A claim that cites a real
  chunk which does not actually say what the claim says would survive verification. Guarding
  that needs an entailment check against the cited span, which is on the three-weeks list.
- **The bundle's wheelhouse does not make the image rebuildable offline.** §M7 words the bundler
  as "`docker save` + pip wheelhouse + model blobs", which invites the reading that the three
  together reconstitute the system from source. They do not. The runtime image also needs
  `curl`, `ca-certificates` and `libexpat1`, plus `poppler-utils` in the fetcher stage, and a
  wheelhouse pins none of them — an offline `docker build` would still go looking for a Debian
  mirror. What makes a restored site *run* is the saved images. What the 172 MB of wheels buys
  is smaller and real: `pip install --no-index --find-links`, to patch a dependency inside a
  running container without a network. Stated because that gap is otherwise found the hard way.
- **`nightglass-bundle verify` proves integrity, not authenticity.** It proves the bundle you
  have is the bundle that was built, given one manifest digest you obtained by another route. It
  says nothing about who built it, and there is no signature anywhere in the format.
- **A genuinely cold `make pull-models` is still untested**, open since M1 and unchanged by any
  of this. The bundle routes around it — a site restoring from one never runs the pull path — so
  the gap is now less likely to be hit and exactly as real as it was.
- **Dark ≠ guilty.** See the framing at the top.

**Related work.** Magalhães, Falcão & Barbosa (2025), IST Lisbon — Sentinel-2 optical vessel
detection with YOLO. NIGHTGLASS is the SAR complement to active regional optical work, and the
contrast is the point: optical fails at night and under cloud, which is the entire reason SAR
exists for this mission.

---

## What I'd do with three more weeks

- Real CFAR with sea-state adaptation, rather than one threshold per block
- **Fix coastal precision properly.** The shoreline buffer is a blunt instrument that trades
  real vessels for clutter. A hydrographic layer with harbour limits, aids to navigation and
  fixed-structure catalogues (wind farms, platforms) would let near-shore detections be
  classified rather than excluded — and that is where the 40% unmatched figure actually lives.
- **Per-detection azimuth time.** A granule spans 25 s and the AIS is currently interpolated to
  the scene mid-time. A detection's own along-track position says when it was really imaged,
  worth ~130 m at 10 kn — inside the current radius, but it is free accuracy.
- Multi-scene temporal tracking, so a detection becomes a track
- Coherent change detection with SLC pairs
- **Sign the bundle.** `nightglass-bundle verify` proves integrity — this is the bundle that was
  built — against one manifest digest carried out of band. It proves nothing about *who* built
  it. Authenticity needs key custody, revocation and an offline root, which is a programme
  rather than a subcommand, and claiming it without those would be the one dishonest line in
  this file.
- **An offline `docker build`.** The wheelhouse covers Python and nothing else; the runtime image
  also needs four apt packages, so rebuilding from source inside an enclave still wants a Debian
  mirror. Carrying one is a different artifact from carrying wheels.
- SBOM via syft and image digests pinned rather than tags. Worth knowing before starting: the
  bundle manifest records a `repo_digest` for all six images, and for the two built here that
  value resolves against no registry, because they were never pushed. Docker reports the field
  either way and nothing local distinguishes them — telling them apart means asking a registry,
  which is the one thing this tool must not do. Digest pinning is therefore a real improvement
  for four of the six and a no-op for the two that matter most.
- **Entailment checking on citations.** Verification currently proves a cited chunk *exists*.
  Proving the chunk *supports the claim* needs a second pass — a natural-language inference
  model, or the chat model re-asked per claim against its cited span alone — and that closes
  the one gap the current check cannot see.
- **Hybrid retrieval.** bge-m3 dense vectors alone lose exact identifiers: an analyst searching
  for `A.1106(29)` or an MMSI wants lexical match, not semantic neighbourhood. BM25 alongside
  the dense index, fused, is the standard fix and Qdrant supports it natively.

---

## Repository layout

```
docker-compose.yml        the enclave — one internal network, no egress
.mcp.json                 the MCP attach, committed — clone and Claude Code has the tools
docker/                   application image (runtime · fetcher · dev), postgis init
  Dockerfile.bundler      golang:1.26-alpine -> scratch; the host never needs Go
scripts/                  preflight, the five proofs, the demo, its pacing and its check
bundler/                  Go. the offline transfer bundle — a second language, on purpose
  cmd/bundle/main.go      nightglass-bundle create|verify|restore|inspect
  internal/manifest/      the manifest, and every way it can be internally incoherent
  internal/bundle/        create · verify · restore — one streaming pass, six refusals
  internal/sources/       reads data/sources.yaml, so a bundle cannot outrun M6's manifest
  internal/dockercli/     docker save · load · the volume round trip, over os/exec
corpus/
  sources.yaml            manifest of the 39 public documents — URLs, not documents
  synthetic/              21 INTREP/INTSUM memos, UNCLASSIFIED // SYNTHETIC, committed
  README.md               what the corpus is, licences, and the deliberate gap
src/nightglass/
  config.py               AOI resolution — the only place a bbox is named
  schemas.py              the §5 tool contracts, with provenance attached
  display.py              wrapping — the two renderers that show a model at work
  rag/
    fetch.py              ONLINE. the only module here that opens an outward socket
    extract.py            pdf · markdown · activation JSON -> text worth embedding
    chunking.py           structure-aware, heading-path aware, stable chunk ids
    embed.py              bge-m3 via the enclave's own ollama
    index.py              Qdrant: ingest, and §5's doc_search
    answer.py             grounded generation, citation verification, refusal
    cli.py                nightglass-corpus fetch|ingest|search|ask|stats
  spatial/
    archive.py            ONLINE. the manifest, and a resumable checksummed fetch
    safe.py               Sentinel-1 SAFE read in place: annotation, calibration, noise, GCPs
    detect.py             the vessel detector — CFAR, land mask, TPS geolocation
    geodesy.py            bearings, and the azimuth-displacement physics
    coastline.py          ONLINE fetch + AOI clip; the second land mask
    ais.py                source adapters — DMAFileSource · GFW · CustomerFeedSource
    db.py                 PostGIS access; the SQL lives in files, not f-strings
    sql/001_schema.sql    stac.scenes · detect.runs+detections · ais.positions
    sql/dark_vessels.sql  §M3's join — interpolate, correct, match. Readable on its own.
    gfw.py                ONLINE fetch of GFW's published detections; the cross-check
    render.py             chips, scene overview, map view — the evidence
    plots.py              validation charts
    validate.py           measure the detector against DMA ground truth
    cli.py                nightglass-spatial fetch-granules|fetch-ais|scenes|detect|dark|…
  tools/
    spatial.py            stac_search · detect_vessels · ais_match · correlate
    documents.py          doc_search — the M2 retriever behind the same boundary
    intrep.py             draft_intrep, and the two-sided guard on the rate
    chaining.py           the local model driving the tools; max-iters + repeat detector
    cli.py                nightglass-tools list|call|chain
  api/                    FastAPI — the six tools over HTTP
  mcp/                    FastMCP — the same six over stdio + sse
  agent/
    graph.py              parse → plan → tools → correlate → draft_intrep → GATE → release
    main.py               nightglass-agent ask|pending|show|approve|reject
data/
  sources.yaml            COMMITTED. url + bytes + sha256 for all 6.2 GB of it
  raw/ interim/ out/      gitignored — the granules, the AIS, the rendered evidence
docs/
  EXECUTION_SPEC.md       what to build
  PRE_DEV_GUIDE.md        verified data access paths
  NOTES.md                decisions, corrections, measurements — 60 numbered findings
  HANDOVER_M6.md          the brief M6 was built from, and what came of it
  HANDOVER_M7.md          what is left, and why it is optional
  superpowers/specs/      the bundler's design, written before it was built
  demo.cast · demo.mp4 · demo.gif    the walkthrough: record, watch, embed
  evidence/               committed renders — the snapshot the numbers come from
```

Licence: Apache-2.0.
