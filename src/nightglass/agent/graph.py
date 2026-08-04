"""§M5's graph: parse → plan → tools → correlate → draft_intrep → HUMAN_GATE → release.

Two things make this a milestone rather than a wrapper, and both are decisions
about where *not* to let the model act.

**The gate is a real interrupt against a real checkpointer.** `interrupt()` halts
the graph and the state is written to Postgres, so the run survives the process
that started it: `nightglass-agent ask` exits at the gate, and
`nightglass-agent approve <thread>` — a different container invocation, minutes
later — resumes from the persisted state and finishes. A bare `input()` blocks a
thread and loses everything if that thread dies; `MemorySaver` would satisfy the
same API and keep the state inside the process that is supposed to have stopped.

Worth being precise about what "inspectable" buys, because the obvious stronger
claim is false. In plain SQL you get the run's existence, its thread, its
position in the graph and which channels it has populated — `checkpoints` and
`checkpoint_blobs` are ordinary tables. The *values* are msgpack, not JSON:
LangGraph's serialiser encodes dicts as msgpack, so `convert_from(blob,'UTF8')`
fails on them. Reading the payload means going through the checkpointer, which
is what `nightglass-agent show` does. Checked, rather than assumed — an earlier
version of this docstring claimed readable JSON.

**The answer is assembled from the correlation, not from the model's
recollection of it.** This is not caution for its own sake — it is the fix for a
measured failure. In M4's proof run the model got every per-scene count right,
listed thirty real detection ids, and still summarised them as "of the 60
detections … 15 were not", conflating two scenes into one scene's framing. Nothing in a prompt fixes that
reliably. So the spatial backbone is deterministic (`correlate` is called with
the parsed bbox, whatever the model did in the `tools` node), the findings are
templated from the `CorrelationResult` by `draft_intrep`, and the model's own
prose is used only where it is genuinely generative and citation-checked.

The `tools` node is deliberately *not* a second copy of M4's chaining proof.
That the local model can chain three spatial tools unaided is already
demonstrated by `make tool-proof`; re-proving it inside the graph would double
the runtime to show the same thing. Here the model chooses what documentary
context the report needs, which is a real choice with a real effect on the
INTREP's assessment section, and it inherits M4's two measured guards: a
max-iteration bound and a two-strike same-tool-same-arguments detector.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import httpx

from nightglass.config import settings
from nightglass.schemas import INTREP, Chunk, CorrelationResult
from nightglass.tools.base import ToolError

GATE = "HUMAN_GATE"

#: How far back a question reaches when it does not say. Wide enough to catch a
#: Sentinel-1 revisit at these latitudes, narrow enough that "recently" does not
#: silently mean "the whole archive".
DEFAULT_WINDOW_HOURS = 72


class AgentState(TypedDict, total=False):
    """Everything the graph carries. Plain JSON-shaped types, deliberately.

    Not for the serialiser's benefit — it encodes dicts and pydantic models
    alike — but for the reader's. `graph.get_state()` on a halted run returns
    exactly this, and a state of plain dicts is one an analyst or a debugger can
    read without importing the schema module that defined it. The models are
    reconstructed at the node that needs them and dumped straight back.
    """

    question: str
    bbox: list[float]
    start: str
    end: str
    scene_id: str | None
    document_query: str | None
    plan: list[str]
    steps: list[dict[str, Any]]
    chunks: list[dict[str, Any]]
    correlation: dict[str, Any] | None
    intrep: dict[str, Any] | None
    approved: bool | None
    reviewer_note: str | None
    answer: str
    errors: list[str]


# -- parse --------------------------------------------------------------------

_PARSE_SYSTEM = """\
You extract search parameters from an analyst's question. Reply only in the \
required JSON format.

- `hours_back`: how far back the question reaches. "the last 72 hours" is 72, \
"yesterday" is 48, "last week" is 168. Use 72 if it does not say.
- `on_date`: an explicit calendar date in the question as YYYY-MM-DD, else null. \
"17 July 2026" is "2026-07-17".
- `document_query`: what to look up in the doctrine corpus to interpret the \
finding — a short phrase, or null if the question is purely about positions.

Do not invent coordinates. The area of interest is fixed by configuration.
"""

_PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "hours_back": {"type": "integer"},
        "on_date": {"type": ["string", "null"]},
        "document_query": {"type": ["string", "null"]},
    },
    "required": ["hours_back", "on_date", "document_query"],
}


def parse(state: AgentState) -> dict[str, Any]:
    """Question → window and what to look up. Model-assisted, bounded.

    The bbox is *not* parsed out of the question even though the model could
    often manage it. §3.1 makes the AOI a configuration property — "nothing
    downstream may hardcode a bbox" — and a model that invents one turns a
    misread question into a search of the wrong ocean, which returns cleanly and
    silently. So the area comes from `NIGHTGLASS_AOI` and the model is told so.
    """
    aoi = settings.aoi
    now = datetime.now(UTC)
    parsed: dict[str, Any] = {}
    errors: list[str] = []

    try:
        parsed = _ask_json(
            _PARSE_SYSTEM,
            f"Today is {now:%Y-%m-%d}. Question: {state['question']}",
            _PARSE_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 — a failed parse must not lose the run
        errors.append(f"parse fell back to defaults: {type(exc).__name__}: {exc}")

    start, end = _window(parsed, now, errors)

    return {
        "bbox": aoi.bbox.as_list(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "document_query": parsed.get("document_query"),
        "errors": errors,
    }


def _window(parsed: dict[str, Any], now: datetime, errors: list[str]) -> tuple[datetime, datetime]:
    """A window, from an explicit date if there is one and a lookback if not."""
    on_date = parsed.get("on_date")
    if on_date:
        try:
            day = datetime.fromisoformat(str(on_date)).replace(tzinfo=UTC)
            return day, day + timedelta(days=1)
        except ValueError:
            errors.append(f"unparseable date {on_date!r}; used the default lookback")
    hours = parsed.get("hours_back")
    hours = hours if isinstance(hours, int) and 0 < hours <= 24 * 365 else DEFAULT_WINDOW_HOURS
    return now - timedelta(hours=hours), now




# -- plan ---------------------------------------------------------------------


def plan(state: AgentState) -> dict[str, Any]:
    """Resolve the request against what this deployment can actually answer.

    Deliberately not a model call. Everything decided here is a fact about
    configuration and the database — which AOI is loaded, whether its feed can
    support a correlation — and asking a language model to look those up would
    be asking it to guess at something checkable.
    """
    aoi = settings.aoi
    steps = [
        f"area of interest: {aoi.name} {aoi.bbox} (from configuration, not the question)",
        f"window: {state['start'][:16]} → {state['end'][:16]} UTC",
        f"AIS feed: {aoi.ais_source}"
        + ("" if aoi.is_ground_truth else " — demonstration source, not ground truth"),
    ]
    query = state.get("document_query")
    steps.append(
        f"documentary context: '{query}'" if query else "documentary context: none requested"
    )
    steps.append("correlation runs deterministically over the configured AOI after tool use")
    return {"plan": steps}


# -- tools --------------------------------------------------------------------

_TOOLS_SYSTEM = """\
You are gathering context for an intelligence report that will be assembled \
automatically after you finish. You have no internet access.

Your job here is the DOCUMENTARY context: call doc_search to find what the \
corpus says about how a finding of this kind should be interpreted, defined or \
handled. You may call nightglass_status or stac_search if you need to know what \
the deployment covers.

Do NOT try to produce the finding yourself. The vessel correlation runs \
deterministically after this step, over the configured area of interest, and \
your numbers would be discarded. Two or three tool calls is enough. When you \
have the context, reply with a one-line summary and stop.
"""


def tools(state: AgentState) -> dict[str, Any]:
    """The model chooses what context the report needs. Bounded, with M4's guards."""
    from nightglass.tools.chaining import chain

    query = state.get("document_query")
    ask = state["question"] if not query else f"{state['question']}\n\nStart with: {query}"

    try:
        result = chain(ask, max_iterations=4, system=_TOOLS_SYSTEM)
    except Exception as exc:  # noqa: BLE001 — context is a bonus, not the report
        return {
            "steps": [],
            "chunks": [],
            "errors": [*state.get("errors", []), f"tool phase failed: {type(exc).__name__}: {exc}"],
        }

    # Take the model's *queries*, not its results. What `chaining` hands the
    # model is a compaction built for a context window — it drops doc_id and
    # score — and rebuilding a Chunk from it fails, which is the right failure:
    # a display format is not an interchange format, and the report's evidence
    # must not be whatever happened to be convenient to show. Re-running the
    # retrieval costs one embedding call per query and yields real Chunks.
    from nightglass.tools import doc_search

    queries = [
        str(s.arguments.get("query", "")).strip()
        for s in result.steps
        if s.tool == "doc_search" and not s.error and s.arguments.get("query")
    ]
    if query and query not in queries:
        queries.append(query)

    chunks: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors = list(state.get("errors", []))
    for q in dict.fromkeys(queries):
        try:
            for chunk in doc_search(q, k=6):
                if chunk.chunk_id not in seen:
                    seen.add(chunk.chunk_id)
                    chunks.append(chunk.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"doc_search({q!r}) failed: {type(exc).__name__}: {exc}")

    return {
        "steps": [
            {
                "tool": s.tool,
                "arguments": s.arguments,
                "error": s.error,
                "repeated": s.repeated,
            }
            for s in result.steps
        ],
        "chunks": chunks,
        "errors": errors + ([f"tool phase stopped: {result.stopped}"] if result.stopped else []),
    }


# -- correlate ----------------------------------------------------------------


def correlate(state: AgentState) -> dict[str, Any]:
    """The deterministic backbone. Runs whatever the model did or did not do.

    A `ToolError` here is an answer, not a crash: over an AOI with no AIS loaded
    the tools refuse rather than reporting every detection as dark, and that
    refusal has to survive all the way to the analyst rather than being turned
    into an empty result somewhere in the graph.
    """
    from nightglass.tools import correlate as _correlate

    try:
        result = _correlate(
            state["bbox"],
            datetime.fromisoformat(state["start"]),
            datetime.fromisoformat(state["end"]),
            scene_id=state.get("scene_id"),
        )
    except ToolError as exc:
        return {"correlation": None, "errors": [*state.get("errors", []), str(exc)]}

    return {"correlation": result.model_dump(mode="json")}


# -- draft_intrep -------------------------------------------------------------


def draft(state: AgentState) -> dict[str, Any]:
    """Findings templated from the correlation; assessment generated and checked."""
    from nightglass.tools import draft_intrep

    if state.get("correlation") is None:
        return {"intrep": None}

    report = draft_intrep(
        CorrelationResult.model_validate(state["correlation"]),
        [Chunk.model_validate(c) for c in state.get("chunks", [])],
    )
    return {"intrep": report.model_dump(mode="json")}


# -- the gate -----------------------------------------------------------------


def human_gate(state: AgentState) -> dict[str, Any]:
    """Halt. Genuinely.

    Everything above this line has run; nothing below it has. The state is in
    Postgres and the process may exit. `interrupt` raises out of the node, the
    checkpointer writes, and `nightglass-agent approve` resumes here with the
    reviewer's decision — in a different process, from the persisted state.
    """
    from langgraph.types import interrupt

    intrep = state.get("intrep")
    decision = interrupt(
        {
            "gate": GATE,
            "question": state["question"],
            "marking": _marking(intrep),
            "claims": len(intrep["claims"]) if intrep else 0,
            "caveats": intrep["caveats"] if intrep else [],
            "unsupported_claims": _unsupported(intrep),
            "errors": state.get("errors", []),
            "prompt": "approve to release, or reject with a reason",
        }
    )

    if isinstance(decision, dict):
        return {
            "approved": bool(decision.get("approved")),
            "reviewer_note": decision.get("note"),
        }
    return {"approved": bool(decision), "reviewer_note": None}


def _marking(intrep: dict[str, Any] | None) -> str:
    if not intrep:
        return "NO REPORT"
    base = intrep.get("classification", "UNCLASSIFIED")
    return base if intrep.get("releasable") else f"{base} // DRAFT — NOT RELEASABLE"


def _unsupported(intrep: dict[str, Any] | None) -> int:
    if not intrep:
        return 0
    return sum(
        1
        for c in intrep["claims"]
        if not (c.get("scene_ids") or c.get("detection_ids") or c.get("chunk_ids"))
    )


# -- release ------------------------------------------------------------------


def release(state: AgentState) -> dict[str, Any]:
    """Assemble the answer from the report, and flip `releasable` only if approved.

    The drafter cannot mark its own work releasable — `draft_intrep` always
    returns False — so this node is the only place in the system where that bit
    is set, and it is reachable only through the interrupt.
    """
    intrep = state.get("intrep")
    approved = bool(state.get("approved"))

    if intrep is None:
        return {"answer": _no_report(state)}

    if not approved:
        note = state.get("reviewer_note") or ""
        head = "REPORT WITHHELD at the human gate — not released."
        return {"answer": f"{head}\nReason: {note}".strip() if note else head}

    intrep = {**intrep, "releasable": True}
    return {"intrep": intrep, "answer": _render(intrep)}


def _render(intrep: dict[str, Any]) -> str:
    """The analyst-facing text, built from the report's own fields.

    Every line here comes from a `Claim` or a caveat that was computed. Nothing
    is generated at this point, which is the whole reason the conflation bug
    measured at M4 cannot recur in the released answer.
    """
    findings, caveats = "FINDINGS", "CAVEATS"

    lines = [intrep["title"], intrep["classification"], "", findings, "-" * len(findings)]
    for claim in intrep["claims"]:
        ids = (
            [f"scene {s.split(':')[-1][-4:]}" for s in claim.get("scene_ids", [])[:1]]
            + ([f"{len(claim['detection_ids'])} detections"] if claim.get("detection_ids") else [])
            + list(claim.get("chunk_ids", []))
        )
        lines.append(f"• {claim['text']}")
        lines.append(f"    [refs: {'; '.join(ids) if ids else 'none'}]")
    lines += ["", caveats, "-" * len(caveats)]
    lines += [f"- {c}" for c in intrep["caveats"]]
    return "\n".join(lines)


def _no_report(state: AgentState) -> str:
    reasons = "\n".join(f"  - {e}" for e in state.get("errors", [])) or "  - (no reason recorded)"
    head = "No report: the correlation could not be run."
    return f"{head}\n{reasons}"


# -- the graph ----------------------------------------------------------------


def build(checkpointer: Any) -> Any:
    """Wire §M5's node list, in the order the spec gives it."""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(AgentState)
    builder.add_node("parse", parse)
    builder.add_node("plan", plan)
    builder.add_node("tools", tools)
    builder.add_node("correlate", correlate)
    builder.add_node("draft_intrep", draft)
    builder.add_node(GATE, human_gate)
    builder.add_node("release", release)

    builder.add_edge(START, "parse")
    builder.add_edge("parse", "plan")
    builder.add_edge("plan", "tools")
    builder.add_edge("tools", "correlate")
    builder.add_edge("correlate", "draft_intrep")
    # Even a run with no report passes the gate. An analyst approving nothing is
    # a decision worth recording, and routing failures around the gate would
    # make the one node that guarantees human review skippable by a database
    # state — which is exactly the thing a gate must not be.
    builder.add_edge("draft_intrep", GATE)
    builder.add_edge(GATE, "release")
    builder.add_edge("release", END)
    return builder.compile(checkpointer=checkpointer)


def new_thread_id() -> str:
    return f"ng-{uuid.uuid4().hex[:12]}"


def _ask_json(system: str, user: str, schema: dict, *, timeout: float = 120.0) -> dict[str, Any]:
    payload = {
        "model": settings.ollama_chat_model,
        "stream": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 4096},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{settings.ollama_host.rstrip('/')}/api/chat", json=payload)
        r.raise_for_status()
        return json.loads(r.json()["message"]["content"])


def intrep_of(state: dict[str, Any]) -> INTREP | None:
    return INTREP.model_validate(state["intrep"]) if state.get("intrep") else None
