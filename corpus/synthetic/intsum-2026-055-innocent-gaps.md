---
doc_id: intsum-2026-055-innocent-gaps
title: Innocent Explanations for the Absence of AIS
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intsum
language: en
date: 2026-04-08
aoi: [lisbon, kattegat]
serial: INTSUM 2026/055
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTSUM 2026/055 — Innocent Explanations for the Absence of AIS

This note exists to be cited in every report that presents dark detections.
**A dark detection is a lead, not a conclusion.** The explanations below are
common and, in most cases, more likely than intent.

## 1. The vessel is not required to carry AIS

AIS is mandatory for ships of 300 gross tonnage and above on international
voyages, cargo ships of 500 gross tonnage and above on domestic voyages, and
passenger ships regardless of size. Below those thresholds there is no
obligation. Much of the coastal fishing fleet and effectively all recreational
craft fall outside it.

An 18 m SAR detection offshore is, on the face of it, more likely to be a
fishing vessel with no AIS obligation than a case of interest.

## 2. Class B transponders

Class B equipment transmits at lower power and lower rate than Class A. It is
received far less reliably at distance, particularly in dense traffic where
competition for transmission slots is greater.

## 3. Equipment failure

Transponders, antennas and cabling fail. It is an ordinary event. It typically
shows as an abrupt end of transmission, with no positional anomaly before or
after.

## 4. Receiver coverage limits

Coastal stations have optical range, on the order of 40 nautical miles. Beyond
that the only coverage is satellite, which has revisit gaps. Absence from a
terrestrial source offshore says more about the station than about the vessel.

## 5. Incomplete data source

Not all AIS sources are equivalent. An aggregated or free source may carry only
a sample of the messages actually received. Using a thinned source as ground
truth produces absurdly high dark-vessel rates that reflect the source rather
than the water.

## 6. Time offset

SAR gives an instant. If the last available AIS position predates the pairing
window, the vessel is absent from that window without ever having stopped
transmitting.

## 7. Lawful shutdown at the master's discretion

IMO Resolution A.1106(29) allows AIS to be switched off where its continued
operation would compromise the safety of the ship — the standard example is
transit through waters at risk of piracy. The decision should be recorded in the
ship's log and reported to the competent authority.

## 8. Consequence for drafting

A report presenting dark detections without stating these hypotheses is
incomplete. The accepted formulation in this cell is *"may be undeclared"* and
not *"is undeclared"*.

Source reliability **B**, information credibility **1**.
