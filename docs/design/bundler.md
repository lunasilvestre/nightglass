# The offline transfer bundle — design

**Status:** approved 2026-08-04, built the same day. This is the design as written *before*
implementation, kept as the record of what was intended; where the built thing diverged, the
divergence is noted in place rather than edited away. [`docs/bundle.md`](../bundle.md) describes
what actually exists.

A clone of this repository can reproduce the demo by fetching 6.2 GB from four hosts. A site with
no route to any of them cannot. This is the artifact that crosses that gap: one tarball, a SHA256
manifest, and a `verify` that refuses six different ways.

It is written in Go, and that is the point rather than a flourish. The thing that unpacks an
air-gapped bundle cannot itself need a Python environment to exist first, so it is a single static
binary with no interpreter, no shared libraries and no package index behind it.

---

## 1. What goes in, and what it costs

Measured on this machine, 2026-08-04, Docker 29.7.1 with the containerd image store.

| part | contents | bytes |
|---|---|---|
| `images/` | 6 × `docker save` | 4.03 GB |
| `models/` | 8 blobs + 2 manifests from `nightglass_ollama_models` | 10.15 GB |
| `wheels/` | 123 wheels, cp313 manylinux x86_64 | 0.17 GB |
| `data/` | 3 granules (`role: required`) + `aisdk-2026-07-17.zip` | 3.65 GB |
| | | **~18.0 GB** |

**These are `docker save` sizes, not `docker images` sizes.** The earlier M7 estimate budgeted ~21 GB
for the images alone, from the `docker images` SIZE column — which reports the *unpacked* size of
the snapshot on disk. `docker save` writes the compressed layer blobs. The two differ by 2.9×:

```
                          docker images      docker save
ollama/ollama:0.32.5           8.04 GB          3.26 GB
nightglass/app:dev             1.16 GB          0.27 GB
nightglass/fetcher:dev         1.21 GB          0.27 GB
postgis/postgis:17-3.5         0.89 GB          0.22 GB
qdrant/qdrant:v1.18.3          0.27 GB          0.08 GB
```

The consequence is not just a smaller number. The handover concluded that the ollama *image* was
"the whole cost, essentially" and asked whether it belongs in the bundle when the model blobs are
carried separately. At 3.26 GB it plainly does — and it always did, because the blobs are weights
and the image is the server that reads them. The real cost centre is **the model blobs, at 10.15 GB
and 56% of the bundle**, and nothing can be done about that: GGUF is already quantised.

**The sixth image is `alpine:3`.** Restore has to write into the `nightglass_ollama_models` named
volume, which requires a container; `scripts/seed-models-from-host.sh` already uses alpine for
exactly this. Four megabytes, and the bundle stops depending on a tool it does not ship.

**One tar per image, not one combined `docker save`.** Measured cost of losing cross-image layer
dedup: 67 MB, 1.6%. Bought with it: a failure message that names which image, and a restore that
can be done by hand with `tar xf` and `docker load` if the binary is ever unavailable. A transfer
tool that is the only thing able to read its own output is a worse tool.

### What is deliberately left out

**`id_ed25519`.** The model volume holds more than `models/`:

```
/root/.ollama/
├── id_ed25519        387 B, mode 600, -----BEGIN OPENSSH PRIVATE KEY-----
├── id_ed25519.pub     81 B
├── cache/model-recommendations.json    1315 B
└── models/           blobs/ + manifests/     <- the only part that travels
```

Bundling the keypair ships one private key to every site that restores the bundle. A fresh Ollama
generates its own on first run, so carrying it buys nothing and costs the property that two
deployments are distinct. `cache/model-recommendations.json` is the residue of finding 14's
ollama.com call — a cache, and a trace of the one outbound path the enclave exists to prevent.

**Anything under `role: optional` or `role: superseded` in `data/sources.yaml`.** The bundle
carries what the proofs and the demo need. `--all` includes the rest for a site that wants the
wrong-orbit granules to reproduce the mistake.

---

## 2. Shape: the manifest is the first member

A bundle is a tar whose **first member is `MANIFEST.json`**, and every member after it is hashed as
it streams past.

```
nightglass-bundle-0.1.0.tar
├── MANIFEST.json                              always first
├── images/qdrant_qdrant_v1.18.3.tar
├── images/…                                   × 6
├── models/manifests/registry.ollama.ai/library/qwen2.5/14b-instruct-q4_K_M
├── models/manifests/registry.ollama.ai/library/bge-m3/latest
├── models/blobs/sha256-2049f5…                × 8
├── wheels/numpy-…-cp313-…whl                  × 123
├── data/raw/sar/S1D_…BC13.zip                 × 3
└── data/raw/ais/aisdk-2026-07-17.zip
```

Manifest-first is what makes a single sequential pass sufficient: you know what to expect before
you meet it. `verify` never seeks, never stages, never unpacks, and holds one 1 MiB copy buffer
regardless of the archive being 18 GB. So it also works on a pipe, which is what an operator
actually wants when the bundle is arriving off removable media:

```
nightglass-bundle verify /media/usb/nightglass-bundle-0.1.0.tar
cat /media/usb/nightglass-bundle-0.1.0.tar | nightglass-bundle verify -
```

**No outer compression.** Layer blobs are gzip, GGUF is quantised, wheels are zip. A second pass
would spend CPU on roughly nothing, and on a pipe it would cost the streaming property that makes
the tool usable at this size. Stated as a decision rather than left as an omission.

### Both halves are already content-addressed

`docker save` does not write a flat tar. It writes an OCI layout:

```
blobs/sha256/062e450697faa5f0…
blobs/sha256/0bd98fa7977f1e75…
index.json
manifest.json          (retained for `docker load` compatibility)
oci-layout             {"imageLayoutVersion":"1.0.0"}
```

Every file inside is named by its own sha256. The ollama volume is the same idea:
`models/blobs/sha256-<hex>`. So the bundler's manifest is **a join over two content-addressed
stores that already exist**, not a new format invented for the occasion. That is why the Go side is
small enough to be stdlib plus a YAML reader.

---

## 3. The manifest, and three checks that fall out of it

```json
{
  "format": "nightglass-bundle/1",
  "created": "2026-08-04T16:20:00Z",
  "tool": "nightglass-bundle 0.1.0",
  "source": { "git_commit": "d268ee5", "git_dirty": false, "docker": "29.7.1" },
  "totals": { "entries": 141, "bytes": 18041827328 },
  "entries": [
    { "path": "images/ollama_ollama_0.32.5.tar", "bytes": 3260579840, "sha256": "…",
      "kind": "image",
      "image": { "ref": "ollama/ollama:0.32.5", "id": "sha256:…" } },

    { "path": "models/blobs/sha256-2049f5…", "bytes": 8988110688, "sha256": "2049f5…",
      "kind": "model-blob" },

    { "path": "data/raw/sar/S1D_…BC13.zip", "bytes": 1007713998, "sha256": "06322098…",
      "kind": "data", "role": "required",
      "note": "The validation scene. Every M3 number in the README is this granule." }
  ]
}
```

The `data` entries reuse `sha256`, `bytes`, `role` and `note` from `data/sources.yaml` verbatim.
`url` is dropped, because there is no URL — the bytes are in the tar. That is the whole extension:
M6's manifest says where a byte came from and what it must hash to; this one says the same thing
about a byte you are holding.

**Check 1 — data entries are re-verified against `data/sources.yaml` at create time.** If a granule
on disk does not hash to what the committed manifest says, the bundle refuses to be built. A bundle
whose contents cannot be traced back to M6's manifest would be a fresh set of unsourced bytes
wearing a checksum.

**Check 2 — model blobs are named `sha256-<hex>`, so the filename is the checksum.** Manifest,
filename and content must all three agree. Content against filename catches a corrupt blob;
manifest against filename catches a doctored manifest.

**Check 3 — the manifest is the only integrity anchor our own images have.** `ollama/ollama`,
`postgis/postgis`, `qdrant/qdrant` and `alpine:3` have registry-resolvable digests.
`nightglass/app:dev` and `nightglass/fetcher:dev` do not, because they were never pushed
anywhere. For the two images the deployment most depends on, this manifest is the only statement
of what they were.

**Measured after the fact, and it sharpens the point rather than softening it.** Docker records
a `RepoDigests` entry for a locally built image too — `nightglass/app@sha256:dc851b20…` — and
that value resolves against no registry. Nothing available at create time tells the two cases
apart; doing so means asking a registry, which is the one thing this tool must not do. So the
manifest's `repo_digest` field means *the digest Docker reported*, not *a digest you can pull*,
and it is documented in those words in `internal/manifest`.

### What the manifest cannot do

It cannot verify itself. `create` prints its digest and writes a `<bundle>.sha256` sidecar; `verify`
prints the same line. That single 64-hex string is what an operator carries out of band.

This is **integrity, not authenticity**. It proves the bundle you have is the bundle that was
built, given one value you trust by another route. It proves nothing about who built it. Signing is
a key-management problem — key custody, revocation, an offline root — that this does not pretend to
solve, and pretending otherwise would be the kind of claim the rest of this repository is careful
not to make.

---

## 4. Six ways `verify` refuses

The failure that matters is not corruption. It is **a bundle that streams cleanly to EOF and is
quietly missing a member** — finding 55's shape, truncation that looks like completion, in the one
tool whose entire job is to say "this is complete". Every check below exists because a naive
implementation passes without it.

| # | condition | message |
|---|---|---|
| 1 | bytes differ | sha256 mismatch, both digests printed in full |
| 2 | member short at EOF | **truncated**, named as such, not a generic read error |
| 3 | a manifest entry that never appeared in the stream | **the finding-55 case** — the one a naive implementation forgets |
| 4 | a member present that the manifest does not list | something was added after the fact |
| 5 | `MANIFEST.json` is not the first member | cannot stream-verify; refuse rather than silently fall back to staging |
| 6 | a path appears twice | ambiguous; the later member would win at restore |

Checks 3 and 4 together are set equality between the manifest and the stream. Neither direction is
optional and only one of them is obvious.

*Built as eight, not six.* Implementation added two the design missed: a member that is not a
regular file (`CodeMemberType` — a symlink or device node in a bundle is a bug in the writer or an
attempt at something), and a tar header whose declared size disagrees with the manifest
(`CodeSize` — cheap, and a clearer diagnosis than the hash mismatch it would otherwise become).
The count went unrevised in this document and in the README for a day, which is its own small
lesson about writing a number down in six places.

Exit codes: `0` verified, `1` refused with a named reason, `2` the bundle could not be read at all.

---

## 5. `restore` is transactional

```
nightglass-bundle restore <bundle.tar> --repo <path-to-clone> [--load] [--models-volume NAME]
```

1. Extract every member to `<path>.part`, hashing as it writes. This is the discipline
   `src/nightglass/spatial/archive.py` already uses for an interrupted download, for the same
   reason: a file that is the right size and the wrong bytes must never be mistaken for a finished
   one.
2. Run all six checks. Rename nothing yet.
3. Only when the **whole** archive has verified — including check 3 — rename every `.part` into
   place.
4. Only then `docker load` each image, and pipe `models/` into the named volume through alpine.

A half-loaded image set is a worse state than an empty one, so nothing is committed until
everything is proven. `--repo` must point at something that looks like a nightglass clone
(a `docker-compose.yml` naming the project); restoring 3.65 GB of granules into an arbitrary
directory is not a thing to do by accident.

The binary writes files and shells out to `docker` via `os/exec` for the two operations only Docker
can perform. It does not link the Docker client library — that would pull a large dependency tree
to re-implement two commands the target host already has. `os/exec` keeps the binary static and
dependency-free, which is the entire argument for it being Go.

---

## 6. Build, test, prove

**Build.** `make bundler` runs `docker build --target bin --output bundler/bin`, producing a
`CGO_ENABLED=0` static binary. The host never needs a Go toolchain — `scripts/preflight.sh` keeps
its current contract of docker and make, and gains no new line. Dependencies are vendored, so
`bundler/` is itself buildable with no package index, which is the property the bundle is about.

**Test.** `go test ./...` in the same container, wired into `make test`. Table-driven over all six
refusals, plus manifest round-trip, the `sources.yaml` cross-check, and blob-name/content
agreement. All pure — no docker, no network, no fixtures on disk beyond `t.TempDir()`. Following
the house convention that a test name is a sentence and a comment explains which real failure the
test is pinned against.

**Prove.** `make bundle-proof`, on a **~100 MB fixture** rather than on 18 GB, because a proof
nobody runs is not one:

```
1. create   a fixture bundle (alpine + qdrant + two small blobs + a few wheels)
2. verify   -> OK, entry count and total bytes
3. flip one byte      -> REFUSED, sha256 mismatch
4. truncate the tar   -> REFUSED, truncated
5. delete a member    -> REFUSED, manifest entry never appeared
6. restore into docker:dind, an empty daemon -> docker images shows exactly what was bundled
```

Steps 3–5 are three distinct refusals and they are the argument. Step 6 is the milestone the
handover names: producing a tarball is an afternoon; proving one restores into a clean Docker
context is the deliverable.

---

## 7. Limitations

**The wheelhouse does not make the image rebuildable offline.** The runtime image also needs four
apt packages — `curl`, `ca-certificates`, `libexpat1`, and `poppler-utils` in the fetcher stage —
and a wheelhouse pins none of them, so an offline `docker build` would still go looking for a
Debian mirror. §M7's own phrasing ("`docker save` + pip wheelhouse + model blobs") invites the
assumption that the three together reconstitute the system from source. They do not: **the saved
images are what make it runnable offline**, and the 172 MB of wheels buys something smaller and
real — `pip install --no-index --find-links` into a running container, to patch a dependency
without a network. Stated because the gap between those two claims is exactly where an air-gapped
deployment would discover it the hard way.

**A genuinely cold `make pull-models` is still untested**, open since M1. This does not fix it; it
routes around it. A site restoring from a bundle never runs the pull path, so the bundle makes the
gap less likely to be hit and no less real.

**`--repo` restores data next to a clone, not into a running enclave.** The enclave mounts
`data/raw/sar` and friends read-only from the host, so restoring the files is sufficient — but the
databases are not in the bundle. A restored site runs `make migrate`, `make scenes`, `make detect`,
`make ingest` as a fresh deployment would. The bundle carries inputs and images, not derived state.

**Integrity, not authenticity.** See §3.

---

## 8. What lands in the repository

```
bundler/                        Go module, vendored deps
  cmd/bundle/main.go            CLI dispatch — create · verify · restore · inspect
  internal/manifest/            the manifest type, marshal, load, validate
  internal/bundle/              create · verify · restore · inspect, one streaming walk
  internal/sources/             read data/sources.yaml
  internal/dockercli/           save · load · the volume round trip, over os/exec
docker/Dockerfile.bundler       golang:1.26-alpine -> scratch, --output a static binary
scripts/bundle-proof.sh         the nine-step round trip
```

*Built as above, with one change: the model volume's layout and its exclusions live in
`internal/bundle/create.go` next to the code that reads it, rather than in a package of their
own. It is thirty lines and one caller; a package for it would have been structure without
separation.*

Makefile gains `bundler`, `bundle`, `verify-bundle`, `restore-bundle`, `bundle-proof`. Per finding
47, the full `.PHONY` inventory gets diffed after the edit rather than eyeballed — it stands at 47
targets before this change.

`.gitignore` gains `nightglass-bundle-*.tar` and its sidecar; neither exists in the current rules
and an 18 GB file in the repo root would otherwise be tracked.

README gains a section between "Six tools, two consumers" and "Design decisions", and a
design-decision row next to *A committed manifest, gitignored bytes* — the row this extends. The
line `Offline CI/CD — the bundler (…)` under *What I'd do with three more weeks* is removed, because
this is that.

`docs/NOTES.md` gains findings from 57 for the `docker save` measurement, the ollama keypair, and
the wheelhouse's real scope.
