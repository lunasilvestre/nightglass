---
doc_id: intsum-2026-068-intrep-format-and-release
title: INTREP Format and Release — Markings and the Review Gate
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intsum
language: en
date: 2026-07-02
aoi: [lisbon, kattegat]
serial: INTSUM 2026/068
licence: Synthetic document written for this project. Freely reusable.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Synthetic document. Written to exercise a retrieval pipeline. It describes no
real vessel, operator or event.*

# INTSUM 2026/068 — INTREP Format and Release

## 1. Minimum structure

An INTREP produced in this cell contains, in this order:

1. **Classification marking**, on the first line and the last.
2. **Serial and date-time** of production, in UTC.
3. **Area of interest** and the time window the report covers.
4. **Summary** — no more than three sentences.
5. **Observations**, each with its references.
6. **Assessment**, with reliability and credibility gradings.
7. **Caveats**, including without exception the standing caveat on dark
   detections.
8. **Sources**, enumerated.

## 2. Mandatory references per claim

Every factual claim carries the references supporting it: scene identifier,
detection identifiers, identifiers of the document extracts cited, and the
applicable timestamps.

A claim with no references **is not published with a caveat — it is removed**.
The distinction matters: a weakly supported claim can be graded as such; a claim
with no provenance cannot be graded at all.

## 3. Marking propagation

The report's classification marking is determined by the **most restrictive
marking** of any source cited. The marking is not chosen by the drafter and
cannot be lowered by rewording.

Concretely: if a cited extract carries the marking `UNCLASSIFIED // SYNTHETIC`,
that marking travels with the product and must remain visible on it. A report
that cites synthetic material and presents itself as plain `UNCLASSIFIED` is
incorrectly marked, even if everything in it is true.

## 4. Human review gate

Every generated product is born carrying the marking
**`DRAFT — NOT RELEASABLE`**. The marking is removed only by an explicit,
recorded human decision, and only after the draft has been read in full.

The gate is not an administrative formality. It is the point at which someone
verifies that the references exist, that the caveats are present and that the
propagated marking is correct — three things an automatic generator can omit
perfectly fluently.

## 5. Language

Prefer **"may be undeclared"** to "is undeclared". Prefer **"consistent with"**
to "demonstrates". Avoid quantifiers without a denominator ("several",
"numerous") — state the number and the total.

## 6. Reliability

Source reliability **B**, information credibility **2**.
