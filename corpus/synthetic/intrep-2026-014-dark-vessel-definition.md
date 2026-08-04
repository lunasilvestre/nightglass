---
doc_id: intrep-2026-014-dark-vessel-definition
title: Operational Definition — Dark Vessel
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-03-11
aoi: [lisbon, kattegat]
serial: INTREP 2026/014
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTREP 2026/014 — Operational Definition: Dark Vessel

## 1. Definition

A **dark vessel** is a vessel **detected by a sensor** — in this cell, a
Sentinel-1 synthetic aperture radar amplitude detection — for which **no
corresponding Automatic Identification System (AIS) report can be found in the
reference feed**, within a stated distance and time tolerance of the detection's
position and the image acquisition instant.

The term of art used in this cell is **dark vessel**, and the longer form
**undeclared vessel** is preferred in
drafting because it is harder to misread.

## 2. What the term does NOT mean

The term is routinely misunderstood on first contact. For the avoidance of doubt:

- It has **nothing to do with the colour, paint scheme or visual appearance** of
  the hull. A white vessel and a grey vessel are equally likely to be dark.
- It does not mean the vessel is unlit, is operating at night, or was imaged at
  night. SAR is an active sensor and images by day and night identically; the
  acquisition time is irrelevant to the term.
- It does not mean the vessel is unidentified in the general sense. A vessel may
  be perfectly well known and still be dark for a given acquisition.
- It does not, on its own, mean the vessel is engaged in wrongdoing.

"Dark" is a statement about **the absence of an expected transmission in a
specific feed at a specific moment**, and about nothing else.

## 3. The three elements

A dark classification is only meaningful when all three are stated together:

1. **The detection** — sensor, scene identifier, acquisition time, position,
   estimated length, detector confidence.
2. **The reference feed** — which AIS source was searched, its coverage, and
   whether it is treated as ground truth in this AOI.
3. **The tolerance** — the search radius in metres and the time window in
   minutes around the acquisition instant.

A report that says "three dark vessels" without those three elements is not
gradeable and must not be released. See INTSUM 2026/060 on Admiralty grading.

## 4. Standing caveat

**A dark detection is a lead, not a conclusion.** The analyst adjudicates; the
system only surfaces candidates. Innocent explanations are common and are
enumerated in INTSUM 2026/055. Reporting that omits the caveat overstates the
product and will be returned to the drafter.

## 5. Reliability

Source reliability **B**, information credibility **2**. Doctrine note, agreed
across the cell; the definition tracks the obligation to transmit set out in IMO
Resolution A.1106(29) and, inside EU waters, Article 6 of Directive 2002/59/EC.
