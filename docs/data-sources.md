# Data sources and licences

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

Every external input is declared in [`data/sources.yaml`](../data/sources.yaml) with a URL, a
byte count and a sha256, and the fetchers refuse anything that hashes differently. The bytes
themselves are gitignored; the manifest is committed, so what this page claims and what lands on
your disk are the same claim.

| Source | Use | Licence / terms |
|---|---|---|
| **Sentinel-1 GRD** (ESA, via ASF) | SAR imagery, both AOIs — `make fetch-granules` | Free and open. Requires a NASA Earthdata account and one-time ASF EULA acceptance. |
| **Danish Maritime Authority AIS** | Point-level ground truth, validation AOI — `make fetch-ais` | Public S3, no credentials. See attribution below. |
| **eo-credentials** | `GFW_TOKEN` and friends live in `~/.config/eo-credentials.env`, never in this repo — [`scripts/load-env.sh`](../scripts/load-env.sh) loads it before `.env`, and a blank value in `.env` means *defer to central* rather than *override with empty*. | — |
| **Global Fishing Watch** SAR detections | Independent reference layer, demo AOI | **CC BY-NC 4.0** — non-commercial, attribution required. |
| **aisstream.io** | Live demonstration feed, demo AOI | Free tier. **Not ground truth** — see [limitations](limitations.md). |
| **ICEYE product documentation** | Document corpus — SAR technical reference | © ICEYE Ltd. Public documentation, **no open licence stated**. Fetched locally, `redistributable: false`, never committed here. |
| **IMO resolutions** | Document corpus — the AIS obligation itself | © IMO. Published via the IMO Knowledge Centre, **no open licence stated**. Same handling. |
| **EUR-Lex** (EU directives and regulations) | Document corpus — EU maritime law | © European Union, 1998–2026. Reuse authorised under Commission Decision 2011/833/EU with attribution. Only legislation printed in the paper Official Journal is authentic. |
| **Copernicus EMS** activation reports | Document corpus — AOI environmental context | © European Union, Copernicus Emergency Management Service. Free, full and open with attribution. |
| Synthetic INTREP/INTSUM memos | Document corpus — doctrine and tradecraft | Written for this project. Marked `UNCLASSIFIED // SYNTHETIC` in both header and metadata, and the marking propagates into anything citing them. Named no real vessel, operator or flag; identifiers use the unallocated MMSI prefix 999. |

> Contains data from the Danish Maritime Authority that is used in accordance with the
> conditions for the use of Danish public data.

[`corpus/sources.yaml`](../corpus/sources.yaml) is the document half of the manifest — URLs, not
documents — and [`corpus/README.md`](../corpus/README.md) covers what the corpus is, its licences
and the deliberate gap in it.

---

**See also:** [quickstart](quickstart.md) · [limitations](limitations.md)
· [grounded answers](rag.md)
