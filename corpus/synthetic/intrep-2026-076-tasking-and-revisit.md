---
doc_id: intrep-2026-076-tasking-and-revisit
title: One Scene Is One Instant — Tasking, Revisit and What a Single Acquisition Proves
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-07-22
aoi: [lisbon, kattegat]
serial: INTREP 2026/076
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTREP 2026/076 — One Scene Is One Instant

## 1. The core limitation

A SAR acquisition is a **snapshot**. It establishes that a vessel was at a
position at one instant, to within the sensor's geolocation accuracy. It
establishes nothing whatsoever about the minute before or the minute after.

Almost every over-claim this cell has had to correct comes from forgetting this.
A single scene cannot support:

- that a vessel was loitering (needs two observations),
- that a vessel was *transferring* cargo rather than merely near another vessel,
- that a vessel had been dark for any duration,
- that a vessel changed course, speed or draught.

What a single scene supports is: **present, here, then, approximately this
size, and not transmitting into this feed at that moment.** Duration claims come
from the AIS track, not from the image.

## 2. Revisit

Coverage over the areas of interest here comes from a repeating orbit, so
acquisitions arrive in fixed windows rather than on demand. Two consequences:

- **Gaps between acquisitions are long compared with vessel movement.** A vessel
  making 12 knots covers roughly 22 NM in an hour. Between two acquisitions of
  the same area it may have left and been replaced by another.
- **Association across scenes is inference, not observation.** Two detections in
  the same place on consecutive passes are not necessarily the same vessel, and
  saying so requires stating the assumption.

Constellation composition also changes over time, so the set of platforms
supplying a given area is a property of the date. Take it from the catalogue for
the acquisition in hand, never from a stored revisit table — a hard-coded
assumption about which platforms are flying silently returns nothing once one
retires.

## 3. Tasking versus archive

Where a sensor can be **tasked**, acquisition times can be chosen to coincide
with a period of interest, and the analytical question shapes the collection.
Where only an **archive** is available — as here — the collection shapes the
analytical question, and the honest framing is *"this is what was imaged"*
rather than *"this is what was happening"*.

Neither is better. But a report built on archive imagery must not be written as
though the acquisition time had been chosen.

## 4. Practical drafting rule

Write the acquisition instant into every claim that depends on it, in UTC. A
claim that reads "vessel X was dark" is wrong; "no AIS correspondence within
500 m of the position of detection D at 06:41:12 UTC on 21 June 2026, against
the named feed" is right, and is the same length once the reader has to ask.

## 5. Reliability

Source reliability **B**, information credibility **2**.
