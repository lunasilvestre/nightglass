---
doc_id: intrep-2026-066-anchorage-loitering-tagus
title: Loitering Behaviour at Tagus Anchorages — Indicators and Base Rates
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-06-29
aoi: [lisbon]
serial: INTREP 2026/066
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTREP 2026/066 — Loitering at the Tagus Anchorages

## 1. Why loitering is a weak indicator on its own

Anchorages exist so that vessels can wait. Waiting is the normal state of an
anchorage, and a vessel stationary off Cascais or Trafaria for two days is
unremarkable: it may be awaiting a berth, a tide, a pilot, bunkers, crew, cargo
documentation, weather, or a commercial decision made ashore.

Loitering therefore has an extremely high base rate and, taken alone, near-zero
discriminating power. It is included in this taxonomy because it is frequently
over-weighted by inexperienced drafters.

## 2. When loitering becomes worth reporting

Only in combination. The following pairings raise it above background:

- **Loitering plus no subsequent port call.** The vessel waits, then leaves
  without ever entering. Waiting is for something; if nothing follows, the
  waiting was for something not recorded here.
- **Loitering plus an AIS gap inside the loiter.** A vessel that is stationary is
  in the best possible conditions for reception. Silence while stationary and
  inshore is much harder to explain by coverage than silence while under way and
  offshore.
- **Loitering plus a second vessel.** Two vessels stationary in close company
  outside the designated anchorage areas, particularly if their arrival and
  departure times bracket each other.
- **Loitering outside the charted anchorage.** Using the water but not the
  facility.
- **Loitering plus a draught change across the period.**

## 3. What SAR adds

AIS alone cannot distinguish "stationary and transmitting" from "stationary and
silent" — silence looks like absence. SAR resolves this directly: a detection at
the loiter position during an AIS gap establishes **presence without
transmission**, which is the whole basis of a dark determination.

This is the strongest argument for tasking SAR over an anchorage rather than only
over transit corridors.

## 4. Sea state caveat

Detection over the anchorages degrades in heavy weather. High sea state raises
sea clutter in VH, which raises the effective detection threshold and drops small
targets first. A quiet anchorage in a storm image is a statement about the
storm.

Copernicus EMS activations over Portugal — notably EMSR864, a coastal flood
event driven by a severe weather system with significant maritime agitation, and
EMSR861 for Storm Kristin — are useful for establishing whether a given
acquisition date fell inside such a period.

## 5. Reliability

Source reliability **C**, information credibility **3**.
