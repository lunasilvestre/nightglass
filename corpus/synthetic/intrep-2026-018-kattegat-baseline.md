---
doc_id: intrep-2026-018-kattegat-baseline
title: Validation Baseline — Kattegat, and Why Denmark Is the Reference AOI
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-03-23
aoi: [kattegat]
serial: INTREP 2026/018
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTREP 2026/018 — Validation Baseline: Kattegat

## 1. Purpose of this AOI

The Kattegat area of interest is not a demonstration area. It exists for one
reason: **it is the only water in this system's coverage where the AIS reference
is complete enough to be treated as ground truth.** Everything that is claimed
quantitatively is claimed here and nowhere else.

The Danish national AIS archive is a dense terrestrial receiver network over the
vessel's home waters, published as complete daily files. That combination —
dense reception plus published archive — does not exist for Portuguese waters at
any price this project can reach.

## 2. Overpass windows

| Pass | Window (UTC) |
|---|---|
| Descending | 05:23–05:40 |
| Ascending | 16:44–17:09 |

The ascending window is wider than commonly published tables suggest. Tables
that begin it around 16:52 silently discard roughly a third of ascending passes,
because a substantial group of acquisitions falls at 16:44. Take overpass
windows from the catalogue, never from a pre-computed revisit table.

## 3. Feed hygiene

The archive is delivered as daily files, and roughly seventy per cent of rows in
a given acquisition window are **exact rebroadcast duplicates** produced by
multiple receiving stations logging the same message. Deduplicate on
`(MMSI, timestamp, latitude, longitude)` before any matching is attempted.

Timestamps in the archive are **UTC**. This is not documented by the publisher
and was established empirically: a spring-forward day shows no one-hour hole at
the transition, which a local-time file would show. Slice acquisition windows in
UTC with no conversion.

## 4. What the AOI is used to establish

- The unmatched rate the pipeline produces, against the ~5% figure reported in
  open literature for comparable waters.
- Whether the matching tolerance is producing false darks. If the pipeline
  reports tens of percent dark over the Kattegat, the pipeline is wrong; the
  water is known.
- The completeness shortfall of any alternative feed. Because ground truth
  exists here, an alternative source can be measured against it — and a source
  that returns a small fraction of the vessels a ground-truth feed returns is
  disqualified as a basis for rate claims.

That last point is the structural reason this AOI is worth its cost: **feed
completeness over Portugal cannot be measured, because the absence of a Danish
equivalent is simultaneously why an alternative feed is needed there and why
there is nothing to check it against.**

## 5. Reliability

Source reliability **A**, information credibility **1**. Ground-truth AOI;
figures verifiable against the published archive.
