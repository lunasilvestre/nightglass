---
doc_id: intrep-2026-041-azimuth-displacement-matching
title: Azimuth Displacement and the Matching Tolerance
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-04-27
aoi: [lisbon, kattegat]
serial: INTREP 2026/041
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTREP 2026/041 — Azimuth Displacement and the Matching Tolerance

## 1. The effect

A synthetic aperture radar forms an image by exploiting the Doppler history of a
target across the synthetic aperture. The processor assumes the target is
stationary. A target that is moving carries an extra Doppler component, and the
processor resolves that component as **position**: the target is placed away from
its true location, **along the satellite's flight direction (azimuth)**.

The displacement depends on the **radial** (line-of-sight) component of the
target's velocity, the platform altitude and the platform velocity. For a
spaceborne X- or C-band SAR in low Earth orbit, the scaling is on the order of a
few hundred metres of azimuth shift per metre per second of radial velocity.

A vessel making 12 knots on a course with a substantial radial component can
therefore appear **several hundred metres to well over a kilometre** away from
where AIS says it was — displaced along-track, not along the line of sight.

## 2. Why this matters for dark-vessel work

The displacement is a **systematic error in one axis**. A symmetric circular
match radius handles it badly:

- Make the radius small enough to be discriminating, and fast movers on
  favourable headings fall outside it. They are then reported dark. They are not
  dark; they are displaced.
- Make the radius large enough to swallow the worst-case displacement, and in
  dense traffic it swallows neighbouring vessels too, producing confident but
  wrong pairings.

**Symmetric tolerance is the single most common way to manufacture false dark
detections from ordinary traffic.**

## 3. Two workable treatments

**Anisotropic tolerance.** Allow a wide tolerance along the scene's azimuth
direction and a tight one across it. Simple, needs only the scene geometry, and
costs nothing in dense traffic because the widened axis is the one traffic is
least likely to be packed along.

**Velocity correction.** Take speed over ground and course over ground from the
AIS report, resolve the radial component against the scene's look direction, and
predict where the SAR processor would have placed that vessel. Then match
against the predicted position with a tight symmetric radius. Better, because it
turns the displacement from noise into a modelled quantity — and a candidate
that still fails to match after correction is a materially stronger lead.

## 4. Reporting consequence

Any dark determination must state which treatment was applied. "No AIS within
500 m" is not a finding unless it is also stated whether those 500 m were
measured against a raw or a displacement-corrected position.

Source reliability **B**, information credibility **2**. Consistent with the
imaging geometry described in the ICEYE SAR foundations material held by this
cell.
