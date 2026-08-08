# The demo: what it shows, and why it shows it twice

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

![NIGHTGLASS end to end](demo.gif)

One command, [`scripts/demo.sh`](../scripts/demo.sh), and nothing in it is staged: the 14B model
picks its own tools while the recording runs, and every number comes out of PostGIS and the SAR
pixels as you watch. It takes **57 s live**.

| file | what it is |
|---|---|
| **▶ [`demo.mp4`](demo.mp4)** | 4.0 MB, H.264, 49 s, play and pause. This is the one to watch. |
| [`demo.gif`](demo.gif) | the same timeline at a lower frame rate — what the README embeds. |
| [`demo.cast`](demo.cast) | the untouched real-time recording. `asciinema play docs/demo.cast`. |

### Retimed, not re-run

[`scripts/pace-demo.py`](../scripts/pace-demo.py) rewrites timestamps only — not one byte of the
content — and refuses to write a file whose output stream differs from the source, so what you
see is the real run at a readable pace. The cast is committed so both renders can be checked
against it, and [`scripts/check-demo.py`](../scripts/check-demo.py) (`make check-demo`) measures
per-row dwell time to answer the only question that matters about a demo video: can a human
actually follow it?

### Two AOIs, in the order an analyst meets them

The recording runs over the Lisbon AOI. The Lisbon AOI has **no AIS**, and that is not an
oversight — Denmark is the only European state publishing free point-level *historical* AIS,
which is exactly why it is the validation AOI. So the recording shows both:

1. Lisbon takes the question and produces 71 detections with the verdict **withheld**.
2. The corpus explains why a missing transponder is a lead rather than a finding.
3. A human releases the report from a different container.
4. The claim about the matcher is then made over Denmark, where there is ground truth to make it
   against.

Two AOIs on screen is also the config-driven argument made visible instead of asserted: nothing
in the code knows which AOI it is serving. The only place a bbox is named is
[`src/nightglass/config.py`](../src/nightglass/config.py).

---

**See also:** [the detector and the space–time join](detection.md) · [the agent](agent.md)
· [limitations](limitations.md)
