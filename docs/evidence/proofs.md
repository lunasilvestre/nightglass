# The seven proofs, as run

*Part of [NIGHTGLASS](../../README.md). The dated, unedited tail of each `make *-proof` run, on the host described below. CI runs one of the seven on every push, `bundle-proof`; `k8s-proof` could run there too but is not worth 2.33 GB of images per push, and the other five need the card, the granule and the licensed AIS day. [testing.md](../testing.md) says which is which. Nothing here is edited: ANSI colour codes are stripped and each block is the last lines of the log.*

| | |
|---|---|
| date | 2026-09-09 |
| commit | `b961d2e` |
| host | Linux 6.12.107+deb13-amd64 x86_64 |
| card | NVIDIA GeForce RTX 3090, 24576 MiB |
| docker | 29.8.0 |

## `make air-gap-proof`

exit 0, 2026-09-09T14:26:11+01:00, wall 0m6.457s

```text
NIGHTGLASS — air-gap proof   2026-09-09T13:26:05Z

----------------------------------------------------------------------
$ docker compose exec api curl -m 5 https://example.com
  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
                                 Dload  Upload   Total   Spent    Left  Speed

  0     0    0     0    0     0      0      0 --:--:-- --:--:-- --:--:--     0curl: (6) Could not resolve host: example.com

✓ blocked (curl exit 6)

----------------------------------------------------------------------
$ docker compose exec api curl -m 5 http://ollama:11434/api/tags   # inside the enclave
models: bge-m3:latest, qwen2.5:14b-instruct-q4_K_M

----------------------------------------------------------------------
$ chat completion against the local model
Synthetic Aperture Radar (SAR) is a remote sensing technology that creates high-resolution images by electronically synthesizing a large antenna aperture, enabling it to operate effectively both day and night regardless of weather conditions because it uses the emitted microwave radiation to penetrate clouds and darkness.

----------------------------------------------------------------------
No route to the internet. Inference ran anyway.

```

## `make rag-proof`

exit 0, 2026-09-09T14:26:36+01:00, wall 0m24.722s

```text
----------------------------------------
Q: what is a dark vessel?

1. A dark vessel is a vessel detected by a sensor for which no corresponding Automatic Identification System (AIS) report can be found in the reference feed within a stated distance and time tolerance of the detection's position and the image acquisition instant.
   [intrep-2026-014-dark-vessel-definition#0000] [intsum-2026-021-dark-vessel-doctrine#0000]

sources
----------------------------------------
[intrep-2026-014-dark-vessel-definition#0000]  (UNCLASSIFIED // SYNTHETIC)  Operational Definition — Dark Vessel
[intsum-2026-021-dark-vessel-doctrine#0000]  (UNCLASSIFIED // SYNTHETIC)  Doctrinal Note — Dark Vessel and Undeclared Vessel

----------------------------------------------------------------------
PART 3 — REFUSAL. A question the corpus does not cover.

----------------------------------------------------------------------
$ nightglass-corpus ask How many dark vessels were detected off Madeira in 2019?

GROUNDED — UNCLASSIFIED
----------------------------------------
Q: How many dark vessels were detected off Madeira in 2019?

Not supported by available sources.

8 chunk(s) were retrieved but none supported an answer.

----------------------------------------------------------------------
Part 1 is why this milestone exists. The model has no prior for the operational
meaning of "dark vessel": across samples it either declares the term to have no
established maritime meaning or offers hull colour as a guess. Neither is the
answer, and neither is hedged in a way an analyst would notice.

Part 2 is the same model, same question, answered from retrieved sources — with
the chunk ids to check it against, and marked UNCLASSIFIED // SYNTHETIC because
the sources it actually cited carry that caveat. Nothing propagated that marking
by hand.

Part 3 is the part worth more than either: the system declining to answer. A
dark detection is a lead, not a conclusion, and a system that admits what it
cannot source is the only kind whose output can be graded at all.

```

## `make dark-proof`

exit 0, 2026-09-09T14:30:44+01:00, wall 4m8.303s

```text
5. One SQL query, detections with no AIS correspondence
───────────────────────────────────────────────────────

§M3 — detections with no AIS correspondence  [S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13]
------------------------------------------------------------------------------------------------------------------
  detections                   35
  matched                      21
  dark                         14
  unmatched_fraction           40.0%
  match radius (m)             500.0
  time window (min)            ±11
  azimuth correction           applied
  azimuth correction helped    14/21
  rate is quotable             True

  detection                    lat       lon   len m  conf  nearest AIS
  det_00023               56.75648  12.17514      60  1.00  230042000  27 m  Δt +2 s
  det_00017               56.85529  12.38936      80  0.71  319278400  32 m  Δt +33 s
  det_00028               56.69396  11.80493     290  0.96  219115000  40 m  Δt +0 s
  det_00010               57.09602  12.18908      70  0.67  265570730  41 m  Δt +4 s
  det_00002               57.31709  11.78736     120  0.93  265072000  52 m  Δt +3 s
  det_00029               56.68024  11.81558      98  0.68  255806513  52 m  Δt +0 s
  det_00001               57.34645  11.71024     116  0.66  244790608  52 m  Δt +4 s
  det_00006               57.25637  11.61343     141  0.64  219355000  64 m  Δt +4 s
  det_00032               56.61351  12.29389      63  0.67  265048530  66 m  Δt +4 s
  det_00014               56.98950  11.77106     130  0.53  273611590  76 m  Δt +5 s
  det_00024               56.73353  12.26465     102  0.73  212198000  104 m  Δt +5 s
  det_00031               56.64737  11.96148     270  0.81  305649000  123 m  Δt +2 s
  det_00033               56.63360  11.95615     344  0.98  246521000  150 m  Δt +3 s
  det_00019               56.84194  12.08966     120  0.67  245266000  155 m  Δt +2 s
  det_00020               56.81119  11.86513      70  0.38  636024515  185 m  Δt +1 s

6. The correction that makes the match work, measured not asserted
──────────────────────────────────────────────────────────────────

azimuth displacement — measured, not argued
--------------------------------------------
scene              S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13
detections         35
AIS vessels        845 in AOI, 123 inside the scene footprint

predicted azimuth displacement   median      1 m   p90    310 m   max   1058 m

  variant                                <100m  <200m  <300m  <500m  <1000m  <2000m   median
  interpolated to acquisition, no correction     5      8     12     21     23     24       376 m
  + azimuth-displacement correction (sign +1)     0      2      4     12     22     23       760 m
  + azimuth-displacement correction (sign -1)     9     14     19     21     22     24       246 m

  sign confirmed by measurement: -1

  detected vs AIS length, 21 matched vessels:
    median detected 120 m   median AIS 134 m   median ratio 1.05×
    correlation r = 0.271

7. The evidence — look at the pixels, not the table
───────────────────────────────────────────────────
  lines 6085–16669 of 16669, samples 16669–26564 of 26564
scene         S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13  [VH]
detector      nightglass-cfar 1.0
aoi bbox      [10.5, 55.5, 12.5, 57.5]
coastline     GSHHG f/L1 +1000 m
examined      97,407,356 px   water 47,108,415   land-masked 50,298,941 (51.6%)
water sigma0  -29.1 dB   NESZ -29.4 dB   (sea is above the noise floor)
threshold      CFAR passes 3,081 px   NESZ floor passes 16,340 px   both 1,943 px   (binding: CFAR)
candidates    167   rejected <15.0 m: 0   >450.0 m: 12   outside AOI: 27   on coastline: 68
merged        25 fragment(s) folded into a brighter neighbour within 200.0 m
detections    35   (targets, not blobs)
elapsed       11.3 s

rendering
--------------------------------------------
  /app/data/out/chips_top.png
  /app/data/out/chips_spread.png
  /app/data/out/overview.png

What to look at
  data/out/chips_top.png        every detection at native resolution
  data/out/overview.png         the AOI in radar geometry, land mask drawn on it
  data/out/azimuth_correction.png   the sign, settled by measurement
  data/out/length_agreement.png     detected extent vs AIS-reported length

A detector that is only ever counted is a detector nobody has checked. Both
failures found while building this one — a land mask tracing the coastline, and
a CFAR whose statistics had collapsed — were invisible in the numbers and
obvious in the first render.

```

## `make tool-proof`

exit 0, 2026-09-09T14:33:07+01:00, wall 2m22.342s

```text
──────────────────────────────────────
  marking   UNCLASSIFIED // SYNTHETIC // DRAFT — NOT RELEASABLE
  claims    18, of which unsupported: 0

  every claim carries its references
    • The area of interest kattegat was searched between 2026-07-17 00:00 and 2026-07-18 00:00 UTC; the catalogue returned 2 Sentinel-1 granule(s).
        [scene×2]
    • Scene S1D_IW_GRDH_1SDV_20260717T052349_20260717T052414_003709_006A36_34CE was acquired at 2026-07-17 05:24:01 UTC in IW mode, polarisations VH+VV, and
        [scene×1]
    • The onboard CFAR detector found 37 candidate vessel(s) within the area of interest in that scene; the smallest measures 50 m.
        [scene×1; det×37]
    • 23 of those detections correspond to a vessel reporting AIS, after interpolating each vessel's position onto the acquisition instant and correcting fo
        [scene×1; det×23]
    … 14 more

  and the caveats are computed, not remembered
    - A detection with no AIS correspondence is a lead, not a conclusion. Revisit gaps, terrestrial coverage limits, transponder failure, class B low-power transponders and vessels not required to
    - No proportion of unmatched detections is stated in this report. The detector's precision is not validated. Over the Danish validation AOI 40% of detections have no AIS correspondence against
    - Danish Maritime Authority — AIS data. The Danish Maritime Authority accepts no liability for any errors or omissions, nor for the correctness or completeness of the data.
    - 1 further scene(s) in the search window were not correlated: S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13. Correlation is bounded to one scene per call; this report co
    - DRAFT — NOT RELEASABLE until reviewed and released at the human gate.

The one number this milestone is careful about
  The AIS over Denmark is ground truth, so rate_is_quotable is true -- the
  source side of the guard passes. The report still refuses to state a rate,
  because the detector's precision is not validated: 40% of detections
  are unmatched against a published base rate of ~5%, and the excess is coastal
  clutter and isolated false alarms, not dark vessels.

  Two independent conditions, checked separately, and only one of them was
  guarded before M4. See src/nightglass/tools/intrep.py.

To attach Claude Desktop
  ~/.config/Claude/claude_desktop_config.json:
    { "mcpServers": { "nightglass": {
        "command": "/usr/bin/docker",
        "args": ["exec", "-i", "nightglass-mcp", "nightglass-mcp", "stdio"] } } }
  Then restart Claude Desktop. Section 1 above already spoke the protocol over
  that exact command, so what Desktop sends next is the same bytes.

```

## `make agent-proof`

exit 0, 2026-09-09T14:34:25+01:00, wall 1m18.502s

```text

resumed from persisted state — RELEASED
------------------------------------------------
A different process from the one that drafted it.

INTREP — kattegat — 2026-09-09
UNCLASSIFIED // SYNTHETIC

FINDINGS
--------
• The area of interest kattegat was searched between 2026-07-17 00:00 and 2026-07-18 00:00 UTC; the catalogue returned 2 Sentinel-1 granule(s).
    [refs: scene 34CE]
• Scene S1D_IW_GRDH_1SDV_20260717T052349_20260717T052414_003709_006A36_34CE was acquired at 2026-07-17 05:24:01 UTC in IW mode, polarisations VH+VV, and is the granule this report is drawn from.
    [refs: scene 34CE]
• The onboard CFAR detector found 37 candidate vessel(s) within the area of interest in that scene; the smallest measures 50 m.
    [refs: scene 34CE; 37 detections]
• 23 of those detections correspond to a vessel reporting AIS, after interpolating each vessel's position onto the acquisition instant and correcting for SAR azimuth displacement; median separation 85 m.
    [refs: scene 34CE; 23 detections]
• 14 detection(s) have no AIS correspondence in dma within 500 m and the search window. Each is a lead for adjudication.
    [refs: scene 34CE; 14 detections]
• Detection det_00002 at 56.55897 N, 11.78923 E, extent 124 m, has no AIS correspondence in dma and is put forward for adjudication.
    [refs: scene 34CE; 1 detections]
• Detection det_00006 at 56.47674 N, 11.80660 E, extent 90 m, has no AIS correspondence in dma and is put forward for adjudication.
    [refs: scene 34CE; 1 detections]

What this shows
  The graph runs parse → plan → tools → correlate → draft_intrep →
  HUMAN_GATE → release and stops dead in the middle. Approval happens in a
  different process, minutes or days later, from state in Postgres.

  The findings are not the model's arithmetic. correlate runs
  deterministically over the configured AOI whatever the model did, and every
  claim is templated from the CorrelationResult. That is the fix for a measured
  failure: at M4 the model got every per-scene count right and still conflated
  two scenes while summarising them.

  Only this path can mark a report releasable. draft_intrep always
  returns releasable=false; the release node is the one place that flips it,
  and it is reachable only through the interrupt.

```

## `make bundle-proof`

exit 0, 2026-09-09T14:35:26+01:00, wall 1m0.918s

```text
$ docker exec <dind> docker run --rm -v nightglass_ollama_models:/m \
      alpine:3 find /m -type f
  /m/models/manifests/registry.ollama.ai/library/tiny/latest
  /m/models/blobs/sha256-bce69bc7b4d464301b96d0722b245d57a0cf183828239ee7ee370ddc21159711
  models/ and nothing else. The private key and the recommendations
  cache that sat beside it in the source volume did not travel.

$ docker exec <dind> ls -l /proof/clone/data/raw/sar /proof/clone/data/raw/ais
  /proof/clone/data/raw/ais:
  total 88
  -rw-rw-r--    1 1000     1000         90000 Sep  9 13:35 aisdk-fixture.zip

  /proof/clone/data/raw/sar:
  total 196
  -rw-rw-r--    1 1000     1000        200000 Sep  9 13:35 S1D_FIXTURE.zip

What this showed
────────────────
  create   one tar, manifest first, data cross-checked against sources.yaml
  inspect  the contents without reading the contents
  verify   on a file and on a pipe
  refused  a flipped byte
  refused  a truncated transfer, named as truncation
  refused  a member the manifest lists and the archive does not hold
  refused  a granule whose bytes are not the ones M6 committed
  restore  into an empty daemon, from one static binary and a tarball

  The real bundle is ~18 GB and is built with make bundle. Everything above
  is a property of the format; none of it is a property of the size.

```

## `make k8s-proof`

exit 0, 2026-09-09T14:40:07+01:00, wall 4m40.586s

```text
6. Resource limits are set, on every container
──────────────────────────────────────────────
  POD                                   CPU_REQ   MEM_REQ   MEM_LIM
  nightglass-api-5b745b9f56-hxmgx       50m       128Mi     1Gi
  nightglass-mcp-b568548fd-jfw9g        50m       128Mi     1Gi
  nightglass-postgis-6bfc6bc899-f87x8   50m       128Mi     1Gi
  nightglass-qdrant-55cb7f556c-hlx5n    50m       128Mi     1Gi
  probe                                 10m       32Mi      256Mi

  every container declares requests and a memory limit

What this showed
────────────────
  cluster  k3s in a container; host has no kubectl, no helm, no k3s
  images   imported from the same per-image tars make bundle writes
  chart    rendered by helm-in-a-container, applied, every pod Running
  control  internet reached with the policy deleted  <- without this the rest is not evidence
  boundary internet blocked with it applied — same pod, seconds later
  intra    qdrant still reachable inside the namespace
  limits   requests and memory limits on every container

  Not exercised: ollama, and the 6.2 GB of granules and corpus the enclave
  reads. Both are in the chart and neither changes whether an egress rule is
  enforced. deploy/values-proof.yaml is the override that turns them off.

```

## On the CPU profile

The same host with `COMPOSE_FILE=docker-compose.yml:docker/compose.cpu.yml` in `.env`: ollama has no GPU reservation and reports a CPU backend. `dark-proof` and `intrep` never touch the model; `demo` does, twice, and takes as long as it takes.

### `make dark-proof` (CPU profile)

exit 0, 2026-09-09T14:45:13+01:00, wall 4m7.469s

```text
examined      97,407,356 px   water 47,108,415   land-masked 50,298,941 (51.6%)
water sigma0  -29.1 dB   NESZ -29.4 dB   (sea is above the noise floor)
threshold      CFAR passes 3,081 px   NESZ floor passes 16,340 px   both 1,943 px   (binding: CFAR)
candidates    167   rejected <15.0 m: 0   >450.0 m: 12   outside AOI: 27   on coastline: 68
merged        25 fragment(s) folded into a brighter neighbour within 200.0 m
detections    35   (targets, not blobs)
elapsed       11.3 s

rendering
--------------------------------------------
  /app/data/out/chips_top.png
  /app/data/out/chips_spread.png
  /app/data/out/overview.png

What to look at
  data/out/chips_top.png        every detection at native resolution
  data/out/overview.png         the AOI in radar geometry, land mask drawn on it
  data/out/azimuth_correction.png   the sign, settled by measurement
  data/out/length_agreement.png     detected extent vs AIS-reported length

A detector that is only ever counted is a detector nobody has checked. Both
failures found while building this one — a land mask tracing the coastline, and
a CFAR whose statistics had collapsed — were invisible in the numbers and
obvious in the first render.

```

### `make intrep` (CPU profile)

exit 0, 2026-09-09T14:45:26+01:00, wall 0m12.267s

```text
  claims              16  (unsupported: 0)
    - The area of interest kattegat was searched between 2026-07-17 00:00 and 2026-07-18 00:00 UTC; the catalogue returned 2 Sentinel-1 granule(s).
    - Scene S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13 was acquired at 2026-07-17 05:23:36 UTC in IW mode, polarisations VH+VV, and is the granule this report is drawn from.
    - The onboard CFAR detector found 35 candidate vessel(s) within the area of interest in that scene; the smallest measures 40 m.
    - 21 of those detections correspond to a vessel reporting AIS, after interpolating each vessel's position onto the acquisition instant and correcting for SAR azimuth displacement; median separation 104 m.
    - 14 detection(s) have no AIS correspondence in dma within 500 m and the search window. Each is a lead for adjudication.
    - Detection det_00003 at 57.27242 N, 12.05189 E, extent 100 m, has no AIS correspondence in dma and is put forward for adjudication.
    - Detection det_00004 at 57.24563 N, 12.06212 E, extent 110 m, has no AIS correspondence in dma and is put forward for adjudication.
    - Detection det_00005 at 57.24240 N, 12.03420 E, extent 100 m, has no AIS correspondence in dma and is put forward for adjudication.
    - Detection det_00007 at 57.21527 N, 11.82825 E, extent 80 m, has no AIS correspondence in dma and is put forward for adjudication.
    - Detection det_00008 at 57.14083 N, 12.15567 E, extent 70 m, has no AIS correspondence in dma and is put forward for adjudication.
    - Detection det_00009 at 57.12194 N, 12.14630 E, extent 40 m, has no AIS correspondence in dma and is put forward for adjudication.
    - Detection det_00011 at 57.05369 N, 12.23000 E, extent 60 m, has no AIS correspondence in dma and is put forward for adjudication.
    - Detection det_00012 at 57.05311 N, 12.20999 E, extent 50 m, has no AIS correspondence in dma and is put forward for adjudication.
    - Detection det_00013 at 57.00801 N, 12.13782 E, extent 80 m, has no AIS correspondence in dma and is put forward for adjudication.
    - Detection det_00015 at 56.91498 N, 12.34489 E, extent 50 m, has no AIS correspondence in dma and is put forward for adjudication.
    - 10 of 14 unmatched detections are listed individually above; the remainder are in the correlation result.
  caveats             6
    ! A detection with no AIS correspondence is a lead, not a conclusion. Revisit gaps, terrestrial coverage limits, transponder failure, class B low-power transponders and vessels not required to carry AIS all produce one. The system surfaces candidates; the analyst adjudicates.
    ! No proportion of unmatched detections is stated in this report. The detector's precision is not validated. Over the Danish validation AOI 40% of detections have no AIS correspondence against a published base rate of ~5%. The excess is coastal clutter and isolated false alarms, not dark vessels: a shoreline-buffer sweep concentrates it near shore, and after duplicate detections of the same hull are merged the unmatched fraction rises rather than falls, because it is the matched detections that were duplicated. Counts of matched pairs are defensible; a dark-vessel rate is not.
    ! Danish Maritime Authority — AIS data. The Danish Maritime Authority accepts no liability for any errors or omissions, nor for the correctness or completeness of the data.
    ! 1 further scene(s) in the search window were not correlated: S1D_IW_GRDH_1SDV_20260717T052349_20260717T052414_003709_006A36_34CE. Correlation is bounded to one scene per call; this report covers only the scene named above.
    ! No supporting documents were retrieved, so this report contains findings only and no doctrinal assessment.
    ! DRAFT — NOT RELEASABLE until reviewed and released at the human gate.

```

### `make demo` (CPU profile)

exit 0, 2026-09-09T14:53:09+01:00, wall 7m43.279s

```text
  The AIS is ground truth, so that half passes. The detector is not
  precision-validated, so the rate is refused anyway.

8. So over Lisbon, ask a second detector instead.
─────────────────────────────────────────────────
ours          71 detections   (nightglass-cfar)
GFW           66 detections   (published layer, same granule)

both saw      49   (74% of GFW's, median separation 78 m)
GFW only      17   they found, we did not
ours only     22   we found, they did not

of those 22, how far to the nearest detection we BOTH saw:
  within 200 m   0   (0%)   — too close to be a second vessel
  median      26064 m

9. And none of that had a way out.
──────────────────────────────────
$ docker compose exec api curl -m 5 https://example.com
curl: (6) Could not resolve host: example.com

  Not a timeout — an internal network resolves no external name, so no
  packet is ever sent. Every number above came from the far side of that.


```

