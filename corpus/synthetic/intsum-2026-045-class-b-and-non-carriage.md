---
doc_id: intsum-2026-045-class-b-and-non-carriage
title: Class B Transponders and Non-Carriage — The Largest Source of Unmatched Detections
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intsum
language: en
date: 2026-05-04
aoi: [lisbon, kattegat]
serial: INTSUM 2026/045
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTSUM 2026/045 — Class B and Non-Carriage

## 1. Carriage obligation

The AIS carriage requirement under SOLAS chapter V applies to ships of 300 gross
tonnage and upwards on international voyages, cargo ships of 500 gross tonnage
and upwards not on international voyages, and passenger ships irrespective of
size.

Everything else is outside it. In practice that means a large share of the
coastal fishing fleet and effectively all recreational craft have **no
obligation to transmit anything**. A detection of such a vessel with no AIS
correspondence is fully explained by the regulation and is not a finding.

## 2. Class A versus Class B

Where a small vessel does carry AIS voluntarily, it is usually Class B.

| | Class A | Class B |
|---|---|---|
| Fitted because | Mandatory under SOLAS V | Voluntary |
| Transmit power | Higher | Lower |
| Reporting rate under way | Every few seconds | Tens of seconds |
| Slot access | Priority | Yields to Class A |
| Practical reception range | Long | Noticeably shorter |
| Behaviour in dense traffic | Robust | Degrades first |

Two consequences follow. Class B units are **more likely to be missed at range**,
and they are **more likely to be missed exactly where traffic is dense**, because
they yield transmission slots to Class A. Both effects push in the direction of
false darks, and both are strongest inshore and in summer.

## 3. Interaction with the minimum length filter

The standard 15 m minimum length filter exists partly for this reason: it
removes most of the population that is under no obligation to transmit, before
that population reaches the matcher and inflates the unmatched count.

Lowering the threshold to catch smaller vessels of interest is a legitimate
choice, but it must be made knowingly — the unmatched rate will rise sharply and
almost all of the rise will be lawful non-carriage.

## 4. Analyst rule

Before escalating any unmatched detection, check the estimated length first. A
detection below roughly 25 m in coastal water, in summer, near a recreational
concentration is the single least interesting product this system generates,
regardless of how confidently the detector reports it.

## 5. Reliability

Source reliability **A**, information credibility **2**. Carriage thresholds are
regulatory fact; the reception characterisation is operational experience.
