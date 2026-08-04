---
doc_id: intsum-2026-030-sar-ais-correlation
title: SAR–AIS Correlation Methodology
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intsum
language: en
date: 2026-03-20
aoi: [lisbon, kattegat]
serial: INTSUM 2026/030
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTSUM 2026/030 — SAR–AIS Correlation Methodology

## 1. Sequence

Correlation always runs in the same order, and every step leaves a record:

1. **Scene selection.** Catalogue search by area of interest and time window.
   Only GRD products in IW mode with dual polarisation are retained.
2. **Detection.** Run over the **VH** polarisation. Sea backscatter is lower in
   VH than in VV, so ship-to-background contrast is higher and the detection
   threshold is more stable.
3. **Length filter.** Candidates below the configured minimum length (15 m by
   default) are discarded, to reduce false positives of oceanographic origin.
4. **AIS preparation.** Clip to the scene envelope and the acquisition window,
   then remove duplicates.
5. **Pairing.** Each detection is compared against AIS positions interpolated to
   the exact acquisition instant.
6. **Classification.** `matched` or `dark`, always with the distance and time
   offset recorded.

## 2. Deduplication — mandatory, and before pairing

Terrestrial AIS networks rebroadcast the same message from several stations. In
a typical acquisition window the fraction of rows that are exact copies is high
— on the order of seventy per cent — and more than twenty identical copies of a
single message have been observed.

The deduplication key is **(MMSI, timestamp, latitude, longitude)**. If it is
not applied before pairing, any "nearest in time" logic is skewed by the number
of rebroadcasts, which has nothing to do with the vessel.

## 3. Interpolation rather than nearest neighbour

After deduplication a typical vessel presents several dozen distinct instants
across a window of a few minutes. That is enough to **interpolate the track to
the exact acquisition instant** rather than accepting the raw position nearest
in time. The difference is material: at 12 knots a two-minute error is about
740 m — more than the usual pairing radius, which is to say enough to turn a
declared vessel into a false dark detection.

## 4. Asymmetric tolerance

See INTREP 2026/041. Azimuth displacement shifts moving targets along the flight
direction, not radially. A symmetric radius is the most common way to
manufacture dark detections out of perfectly ordinary traffic.

## 5. Recording

Every pairing carries the AIS source used and an indicator of whether that
source is ground truth in this area. Without that indicator, a dark-vessel rate
is not quotable.

Source reliability **B**, information credibility **2**.
