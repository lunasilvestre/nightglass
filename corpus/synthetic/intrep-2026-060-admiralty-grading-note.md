---
doc_id: intrep-2026-060-admiralty-grading-note
title: Admiralty Grading in This Cell — Source Reliability and Information Credibility
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-06-16
aoi: [lisbon, kattegat]
serial: INTREP 2026/060
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTREP 2026/060 — Admiralty Grading

## 1. Two independent axes

Every input carries two grades, and they are graded **separately** because they
answer different questions.

**Source reliability (A–F)** — how much confidence the source itself has earned,
independent of this particular report.

| Grade | Meaning |
|---|---|
| A | Completely reliable |
| B | Usually reliable |
| C | Fairly reliable |
| D | Not usually reliable |
| E | Unreliable |
| F | Reliability cannot be judged |

**Information credibility (1–6)** — how much confidence this particular item has
earned, independent of who supplied it.

| Grade | Meaning |
|---|---|
| 1 | Confirmed by other sources |
| 2 | Probably true |
| 3 | Possibly true |
| 4 | Doubtful |
| 5 | Improbable |
| 6 | Truth cannot be judged |

A reliable source can report something implausible (A5); an unproven source can
report something independently confirmed (F1). Collapsing the two into a single
"confidence" score destroys exactly the distinction the scheme exists to
preserve.

## 2. Standing grades for this cell's routine inputs

| Input | Reliability | Credibility | Note |
|---|---|---|---|
| Sentinel-1 GRD from the mission catalogue | A | 1 | Instrument data, published |
| Danish national AIS archive | A | 1 | Ground truth for the Kattegat AOI |
| Our own SAR detector output | B | 2 | Our detector, no independent tuning |
| A thinned or sampled AIS feed | C | 4 | Coverage unknown, not ground truth |
| Published detection layers matched upstream | B | 2 | Reference layer, not independent |
| This cell's doctrine notes | B | 2 | Internally agreed |

## 3. Why an ungradeable output cannot be released

Grading requires knowing where each element came from. **Output that cannot be
traced cannot be graded, and output that cannot be graded cannot enter the
intelligence cycle** — however fluent it reads.

This is the operational reason every claim in an INTREP must carry its
references: scene identifier, detection identifiers, document chunk identifiers,
timestamps. A claim without references is not a low-graded claim. It is not a
claim at all, and it must be removed before release rather than published with a
caveat.

## 4. Handling of generated text

Any text produced by an automated assistant inherits the grades of the sources
it cites and carries none of its own. Where such text asserts something for
which no source is cited, the correct handling is deletion, not down-grading —
an unsourced assertion has no provenance to grade, and its fluency is not
evidence.

Assistants must be constrained to state explicitly when the available sources do
not support an answer. **A system that says it does not know is more useful than
one that always answers**, because only the first can be graded at all.

## 5. Reliability

Source reliability **B**, information credibility **2**.
