---
doc_id: intsum-2026-021-dark-vessel-doctrine
title: Doctrinal Note — Dark Vessel and Undeclared Vessel
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intsum
language: en
date: 2026-03-14
aoi: [lisbon]
serial: INTSUM 2026/021
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTSUM 2026/021 — Doctrinal Note: Dark Vessel

## 1. Definition

A **dark vessel** is a vessel **detected by a sensor** — here, an amplitude
detection in a Sentinel-1 synthetic aperture radar (SAR) image — **for which no
corresponding AIS message can be found** in the reference source used, within a
declared distance and time tolerance around the detection's position and the
image acquisition instant.

The preferred form in drafting is **undeclared vessel**, which is less
ambiguous.

## 2. What the term does NOT mean

The term is frequently misread by anyone meeting it for the first time. To
remove doubt:

- **It has nothing to do with the colour, paint or visual appearance of the
  hull.** A vessel painted white is exactly as likely to be dark as one painted
  grey. Reading "dark" as a description of colour is simply wrong.
- It does not mean the vessel is running without lights, nor that it was
  observed at night. SAR is an active sensor and images equally by day and by
  night; the hour of the pass is irrelevant to the term.
- It does not, on its own, mean that any unlawful activity is taking place.

"Dark" is a statement about **the absence of an expected emission, in a named
source, at a named moment** — and about nothing else.

## 3. Mandatory elements

A classification of "dark" is meaningful only if all three elements are stated
together:

1. **The detection** — sensor, scene identifier, acquisition instant, position,
   estimated length and detector confidence.
2. **The AIS reference source** — which one was consulted, what its coverage is,
   and whether it is treated as ground truth in this area of interest.
3. **The tolerance** — search radius in metres and time window in minutes around
   the acquisition instant.

## 4. Standing caveat

**A dark detection is a lead, not a conclusion.** The system surfaces
candidates; the analyst adjudicates. Innocent explanations are common and are
enumerated in INTSUM 2026/055.

## 5. Reliability

Source reliability **B**, information credibility **2**. Doctrinal note agreed
within the cell. The definition sits alongside the carriage obligation
established in IMO Resolution A.1106(29) and, in European Union waters, in
Article 6 of Directive 2002/59/EC.
