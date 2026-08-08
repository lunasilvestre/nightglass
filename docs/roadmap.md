# What I'd do with three more weeks

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

Each of these is a named gap in [limitations](limitations.md) rather than a feature wish.

- Real CFAR with sea-state adaptation, rather than one threshold per block
- **Fix coastal precision properly.** The shoreline buffer is a blunt instrument that trades
  real vessels for clutter. A hydrographic layer with harbour limits, aids to navigation and
  fixed-structure catalogues (wind farms, platforms) would let near-shore detections be
  classified rather than excluded — and that is where the 40% unmatched figure actually lives.
- **Per-detection azimuth time.** A granule spans 25 s and the AIS is currently interpolated to
  the scene mid-time. A detection's own along-track position says when it was really imaged,
  worth ~130 m at 10 kn — inside the current radius, but it is free accuracy.
- Multi-scene temporal tracking, so a detection becomes a track
- Coherent change detection with SLC pairs
- **Sign the bundle.** `nightglass-bundle verify` proves integrity — this is the bundle that was
  built — against one manifest digest carried out of band. It proves nothing about *who* built
  it. Authenticity needs key custody, revocation and an offline root, which is a programme
  rather than a subcommand, and claiming it without those would be the one dishonest line in
  this repository.
- **An offline `docker build`.** The wheelhouse covers Python and nothing else; the runtime image
  also needs four apt packages, so rebuilding from source inside an enclave still wants a Debian
  mirror. Carrying one is a different artifact from carrying wheels.
- SBOM via syft and image digests pinned rather than tags. Worth knowing before starting: the
  bundle manifest records a `repo_digest` for all six images, and for the two built here that
  value resolves against no registry, because they were never pushed. Docker reports the field
  either way and nothing local distinguishes them — telling them apart means asking a registry,
  which is the one thing this tool must not do. Digest pinning is therefore a real improvement
  for four of the six and a no-op for the two that matter most.
- **Entailment checking on citations.** Verification currently proves a cited chunk *exists*.
  Proving the chunk *supports the claim* needs a second pass — a natural-language inference
  model, or the chat model re-asked per claim against its cited span alone — and that closes
  the one gap the current check cannot see.
- **Hybrid retrieval.** bge-m3 dense vectors alone lose exact identifiers: an analyst searching
  for `A.1106(29)` or an MMSI wants lexical match, not semantic neighbourhood. BM25 alongside
  the dense index, fused, is the standard fix and Qdrant supports it natively.

---

**See also:** [limitations](limitations.md) · [design decisions](design-decisions.md)
· [NOTES.md](NOTES.md), the running decision log
