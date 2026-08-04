# The NIGHTGLASS document corpus

Two halves, held differently on purpose.

| | Real half | Synthetic half |
|---|---|---|
| What | Public documents from four publishers | INTREP/INTSUM-style memos written for this project |
| Where | `data/corpus/` — **gitignored** | `corpus/synthetic/` — **committed** |
| How it gets there | `make fetch-corpus`, online, once | Already there |
| Described by | `corpus/sources.yaml` (a manifest of URLs) | The files themselves |

**Nothing real is vendored.** `corpus/sources.yaml` lists where each public
document comes from; `make fetch-corpus` downloads it onto the machine that will
read it and records a sha256. The synthetic memos are committed because there is
no URL to fetch an invented memo from, and because M6 requires a fresh clone to
be able to reproduce the demo.

## Composition

| Publisher | Docs | What it contributes |
|---|---|---|
| ICEYE Ltd | 17 | SAR imaging geometry, azimuth, radiometric calibration, geolocation accuracy, product levels, glossary |
| International Maritime Organization | 10 | The AIS carriage and operation obligation, its exceptions, LRIT, port state control |
| European Union (EUR-Lex) | 8 | AIS obligation inside EU waters, ship-to-ship transfer notification, AIS interruption as conduct, EMSA's mandate |
| Copernicus EMS | 4 | Real, dated, citable environmental context over the Iberian AOI |
| **Synthetic** | **21** | Doctrine, tradecraft, AOI baselines, reporting conventions |
| **Total** | **60** | |

The three load-bearing real documents, if you read nothing else:

- **IMO Resolution A.1106(29)** — the obligation to transmit, and the narrow
  master's-discretion exception. Without it, "dark" is jargon rather than a
  deviation from a stated norm.
- **Directive 2002/59/EC Article 6** — inside EU waters, AIS *shall be
  maintained in operation at all times*.
- **Council Regulation (EU) 2023/1214** — switching AIS off is treated as
  conduct in its own right, alongside advance notification of ship-to-ship
  transfers in a Member State's EEZ.

Those three are why a dark detection is a *reportable* event and not merely a
missing row.

## Licences, and why `redistributable` is a machine-readable field

Each group in `sources.yaml` carries `redistributable: true|false`.

- **ICEYE and IMO — `false`.** Both publish these documents openly; neither
  states a reuse licence, so default copyright applies. Fetching a public
  document onto the machine that reads it is not redistribution. Committing it
  to a public git repository is.
- **EUR-Lex and Copernicus — `true`.** Commission Decision 2011/833/EU and the
  Copernicus data policy both permit reuse with attribution.

The flag is a field rather than a comment so the fetcher can enforce it: an
entry marked `false` may only be written to a gitignored path, and
`make fetch-corpus` fails loudly rather than quietly doing the wrong thing. The
enclave never distinguishes them — it only ever reads local files.

Attribution owed, and reproduced in the top-level README:

> © European Union, Copernicus Emergency Management Service.
> © European Union, 1998–2026 (EUR-Lex). Only European Union legislation printed
> in the paper edition of the Official Journal is deemed authentic.

## The synthetic memos

Twenty-one memos in `corpus/synthetic/`, marked `UNCLASSIFIED // SYNTHETIC` **in
the front-matter and in the first line of the body**. The ingest
pipeline reads the marking from the front-matter and propagates it into every
chunk and from there into any report that cites one — the classification
propagation, done structurally rather than by asking a model to remember.

Ground rules they were written under:

- **No real vessel, operator, owner or flag administration is named**, and no
  conduct is attributed to any. Where a memo needs an identifier it uses MMSI
  maritime identification digits **999**, which ITU has not allocated to any
  administration, so no entry can collide with a real vessel.
- **Real geography, invented observations.** The Tagus estuary, Cascais
  anchorage and Leixões are real places and are described accurately. Traffic
  figures, tracks and events in them are invented.
- **Doctrine, not data.** The memos carry definitions, taxonomies and reporting
  conventions. Quantitative claims about the water come from the pipeline or
  from the real documents, not from here.
- All twenty-one are in English. Six were originally drafted in another language
  and were rewritten; `bge-m3` was chosen for its multilingual retrieval and
  still earns that choice on a national deployment, but nothing in this corpus
  exercises it today.

## The deliberate gap

M2's second acceptance criterion is that an unanswerable question produces an
explicit refusal rather than a guess. That only tests anything if the gap is
real, so one has been reserved and left empty:

**No document in this corpus — real or synthetic — reports vessel detections,
dark-vessel counts or traffic figures for Madeira or the Azores.**

Both are mentioned once each in `sources.yaml` reasoning and in NOTES.md as
areas of sparse Sentinel-1 coverage, and nowhere else. A question of the form
*"how many dark vessels were detected off Madeira?"* is therefore genuinely
unsupported by the corpus, and the refusal is a real one rather than a phrase
triggered by a keyword.

Do not add Madeira or Azores observations to this corpus without also changing
the refusal test.

## Adding to the corpus

**A public document:** append an item to the appropriate group in
`sources.yaml`, then `make fetch-corpus && make ingest`. `doc_id` is the
citation key an INTREP will carry — treat it as an identifier, not a label.
Renaming one orphans every chunk already indexed under it.

**A synthetic memo:** drop a markdown file in `corpus/synthetic/` with the same
front-matter shape as its neighbours, then `make ingest`. Ingest is idempotent —
point IDs are derived from the chunk ID, so re-running updates in place rather
than duplicating.
