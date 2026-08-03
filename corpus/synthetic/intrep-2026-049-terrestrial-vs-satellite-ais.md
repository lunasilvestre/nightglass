---
doc_id: intrep-2026-049-terrestrial-vs-satellite-ais
title: Terrestrial and Satellite AIS Coverage — Where Silence Is Informative
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-05-11
aoi: [lisbon, kattegat]
serial: INTREP 2026/049
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTREP 2026/049 — Terrestrial and Satellite AIS Coverage

## 1. The two collection modes

**Terrestrial AIS.** Shore-based VHF receivers. Line-of-sight, so range is set by
antenna height and is typically on the order of 40 NM. Within range, temporal
coverage is essentially continuous — every transmission is heard. Outside range,
coverage is zero, and the transition is fairly sharp.

**Satellite AIS.** Receivers in low Earth orbit. Global in extent but sampled in
time: a given patch of ocean is observed during passes, not continuously. A
satellite receiver also sees a very large footprint at once, which in busy water
means many vessels transmitting into the same time slots and a correspondingly
high message collision rate.

The two modes fail in **opposite** ways. Terrestrial is complete where it
reaches and absent where it does not. Satellite reaches everywhere and is
complete nowhere.

## 2. Consequence for interpreting silence

Silence is informative **only inside a region of known good reception at a known
time**. Specifically:

- **Inshore, inside terrestrial range, at any time**: silence is strongly
  informative. There is no coverage explanation available.
- **Offshore, terrestrial only**: silence is uninformative. It is the expected
  reading.
- **Offshore, satellite, coincident with a pass**: moderately informative,
  discounted for collision loss in dense traffic.
- **Offshore, satellite, between passes**: uninformative.

For the LISBON AOI this maps onto geography directly. The eastern part of the
box — the estuary, the approach channel, the anchorages — sits inside good
terrestrial coverage. The western band of open Atlantic does not. **The same
detection carries very different weight depending on which side of the box it
falls in**, and a report that does not say which is incomplete.

## 3. The trap in combining sources

Merging a terrestrial archive with a satellite or aggregated feed produces a
dataset whose coverage varies across space and time in a way that is no longer
recorded anywhere. A dark determination made against such a merge cannot be
graded, because the question "was this vessel observable?" no longer has an
answer.

If sources are merged, the per-position source must be preserved, and any
coverage claim must be made against a single named source.

## 4. Practical rule

Before writing that a detection was dark, state the collection mode and its
expected coverage at that position and time. If the answer is "we would probably
not have heard this vessel anyway", the detection is not a lead.

## 5. Reliability

Source reliability **B**, information credibility **2**.
