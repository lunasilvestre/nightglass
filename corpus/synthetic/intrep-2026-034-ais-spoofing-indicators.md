---
doc_id: intrep-2026-034-ais-spoofing-indicators
title: Identity Manipulation in AIS — Indicators
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-04-16
aoi: [lisbon, kattegat]
serial: INTREP 2026/034
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. Any identifiers
shown use MMSI maritime identification digits **999**, which is not allocated to
any administration.*

# INTREP 2026/034 — Identity Manipulation in AIS

## 1. Why this sits next to dark-vessel work

Going dark and lying are alternative solutions to the same problem. A vessel
that wants its position unrecorded can stop transmitting — which creates a
conspicuous hole — or keep transmitting something false, which does not. The
second is harder to detect from AIS alone and much easier to detect when an
independent sensor is available.

AIS carries no authentication of any kind. Every field is self-declared and
none is signed. This is a design property, not a defect: AIS was specified as a
collision-avoidance aid between cooperating ships, not as a surveillance
system, and it is used as one only because nothing better is universally fitted.

## 2. Indicator classes

**Static-field inconsistency.** Declared length, beam or ship type inconsistent
with the SAR-estimated hull length at the matched position. A 300 m return
declaring itself a 40 m vessel is the cleanest single indicator this cell can
produce, because it requires only one acquisition.

**Implausible kinematics.** Position jumps that exceed any achievable speed;
speed over ground inconsistent with successive positions; a course that crosses
land.

**Duplicate identity.** The same MMSI reported from two locations too far apart
to be one vessel. One of them is not that vessel.

**Identity churn.** Frequent changes of declared name or call sign against a
constant MMSI, or the reverse.

**Position offset with plausible motion.** The most difficult case: a track that
is internally consistent but displaced wholesale from the true position. AIS
alone cannot see this at all. It shows up only as a systematic mismatch against
an independent sensor.

## 3. What SAR contributes

SAR is an independent, non-cooperative measurement of presence, position and
approximate size. It cannot read an identity, and it should never be described
as if it could. What it can do is **contradict** a declared one — and a
contradiction between a self-declared field and a physical measurement is a
much stronger product than either alone.

## 4. Reporting discipline

An identity-manipulation assessment must state which specific field was
contradicted and by what measurement. "Suspicious AIS behaviour" is not
reportable. "Declared length 40 m; SAR-estimated hull length 268 m at 310 m from
the declared position, same acquisition" is.

## 5. Reliability

Source reliability **B**, information credibility **2**.
