# Limitations

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

Stated before being asked, because each one was tested rather than assumed.

## AIS coverage, and what a rate may be quoted from

- **No free historical, complete point-level AIS exists for Iberian waters.** GFW's ORBCOMM
  sublicence forbids redistributing AIS or any derivative, DGRM publishes nothing, EMSA
  restricts SafeSeaNet to national administrations, and the free satellite-AIS tiers went away
  in the 2025 Kpler/S&P consolidation. So over Portugal, GFW detections are a **reference
  layer** — an independent published product to cross-check against — not an independent
  correlation this system performed.
- **The live aisstream feed is thinned, measured, not guessed.** Same Kattegat bbox, same
  22-minute clock window, against DMA as ground truth: **770 vessels vs 132** — aisstream sees
  about **17%**, missing five in six. Independently, message throughput runs ~1.4 messages per
  vessel per 4 minutes against ~40 expected for a fully-observed class A vessel, roughly **3% of
  expected volume**. Vessel discovery had saturated by minute 10, so recording for longer does
  not close the gap. Absence from a thinned feed is not absence of transmission, so this source
  can demonstrate the matching mechanism but **cannot support a dark-vessel rate**.
- **Therefore: matched pairs over Portugal, rates only over Denmark.** The schema enforces this
  rather than leaving it to prose — `Match.source_is_ground_truth` travels with every match and
  `CorrelationResult.rate_is_quotable` is false unless all of them came from a ground-truth
  feed.
- **Completeness over Portugal cannot be measured at all.** The absence of a DMA equivalent is
  simultaneously why a live feed is needed there and why there is no reference to validate it
  against. Denmark is the only AOI where this is observable, which is much of what the Danish
  AOI is for.
- **`make fetch-ais` will eventually stop working, and the code cannot fix it.** The DMA S3
  bucket serves daily files on a rolling window of roughly eighteen months. `aisdk-2026-07-17`
  is inside it today; when it ages out, the Danish validation becomes unreproducible from this
  manifest and the archive would have to be re-pointed at a monthly file. The granules do not
  have this problem — the Sentinel-1 archive at ASF is permanent. Stated because a manifest
  invites the assumption that everything in it is permanently retrievable, and one half is not.
- **Only Class A and Class B AIS is treated as a vessel.** The DMA feed also carries base
  stations and aids to navigation — 7,857 rows in this acquisition window, 5.7% of it. They are
  excluded, because matching a detection to a navigation buoy would report a fixed installation
  as a vessel that had declared itself. The consequence is the honest one and it runs the other
  way: a detection sitting on a lit beacon is reported **unmatched**, and is part of the 40%.
  This rule was originally missing from the code — the hand-cut CSV used through early
  development had it baked in and nothing said so, which is much of the argument for the fetchers.

## The detector

- Single scene per AOI. No CFAR tuning. No accuracy claims beyond what was measured.
- **40% of detections unmatched is not a 40% dark-vessel rate.** Published work on Danish waters
  finds ~5% unmatched and ~0.4% genuinely dark after review. The excess concentrates near shore —
  the shoreline-buffer sweep ([detection.md](detection.md)) removes 17 unmatched detections
  against 2 matched between 300 m and 3 km — and it does not fall below 23% even three kilometres
  offshore. The honest reading is that the *matcher* is validated (21 matches, median 104 m,
  every AIS vessel over 200 m in the footprint recovered) and the *detector's* precision is not
  good enough to quote a rate from. This figure was 25% until duplicate detections of the same
  hull were merged; the correction moved it the wrong way, which is why it is stated rather than
  smoothed.
- **Detected length is a size band, not a measurement.** Median ratio against AIS-reported length
  is 1.05×, but per-vessel correlation is only **r = 0.271**. It is used as a filter and
  reported as an attribute; it is not evidence about a specific vessel.
- **Heading is reported only when the blob is genuinely elongated**, and is ambiguous by 180°
  even then — a SAR blob has no bow. The first version returned a heading for every detection
  and the values clustered on two figures 90° apart, which was a picture of the pixel grid
  rather than of a fleet.
- **The land mask is a coastline plus a buffer, not a hydrographic product.** Vessels genuinely
  within 1 km of shore are not reported at all. Harbour traffic is outside what this sees.
- **Azimuth displacement is corrected; range migration and wake effects are not.** The
  correction uses one scene-mean platform speed and per-detection slant range and incidence; it
  does not model the vessel's own acceleration or a squinted geometry.

## The document layer

- **A third of the document corpus is synthetic, and it is the third that defines the terms.**
  The doctrine, the AOI baselines and the reporting conventions are memos written for this
  project; the regulatory grounding underneath them is real. Answers built on the synthetic
  material are marked `UNCLASSIFIED // SYNTHETIC` automatically, so the distinction is visible
  in the output rather than buried here — but it is a real limitation, not just a marking.
- **Retrieval quality is demonstrated, not measured.** There is no labelled question set and no
  recall@k number, because building an honest one over a corpus I wrote half of would mostly
  measure my own phrasing. What is verified is that every citation resolves to a real chunk and
  that unsupported questions refuse; what is shown is a handful of transcripts.
- **The refusal path catches unsupported claims, not wrong ones.** A claim that cites a real
  chunk which does not actually say what the claim says would survive verification. Guarding
  that needs an entailment check against the cited span, which is on the
  [three-weeks list](roadmap.md).

## The bundle, and the second boundary

- **The bundle's wheelhouse does not make the image rebuildable offline.** Describing the bundler
  as "`docker save` + pip wheelhouse + model blobs" invites the reading that the three
  together reconstitute the system from source. They do not. The runtime image also needs
  `curl`, `ca-certificates` and `libexpat1`, plus `poppler-utils` in the fetcher stage, and a
  wheelhouse pins none of them — an offline `docker build` would still go looking for a Debian
  mirror. What makes a restored site *run* is the saved images. What the 172 MB of wheels buys
  is smaller and real: `pip install --no-index --find-links`, to patch a dependency inside a
  running container without a network. Stated because that gap is otherwise found the hard way.
- **`nightglass-bundle verify` proves integrity, not authenticity.** It proves the bundle you
  have is the bundle that was built, given one manifest digest you obtained by another route. It
  says nothing about who built it, and there is no signature anywhere in the format.
- **A genuinely cold `make pull-models` is still untested** — a long-standing gap unchanged by
  any of this. The bundle routes around it — a site restoring from one never runs the pull path — so
  the gap is now less likely to be hit and exactly as real as it was.
- **The Kubernetes boundary is weaker than the compose one, in one nameable way.** A pod under the
  chart's default-deny egress policy still *resolves* an external name — CoreDNS sits in
  `kube-system`, outside the policy, and forwards upstream — and is then refused the connection.
  Compose fails earlier and harder: the name never resolves and no packet is ever sent. Restricting
  CoreDNS's own egress was tried and broke in-cluster service resolution, so it is not shipped. On a
  real site with no reachable upstream resolver the difference disappears by itself, which is true
  and is not the same as having demonstrated it.
- **`make k8s-proof` does not exercise ollama or the 6.2 GB of granules and corpus.** Both are in
  the chart; neither changes whether an egress rule is enforced, and moving 13 GB of weights to
  make a point about a firewall rule is how a proof becomes something nobody runs. The chart gives
  `/app/data` an `emptyDir`, so a cluster deployment starts and the tools that read pixels have
  nothing to read until a volume is populated from the transfer bundle.

## And the one that frames all of it

- **Dark ≠ guilty.** A vessel with no AIS correspondence has plenty of innocent explanations —
  satellite revisit gaps, terrestrial receiver limits, transponder failure, low-power class B
  sets, vessels never required to carry AIS at all. The system surfaces candidates. The analyst
  adjudicates.

---

**Related work.** Magalhães, Falcão & Barbosa (2025), IST Lisbon — Sentinel-2 optical vessel
detection with YOLO. NIGHTGLASS is the SAR complement to active regional optical work, and the
contrast is the point: optical fails at night and under cloud, which is the entire reason SAR
exists for this mission.

---

**See also:** [what I'd do with three more weeks](roadmap.md) · [design decisions](design-decisions.md)
· [the detector](detection.md) · [data sources and licences](data-sources.md)
