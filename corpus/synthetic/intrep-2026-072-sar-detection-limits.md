---
doc_id: intrep-2026-072-sar-detection-limits
title: What the SAR Detector Can and Cannot Establish
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-07-13
aoi: [lisbon, kattegat]
serial: INTREP 2026/072
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTREP 2026/072 — SAR Detection Limits

## 1. Why SAR at all

Synthetic aperture radar is an **active** sensor: it supplies its own
illumination at microwave wavelengths. Two properties follow, and they are the
entire reason this cell uses SAR rather than optical imagery.

- **It images at night as well as by day.** Nothing about the product depends on
  solar illumination.
- **It sees through cloud.** At the wavelengths used, cloud, haze and most
  precipitation are effectively transparent.

Optical vessel detection is real, useful, and complementary — but it fails
exactly when maritime surveillance most needs an answer: at night, and under the
Atlantic cloud that covers the Portuguese coast for much of the year.

## 2. What a detection is

A vessel appears in a SAR amplitude image as a bright return against a darker
sea background, because a metal hull and its superstructure scatter far more
energy back toward the sensor than a rough water surface does.

Detection is run on the **VH** (cross-polarised) channel. Sea surface
backscatter is lower in VH than in VV, so the contrast between hull and
background is greater and the detection threshold is more stable across the
scene.

The product is a position, an approximate hull length derived from the extent of
the return, an approximate heading, and a detector confidence. That is all.

## 3. What it cannot establish

- **Identity.** SAR returns carry no name, no MMSI, no flag. A detection can be
  matched to a declared identity or contradict one; it cannot supply one.
- **Cargo, intent or activity.** Nothing about what a vessel is doing beyond its
  position, size and heading.
- **Crew, ownership or operator.**
- **Precise length.** The estimate is derived from a return that depends on
  aspect angle, sea state and superstructure. Treat it as a class indicator, not
  a measurement.

## 4. Conditions that degrade detection

**Sea state** is the dominant term. Rising wind roughens the surface and raises
background backscatter; the effective detection threshold rises with it and the
smallest targets are lost first. A scene taken in heavy weather under-reports
small vessels, and a drop in detection count across a storm is a statement about
the storm rather than about traffic.

**Very small targets** near the resolution limit are unreliable regardless of
conditions, which is the second reason for the minimum length filter.

**Land and near-shore clutter.** Returns from structures, breakwaters and moored
vessels alongside are not sea returns and must be masked rather than detected.

**Ambiguities and artefacts.** Strong scatterers can produce replicas displaced
in azimuth. These look like additional vessels and are not.

## 5. Reporting consequence

A dark detection report states the acquisition conditions, not only the count.
Without sea state, a count has no denominator.

## 6. Reliability

Source reliability **B**, information credibility **2**. Consistent with the SAR
imaging and radiometric material held by this cell.
