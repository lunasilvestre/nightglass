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

**Status: M3 — the spatial layer.** The enclave stands up and is sealed (M0), runs inference
offline (M1), answers from a 60-document corpus with every claim traced to a retrievable chunk
or refuses (M2), and now runs its own vessel detector over real Sentinel-1 pixels and correlates
the detections against real AIS in space *and* time (M3). Over the Danish validation AOI, 45 of
60 detections match a vessel that was actually there, with **88% recall on AIS vessels ≥ 30 m**.
The remaining tools of `EXECUTION_SPEC.md` §5 are M4; their contracts are already typed in
`src/nightglass/schemas.py`. `NOTES.md` is the running decision log.

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
                              └─ coastline-fetcher ──► internet   with egress, and
                                                                  none of them runs
                                                                  during operation)
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
  input. Weights arrive via `model-puller` (`make pull-models`), documents via `corpus-fetcher`
  (`make fetch-corpus`), the shoreline via `coastline-fetcher` (`make fetch-coastline`), and
  granules are staged onto a read-only mount before the enclave is sealed. All three fetchers
  are profile-gated services on the `provision` network, invoked explicitly, and none is running
  during operation. Provisioning and operation are different security postures and the compose
  file says so out loud rather than blurring them.

  That the list is four items long and not three is itself a finding: the detector's own land
  mask structurally cannot separate a 100 m skerry from a 100 m hull, so a real air-gapped
  deployment has to ship a coastline with it. The clip happens online, so the enclave carries
  713 KB rather than the 149 MB archive.

---

## Quickstart

```bash
cp .env.example .env      # then set POSTGRES_PASSWORD
make preflight            # checks docker, the nvidia runtime, VRAM, the AOI config
make up                   # build + start, waits for every container healthy
make pull-models          # once, ~10 GB   ┐
make fetch-corpus         # once, ~35 MB   ├ the only steps that touch the network
make fetch-coastline      # once, 149 MB   ┘ (149 MB in, 713 KB kept)
make ingest               # chunk + embed the corpus into Qdrant (~1 min, offline)
make air-gap-proof        # §M1: no egress, inference works anyway
make rag-proof            # §M2: ungrounded vs grounded, and the refusal path
make dark-proof           # §M3: detector, AIS, the space-time join, and the renders
make tool-proof           # §M4: the tools over MCP, and the local model chaining them
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

## Dark vessels: detection, and a space–time join

`make dark-proof` runs the whole chain over the Danish AOI — schema, scene, detector, AIS, the
query, then the renders. **Denmark, not Portugal, and that is the point of having two AOIs:** it
is the only one with free point-level historical AIS, so it is the only place a claim about the
matcher can be *checked* rather than asserted.

```
detections    60          in the Kattegat AOI, from one Sentinel-1 GRD granule
matched       45          an AIS vessel within 500 m of where SAR would have drawn it
dark          15          no AIS correspondence — 25.0%
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

| shoreline buffer | detections | matched | dark | dark rate |
|---|---|---|---|---|
| 300 m | 71 | 46 | 25 | 35% |
| **1000 m** *(default)* | **60** | **45** | **15** | **25%** |
| 2000 m | 53 | 44 | 9 | 17% |
| 3000 m | 50 | 43 | 7 | 14% |

Going from 300 m to 3 km removes 21 detections — **18 of them dark and only 3 matched**. Coastal
detections are overwhelmingly not vessels, and the buffer costs almost no real ones. 1 km is
where that stops being nearly free.

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
risk. Running the same configuration unchanged over a Portuguese granule — different platform
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

### What the numbers do *not* say

- **25% unmatched is not a 25% dark-vessel rate.** Published work on Danish waters finds ~5%
  unmatched and ~0.4% genuinely dark after review. The residual here concentrates near shore and
  is dominated by coastal clutter this pipeline has not fully separated from vessels.
- **Detected length is not a reliable size estimate.** Detecting at 8σ and measuring at the same
  threshold gave lengths with **r = 0.015** against AIS — no relationship at all, because at
  that threshold the blob tracks peak brightness rather than hull. Re-growing each detection at
  2.5σ fixed the *bias* (median ratio 0.30× → **1.15×**) but the per-vessel scatter stays wide
  (**r = 0.198**). The median is usable; an individual number is not.
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

A Portuguese question, no bbox and no scene id in the prompt, tools chosen by the model:

```
1. stac_search({"bbox": [10.5,55.5,12.5,57.5], "start": "2026-07-17T00:00:00Z", …})   -> 2 scenes
2. detect_vessels({"scene_id": "S1D_…_BC13"})                                          -> 60
3. ais_match({"detections": [60 ids]})                          -> matched 45, unmatched 15
```

45 and 15 are the hand-checked SQL's numbers, and the fifteen unmatched ids it reported back are
**identical to the database's** — checked, not assumed. It hedged without being asked
(*"leads para análise adicional … não podem ser consideradas como evidências conclusivas"*) and
stated no rate.

That pair — a frontier model over a pipe from outside, a 14B model from inside, one tool
surface — is the part worth having. A capability that only works with a frontier model on the
other end of it is not an air-gapped capability.

### The number the tools will not give you

`CorrelationResult.rate_is_quotable` asks whether the AIS feed is complete enough to be a
denominator. Over Denmark it is: DMA is ground truth, and the field is **true**.

The report still refuses to state a dark-vessel rate, because that field only guards one side of
the fraction. It says nothing about whether the things in the *numerator* are vessels — and 25%
unmatched against a ~5% published base rate says they are substantially coastal clutter. A
matcher being validated does not make a detector's coastal precision validated.

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

  • 45 dessas deteções correspondem a uma embarcação que reporta AIS, após interpolar
    a posição de cada embarcação para o instante de aquisição e corrigir o
    deslocamento em azimute do SAR; separação mediana 96 m.        [scene×1; det×45]

  - Nenhuma proporção de deteções sem correspondência é indicada neste relatório.
    A precisão costeira do detetor não está validada. …
  - Danish Maritime Authority — AIS data. …
  - RASCUNHO — NÃO DIVULGÁVEL até revisão e libertação no controlo humano.
```

The DMA attribution stays in English inside a Portuguese report on purpose: a translated licence
condition is not the licence condition.

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

## Design decisions

| Choice | Reason |
|---|---|
| **Ollama inside the enclave, not the host service** | The host has ollama on `:11434` with the models already pulled, and pointing compose at it would save re-downloading ~10 GB. It also needs `host.docker.internal:host-gateway` — a deliberate hole in the exact boundary this project exists to demonstrate — and it breaks "clone and `make up`". The model server belongs *inside* the enclave because in a real deployment there is no host to borrow from. Cost is paid once via `make pull-models`. |
| Ollama over vLLM / TGI+TEI | **One service serves chat *and* embeddings**; vLLM and TGI are one-model-per-process and would need two containers. Ollama supports fully air-gapped operation, while vLLM needs network configuration to reach full isolation — which is the whole milestone. Models are content-addressed blobs in one directory, so the offline bundle is a tar of a folder, not an untangling of a HuggingFace cache. Honest limit: Ollama serialises concurrent requests and vLLM does ~3.2× the throughput. Irrelevant for one analyst. The line is *"Ollama for the enclave, vLLM if this became multi-tenant."* |
| Qdrant over pgvector | Single binary, trivial offline deploy, no external dependencies. pgvector is already familiar from production work; Qdrant shows breadth and is a common air-gapped default. |
| bge-m3 for embeddings | Genuinely multilingual. Measured: a Portuguese query against its English equivalent scores **0.842** cosine, against unrelated Portuguese text **0.283** — a 0.56 separation, so it keys on meaning rather than language. Deployments are national; the corpus and the queries will not both be English. This choice is one-way, since changing it means re-embedding everything. |
| Qwen2.5 **14B** at q4_K_M | Fits consumer VRAM (~9 GB weights, ~15 GB resident with a 32k KV cache, on a 24 GB card), strong tool-calling, permissive licence. Verified chaining three distinct tools unprompted from a Portuguese question. |
| LangGraph over CrewAI | An explicit state machine with a genuinely interruptible node, which the human-in-the-loop gate needs. State persists and is inspectable while halted — a bare `input()` only blocks a thread. |
| PostGIS for geometry | Spatial correlation belongs in a spatial database, not in Python. The dark-vessel join is one query in `src/nightglass/spatial/sql/dark_vessels.sql` — track interpolation via `LEAD`, the azimuth-displacement correction via `ST_Project`, distances via `geography` casts so they come back in metres rather than degrees. It stays a `.sql` file so it can be read top to bottom and run a CTE at a time. |
| Scene as a STAC Item, not a bespoke table | `stac_search` (§5) is a catalogue query. Modelling the catalogue as STAC keeps the door open to pointing the same tool at a real STAC API — which is what a customer deployment has — rather than at a table only this project understands. The Item is stored whole in `jsonb`; the columns beside it are extracted for indexing. |
| A shoreline is a fourth provisioning input | Weights, documents, granules — and now GSHHG. The detector's own land mask structurally cannot separate a 100 m skerry from a 100 m hull, so an air-gapped deployment genuinely has to ship a coastline with it. Saying that is a better answer to "what does this need bundled" than pretending the list was three items long. |
| TPS over polynomial GCP fit | Measured on a real granule: polynomial leaves 40 m mean / 185 m worst-case geolocation error, TPS leaves 0.000 m. 15× slower on a cost of 0.15 s per 20,000 points. |
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
- **25% of detections unmatched is not a 25% dark-vessel rate.** Published work on Danish waters
  finds ~5% unmatched and ~0.4% genuinely dark after review. The excess here concentrates near
  shore and is coastal clutter this pipeline has not fully separated from vessels — the
  shoreline-buffer sweep above shows near-shore detections are unmatched at 6:1. The honest
  reading is that the *matcher* is validated (45 matches, median 119 m, 88% recall above 30 m)
  and the *detector's* coastal precision is not yet good enough to quote a rate from.
- **Detected length is a size band, not a measurement.** Median ratio against AIS-reported length
  is 1.15×, but per-vessel correlation is only **r = 0.198**. It is used as a filter and
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
- **Dark ≠ guilty.** See the framing at the top.

**Related work.** Magalhães, Falcão & Barbosa (2025), IST Lisbon — Sentinel-2 optical vessel
detection with YOLO. NIGHTGLASS is the SAR complement to active Portuguese optical work, and the
contrast is the point: optical fails at night and under cloud, which is the entire reason SAR
exists for this mission.

---

## What I'd do with three more weeks

- Real CFAR with sea-state adaptation, rather than one threshold per block
- **Fix coastal precision properly.** The shoreline buffer is a blunt instrument that trades
  real vessels for clutter. A hydrographic layer with harbour limits, aids to navigation and
  fixed-structure catalogues (wind farms, platforms) would let near-shore detections be
  classified rather than excluded — and that is where the 25% unmatched figure actually lives.
- **Per-detection azimuth time.** A granule spans 25 s and the AIS is currently interpolated to
  the scene mid-time. A detection's own along-track position says when it was really imaged,
  worth ~130 m at 10 kn — inside the current radius, but it is free accuracy.
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
.mcp.json                 the MCP attach, committed — clone and Claude Code has the tools
docker/                   application image (runtime · fetcher · dev), postgis init
scripts/                  preflight, the four proofs, MCP stdio probe, model seeding
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
  spatial/
    safe.py               Sentinel-1 SAFE read in place: annotation, calibration, noise, GCPs
    detect.py             the vessel detector — CFAR, land mask, TPS geolocation
    geodesy.py            bearings, and the azimuth-displacement physics
    coastline.py          ONLINE fetch + AOI clip; the second land mask
    ais.py                source adapters — DMAFileSource · GFW · CustomerFeedSource
    db.py                 PostGIS access; the SQL lives in files, not f-strings
    sql/001_schema.sql    stac.scenes · detect.runs+detections · ais.positions
    sql/dark_vessels.sql  §M3's join — interpolate, correct, match. Readable on its own.
    render.py             chips, scene overview, map view — the evidence
    plots.py              validation charts
    validate.py           measure the detector against DMA ground truth
    cli.py                nightglass-spatial migrate|scenes|detect|load-ais|dark|render
  tools/
    spatial.py            stac_search · detect_vessels · ais_match · correlate
    documents.py          doc_search — the M2 retriever behind the same boundary
    intrep.py             draft_intrep, and the two-sided guard on the rate
    chaining.py           the local model driving the tools; max-iters + repeat detector
    cli.py                nightglass-tools list|call|chain
  api/                    FastAPI — the six tools over HTTP
  mcp/                    FastMCP — the same six over stdio + sse
  agent/                  LangGraph (M5)
docs/evidence/            committed renders — the snapshot the README's numbers come from
data/                     gitignored — 6.2 GB of SAR and AIS, the corpus, the coastline
  out/                    rendered evidence, regenerated by `make dark-proof`
EXECUTION_SPEC.md         what to build
PRE_DEV_GUIDE.md          verified data access paths
NOTES.md                  decisions, corrections, measurements
```

Licence: Apache-2.0.
