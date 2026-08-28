# Grounded answers, and the refusal path

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

60 documents, 1,814 chunks, embedded locally with bge-m3 and stored in Qdrant. Reproduce with
`make rag-proof`.

> **Those are the manifest's numbers, and EUR-Lex is not currently answering.** The eight EU
> legal instruments return `202` with an empty body, so a corpus fetched today indexes 52
> documents and 953 chunks rather than 60 and 1,814. `make fetch-corpus` fails rather than
> reporting that as a success, and re-running retries only the missing entries — see
> [limitations](limitations.md#the-document-layer).

The argument for the whole document layer is one comparison. **Same model, same question, same
machine — the only difference is whether retrieval is on.**

```
$ nightglass-corpus ask "what is a dark vessel?" --ungrounded

UNGROUNDED — no retrieval, model priors only
--------------------------------------------
A "dark vessel" isn't a standard term in common usage or in specific fields
like biology, chemistry, or astronomy, so its meaning can vary depending on
the context it's used in. Here are a few possible interpretations:

1. **Literature and Fiction**: ... an object with mysterious or ominous
   qualities, often associated with supernatural powers or evil forces.
2. **Philosophy and Metaphor**: ... something that holds negative emotions,
   secrets, or darkness within it.
3. **Art and Symbolism**: ... an abstract concept such as sorrow, despair, or
   the unknown.
4. **Technology (Retrocomputing)**: ... a black case or enclosure for hardware
   components, emphasizing aesthetics over functionality.
```

Fluent, confident, four confident readings, and **not one of them maritime**. The model states
outright that the term "isn't a standard term in common usage". Nothing in its priors holds the
operational meaning, and nothing in its tone signals the gap — which is the entire argument for
retrieval over a curated corpus rather than a bigger model.

```
$ nightglass-corpus ask "what is a dark vessel?" --sources

GROUNDED — UNCLASSIFIED // SYNTHETIC
----------------------------------------
1. A dark vessel is a vessel detected by a sensor for which no corresponding
   Automatic Identification System (AIS) report can be found in the reference
   feed within a stated distance and time tolerance of the detection's position
   and the image acquisition instant.
   [intrep-2026-014-dark-vessel-definition#0000]
   [intsum-2026-021-dark-vessel-doctrine#0000]

sources
----------------------------------------
[intrep-2026-014-dark-vessel-definition#0000]  (UNCLASSIFIED // SYNTHETIC)
[intsum-2026-021-dark-vessel-doctrine#0000]    (UNCLASSIFIED // SYNTHETIC)
```

Same model, same question, answered from retrieved sources — with the chunk ids to check it
against, and marked `UNCLASSIFIED // SYNTHETIC` because the sources it actually cited carry that
caveat. Nothing propagated that marking by hand.

```
$ nightglass-corpus ask "How many dark vessels were detected off Madeira in 2019?"

GROUNDED — UNCLASSIFIED
----------------------------------------
Not supported by available sources.

8 chunk(s) were retrieved but none supported an answer.
```

The refusal is worth more than either answer. Eight chunks came back and none of them supported
a claim, so the system declined rather than assembling something plausible out of adjacent
material. The gap is deliberate and documented in `corpus/README.md`: no document in the corpus
reports vessel detections off Madeira, and adding one would quietly delete this test.

**The corpus is entirely English, and `bge-m3` is still the right embedding model** — but for a
reason this repository no longer demonstrates. It was chosen because deployments are national
and a corpus and its queries will not always share a language; six memos were originally drafted
in another language to exercise that, and they have since been rewritten in English. The
measured cross-language separation that justified the choice (0.842 cosine against a translated
equivalent, 0.283 against unrelated text in the same language) still holds, but
nothing in the shipped corpus exercises it today. Stated plainly rather than left as an
unsupported claim.

```
$ nightglass-corpus search "how should AIS duplicate messages be handled before matching?"

0.6163  [imo-msc74-69-performance-standards#0023]   Resolution MSC.74(69) — …shipborne AIS
0.6156  [imo-a1106-29-ais-operational-use#0017]     Resolution A.1106(29) — onboard use of AIS
```

### The corpus

| Publisher | Docs | Contributes |
|---|---|---|
| ICEYE Ltd | 17 | SAR imaging geometry, azimuth, radiometry, geolocation accuracy, product levels |
| International Maritime Organization | 10 | The AIS carriage and operation obligation, its exceptions, LRIT, port state control |
| European Union (EUR-Lex) | 8 | The AIS obligation in EU waters, STS notification, AIS interruption as conduct |
| Copernicus EMS | 4 | Dated environmental context over the Lisbon AOI |
| Synthetic INTREP/INTSUM | 21 | Doctrine, tradecraft, AOI baselines, reporting conventions |

Three real documents do the load-bearing work, and they are why "dark" is a deviation from a
stated norm rather than jargon: **IMO A.1106(29)** (*"AIS should always be in operation when
ships are underway or at anchor"*, with a narrow master's-discretion exception that must be
logged and reported), **Directive 2002/59/EC Article 6** (*"shall maintain it in operation at all
times"* inside EU waters), and **Council Regulation (EU) 2023/1214**, which treats switching AIS
off as conduct in its own right.

Nothing real is committed to this repository. `corpus/sources.yaml` is a manifest of URLs;
`make fetch-corpus` downloads each one and records a sha256. Sources whose publishers grant no
reuse licence are marked `redistributable: false`, and the fetcher refuses to write them unless
the destination directory carries a `.gitignore` of `*` — the constraint is enforced by the code
rather than trusted to a comment. Details in [`corpus/README.md`](../corpus/README.md).

### Acquisition is online; operation is not

Documents are acquired exactly the way model weights are: a profile-gated service, on a separate
network, invoked explicitly. `corpus-fetcher` is to documents what `model-puller` is to weights.
The enclave then mounts the corpus **read-only** and cannot fetch, cannot write, and does not
even carry a PDF parser — `pdftotext` is installed only in the `fetcher` image stage, because
the enclave never sees a PDF, only normalised markdown.

Three independent barriers, and the error message names which one you hit:

```
$ docker compose exec api nightglass-corpus fetch
fetch failed: cannot write to /app/data/corpus: [Errno 30] Read-only file system
`fetch` is a PROVISIONING step: it runs in the corpus-fetcher service, on the provision
network, via `make fetch-corpus`. Inside the enclave this directory is mounted read-only
and no publisher is reachable.

$ docker compose exec api nightglass-corpus fetch --out /tmp/probe   # writable — so it is the network's turn
          FAILED: ConnectError: [Errno -3] Temporary failure in name resolution
```
