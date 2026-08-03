# NIGHTGLASS — Execution Spec

**Air-gapped SAR intelligence assistant.** Dark-vessel detection and reporting, fusing SAR imagery with AIS and an intelligence document corpus, exposed as MCP tools, running with no route to the internet.

Deliberately shaped as the open-source shadow of **ICEYE Ocean Vision Detect** — a real product that detects unreported vessels and ship-to-ship transfers.

---

## How to work on this

Build continuously. Nelson reads along and asks questions as it goes — that's the point, not an interruption. He has an ICEYE interview Wed 5 Aug and a timed take-home task after it, so **reps now are the preparation**. Getting fluent in this stack before a clock starts is worth more than any document.

Milestones below are ordered, not scheduled. There's no deadline attached to any of them.

**Read `PRE_DEV_GUIDE.md` first.** It has the verified data access paths, exact commands, and the gotchas that will otherwise cost hours. Real Sentinel-1 and real AIS are both reachable without meaningful friction — there is no need for synthetic data at any point.

### Momentum rules

These matter more than any individual requirement in this spec:

- **Never block on an external dependency.** If something genuinely stalls, stub that one interface and keep going — but check `PRE_DEV_GUIDE.md` first, because most apparent blockers there turned out not to be real.
- **If something fails twice, write down what you tried in `NOTES.md`, move to the next milestone, come back later.** A stalled milestone shouldn't stop the others.
- **Reversible decisions don't need approval.** Make the call, note it in `NOTES.md`, keep moving. Library choice, schema shape, file layout — just pick one.
- **Prefer a working simplification to a stalled correct thing.** A dumb version that runs is more useful than an elegant version that doesn't, and it can be replaced later.
- **Partial credit counts.** If a "done when" check half-passes, that's still progress. Note the gap and continue.

When a design decision is genuinely ambiguous, resolve it toward *"what would an intelligence analyst need in order to trust and audit this?"* — not toward elegance, throughput, or generality.

### Two things worth keeping

Everything else is negotiable. These two are the actual demonstration:

1. **The embedding model runs locally.** Calling a hosted embedding API is the most common way an air gap gets broken in practice. Chat model, embedding model and vector store all inside the enclave.
2. **Every generated claim carries provenance** — scene ID, detection ID, document ID, timestamp — and the system says so when it can't cite. See §7.

---

## 1. Out of scope

Skip these. Each eats a day and demonstrates nothing the JD asks for:

- Training or fine-tuning any model
- A polished web frontend (a minimal read-only view is fine late on; CLI + MCP is enough)
- SLC processing, interferometry, coherent change detection
- Authentication, multi-tenancy, RBAC
- A hand-written vector store or CFAR implementation
- Kubernetes beyond the optional M7
- Streaming, real-time ingest, schedulers
- Supporting more than one AOI well

---

## 2. Stack

```
docker compose — internal network, no egress from app services

  ollama      qwen2.5:14b-instruct-q4_K_M  chat  (24 GB VRAM available — 14B fits)
              bge-m3                        embeddings (multilingual, PT/EN)
  qdrant      intel document chunks
  postgis     STAC scene catalog · AIS tracks · vessel detections
  api         FastAPI — the six tools (§5)
  agent       LangGraph — plan → tools → correlate → draft → HIL gate
  mcp         FastMCP — same six tools over MCP (stdio + SSE)
  bundler/    Go CLI — offline transfer bundle + checksum manifest (M7)
```

**Model tags matter here.** `qwen2.5:14b-instruct-q4_K_M` and `bge-m3`. Plain `q4` is not a valid Ollama tag — the real ones are `q4_0`, `q4_1`, `q4_K_M`, `q4_K_S`. If a pull 404s, that's usually why. 14B (~9 GB) plus bge-m3 (~1.2 GB) leaves ample headroom on a 24 GB 3090.

Substitute freely if something won't install. Note the swap in `NOTES.md` and move on.

**Design decisions.** Nelson gets asked "why did you choose that?" in the hiring manager round, so keep this table in the README with reasoning intact:

| Choice | Reason |
|---|---|
| Qdrant over pgvector | Single binary, trivial offline deploy, no external deps. Nelson already runs pgvector in production — Qdrant shows breadth and is a common air-gapped default. |
| bge-m3 | Genuinely multilingual (100+ languages incl. Portuguese). Deployments are national; corpus and queries won't be English-only. |
| Qwen2.5 **14B** | Fits consumer VRAM at q4_K_M (~9 GB of 24 GB, leaving room for bge-m3 at ~1.2 GB), strong tool-calling, permissive licence. **[CORRECTED]** — this row said 7B while §2's stack block and §3.4 both specify `qwen2.5:14b-instruct-q4_K_M`. 14B is the decision; 24 GB VRAM affords it. |
| **Ollama** over vLLM / TGI+TEI | **One service serves chat *and* embeddings** — vLLM and TGI are one-model-per-process, so they'd need two containers. Ollama and llama.cpp support fully air-gapped operation; **vLLM requires network configuration** to reach full isolation, which is the entire M1 milestone. Models are content-addressed blobs in a single directory (`OLLAMA_MODELS`), so the M7 bundler is a tar of one folder rather than an untangling of a HuggingFace cache. Honest limit: Ollama serialises concurrent requests and vLLM does ~3.2× throughput — irrelevant for a single-analyst demo, and §1 excludes scale explicitly. The defensible line is *"Ollama for the enclave, vLLM if this became multi-tenant."* |
| LangGraph over CrewAI | Explicit state machine with an interruptible node — needed for the human-in-the-loop gate. Also closes a framework the JD names. |
| PostGIS for geometry | Spatial correlation belongs in a spatial database, not in Python. |
| GRD not SLC | Vessel detection needs amplitude only. Phase is for interferometry, which is out of scope. |

---

## 3. Data

**Full detail, with verified commands and gotchas, is in `PRE_DEV_GUIDE.md`.** Summary here.

### 3.1 Two AOIs — and why

**No free _historical, complete_ point-level AIS exists for Portuguese waters.** Verified: GFW's ORBCOMM sublicence forbids redistributing AIS "or any portion or derivative thereof", DGRM publishes nothing, EMSA restricts SafeSeaNet to national administrations, and satellite AIS free tiers disappeared in the 2025 Kpler/S&P consolidation. Denmark is the only European state publishing free point-level **historical** AIS.

> **[CORRECTED 2026-08-03]** This section originally claimed, flatly, that *no* free
> point-level AIS exists for Portuguese waters. That is too strong and would not survive
> being challenged. **aisstream.io** serves free real-time global point-level AIS over
> WebSocket and does cover Portuguese waters — verified by pulling MMSI 263701390
> `GIL VICENTE` (263 = Portuguese flag) from the Tagus estuary.
>
> Two qualifiers keep the two-AOI design intact:
> - **No archive.** Real-time only, so it cannot serve a past acquisition. It works only by
>   recording forward and pairing with a future overpass.
> - **The feed is incomplete, measured against ground truth.** Same Kattegat bbox, same
>   22-minute clock window: **DMA 770 vessels / 80,388 rows vs aisstream 132 vessels /
>   609 messages.** aisstream sees **~17% of vessels — it misses 5 in 6.** Vessel discovery
>   had *saturated* by minute 10, so longer recording does not close the gap; those vessels
>   are not being received at all.
>
> Consequence for §7's honesty requirement: absence from this feed is **not** absence of
> transmission. Using it as matcher ground truth would mark ~83% of detections dark — not the
> 40%-dark failure §3.2 warns about, but worse. Use it to demonstrate the mechanism and to
> make `CustomerFeedSource` a real adapter rather than a stub — **never** as ground truth, and
> **never** to quote a dark-vessel rate.
>
> **Report matched pairs over Portugal, rates only over Denmark.** "Here are N detections I
> matched to self-collected AIS, with the space–time reasoning shown" is fully defensible.
> "X% were dark" is not supportable from this source.
>
> Note the structural trap: completeness over Portugal **cannot be measured**, because the
> absence of a DMA equivalent is both why aisstream is needed there and why there is no
> reference to validate it against. Denmark is the only AOI where this is observable — which
> is a large part of what the Danish AOI is actually for. Measurements in `NOTES.md`.

So:

| | **Portugal — demo AOI** | **Denmark — validation AOI** |
|---|---|---|
| SAR | Sentinel-1 GRD, own detector | Sentinel-1 GRD, own detector |
| AIS | none free — GFW SAR detections as reference layer | **real point-level DMA AIS** |
| Proves | mission relevance | the matcher actually works |

**This makes AOI parameterisation a hard design requirement, not a nicety.** Everything — bbox, time window, AIS source adapter, detector config — comes from config, never hardcoded. Two AOIs, one codebase, swap by config file. That's also exactly what "deploy into customer environments" means, so it's worth doing properly.

The AIS layer needs a **source adapter interface** with at least two implementations: `DMAFileSource` (point-level CSV) and `GFWDetectionSource` (reference detections, already AIS-matched upstream). A third stub, `CustomerFeedSource`, that raises "not configured" is worth having — it makes the deployment story concrete.

> **[UPGRADE 2026-08-03] `CustomerFeedSource` no longer has to be a stub.** aisstream.io gives
> a real live WebSocket feed, so it can be an actual streaming adapter. Worth doing: it is the
> only non-file-based implementation, so it exercises the interface far harder than two CSV
> readers — different lifecycle (long-lived connection, reconnect-with-backoff), different
> failure modes (drops, no SLA), different time semantics (arrival vs report time). An
> abstraction validated only against two file readers is barely validated.
>
> It also turns "in a real deployment the customer brings the feed" from a talking point into
> working code. **But it must be labelled a demonstration source, not ground truth** — see the
> thinning measurement above and §7's "a dark detection is a lead, not a conclusion".

**Portugal:**
- ~~Sentinel-1 coverage over Portuguese waters is **unverified**~~ → **[VERIFIED 2026-08-03] Coverage is healthy; this risk is retired.** ASF `GRD_HD` counts, 1–15 Jun 2026: Lisbon/Tagus **46**, deep Atlantic **34**, Porto/Leixões **31**, Algarve **28**, Azores 5, Madeira 4. Coastal *and* open ocean both fine — the concern that S1 doesn't image open ocean did not hold here. Full June over Lisbon: **81 granules, 100% dual-pol VV+VH**, so VH is available for detection on every scene.
- GFW SAR detections via free token; `gfw_sar_vessel_detections(spatial_resolution="HIGH", temporal_resolution="HOURLY", group_by="MMSI", filter_by="matched='false'")`. CC BY-NC 4.0.
- **GFW's SAR product has been in outage since 3 July 2026** (S1A retirement / 1C-1D migration). Use a historical date before July 2026.
- **Be explicit in the README that GFW detections are a reference layer, not your computation.** You ran your own detector on real scenes and cross-checked against an independent published layer. That's a real result. Claiming independent AIS correlation over Portugal is not, and it would unravel in a technical round.

**Denmark:**
- ~~`https://web.ais.dk/aisdata/…`~~ **[CORRECTED — that host is DEAD.]** Its TLS cert expired 12 Jun 2025 and the server now hangs; the system moved to the Danish Emergency Management Agency. Use the public S3 bucket, **plain `http://`** (`https://` fails on this host):
  `http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/aisdk-YYYY-MM-DD.zip`
  The official `aisdata.ais.dk` page is a JS-rendered listing — scraping its HTML yields nothing. Use the S3 REST API (`?list-type=2&delimiter=/`). Dailies at bucket root, rolling ~17 months (`2025-02-27` → `2026-07-31`); `YYYY/` prefixes hold older monthly archives.
- **~850–990 MB zipped → 3.1–5.5 GB**, 18–24 M rows/day (not 300–500 MB).
- AOI: Kattegat / the Belts, roughly lat 55.5–57.5, lon 10.5–12.5.
- Date 1–3 weeks back — DMA publishes with a **~3-day** lag (not 48 h; verified from `Last-Modified`).
- Overpass windows: descending ≈ 05:23–05:40 UTC, ascending ≈ **16:44**–17:09 UTC. **[CORRECTED]** A 16:52 lower bound drops ~⅓ of ascending passes (16:44 appears ×10 across 72 July 2026 granules). Slice AIS to those, then ±5–10 min of granule `startTime`.
- **[NEW] Portugal overpass windows:** descending 06:33–06:51 UTC, ascending 18:26–18:43 UTC.

**Both:** scene discovery via ASF Search API, no auth. Download via free NASA Earthdata account and `~/.netrc`. **~780–990 MB** per dual-pol IW GRDH **[CORRECTED from 1.7–2.1 GB — that figure describes CDSE's original SAFE, not what ASF serves]**. ASF serves classic `.zip` SAFE, avoiding CDSE's COG_SAFE and deferred-data complications.

> **[CORRECTED] A valid Earthdata account is not enough to download.** Requests 401 with
> `"Could Not Login. Be sure to agree to the EULA."` Authorize **Alaska Satellite Facility
> Data Access** once at <https://urs.earthdata.nasa.gov/profile> and accept the Sentinel EULA.
> Diagnostic: a bearer token returning 200 against `cmr.earthdata.nasa.gov` while the datapool
> 401s means the EULA, not the credentials. curl also needs `--location-trusted` to carry
> credentials across the redirect.

xView3 is **not used** — credentialed, and its "dark" labels are AIS-derived rather than a real feed.

Constellation note: **S1A ended operations 29 June 2026.** S1C and S1D are the operational pair, repeat pattern shifted a day from the old A/B cycle — query the catalogue rather than trusting pre-2026 revisit tables.

### 3.2 Things that will cost hours if missed

- **The GRD measurement TIFF has no CRS and no geotransform.** Georeferencing is a geolocation grid in `annotation/*.xml`. Assuming north-up silently produces wrong positions, so nothing matches AIS and it looks like a broken matcher.
- **DN is not backscatter.** Calibrate via the sigma-nought LUT; subtract thermal noise for water. `sigma0 = DN²/A²`, `dB = 10·log10(sigma0)`.
- **Use VH for detection** — lower background over water, better ship contrast.
- **The AIS CSV's first column is literally `# Timestamp`**, hash included, and dates are `%d/%m/%Y`. **[VERIFIED]** — also confirmed 26 comma-separated fields, and lat/lon use decimal **points**, despite the DMA README's decimal-comma examples.
- ~~**DMA's timezone is undocumented.** Run the DST histogram test~~ → **[RESOLVED 2026-08-03: it is UTC.]** DST histogram on `aisdk-2026-03-29` is smooth through the spring-forward (02:00 = 712,161, between its neighbours 711,596 and 696,666) — no one-hour hole. Parse as UTC; no conversion. DMA still documents this nowhere, so the finding lives in `NOTES.md`.
- **Azimuth displacement** — moving ships are shifted along-track by hundreds of metres. A symmetric match radius manufactures false darks. Make the tolerance asymmetric or velocity-correct from AIS SOG/COG.
- **[NEW] 71% of DMA rows are exact rebroadcast duplicates.** Measured on the 22-min Kattegat window: 134,851 raw → 38,945 deduped, worst case **21 identical copies** of one message. **Dedup on `(MMSI, timestamp, lat, lon)` before matching.** After dedup, 42.8 distinct timestamps per vessel — ample to interpolate to the acquisition instant rather than taking nearest-in-time raw.
- **[NEW] Python's stdlib `netrc` crashes** on a `~/.netrc` containing a `token <JWT>` line (`NetrcParseError: bad follower token 'token'`). `curl -n` tolerates it. Bites inside download helpers.

**Sanity check:** published work on Danish waters finds ~5% of detections unmatched and ~0.4% genuinely dark after review. If the pipeline reports 40% dark, it's broken.

### 3.3 Document corpus

Copernicus EMS activation reports (public PDFs — EMSR861 and EMSR864 are already familiar), public IMO circulars, EU shipping sanctions notices. Top up to 40–60 documents with short INTREP/INTSUM-style memos about the AOI, each marked `UNCLASSIFIED // SYNTHETIC` in header and metadata.

### 3.4 Models

```
ollama pull qwen2.5:14b-instruct-q4_K_M
ollama pull bge-m3
```

### 3.5 Attribution

DMA data requires this line in the README:

> "Contains data from the Danish Maritime Authority that is used in accordance with the conditions for the use of Danish public data."

---

## 4. Milestones

Ordered. "Done when" is a check to aim at, not a gate — partial passes are fine, note the gap and keep going.

### M0 — Scaffold
Repo, `docker compose`, `.env.example`, Makefile, README skeleton, `NOTES.md`.

**Done when:** `make up` brings all containers up healthy.

### M1 — Offline inference
Ollama serving both models. App services on an `internal: true` bridge network with no egress.

**Done when:** `docker compose exec api curl -m 5 https://example.com` fails while a chat completion against Ollama succeeds, in the same session. Capture that terminal output — it goes in the README and the demo.

### M2 — Document RAG with citations
Ingest corpus → chunk → embed with bge-m3 → Qdrant. Retrieval returning chunks with document ID, title, classification marking. Generation grounded strictly in retrieved context.

**Done when:** a question returns an answer whose factual claims map to retrievable chunk IDs, and an unanswerable question produces an explicit "not supported by available sources" rather than a guess.

> **[VERIFIED 2026-08-03] bge-m3 cross-lingual retrieval works, which §6 depends on.**
> A Portuguese query must retrieve English documents. Measured cosine similarity, 1024-dim:
>
> | pair | cosine |
> |---|---|
> | *"embarcação não declarada… ao largo de Lisboa"* ↔ its English equivalent | **0.842** |
> | same Portuguese text ↔ unrelated Portuguese text | 0.283 |
>
> A 0.56 separation between cross-lingual-same-meaning and same-language-different-meaning
> means the embedding keys on meaning, not language. The Portuguese demo query will retrieve
> an English corpus.
>
> **This choice is one-way:** switching embedding models later means re-embedding everything.
> Settle it now.

### M3 — Spatial layer
PostGIS schema. Scene as a STAC item, detections loaded, `ais_positions` loaded from the DMA daily file filtered to the acquisition window and footprint.

**Done when:** one SQL query returns detections with no AIS correspondence inside a space–time window. Worth getting this right as plain SQL, hand-checked, before the agent touches it — debugging a spatial join through an LLM is miserable.

### M4 — Tool layer + MCP
The six tools (§5) as FastAPI endpoints. FastMCP server over the same functions.

**Done when:** the tools are callable over MCP from Claude Desktop, *and* the local Qwen model chains at least three of them to answer a query.

> **[VERIFIED 2026-08-03 — the chaining half already passes.]** Dry-run against ollama 0.32.5
> with stub tool results, using §6's actual Portuguese query:
>
> ```
> stac_search({"bbox":[-10.5,38,-8.5,39.5],"start":"2026-07-31T19:00Z","end":"2026-08-03T00:00Z"})
> detect_vessels({"scene_id":"S1D_20260802T063352","min_length_m":20})
> ais_match({"detections":["det_001","det_002"],"radius_m":500})
> → final answer, in Portuguese
> ```
>
> Three distinct tools chained unprompted. It parsed the bbox out of Portuguese prose and
> resolved *"últimas 72 horas"* against *"Hoje é 2026-08-03"* into a correct window. It also
> hedged without being asked — *"pode não ser declarada"* — which is §7's "a lead, not a
> conclusion" arriving for free.
>
> **⚠️ Build a loop-breaker into M5 anyway.** When a tool result contradicts what the model
> asked for — feeding back a 17 Jul scene after it requested 31 Jul–3 Aug — it re-called
> `stac_search` four times with tweaked date ranges instead of advancing. That is arguably
> *correct* behaviour (the result genuinely didn't satisfy the request), but unbounded it
> burns the whole context. LangGraph needs a max-iterations guard and ideally a
> "same tool, same args, twice" detector.

That dual proof — same tool surface working connected and disconnected — is a good part of the story. Worth demonstrating both.

### M5 — Agent + HIL gate
LangGraph: `parse → plan → tools → correlate → draft_intrep → HUMAN_GATE → release`.

Use LangGraph's interrupt mechanism rather than a bare `input()`, so execution genuinely halts with inspectable persisted state. That distinction is the interesting part.

**Done when:** a Portuguese-language question runs through to a drafted INTREP, halts at the gate, resumes on approval.

### M6 — Package
README with architecture diagram, design-decision table, limitations, licence. 90-second screen recording.

**Done when:** someone else could clone, `make up`, and reproduce the demo.

### M7 — If there's appetite

- **Go bundler** — `docker save` + pip wheelhouse + model blobs → one tarball with SHA256 manifest and a `verify` subcommand. Closes the second-language gap; a static binary with no runtime deps is *why* Go fits here.
- **Eval set** — 20 questions, retrieval hit-rate@k and groundedness. Gives a number to quote.
- **k3s/kind deploy** — Helm chart, preloaded images, resource limits, **NetworkPolicy default-deny egress**. That policy is the Kubernetes expression of an air gap and is the best single detail in this list.
- **SBOM** via syft, pinned image digests.

---

## 5. Tool contracts

Keep signatures stable — the MCP server and the agent both bind to them.

```python
stac_search(bbox: list[float], start: datetime, end: datetime) -> list[Scene]
# Scene: id, acquisition_time, mode, polarizations, footprint_wkt, incidence_angle

detect_vessels(scene_id: str, min_length_m: float = 15.0) -> list[Detection]
# Detection: id, scene_id, lon, lat, length_m, heading_deg, confidence

ais_match(detections: list[str], time_window_min: int = 60,
          radius_m: float = 500.0) -> list[Match]
# Match: detection_id, mmsi | None, distance_m | None, time_delta_s | None,
#        status: "matched" | "dark"

doc_search(query: str, k: int = 8,
           filters: dict | None = None) -> list[Chunk]
# Chunk: doc_id, chunk_id, text, score, classification, source_url | None

correlate(bbox, start, end, min_length_m=15.0) -> CorrelationResult
# Orchestrates stac_search → detect_vessels → ais_match.
# Returns dark + matched detections with full provenance chain.

draft_intrep(correlation: CorrelationResult,
             context_chunks: list[Chunk]) -> INTREP
# Structured report. Every claim carries source refs. See §7.
```

Two things worth getting right:

- Every tool returns provenance alongside values, not bare results.
- `ais_match` should account for the offset between AIS report time and image acquisition — a vessel moves in between. That's the substance of the fusion problem, and collapsing it to a naive point-in-polygon throws away the interesting part.

Tools stay pure functions over the database — no hidden state, no caching that changes results between runs.

---

## 6. The demo

**Run the demo over the Portuguese AOI.** Denmark appears in the README as the validation case with the real correlation numbers; Portugal is what gets recorded.

An analyst asks, in Portuguese:

> *"Houve alguma embarcação não declarada na área X nas últimas 72 horas?"*

1. Agent parses AOI and time window
2. `stac_search` → SAR scenes covering it
3. `detect_vessels` → N detections
4. `ais_match` → three with no AIS correspondence
5. `doc_search` → intel documents referencing the AOI and vessel class
6. `draft_intrep` → report where every claim is cited
7. Halts at the human review gate
8. On approval → marked releasable

Closing shot: `docker compose exec api curl -m 5 https://google.com` fails, and the whole thing ran anyway.

90 seconds, terminal output, no narration needed.

The Portuguese query isn't decoration — the JD requires a native speaker deploying in-country, and bge-m3 handles it.

---

## 7. Provenance and refusal

Intelligence analysts grade every input on two independent axes (NATO Admiralty Code: source reliability A–F, information credibility 1–6). Output that can't be traced can't be graded, so it can't enter the intelligence cycle — however fluent it reads.

So:

- Every INTREP claim carries scene ID, detection ID(s), document chunk ID(s), timestamps
- The generation prompt instructs: assert nothing absent from retrieved context; if context is insufficient, say so
- Exercise the refusal path deliberately and show it in the demo — a system that admits what it doesn't know reads as more credible than one that always answers
- Draft output marked `DRAFT — NOT RELEASABLE` until the gate is passed
- Classification markings propagate from source documents into the report

Put one framing line in the README: **a dark detection is a lead, not a conclusion.** Missing AIS has innocent explanations — satellite revisit gaps, terrestrial coverage limits, transponder failure, class B low-power transponders, vessels not required to carry AIS. The system surfaces candidates; the analyst adjudicates.

That caveat is a stronger signal than any feature in this spec.

---

## 8. README requirements

The README is what a hiring manager actually reads.

1. **One-paragraph pitch** — what it does, in analyst terms
2. **Architecture diagram** — compose topology with the no-egress boundary marked
3. **Quickstart** — `make up`, run the demo
4. **The air-gap proof** — the M1 terminal capture
5. **Design decisions** — the §2 table, with reasoning
6. **Data sources and licences** — GFW CC BY-NC 4.0; DMA attribution line verbatim; synthetic documents identified as synthetic
7. **Limitations** — no free *historical, complete* point-level AIS for Portugal, so GFW detections are a reference layer not an independent correlation; any self-collected aisstream AIS is a **thinned** feed (~3% of expected message volume measured) and therefore cannot support a dark-vessel *rate*, only a demonstration of the mechanism; single scene per AOI; no CFAR tuning; no accuracy claims beyond what was measured; dark ≠ guilty
   - State the aisstream sparsity **as a measured number, not a hedge**. "I measured ~1.4 messages per vessel per 4 minutes against ~40 expected, so I did not claim a dark rate from it" is a much stronger answer than omitting the source or overclaiming it — it shows the completeness question was tested rather than assumed.
   - Also worth a short **related work** note citing Magalhães, Falcão & Barbosa (2025), IST Lisbon — Sentinel-2 optical vessel detection with YOLO. Positions NIGHTGLASS as the SAR complement to active Portuguese optical work, and sets up the contrast: optical fails at night and under cloud, which is the entire SAR value proposition.
8. **What I'd do with three more weeks** — real CFAR with sea-state adaptation, multi-scene temporal tracking, coherent change detection with SLC, offline CI/CD, SBOM and image pinning

Sections 7 and 8 punch above their weight. Volunteering limitations before being asked reads as senior, and "what's next" shows you know the difference between a weekend artifact and a system.

---

## 9. Working with Nelson

He's reading along to understand the system, not to approve it. Useful rhythm:

- **Narrate decisions briefly as they happen** — one line on why Qdrant, why this schema shape. Cheaper than reconstructing it later.
- **Flag the genuinely interesting bits** so they don't slide past: the LangGraph interrupt, the space–time join, the no-egress network config, the citation plumbing.
- **Keep `NOTES.md` current** with decisions made, things tried that failed, and open questions. That file is the study aid.
- **Ask when a decision is irreversible or expensive.** Otherwise just decide.

Questions he'll get asked in the hiring manager round, so they're worth being able to answer from the code: why GRD not SLC, why Qdrant over pgvector, why the AIS table is derived, how the interrupt actually persists state, what breaks first in a real air-gapped deployment.

At some point he should make a non-trivial change himself. Hands in it beats reading it.

---

## 10. Checklist

*Pre-dev status as of 2026-08-03 — see `NOTES.md` for the full checklist and measurements.*

```
[~] PRE_DEV_GUIDE.md checklist complete   ← real data on disk before M2
    [x] environment verified (GPU, docker, GDAL, python geo stack)
    [x] Portuguese S1 coverage verified — top risk retired
    [x] DMA source relocated, 2 days downloaded (1.4 GB), schema verified
    [x] DMA timezone resolved = UTC
    [x] AIS acquisition-window gate PASSED — 907 vessels / 134,851 positions
    [ ] ollama + models            ← needs sudo
    [ ] ASF EULA accepted          ← CRITICAL PATH: gates every SAR scene, both AOIs
    [ ] GFW token
    [ ] aisstream recorder running ← hard deadline, stable before Fri 7 Aug 07:00 UTC
[ ] M0  scaffold, compose up, healthchecks, NOTES.md
[ ] M1  offline inference + failed-egress proof captured
[ ] M2  RAG with citations + refusal path
[ ] M3  PostGIS, dark-vessel SQL hand-checked against the ~5% base rate
[ ] M4  six tools, MCP server, Claude Desktop + local model both driving it
[ ] M5  LangGraph agent, real interrupt at HIL gate
[ ] M6  README, diagram, 90-second recording
[ ] M7  optional extras
```

---

## Context

Prep notes are in the Obsidian vault at `Projects/iceye-fde/`. The SAR fundamentals and intelligence-domain sections there explain the *why* behind several decisions here — worth a look if a design choice seems arbitrary.
