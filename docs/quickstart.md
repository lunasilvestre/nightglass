# Quickstart

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

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
