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

This system never states a dark-vessel rate, and the released prose is assembled rather than
generated for the same reason: `rate_is_quotable` is a field the code checks, true here only
because every match came from DMA, and it is only half the check — full argument for that half
and the enforcement layers: [limitations](limitations.md#ais-coverage-and-what-a-rate-may-be-quoted-from).
The other half, why 40% unmatched is not a 40% dark-vessel rate and why that figure got worse
when it was corrected: [limitations](limitations.md#the-detector).

**No AIS is loaded for Portugal, and the tools refuse rather than guess.** `ais_match` raises
instead of returning 71 unmatched detections, and `correlate` returns the detections with the
verdict withheld. "We searched a feed and found nothing" and "there was no feed to search" are
different statements, and only the first one is a dark detection.
