# NIGHTGLASS — documentation

*The deep dives behind [the README](../README.md).*

### Start here

| page | what is in it |
|---|---|
| [architecture.md](architecture.md) | one line of compose, and everything that follows from it |
| [quickstart.md](quickstart.md) | clone to running enclave, and the two accounts it needs |
| [demo.md](demo.md) | the 57-second run, why it is retimed and not re-run, and why two AOIs |
| [repository-layout.md](repository-layout.md) | what lives where |
| [quickstart.md#what-runs-without-an-account](quickstart.md#what-runs-without-an-account) | the table of exactly what a stranger with no Earthdata account can and cannot run |

### The proofs

One of the seven runs in CI on every push — `make bundle-proof`, which needs nothing but a
runner. The other six need the host: `make k8s-proof` is feasible on a runner too but not worth
2.33 GB of images through a privileged k3s on every push, and the remaining five need the card,
the granule and the licensed AIS day. [testing.md](testing.md) says which is which, and
[evidence/proofs.md](evidence/proofs.md) is the dated, committed output of all seven.

| page | what it proves |
|---|---|
| [testing.md](testing.md) | what CI actually proves, what the coverage number means, and where the rest is answered |
| [air-gap.md](air-gap.md) | no route out, and the model answers anyway |
| [rag.md](rag.md) | grounded vs ungrounded, the citation plumbing, and the refusal path |
| [detection.md](detection.md) | the detector, the azimuth physics, the space–time join in SQL |
| [agent.md](agent.md) | the checkpointer, the halt, and the resume in a different container |
| [tools.md](tools.md) | six tools over HTTP and MCP, and the local model chaining them |
| [bundle.md](bundle.md) | 18 GB behind a static Go binary, and the eight ways it refuses |
| [kubernetes.md](kubernetes.md) | the same boundary as a NetworkPolicy, against a negative control |

### The reasoning, and the honest parts

| page | what is in it |
|---|---|
| [design-decisions.md](design-decisions.md) | every choice that had a live alternative, and its cost |
| [limitations.md](limitations.md) | tested rather than assumed, and stated before being asked |
| [roadmap.md](roadmap.md) | what three more weeks would buy |
| [data-sources.md](data-sources.md) | every external input, its use and its licence |
| [NOTES.md](NOTES.md) | the running decision log, including what failed |
| [design/bundler.md](design/bundler.md) | the bundler's design, written before it was built |

### The pictures

`how-it-works.svg` · `architecture.svg` · `agent-graph.svg` · `results.svg` — the diagrams.
`demo.cast` · `demo.mp4` · `demo.gif` — the walkthrough.
[`evidence/`](evidence) — committed renders; the snapshot the numbers come from.
`azimuth_correction.png`, `chips_top.png`, `length_agreement.png`, `map_result.png` and
`overview.png` are written by `make dark-proof` over scene `…BC13` (the Kattegat validation
granule) and are byte-identical to the 2026-09-09 run — see [the detector](detection.md#looking-at-it).
`pt_chips.png` is separate: the Lisbon cross-check chip strip from
[the generalisation section](detection.md#it-generalises).
