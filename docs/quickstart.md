# Quickstart

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

```bash
cp .env.example .env      # then set POSTGRES_PASSWORD
make preflight            # checks docker, the nvidia runtime, VRAM, the AOI config
make up                   # build + start, waits for every container healthy — no GPU? see below
make pull-models          # once, ~10 GB   ┐
make fetch-corpus         # once, ~35 MB   │
make fetch-granules       # once, 2.8 GB   ├ the only steps that touch the network
make fetch-ais            # once, 890 MB   │
make fetch-coastline      # once, 149 MB   │ (149 MB in, 713 KB kept)
make fetch-gfw            # once, ~70 detections ┘ the cross-check layer
make ingest               # chunk + embed the corpus into Qdrant (~1 min, offline)
make scenes               # catalogue the granules on disk as STAC items (offline)
make air-gap-proof        # no egress, inference works anyway
make rag-proof            # ungrounded vs grounded, and the refusal path
make dark-proof           # detector, AIS, the space-time join, and the renders
make tool-proof           # the tools over MCP, and the local model chaining them
make agent-proof          # halts at the human gate, resumes in a different container
make demo                 # the recording, live, ~60 s
make bundle-proof         # the transfer bundle — four refusals, and a restore
make k8s-proof            # the same air gap as a NetworkPolicy, against a control
```

`make` with no target lists everything.

### The two accounts

Two of those need an account, both free: `fetch-granules` wants an
[Earthdata login](https://urs.earthdata.nasa.gov) **with the ASF EULA accepted** — a valid
password on its own produces a redirect loop rather than a 401 — and `fetch-gfw` wants a
[GFW API token](https://globalfishingwatch.org/our-apis/tokens). `fetch-ais` needs neither.

Every fetch is checksummed against [`data/sources.yaml`](../data/sources.yaml), resumes a partial
download, and re-runs as a no-op; `make fetch-granules VERIFY=1` re-hashes what is already on
disk instead of trusting its size. The default set is 2.8 GB — everything the proofs and the
demo need; `ALL=1` fetches the full manifest.

### What runs without an account

A stranger with neither account still reaches a real slice of the system, and it stops at a
specific place: every target that touches the detector needs the 1.0 GB Kattegat granule, which
only `make fetch-granules` supplies, and that needs the Earthdata account above whether or not
there is a GPU behind it.

| target | what it needs |
|---|---|
| `make preflight`, `make up` | nothing |
| `make fetch-corpus`, `make ingest` | nothing |
| `make docs-search`, `make ask-docs` | nothing |
| `make rag-proof` | nothing — CPU profile: slow |
| `make test`, `make lint` | nothing |
| `make bundler-test`, `make bundle-proof` | nothing |
| `make k8s-lint`, `make k8s-render`, `make k8s-proof` | nothing |
| `make fetch-granules` | an Earthdata account, with the ASF EULA accepted |
| `make fetch-gfw` | a GFW API token |
| `make fetch-ais` | no credentials to download — but it slices the day against the granule's own acquisition time, so without a granule already on disk (`make fetch-granules` first) it downloads 890 MB and then exits 1 |
| `make dark-proof`, `make intrep`, `make demo`, `make tool-proof`, `make agent-proof`, `make validate-shift` | the granule — so, transitively, the Earthdata account above |

"Nothing" above means no account — `make ingest`, `make docs-search`, `make ask-docs` and
`make rag-proof` still need `make pull-models` (~10 GB) first, same as every other target.

The CPU profile below removes the GPU requirement from the last row. It does not remove the
granule, so it serves someone who already has an Earthdata account and no GPU, not a stranger
with neither.

### The CPU profile

Setting `COMPOSE_FILE=docker-compose.yml:docker/compose.cpu.yml` in `.env` removes the one GPU
reservation on `ollama` — `docker-compose.yml`'s `deploy.resources.reservations.devices` block —
and nothing else: same images, same steps, slower. The variable also appears, harmless, in every
service's environment, because `.env` doubles as `env_file` for all of them.

The deterministic path makes zero LLM calls: `make dark-proof` then `make intrep` produce a
factual INTREP — findings only, no assessment section, `releasable=false` by construction,
because `draft_intrep`'s own `if narrative and chunks:` gate never fires with `query` left empty.
What that path does not demonstrate is the agent's own tool choice; for that, `make ask` still
needs the model. Measured on the CPU profile of one 32-core host: `make ingest` took ~7m5s, one `make ask`
call took ~6m45s (407 s), and the 14B model held ~13.0 GiB of RAM resident. `make up` echoes
all three figures at the end when the CPU profile is active. `make demo` itself ran end to end
in 7m43s there, against 57 s on the GPU profile; `make dark-proof` took the same 4 minutes on
both, because it never calls the model. The tails of those runs are in
[evidence/proofs.md](evidence/proofs.md).

### `POSTGRES_PASSWORD`, once

It is read only at the database's first `initdb` — changing `.env` afterwards changes nothing
already on disk. Resetting it means dropping the volume, not editing the file:
`docker volume rm nightglass_postgis_data` (never `make clean`, which also removes the model and
vector-store volumes).

### One enclave per host

`docker-compose.yml` hardcodes the project name and every container and volume name, so two
checkouts on the same host share one enclave and one database rather than running side by side.

### `make demo` checks its own inputs first

Six provisioning steps — corpus, granules, AIS, coastline, GFW, models — have to be done before
the recording itself. Before any live model call, the script now runs a preflight inside the
`api` container: it checks the Qdrant collection holds points, that `stac.scenes`,
`detect.detections` and `ais.positions` each have rows, and that `data/gfw/gfw_lisbon.json`
exists. Any check that fails names the one `make` target that fixes it and exits before spending
the ~90 s of live work; on a healthy enclave the preflight prints nothing.

### On this machine

The host runs ollama as a systemd service with `OLLAMA_KEEP_ALIVE=-1`, which pins ~15 GB of the
3090 permanently and leaves the enclave's own ollama unable to load a 14B model. `make preflight`
detects this and tells you to `sudo systemctl stop ollama`. There is also a `make seed-models`
shortcut that copies the blobs already on the host into the volume instead of re-downloading them
— a local convenience, explicitly not the documented path.

### A site with no route to any of this

The quickstart above assumes a network. A real site has none, and the artifact that crosses that
gap is the transfer bundle: **18 GB behind a 3.4 MB static Go binary that refuses eight ways
before it will restore.** See [crossing the gap](bundle.md).

---

**See also:** [architecture](architecture.md) · [data sources and licences](data-sources.md)
· [repository layout](repository-layout.md)
