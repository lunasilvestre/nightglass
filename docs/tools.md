# Six tools, two consumers

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

`make tool-proof` runs the whole of it: the MCP transport Claude Desktop attaches with, a tool
called over that transport, the local 14B model chaining three tools unaided, and the report
refusing to state the one number it must not.

The six tools live in `src/nightglass/tools/` and are defined once. FastAPI serves them over
HTTP, FastMCP serves the same functions over MCP, and the agent calls them in-process. A
tool that existed twice would be a tool that behaved differently depending on who asked.

### The boundary is crossed by a pipe, not a port

Claude Desktop runs on the host; the server runs in the enclave. They meet over stdio:

```bash
docker exec -i nightglass-mcp nightglass-mcp stdio
```

There is no published port, and there could not be: a container on a network declared
`internal: true` silently gets no host port mapping at all, even if one is written. `docker exec`
crosses the boundary without opening it, which is the honest way to do it.

That command is committed as `.mcp.json`, so a clone gives Claude Code the tools after one trust
approval. Verified with a real MCP client — `claude mcp list` reports ✔ Connected, and a headless
session drove `nightglass_status` and `correlate` over the pipe. `make mcp-tools` speaks the same
JSON-RPC over the same command without needing a client at all:

```
serverInfo   nightglass 0.1.0   protocol 2025-06-18
tools/list   7
    nightglass_status  ()
    stac_search        (bbox, start, end)
    detect_vessels     (scene_id)
    ais_match          (detections)
    doc_search         (query)
    correlate          (bbox, start, end)
    draft_intrep       (bbox, start, end)
```

### Provenance travels with the value

Not in a log line — on the object, so it survives crossing a tool boundary and losing its Python
type. A matched detection carries the reasoning that matched it:

```
MMSI 246541000, THUN GAZELLE, Tanker; position interpolated onto the acquisition
instant, azimuth displacement -213 m applied (separation before correction 181 m);
matched inside 500 m / ±11 min
```

and an unmatched one carries the caveat rather than leaving it to the reader:

```
no AIS correspondence in dma within 500 m and ±11 min of acquisition. This is a
statement about one feed at one instant, not about the vessel: revisit gaps,
terrestrial coverage limits, transponder failure, class B low power and vessels
not required to carry AIS all produce it. A lead, not a conclusion.
```

### The same tools, driven by the 14B model inside the enclave

A plain analyst question, no bbox and no scene id in the prompt, tools chosen by the model:

```
1. stac_search(...)                                    -> 2 scenes
2. detect_vessels({"scene_id": "S1A_IW_SLC__…_NWJ.csv"})
   ERROR  no scene '…' in the catalogue. Run `make scenes` …
3. ais_match({"detections": ["det-20260717-0523-…-0", …]})
   ERROR  10 detection id(s) are not in detect.detections … ids are assigned by
          the detector run and are not guessable.
4. stac_search(...)   REPEAT — same tool, same arguments, told to advance
5. detect_vessels({"scene_id": "S1D_…_BC13"})          -> 35 detections
6. ais_match({"detections": [35 real ids]})            -> matched 21, unmatched 14
```

The model invented a scene id and ten detection ids on its first attempt. **Every one was
refused**, with a message naming the fix, and the repeat detector caught the identical retry that
followed. It then used the real ids and reached 21 and 14 — the hand-checked SQL's numbers. A
tool surface that accepts a plausible-looking invented id is one whose provenance chain means
nothing; this is what it looks like when it does not.

That pair — a frontier model over a pipe from outside, a 14B model from inside, one tool
surface — is the part worth having. A capability that only works with a frontier model on the
other end of it is not an air-gapped capability.

### The number the tools will not give you

`CorrelationResult.rate_is_quotable` asks whether the AIS feed is complete enough to be a
denominator. Over Denmark it is: DMA is ground truth, and the field is **true**.

The report still refuses to state a dark-vessel rate, because that field only guards one side of
the fraction. It says nothing about whether the things in the *numerator* are vessels — and 40%
unmatched against a ~5% published base rate says they are substantially clutter and false alarms.
A matcher being validated does not make a detector's precision validated.

So there are two independent conditions, checked separately, and `DETECTOR_PRECISION_VALIDATED`
in `tools/intrep.py` is a constant sitting at `False` with a test as its tripwire — flipping it
takes a measurement, in the same commit. Three layers enforce the consequence, in increasing
order of how much they can be trusted: the templated findings never compute a proportion, the
generation prompt forbids one, and `scrub_rate_claims` removes any claim that states one anyway.
Only the third is a check rather than a request.

What comes out instead is a draft that carries its references and computes its own caveats:

```
marking   UNCLASSIFIED // SYNTHETIC // DRAFT — NOT RELEASABLE
claims    18, of which unsupported: 0

  • 23 of those detections correspond to a vessel reporting AIS, after interpolating
    each vessel's position onto the acquisition instant and correcting for SAR
    azimuth displacement; median separation 85 m.                  [scene×1; det×23]

  - No proportion of unmatched detections is stated in this report.
    The detector's precision is not validated. …
  - Danish Maritime Authority — AIS data. …
  - DRAFT — NOT RELEASABLE until reviewed and released at the human gate.
```

The DMA attribution is reproduced verbatim: a reworded licence condition is not the licence
condition.

### Two decisions worth naming

**`correlate` reuses a recorded detector run rather than re-reading the pixels.** This looks like
an efficiency argument and is a correctness one: detection ids are assigned *after* the length
and AOI filters, so re-running renumbers them, and `ais_match(["…:det_00005"])` would silently
mean a different vessel. Reuse is gated on identity of every recorded input — detector, version,
polarisation, AOI box, coastline, and every field of `DetectorConfig`, which is checkable because
`detect.runs.parameters` is jsonb. Measured: reused and recomputed give 60 detections that are
byte-identical on id, position, length, heading and confidence, in 0.9 s against 13.9 s.

**`correlate` is bounded to one scene per call.** Reading a granule takes 14–20 s. The
alternative — return a run id and let the client poll — needs a job table and a lifecycle, which
is exactly the kind of hidden state the tool contracts rule out. Scenes the search found but did not correlate come back
carrying a note saying so and how to select them, so the bound is visible in the result.
