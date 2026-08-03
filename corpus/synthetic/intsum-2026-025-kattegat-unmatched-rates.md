---
doc_id: intsum-2026-025-kattegat-unmatched-rates
title: Reading an Unmatched Rate — What Counts as Plausible
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intsum
language: en
date: 2026-03-30
aoi: [kattegat, lisbon]
serial: INTSUM 2026/025
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTSUM 2026/025 — Reading an Unmatched Rate

## 1. The two numbers that matter

Open literature on SAR–AIS correlation in well-instrumented European waters
converges on two figures:

- **Roughly 5% of SAR detections do not match an AIS report** under sensible
  tolerances.
- **Well under 1% survive analyst review as genuinely unexplained.**

The gap between the two is the analyst's work. Most of what the machine calls
unmatched dissolves on inspection into small craft below the carriage threshold,
Class B units, timing, or displacement.

## 2. Diagnostic ladder

Use the observed rate as a diagnostic of the pipeline before treating it as a
finding about the water:

| Observed unmatched | Most likely reading |
|---|---|
| 0% | Matching radius far too wide, or AIS being matched to itself |
| 1–10% | Plausible. Proceed to review the individual candidates |
| 10–25% | Suspect the tolerance, the time window, or the interpolation |
| >25% | Suspect the feed. Something is not being received at all |
| >40% | The pipeline is broken. Do not report |

A rate of 40% dark is not an alarming intelligence finding. It is a bug report.

## 3. The dominant failure modes, in order

1. **A thinned AIS feed treated as ground truth.** If a feed carries a small
   fraction of the vessels actually transmitting, the matcher marks the
   remainder dark. This produces the largest errors and produces them
   confidently.
2. **Symmetric matching tolerance.** See INTREP 2026/041. Manufactures false
   darks from fast movers.
3. **Duplicate-weighted nearest-in-time selection.** See INTSUM 2026/030.
4. **Time window too narrow for the feed's report interval.**
5. **No minimum length filter**, so oceanographic and wind-driven false
   positives enter as unmatched detections.

## 4. Where a rate may be quoted

Only over an AOI whose AIS source is ground truth. Elsewhere the defensible
statement is *"here are N detections I could not match, with the space–time
reasoning shown"* — a description of what was done, not an estimate of what is
in the water.

This is a structural constraint, not a stylistic preference: the property should
travel with the data rather than depending on the drafter remembering it.

## 5. Reliability

Source reliability **B**, information credibility **2**.
