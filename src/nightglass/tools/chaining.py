"""The local model driving the §5 tools — §M4's second "done when".

    "the tools are callable over MCP from Claude Desktop, *and* the local Qwen
     model chains at least three of them to answer a query."

Same six functions, second consumer. Claude Desktop reaches them over MCP from
outside the enclave; this reaches them in-process from inside it, with no
network involved beyond ollama on the enclave bridge. Demonstrating both is the
part of the story worth having: a tool surface that only works when a frontier
model is on the other end of it is not an air-gapped capability.

This is a deliberately small loop — one `while`, no framework. §M5 replaces it
with LangGraph and a real interrupt at the human gate; what M5 should inherit
from here is not the structure but the two guards, because both were measured
rather than guessed:

**Max iterations.** Unbounded, the loop burns the context window.

**A same-tool-same-arguments detector.** Measured during pre-dev: fed a tool
result that contradicted its request — a 17 Jul scene when it had asked for
31 Jul–3 Aug — qwen2.5 re-called `stac_search` four times with tweaked date
ranges instead of advancing. That is arguably *correct* behaviour, since the
result genuinely did not satisfy the request, so the guard does not kill the
run: it tells the model, in the transcript, that this exact call was already
made and what came back, and only forces an answer if it does it again.

The tool results the model sees are compacted — counts, all ids, and a bounded
sample of positions. Compaction is visible in the payload (`sample_of`) rather
than silent, because a truncated list that does not say it was truncated reads
to the model as the whole answer, and it will then state the whole answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from nightglass.config import settings
from nightglass.tools.base import ToolError

#: Positions shown per result. The ids are always complete — they are the handle
#: the next tool call needs — but sixty rows of latitude and longitude are not
#: what the model needs to decide what to do next.
SAMPLE = 10

MAX_ITERATIONS = 8

_SYSTEM = """\
You are an intelligence support assistant in an air-gapped maritime analysis \
cell. You have no internet access. Everything you assert must come from a tool \
result in this conversation.

How to work:
- Call nightglass_status first if you do not know the area of interest.
- To answer a question about vessels in an area during a time window: \
stac_search to find a scene, then detect_vessels over that scene, then \
ais_match on the detection ids. correlate does all three in one call.
- Pass EVERY detection id to ais_match in a single call. Do not batch them and \
do not use the sample shown in a result as the working list. A tool result that \
says "partial": true covers only what you asked about, never the whole scene.
- Use doc_search for what a term means or what a regulation requires.
- Answer in English, whatever language the question is in.

What you must not do:
- Do not state a rate, percentage, fraction or proportion of detections lacking \
AIS. This deployment's detector precision is not validated, so counts are \
supportable and proportions are not.
- Do not call a vessel illegal, evading, suspicious or non-compliant. A \
detection with no AIS match is a lead for an analyst to adjudicate. Innocent \
explanations include satellite revisit gaps, terrestrial coverage limits, \
transponder failure, class B low-power transponders, and vessels not required \
to carry AIS at all.
- Do not invent scene ids, detection ids or positions. If a tool returned \
nothing, say so.
"""


@dataclass
class Step:
    """One tool call the model made, and what it got back."""

    tool: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None
    repeated: bool = False


@dataclass
class ChainResult:
    question: str
    answer: str
    steps: list[Step] = field(default_factory=list)
    iterations: int = 0
    stopped: str | None = None
    model: str = ""

    @property
    def tools_used(self) -> list[str]:
        return list(dict.fromkeys(s.tool for s in self.steps if s.error is None))

    def render(self) -> str:
        lines = [f"model     {self.model}", f"question  {self.question}", ""]
        for i, s in enumerate(self.steps, 1):
            args = json.dumps(s.arguments, default=str, ensure_ascii=False)
            lines.append(f"  {i}. {s.tool}({args[:160]})")
            if s.error:
                lines.append(f"     ERROR  {s.error}")
            elif s.repeated:
                lines.append("     REPEAT  same tool, same arguments — told to advance")
            else:
                lines.append(f"     ->     {_one_line(s.result)}")
        lines += [
            "",
            f"distinct tools chained  {len(self.tools_used)}  ({', '.join(self.tools_used)})",
            f"iterations              {self.iterations}",
        ]
        if self.stopped:
            lines.append(f"stopped                 {self.stopped}")
        lines += ["", "answer", "------", self.answer]
        return "\n".join(lines)


def chain(
    question: str,
    *,
    ollama_host: str | None = None,
    chat_model: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
    num_ctx: int = 16384,
    timeout: float = 600.0,
    verbose: bool = False,
    system: str | None = None,
) -> ChainResult:
    """Let the local model answer `question` by calling the §5 tools.

    `system` overrides the standing instructions. §M5's graph uses it to narrow
    this loop to gathering documentary context — its spatial numbers would be
    discarded there, because the correlation runs deterministically — while
    reusing the two guards below, which were measured rather than guessed.
    """
    host = (ollama_host or settings.ollama_host).rstrip("/")
    model = chat_model or settings.ollama_chat_model
    aoi = settings.aoi

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system or _SYSTEM},
        {
            "role": "system",
            "content": (
                f"Today is {datetime.now().astimezone():%Y-%m-%d}. The configured area of "
                f"interest is {aoi.name}, bbox {aoi.bbox.as_list()}, AIS source "
                f"{aoi.ais_source}."
            ),
        },
        {"role": "user", "content": question},
    ]

    out = ChainResult(question=question, answer="", model=model)
    seen: dict[str, str] = {}
    strikes = 0

    with httpx.Client(timeout=timeout) as client:
        for iteration in range(1, max_iterations + 1):
            out.iterations = iteration
            last = iteration == max_iterations
            reply = _ask(client, host, model, messages, num_ctx, tools=None if last else TOOLS)
            messages.append(reply)
            calls = reply.get("tool_calls") or []

            if not calls:
                out.answer = (reply.get("content") or "").strip()
                if last:
                    out.stopped = f"max_iterations={max_iterations} reached"
                return out

            for call in calls:
                fn = call.get("function") or {}
                name = str(fn.get("name", ""))
                args = _as_dict(fn.get("arguments"))
                key = f"{name}({json.dumps(args, sort_keys=True, default=str)})"
                step = Step(tool=name, arguments=args)

                if key in seen:
                    strikes += 1
                    step.repeated = True
                    out.steps.append(step)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": name,
                            "content": (
                                "You already made this exact call. The result was "
                                f"unchanged: {seen[key]} Do not call it again with the "
                                "same arguments — either call a different tool, change "
                                "the arguments meaningfully, or answer the question "
                                "with what you have."
                            ),
                        }
                    )
                    if strikes >= 2:
                        out.stopped = "repeated the same call twice; forced an answer"
                        final = _ask(client, host, model, messages, num_ctx, tools=None)
                        out.answer = (final.get("content") or "").strip()
                        return out
                    continue

                try:
                    step.result = _dispatch(name, args)
                    seen[key] = _one_line(step.result)
                except Exception as exc:  # noqa: BLE001 — the model gets to react
                    step.error = f"{type(exc).__name__}: {exc}"
                out.steps.append(step)
                if verbose:
                    print(f"  {name}  ->  {step.error or _one_line(step.result)}", flush=True)
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": json.dumps(
                            {"error": step.error} if step.error else step.result,
                            default=str,
                            ensure_ascii=False,
                        ),
                    }
                )

    out.stopped = f"max_iterations={max_iterations} reached"
    return out


def _ask(
    client: httpx.Client,
    host: str,
    model: str,
    messages: list[dict[str, Any]],
    num_ctx: int,
    *,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": messages,
        "options": {"temperature": 0, "num_ctx": num_ctx},
    }
    if tools:
        payload["tools"] = tools
    r = client.post(f"{host}/api/chat", json=payload)
    r.raise_for_status()
    return r.json()["message"]


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    return {}


# -- dispatch, and the compaction the model sees ------------------------------


def _dispatch(name: str, args: dict[str, Any]) -> Any:
    from nightglass import tools as T

    if name == "nightglass_status":
        # The MCP server's own implementation, called as a plain function —
        # fastmcp's `@mcp.tool` registers it and returns it unchanged, so the
        # model on this side and Claude Desktop on the other read the same text.
        from nightglass.mcp.server import nightglass_status

        return nightglass_status()
    if name == "stac_search":
        return _scenes(T.stac_search(args["bbox"], args["start"], args["end"]))
    if name == "detect_vessels":
        return _detections(
            T.detect_vessels(args["scene_id"], float(args.get("min_length_m", 15.0)))
        )
    if name == "ais_match":
        # The tolerance is NOT taken from the model, and that is deliberate.
        # The radius is the entire boundary between "matched" and "dark": at the
        # validated 500 m this scene is 45 matched and 15 dark, at 100 m it is 20
        # and 40, at 50 m it is 8 and 52 — same pixels, same AIS, an answer that
        # moves by a factor of three. Letting a model pick it is letting it
        # choose its own evidence threshold mid-answer, and pre-dev already
        # measured what this one does when a result does not satisfy it: it
        # re-calls with tweaked arguments. Analysts and the §M5 agent can still
        # pass a tolerance through the Python contract; this surface cannot.
        ids = args["detections"]
        radius, window = settings.match_radius_m, settings.match_window_min
        return _matches(T.ais_match(ids, int(window), radius), _scene_totals(ids), radius, window)
    if name == "doc_search":
        return _chunks(T.doc_search(args["query"], int(args.get("k", 6))))
    if name == "correlate":
        return _correlation(
            T.correlate(
                args["bbox"],
                args["start"],
                args["end"],
                float(args.get("min_length_m", 15.0)),
                scene_id=args.get("scene_id"),
            )
        )
    raise ToolError(f"no tool named {name!r}. Available: {', '.join(t['function']['name'] for t in TOOLS)}")


def _scene_totals(detection_ids: list[str]) -> dict[str, int]:
    """How many detections each named scene actually has, so `partial` is decidable."""
    from nightglass.spatial.db import connect

    scenes = sorted({i.rsplit(":", 1)[0] for i in detection_ids if ":" in i})
    if not scenes:
        return {}
    with connect() as db, db.cursor() as cur:
        cur.execute(
            "SELECT scene_id, count(*) AS n FROM detect.detections "
            "WHERE scene_id = ANY(%s) GROUP BY scene_id",
            (scenes,),
        )
        return {r["scene_id"]: r["n"] for r in cur.fetchall()}


def _scenes(scenes: list[Any]) -> dict[str, Any]:
    return {
        "count": len(scenes),
        "scenes": [
            {
                "scene_id": s.id,
                "acquisition_time": f"{s.acquisition_time:%Y-%m-%dT%H:%M:%SZ}",
                "mode": s.mode,
                "polarizations": s.polarizations,
            }
            for s in scenes
        ],
    }


def _detections(detections: list[Any]) -> dict[str, Any]:
    """Every id, and a clearly-fenced sample of positions.

    The fencing is not cosmetic. A first version returned `detection_ids`
    alongside a bare `sample` of ten, and the model called `ais_match` on the
    ten — six times, in batches — then stated a conclusion about the scene from
    the last batch. A sample shown next to the working set gets used as the
    working set, so the sample now says what it is in its own key name and the
    payload carries the instruction rather than relying on the system prompt.
    """
    return {
        "count": len(detections),
        "detection_ids": [d.id for d in detections],
        "next_step": (
            f"To find which of these lack AIS, call ais_match once with ALL "
            f"{len(detections)} ids from detection_ids. Do not batch them and do "
            "not use the positions_sample below as the list — it is a preview of "
            "the first few, for orientation only."
        ),
        "positions_sample_not_the_working_set": [
            {
                "id": d.id,
                "lat": round(d.lat, 5),
                "lon": round(d.lon, 5),
                "length_m": d.length_m,
                "confidence": d.confidence,
            }
            for d in detections[:SAMPLE]
        ],
        "note": "Our own detector over the radar pixels, not a published layer.",
    }


def _matches(
    matches: list[Any], scene_totals: dict[str, int], radius_m: float, window_min: float
) -> dict[str, Any]:
    """Counts, and whether they are counts of the whole scene or of a slice.

    `partial` exists because the failure it catches is invisible: asked about a
    subset, the tool answers correctly about that subset, and nothing in the
    answer says it was a subset. The model then reports it as the scene. A
    result that cannot say "you only asked about a sixth of this" is a result
    that will be over-generalised.
    """
    dark = [m for m in matches if m.status == "dark"]
    matched = [m for m in matches if m.status == "matched"]
    total = sum(scene_totals.values())
    partial = len(matches) < total
    out: dict[str, Any] = {
        "matched_count": len(matched),
        "unmatched_count": len(dark),
        "requested": len(matches),
        "scene_detection_totals": scene_totals,
        "partial": partial,
        "match_tolerance": (
            f"{radius_m:.0f} m and ±{window_min:g} min — the validated setting, "
            "fixed. These counts are only comparable with others at the same "
            "tolerance."
        ),
        "sample_of": f"{min(SAMPLE, len(matches))} of {len(matches)} shown below",
        "sample": [
            {
                "detection_id": m.detection_id,
                "status": m.status,
                "mmsi": m.mmsi,
                "distance_m": round(m.distance_m) if m.distance_m is not None else None,
                "time_delta_s": m.time_delta_s,
                "ais_source": m.ais_source,
            }
            for m in matches[:SAMPLE]
        ],
        "unmatched_detection_ids": [m.detection_id for m in dark],
        "may_state_a_rate": False,
        "note": (
            "Report unmatched detections as counts and positions, never as a "
            "proportion. An unmatched detection is a lead for an analyst, not a "
            "finding about a vessel."
        ),
    }
    if partial:
        out["warning"] = (
            f"These counts cover the {len(matches)} detection(s) you asked about, "
            f"not the {total} in the scene(s). They are NOT the scene totals and "
            "must not be reported as such. Call ais_match again with every id "
            "from detect_vessels to get the full picture."
        )
    return out


def _chunks(chunks: list[Any]) -> dict[str, Any]:
    return {
        "count": len(chunks),
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "classification": c.classification,
                "title": c.title,
                "text": c.text,
            }
            for c in chunks
        ],
    }


def _correlation(c: Any) -> dict[str, Any]:
    from nightglass.tools.intrep import rate_verdict

    verdict = rate_verdict(c)
    return {
        "aoi": c.aoi_name,
        "scenes": [s.id for s in c.scenes],
        "correlated_scene": next((d.scene_id for d in c.detections), None),
        "detection_count": len(c.detections),
        "matched_count": len(c.matches) - len(c.dark),
        "unmatched_count": len(c.dark),
        "unmatched_detection_ids": [m.detection_id for m in c.dark],
        "sample_of": f"{min(SAMPLE, len(c.detections))} of {len(c.detections)} shown below",
        "sample": [
            {"id": d.id, "lat": round(d.lat, 5), "lon": round(d.lon, 5), "length_m": d.length_m}
            for d in c.detections[:SAMPLE]
        ],
        "may_state_a_rate": verdict.quotable,
        "why_not": verdict.reasons,
    }


def _one_line(result: Any) -> str:
    if isinstance(result, dict):
        keys = ("count", "detection_count", "matched_count", "unmatched_count")
        bits = [f"{k}={result[k]}" for k in keys if k in result]
        return ", ".join(bits) or json.dumps(result, default=str)[:120]
    return json.dumps(result, default=str, ensure_ascii=False)[:120]


# -- the schemas the model chooses from ---------------------------------------

_BBOX = {
    "type": "array",
    "items": {"type": "number"},
    "description": "[min_lon, min_lat, max_lon, max_lat] in WGS84 degrees",
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "nightglass_status",
        "The configured area of interest, its AIS source, and what this "
        "deployment may claim. Call this first if the area is not obvious.",
        {},
        [],
    ),
    _tool(
        "stac_search",
        "Find Sentinel-1 SAR scenes covering an area during a time window. "
        "Searches the local catalogue; an empty result means no scene was staged.",
        {
            "bbox": _BBOX,
            "start": {"type": "string", "description": "ISO-8601 UTC, e.g. 2026-07-17T00:00:00Z"},
            "end": {"type": "string", "description": "ISO-8601 UTC"},
        },
        ["bbox", "start", "end"],
    ),
    _tool(
        "detect_vessels",
        "Run our own CFAR vessel detector over one scene. Returns detection ids "
        "to pass to ais_match, plus a sample of positions.",
        {
            "scene_id": {"type": "string", "description": "from stac_search"},
            "min_length_m": {
                "type": "number",
                "description": (
                    "smallest vessel to report; leave unset unless the question "
                    "asks for a size threshold. Lowering it below the recorded "
                    "run re-reads the SAR pixels and takes about a minute."
                ),
            },
        },
        ["scene_id"],
    ),
    _tool(
        "ais_match",
        "Match detections against AIS, returning matched and unmatched together. "
        "Pass every id from detect_vessels in ONE call — batching gives counts "
        "for a slice that look like counts for the scene. The match tolerance is "
        "fixed at the validated setting and is not yours to choose. Unmatched "
        "means no AIS correspondence in this feed at this instant: a lead to "
        "adjudicate, never a conclusion, and never a proportion.",
        {
            "detections": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "every detection id from detect_vessels, in one call — not a "
                    "sample and not a batch"
                ),
            }
        },
        ["detections"],
    ),
    _tool(
        "doc_search",
        "Search the local corpus of doctrine, regulation and procedure. Use for "
        "what a term means or what a rule requires, not for facts about a scene.",
        {"query": {"type": "string"}, "k": {"type": "integer"}},
        ["query"],
    ),
    _tool(
        "correlate",
        "Do stac_search, detect_vessels and ais_match in one call over an area "
        "and window. Bounded to one scene per call.",
        {
            "bbox": _BBOX,
            "start": {"type": "string", "description": "ISO-8601 UTC"},
            "end": {"type": "string", "description": "ISO-8601 UTC"},
            "min_length_m": {"type": "number"},
            "scene_id": {"type": "string", "description": "optional: which scene to correlate"},
        },
        ["bbox", "start", "end"],
    ),
]
