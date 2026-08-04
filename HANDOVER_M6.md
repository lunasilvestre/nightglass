# Handover prompt — M6

Paste everything below the line into a fresh session.

---

Read the "→ HANDOFF TO M6" section at the top of NOTES.md, then EXECUTION_SPEC.md §M6 and §8,
and build M6 — packaging and the recording.

Start by running `make up`, then `make dark-proof`, `make tool-proof` and `make agent-proof`.
Together they take about eight minutes, most of it the 14B model, and they are the fastest way
to see the state of things. If `dark-proof` refuses with "no coastline", run
`make fetch-coastline` first — that failure is loud by design.

**M0–M5 are done and every "done when" is met.** The enclave is sealed, inference runs offline,
the RAG cites or refuses, the detector and the space–time join work against ground truth, the
six §5 tools serve over HTTP and MCP, and the LangGraph agent halts at a human gate and resumes
in a different container. What is left is §M6: someone else clones this, runs `make up`, and
reproduces the demo — plus a 90-second recording.

## Two things block "clone and reproduce", and one is not written yet

**1. There is no `make fetch-granules`.** Four of five provisioning inputs have a fetch target;
the SAR granules were staged by hand during pre-dev and `data/` is gitignored. So §M6's done-when
is currently false for anyone but this machine. Sentinel-1 is free and open, `~/.netrc` already
carries working Earthdata credentials and the ASF EULA is accepted, and the pattern to copy is
`gfw-fetcher` in docker-compose.yml: a profile-gated service on the `provision` network,
`target: fetcher`, `user: ${HOST_UID}`, credentials forwarded from the invoking shell rather than
written into `.env`. The six granule ids are in `stac.scenes` and in NOTES.

**2. The demo AOI has no AIS, and §6 says record over Portugal.** This is not a bug and the tools
are honest about it — `ais_match` raises rather than reporting 133 unmatched detections,
`correlate` returns the detections with the verdict withheld, and the INTREP leads with "these
are detections, not dark detections". But a recording of a refusal is a strange demo. aisstream
cannot fix it: the adapter says so itself, it is real-time only with no archive, so nothing
recorded now can serve a June acquisition.

Pick before you build the recording, not during it. The options, in the order they are worth
trying, are in the M6 handoff in NOTES. My recommendation is **Portugal for detection, the GFW
cross-check and the Portuguese-language INTREP; Denmark for the correlation numbers.** Two AOIs
on screen makes §3.1's config-driven argument visible instead of asserted, and it needs nothing
new built — `make fetch-gfw && make gfw-compare` already works. Whatever you choose, do not let
the recording imply a Portuguese dark-vessel finding.

## The numbers changed recently — use the current ones

A merge step landed after M5 (NOTES finding 46) and it moved the headline figures, so treat any
number you find in an old commit message or a stale screenshot as wrong. Current, over the Danish
validation scene `S1D_…BC13`:

```
detections 35   matched 21   dark 14   unmatched 40.0%   median match 104 m
```

**40% is not a dark-vessel rate and the code will not let you state one.** It is the detector's
false-alarm rate, measured. It reads worse than the 25% it replaced because the old denominator
was padded with duplicate detections of vessels that *did* match — 45 matched detections were 18
distinct MMSIs. The guard in `tools/intrep.py` refuses a rate on two independent grounds, one of
which (`DETECTOR_PRECISION_VALIDATED`) is a constant with a test as its tripwire. Do not flip it.

## What I would not do

Do not tune the detector to agree with GFW — that is fitting to another detector, and the AIS
validation over Denmark is the stronger evidence. Do not add features; the system does what the
spec asks. Do not soften the 40%.

## One habit worth inheriting

**Ask Denmark first.** The fragmentation bug was found sideways through a GFW comparison over
Portugal when a single `Counter(mmsi)` over the Danish matched set would have shown it
immediately, with ground truth instead of a second opinion. Before building anything to answer a
question about the detector, check whether the DMA AIS already answers it. It usually does, and
it answers definitively.

Related: `make lint` and `make test` do not exercise Makefile targets or the proof scripts. An
M5 edit deleted an entire Makefile section and shipped, because everything I was checking still
passed (finding 47). If you change the Makefile with a script, diff the `.PHONY` inventory.

## Keep NOTES.md current

Numbered findings for surprises, README for evidence. Findings are at 47. The file is the study
aid and it is doing real work — most of what is above came out of it.
