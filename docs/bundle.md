# Crossing the gap: one tarball, and the ways it refuses

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

Everything else in NIGHTGLASS assumes the enclave was *built* somewhere with a network. A real site has no
route to ASF, to the Danish Maritime Authority, to a container registry or to PyPI — and one of
those four is on a clock. The DMA serves daily AIS on a rolling ~18-month window, so on the day
`aisdk-2026-07-17` ages out, the Danish validation stops being reproducible from
[`data/sources.yaml`](../data/sources.yaml) and no amount of code fixes it. A bundle is what
outlives that.

```bash
make bundler          # build the static binary — the host never needs Go
make bundle           # ~18 GB: images, model blobs, wheels, granules, the AIS day
make verify-bundle    # stream it, check every byte against its own manifest
make restore-bundle   # verify, then docker load and place the data
make bundle-proof     # the whole round trip on a 100 MB fixture, ~60 s
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
on it. Truncation that looks like completion is the worst shape of failure to have in
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
[`spatial/archive.py`](../src/nightglass/spatial/archive.py) already uses for an interrupted
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

The working estimate had budgeted ~21 GB for this and named the ollama image as "the whole cost,
essentially". Both numbers came from the `docker images` SIZE column, which reports the *unpacked*
snapshot; `docker save` writes the compressed layers, and the two differ by 2.9× — the five
stack images are 11.57 GB by that column and **4.02 GB** in an archive. The real cost centre is
the model blobs at **56%**, and nothing can be done about those because GGUF is already
quantised. Worth writing down: a number copied out of a tool's
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
and `cache/model-recommendations.json` is the residue of an ollama.com call made before the
enclave was sealed — a cache, and a trace of the one outbound path the enclave exists to prevent.
`make bundle-proof` asserts the archive contains no `id_ed25519`, rather than trusting that the
exclusion stayed written.
