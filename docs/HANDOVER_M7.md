# Handover prompt — M7

> **Item 1 (the Go bundler) is done, 2026-08-04.** `bundler/`, `make bundle`, `make bundle-proof`,
> README section *Crossing the gap*, findings 57–60. Two things below turned out to be wrong and
> are marked **[CORRECTED]** where they appear: the size budget, and the conclusion drawn from it.
> The rest of this file stands, and items 2–4 are untouched.

**One sentence, if that is all you want to paste:**

> Read `docs/HANDOVER_M7.md` and the "→ HANDOFF TO M7" section of `docs/NOTES.md`, then pick one
> item from `docs/EXECUTION_SPEC.md` §M7 and build it — my recommendation is the Go bundler,
> because it is the only one that adds a language and it extends the manifest M6 just built.

The longer version below is the same thing with the reasoning attached.

---

## Start here

```bash
cd ~/Documents/dev/nightglass
make preflight && make up
make demo            # §6 end to end, both AOIs, ~57 s. The fastest way to see the whole system.
make dark-proof      # M3 — the spatial chain
make tool-proof      # M4 — MCP over stdio, the local model chaining, the INTREP guard
make agent-proof     # M5 — halts at the gate, resumes in a different container
```

`make demo` first. It is one minute and it shows every milestone at once; the proofs are for when
you need to see a specific one in detail. Together the three proofs take about eight minutes,
most of it the 14B model.

If `dark-proof` refuses with "no coastline", run `make fetch-coastline`. If anything refuses with
a missing granule or AIS file, run `make fetch-granules && make fetch-ais` — as of M6 those exist
and every input this system needs has a `make fetch-*` target.

## Where things stand

**M0–M6 are done and every "done when" is met.** The enclave is sealed, inference runs offline,
the RAG cites or refuses, the detector and the space–time join work against ground truth, the six
§5 tools serve over HTTP and MCP, the LangGraph agent halts at a human gate and resumes in a
different container, and a clone can now fetch every byte from a checksummed manifest and
reproduce the demo. The recording is `docs/demo.mp4`.

**§M7 is titled "If there's appetite", and that framing is real.** The spec is met. Nothing below
is owed to anyone, and "the system does what was asked, here is what I would do next" is a
perfectly good place to stop. Read the rest as options, not as a backlog.

## The four §M7 items, in the order I would take them

**1. The Go bundler — `docker save` + a pip wheelhouse + the model blobs → one tarball with a
SHA256 manifest and a `verify` subcommand.** My recommendation, for two reasons that are not the
obvious one.

It is the only item that adds a *second language*, and a static binary with no runtime deps is
genuinely why Go fits: the thing that unpacks an air-gapped bundle cannot itself need a Python
environment to exist first. That argument is the point, and it only lands if it is actually Go.

Less obviously, **M6 built half of it already.** `data/sources.yaml` is a committed manifest with
a URL, a byte count and a sha256 for every external input, and `src/nightglass/spatial/archive.py`
is a resumable, hash-verifying fetcher over it. The bundler is the same shape pointed at image
layers and model blobs instead of granules. Read `archive.py` before writing any of it — not to
reuse the code, but because the failure modes it names (a cached file that is the right size and
the wrong bytes; a credential that must reach exactly one host) are the same failure modes a
bundle verifier has.

**Before you start item 1, two things measured on this machine 2026-08-04.**

`go` is **not installed** — step zero is a toolchain, not a line of code. And the bundle is
bigger than "a few images":

```
docker save  nightglass/app:dev        1.16 GB
             nightglass/fetcher:dev    1.21 GB
             ollama/ollama:0.32.5      8.04 GB   <- the whole cost, essentially
             postgis/postgis:17-3.5     887 MB
             qdrant/qdrant:v1.18.3      270 MB
volume       nightglass_ollama_models  9.40 GB
                                      -------
                                      ~21 GB before a pip wheelhouse
```

**[CORRECTED]** — every image figure above is the `docker images` SIZE column, which reports the
*unpacked* snapshot on disk. `docker save` writes the compressed layer blobs, and the two differ
by 2.9×: the five images are **4.02 GB** in one archive, not 11.57 GB, and `ollama/ollama` is
3.26 GB rather than 8.04. So the ollama image is not "the whole cost" and the question of
whether it belongs in the bundle does not arise — **the model blobs are, at 10.15 GB and 56% of
an 18 GB bundle**, and they cannot shrink because GGUF is already quantised. The cheap way to get
the real number is `docker image inspect --format '{{.Size}}'`, which agrees with `docker save`
to within a megabyte. Finding 57.

`docker save` does not dedupe layers across images the way a registry does, so treat that as a
floor. **[CORRECTED]** — measured, the dedup on offer across all six images is 67 MB, 1.6%, so
the bundler writes one tar per image and spends it on a failure message that names which image.
Related and worse: `nightglass/app:dev` and `nightglass/fetcher:dev` share only 7 layers of 13
and 14 despite `FROM runtime AS fetcher`, because the 743 MB pip layer is not reproducible across
builds (finding 60).

Two consequences worth deciding up front rather than discovering: whether the ollama
*image* is even needed in the bundle when the model *blobs* are already carried separately, and
whether `verify` streams (hash while reading, never unpack) — over 21 GB the difference between
streaming and staging is the difference between a tool someone runs and one they avoid.

**[RESOLVED]** — the image is needed (blobs are weights, the image is the server that reads
them). `verify` streams, and the mechanism that makes it possible is putting `MANIFEST.json`
first in the tar: one sequential pass, no seeking, one megabyte of memory over 18 GB, and it
therefore works on stdin. A third thing nobody had flagged: the model volume also holds an
OpenSSH **private key**, and only `models/` may travel (finding 58).

Budget the session for the verify round-trip, not for the writer. Producing a tarball is an
afternoon; proving one restores into a clean Docker context is the milestone.

**2. k3s/kind deploy with a default-deny egress `NetworkPolicy`.** The spec calls this "the best
single detail in this list" and it is right: the NetworkPolicy is the Kubernetes expression of
the same idea `internal: true` expresses in compose, and showing the same boundary twice in two
substrates is a stronger claim than showing it once. It is also the smallest of the four if you
do not gold-plate the chart.

**3. The eval set — 20 questions, retrieval hit-rate@k and groundedness.** Cheapest, and it gives
a number where the README currently has a sentence ("retrieval quality is demonstrated, not
measured", in Limitations). One caveat worth keeping: a third of the corpus is synthetic and was
written for this project, so an eval over it partly measures the author's own phrasing. Say so in
the README rather than quoting the number bare — the Limitations section already sets that
precedent and it is the strongest section in the file.

**4. SBOM via syft, pinned image digests.** Half a day, no argument required, and it pairs
naturally with the bundler because both are about knowing exactly what is inside an artifact.

## Things that are true and are not obvious from the code

**Every input has a fetch target now, and one of them will rot.** The DMA S3 bucket serves daily
AIS on a rolling window of roughly eighteen months. `aisdk-2026-07-17` is inside it today; when it
ages out, `make fetch-ais` fails and the Danish validation stops being reproducible from the
manifest. The granules do not have this problem — ASF's Sentinel-1 archive is permanent. This is
stated in the README's Limitations and it is the one place where "clone and reproduce" has a
shelf life. If a bundler exists, the bundle is the fix.

**This machine holds three granules the default fetch does not.** `make fetch-granules` fetches
the `required` set — 2.8 GB, three granules — and that is what the catalogue and the recording
show. The Leixões granule and the two wrong-orbit ones are in
`data/raw/sar/_not_in_default_fetch/`, out of the non-recursive glob `make scenes` uses. Restore
with `mv data/raw/sar/_not_in_default_fetch/*.zip data/raw/sar/ && make scenes`, or
`make fetch-granules ALL=1`. They were moved so the recording would show what a clone gets rather
than what this machine happens to have.

**`make demo` needs a warm database.** The detector runs cold at 14–20 s per scene and the script
is timed against detections that already exist. Run any proof first. It says so in its own header
rather than being mysteriously slow.

**The recording is three artifacts from one recording.** `docs/demo.cast` is the untouched record;
`scripts/pace-demo.py` rewrites its timestamps (and refuses to write if a byte moved) so nothing
scrolls off screen inside 4.5 s; `scripts/render-demo.sh` renders the MP4 and the GIF and then
runs `scripts/check-demo.py`, which fails the build if any row is on screen for under three
seconds. `make record-demo` does the lot. If you change what the demo prints, re-record — do not
hand-edit the cast.

**The numbers, current as of M6.** Over the Danish validation scene `S1D_…BC13`:

```
detections 35   matched 21   dark 14   unmatched 40.0%   median match 104 m
```

**40% is not a dark-vessel rate and the code will not let you state one.** Two independent guards,
one of which (`DETECTOR_PRECISION_VALIDATED`) is a constant with a test as its tripwire. Do not
flip it. `scrub_rate_claims` is the backstop over generated text and it has fired on a real run.

## What I would not do

**Do not tune the detector to agree with GFW.** That is fitting to another detector. The AIS
validation over Denmark is the stronger evidence and it is already banked.

**Do not soften the 40%.** It reads worse than the 25% it replaced and that is the finding: the
old denominator was padded with duplicate detections of vessels that did match.

**Do not add features to the six §5 tools.** The tool surface is the contract that FastAPI, MCP
and the agent all bind to, and it does what the spec asks.

**Do not reintroduce a second human language** without saying so. Finding 48 records removing it
as a deliberate divergence from §M5/§6/§2; `bge-m3` stays because a national deployment will still
have a corpus and queries that do not share a language, but nothing shipped exercises that.

## Two habits worth inheriting

**Ask Denmark first.** Before building anything to answer a question about the detector, check
whether the DMA AIS already answers it. The fragmentation bug (finding 46) was found sideways
through a GFW comparison when one `Counter(mmsi)` over the Danish matched set would have shown it
immediately, with ground truth instead of a second opinion.

**Look at the artifact, not only at the metrics.** Finding 55 is the sharpest version of this and
it happened in M6: the demo recording shipped with truncated output — a tool call rendered as
plausible-but-incomplete JSON, and a caveat cut mid-sentence at exactly the clause carrying §7 —
while *every* automated check passed. Dwell time, screen churn, byte-identity of the paced cast:
all green, all measuring transport. Nelson found it by watching the video. Metrics tell you
whether a thing can be seen; they do not tell you whether it is true.

Related, and still true: `make lint` and `make test` do not exercise Makefile targets, the proof
scripts, or anything rendered. If you change the Makefile with a script, diff the `.PHONY`
inventory (finding 47).

## Keep the notes current

Numbered findings for surprises, README for evidence. Findings are at **56**. `docs/NOTES.md` is
the study aid and it is doing real work — most of what is above came out of it.
