# The agent, and a gate that actually stops

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

![The agent graph — a linear chain that genuinely stops at the gate](agent-graph.svg)

`parse → plan → tools → correlate → draft_intrep → HUMAN_GATE → release`, as a LangGraph state
machine against a **Postgres checkpointer**. `make agent-proof` runs it end to end.

The halt is demonstrated the only way that means anything — the drafting container *exits*, and
a different one picks the run up:

```
$ make ask Q="Were there any vessels with no AIS correspondence on 17 July 2026?"
⏸  HUMAN_GATE — the graph has stopped
  marking             UNCLASSIFIED // SYNTHETIC // DRAFT — NOT RELEASABLE
  claims              18
  unsupported claims  0

$ psql -c "select thread_id, count(*), sum(length(blob)) from checkpoint_blobs group by 1"
    thread_id    | channels | state
-----------------+----------+--------
 ng-cad785dbbc71 |       10 | 104 kB

$ make approve T=ng-cad785dbbc71          # a different container, minutes later
resumed from persisted state — RELEASED
```

`MemorySaver` would satisfy the same API and keep the state inside the process that is supposed
to have stopped; a bare `input()` would block a thread and lose everything if it died. Being
precise about what this buys, because the obvious stronger claim is false: plain SQL gives you
the run's existence, its thread, where it stopped and how much state it holds. The *values* are
msgpack, so reading the payload goes through the checkpointer — which is what
`nightglass-agent show` does for the reviewer.

### Where the model is allowed to act

The interesting decisions in this graph are about where the model is kept *out*.

| node | who decides | why |
|---|---|---|
| `parse` | model | language, lookback, whether documents are needed — all recoverable if wrong |
| | **not** the model | **the bbox.** The AOI configuration decides it. A model inventing one searches the wrong ocean and returns cleanly |
| `plan` | nobody | facts about config and the database, all checkable |
| `tools` | model | which documentary context the report needs — bounded, with the max-iteration and repeat guards |
| `correlate` | nobody | runs deterministically over the configured AOI whatever the model did |
| `draft_intrep` | mixed | findings templated from the `CorrelationResult`; assessment generated, cited, scrubbed |
| `HUMAN_GATE` | the human | the only path to `releasable=True` |
| `release` | nobody | prose assembled from the report's own fields |

That split is a fix for a measured failure, not caution for its own sake. In early testing the model got
every per-scene count right, listed thirty real detection ids, and still summarised them as "of
the 60 detections … 15 were not" — conflating two scenes. No prompt fixes that reliably, so the
released answer is not generated: every line comes from a `Claim` or a computed caveat.

On the first end-to-end run the rate guard earned its place unprompted. The model wrote a
proportion into the assessment section, and the draft came back carrying a caveat nobody typed:

```
1 generated claim(s) were removed before drafting because they stated a rate
of unmatched detections.
```

The prompt layer and the schema layer both let it through. The check caught it.

### What the numbers do *not* say

- **40% unmatched is not a 40% dark-vessel rate.** Published work on Danish waters finds ~5%
  unmatched and ~0.4% genuinely dark after review. The residual here concentrates near shore and
  is dominated by clutter and isolated false alarms this pipeline has not separated from vessels.
- **That 40% used to read 25%, and the correction made it worse.** 45 matched detections over
  the Kattegat resolved to **18 distinct MMSIs** — one ship accounting for six of them — so the
  old denominator was padded with duplicates of vessels that *did* match. Merging them
  (`DetectorConfig.merge_radius_m`, validated by AIS: zero clusters mix MMSIs at 100–300 m)
  collapses matched detections 45 → 21 while unmatched barely moves, 15 → 14. Which is itself
  informative: the unmatched residue is *isolated*, not fragments of real ships.
- **No AIS is loaded for Portugal, and the tools refuse rather than guess.** `ais_match` raises
  instead of returning 133 unmatched detections, and `correlate` returns the detections with the
  verdict withheld. "We searched a feed and found nothing" and "there was no feed to search" are
  different statements, and only the first one is a dark detection.
- **Detected length is not a reliable size estimate.** Detecting at 8σ and measuring at the same
  threshold gave lengths with **r = 0.015** against AIS — no relationship at all, because at
  that threshold the blob tracks peak brightness rather than hull. Re-growing each detection at
  2.5σ fixed the *bias* (median ratio 0.30× → **1.05×**) but the per-vessel scatter stays wide
  (**r = 0.271**). The median is usable; an individual number is not — which is why the INTREP's
  per-detection extents should be read as "something of roughly this size was here".
- **`rate_is_quotable` is a field the code checks**, not a sentence someone has to remember. It
  is true here only because every match came from DMA. It is also only *half* the check — see
  below.
