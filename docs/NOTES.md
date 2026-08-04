# NIGHTGLASS — Notes

Decision log, things tried that failed, open questions. Per EXECUTION_SPEC §9.

---

## M7 item 1 — done 2026-08-04. A bundle that refuses, and a size budget that was wrong.

§M7's first item, the Go bundler. Taken because it is the only one that adds a language and
because `data/sources.yaml` plus `spatial/archive.py` were already half of it — the bundler
extends M6's manifest from *fetch* to *carry*, and the failure modes `archive.py` names (a
cached file that is the right size and the wrong bytes; a partial transfer that opens without
complaint) are the same failure modes a bundle verifier has.

`bundler/`, Go 1.26, stdlib plus `gopkg.in/yaml.v3` (vendored, so the tool that makes an
offline artifact is itself buildable offline). 3.4 MB, `CGO_ENABLED=0`, statically linked. Four
subcommands: `create`, `verify`, `restore`, `inspect`. `make bundle-proof` is the round trip, on
a fixture, in 60 s.

**The shape.** A bundle is a tar whose first member is `MANIFEST.json`. That is what makes
`verify` one sequential pass — no seeking, no staging, one megabyte of memory across 18 GB —
which is what lets it run on a pipe as the bundle comes off the transfer medium. The cost is
that `create` has to read everything before it can write the first member, and that is the right
side to put it on: create runs once per bundle, verify runs every time one moves.

**`restore` reuses `verify`'s pass rather than reimplementing it.** One `walk` function, one
sink parameter: nil discards the bytes, non-nil writes them. A restore with its own copy of the
checks is a restore whose checks can drift from the ones the operator ran.

**Two small ones not worth a number.** `docker run -v <relative>:/out` treats the source as a
**named volume**, not a directory, and fails with a complaint about invalid characters in a
volume name — which is a long way from "that path is relative". The staging directory defaulted
to `<out>.staging` and was relative, so the wheelhouse step died 3.5 minutes into a build with
everything else already staged; `filepath.Abs` at the argument boundary is the fix. Separately,
`short()` elided long paths to 46 *bytes* while `fmt`'s `%-46s` pads in *runes*, so every elided
line sat two columns out — the ellipsis is one column and three bytes. Both were caught by tests
rather than by looking, which is the correct direction for this kind of thing.

### 57. ⭐⭐ The M7 handover's bundle budget was wrong by 7 GB, and its conclusion was inverted

`docs/HANDOVER_M7.md` sized the bundle at "~21 GB before a pip wheelhouse" and concluded that
`ollama/ollama:0.32.5` at 8.04 GB was "the whole cost, essentially", raising the question of
whether that image belonged in the bundle at all when the model blobs were carried separately.

Both numbers came from the `docker images` SIZE column, which reports the **unpacked** snapshot
on disk. `docker save` writes the **compressed** layer blobs. Measured, on this machine:

```
                            docker images    docker save
ollama/ollama:0.32.5             8.04 GB        3.26 GB
nightglass/app:dev               1.16 GB        0.27 GB
nightglass/fetcher:dev           1.21 GB        0.27 GB
postgis/postgis:17-3.5           0.89 GB        0.22 GB
qdrant/qdrant:v1.18.3            0.27 GB        0.08 GB
                                --------       --------
all five, one archive           11.57 GB        4.02 GB
```

`docker image inspect --format '{{.Size}}'` agrees with the `docker save` column to within a
megabyte, which is the cheap way to get the real number without writing 4 GB.

So the ollama image was never the cost centre. **The model blobs are: 10.15 GB, 56% of the
bundle**, and nothing can be done about that because GGUF is already quantised. The question the
handover raised — is the ollama image needed when the blobs travel separately — also answers
itself once it is 3.26 GB rather than 8, and it always had the same answer for a different
reason: the blobs are weights and the image is the server that reads them.

**General form, and it is finding 21's again in a different costume.** A number copied out of a
tool's summary column is a measurement of that column. `docker images` is answering "how much
disk is this using", `docker save` is answering "how much will I have to carry", and those are
different questions that happen to share a unit.

### 58. ⭐ The model volume holds a private key, and a naive tar of it ships one key to every site

`scripts/seed-models-from-host.sh` and the M0 decision table both describe the model store as
"a tar of one directory", which is true of `models/` and not true of the volume:

```
/root/.ollama/
├── id_ed25519        387 B, mode 600, -----BEGIN OPENSSH PRIVATE KEY-----
├── id_ed25519.pub     81 B
├── cache/model-recommendations.json    1315 B
└── models/           blobs/ + manifests/
```

`id_ed25519` is Ollama's instance identity. Bundling it would ship one private key to every site
that restores the bundle, and a fresh Ollama generates its own on first run — so carrying it
costs the property that two deployments are distinct and buys nothing. `cache/` is the residue
of **finding 14**'s ollama.com call: a cache, and a trace of the one outbound path the enclave
exists to prevent. Only `models/` travels, and `make bundle-proof` asserts the archive contains
no `id_ed25519` rather than trusting that the exclusion stayed written.

Worth noting the direction this was found from: the volume was inspected to answer a question
about *size*, and the keypair was in the listing. Looking at the artifact rather than at the
metric, again.

### 59. Go's `flag` stops at the first positional, and the failure is a flag that does nothing

`nightglass-bundle restore bundle.tar --into /r --install --repo .` parsed as one positional and
four ignored arguments, because `flag.Parse` stops at the first non-flag token. The refusal was
`restore needs one bundle` — which is at least loud.

The dangerous version is the one that is not. `verify bundle.tar -v` would have silently dropped
`-v`, and if the flag order had been the other way round for `--install`, a restore would have
verified, installed nothing, and exited 0. **A flag that silently does not apply is worse than an
argument error**, and it is the same category as finding 34: partiality that does not announce
itself. Fixed by re-parsing the remainder after lifting the positional out, so flags on either
side of the file work.

### 60. `FROM runtime AS fetcher` does not mean the two images share the expensive layer

`nightglass/app:dev` and `nightglass/fetcher:dev` share **7 layers of 13 and 14**. The 743 MB
`pip install` layer is not one of them, despite the Dockerfile building `fetcher` from
`runtime`. They were built at different times, and a pip install is not reproducible: the same
`RUN` produces different bytes, so the layer digest diverges and everything above it with it.

Consequence for the bundle: saving both costs 0.50 GB against the ~0.27 GB they would cost if
they shared, and a single `docker save` of all six images recovers only 67 MB of cross-image
dedup — 1.6%, which is why the bundler writes one tar per image and buys a failure message that
names which image instead. Building both in one `docker compose build` would collapse it
properly. Not done: it is a docker-build concern rather than a bundler one, and 230 MB on 18 GB
did not justify touching a build that works.

---

## M6 — done 2026-08-04. A clone can now reproduce it, and reproducing it found a bug.

`make fetch-granules` and `make fetch-ais` were the last two things standing between this
repository and *reproducible*. Everything else already had a provisioning step; the granules and
the Danish AIS day had been staged by hand during pre-dev, and `data/` is gitignored, so every
number in the README was true and none of it could be regenerated anywhere but here.

### 50. ⭐⭐⭐ The acquisition-window CSV had a rule in it that the code did not have

The strongest argument for building the fetchers is what happened the first time the fetched
data was used instead of the hand-staged data.

`data/interim/ais_kattegat_20260717_0513-0534.csv` was cut by hand during pre-dev. `fetch-ais`
cuts the same window out of the same daily file the way `DMAFileSource` actually reads it — and
got **10,230 more rows and 11 more MMSIs** over an identical bbox and clock window. Broken down
by DMA's "Type of mobile" column:

```
                new slice   hand-made slice
  Class A         104,590           102,660
  Class B          30,239            29,796
  Base Station      7,630                 0     <-- shore transmitters
  AtoN                227                 0     <-- buoys, beacons, platforms
```

The hand cut had silently kept only Class A and Class B. **The code had no such rule.** So the
matcher, run the way the repository documents, would have been free to match a detection against
a navigation buoy or a shore station and report it as a vessel that had declared itself — the
exact opposite of the failure the system is careful about everywhere else, and one that would
have made dark detections *disappear* rather than appear.

The fix is `VESSEL_CLASSES` in `_parse_dma_row`, not a filter in the slicer: the rule has to
apply whether the matcher reads the slice or the 890 MB daily zip, and putting it in the slice
would have preserved the bug for anyone who read the zip. The remaining difference between the
two files is 23 seconds at the start of the window — the hand cut began at 05:13:00, the code's
±11 min begins at 05:12:36 — and it changes nothing: same 35 detections, same 21 matched, same
14 dark, same MMSIs, same distances, same 104 m median.

The general form is worth keeping. **A hand-staged artifact can carry a rule nobody wrote down**,
and it will keep carrying it until something regenerates it. Nothing in `make lint`, `make test`
or any of the four proofs could have found this, because all of them read the same hand-made file.

### 51. `data/sources.yaml` — a committed manifest is what makes the numbers traceable

One file, eight entries, each with a URL, a byte count, a sha256 and a role. The fetchers verify
against it and refuse a mismatch. Two things fell out of it that were not the point when it was
written:

- **The roles made the wrong-orbit granules useful.** `role: superseded` on the two path-23
  scenes keeps the mistake in the record — they sat offshore in the Atlantic and missed the
  Tagus approaches entirely — without making anyone download 1.4 GB to reproduce it. `ALL=1`
  fetches them. It replaced two hand-written text manifests, one of which superseded half of the
  other, which is exactly the state that makes a clone fail.
- **The default set is 3 granules, not 6, so this machine was wrong.** Aligning the host to what
  `make fetch-granules` actually gives changed `stac_search` over Lisbon from 3 scenes to 1 and
  removed a caveat from the demo INTREP. Worth knowing: *the machine that builds a thing is the
  worst place to test whether it can be built.*

### 52. "We have seen this URL before" is not a redirect loop

ASF's download is six hops: `datapool` → `sentinel1` → `urs.earthdata.nasa.gov/oauth/authorize`
→ `sentinel1/login?code=…` → **the same `sentinel1` granule URL as hop 1** → signed CloudFront.
The OAuth round-trip legitimately returns to a URL it has already visited, this time holding a
session cookie.

A first draft used URL identity as the loop guard and refused every download with a confident,
specific and completely wrong diagnosis — "this is what an unaccepted EULA looks like" — for a
credential whose EULA was accepted and which `curl` had just used successfully. Bounding the
*hop count* is the guard that works. The lesson is about the shape of the error message rather
than the guard: a wrong diagnosis stated precisely costs more than a vague one, because it sends
the reader to check the wrong thing.

Related, and deliberate: every ASF example reaches for `curl --location-trusted`, which means
*send the password to whatever host redirects you*. `archive.py` walks the chain itself and
attaches Basic auth only when the hop is `urs.earthdata.nasa.gov`. The signed URL at the end
carries its own authorisation and has no use for a password.

### 53. The demo shows two AOIs, and that was the decision M6 had to make

§6 says record over the Lisbon AOI. The Lisbon AOI has no AIS. The options were in the M6
handoff; the recording takes option 2, and the ordering is what makes it work: Lisbon takes the
question and produces 71 detections **with the verdict withheld**, the corpus is asked why a
missing transponder is a lead rather than a finding (IMO A.917(22): the master may switch AIS
off, and must log it), a human releases the report from a different container, and only then
does Denmark carry the claim about the matcher. The GFW cross-check answers "so what did you do
without AIS" last.

Two smaller decisions inside it:

- **One recording, retimed for a viewer, and the retiming is checked.** A live capture is paced
  for the machine, and this one had both failure modes at once: 30.5 s of frozen screen while
  the model chose its tools, then **33 lines in a single event** — a whole 34-row screen — that
  the next section scrolled away within a second. `scripts/pace-demo.py` rewrites timestamps and
  nothing else; it diffs the output stream before and after and refuses to write if a byte moved.
  The rule it enforces is the one a viewer cares about — `t[n] >= t[n-height] + 4.5 s`, so
  nothing scrolls off the top until it has been readable — which turns "dump a screen" into a
  scroll without anyone tuning per-section delays.
- **The check found the bug the pacing introduced.** `check-demo.py` measures per-row dwell from
  the timeline *and* slices the encoded video at 1 fps. It first reported a comfortable 4.5 s
  floor over a video whose closing shot lasted one second: the 8 s tail hold had been written as
  a trailing empty event, agg drops events with no bytes, and the arithmetic was crediting time
  the video never had. The hold moved to `--last-frame-duration`, and the checker now takes its
  end-of-video from `ffprobe` rather than from the cast — so the two measurements can disagree,
  which is the only way one can catch the other.
- **`tee /dev/stderr` reorders a script the moment stdout is not a terminal.** The agent's output
  has to stream while it is produced, and capturing the thread id through stderr put the whole
  transcript before the section headers as soon as the run was redirected to a file. `tee` to a
  temp file instead.

### 54. Two metrics that measured the tool instead of the thing

Both found while checking the recording, and the same shape twice.

**`agg --idle-time-limit` defaults to 5 seconds, not to "off".** Omitting the flag to get a
full-fidelity render produced a 36 s file from a 62 s cast — a quarter of the recording gone
silently, with nothing in the command to point at. Every render now passes it explicitly,
including the one that wants no cap at all (`600`).

**"How long does a frame stay identical" measures the codec.** The first version of the video
check hashed each 1 fps sample and reported the longest identical run as 3 s over a video whose
final shot is held for 8. H.264 is lossy: two decoded frames of a motionless screen are never
byte-identical. Comparing against a threshold instead reports the hold correctly. A metric with
a hard-coded notion of "the same" tends to be measuring the pipeline rather than the content.

### 55. ⭐⭐ Truncation that looks like completion, in the one place it must not

Watching the rendered video rather than reading its metrics found this, and
nothing else would have. Two renderers built a tool call as

```python
f"{tool}({json.dumps(args)[:90]})"      # slice the arguments, add the paren
```

which on a real call put this on screen, in the demo:

```
1. correlate({"bbox": [-10.5, 38, -8.5, 39.5], … "min_length_m": 20, "star)
```

That is not truncated JSON, it is **plausible** JSON — balanced, syntactically
innocent, and missing the entire time window. A reader cannot tell it from the
truth. The system's whole argument is that a claim can be traced to what
produced it, and the display of what produced it was quietly lying about its
own arguments.

The caveats were worse, because a caveat is the part of an INTREP that exists to
be read in full. `[:150]` cut

> NO AIS was available for this acquisition window, so none of the 71
> detection(s) above has been assessed against AIS. They are detections, not
> dark de‸

exactly at the sentence that carries §7. The honesty mechanism was being cropped
for width, and it had already survived three recordings and a frame-by-frame
review because every check was measuring *timing*.

Fixed by wrapping in `nightglass/display.py`, shared by both callers because
both had the identical bug and fixing one would have left the other. Three tests
pin it: a call round-trips to the same JSON whatever the line breaks did, a
caveat round-trips to the same sentence, and an identifier is never split across
a line — a `det-…` id broken in half stops being greppable, which is most of
what an id is for.

**The general form.** Every automated check here passed on the broken output.
Dwell time, screen churn, byte-identity of the paced cast — all green, all
measuring the transport. What was wrong was *the content of a line*, and the
only instrument that reads that is a person looking at it. Metrics say whether
you can see it; they do not say whether it is true.

### 56. The English conversion missed the Makefile

Finding 48 recorded the switch to English. `make help` was still offering
`make ask Q="Houve alguma...?"` a milestone later, along with two other targets and two module
docstrings, because the sweep went through templates, prompts and corpus and not through the
target descriptions. Grep for the *content* — the same lesson as the bilingual `REFUSAL_TEXT`,
in a file nobody thought of as carrying user-facing text.

---

## Post-M5 — English throughout

### 48. Removing a second language removed a demonstration, and the honest move was to say so

The system now works only in English: the six §5 tools, the agent graph, the INTREP templates,
the proof scripts and the six synthetic memos that were drafted in another language. `bge-m3`
and the multilingual corpus metadata stay, because a national deployment will still have a
corpus and queries that do not share a language — but **nothing shipped exercises that any
more**, and the README now says so instead of carrying a cross-lingual retrieval claim it can no
longer support. The measured separation that justified the model choice (0.842 against a
translated equivalent, 0.283 against unrelated text) is kept here as the reason, marked as no
longer reproducible from this corpus.

Three things worth knowing about the conversion:

**Finding 11 survived, and got sharper.** Asked *"what is a dark vessel?"* ungrounded, qwen2.5
now states the term "isn't a standard term in common usage" and offers literature, philosophy,
art symbolism and *retrocomputing*. Not one reading is maritime. The original run in another
language answered "painted in dark colours" — a different wrong answer to the same effect. The
finding is about the absence of a prior and never depended on the language it was found in, so
it was re-verified rather than translated.

**The refusal string was bilingual and nobody noticed.** `REFUSAL_TEXT` read
`"Not supported by available sources. / <the same in another language>"` and survived every
sweep of the templates, because it lives in `rag/answer.py` with the M2 generation code rather
than with the M4 report templates. Grep for the *content*, not for the feature you think you are
removing.

**Ingest is idempotent by chunk id, which is the wrong property here.** Renaming six documents
created six new sets of points and left the old ones in Qdrant, so `make ingest` reported a
healthy corpus while the index still answered in the old language. `make ingest RECREATE=1` is
the fix, and the idempotence that makes ordinary re-ingestion safe is exactly what hides a
rename.

### 49. ⭐ The English rewording made the model prefer `correlate`, and the recovery is the evidence

Rewording the chaining question in English dropped `make tool-proof` below §M4's bar twice: the
model answered with `correlate` alone (1 tool), then `correlate` + `doc_search` (2). That is not
a regression — `correlate` runs all three spatial steps internally and is the better choice for
a question phrased as one request. The bar counts *distinct tools*, and building a good
orchestration tool is in tension with it.

Asking the question the way an analyst actually would — the scene first, then the detections in
it — restores the chain. And the run that restored it is worth more than the clean one it
replaced:

```
1. stac_search(...)                                    -> count=2
2. detect_vessels({"scene_id": "S1A_IW_SLC__…_NWJ.csv"})
   ERROR  no scene '…' in the catalogue. Run `make scenes` …
3. ais_match({"detections": ["det-20260717-0523-…-0", …]})
   ERROR  10 detection id(s) are not in detect.detections … ids are assigned by
          the detector run and are not guessable.
4. stac_search(...)   REPEAT — same tool, same arguments, told to advance
5. detect_vessels({"scene_id": "S1D_…_BC13"})           -> count=35
6. ais_match({"detections": [35 real ids]})             -> matched 21, unmatched 14
```

The model **invented a scene id and ten detection ids**, and every one was refused rather than
absorbed. Then it re-issued an identical `stac_search` and the repeat detector caught that too.
Three guards fired in one run, the model recovered, and the final numbers are the hand-checked
ones. A system that accepts a plausible-looking invented id is a system whose provenance chain
means nothing; this is what it looks like when it does not.

**It reproduces exactly.** Four consecutive `make tool-proof` runs produced byte-identical
transcripts — same invented ids, same refusals, same recovery, same 35/21/14 — which is what
temperature 0 over a fixed database should give and is worth knowing before anyone treats a
differing run as noise. The script now says up front that the ERROR and REPEAT lines are
expected, because a reader meeting two red lines in a proof reasonably assumes something broke.

### Deliberate divergence from EXECUTION_SPEC

§M5's original done-when named a specific language, §6's demo query was written in it, and §2's
`bge-m3` row justified the model by it. All three have been reworded. The spec is the contract
and this was a decision taken against it, so it is recorded here rather than left for someone to
find as a discrepancy.

---

## Post-M5 — the merge step, and the number it made worse

### 46. ⭐⭐⭐ 45 matched detections were 18 ships, and fixing it moved 25% dark to 40%

Finding 38 caught the detector counting one vessel several times over Portugal, using GFW as the
instrument. The instrument was wrong — not useless, but second best, and I should have reached
for the one already on the bench. **Denmark has AIS**, so the question "are these detections the
same ship?" has a ground-truth answer that costs one query:

```
matched detections 45   distinct MMSIs behind them 18
one MMSI accounts for 6 of them

merge radius   clusters   multi-member   same MMSI   DIFFERENT MMSIs
      100 m         42            14           14            0
      200 m         35            14           14            0
      300 m         31            12           12            0
```

**Zero clusters mix MMSIs at any radius.** Every group of nearby detections is one ship,
confirmed by transponder. So merging is not a heuristic here, it is a correction with ground
truth behind it, and `merge_radius_m = 200.0` sits in the middle of the range AIS says is safe.
Single-link linkage, because azimuth smear is a *linear* streak and a chain of fragments along
the flight direction is exactly the shape that should collapse.

**The result is worse than what it replaced, which is the point.**

| Kattegat, scene BC13 | before | after |
|---|---|---|
| detections | 60 | **35** |
| matched | 45 | 21 |
| unmatched | 15 | 14 |
| **unmatched fraction** | **25.0%** | **40.0%** |
| median match distance | 119 m | 104 m |
| detected-vs-AIS length | 1.15×, r = 0.198 | **1.05×, r = 0.271** |

The old 25% was flattered by a denominator padded with duplicates of vessels that *did* match:
matched detections collapsed 45 → 21, unmatched barely moved, 15 → 14. Which is itself
informative — **dark detections are isolated, matched ones are fragmented**, exactly what you
would expect if the unmatched residue is clutter and single spurious blobs rather than pieces of
real ships.

40% is the number §3.2 warns about by name: *"if your pipeline reports 40% dark, it's broken."*
It does not change the conclusion, it sharpens it. The system already refuses to quote a dark
rate on precision grounds, and the refusal now has a number behind it that makes the reason
obvious rather than arguable.

**What did not change, checked rather than assumed.** Recall is identical with merging on and
off — 14 of 18 AIS vessels ≥ 30 m inside the footprint, both ways — because merging only ever
folds a detection into a *brighter neighbour within 200 m*, and a vessel that was found stays
found. The azimuth-displacement sign is still −1. Length agreement improved, which makes sense:
a fragment is a truncated hull.

**And over Portugal the fragments vanish cleanly.** 133 detections → 71, while agreement with
GFW holds at 49 of their 66. The 84 detections GFW did not share become 22, and where 61% of
them used to sit within 200 m of something we had already counted, **now 0% do** — median
distance to the nearest agreed detection 26 km. The residue is genuinely isolated: extra
sensitivity or extra false alarms, not double counting.

### 47. A Makefile section disappeared into a `str.index` prefix match

`t.index(".PHONY: ask")` matches `.PHONY: ask-docs`, which appears earlier in the file. An edit
meant to replace one target replaced everything from there to the M5 block — the whole
`##@ Spatial (M3)` section, `ask-docs` and `rag-proof` — and it shipped, because `make lint` and
`make test` do not exercise Makefile targets and the target I was testing still worked.

Caught by `make gfw-compare` failing with "No rule to make target" two commits later. Restored
from `git show 1c0e57a:Makefile` and verified by diffing the full `.PHONY` inventory rather than
by looking. The lesson is the same one finding 21 records in a different medium: it was
invisible in everything I was checking.

---

## M5 — done 2026-08-04. A graph that genuinely stops, and an answer nobody generated.

`make agent-proof` is the milestone: an analyst question drafts an INTREP, the graph halts at
`HUMAN_GATE`, the **container exits**, and a different container resumes it from Postgres.

### 41. ⭐ "Inspectable persisted state" is true, and not in the way the docstring first claimed

The checkpointer is `PostgresSaver`, not `MemorySaver`, and the difference is the milestone:
`MemorySaver` satisfies the same API while keeping the state inside the process that is supposed
to have stopped. With Postgres the halted run outlives its container — verified by drafting in
one and releasing from another, with nothing shared but the database.

But the obvious stronger claim is false, and an early draft of `graph.py` made it. LangGraph's
serialiser writes **msgpack**, not JSON, so `convert_from(blob,'UTF8')` fails on every value
channel:

```
   channel   |  type   | bytes
-------------+---------+-------
 correlation | msgpack | 78746
 intrep      | msgpack | 17600
 chunks      | msgpack |  7094
```

What plain SQL gives you is real and worth stating precisely: the run exists, which thread it is,
where it stopped, which channels it has populated and how big they are. Reading the *payload*
goes through the checkpointer — which is what `nightglass-agent show` does. Checked rather than
assumed, and the docstring now says the narrower true thing.

### 42. ⭐ The rate scrubber fired inside the graph, on the first real run

M4 built `scrub_rate_claims` as a backstop behind two weaker layers and noted it was the only
one that was a check rather than a request. On M5's first end-to-end run over Denmark the draft
came back with a caveat nobody wrote by hand:

> *1 generated claim(s) were removed before drafting because they stated a rate of unmatched
> detections.*

The model, asked for a documentary assessment, wrote a rate into it. The guard removed the claim
and recorded that it had. Neither the prompt layer nor the schema layer stopped it; the check
did. That is the entire argument for having a check, arriving unprompted.

### 43. A display format is not an interchange format

The `tools` node first tried to rebuild `Chunk` objects out of what `chaining.py` hands the
model — and crashed on `doc_id` and `score` missing. Correct failure: that structure is a
*compaction for a context window*, it drops fields on purpose (finding 34), and coupling the
report's evidence to it would have meant the citations were whatever happened to be convenient
to show a 14B model.

The fix takes the model's **queries** rather than its results and re-runs `doc_search` in the
graph, which costs one embedding call and yields real `Chunk`s with scores. The model still
chooses what to look up — the genuine judgement — and the evidence is retrieved properly.

### 44. Where the model is allowed to act, and where it is not

Worth recording as a whole, because it is the shape of the milestone rather than one bug:

| node | who decides | why |
|---|---|---|
| `parse` | model | language, lookback, whether documents are needed — all recoverable if wrong |
| | **not** model | **the bbox.** §3.1 makes the AOI configuration; a model inventing one searches the wrong ocean and returns cleanly |
| `plan` | nobody | facts about config and the database, all checkable — asking a model would be asking it to guess |
| `tools` | model | which documentary context the report needs, bounded, with M4's two guards |
| `correlate` | nobody | runs deterministically over the configured AOI whatever the model did |
| `draft_intrep` | mixed | findings templated from the `CorrelationResult`; assessment generated, cited, scrubbed |
| `HUMAN_GATE` | human | the only path to `releasable=True` |
| `release` | nobody | prose assembled from the report's own fields |

The M4 conflation bug — every per-scene count right, and "of the 60 detections … 15 were not"
across two scenes — is structurally unreachable in the released answer now: nothing generates it.

### 45. The loop-breaker's two-strike shape survived contact with a second use

`chain()` gained a `system` override so the graph could narrow it to context-gathering while
inheriting the guards unchanged. On the M5 runs the model called `correlate` inside the tools
node despite being told the correlation runs afterwards — harmless, because its numbers are
discarded and the deterministic node runs regardless, which is the point of putting the backbone
outside the model's reach rather than instructing it harder.

### Performance, M5

| step | time |
|---|---|
| `ask` to the gate (parse + tools + correlate + draft) | ~2 min, most of it the 14B model |
| `approve` — resume, release, render | ~4 s |
| `pending` / `show` | ~3 s, dominated by container start |

---

## → HANDOFF TO M7 (read this first in a fresh session)

**M0–M6 are done.** The full brief is `docs/HANDOVER_M7.md`; this is the part that belongs in the
decision log rather than in a prompt.

`§M7` is titled "If there's appetite" and that is accurate — the spec is met and stopping here is
a defensible end state. The four items, in the order I would take them, with the reasoning in the
handover: **the Go bundler** (the only one that adds a language, and `data/sources.yaml` +
`spatial/archive.py` are already half of it), **the k3s NetworkPolicy** (the same boundary
expressed in a second substrate, and the spec calls it the best detail in the list), **the eval
set** (cheapest, turns a sentence in Limitations into a number), **SBOM and digest pinning**.

**Item 1 is done** — see the M7 section at the top of this file, findings 57–60. Three remain,
and the order above still holds for them. Two things item 1 changed for whoever takes the rest:
the bundle manifest is the only integrity anchor `nightglass/app:dev` and `nightglass/fetcher:dev`
have, which is most of the argument for item 4 already made; and `bundler/` establishes that a
second language costs the host nothing if it is built in a container and delivered as a static
binary, which is the same trick a k3s chart would want for any tooling it needs.

### Still open, in priority order

1. **The 40% unmatched rate.** Unchanged and guarded in two places. Separating harbour structures
   and fixed installations from vessels is the real fix. M6 moved one step toward it by accident:
   base stations and AtoN are now excluded from the AIS side (finding 50), so what remains in the
   40% is genuinely unexplained rather than partly buoys.
2. **`make fetch-ais` has a shelf life.** The DMA bucket is a rolling ~18-month window. When
   `aisdk-2026-07-17` ages out the Danish validation stops being reproducible from the manifest.
   A bundle is the fix, which is one more argument for taking M7 item 1.
3. **A genuinely cold `make pull-models`** — unchanged since M2. Still never run on a machine
   without the blobs.
4. **Citation entailment** — unchanged from M2. The refusal path catches unsupported claims, not
   claims that cite a real chunk which does not say what the claim says.
5. **Nothing exercises cross-language retrieval.** `bge-m3` stays as insurance; the README says
   so rather than carrying a claim it cannot support.

---

## → HANDOFF TO M6 — ✅ SUPERSEDED

*M6 is done: `make fetch-granules`, `make fetch-ais`, `data/sources.yaml`, `scripts/demo.sh` and
the three demo artifacts. Kept for the reasoning it records — in particular the recording decision, which
was taken as option 2. See findings 50–54 above.*

**M0–M5 are done.** What remains is §M6: the README's architecture diagram, the design-decision
table, the licence and limitations sections — mostly written already — and a 90-second recording.

### Run this first

```bash
cd ~/Documents/dev/nightglass
make preflight && make up
make dark-proof     # M3 — the spatial chain
make tool-proof     # M4 — MCP over stdio, the local model chaining, the INTREP guard
make agent-proof    # M5 — halts at the gate, resumes in a different container
```

### The one thing M6 has to decide

**§6 says record the demo over Portugal, and Portugal has no AIS.** The tools are honest about
it — `ais_match` refuses, `correlate` returns detections with the verdict withheld, the INTREP
leads with "these are detections, not dark detections" — but a recording of a refusal is a
strange demo. Three options, in the order they are worth trying:

1. **Record Portugal as detection + cross-check.** `make fetch-gfw && make gfw-compare` gives a
   detection-for-detection comparison against a published layer over the identical granule, plus
   finding 38's fragmentation result. Add the grounded RAG answer and the INTREP with its
   refusal. This is the honest Lisbon-AOI demo and it needs nothing new built.
2. **Record Denmark for the correlation half**, Portugal for the language and the cross-check.
   Two AOIs on screen is §3.1's argument made visible rather than asserted.
3. **Catch a live Lisbon-AOI overpass** with an aisstream recorder running. Real, but it is a
   multi-day dependency and the feed still cannot support a rate.

Option 1 or 2. Do not let the recording imply a dark-vessel finding over the Lisbon AOI.

### Everything else worth knowing is in the M5 handoff below

Still accurate: the tool surface, the guards, the AOI collisions in the Makefile
(`AOI` defaults to kattegat; `GFW_AOI` and `AGENT_AOI` exist because of it), and the host state.

---

## → HANDOFF TO M5 — ✅ SUPERSEDED

*M5 is done. Kept for the reasoning it records; the current handoff is above.*

**M0–M4 are done.** `make tool-proof` runs the whole tool layer and is the fastest way to see the
state of things. Everything below is what a new session cannot infer from the specs or code.

### Run this first

```bash
cd ~/Documents/dev/nightglass
make preflight && make up
make dark-proof    # M3 — the spatial chain, and the numbers M5 must not overstate
make tool-proof    # M4 — MCP over stdio, the local model chaining, the INTREP guard
```

`tool-proof` takes ~3 minutes, most of it the 14B model thinking. If `dark-proof` refuses with
"no coastline", run `make fetch-coastline` first.

### What M5 inherits, and the one thing to build differently

**The six §5 tools are done and are pure functions over PostGIS.** `nightglass.tools` is the
single definition; FastAPI and FastMCP both import it and neither owns any logic. The LangGraph
agent should import the same functions **in-process** — there is no reason for it to go through
HTTP to reach code in its own image.

**`nightglass/tools/chaining.py` is the M4 proof and the M5 skeleton.** It is one `while` loop
with no framework: max-iterations, plus the same-tool-same-arguments detector §M4 asked for.
Both guards fired on real runs (finding 36) — do not drop them when the graph replaces the loop.
Two details worth keeping:

- The repeat detector **must not stop on the first repeat.** Re-calling can be correct when a
  result genuinely does not satisfy the request. First strike puts the previous result back in
  the transcript and says "advance"; second strike forces an answer. That two-strike shape is
  what turned a context-burning loop into a 3-iteration run.
- The model's tool schema **deliberately does not expose `radius_m` or `time_window_min`**
  (finding 35). Keep it that way in the graph. The §5 Python signature still has them for
  analysts and for the agent's own use.

**⚠️ Build the final answer from the `CorrelationResult`, not from the model's recollection.**
This is the single most useful thing to carry over. In the last proof run the model got every
per-scene count right, listed 30 real detection ids, and *still* summarised them as "of the 60
detections … 15 were not" — conflating two scenes. It is not a prompting problem. `draft_intrep`
already does it correctly because its findings are templated from the correlation object; the
graph's `release` node should assemble prose the same way and use the model only for the parts
that are genuinely generative.

### The number to keep being careful with

Unchanged from M4 and now enforced in two places rather than one. Over Denmark
`CorrelationResult.rate_is_quotable` is **true** — DMA is ground truth, so the *source* side
passes — and a rate is still not quotable, because `DETECTOR_PRECISION_VALIDATED` is False: 25%
of detections are unmatched against a ~5% published base rate and the excess is coastal clutter.

`tools.rate_verdict(correlation)` returns both reasons and is what M5 should call. Do not
re-derive the condition in the graph; if the guard ever needs to change, it should change in one
file. `scrub_rate_claims` is the backstop over generated text and there is a test asserting the
guard's own explanatory caveat would not survive it — which is why caveats are assembled
structurally and never scrubbed.

### Things that are true and are not obvious from the code

- **Two Danish granules, not one.** `stac_search` over the Kattegat window returns both `…BC13`
  and `…34CE`, they are disjoint in latitude, and they independently give 60/45/15 each.
  `correlate` picks by coverage of the requested bbox, so it correlates **34CE**, not the BC13
  that every M3 number in the README refers to. Not a bug; worth knowing before wondering why the
  scene id changed.
- **`correlate` is bounded to one scene per call** and says so in each uncorrelated scene's
  provenance note. If M5 needs both, call it twice with `scene_id`.
- **The first `detect_vessels` on a cold scene takes 14–20 s** and writes the run, so everything
  after it is a database read at ~1 s. A graph that re-runs the detector per node would be paying
  that every time; it will not, because reuse is keyed on parameter identity, but only if the
  parameters actually match — `min_length_m` below the recorded run forces a full recompute.
- **`make tool-call T=… J='{…}'`** calls any single tool with raw JSON out. When the graph does
  something odd, this answers "was it the tool or the model?" in one command.

### The MCP attach is verified — by Claude Code, not by Claude Desktop

**`.mcp.json` is committed at the repo root**, so a clone gives Claude Code the tools with no
setup: `/usr/bin/docker exec -i nightglass-mcp nightglass-mcp stdio`. Project-scoped servers
need a one-time trust approval on first `claude` start in the directory — that is correct
security behaviour, not a fault, and it is why `claude mcp get nightglass` reads
"⏸ Pending approval" until someone approves it once.

Verified with a real MCP client rather than a hand-rolled probe. Added at local scope
temporarily, `claude mcp list` reported **✔ Connected**, and two headless sessions drove the
tools end to end:

- `nightglass_status` + `correlate` over a *Danish* bbox → correctly **refused**, and the
  `ToolError` reached the model intact: "the requested bbox lies outside this deployment's AOI".
  A better result than a success, because it proves the remediation text survives the transport.
- `correlate` over the Lisbon bbox → 133 detections, **0 matched, 133 dark**, `ais_source`
  reading `"none loaded"`, and the two uncorrelated scenes named. See the warning below.

`~/.config/Claude/claude_desktop_config.json` also has an `mcpServers.nightglass` entry (backup
alongside it as `.bak-pre-nightglass`). Desktop reads that file only at startup and has not been
restarted, so it is configured-but-unconfirmed; Claude Code is the confirmation, over the same
command and therefore the same bytes. Desktop's *other* servers come from
`extensions-installations.json`, not from this file, so an empty-looking `mcpServers` key on a
fresh machine does not mean the file is unused.

**The MCP surface serves whatever AOI the container has**, which is `lisbon` from `.env` — that
is §3.1 working as designed, one deployment one AOI. To reach the Danish validation AOI over
MCP, add `-e NIGHTGLASS_AOI=kattegat` to the `docker exec` args. `make tool-proof` does exactly
that, which is why its numbers are Danish and `.mcp.json`'s are from the Lisbon AOI.

### ⚠️ The demo AOI has no AIS, and that is now M5/M6's critical path

Confirmed over MCP, not inferred: a Lisbon `correlate` returns **133 detections and no
correlation at all** — finding 37 replaced the old `133 dark` with an explicit refusal, so the
tools now say "these are detections, not dark detections" instead of overstating. That is honest,
and it is still not a demo: §6 says the recording runs over Portugal, and Portugal currently has
nothing to correlate against.

Three ways out, in the order they are worth trying:

1. **Load the aisstream recording**, if the recorder has been running. It is a thinned feed and
   cannot support a rate, which is already stated as a limitation — but it makes the matcher
   *visible* over Portugal, which is what the demo needs.
2. **Use GFW as the reference layer** for the Lisbon scene. Verified retrievable during M3
   (finding in the M3 handoff, still accurate), and the granule GFW computed over is the one on
   disk, so it is a detection-for-detection comparison. This is already scheduled for M6.
3. **Record the demo over Denmark** and accept the divergence from §6. Weakest option: it throws
   away the two-AOI argument that §3.1 exists to make.

Whichever is chosen, decide it before building the M5 graph, not after — the graph's shape does
not change, but what the demo *shows* does.

### Still open, in priority order

1. **The 25% unmatched rate.** Unchanged, and now guarded in two places rather than argued about.
   Separating harbour structures and fixed installations from vessels is the real fix.
2. **The GFW comparison** — verified during M3, unwritten, and scheduled for M6. `correlate` now
   exists, which was the reason for deferring it.
3. **A genuinely cold `make pull-models`** — unchanged from M2.
4. **Demo AOI still undecided** between Lisbon and Leixões. Note that **no AIS is loaded for
   Lisbon**, so a `correlate` there today returns every detection unmatched against a feed of
   "none loaded" — which the guard handles correctly but which is not a demo.
5. **Citation entailment** — unchanged from M2.
6. **`scrub_rate_claims` is a regex over two languages.** It catches the shapes this model
   produces, including two it did not catch in a first draft, and it is not a proof. The real
   guarantee is that the templated findings never compute a proportion at all.

### Credentials are already solved — do not re-solve them

`GFW_TOKEN` is **blank in `.env` on purpose** and that is not a missing credential. It comes from
`~/.config/eo-credentials.env` (one identity, many projects, chmod 600, never committed), and
`scripts/load-env.sh` loads central first, project second, with one subtlety worth knowing before
"fixing" it: a blank value in `.env` means *defer to central* rather than *override with empty*,
because the project file loads last and would otherwise clobber a perfectly good token — and the
failure would read as "token not set" rather than "token was overwritten".

    source scripts/load-env.sh && nightglass_env_status

prints what is set without ever printing a value. Verified 2026-08-04: GFW_TOKEN 798 chars,
Earthdata in `~/.netrc`.

**One rule in that loader's header is right in spirit and too broad as written.** It says
credentials are never passed into docker compose, and that if you find yourself adding
`GFW_TOKEN` to a compose service you should stop. True of the *enclave* services — a credential
behind `internal: true` is useless, which is the whole M1 demonstration. But the `provision`
profile is also compose, on its own network, with egress by design, and it is the established
pattern for every other fetch (`corpus-fetcher`, `coastline-fetcher`, `model-puller`). The
precise rule is **the enclave never gets it; the provision profile may**, and that is the
decision to settle when `gfw-reference` is built — `GFWDetectionSource`'s docstring already names
that command and it does not exist yet.

If it goes the compose route: `env_file` handles the central file directly. Tested — Compose
parses `export VAR=value` lines, which is not obvious and is the thing that would otherwise force
a rewrite of a shared credentials file to suit one project.

### ⚠️ Host machine state that is NOT in the repo

Unchanged: `ollama.service` is `inactive` but still `enabled` and will come back on the next
reboot, re-pinning ~15 GB of the 3090. `make preflight` detects it. The enclave volumes hold both
models (9.5 GB) — do not `make clean` casually.

---

## → HANDOFF TO M4 — ✅ SUPERSEDED

*M4 is done. Kept for the reasoning it records; the current handoff is above.*

**M0–M4 are done.** `make dark-proof` runs the whole spatial chain and is the fastest way to see
the state of things. Everything below is what a new session cannot infer from the specs or code.

### Run this first

```bash
cd ~/Documents/dev/nightglass
make preflight && make up
make air-gap-proof     # M1
make rag-proof         # M2
make dark-proof        # M3 — schema, detector, AIS, the join, the renders
```

If `dark-proof` refuses with "no coastline", run `make fetch-coastline` — 149 MB down, 713 KB
kept, and the detector is not trustworthy near shore without it (finding 24).

### ⚠️ What M4 inherits, and the one number to be careful with

**The matcher is validated. The detector's coastal precision is not.** Over the Kattegat:
45 of 60 detections match real AIS, median distance **119 m**, and recall is **88% for AIS
vessels ≥ 30 m** (100% at ≥ 200 m). But 25% of detections are unmatched against a published
base rate of ~5%, and the excess is coastal clutter, not dark vessels.

So when M4 wires `correlate` and M5 drafts an INTREP: **the defensible claim is "here are N
detections I matched, with the space–time reasoning shown", not a dark-vessel rate.**
`CorrelationResult.rate_is_quotable` already guards the source side of this; nothing yet guards
the precision side. Worth a caveat string on the INTREP.

### What already exists and should not be rebuilt

- **`stac_search` is real** — `spatial/db.py::stac_search`, a PostGIS query over `stac.scenes`,
  which holds each granule as a whole STAC Item in `jsonb`. M4 wraps it; it does not reimplement it.
- **`detect_vessels` is real** — `spatial/detect.py::detect_scene`. ~12 s per scene over an AOI.
- **`ais_match` is real, as SQL** — `spatial/sql/dark_vessels.sql`, and it already returns
  matched *and* dark in one result set so the base rate stays computable. M4's tool wraps
  `db.dark_query`.
- **`doc_search` is real** from M2, and `api/main.py::doc_search` is the pattern the other five
  endpoints should copy — `asyncio.to_thread` around the blocking client, a 503 with a
  remediation hint, `response_model` from `schemas.py`.
- So M4 is `correlate` + `draft_intrep` + the MCP/FastAPI surface. Four of the six already work.
- **Six granules are catalogued** (`make scenes`), four over the Lisbon AOI and two Danish.

**The detector generalises — do not re-tune it.** Every threshold was set on one Danish scene,
so this was the real risk. Run unchanged over a Lisbon-AOI granule (S1A, different sea state and
geometry) it gives 133 detections with CFAR still binding and chips that are plainly vessels.
Finding 31. If M4 sees odd detector behaviour, suspect the wiring, not the thresholds.

### The shape M4 actually has to get right

`correlate` is not a new algorithm. It is `stac_search → detect_vessels → ais_match` with the
provenance chain preserved rather than flattened to a count — `CorrelationResult` in
`schemas.py` already says exactly that, and `dark_query` already returns matched *and* dark in
one result set. The work is orchestration plus honesty plumbing, not computation.

Two things worth deciding early rather than discovering:

- **Detections are already in PostGIS.** `correlate` should decide whether it re-runs the
  detector or reads `detect.detections` for a scene that already has a run. §5 says tools are
  pure functions over the database with no hidden caching that changes results between runs —
  so "read if a run exists for this scene *with these parameters*, otherwise detect" is the
  defensible reading, and `detect.runs.parameters` is jsonb precisely so that is checkable.
- **12 s per scene is fine for a CLI and slow for an MCP call.** Claude Desktop will time out
  on a multi-scene `correlate`. Either bound it to one scene, or make the tool return the run
  id and let the client poll — decide before writing the endpoint, not after it hangs.

### ✅ RESOLVED AT M3 — GFW per-detection `matched` flags exist

Was open item #1, "resolve early in M3". **They are retrievable, and the position is stronger
than the old note assumed.** `/v3/4wings/report` returns gridded aggregates — that is what the
earlier probe found — but `/v3/4wings/tile/position/{z}/{x}/{y}` returns **individual detection
points** as MVT, filterable with `filters[0]=matched='false'`. Verified over Lisbon, z9 tile
242/196, 2026-06-13: 13 detections = 10 matched + 3 unmatched.

The decisive detail: each feature's `id` is `<granule_id>;<lon>;<lat>`, and that granule
(`S1A_..._20260613T064316_..._F72E`) is **already on disk**. So the Lisbon cross-check is a
detection-for-detection comparison against a published layer computed over the *identical*
granule — not the degraded "GFW saw N here, I saw M" fallback the handoff feared.

⚠️ **Verified, not built — and it belongs to M6, not to M4.** An earlier draft of this handoff
called it the top M4 item; that was wrong. GFW is another *detector*, so agreeing with it is
weaker evidence than the AIS validation already banked over Denmark. Its value is the §6 demo
and the §8 README claim, so it lands better alongside the recording — and `correlate` (M4) is
the natural place for the comparison to live, which means building it first would mean building
it twice. No decay risk: the scenes are 13 Jun 2026, GFW's outage started 3 Jul, and the exact
endpoint and parameters are recorded above.

Two gotchas: `curl` glob-expands the `[0]` in `datasets[0]=` and silently sends nothing — use
`curl -g`, the symptom is an empty HTTP status. And `tile/heatmap` 422s without an explicit
`format=`, while `tile/position` does not.

### Still open, in priority order

1. **The 25% unmatched rate.** Not a blocker, but it is the number a technical interviewer will
   push on. The shoreline-buffer sweep in the README shows near-shore detections are unmatched
   at 6:1; separating harbour structures and fixed installations from vessels is the real fix.
2. **The GFW comparison — ✅ BUILT 2026-08-04.** `make fetch-gfw` (provision network) and
   `make gfw-compare` (offline). It found more than it was built to check: see finding 38, the
   detector counts one vessel several times over Portugal. What is left is putting the numbers
   in the README's limitations section and in the M6 recording.
3. **A genuinely cold `make pull-models`** — unchanged from M2, still only ever run against a
   volume that already held the blobs.
4. **FastMCP 3.4.5 still accepts `transport="sse"`** — revisit at M4 when Claude Desktop attaches.
   The stdio path is the one that matters, because it crosses the boundary without opening it:
   `docker compose exec -T mcp nightglass-mcp stdio`.
5. **Demo AOI still undecided** between Lisbon and Leixões; the coastline fetch already
   clipped both, so either works with no further provisioning. The Lisbon scenes are the ones
   with detections loaded and the ones GFW covers.
6. **Citation entailment** — unchanged from M2.
7. **A loop-breaker is still required at M5**, and it was measured during pre-dev, not guessed:
   fed a tool result that contradicted its request, qwen2.5 re-called `stac_search` four times
   with tweaked dates instead of advancing. Max-iterations plus a same-tool-same-args detector.

### ⚠️ Host machine state that is NOT in the repo

Unchanged from M2: `ollama.service` is `inactive` but still `enabled` and will come back on the
next reboot, re-pinning ~15 GB of the 3090. `make preflight` detects it. The enclave volumes hold
both models (9.5 GB) — do not `make clean` casually.

New at M3: `data/coastline/` holds three clipped GeoJSONs (~900 KB total) and `data/out/` holds
the rendered evidence. Both are gitignored. `data/out` is `chmod 1777` because the enclave writes
there as uid 10001 while the host reads as 1000.

---

## → HANDOFF TO M3 — ✅ SUPERSEDED

*M3 is done. Kept for the reasoning it records; the current handoff is above.*

**M0, M1 and M2 are done.** Everything below is state a new session cannot infer from the specs
or the code.

### Run this first

```bash
cd ~/Documents/dev/nightglass
make preflight          # tells you the truth about the machine in 5 lines
make up                 # idempotent; the stack may already be running
make air-gap-proof      # M1: both halves should pass
make rag-proof          # M2: ungrounded vs grounded vs refusal
```

If `rag-proof` says nothing is indexed, the Qdrant volume was destroyed — `make fetch-corpus &&
make ingest` rebuilds it in about two minutes, and `fetch-corpus` is cached against
`data/corpus/raw/` so it re-downloads nothing.

### ⚠️ M3's real prerequisites — two things that do not exist yet

Same shape as the corpus problem M2 opened with: name them now rather than discovering them
halfway through writing a spatial join.

**1. There is no vessel detector.** §M3 says "detections loaded", and there is nothing to load.
`Detection` exists as a type in `schemas.py` and `detect_vessels` exists as a §5 signature, but
no code reads a pixel. The spatial join has nothing to join until a detector runs over the VH
channel of a real granule. Verified: `src/nightglass/` holds `config · schemas · api · mcp ·
agent · rag` and nothing else.

Two things already established that make this cheaper than it sounds, both from M1:
- **Read scenes in place — no extraction.** `/vsizip/{zip}/{name}.SAFE/measurement/{vh}.tiff`
  works on all six granules and saves ~30 GB of unzipping.
- **Don't hand-parse the geolocation grid.** `rasterio` exposes all 210 GCPs directly via
  `src.gcps`; `src.crs` is `None` and `src.transform` is identity, both expected for GRD.

**2. The spatial dependencies are not in the enclave image.** `pyproject.toml` has them under
the `spatial` extra, but the runtime stage installs `.[services]` only — confirmed by importing
inside the container: rasterio, shapely, pyproj, geopandas and pandas are all missing.

This must be fixed at **build** time, not run time. Inside the enclave there is no package index
to install from — that is the same trap `make test` hit at M0, which failed with `Temporary
failure in name resolution` and was correct behaviour from a useless target. One line in
`docker/Dockerfile`: `.[services]` → `.[services,spatial]`. Expect a slower build; rasterio and
geopandas pull real wheels.

**PostGIS is ready but empty.** M0's initdb created the extensions (postgis, postgis_topology,
btree_gist) and the `stac` / `detect` / `ais` schemas. **Zero tables.** The DDL is M3's.

### What M2 left for M3

- **The corpus is done and does not need revisiting.** 60 documents, 1,814 chunks. Adding a
  public document is one entry in `corpus/sources.yaml`; adding a memo is one file in
  `corpus/synthetic/`. Both are followed by `make ingest`, which is idempotent.
- **`doc_search` is the first of §5's six tools and it is finished** — as a CLI, as
  `POST /tools/doc_search`, and as `DocumentIndex.search`. M4 wraps the same function for MCP
  rather than reimplementing it.
- **Do not add Madeira or Azores detection figures to the corpus** without also changing the
  refusal test. That gap is the only thing making the refusal demonstration mean anything, and
  it is documented in `corpus/README.md`.
- **The GFW per-detection `matched` flag question is now the top open item** — see "Open" below.
  It was M2's second-priority item and nothing in M2 touched it.

### ⚠️ Host machine state that is NOT in the repo

- **`ollama.service` is `inactive` but still `enabled`.** It was stopped by hand so the
  enclave could load a 14B model. **It will come back on the next reboot** and re-pin ~15 GB
  of the 3090, at which point inference inside the enclave fails. `make preflight` detects
  this and names the fix. `sudo systemctl disable --now ollama` makes it permanent — not done,
  because it is Nelson's machine and the host install is a genuine dev convenience.
- The enclave volumes hold both models already (9.5 GB). Do **not** run `make clean` casually;
  it destroys them.

### ✅ RESOLVED AT M2 — the document corpus exists

Was: *"`data/` holds SAR and AIS only. There are zero documents on disk."* Now 60 documents,
39 fetched and 21 written. Composition, licences and the reasoning are in `corpus/README.md`.
The one structural decision worth carrying forward: **the corpus has two halves held
differently** — `corpus/synthetic/` is committed because a fresh clone must reproduce the demo
and there is no URL to fetch an invented memo from; `data/corpus/` is fetched and gitignored
because some publishers grant no reuse licence.

Both things the old handoff said not to re-litigate held up:
- **bge-m3 was the right one-way call.** Cross-lingual retrieval works in *both* directions on
  the real corpus, not just the sentence-pair test: an English query about AIS deduplication
  ranked a non-English INTSUM first at **0.714**, above the English IMO material at 0.616 (the
  corpus has since been rewritten in English, so this no longer reproduces — see README).
- **Classification propagation is implemented structurally**, not as a prompt instruction. The
  marking on an answer is computed from the chunks it actually *cited* — `UNCLASSIFIED //
  SYNTHETIC` when doctrine memos are cited, plain `UNCLASSIFIED` when only IMO/EU sources are.
  Verified both ways.

### Where things live

| what | where |
|---|---|
| the enclave, and why it looks like that | `docker-compose.yml` — read the header comment |
| AOI resolution; the only place a bbox is named | `src/nightglass/config.py` |
| §5 tool contracts, with provenance attached | `src/nightglass/schemas.py` |
| what M0/M1/M2 actually proved | `README.md` architecture, air-gap proof, grounded-answers sections |
| the corpus: composition, licences, the deliberate gap | `corpus/README.md` |
| gotchas that cost time | findings 8–20 below |

### Open, in priority order

1. **GFW per-detection `matched` flags** — the public API returned *gridded aggregate counts*,
   not per-detection records. §3.1 wants unmatched detections specifically. If those flags are
   only in the paper's BigQuery tables, the Lisbon reference layer degrades to "GFW saw N
   here, my detector saw M" — still a real cross-check, weaker than the spec assumes.
   **Resolve early in M3**, not late.
2. **A genuinely cold `make pull-models`** — it has only ever run against a volume that already
   held the blobs, so it verified digests rather than downloading. Everything of ours is proven;
   ollama's downloader is not. Worth one cold run before M6 claims reproducibility.
   `make fetch-corpus` does not have this problem: it has been run cold, all 39 documents, zero
   failures, and its cache is per-file so a partial run resumes.
3. **FastMCP 3.4.5 still accepts `transport="sse"`**, but `http`/`streamable-http` is the modern
   path. Revisit at M4 when Claude Desktop attaches for real.
4. **Demo AOI still deliberately undecided** between Lisbon and Leixões — the recorder
   covers both. Decide after the scenes land, not before. INTSUM 2026/071 in the corpus lays out
   the trade (Leixões has three overlapping S1 paths against Lisbon's two, so ~50% more
   acquisitions; Lisbon has the richer estuary geometry and the EMSA narrative).
5. **Citation verification proves a cited chunk exists, not that it supports the claim.** A
   claim citing a real chunk that does not actually say what the claim says would survive. The
   fix is an entailment pass per claim against its cited span alone. Noted in the README's
   limitations and three-weeks list; not urgent, but it is the honest boundary of the current
   guarantee.

### Conventions worth keeping

- `make` with no target lists everything.
- Every milestone's evidence goes in the README; every surprise goes in NOTES.md as a numbered
  finding. The numbered findings are the study aid, and several of them are interview answers.
- Run the path a stranger would take, not the one that is fast for you. That is what found the
  `OLLAMA_HOST` bug (finding 13) — the convenient shortcut worked perfectly and hid it.

---

## Pre-dev guide run — 2026-08-03

Executed `PRE_DEV_GUIDE.md` §1–§8. Results below, **including corrections to the guide.**
Everything marked VERIFIED was checked by running the command, not inferred.

### Environment — VERIFIED, matches guide

| | Guide said | Actual |
|---|---|---|
| GPU | RTX 3090 24 GB | ✅ RTX 3090, 24576 MiB (22321 MiB free) |
| Docker | 29.7.1 / Compose v5.3.1 | ✅ exact match |
| `nvidia-ctk` | present | ✅ `/usr/bin/nvidia-ctk` |
| GDAL | 3.10.3 | ✅ 3.10.3 |
| Python | 3.13.5 + geo stack | ✅ rasterio 1.5.0, pandas 3.0.3, geopandas 1.1.3, shapely 2.1.2, pyproj 3.7.1, requests 2.32.3 |
| Disk | 606 GB free | ✅ 606 GB |
| `ollama` | missing | ✅ **0.32.5 installed**, both models pinned resident (`UNTIL: Forever`) |
| `go` | missing | ❌ still missing (M7 only, ignore for now) |

**Ollama, verified 2026-08-03.** Real VRAM is higher than the guide's weights-only table:
`ollama ps` reports **15 GB** for qwen2.5:14b @ 32k ctx (weights *plus* KV cache) and 0.66 GB
for bge-m3, so `nvidia-smi` shows **17.5 GB used / 6.5 GB free** — not the ~13 GB headroom the
table implies. Pinned via a systemd drop-in (`OLLAMA_KEEP_ALIVE=-1`,
`OLLAMA_MAX_LOADED_MODELS=2`) because the default `5m` keep-alive would evict a 15 GB model
between RAG embed and chat calls, showing up as latency rather than an error.

Two systemd traps hit while doing it: the `[Service]` header is mandatory, and systemd does
**not** accept trailing inline comments — only whole lines starting with `#`.

**M4 tool-chaining already passes** (dry-run, stub tool results, §6's demo query):
`stac_search → detect_vessels → ais_match →` final answer. Three distinct tools chained
unprompted; bbox parsed from prose; *"the last 72 hours"* resolved against a stated today-date.
It even hedged unprompted — *"may be undeclared"*, which is §7's framing for free.

⚠️ **But build a loop-breaker into M5.** When a tool result contradicted the request (a 17 Jul
scene fed back after it asked for 31 Jul–3 Aug), it re-called `stac_search` **four times** with
tweaked dates instead of advancing. Defensible reasoning, but unbounded it burns the context
window. Needs max-iterations plus a same-tool-same-args repeat detector.

**bge-m3 cross-lingual retrieval verified** — PT↔EN same meaning **0.842**, PT↔unrelated PT
0.283. It keys on meaning, not language, so a cross-language query will retrieve an English
corpus. This choice is one-way: switching embedders means re-embedding everything.

### Sentinel-1 coverage over Portugal — VERIFIED, risk retired

The guide called this "the one real risk" and "highest-priority unknown". **It is not a risk.**
ASF `GRD_HD` counts, 2026-06-01 → 2026-06-15:

| AOI | Count |
|---|---|
| **Lisbon / Tagus** `(-10.5 38.0, -8.5 39.5)` | **46** |
| Deep Atlantic `(-13.0 37.0, -11.0 39.0)` | 34 |
| Porto / Leixões | 31 |
| Algarve / Gulf of Cádiz | 28 |
| Azores (point -28.0 38.5) | 5 |
| Madeira (point -16.9 32.7) | 4 |

Coastal **and** open-ocean coverage are both healthy. Even the deep Atlantic box returns 34.
Azores/Madeira are sparse but non-zero — the guide's worry about open ocean was overcautious.

Full June 2026 over the Lisbon box: **81 granules, 100% dual-pol VV+VH.** VH is available for
detection everywhere, so the guide's §6 "use VH" advice is actionable on every scene.

### Overpass windows — NEW, not in the guide

Guide gave Denmark windows only, and they are slightly wrong.

| AOI | Descending (UTC) | Ascending (UTC) |
|---|---|---|
| Denmark / Kattegat — guide | 05:24–05:41 | 16:52–17:09 |
| Denmark / Kattegat — **measured** | **05:23–05:40** ✅ close | **16:44–17:09** ⚠️ guide misses the 16:44 pass |
| **Portugal / Lisbon — measured** | **06:33–06:51** | **18:26–18:43** |

⚠️ The guide's ascending Denmark window would silently drop ~1/3 of ascending passes
(16:44 appears 10× in July 2026). Widen it.

### Constellation — VERIFIED, guide correct

S1A retirement 29 Jun 2026 confirmed empirically from the catalogue:

- Portugal, **June** 2026: Sentinel-1A ×31, 1C ×18, 1D ×32 → S1A still flying
- Denmark, **July** 2026: Sentinel-1C ×36, 1D ×36, **zero S1A** → retired

Post-June scenes are S1C/S1D only. Granule prefixes are `S1C_`/`S1D_`; any regex hardcoding
`S1A|S1B` will match nothing. Query the catalogue, never a pre-2026 revisit table.

### Scene size — CORRECTION

Guide says 1.7–2.1 GB per dual-pol IW GRDH. **Actual: 780–990 MB**, roughly half.
Disk budget is far more comfortable than planned.

---

## ⚠️ Corrections that would have cost hours

### 1. `web.ais.dk` is DEAD — the guide's DMA URL does not work

`PRE_DEV_GUIDE.md` §5 and `EXECUTION_SPEC.md` §3.1 both give
`https://web.ais.dk/aisdata/`. Current state:

- TLS certificate `CN=*.govcloud.dk` **expired 12 Jun 2025** — over a year stale
- Even with `curl -k`, the handshake completes and the server then **hangs** with no response
- Port 80 does not answer

Cause: per dma.dk, *"the Danish Land Based AIS-system is now under the jurisdiction of the
Danish Emergency Management Agency."* The service moved.

**Working replacement — VERIFIED:**

```
http://aisdata.ais.dk/          ← official page, but a JS-rendered S3 listing (no links in HTML)
http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/   ← the actual bucket, plain S3 REST
```

Note **`http://`, not `https://`** — the HTTPS variant fails on this host too.

The pretty page is a `s3-bucket-listing` JS widget; scraping its HTML returns zero filenames.
Hit the bucket's S3 REST API instead — it is public, unauthenticated, and paginates properly:

```bash
# list recent dailies
curl -s "http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/?list-type=2&delimiter=/&max-keys=1000" \
  | grep -oE '<Key>[^<]+</Key>' | sed 's|</\?Key>||g'

# fetch one day
curl -O "http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/aisdk-2026-07-17.zip"
```

**Bucket layout** (the guide's `aisdk-YYYY-MM.zip` monthly guess was half right):
- **root** — daily files, rolling window `aisdk-2025-02-27.zip` → `aisdk-2026-07-31.zip`
- `YYYY/` prefixes (2006–2025) — older **monthly** archives, e.g. `2006/aisdk-2006-03.zip`

So only ~17 months of dailies are retained. Anything older is monthly-only.

### 2. Daily file size — CORRECTION, ~2× the guide

Guide says ~300–500 MB zipped. **Measured:** 848 MB (07-17), 956 MB (07-15), 988 MB (07-16),
593 MB (03-29). Budget ~1 GB zipped and (by the guide's 10× ratio) **8–12 GB unzipped** per day.
Stream-filter — do not unzip a whole day to disk casually.

### 3. Publish lag is ~3 days, not 48 h

`aisdk-2026-07-15.zip` has `Last-Modified: Sat, 18 Jul 2026`. Latest available today
(2026-08-03) is `2026-07-31`. Plan for 3 days, not 2.

### 4. `~/.netrc` has a `token` line that breaks Python's stdlib parser

The existing netrc is:

```
machine urs.earthdata.nasa.gov
login <redacted>
password <redacted>
token <JWT>
```

`curl -n` tolerates the trailing `token` key. **Python's `netrc` module does not:**

```
netrc.NetrcParseError: bad follower token 'token' (/home/nls/.netrc, line 4)
```

Anything in the ingest path calling `netrc.netrc()` will crash. Either drop the `token` line,
or read credentials another way. Worth knowing before it surfaces as a mystery failure inside a
download helper.

### 5. DMA timezone — RESOLVED: it is UTC

DMA documents this nowhere; their own bucket README gives the schema and the day-first format
but states no timezone. Settled empirically with the guide's DST histogram test on
`aisdk-2026-03-29.csv` (last Sunday of March 2026; Europe/Copenhagen springs forward
02:00→03:00 local):

```
00:00   709855   01:00   711596   02:00   712161   03:00   696666
```

Smooth across the transition — no one-hour hole at `02:xx`. **Timestamps are UTC.**
A local-time file would have shown a gap. Slice acquisition windows in UTC directly.

### 6. Decimal-comma worry — NOT a real trap

The README writes coordinates as `57,8794` / `17,9125`, which suggested the CSV might not be
comma-delimited. Checked against the real file: **that is Danish prose, not the format.**
Actual rows use decimal points and plain comma delimiting:

```
# Timestamp,Type of mobile,MMSI,Latitude,Longitude,...          ← 26 fields, '# Timestamp' intact
17/07/2026 05:13:00,Class B,257843840,56.489370,10.951233,...
```

Guide's §5 schema description is accurate in every respect.

### 7. ~71% of AIS rows are exact duplicates — NEW, quantifies the guide's warning

The guide warns that "AIS hygiene manufactures phantom darks" but gives no magnitude. Measured
on the 22-minute Kattegat acquisition window:

| | |
|---|---|
| raw rows | 134,851 |
| after exact-duplicate removal | 38,945 (**−71%**) |
| unique `(MMSI, timestamp)` | 38,850 |
| worst rebroadcast multiplicity | **21 identical copies** of one message |
| after dedup | 42.8 distinct timestamps per vessel, max 544 |

Multi-station rebroadcast, exactly as the guide predicts. **Dedup on
`(MMSI, timestamp, lat, lon)` before matching** — otherwise duplicate-weighted
nearest-in-time logic is distorted. 42.8 positions per vessel across 22 minutes is ample to
interpolate each track to the acquisition instant rather than taking nearest-raw, which is what
the guide's §7 asks for.

---

---

## aisstream.io — the "no free AIS for Portugal" claim needs amending

`PRE_DEV_GUIDE.md` and `EXECUTION_SPEC.md` §3.1 both assert flatly:
**"No free point-level AIS exists for Iberian waters."** That is too strong.

[aisstream.io](https://aisstream.io) gives free real-time global AIS over WebSocket, API key
only, bbox subscription. Pulled live from the Tagus estuary on 2026-08-03:

```json
{"MMSI": 263701390, "name": "GIL VICENTE", "lat": 38.6526, "lon": -9.08095,
 "sog": 0, "cog": 341, "utc": "2026-08-03 15:12:39 UTC"}
```

MMSI `263…` is a national flag prefix. Free, point-level, Iberian waters.

**But it does not rescue the Lisbon correlation story, for two measured reasons.**

### 1. Real-time only — no archive

There is no historical endpoint. It cannot serve the 13 June Lisbon scenes, or any past
acquisition. It only works by recording *forward* and matching a *future* overpass.

### 2. The feed is incomplete — measured against ground truth

**The controlled test.** Same Kattegat bbox, same 22-minute clock window (15:29–15:51 UTC),
one source against the other:

| source | messages | unique vessels |
|---|---|---|
| **DMA** (17 Jul 2026) | 80,388 raw / ~23,300 deduped | **770** |
| **aisstream** (3 Aug 2026, live) | **609** | **132** |

**aisstream sees ~17% of vessels — it misses roughly 5 in 6.**

**This is a ceiling, not a warm-up.** Per-minute new-vessel counts: 32, 21, 14, 13, 11, 4, 4,
4, 3, 3, 3, 0, 4, 3, 0, 3, 3, 3, 0, 3, 0, 1 — flat from ~minute 10. Recording for four days
will not close it. Those vessels are not being received at all, not merely reported slowly.

Caveats, honestly: different days (17 Jul vs 3 Aug) so traffic differs — but not by 6×. Same
bbox, same hour. Denmark is DMA's home receiver network, so best case for the reference side.

**Consequence:** if ~83% of real vessels are absent from the feed, the matcher marks ~83% of
detections dark. Worse than the 40%-dark failure the guide warns about.

**The structural trap:** completeness over Portugal *cannot be measured*, because the absence
of a DMA equivalent is both why aisstream is needed there and why there is no reference to
check it against. Denmark is the only AOI where this gap is observable — which is a large part
of what the Danish AOI is actually for.

### 2b. Earlier throughput samples (superseded by the above, kept for the reasoning)

Message throughput is the robust test, because it needs no cross-source comparison. Class A
transmits every 2–10 s, so a fully-observed vessel yields **~40 messages per 4 minutes**:

| AOI | window | messages | unique vessels | msgs/vessel |
|---|---|---|---|---|
| Lisbon | 4 min | 39 | 29 | **1.3** |
| Kattegat | 4 min | 114 | 79 | **1.4** |

~1.4 against an expected ~40 is roughly **3% of expected message volume**. The feed is sampled,
not complete.

Cross-checked against ground truth in the one place we have it — the same Kattegat bbox:

| source | window | unique vessels |
|---|---|---|
| **DMA** (17 Jul, 05:13–05:34 UTC) | 22 min | **907** |
| **aisstream** (3 Aug, ~15:20 UTC) | 4 min | 79 (still climbing: 27→50→67→79) |

Not a controlled comparison — different day, different hour, and aisstream had not saturated
(increments were decaying 27→23→17→12, extrapolating to roughly 130–200 at 22 min). Even
generously, that is a **4–7× shortfall**. The independent msgs/vessel figure points the same way.

### Consequence — do NOT use it as ground truth

Absence from a thinned feed is not absence of AIS transmission. Feeding this to the matcher
would mark large numbers of ordinary vessels "dark" and blow straight past the guide's ~5%
unmatched / 0.4% genuinely-dark base rate — the exact "if your pipeline reports 40% dark, it's
broken" failure mode.

### What it IS good for

- **Makes `CustomerFeedSource` real.** EXECUTION_SPEC §3.1 wants it as a stub raising "not
  configured". A live WebSocket adapter turns "in a real deployment the customer brings the
  feed" from a talking point into working code — the third implementation of the source
  adapter interface, exercising it properly.
- **A live demo layer** over Portugal, clearly labelled as indicative coverage, not ground truth.
- **Honest framing:** *aisstream demonstrates the mechanism over Portugal; Denmark remains the
  rigorous validation.* Stronger than either "no free AIS exists" (false) or "I independently
  correlated Portugal" (unsupportable). It also shows the claim was tested rather than assumed.

**Amend both documents** to: *no free **historical, complete** point-level AIS exists for
Iberian waters; a free real-time feed exists but is too sparse for dark-vessel rate
estimation.*

---

## Blocked — needs Nelson

### A. ASF downloads return 401 — Earthdata EULA not accepted

Scene *search* needs no auth and works fine. *Download* fails:

```
$ curl -n --location-trusted -r 0-1023 <downloadUrl>
401 — "Could Not Login. Be sure to agree to the EULA."
```

Diagnosis:
- Earthdata account `lunasilvestre` exists; netrc present, `chmod 600` ✅
- Bearer token is **valid** — issued 2026-06-30, expires 2026-08-29, and returns
  `200` against `cmr.earthdata.nasa.gov` ✅
- So this is **not** bad credentials and **not** an expired token

It is the ASF EULA / application authorization, which must be accepted once in a browser:
<https://urs.earthdata.nasa.gov/profile> → *Applications* → *Authorized Apps* → authorize
**Alaska Satellite Facility Data Access**, and accept the Sentinel EULA.

**Nothing SAR-side can proceed until this is done.** It gates both AOIs.

### B. `ollama` not installed

```bash
curl -fsSL https://ollama.com/install.sh | sh     # needs sudo
ollama pull qwen2.5:14b-instruct-q4_K_M
ollama pull bge-m3
```

### C. GFW API token — ✅ CLEARED 2026-08-03

Registered as an individual, intended use "Non Commercial". Token issued (798 chars) and
verified working: **HTTP 200** against `v3/vessels/search`. Stored in
`~/.config/eo-credentials.env` as `GFW_TOKEN` — the name `gfwr`'s `gfw_auth()` expects — and
**not** in the repo. Licence CC BY-NC 4.0; attribution owed in the README (EXECUTION_SPEC §8.6).

**GFW SAR reference layer — VERIFIED WORKING on the scene dates, outage confirmed still live.**

Dataset `public-global-sar-presence:v4.0` ("Radar vessel detections (SAR)"), status `done`,
coverage from 2017-01-01. Queried via `4wings/report` over the Lisbon AOI:

| date range | detections |
|---|---|
| **2026-06-13 → 06-14** (our scene date) | **69** ✅ |
| 2026-06-01 → 06-30 | 302 ✅ |
| 2026-07-25 → 08-02 | **0** ⚠️ outage still ongoing |

So the Lisbon reference layer **exists for exactly the scenes we downloaded**, and the
3 Jul 2026 outage is confirmed unresolved as of today. The spec's instruction — use a
historical date before July 2026 — is not caution, it is required.

API gotcha: `4wings/report` **requires** `group-by` (one of `VESSEL_ID, FLAG, GEARTYPE,
FLAGANDGEARTYPE, MMSI`) or it 422s. Also `/v3/datasets` returns `total: 45535` with an empty
`entries` array unless paginated properly — query a dataset by ID directly instead.

⚠️ **Still unverified, and it matters at M3:** the above returns **gridded aggregate counts**,
not per-detection records carrying `matched=true/false`. EXECUTION_SPEC §3.1 wants *unmatched*
detections specifically. Whether per-detection matched flags are retrievable through the
public API (vs only through the paper's BigQuery tables) is the open question. If they are
not, the Lisbon "dark" layer degrades to "GFW saw N detections here, my detector saw M" —
still a real cross-check, but weaker than the spec assumes. **Resolve this early in M3.**

---

## → HANDOFF TO M0 — ✅ SUPERSEDED

*M0 and M1 are done. This section is kept for the reasoning it records; the current
handoff is **→ HANDOFF TO M2** at the top of this file.*

Pre-dev is complete. Everything below is state a new session cannot infer from the specs.

### Files that already exist — do NOT recreate or clobber

| path | what it is |
|---|---|
| `.env` | 0600, gitignored. Real config. **Not** a template. |
| `.env.example` | committed template; keep the two in sync (all keys must match) |
| `.gitignore` | covers `.env`, `data/`, `*.zip`, `*.tif` |
| `scripts/load-env.sh` | `source` it (don't execute). Loads `~/.config/eo-credentials.env` then `.env`. Provides `nightglass_env_status`. |
| `data/sar_manifest.txt` | 4 pinned granules — ⚠️ its **Lisbon-AOI** entries are path 23, offshore, **superseded** |
| `data/sar_manifest_pt_fix.txt` | the correct Lisbon scenes (path 125, over the approaches) |

**Credentials live outside the repo**, in `~/.config/eo-credentials.env` (0600):
`GFW_TOKEN`, `AISSTREAM_API_KEY`, `CDS_API_KEY`, `EUMETSAT_*`. Earthdata stays in `~/.netrc`.

### ✅ RESOLVED AT M0 — ollama runs in compose. See "M0 — built" below.

**This was a real conflict and M0 resolved it deliberately.** The reasoning as it stood
before the decision is kept intact below, because the argument is the answer.

`EXECUTION_SPEC.md` §2 lists `ollama` as a **compose service**. But it is currently installed
as a **host systemd service on port 11434**, with models pulled and keep-alive pinned.

- **Pointing compose at the host** is tempting (models already there, ~10 GB not re-downloaded)
  but it **breaks the entire demonstration**: app services run on `internal: true` with no
  egress, and reaching the host needs `host-gateway`/`host.docker.internal` — a deliberate hole
  in the exact boundary M1 exists to prove. It also breaks M6's "clone and `make up`".
- **Recommended: ollama as a compose service**, per the spec. The host install stays a dev
  convenience. Notes: don't publish 11434 from the container (only `api`/`agent` need it on the
  internal network, so there's no clash with the host service), and put model blobs on a named
  volume with a documented pull step so a cloner can reproduce. Bind-mounting the host's
  `OLLAMA_MODELS` dir avoids re-downloading 10 GB locally but must not be the documented path.

Whatever is chosen, **write it into `NOTES.md` and the README design table** — "why is the
model server inside the enclave rather than beside it" is exactly a hiring-manager question.

---

## M0 — built 2026-08-03. `make up` → five containers healthy.

```
NAME                 IMAGE                    STATUS
nightglass-api       nightglass/app:dev       Up (healthy)
nightglass-mcp       nightglass/app:dev       Up (healthy)
nightglass-ollama    ollama/ollama:0.32.5     Up (healthy)   11434/tcp
nightglass-postgis   postgis/postgis:17-3.5   Up (healthy)   5432/tcp
nightglass-qdrant    qdrant/qdrant:v1.18.3    Up (healthy)   6333-6334/tcp
```

Those `PORTS` values are container-exposed ports with **no host binding** — see finding 8.

### Decision: ollama is a compose service

Taken as the handoff recommended, for the handoff's reason: reaching the host service needs
`host.docker.internal:host-gateway`, which is a deliberate hole in the exact boundary M1
exists to prove, and it breaks M6's "clone and `make up`". `.env.example` had already
committed to it with `OLLAMA_HOST=http://ollama:11434`.

The two real costs, and what was done about each:

| cost | mitigation |
|---|---|
| ~10 GB of model blobs re-downloaded | `make pull-models` — a **profile-gated** service on a **separate network**, so the enclave itself never has egress. Plus `make seed-models`, which copies the blobs already on this host into the volume. That one needs sudo and is explicitly *not* the documented path. |
| VRAM contention | The host service pins ~15 GB with `OLLAMA_KEEP_ALIVE=-1`, leaving 6.5 GB of 24 GB. `scripts/preflight.sh` detects it and names the fix (`sudo systemctl stop ollama`). |

### 8. `internal: true` silently drops published ports — NEW, and it shaped the topology

Verified directly on Docker 29.7.1 before writing the compose file:

```
$ docker run -d --network <internal-net> -p 18099:80 nginx:alpine   # starts fine
$ docker ps        →  80/tcp          ← no host mapping. None.
$ curl 127.0.0.1:18099  →  HTTP 000
$ # identical container on a normal bridge  →  HTTP 200
```

**No warning is emitted anywhere.** The mapping is simply never created.

So no NIGHTGLASS service publishes a port — not as a style choice, but because it would not
work and the compose file would be lying. Consequences: host access is `docker compose exec`
(which is what §6's demo already uses), and MCP reaches Claude Desktop over **stdio through
`docker exec`** rather than SSE over a socket. A pipe crosses the boundary without opening it.

The tempting workaround is a second bridge network with
`com.docker.network.bridge.enable_ip_masquerade=false` — published ports work, egress does
not. **Rejected.** It weakens the proof from *"no route exists"* to *"packets leave and never
come back"*, Docker's embedded DNS still resolves external names on such a network, and it is
a tunable flag rather than a structural property. Default-deny by construction survives a
future careless edit; a flag does not.

### The air gap is real, and it fails in the strong way

```
$ docker compose exec api curl -m 5 https://example.com
curl: (6) Could not resolve host: example.com
$ docker compose exec api getent hosts ollama qdrant postgis
172.21.0.4  ollama      172.21.0.3  qdrant      172.21.0.2  postgis
```

Not a timeout — **name resolution itself is refused**, because an internal network's embedded
DNS does not forward externally. That is the difference between "firewalled" and "air-gapped",
and it is the M1 capture worth recording. Internal service discovery still works.

Corollary that bit immediately: **`make test` originally pip-installed pytest at run time and
failed** with `Temporary failure in name resolution`. Correct behaviour, useless target. Test
deps moved to a `dev` build stage; tests now run with `--network none`. Anything that assumes
a package index at run time has to move to build time — worth remembering at M2 and M7.

### 9. A transposed bbox is NOT generally detectable — validation cannot save us

Assumed at first that a lat/lon swap could be caught by range-checking. It cannot. Lisbon's
box swapped — `38.0,-10.5,39.5,-8.5` — is a **perfectly valid box off Somalia**; Kattegat's
lands in the Arabian Sea. Both parse clean, pass every check, and return nothing.

Only a swap that pushes a latitude past ±90 is catchable, and none of our AOIs do.

So the defence is structural, not validational: axis order is converted in exactly one place,
`BBox.as_aisstream()`, and `tests/test_config.py` asserts the undetectable case explicitly so
nobody later assumes the validator covers it. This is the same failure the handoff flagged as
"the most common way to get a silently empty stream".

### What M0 built

```
docker-compose.yml          one internal network, no egress, pinned tags
docker/Dockerfile           runtime + dev stages; curl is present because M1's proof needs it
docker/postgis/initdb/      postgis, postgis_topology, btree_gist; schemas stac/detect/ais
Makefile                    up, down, preflight, pull-models, seed-models, air-gap-proof, test, lint
scripts/preflight.sh        docker, nvidia runtime, VRAM, AOI config
scripts/air-gap-proof.sh    §M1's check, both halves, as a repeatable command
scripts/seed-models-from-host.sh
src/nightglass/config.py    AOI resolution — the only place a bbox is named
src/nightglass/schemas.py   §5 contracts, provenance attached to every result
src/nightglass/api/         FastAPI: /health /ready /config
src/nightglass/mcp/         FastMCP, stdio + sse, one probe tool
src/nightglass/agent/       placeholder; the graph is M5
tests/test_config.py        10 tests, all passing
README.md                   §8 skeleton — air-gap capture slot still empty, that is M1's
```

### Design calls made along the way (all reversible, per §"momentum rules")

- **The agent is a one-shot `run --rm`, not a long-running service.** It runs to a human gate
  and halts, so it has nothing to serve between invocations; a daemon wrapper would exist only
  to satisfy a healthcheck. It sits behind `profiles: ["cli"]` so `make up` never starts it.
- **`/health` is dependency-free; `/ready` does the dependency checks.** A healthcheck that
  reaches sideways turns one slow service into a cascade of unhealthy containers.
- **`pg_isready -h 127.0.0.1`, not the default socket.** Over the unix socket it answers
  *during* initdb while the TCP listener is still closed, so the container reports healthy
  before anything can connect to it.
- **Qdrant's image has no curl and no wget.** Healthcheck uses bash's `/dev/tcp`.
- **Ground truth is enforced in the type system, not in prose.** `Match.source_is_ground_truth`
  travels with every match and `CorrelationResult.rate_is_quotable` is false unless all matches
  came from a ground-truth feed. §7's "rates only over Denmark" should not depend on remembering.
- **Overpass windows moved into `.env`** as `AOI_<NAME>_PASS_DESCENDING/ASCENDING`, since §3.1
  lists "time window" among the things that must come from config. Kattegat's ascending bound is
  the corrected **16:44**, not the guide's 16:52.
- **`POSTGRES_PASSWORD` in `.env` was still the literal `change_me_locally`** — it is now a
  generated value. `.env.example` keeps the placeholder. Key parity between the two is intact.

### 10. `/usr/share/ollama` is mode 700 — existence checks must run under sudo

`seed-models-from-host.sh` first guarded with a plain `[[ -d "$HOST_MODELS" ]]`. That guard is
useless: the ollama home is **mode 700 owned by `ollama`**, so an unprivileged test on any path
beneath it returns false whether or not the path exists, and the script would have reported
"no host model store" on a store sitting right there. Every existence check in it now runs as
`sudo test -d`. Generalises: a permission-denied traversal and a genuine absence are
indistinguishable to `test`, and only one of them is worth an error message.

---

## M1 — done 2026-08-03. Both halves captured.

Models seeded from the host (9.5 GB, 8 blobs, both manifests), host ollama stopped, and
`make air-gap-proof` passes end to end. The capture is in the README.

```
$ docker compose exec api curl -m 5 https://example.com
curl: (6) Could not resolve host: example.com          ← not a timeout. no route at all.
$ chat completion against qwen2.5:14b, inside the enclave
Synthetic Aperture Radar (SAR) is a radar technique that creates high-resolution
images regardless of weather or lighting conditions by synthesizing a large
antenna aperture, which allows it to operate both day and night.          ← correct
```

### 11. ⭐ The model has NO prior for "dark vessel" — this is the case FOR M2

Asked *"what is a dark vessel?"* with no retrieved context, qwen2.5:14b states outright that it
"isn't a standard term in common usage or in specific fields" and offers four confident readings:
literature and fiction, philosophy and metaphor, art and symbolism, and — memorably —
retrocomputing, "a black case or enclosure for hardware components".

**Not one of them is maritime.** Fluent, confident, entirely domain-wrong, and it would read as
authoritative to anyone who did not already know better.

*(Re-verified 2026-08-04 after the corpus and the interface were converted to English. The
original run asked the same question in another language and got the reading "painted in dark
colours" — a different wrong answer to the same effect. The finding is about the absence of a
prior, and it does not depend on the language it was found in.)*

This is the single most useful thing to come out of M1, for three reasons:

1. **It is the concrete argument for M2.** The operational meaning of "dark vessel" has to come
   from the retrieved corpus, never from model priors. That is no longer an assertion in a spec;
   there is a transcript.
2. **It is the README's RAG before/after.** Same question, ungrounded vs grounded-with-citations,
   is a far stronger demonstration than any retrieval-hit-rate number. Save it for §M2.
3. **It was the wrong prompt for the M1 capture** — a reader sees a wrong answer directly beneath
   the words "inference works". The air-gap script now asks about SAR instead, which the base
   model answers correctly. The question is kept here deliberately, not discarded.

Generalises to §7: a fluent, confident, wrong answer from an ungrounded local model is *exactly*
the failure the provenance requirement exists to catch. Worth saying out loud in the hiring
manager round — the honest version is "I found my own model doing this and built around it."

### 13. ⭐ `OLLAMA_HOST` means two different things — and it broke `make pull-models`

Running the documented provisioning path for the first time failed immediately:

```
$ make pull-models
Error: listen tcp: lookup ollama on 127.0.0.11:53: no such host
ollama did not come up
```

**`OLLAMA_HOST` is read by `ollama serve` as the address to BIND to, and by every ollama client
as the URL to CONNECT to.** `.env` sets it to `http://ollama:11434`, which is correct for the
enclave's clients (`api`, `agent`, `mcp`). The `model-puller` inherited that same value via
`env_file`, so its `ollama serve` tried to bind to a hostname that does not exist on the
provision network, and died.

Worse, the *enclave's* ollama had the identical misconfiguration and **worked by accident** —
the hostname `ollama` resolves to that container itself on the enclave network, so binding to it
succeeded. A latent fragility that looked like a working system.

Both now set the bind address explicitly: `0.0.0.0:11434` for the enclave server,
`127.0.0.1:11434` for the puller, which serves nobody and only needs a local endpoint to pull
through. Re-verified afterwards: five containers healthy, air-gap proof still passes both halves.

**This is the argument for exercising the documented path rather than the convenient one.**
`make seed-models` worked fine and hid this completely; a cloner following the README would have
hit it on their first command. Found because the path a stranger takes was actually run.

### 14. Ollama has its own outbound paths — the network was the only thing stopping them

Reading the `model-puller` startup log properly (rather than just noting it succeeded):

```
OLLAMA_NO_CLOUD:false   OLLAMA_REMOTES:[ollama.com]
msg="Ollama cloud disabled: false"
msg="model recommendations cache sleep scheduled" wait=3h20m42s
```

Ollama 0.32.5 ships **cloud-hosted model routing** via ollama.com and a periodic
**model-recommendations refresh**. Inside the enclave both fail — there is no DNS to resolve
ollama.com with — so nothing leaked. But *"it fails because the network stops it"* is a weaker
claim than *"it was never enabled"*, and for a project whose entire thesis is the boundary,
leaning on one layer is the wrong instinct.

`OLLAMA_NO_CLOUD=true` now set on the enclave service; verified in its log as
`Ollama cloud disabled: true`. Deliberately **not** set on `model-puller`, which legitimately
reaches the registry. The boundary and the application now agree rather than one covering for
the other.

Also from that log, and correct rather than a bug: the puller reports `library=cpu`,
`total_vram="0 B"`. It has no GPU because only the enclave `ollama` service carries the device
reservation. Pulling is a download, not inference. Nobody should "fix" this by adding a GPU.

Generalises, and it is the honest answer to §9's *"what breaks first in a real air-gapped
deployment"*: not the model, not the database — **some dependency's built-in telemetry, update
check or cloud fallback**, which nobody declared and which works fine in every environment where
it was tested.

### 12. Free-VRAM preflight blocked the thing it existed to enable

`make air-gap-proof` refused to run with *"only 7412 MiB free — inference needs ~16000 MiB"* —
because the enclave's ollama had **already loaded the model**. The VRAM was consumed by precisely
the process that needed it, and free-VRAM arithmetic had become the wrong question.

Passed the first time only because nothing was loaded yet. Now the check asks *is the model
resident?* before *is there room to load it?*, and short-circuits when `ollama ps` inside the
enclave lists anything. A resource precondition has to be written against the goal state, not
the free-resource count, or it starts failing the moment it succeeds.

### Still open after M0

- ~~`make pull-models` is still unproven~~ → **run and passing** (see finding 13 — it was broken,
  and running it is what found the bug). Residual gap: it was exercised against a volume that
  already held the blobs, so it verified manifests and digests rather than downloading 9.5 GB
  cold. Everything of *ours* is now proven — network, volume, serve-and-wait, env, the loop —
  and what remains untested is ollama's own downloader. Worth one genuinely cold run before M6,
  but it is no longer a guess.
- ~~`make air-gap-proof`'s chat half is unrun~~ → **both halves pass**, capture in the README.
- FastMCP 3.4.5 still accepts `transport="sse"`, but `http`/`streamable-http` is the modern
  path. Worth revisiting at M4 when Claude Desktop attaches for real.

### Two findings that save real time at M3

**1. Don't hand-parse the geolocation grid.** `PRE_DEV_GUIDE.md` §6 says georeferencing lives
in `annotation/*.xml` and to "build GCPs from it". **GDAL already does this.** `rasterio` exposes
all 210 tie points directly:

```python
with rasterio.open(path) as src:
    src.crs        # None      — expected, GRD is not map-projected
    src.transform  # identity  — expected
    src.gcps       # (list of 210 GCP objects, CRS EPSG:4326)  ← use these
```

**2. Read scenes in place — no extraction.** A SAFE zip is 5.45 GB unpacked; GDAL's `/vsizip/`
reads the measurement TIFF straight out of it:

```python
vh = f"/vsizip/{zip_path}/{safe_name}.SAFE/measurement/{vh_tiff_name}"
```

Verified on all six granules. Saves ~30 GB of disk and the unzip time.

### Verified working, don't re-litigate

- ASF download needs **EULA + `--location-trusted` + a cookie jar** (`-c`/`-b`). Without the
  cookie jar it redirect-loops ~50 times; that is not an auth failure.
- Ollama tool-chaining passes M4's bar (3 tools) — but **M5 needs a loop-breaker**; it re-called
  one tool 4× when a result contradicted its request.
- bge-m3 cross-lingual retrieval works (PT↔EN 0.842 vs 0.283 unrelated).
- GFW SAR reference layer returns **69 detections on 2026-06-13** over Lisbon; outage still
  live for late July. `4wings/report` **requires** `group-by`.

### Data inventory — 6.2 GB on disk

```
data/raw/sar/   6 granules, 4.7 GB   4× S1D (2 DK 17 Jul, 2 PT offshore) + 2× S1A (PT approaches 13 Jun)
data/raw/ais/   2 days, 1.5 GB       aisdk-2026-07-17 (validation) · aisdk-2026-03-29 (DST test)
data/interim/   23 MB                ais_kattegat_20260717_0513-0534.csv — the matched-window slice
```

---

## M2 — done 2026-08-03. 60 documents, 1,814 chunks, cited answers and a real refusal.

`make rag-proof` runs the whole demonstration. Both "done when" conditions pass: claims map to
retrievable chunk IDs, and an unanswerable question refuses instead of guessing.

### What M2 built

```
corpus/sources.yaml         manifest of 39 public documents — URLs and licences, not documents
corpus/synthetic/           21 INTREP/INTSUM memos, UNCLASSIFIED // SYNTHETIC, committed
corpus/README.md            composition, licence handling, the deliberate refusal gap
src/nightglass/rag/         fetch · extract · chunking · embed · index · answer · cli
docker/Dockerfile           new `fetcher` stage: runtime + poppler-utils, ONLINE only
docker-compose.yml          corpus-fetcher service (provision network, profile-gated)
                            x-corpus read-only mounts on api, mcp, agent
scripts/rag-proof.sh        §M2's proof, as a repeatable command
tests/test_rag.py           33 tests — markings, chunk identity, citation verification
```

### Corpus composition

| Publisher | Docs | Chunks |
|---|---|---|
| European Union (EUR-Lex) | 8 | 860 |
| International Maritime Organization | 10 | 488 |
| ICEYE Ltd | 17 | 357 |
| NIGHTGLASS synthetic | 21 | 96 |
| Copernicus EMS | 4 | 13 |

ICEYE product documentation was Nelson's suggestion mid-build and was the right call: it is the
most on-point technical text available anywhere for this project, and it comes from the vendor
whose product this shadows. Fetched from the docs' own git source **at a pinned commit** rather
than by scraping the rendered site — the published site is `latest` and moves, and a corpus that
cannot be rebuilt byte-for-byte is not reproducible.

### 15. ⭐ pypdf mangles EUR-Lex PDFs and pdftotext does not — 676 occurrences vs 0

The first extractor used `pypdf`, being pure Python and one dependency instead of a native one.
On the Official Journal PDFs it produces:

```
REGUL A TIONS
C OUNCIL REGUL A TION (EU) 2017/1509
concer ning restr ictiv e measures against the Democratic P eople's Republic of K orea
Ha ving regar d to the T reaty on the Functioning of the European Union
```

Spurious spaces inside words, from how those files encode character spacing. Counting the
artefact (`\b[A-Za-z]{2,} \b(ing|tion|ment)\b`) across two regulations: **pypdf 676 and 114,
pdftotext 0 and 0.** poppler also gets two-column Official Journal reading order right.

Mangled words would poison the embeddings *and* appear inside quoted evidence, which is the
worse half — a citation that reads as damaged discredits the whole product.

So poppler it is, installed **only in the `fetcher` image stage**. The enclave runtime has no
PDF parser, because it never sees a PDF: it reads normalised markdown and nothing else.

⚠️ **The check that nearly hid this.** My first mangling count used
`\b[A-Za-z]{1,3} [a-z]{1,3}\b(ing|tion)` and reported 15 and 229 hits *on the clean pdftotext
output*, which looked like the fix had failed. It hadn't — that regex matches "and having",
"for making", "ent using". The signature of mangling is a **word boundary before** the fragment,
not merely a short word near one. Nearly reverted a correct fix on the strength of a bad grep.

### 16. Running-header stripping: two bugs, and a deliberate trade

Legal PDFs repeat a header on every page. Left in, "Official Journal of the European Union"
becomes one of the strongest signals in the document, which is exactly backwards.

Detection is a frequency count on a **digit-blind** normalisation, so `L 229/2`, `L 229/3` and
`L 229/4` collapse to one key `L #/#`. An exact-match count sees three distinct strings and
strips none of them.

Two bugs found by running it, in order:

1. **Margins counted one way, removed another.** The frequency table was built over the first
   three *non-empty* lines of each page; removal then tested the first three *raw* line indices.
   In a PDF the header is almost always preceded by blank lines, so the two sets barely
   overlapped. Result: 17 surviving copies of `A 29/Res.1106` in one 35k-character resolution.
2. **A three-line margin is too small for these documents.** The IMO resolutions carry a
   **six-line** running header — document symbol, page number, resolution number, adoption date,
   and a two-line title. A three-line window sees the top half and lets the rest through.

Now `_MARGIN = 6` at a 0.3 page fraction: 17 copies → 1, and both load-bearing clauses survive.

**The trade, stated because it is real:** the digit-blind key is what makes detection work and
is also what makes it possible to delete body text that differs only in its numbers ("Article 5",
"Article 6"). The bound is that a running head can never be more than **a third of a page**, so
the mistake stays confined to page edges. Both the property and its cost have tests.

### 17. ⭐ Finding 11's transcript does not reproduce — and the finding survives anyway

M1 recorded qwen2.5:14b answering that a dark vessel is a boat "painted in dark
colours", and called it M2's whole argument. Re-run at M2, four samples, three at the default
temperature and one at 0:

- *"has no specific or common meaning in seafaring"* — the term has no established
  meaning (×2)
- *"a vessel painted black or operating at night without lights"* — offered as a guess
- *"Visual description: refers to the physical appearance of the vessel"* — the paint reading, but
  hedged as one interpretation among several

The paint-colour answer is **one sample from a distribution**, not a reproducible behaviour, and
NOTES presented it as though it were.

The underlying claim is unharmed and is arguably better stated now: **the model has no prior for
the central term of this project.** Sometimes it says so, sometimes it guesses at paint. Neither
gets an analyst to the operational meaning, and neither is hedged in a way a reader would catch.
The README now says exactly that, and shows the "no established meaning" sample because it is
the one that reproduces.

Generalises, and it is the more useful lesson: **a single striking transcript is an anecdote.**
Before a captured output becomes the argument in a README, run it enough times to know whether
it is the behaviour or the sample.

### 18. Licence as an enforced field, not a comment

ICEYE and IMO both publish these documents openly and **neither states a reuse licence**, so
default copyright applies. Fetching one onto the machine that reads it is not redistribution;
committing it to a public repository is.

Rather than trust the top-level `.gitignore` to stay correct forever, entries carry
`redistributable: false`, and the fetcher **writes a `.gitignore` containing `*` into the corpus
root and refuses to write restricted material without it**. The guarantee is local to the
directory holding the material and survives someone reorganising ignore rules elsewhere.

Same instinct as `Match.source_is_ground_truth` at M0: when a constraint matters, make it a
field the code checks rather than a sentence someone has to remember.

### 19. No retrieval score threshold, because the measurement says not to

The obvious way to make a RAG system refuse is a similarity floor. Measured over 11 probe
questions against this corpus:

| | best-chunk cosine |
|---|---|
| answerable (6 questions) | **0.543 – 0.708** |
| unanswerable (5 questions) | **0.440 – 0.497** |

The bands do not overlap — but they are **0.046 apart**. That is far too narrow to hang a
refusal on, and the failure mode of a threshold set slightly high is nasty: answerable questions
start refusing, which looks exactly like the honesty feature working correctly.

So `NIGHTGLASS_RAG_MIN_SCORE` exists and is **unset**. Refusal is decided by citation
verification, which needs no threshold to be right: retrieve, generate, then check every cited
chunk ID against what was actually retrieved, drop fabricated ones, discard claims left with
none, and refuse if nothing survives. The model's own `supported: true` is an opinion and is not
decisive.

### 20. Copernicus EMS has no listing endpoint, and its pages are a JS app

The activation pages at `mapping.emergency.copernicus.eu/activations/EMSR861/` render entirely
client-side: the served HTML contains navigation and a cookie banner, and none of the text.

The data is at a public JSON API found in the site's JS bundle:

```
https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/?code=EMSR861
```

⚠️ **`code` is mandatory** — every listing form 404s with
`{"detail": "No Activation matches the given query."}`. There is no way to enumerate
activations, so the four in the corpus came from scanning **EMSR840–EMSR915** individually
(65 of 76 codes resolved) and filtering for Portugal. EMSR861 and EMSR864 — the two §3.3 names —
both turn out to be Portugal activations, and EMSR842 and EMSR908 are the only other
Portugal-touching ones in that range.

Worth being honest about what these contribute: Copernicus EMS Rapid Mapping is disaster
response, so there is **no maritime security content in any of them**. They earn their place as
real, dated, citable environmental context over the demo AOI — which is what INTREP 2026/066
cites them for, and nothing more.

---

## M3 — done 2026-08-03. Own detector, real pixels, and a space–time join that holds up.

`make dark-proof` runs it end to end. §M3's "done when" passes: one SQL query returns detections
with no AIS correspondence inside a space–time window, and it was written and hand-checked as
plain SQL before any agent touched it.

```
detections    60      matched 45      dark 15  (25.0%)
median match distance 119 m       recall 88% on AIS vessels ≥ 30 m
```

### What M3 built

```
src/nightglass/spatial/
  safe.py                 SAFE read in place — annotation, calibration + noise LUTs, GCPs, STAC
  detect.py               the detector: CFAR, two land masks, region-grown sizing, TPS geolocation
  geodesy.py              bearings, and the azimuth-displacement physics
  coastline.py            ONLINE GSHHG fetch + AOI clip; the second land mask
  ais.py                  source adapters: DMAFileSource · GFWDetectionSource · CustomerFeedSource
  db.py                   PostGIS access; SQL lives in files
  sql/001_schema.sql      stac.scenes · detect.runs+detections · ais.positions
  sql/dark_vessels.sql    §M3's join, readable and runnable on its own
  render.py · plots.py    chips, overview, map, validation charts
  validate.py             measure the detector against DMA ground truth
docker/Dockerfile         .[services,spatial,viz] + libexpat1
docker-compose.yml        coastline-fetcher; SAR/AIS/coastline mounts, read-only
scripts/dark-proof.sh     §M3's proof, as a repeatable command
tests/test_spatial.py     35 tests — LUTs, geodesy, block stats, morphology, AIS parsing
```

### 21. ⭐ Every bug in the detector was invisible in the numbers and obvious in the first render

This is the finding. Three separate failures, all of which produced a plausible-looking table:

| what the table said | what the picture showed |
|---|---|
| 626 detections, sensible length distribution | 82% of the scene "land-masked" — it was processing Sweden, because the AOI bounded rows but not columns |
| 296 detections, water sigma0 −36.5 dB | salt-and-pepper noise where water should be smooth — `max(DN²−noise, 0)` was clamping half the sea to zero |
| 186 detections, clean statistics | a continuous string of "vessels" tracing the entire Danish coast |

None of those is detectable from a count, a length histogram, or a spot-check of coordinates.
All three were unmissable within seconds of looking at a rendered image. So rendering is part of
the pipeline (`make render`), not a debugging afterthought, and `data/out/` is a mount.

Generalises: **a detector that is only ever counted is a detector nobody has checked.**

### 22. ⭐ The azimuth-displacement sign: derived one way, measured the other, measurement won

The physics is real and large — R/V ≈ 115 s here, so a 12 kn vessel crossing the range direction
is drawn ~450 m from where it was, most of a 500 m match radius.

The derivation said a receding target is displaced *forward* along the flight path. Measured
against DMA truth:

| | <100 m | <200 m | <500 m | median |
|---|---|---|---|---|
| no correction | 8 | 17 | 43 | 330 m |
| sign **+1** (derived) | 0 | 3 | 23 | 630 m |
| sign **−1** (measured) | **19** | **33** | **45** | **173 m** |

The wrong sign is about as far wrong as the right one is right — the signature of a real
systematic offset applied backwards. The derivation had the processor placing a target at the
azimuth time whose Doppler *equals* the observed one; it actually focuses at the target's Doppler
*zero crossing*, which flips the sign.

**The lesson is not the sign. It is that the sign was left as a parameter to be measured**
(`geodesy.azimuth_displacement_m(..., sign)`), against ground truth, in a command that still
exists (`make validate-shift`). Had it been baked in as a constant, the pipeline would have run
happily with double the error and nothing would have said so.

### 23. ⭐ Allowing negative sigma0 fixed one bug and created another — in the same expression

VH over calm sea in this scene is genuinely **below the noise floor**: measured water sigma0
−29.1 dB against a NESZ of −29.4 dB. So `DN² − noise` is negative for roughly half the water
pixels, and clamping it at zero turns a well-behaved random field into a half-black binary one.
Removing the clamp fixed the image.

It immediately broke the CFAR, because the censoring rule was `pixels > censor × block_mean`.
That assumes a positive mean. With the mean near zero and often slightly negative — the S1 noise
LUT mildly over-subtracts at VH — `censor × mean` lands at or below zero, the "keep" set
collapses to the most negative pixels in the block, and the returned standard deviation
describes the bottom tail rather than the sea. Symptom: **1.2% of open water passing an 8-sigma
test**, about five orders of magnitude too many.

Fix is one line — censor by *sigmas above the mean*, not by a multiple of the mean — and it is
sign-agnostic by construction.

Two things made this findable rather than mysterious. The render showed the first half. And the
two threshold criteria (relative CFAR, absolute NESZ floor) are **counted separately** in the run
record, so "CFAR passes 565,727 px, NESZ floor passes 42,015" said plainly that the relative test
had collapsed. A single combined count would have hidden it.

### 24. ⭐ A data-derived land mask cannot separate a skerry from a hull, and that is structural

The mask has to *open* — erode then dilate — before buffering the shore, or every bright vessel
becomes its own island and the mask deletes exactly what the detector is looking for. Opening
removes bright objects smaller than the structuring element. **A 100 m rock is a bright object
smaller than the structuring element.** No threshold moves that trade-off: at VH a wet rock and a
hull are both compact, bright and small.

It presented exactly as theory predicts — a tidy line of "vessels" down the Swedish archipelago
off Gothenburg, three to ten kilometres offshore, with almost no AIS anywhere near them.

So the fix is data the scene does not contain: GSHHG full resolution (~100 m), fetched at
provisioning time and clipped to the AOI. 149 MB downloaded, 713 KB kept. The trade-off is
measured, not guessed:

| buffer | detections | matched | dark | rate |
|---|---|---|---|---|
| 300 m | 71 | 46 | 25 | 35% |
| **1000 m** | **60** | **45** | **15** | **25%** |
| 2000 m | 53 | 44 | 9 | 17% |
| 3000 m | 50 | 43 | 7 | 14% |

300 m → 3 km removes 21 detections: **18 dark, 3 matched.** Coastal detections are overwhelmingly
not vessels and the buffer costs almost no real ones.

The wider point is the deployment story: weights, documents, granules **and a shoreline** are
four things an air-gapped system needs shipped with it, not three. Saying so is a better answer
than pretending the list was shorter.

### 25. A `WHERE` clause silently deleted a third of the ground truth

The dark query filtered `t.cog_deg IS NOT NULL` so the azimuth-displacement trigonometry would
not see a NULL. **322 of 907 vessels have no COG at all** — they were dropped from ever being
matched, and every one became a spurious dark detection.

A vessel reports no course precisely when it is moored or drifting, which is exactly when its
azimuth displacement is zero. So the correct handling is a zero shift and a full chance to match,
not exclusion. The filter moved from the `WHERE` into a `CASE` on the shift expression.

Generalises: **a `WHERE` clause added to protect a calculation will quietly change the
population.** The tell was the count — 907 vessels loaded, 621 reaching the join — and nothing
was printing both.

### 26. Detecting and measuring want different thresholds

Detection needs a high threshold or the scene fills with false alarms. Measurement at that same
threshold gave lengths with **r = 0.015** against AIS-reported length — not a scale error, *no
relationship at all*, because at 8 sigma the blob's extent tracks peak brightness rather than
hull. A median ratio of 0.30× looked like a calibration problem and was not.

Re-growing each accepted detection at 2.5 sigma, seeded from the blob that passed 8, bounded to a
window, fixed the bias: median ratio **1.15×**. Correlation improved to **r = 0.198**, which is
still weak — so the honest report is that length is a size band, not a measurement, and the
README says so.

### 27. `%` in a SQL comment breaks psycopg, and escaping it breaks the file

The join lives in a `.sql` file precisely so it can be opened and run a CTE at a time. Two
comments contained `~20%` and `~5%`; psycopg scans the whole string for placeholders and failed
with *"incomplete placeholder: '%'"*. Doubling them to `%%` fixes psycopg and makes the file
invalid to run directly in `psql` — destroying the property the file exists for. Reworded the
prose instead.

### 28. rasterio's wheel needs a system library `python:slim` does not ship

`pip install rasterio` succeeds; `import rasterio` then dies with
`libexpat.so.1: cannot open shared object file`. The manylinux wheel vendors GDAL, PROJ and GEOS
but still dynamically links the system libexpat.

**A green build is not proof the enclave can read a pixel** — and there is no apt inside the
enclave to fix it afterwards. `libexpat1` is now in the base stage. Same category as M0's
`make test` failing on a missing package index: anything the running system needs has to be in
the image before the network goes away.

### 29. TPS geolocation is exact; a polynomial GCP fit is 185 m out

`rasterio.transform.GCPTransformer(gcps)` defaults to a polynomial fit through the 210 tie
points. Measured residuals against the tie points themselves:

| | mean | p95 | max |
|---|---|---|---|
| polynomial | 40.1 m | 100 m | **185 m** |
| **TPS** (`tps=True`) | **0.000 m** | 0.000 m | 0.000 m |

The geolocation grid of a 250 km swath is not a polynomial surface. TPS costs 15× more per point
— 0.15 s per 20,000 points, i.e. nothing — and at a 500 m match radius, 185 m of avoidable error
is over a third of the budget.

⚠️ Use `offset="ul"`, not the default `offset="center"`. The default adds half a pixel in each
axis, which showed up as a uniform **7.07 m** (= √2 × 5 m) bias and briefly looked like a real
geolocation error in the TPS numbers.

### 30. Two chart colours that failed a check my eye had already flagged

The result map drew matched detections in orange and unmatched in red. They were hard to tell
apart, and the palette validator said why: normal-vision separation **ΔE 7.1 against a floor of
15** — genuinely hard to distinguish *with full colour vision*, before colour blindness enters
it. Swapping matched to aqua passes at ΔE 20.9, and unmatched detections also carry a different
marker shape so identity never rests on colour alone.

Worth the note because the check took ten seconds and the alternative was arguing about taste.

### 31. The detector generalises to Portugal with no retuning — and that was the cheap check

Every parameter was set while looking at *one* Danish scene, which is a real overfitting risk.
Running the same configuration over `S1A_..._20260613T064316` (Lisbon approaches) unchanged:

| | Kattegat (S1D, 17 Jul) | Lisbon (S1A, 13 Jun) |
|---|---|---|
| water sigma0 / NESZ | −29.1 / −29.4 dB | −26.5 / −26.5 dB |
| land-masked | 51.6% | 33.0% |
| binding criterion | CFAR | CFAR |
| detections | 60 | 133 |

Different platform, different sea state, different beam geometry, no parameter touched. The
chips are unambiguous vessels.

**A hypothesis worth recording because it was wrong.** The map showed a near-vertical line of
detections at ~10.0°W, which looked like a sub-swath boundary artefact — a plausible failure,
since the IW noise correction is imperfect at those seams. Tested it: the noise annotation puts
the boundaries at samples 8751 and 17566, and only **1% and 3%** of detections fall within ±400
samples of them. Refuted. It is the north–south coastal shipping lane, and the chips confirm
hulls.

The lesson is the order: the hypothesis was cheap to state, cheaper to test against the
product's own annotation, and testing it took less time than arguing about it would have.

**One real effect it did surface:** almost every Lisbon-AOI chip shows heavy **azimuth smear** —
the vertical streak of a moving target — and the region-growing sizer picks the smear up, which
is why lengths run large there (226 m, 270 m, 325 m). This is the same mechanism behind the weak
length correlation measured in Denmark (r = 0.198), now visible rather than inferred. A wake- or
smear-aware sizer is the fix; it is on the three-weeks list, not in M3.

### Performance, for reference

| step | time |
|---|---|
| detector over the Kattegat AOI (97 M px examined) | ~12 s |
| AIS load, 38,239 deduplicated positions via COPY | ~6 s |
| the dark query | < 1 s |
| GSHHG fetch + clip for three AOIs | ~90 s, once |

---

## M4 — done 2026-08-03. Six tools, two surfaces, and a second guard on the one number.

`make tool-proof` is the whole milestone in one command: the MCP transport Claude Desktop
attaches with, a tool called over it, the local 14B model chaining three tools unaided, and the
INTREP refusing to state a rate.

### The two decisions the handoff said to make before writing code

**1. `correlate` reads an existing detector run rather than re-running the pixels.** The
tempting reading of §5's "no caching that changes results between runs" is that every call must
recompute. It is the wrong one, and finding 32 is the measurement that settles it. A run is
reused only on identity of every recorded input — detector, version, polarisation, AOI box,
coastline descriptor and every field of `DetectorConfig` — which is checkable because
`detect.runs.parameters` is jsonb.

**2. `correlate` is bounded to one scene per call.** The alternative — return a run id and let
the client poll — needs a job table, a status endpoint and a lifecycle, which is the hidden
state §5 rules out. One scene is 14–20 s and fits an MCP call; the two Danish granules together
would not. Scenes the search found but did not correlate come back in `scenes` carrying a
provenance note naming them and saying how to select them, so the bound is visible in the result
rather than documented somewhere else.

### 32. ⭐ Re-running the detector renumbers the detections — which is what decides reuse

The argument for reuse looked like an efficiency argument and turned out to be a correctness
argument. `_finalise` assigns ids (`…:det_00007`) **after** the length and AOI filters, so the
same physical vessel gets a different id at a different `min_length_m`. Measured over the
Kattegat scene:

| | detections | first five ids |
|---|---|---|
| stored run at 15 m, filtered to ≥ 100 m | 35 | `det_00001 … det_00005` |
| fresh run at 100 m | 35 | `det_00000 … det_00004` |

Same 35 physical detections — identical positions, lengths, headings, confidences — under
**different ids**. So `ais_match(["…:det_00005"])` silently means a different vessel depending on
whether the detector happened to be re-run in between. Reuse is the branch that keeps a tool
call meaning the same thing twice; recomputing is the one that changes results between runs.

Verified the other direction too, since the reuse path is only as good as its claim to be equal:
reused vs `recompute=True` at the same threshold gives 60 detections both ways, byte-identical on
id, position, length, heading and confidence — in 0.9 s against 13.9 s.

The filter-down equality is provable rather than approximate: nothing upstream of `_finalise`
reads `min_length_m`, so a stored run at 15 m filtered to ≥ 30 m *is* the run-at-30 m detection
set. A stored run at 30 m asked for 15 m is not a filter, it is missing data, and
`_same_parameters` refuses it.

### 33. ⭐ The rate guard was only half wired, and the Danish case is what proves the other half

`CorrelationResult.rate_is_quotable` guards the **source** side: is the AIS feed complete enough
to be a denominator. Nothing guarded the **precision** side: are the things in the numerator
vessels. Over Denmark the first passes — DMA is ground truth — and the answer is still no,
because 25% of detections are unmatched against a ~5% published base rate and the excess is
coastal clutter (finding 24). `rate_verdict()` checks both independently, and
`DETECTOR_PRECISION_VALIDATED` is a constant sitting at `False` with a test as its tripwire, so
flipping it can only happen in the same commit as the measurement that earns it.

Three layers, in increasing order of how much they can be trusted — the same structure
`rag/answer.py` uses for citations: the templated findings never compute a proportion; the
generation prompt forbids one; `scrub_rate_claims` removes any that survives. Only the third is
a check rather than a request, and it is the weakest-guarantee layer precisely because the first
layer means there is no correct number for a generated claim to be paraphrasing.

**A bug the guard hid while looking like it worked.** `dark_vessels.sql` returns
`COALESCE(n.is_ground_truth, false)`, which is false for every *unmatched* detection simply
because there is no matched vessel to read the flag from. Copying that onto the `Match` made
`rate_is_quotable` false the moment a single detection went dark — i.e. exactly when the question
is worth asking. The guard read as working while being stuck off, and over Denmark it was
reporting the wrong reason. The flag describes the feed that was *searched*, not whether the
search succeeded. Now derived from `ais.positions` for the acquisition window, with a test.

**And one the scrubber caught on itself.** A first draft of the regex required the negation and
the noun to be adjacent, so `25% of detections do not **have** AIS correspondence` — two words
apart — walked straight through. It also missed the complement: `75% of detections have AIS
correspondence` states the same number. Both are now cases in
`tests/test_tools.py`. The caveat explaining the guard has to use the words "dark-vessel rate" to
say the report does not quote one, which is why caveats are assembled structurally and never
scrubbed — running the guard over its own explanation would delete it.

### 34. ⭐ A ten-item sample shown next to the working set gets used as the working set

The first real chain run passed §M4's bar — three distinct tools, unprompted hedging — and
produced a **wrong answer**. The model called
`ais_match` six times in batches of ten, then reported the last batch's counts as the scene's.

The cause was mine, not the model's. `detect_vessels`' compacted result carried the full
`detection_ids` list *and* a bare `sample` of ten positions; the model used the sample. Worse,
each batched call answered correctly about its slice and nothing in the answer said it was a
slice — the tool cannot distinguish "tell me about these ten" from "tell me about the scene".

Two fixes, both about making partiality visible rather than about instructing harder:

- the sample key is now named `positions_sample_not_the_working_set`, and the payload carries a
  `next_step` telling the caller to pass every id in one call;
- `ais_match`'s result reports `requested`, `scene_detection_totals` and `partial`, plus an
  explicit warning when a slice is being answered. A result that cannot say "you asked about a
  sixth of this" is a result that will be over-generalised.

This is the same lesson as finding 21 in a different medium: the failure was invisible in the
counts, which were all individually correct.

### 35. ⭐ The model retuned the matcher mid-answer, and the tool let it

Finding 34's fix worked — one `ais_match` call with all sixty ids — and the answer was still
wrong: **7 matched, 53 unmatched** against a hand-checked truth of 45 and 15. The ids were right
(all sixty, correct scene, none invented), so the tool had been asked something different from
what I assumed.

It had. §5 puts `radius_m` in `ais_match`'s signature, so the model-facing schema exposed it, so
the model set it. Measured over the same pixels and the same AIS:

| match radius | matched | dark |
|---|---|---|
| 1000 m | 47 | 13 |
| **500 m — validated** | **45** | **15** |
| 100 m | 20 | 40 |
| 50 m | 8 | 52 |

The answer moves by a factor of three across values that all look reasonable in a function call.
`dark_vessels.sql` already says why in its header — "the radius is the whole boundary between
matched and dark" — but that reasoning was about not widening it to absorb the azimuth
displacement. This is the same knob from the other side: a model free to narrow it can
manufacture darkness, and every intermediate count it prints is internally consistent.

**So the local model's tool schema no longer exposes it.** The §5 Python contract keeps
`radius_m` and `time_window_min` — an analyst sweeping the tolerance is doing legitimate work,
and that sweep is how finding 24's 1 km buffer was chosen — but the surface driven by an
unsupervised 14B model does not get the knob that decides the headline number. Pre-dev already
measured what this model does when a result fails to satisfy it: it re-calls with tweaked
arguments, four times. The MCP surface keeps the parameter, because a human is reading each
call, with the numbers above in the tool description.

Every `ais_match` result now states the tolerance it used, so two counts can never be compared
without noticing they came from different rules.

**After the fix**, from a question with no scene id and no bbox in the prompt:
`stac_search → detect_vessels → ais_match`, 60 detections, 45 matched, 15 dark, and the fifteen
unmatched ids it listed are **identical to the database's**, in order, with nothing invented. It
also hedged unprompted — *"leads for further analysis … cannot be considered conclusive
evidence"* — and stated no rate.

### 36. ⭐ The loop-breaker fired on its first real outing, and the residue is M5's job

Pre-dev measured this model re-calling `stac_search` four times with tweaked dates when a result
did not satisfy it. Against real tools it did the same thing in a different place: having
correctly matched **both** Danish granules (45/15 each), it re-issued both `ais_match` calls with
byte-identical arguments. The same-tool-same-arguments detector caught the first repeat, told it
in the transcript that the call had already been made and what came back, and forced an answer on
the second. Three iterations, not an exhausted context window.

Two things worth carrying into M5. The guard should **not** kill the run on the first repeat —
re-calling can be correct when a result genuinely fails to satisfy the request, so the first
strike is information and the second is a stop. And the residue it cannot fix: the final answer
opened *"of the 60 detections … 45 matched … and 15 did not"* and then listed **30**
unmatched ids across both scenes. Every id was real and every per-scene count was right; the
model conflated two scenes into one scene's framing while summarising.

That is not fixable by prompting, and it is the argument for §M5's shape: the answer should be
assembled from the `CorrelationResult` the tools returned, not from the model's recollection of
them. `draft_intrep` already works that way — its findings are templated from the correlation
object, which is why section 4 of `make tool-proof` has the arithmetic right and section 3 does
not.

### 37. ⭐ "Found nothing" and "there was nothing to look in" are different answers

Over Lisbon, `correlate` returned **133 detections, 133 dark**. The SQL was right — a LEFT JOIN
against an empty `ais.positions` dutifully reports every detection as unmatched — and the
statement was false. §3.2's warning is *"if your pipeline reports 40% dark, it's broken"*; this
was 100%, and it read as a finding about the sea rather than about the database.

The fix is a predicate, not a threshold. `Feed.loaded` asks whether *any* AIS position exists in
the acquisition window, and the two callers use it differently on purpose:

- **`ais_match` refuses** — a `ToolError`, so an HTTP caller gets 503 with the reason and a model
  gets told rather than misled.
- **`correlate` skips and says so** — the 133 detections are real work and are still returned;
  what is withheld is the matched/dark verdict, because there is nothing to have matched against.
  The scene's provenance note now opens `DETECTION ONLY`, and `draft_intrep` leads its caveats
  with *"none of the 133 detection(s) above has been assessed against AIS. They are
  detections, not dark detections."*

The predicate is checked **before** the join rather than inferred from its output, and it is
deliberately binary: one AIS position counts as a feed. A sparsity *threshold* here would be a
judgement call with nothing behind it, and thinning is already handled where it belongs — by
`source_is_ground_truth` and the rate guard.

**The remediation hint is AOI-aware, and that is the part worth copying.** The obvious text —
"run `nightglass-spatial load-ais`" — is a lie over Portugal, where the configured feed is
aisstream and aisstream has no archive, so no recording started today can serve a June
acquisition. A hint that cannot be followed costs the reader a search for a file that was never
possible to make, so the message branches: `dma` names the command, `aisstream` says why there is
nothing to load and points at the Danish AOI, `gfw` says it is a reference layer and never an
input to `ais_match`.

Denmark is unchanged and regression-checked: 60 detections, 45 matched, 15 dark, and the scene
note now records *"matched against dma — 38,178 AIS positions within ±11 min"*, so a future
`133 dark` cannot be mistaken for a correlation that ran.

### 38. ⭐⭐ The Lisbon-AOI excess is not clutter — the detector counts one vessel several times

The GFW cross-check was built expecting to confirm the detector generalises. It did, and it also
answered a question nobody had asked properly. Over the identical granule
(`S1A_…20260613T064316…F72E`, on disk, 66 GFW detections inside the Lisbon AOI):

```
ours          133 detections   (nightglass-cfar)
GFW            66 detections   (published layer, same granule)
both saw       49   74% of GFW's, median separation 56 m
GFW only       17
ours only      84
```

**56 m median separation between two independent detectors** is the headline agreement number,
and it is tighter than the 119 m median against DMA AIS — expected, since both are measuring
pixels rather than pixels against a transponder.

The 84 was the interesting one, and the flattering reading — "our detector is more sensitive" —
is wrong. Measured how far each of those 84 sits from the nearest detection *both* detectors
found:

```
within 200 m   51   (61%)      too close to be a second vessel
median        152 m
```

Sixty-one per cent of the excess is sitting on top of something we already counted. This granule
holds nearer **82 distinct targets than 133**. The detector has no merge step, and finding 31
already saw the mechanism in the chips without drawing the conclusion: Lisbon-AOI scenes show
heavy **azimuth smear**, and the region-growing sizer picks up the smear, so one hull becomes
several blobs.

**The coastal-clutter hypothesis was tested and only partly holds here.** Within 5 km of shore:
24% of the ours-only detections against 6% of the agreed ones — a real 4× enrichment, but it
accounts for about a quarter of the excess, not the bulk. Over Denmark clutter dominated; over
Portugal fragmentation does. Two AOIs, two different failure modes, and neither would have been
visible from the other.

**What this changes.** "133 detections over Lisbon" was never a claim about 133 vessels and is
now measurably not one. The rate guard is unaffected — it already refuses on precision grounds —
but the README's limitation is sharper for being decomposed: the count is inflated by
fragmentation *and* the residue is enriched near shore, and both are measured rather than
asserted. A merge step, or a smear-aware sizer, moves the real number; it is on the three-weeks
list where finding 31 put it, now with a number attached.

**On what the comparison is worth.** Two detectors agreeing is weaker evidence than the AIS
validation banked over Denmark — it says the detector generalises, not that either is right, and
`Comparison.render()` prints that sentence every time so the number is never quoted without it.
GFW's own `matched` flags on the 49 agreed detections (45 matched, 4 not) are reported as
*their* computation under CC BY-NC 4.0, never merged into a `Match`, which is the line
`GFWDetectionSource` has refused to cross since M3.

### 39. `make_interval(mins => …)` will not take a fractional argument

Every named argument of `make_interval` is an integer except `secs`, so a window of 11.0 minutes
fails to resolve the function rather than rounding — `UndefinedFunction`, at runtime, from a
query that reads fine. `dark_vessels.sql` already took its window in seconds for this reason; the
new feed-lookup query did not, and found out.

### 40. "Take the newest scene" is the wrong default when granules come in pairs

Consecutive granules from one Sentinel-1 pass both clip an AOI — the normal case, not an edge
one. Over the Kattegat the newer of the two (`…34CE`, 05:24:01) covers **1.50 deg²** of the
requested box and the older (`…BC13`, 05:23:36) covers **0.94**, but the reverse is equally
possible and nothing in the result would look wrong either way. `correlate` ranks by intersection
area with the requested bbox and breaks ties by recency.

Deliberately *not* "prefer a scene that already has a detector run", tempting as that is at
14–20 s a granule: that would make the answer depend on what happened to be computed earlier,
which is the caching-changes-results failure again, and it would be invisible because the wrong
answer would still be a real correlation over a real scene.

**Incidental, and worth a second look at some point:** the two Danish granules are disjoint in
latitude (56.61–57.39 and 55.53–56.59, zero detections within 200 m of each other) and
independently give **60 detections, 45 matched, 15 dark each** — the same 25% on two separate
granules. Coincidence in the totals, but it does make the 25% look systematic rather than
scene-specific, which is consistent with the coastal-clutter reading.

### Performance, M4

| step | time |
|---|---|
| `detect_vessels`, run reused from PostGIS | 0.9 s |
| `detect_vessels`, cold — reads the granule | 14 s (BC13), 20 s (34CE) |
| `correlate`, warm | 1.6 s |
| `draft_intrep` with the narrative section | ~10 s |
| the local model chaining three tools | ~2 min |
| MCP `initialize` + `tools/list` over stdio | < 1 s |

---

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Demo AOI** | **Deliberately still open** — recorder covers both | Two viable candidates and no need to choose yet. **Lisbon/Tagus** `-10.5 38.0 → -8.5 39.5` has the best measured coverage (46 in 2 weeks, 81 in June, all VV+VH) and the EMSA-runs-from-Lisbon narrative. **Leixões** `-9.8 40.9 → -8.6 41.6` has three overlapping S1 paths (125/45/147) instead of two, so ~50% more acquisitions. Decide after Friday's scenes are on disk, not before. |
| **AIS recorder bbox** | **Union: lat 38.0–42.5, lon −11.5 → −8.0** | Spans both Iberian candidates with margin. Costs a larger footprint and some offshore dead space; buys the ability to change AOI after seeing real data. You can always filter down, never back-fill. `NIGHTGLASS_RECORD_BBOX_LAT/LON` in `.env`. **Axis order is `[[lat,lon],[lat,lon]]`** — opposite to most GIS tooling, and the most common way to get a silently empty stream. |
| **Danish validation date** | **2026-07-17** | S1D descending, granules 05:23:24 + 05:23:49 UTC. AIS day was the smallest candidate (848 MB) and the overpass sits earliest in the descending window. |
| **AIS ingest transport** | S3 REST against `aisdata.ais.dk.s3.eu-central-1.amazonaws.com` | Guide's `web.ais.dk` host is dead; official page is JS-rendered and unscrapeable. S3 REST is public, paginates, and needs no auth. See correction 1. |
| **AIS dedup key** | `(MMSI, timestamp, lat, lon)` | 71% of rows are rebroadcast duplicates (correction 7). Must happen before matching. |
| **AIS time handling** | Parse as UTC, no conversion | Settled by DST test (correction 5). |
| **Corpus is split, not one directory** | `corpus/synthetic/` committed · `data/corpus/` fetched + gitignored | Two different problems. A fresh clone must reproduce the demo, and there is no URL to fetch an invented memo from — so the memos are committed. Some publishers grant no reuse licence — so their documents are fetched, never vendored. One front-matter format for both, so ingest cannot tell them apart. |
| **PDF text via poppler `pdftotext`** | native dependency, `fetcher` image stage only | pypdf mangles EUR-Lex Official Journal PDFs: 676 and 114 spurious in-word spaces against 0 for pdftotext (finding 15). Confined to the online stage — the enclave never sees a PDF. |
| **ICEYE docs at a pinned commit** | `6c42568…`, not the rendered `latest` site | A corpus that cannot be rebuilt byte-for-byte is not reproducible, and the published site moves. `source_url` still points at the human-readable page, which is what a citation should open. |
| **Refusal by citation verification, not a score floor** | `NIGHTGLASS_RAG_MIN_SCORE` unset | Measured bands are 0.046 apart (finding 19). A threshold set slightly high turns answerable questions into refusals, which is indistinguishable from the feature working. |
| **Two land masks, not one** | data-derived (fine blocks, opened, buffered) **plus** GSHHG f/L1 clipped to the AOI | The data-derived mask must open before buffering or ships mask themselves; opening removes anything ship-sized, including skerries. Structural, not a tuning failure (finding 24). Buffer default 1000 m from a measured sweep: 300 m → 3 km removes 18 dark detections and 3 matched. |
| **Azimuth-displacement sign** | **−1**, confirmed by measurement, not by derivation | A receding target is drawn BACKWARD along the flight path. The derivation said forward and was wrong (finding 22). `make validate-shift` re-measures it against DMA truth; median match distance 330 m → 173 m. |
| **Detect at 8σ, measure at 2.5σ** | two thresholds, region-grown from the detection blob | One threshold cannot do both: at 8σ the blob is superstructure and length correlates with AIS at r = 0.015 (finding 26). Detection keeps its low false-alarm rate; measurement gets a threshold near the clutter tail. |
| **Sigma0 is NOT clipped at zero** | negative residuals kept | VH over calm sea is below NESZ here, so half the water pixels are legitimately negative. Clamping makes a binary field and destroys the CFAR statistics (finding 23). Censoring is by sigmas above the mean so it works either side of zero. |
| **TPS georeferencing, `offset="ul"`** | not the default polynomial fit | Polynomial leaves 40 m mean / 185 m worst case through 210 tie points; TPS leaves 0.000 m for 15× a negligible cost (finding 29). |
| **The dark join is a `.sql` file** | not assembled in Python | §M3 asks for it "as plain SQL, hand-checked". A file that can be opened and run a CTE at a time is checkable; an f-string is not. Forced one real constraint: no bare `%` anywhere, even in comments (finding 27). |
| **Scene stored as a whole STAC Item** | `jsonb`, with columns extracted beside it for indexing | `stac_search` is a catalogue query; modelling the catalogue as STAC keeps the door open to pointing the same tool at a real STAC API, which is what a customer deployment has. |
| **`ais.positions` PK is `(mmsi, ts, lat, lon)`** | the dedup rule as a constraint | 71% of raw DMA rows are rebroadcast duplicates. Making the key the rule means the database enforces it rather than the loader remembering to. |
| **Chunk id = `{doc_id}#{ordinal:04d}`** | positional, not content-hashed | Qdrant point IDs derive from it, so re-ingest updates in place. A content hash would orphan every citation already written down the moment a typo upstream was fixed. |

### Data on disk

```
data/raw/ais/aisdk-2026-07-17.zip    849 MB  (5.45 GB unzipped)  validation day
data/raw/ais/aisdk-2026-03-29.zip    593 MB  (3.13 GB unzipped)  DST test file
data/interim/ais_kattegat_20260717_0513-0534.csv   23 MB         acquisition-window slice
```

`data/` must be gitignored at M0.

---

## Checklist status

```
ENVIRONMENT
[x] nvidia-smi → 3090 visible, 22.3 GB free
[x] docker compose version → v5.3.1
[x] ollama 0.32.5 + both models, pinned resident (UNTIL=Forever)

PORTUGAL (demo AOI)
[x] ASF count query >0 for chosen bbox → 46, risk retired
[x] Demo AOI fixed → Lisbon/Tagus -10.5 38.0 → -8.5 39.5
[x] S1 GRD over Portugal downloaded, VH opens (path 125, covers approaches)
[x] GFW API token obtained + verified (HTTP 200)
[x] GFW per-detection records + matched flags → 4wings/tile/position, NOT
    4wings/report. 13 detections = 10 matched + 3 unmatched over Lisbon
    2026-06-13, each carrying its source granule id. See finding above.

DENMARK (validation AOI)
[x] Earthdata + EULA accepted + cookie jar → downloads work (206, PK header)
[x] Kattegat S1 coverage confirmed → 72 granules in July 2026
[x] S1 GRD over Kattegat downloaded, VH opens, 210 GCPs
[x] DMA source located (guide's URL is dead — see correction 1)
[x] DMA zip downloaded and verified → aisdk-2026-07-17.zip, 5.45 GB unzipped
[x] AIS CSV parses with '# Timestamp' intact → 26 comma-separated fields
[x] Timezone DST test run → UTC confirmed
[x] AIS filtered to acquisition ±10 min → 907 vessels / 134,851 positions ✅ GATE PASSED
```

**The Danish half of the pre-dev gate is fully cleared.** The guide's closing line —
*"SAR pixels plus a few hundred AIS positions inside the footprint at acquisition time, and
everything downstream is code"* — is half satisfied: 907 unique vessels (not merely a few
hundred positions) sit inside the Kattegat AOI at the S1D acquisition instant. The only
missing ingredient on both AOIs is the SAR pixels, which is blocker A alone.
