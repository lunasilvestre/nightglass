---
doc_id: intrep-2026-007-ais-gap-taxonomy
title: Taxonomy of AIS Gaps — Benign, Ambiguous, Suspicious
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-02-19
aoi: [lisbon, kattegat]
serial: INTREP 2026/007
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTREP 2026/007 — Taxonomy of AIS Gaps

An AIS gap is any interval in which a vessel that is expected to be transmitting
is not present in the feed. Not all gaps are equal, and the single most common
analytical error in this cell is treating them as if they were.

## 1. Benign gaps — attributable to the observer

The vessel transmitted; we did not hear it. Nothing about the vessel's conduct
can be inferred.

- **Terrestrial receiver range.** Coastal AIS receivers are line-of-sight, with
  a practical range of roughly 40 NM. Beyond that, only satellite AIS sees the
  vessel, and satellite AIS has revisit gaps.
- **Message collision in dense traffic.** Where many vessels share the same
  time slots, receivers lose messages. Density and loss rise together, so gaps
  cluster exactly where traffic is heaviest.
- **Feed thinning.** Aggregated or free feeds may carry only a sample of the
  messages actually received. A feed that reports 17% of the vessels a
  ground-truth feed reports will manufacture dark detections at scale.
- **Sensor revisit.** SAR gives one instant. If the last AIS report predates it
  by more than the matching window, the vessel is absent from the window without
  ever having stopped transmitting.

## 2. Benign gaps — attributable to the vessel, lawfully

The vessel is not transmitting, and is entitled not to be.

- **Non-carriage.** Small craft, many fishing vessels and most recreational
  craft are under the SOLAS carriage threshold and carry no AIS at all.
- **Class B power.** Class B transponders transmit at lower power and lower rate
  than Class A, and are markedly more likely to be missed at range.
- **Equipment failure.** Transponders and antennas fail. This is unremarkable
  and is usually accompanied by an abrupt end to transmission with no
  positional oddity.
- **Master's discretion under IMO A.1106(29).** The guidance permits AIS to be
  switched off where continued operation would compromise the safety or security
  of the ship — the standing example is transit through waters where piracy or
  armed robbery is a live risk. The action is expected to be recorded in the
  ship's log and reported to the competent authority.

## 3. Ambiguous gaps

Consistent with either explanation; require a second observation before they
carry weight.

- A single gap of under 30 minutes at the edge of receiver coverage.
- A gap that begins and ends with the vessel on a steady course and speed.
- A gap during which no SAR acquisition exists, so nothing contradicts it.

## 4. Suspicious gaps — the ones worth reporting

None of these is proof. Each raises the credibility grade of a dark detection.

- **Gap coincident with a SAR detection.** The vessel is demonstrably present
  and demonstrably not transmitting. This is the strongest single indicator
  available to this cell.
- **Repetition.** The same track goes dark over the same stretch of water on
  successive transits.
- **Gap with positional inconsistency at the ends.** The vessel reappears
  further from the gap's start than its reported speed allows, or on a course
  that does not connect.
- **Paired gaps.** Two vessels go dark in overlapping windows within a few
  nautical miles of each other and reappear separated — the signature of an
  undeclared ship-to-ship transfer.
- **Gap in a location with no operational reason for one** — inside a
  well-covered approach channel rather than in mid-ocean.

## 5. Rule of thumb

Rank a gap by **who could not hear whom**. If the explanation lies with the
observer, the gap says nothing about the vessel. Only when observation is known
to be good does silence become information.

Source reliability **B**, information credibility **2**.
