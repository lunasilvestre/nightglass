---
doc_id: intrep-2026-052-tagus-approaches-baseline
title: Traffic Baseline — Tagus Estuary Approaches
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intrep
language: en
date: 2026-05-18
aoi: [lisbon]
serial: INTREP 2026/052
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. The area
described is real; the traffic figures and observations in it are invented.*

# INTREP 2026/052 — Traffic Baseline: Tagus Estuary Approaches

## 1. Area

Area of interest **LISBON**, bounded approximately by 10.5°W–8.5°W and
38.0°N–39.5°N. It contains the Tagus (Tejo) estuary and the port of Lisbon, the
Cascais and Trafaria anchorages, the Setúbal approaches to the south, and a
substantial band of open Atlantic to the west.

The box is deliberately larger than the port itself. Approach and departure
behaviour is where the analytically interesting conduct occurs; a box drawn
tight around the berths would see only vessels that had already decided to
declare themselves.

## 2. Sentinel-1 coverage

Coverage over this box is good and is not a constraint on the work. Two-week
sampling returns tens of GRD High Resolution Dual-Polarisation acquisitions, and
a full month returns of the order of eighty, all of them VV+VH. VH is therefore
available on every scene, which matters because detection is run on VH.

Overpass windows for this AOI, in UTC:

| Pass | Window (UTC) |
|---|---|
| Descending | 06:33–06:51 |
| Ascending | 18:26–18:43 |

AIS must be sliced against these windows in UTC directly. The descending pass
falls in local morning and the ascending in local evening; both sit inside
periods of active commercial movement, so neither is a quiet-water sample.

## 3. Traffic character

Four populations share the box and behave differently enough that they should
never be pooled in a single expectation:

- **Deep-sea commercial traffic** — container, bulk and tanker movements to and
  from Lisbon and Setúbal, plus north–south transits that pass without calling.
  Almost all Class A, almost all continuously declared.
- **Short-sea and coastal traffic** — reliably declared, but frequently at the
  edge of terrestrial receiver coverage on the western side of the box.
- **Fishing** — highly variable. A large fraction is below the AIS carriage
  threshold entirely. This population is the dominant source of small unmatched
  detections and is the reason the 15 m minimum length filter exists.
- **Recreational** — seasonal, concentrated near the estuary mouth and Cascais,
  overwhelmingly Class B or nothing at all. Peaks sharply in summer.

## 4. Expectation for unmatched detections

Open literature on well-instrumented European waters reports on the order of
**5% of SAR detections unmatched**, of which a small fraction — well under one
percent of the total — survives review as genuinely unexplained.

**This cell does not quote an unmatched rate for the LISBON AOI**, because no
complete historical point-level AIS reference exists for Iberian waters. What
can be stated for this AOI is the mechanism and the individual candidates, with
the reasoning shown. Rates are quoted only over the Danish validation AOI. See
INTREP 2026/018.

If a run over this box reports tens of percent dark, the finding is about the
AIS source or the matching tolerance, not about the water.

## 5. Reliability

Source reliability **C**, information credibility **3**. Baseline compiled from
routine observation; figures indicative.
