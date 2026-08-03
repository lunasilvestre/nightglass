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

**Status: M2 — document RAG with citations.** The enclave stands up and is sealed (M0), runs
inference offline (M1), and now answers questions from a 60-document corpus with every claim
traced to a retrievable chunk — or refuses when the corpus cannot support one. The spatial layer
and the remaining five tools of `EXECUTION_SPEC.md` §5 are M3–M4; their contracts are already
typed in `src/nightglass/schemas.py`. `NOTES.md` is the running decision log.

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

        nightglass_provision ─┬─ model-puller   ──► internet   (profile-gated;
                              └─ corpus-fetcher ──► internet    the ONLY services
                                                                with egress, and
                                                                neither runs during
                                                                operation)
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
- **Nothing inside can fetch its own inputs at runtime.** Correct, and it applies to both
  things this system needs from the outside world. Weights arrive via `model-puller`
  (`make pull-models`); documents arrive via `corpus-fetcher` (`make fetch-corpus`). Both are
  profile-gated services on the `provision` network, invoked explicitly, and neither is running
  during operation. Provisioning and operation are different security postures and the compose
  file says so out loud rather than blurring them.

---

## Quickstart

```bash
cp .env.example .env      # then set POSTGRES_PASSWORD
make preflight            # checks docker, the nvidia runtime, VRAM, the AOI config
make up                   # build + start, waits for every container healthy
make pull-models          # once, ~10 GB   ┐ the only two steps that touch the network
make fetch-corpus         # once, ~35 MB   ┘
make ingest               # chunk + embed the corpus into Qdrant (~1 min, offline)
make air-gap-proof        # §M1: no egress, inference works anyway
make rag-proof            # §M2: ungrounded vs grounded, and the refusal path
```

`make` with no target lists everything.

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
Radar de Abertura Sintética (SAR) é uma tecnologia que permite capturar imagens
detalhadas da superfície da Terra usando ondas de rádio, funcionando bem de noite
porque não depende da luz solar para operar.

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
$ nightglass-corpus ask "o que é uma embarcação escura?" --ungrounded

A expressão "embarcação escura" não tem um significado específico ou comum na
náutica ou em outros contextos conhecidos. É possível que você esteja se
referindo a algum tipo de metáfora, título de obra literária, filme, ou outro
contexto específico onde essa frase é usada.
```

The central term of this entire project, and the model has no prior for it. Across samples it
either declares the term to have no established maritime meaning, as here, or offers hull colour
as a guess — *"pintado em cores escuras"*. Both are wrong, and neither is hedged in a way an
analyst skimming would catch.

```
$ nightglass-corpus ask "o que é uma embarcação escura?" --sources

GROUNDED — UNCLASSIFIED // SYNTHETIC

1. Chama-se embarcação escura a uma embarcação detetada por sensor — neste caso uma
   deteção de amplitude em imagem de radar de abertura sintética (SAR) Sentinel-1 —
   para a qual não é possível encontrar qualquer mensagem AIS correspondente na fonte
   de referência utilizada, dentro de uma tolerância declarada de distância e de tempo
   em torno da posição da deteção e do instante de aquisição da imagem.
   [intsum-2026-021-embarcacao-escura-doutrina#0000]
2. "Escura" é uma afirmação sobre a ausência de uma emissão esperada, numa fonte
   concreta, num momento concreto — e sobre mais nada.
   [intsum-2026-021-embarcacao-escura-doutrina#0002]
```

Every claim carries the chunk IDs it came from, and every one of them is retrievable. The
`UNCLASSIFIED // SYNTHETIC` marking was **not** written by the drafter or the model: it is
computed from the markings of the chunks that were actually cited. Ask something the real
documents answer and the same command returns plain `UNCLASSIFIED`:

```
$ nightglass-corpus ask "Under what circumstances does IMO guidance permit a master
                         to switch off AIS, and what must the master then do?" --sources

GROUNDED — UNCLASSIFIED

1. AIS may be switched off if the master believes that its continual operation might
   compromise the safety or security of his/her ship.
   [imo-a1106-29-ais-operational-use#0015] [imo-a917-22-ais-guidelines#0013]
2. If AIS is switched off, the master should report this action and the reason for doing
   so to the competent authority if operating in a mandatory ship reporting system.
   [imo-a1106-29-ais-operational-use#0015]
3. The master must record the action of switching off AIS and the reason for it in the
   ship's logbook.
   [imo-a1106-29-ais-operational-use#0015] [imo-a917-22-ais-guidelines#0013]
```

And the part that matters more than either — a question the corpus genuinely cannot answer:

```
$ nightglass-corpus ask "Quantas embarcações escuras foram detetadas ao largo da Madeira em 2019?"

GROUNDED — UNCLASSIFIED

Not supported by available sources. / Não suportado pelas fontes disponíveis.

8 chunk(s) were retrieved but none supported an answer.
```

The gap is real and documented, not engineered per question: no document in the corpus reports
detections for Madeira or the Azores, and `corpus/README.md` says so explicitly so that adding
one is a deliberate act rather than an accident.

**Refusal is enforced structurally, not requested.** The model is asked to cite, and it is given
a JSON schema in which a claim cannot exist without a citation list — but both of those are
requests. What decides the outcome is a check afterwards: every cited chunk ID is verified
against the set actually retrieved, fabricated IDs are dropped, claims left with none are
discarded, and an empty result is a refusal regardless of the model reporting itself as
confident. §7's position is that output which cannot be traced cannot be graded and therefore
cannot enter the intelligence cycle, so the untraceable parts are removed rather than published
with a caveat.

**Cross-lingual retrieval works in both directions**, which the §6 demo depends on. A Portuguese
query reaches English IMO and EU sources; an English query reaches the Portuguese memos and ranks
them first:

```
$ nightglass-corpus search "how should AIS duplicate messages be handled before matching?"

0.7143  [intsum-2026-030-correlacao-sar-ais#0001]   Metodologia de Correlação SAR–AIS
0.6163  [imo-msc74-69-performance-standards#0023]   Resolution MSC.74(69) — …shipborne AIS
```

### The corpus

| Publisher | Docs | Contributes |
|---|---|---|
| ICEYE Ltd | 17 | SAR imaging geometry, azimuth, radiometry, geolocation accuracy, product levels |
| International Maritime Organization | 10 | The AIS carriage and operation obligation, its exceptions, LRIT, port state control |
| European Union (EUR-Lex) | 8 | The AIS obligation in EU waters, STS notification, AIS interruption as conduct |
| Copernicus EMS | 4 | Dated environmental context over the Portuguese AOI |
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
docker/                   application image (runtime · fetcher · dev), postgis init
scripts/                  preflight, air-gap proof, rag proof, model seeding, env loader
corpus/
  sources.yaml            manifest of the 39 public documents — URLs, not documents
  synthetic/              21 INTREP/INTSUM memos, UNCLASSIFIED // SYNTHETIC, committed
  README.md               what the corpus is, licences, and the deliberate gap
src/nightglass/
  config.py               AOI resolution — the only place a bbox is named
  schemas.py              the §5 tool contracts, with provenance attached
  rag/
    fetch.py              ONLINE. the only module here that opens an outward socket
    extract.py            pdf · markdown · activation JSON -> text worth embedding
    chunking.py           structure-aware, heading-path aware, stable chunk ids
    embed.py              bge-m3 via the enclave's own ollama
    index.py              Qdrant: ingest, and §5's doc_search
    answer.py             grounded generation, citation verification, refusal
    cli.py                nightglass-corpus fetch|ingest|search|ask|stats
  api/                    FastAPI
  mcp/                    FastMCP, stdio + sse
  agent/                  LangGraph (M5)
data/                     gitignored — 6.2 GB of SAR and AIS, plus the fetched corpus
EXECUTION_SPEC.md         what to build
PRE_DEV_GUIDE.md          verified data access paths
NOTES.md                  decisions, corrections, measurements
```

Licence: Apache-2.0.
