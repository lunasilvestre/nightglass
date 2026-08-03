# NIGHTGLASS

**Air-gapped SAR intelligence assistant.** Finds vessels in Sentinel-1 radar imagery, checks
them against AIS, and drafts a cited intelligence report — with no route to the internet.

An analyst asks, in Portuguese, whether anything undeclared has moved through an area in the
last 72 hours. NIGHTGLASS searches the SAR catalogue for scenes covering it, runs its own
detector over the radar amplitude, correlates each detection against vessel position reports in
space *and* time, pulls supporting context out of an intelligence document corpus, and produces
an INTREP in which every claim carries the scene, detection and document IDs it rests on. It
halts for human review before anything is marked releasable. The chat model, the embedding
model and the vector store all run inside the enclave; nothing calls out.

Deliberately shaped as the open-source shadow of ICEYE Ocean Vision Detect.

> **A dark detection is a lead, not a conclusion.** A vessel with no AIS correspondence has
> plenty of innocent explanations — satellite revisit gaps, terrestrial receiver limits,
> transponder failure, low-power class B sets, vessels never required to carry AIS at all. The
> system surfaces candidates. The analyst adjudicates.

**Status: M0 — scaffold.** The enclave stands up and is sealed. The tools it will serve are
specified in `EXECUTION_SPEC.md` §5 and typed in `src/nightglass/schemas.py`; they are not
implemented yet. `NOTES.md` is the running decision log.

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

        nightglass_provision ── model-puller ──► internet   (profile-gated;
        the ONLY service with egress, and it is not running during operation)
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
- **Ollama cannot download models at runtime.** Correct. Provisioning happens once, on a
  separate network, behind a compose profile (`make pull-models`); operation happens forever
  with no network at all. They are different security postures and the compose file says so.

---

## Quickstart

```bash
cp .env.example .env      # then set POSTGRES_PASSWORD
make preflight            # checks docker, the nvidia runtime, VRAM, the AOI config
make up                   # build + start, waits for every container healthy
make pull-models          # once, ~10 GB — the only step that touches the network
make air-gap-proof        # §M1: no egress, inference works anyway
```

`make` with no target lists everything.

**On this machine**, the host runs ollama as a systemd service with `OLLAMA_KEEP_ALIVE=-1`,
which pins ~15 GB of the 3090 permanently and leaves the enclave's own ollama unable to load a
14B model. `make preflight` detects this and tells you to `sudo systemctl stop ollama`. There is
also a `make seed-models` shortcut that copies the blobs already on the host into the volume
instead of re-downloading them — a local convenience, explicitly not the documented path.

---

## The air-gap proof

<!-- M1: paste the real terminal capture here. `make air-gap-proof` produces it. -->

_Pending M1._ The check is `docker compose exec api curl -m 5 https://example.com` failing while
a chat completion against Ollama succeeds in the same session.

---

## Design decisions

| Choice | Reason |
|---|---|
| **Ollama inside the enclave, not the host service** | The host has ollama on `:11434` with the models already pulled, and pointing compose at it would save re-downloading ~10 GB. It also needs `host.docker.internal:host-gateway` — a deliberate hole in the exact boundary this project exists to demonstrate — and it breaks "clone and `make up`". The model server belongs *inside* the enclave because in a real deployment there is no host to borrow from. Cost is paid once via `make pull-models`. |
| Ollama over vLLM / TGI+TEI | **One service serves chat *and* embeddings**; vLLM and TGI are one-model-per-process and would need two containers. Ollama supports fully air-gapped operation, while vLLM needs network configuration to reach full isolation — which is the whole milestone. Models are content-addressed blobs in one directory, so the offline bundle is a tar of a folder, not an untangling of a HuggingFace cache. Honest limit: Ollama serialises concurrent requests and vLLM does ~3.2× the throughput. Irrelevant for one analyst. The line is *"Ollama for the enclave, vLLM if this became multi-tenant."* |
| Qdrant over pgvector | Single binary, trivial offline deploy, no external dependencies. pgvector is already familiar from production work; Qdrant shows breadth and is a common air-gapped default. |
| bge-m3 for embeddings | Genuinely multilingual. Measured: a Portuguese query against its English equivalent scores **0.842** cosine, against unrelated Portuguese text **0.283** — a 0.56 separation, so it keys on meaning rather than language. Deployments are national; the corpus and the queries will not both be English. This choice is one-way, since changing it means re-embedding everything. |
| Qwen2.5 **14B** at q4_K_M | Fits consumer VRAM (~9 GB weights, ~15 GB resident with a 32k KV cache, on a 24 GB card), strong tool-calling, permissive licence. Verified chaining three distinct tools unprompted from a Portuguese question. |
| LangGraph over CrewAI | An explicit state machine with a genuinely interruptible node, which the human-in-the-loop gate needs. State persists and is inspectable while halted — a bare `input()` only blocks a thread. |
| PostGIS for geometry | Spatial correlation belongs in a spatial database, not in Python. |
| GRD not SLC | Vessel detection needs amplitude only. Phase is for interferometry, which is out of scope. |
| Agent as a one-shot, not a service | It runs to a human gate and halts. It has nothing to serve between invocations, so a daemon wrapper would exist only to satisfy a healthcheck. |

---

## Data sources and licences

| Source | Use | Licence / terms |
|---|---|---|
| **Sentinel-1 GRD** (ESA, via ASF) | SAR imagery, both AOIs | Free and open. Requires a NASA Earthdata account and one-time ASF EULA acceptance. |
| **Danish Maritime Authority AIS** | Point-level ground truth, validation AOI | See attribution below. |
| **Global Fishing Watch** SAR detections | Independent reference layer, demo AOI | **CC BY-NC 4.0** — non-commercial, attribution required. |
| **aisstream.io** | Live demonstration feed, demo AOI | Free tier. **Not ground truth** — see Limitations. |
| Synthetic INTREP/INTSUM memos | Document corpus padding | Written for this project. Marked `UNCLASSIFIED // SYNTHETIC` in both header and metadata. |

> Contains data from the Danish Maritime Authority that is used in accordance with the
> conditions for the use of Danish public data.

---

## Limitations

Stated before being asked, because each one was tested rather than assumed.

- **No free historical, complete point-level AIS exists for Portuguese waters.** GFW's ORBCOMM
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
- **Dark ≠ guilty.** See the framing at the top.

**Related work.** Magalhães, Falcão & Barbosa (2025), IST Lisbon — Sentinel-2 optical vessel
detection with YOLO. NIGHTGLASS is the SAR complement to active Portuguese optical work, and the
contrast is the point: optical fails at night and under cloud, which is the entire reason SAR
exists for this mission.

---

## What I'd do with three more weeks

- Real CFAR with sea-state adaptation, rather than a fixed threshold
- Multi-scene temporal tracking, so a detection becomes a track
- Coherent change detection with SLC pairs
- Offline CI/CD — the bundler (`docker save` + wheelhouse + model blobs → one tarball with a
  SHA256 manifest and a `verify` subcommand)
- SBOM via syft and image digests pinned rather than tags

---

## Repository layout

```
docker-compose.yml        the enclave — one internal network, no egress
docker/                   application image, postgis init
scripts/                  preflight, air-gap proof, model seeding, env loader
src/nightglass/
  config.py               AOI resolution — the only place a bbox is named
  schemas.py              the §5 tool contracts, with provenance attached
  api/                    FastAPI
  mcp/                    FastMCP, stdio + sse
  agent/                  LangGraph (M5)
data/                     gitignored — 6.2 GB of SAR and AIS lives here
EXECUTION_SPEC.md         what to build
PRE_DEV_GUIDE.md          verified data access paths
NOTES.md                  decisions, corrections, measurements
```

Licence: Apache-2.0.
