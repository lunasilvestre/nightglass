---
doc_id: spotrep-2026-063-tagus-sts-candidate
title: SPOTREP — Paired AIS Gap West of Cascais, Possible Ship-to-Ship Transfer
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: spotrep
language: en
date: 2026-06-21
aoi: [lisbon]
serial: SPOTREP 2026/063
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. The vessels below are invented. Their identifiers use MMSI
maritime identification digits **999**, which is not allocated to any
administration, precisely so that no entry here can collide with a real vessel.*

# SPOTREP 2026/063 — Paired AIS Gap West of Cascais

## 1. Event

During the descending Sentinel-1 pass of 21 June 2026 (acquisition 06:41 UTC), a
SAR detection pair was observed approximately 21 NM west of Cascais, inside the
LISBON area of interest. Two returns, separated by roughly 140 m, both
consistent with hulls in the 180–250 m class, on near-parallel headings.

Neither return matched an AIS position within 500 m of the acquisition instant,
after azimuth-displacement correction per INTREP 2026/041.

## 2. Track reconstruction

| Designator | MMSI (synthetic) | Last AIS before | First AIS after | Gap |
|---|---|---|---|---|
| ALPHA | 999000117 | 21 Jun 04:58 UTC | 21 Jun 09:12 UTC | 4 h 14 m |
| BRAVO | 999000342 | 21 Jun 05:11 UTC | 21 Jun 08:47 UTC | 3 h 36 m |

The two gaps **overlap by more than three hours** and enclose the acquisition
instant. Both vessels reappeared on courses consistent with having remained in
the area throughout, and their reported draughts after the gap differ from those
before it in opposite directions.

## 3. Assessment

The pattern — two overlapping gaps, co-located returns at close separation on
parallel headings, opposed draught changes — is consistent with an undeclared
ship-to-ship transfer. It is also consistent with two vessels independently
losing AIS reception at the outer edge of terrestrial coverage while happening to
pass close aboard.

The discriminator is the **draught change**, which no coverage explanation
accounts for. On that basis the event is assessed as **possible**, not probable.

Note that ship-to-ship transfers are not unlawful as such. Under EU measures
introduced by Council Regulation (EU) 2023/1214, what attaches is a
**notification requirement** — advance notice to the competent authority of a
transfer occurring within a Member State's exclusive economic zone — and
separately the treatment of AIS interruption as conduct in its own right. The
reportable proposition here is the absence of notification, not the transfer.

## 4. Recommendation

Refer to the maritime authority for a check against port call and notification
records. This cell holds no notification data and cannot close the question.

## 5. Reliability

Source reliability **C**, information credibility **3**. Single acquisition, no
corroborating sensor, identifiers synthetic.
