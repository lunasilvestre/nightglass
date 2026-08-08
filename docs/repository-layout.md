# Repository layout

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

```
docker-compose.yml        the enclave — one internal network, no egress
.mcp.json                 the MCP attach, committed — clone and Claude Code has the tools
docker/                   application image (runtime · fetcher · dev), postgis init
  Dockerfile.bundler      golang:1.26-alpine -> scratch; the host never needs Go
scripts/                  preflight, the seven proofs, the demo, its pacing and its check
deploy/
  helm/nightglass/        the enclave as a chart; templates/networkpolicy.yaml is the point
  values-proof.yaml       what `make k8s-proof` overrides, and therefore does not exercise
bundler/                  Go. the offline transfer bundle — a second language, on purpose
  cmd/bundle/main.go      nightglass-bundle create|verify|restore|inspect
  internal/manifest/      the manifest, and every way it can be internally incoherent
  internal/bundle/        create · verify · restore — one streaming pass, eight refusals
  internal/sources/       reads data/sources.yaml, so a bundle cannot outrun the manifest
  internal/dockercli/     docker save · load · the volume round trip, over os/exec
corpus/
  sources.yaml            manifest of the 39 public documents — URLs, not documents
  synthetic/              21 INTREP/INTSUM memos, UNCLASSIFIED // SYNTHETIC, committed
  README.md               what the corpus is, licences, and the deliberate gap
src/nightglass/
  config.py               AOI resolution — the only place a bbox is named
  schemas.py              the six tool contracts, with provenance attached
  display.py              wrapping — the two renderers that show a model at work
  rag/
    fetch.py              ONLINE. the only module here that opens an outward socket
    extract.py            pdf · markdown · activation JSON -> text worth embedding
    chunking.py           structure-aware, heading-path aware, stable chunk ids
    embed.py              bge-m3 via the enclave's own ollama
    index.py              Qdrant: ingest, and doc_search
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
    sql/dark_vessels.sql  the dark-vessel join — interpolate, correct, match. Readable on its own.
    gfw.py                ONLINE fetch of GFW's published detections; the cross-check
    render.py             chips, scene overview, map view — the evidence
    plots.py              validation charts
    validate.py           measure the detector against DMA ground truth
    cli.py                nightglass-spatial fetch-granules|fetch-ais|scenes|detect|dark|…
  tools/
    spatial.py            stac_search · detect_vessels · ais_match · correlate
    documents.py          doc_search — the RAG retriever behind the same boundary
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
  README.md               the index to everything below
  NOTES.md                decisions, corrections, measurements — the numbered findings
  architecture.md · quickstart.md · demo.md    what the README summarises
  air-gap.md · rag.md · detection.md · agent.md · tools.md · bundle.md
  kubernetes.md           the deep dives behind the proofs
  design-decisions.md · data-sources.md · limitations.md · roadmap.md
  repository-layout.md    this file
  design/                 the bundler's design, written before it was built
  how-it-works.svg · architecture.svg · agent-graph.svg · results.svg    the diagrams
  how-it-works.png        raster export of the first one, for anywhere SVG will not render
  social-preview.jpg      the repository's social card; set in repo settings, linked nowhere
  demo.cast · demo.mp4 · demo.gif    the walkthrough: record, watch, embed
  evidence/               committed renders — the snapshot the numbers come from
```

---

**See also:** [docs index](README.md) · [architecture](architecture.md) · [quickstart](quickstart.md)
