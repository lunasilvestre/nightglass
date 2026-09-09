"""§M5's graph and its CLI, tested at the seams the services sit behind.

The model call, the tool chain, the correlation and the checkpointer's Postgres
are replaced here; the graph's own decisions are not. What is pinned below is
what the graph does with what it is handed: which window it settles on when the
model's answer is unusable, that the tool node keeps the model's *queries* and
throws its *results* away, that `releasable` flips in exactly one place, and
that the gate is a real halt — `build()` runs against langgraph's own in-process
checkpointer, stops at HUMAN_GATE with the state written, and only a
`Command(resume=…)` gets it to END.

Nothing here asserts that a fake returned what it was told to return. Where a
stub appears it is the thing being routed around, and the assertion is about the
routing.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nightglass import tools as T
from nightglass.agent import graph as G
from nightglass.agent import main as M
from nightglass.config import settings
from nightglass.schemas import (
    INTREP,
    Chunk,
    Claim,
    CorrelationResult,
    Detection,
    Match,
    Scene,
)
from nightglass.tools.base import ToolError

KATTEGAT = {
    "AOI_KATTEGAT_BBOX": "10.5,55.5,12.5,57.5",
    "AOI_KATTEGAT_AIS_SOURCE": "dma",
}
LISBON = {
    "AOI_LISBON_BBOX": "-10.5,38.0,-8.5,39.5",
    "AOI_LISBON_AIS_SOURCE": "aisstream",
}


@pytest.fixture
def kattegat(monkeypatch: pytest.MonkeyPatch):
    """The configured AOI, from the environment — never from `.env`."""
    for k, v in KATTEGAT.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(settings, "aoi_name", "kattegat")
    return settings.aoi


@pytest.fixture
def lisbon(monkeypatch: pytest.MonkeyPatch):
    for k, v in LISBON.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(settings, "aoi_name", "lisbon")
    return settings.aoi


def _intrep(**kw: Any) -> dict[str, Any]:
    base = INTREP(
        title="INTREP — kattegat",
        generated_at=datetime(2026, 7, 17, 6, 0, tzinfo=UTC),
        aoi_name="kattegat",
        claims=[
            Claim(text="60 detections over one scene.", scene_ids=["s1"], detection_ids=["s1:0"]),
            Claim(text="An unsourced observation."),
        ],
        caveats=["A detection with no AIS correspondence is a lead, not a conclusion."],
    ).model_dump(mode="json")
    base.update(kw)
    return base


# -- parse: the window ---------------------------------------------------------

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def test_window_uses_an_explicit_date_as_a_whole_day():
    start, end = G._window({"on_date": "2026-07-17"}, NOW, [])
    assert start == datetime(2026, 7, 17, tzinfo=UTC)
    assert end - start == timedelta(days=1)


def test_an_unparseable_date_falls_back_and_says_so():
    """A model that answers with something unusable must not silently choose the
    window: the error travels with the run so the analyst sees which window ran."""
    errors: list[str] = []
    start, end = G._window({"on_date": "last thursday", "hours_back": 24}, NOW, errors)
    assert end - start == timedelta(hours=24)
    assert errors and "last thursday" in errors[0]


@pytest.mark.parametrize("hours", [0, -5, 24 * 365 + 1, "72", None, 3.5])
def test_an_out_of_range_lookback_becomes_the_default(hours: Any):
    """72 hours is the documented default; anything the model returns that is not
    a positive integer year or less is replaced by it rather than trusted."""
    start, end = G._window({"hours_back": hours}, NOW, [])
    assert end - start == timedelta(hours=G.DEFAULT_WINDOW_HOURS)


def test_a_usable_lookback_is_honoured():
    start, end = G._window({"hours_back": 168}, NOW, [])
    assert end - start == timedelta(hours=168)


def test_parse_keeps_the_configured_bbox_when_the_model_call_fails(kattegat, monkeypatch):
    """§3.1: the area of interest comes from configuration. A failed parse costs
    the window, never the ocean being searched, and the run continues."""

    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("ollama is not up")

    monkeypatch.setattr(G, "_ask_json", boom)
    out = G.parse({"question": "anything dark last night?"})

    assert out["bbox"] == kattegat.bbox.as_list()
    assert out["document_query"] is None
    assert out["errors"] and "RuntimeError" in out["errors"][0]
    assert datetime.fromisoformat(out["end"]) - datetime.fromisoformat(
        out["start"]
    ) == timedelta(hours=G.DEFAULT_WINDOW_HOURS)


def test_parse_takes_the_document_query_from_the_model(kattegat, monkeypatch):
    monkeypatch.setattr(
        G,
        "_ask_json",
        lambda *a, **k: {"hours_back": 24, "on_date": None, "document_query": "dark vessel"},
    )
    out = G.parse({"question": "…"})
    assert out["document_query"] == "dark vessel"
    assert out["errors"] == []


# -- plan ----------------------------------------------------------------------


def test_plan_marks_a_demonstration_feed_as_not_ground_truth(lisbon):
    steps = G.plan({"start": "2026-07-17T00:00:00+00:00", "end": "2026-07-18T00:00:00+00:00"})[
        "plan"
    ]
    assert any("not ground truth" in s for s in steps)
    assert any("from configuration, not the question" in s for s in steps)


def test_plan_says_when_no_documentary_context_was_requested(kattegat):
    steps = G.plan({"start": "2026-07-17T00:00", "end": "2026-07-18T00:00"})["plan"]
    assert "documentary context: none requested" in steps
    assert not any("not ground truth" in s for s in steps)


# -- tools ---------------------------------------------------------------------


class _Step:
    def __init__(self, tool: str, arguments: dict, error: str | None = None) -> None:
        self.tool, self.arguments, self.error, self.repeated = tool, arguments, error, False


class _ChainResult:
    def __init__(self, steps: list[_Step], stopped: str | None = None) -> None:
        self.steps, self.stopped = steps, stopped


def _chunk(cid: str, text: str = "text") -> Chunk:
    return Chunk(doc_id=cid.split("#")[0], chunk_id=cid, text=text, score=0.5)


def test_the_tool_node_re_runs_retrieval_instead_of_reusing_the_model_transcript(
    kattegat, monkeypatch
):
    """The model's tool *results* are a compaction built for a context window and
    are dropped; its *queries* are kept and re-run so the report cites real
    Chunks. Duplicate queries cost one retrieval, and a chunk seen twice is
    carried once."""
    calls: list[str] = []

    def fake_doc_search(q: str, k: int = 8, filters: Any = None) -> list[Chunk]:
        calls.append(q)
        return [_chunk("doc#1"), _chunk("doc#2")] if q == "dark vessel" else [_chunk("doc#1")]

    monkeypatch.setattr(
        "nightglass.tools.chaining.chain",
        lambda *a, **k: _ChainResult(
            [
                _Step("doc_search", {"query": "dark vessel"}),
                _Step("doc_search", {"query": "dark vessel"}),  # same query twice
                _Step("doc_search", {"query": "unreachable"}, error="ToolError: qdrant down"),
                _Step("nightglass_status", {}),
            ]
        ),
    )
    monkeypatch.setattr(T, "doc_search", fake_doc_search)

    out = G.tools({"question": "q", "document_query": "AIS carriage", "errors": []})

    assert calls == ["dark vessel", "AIS carriage"]  # deduped, and the failed step dropped
    assert [c["chunk_id"] for c in out["chunks"]] == ["doc#1", "doc#2"]
    assert [s["tool"] for s in out["steps"]] == [
        "doc_search",
        "doc_search",
        "doc_search",
        "nightglass_status",
    ]
    assert out["errors"] == []


def test_a_failing_retrieval_is_recorded_and_the_run_continues(kattegat, monkeypatch):
    def raiser(_q: str, k: int = 8, filters: Any = None) -> list[Chunk]:
        raise ToolError("the corpus is not ingested; run `make ingest`")

    monkeypatch.setattr(
        "nightglass.tools.chaining.chain",
        lambda *a, **k: _ChainResult([_Step("doc_search", {"query": "x"})], stopped="max"),
    )
    monkeypatch.setattr(T, "doc_search", raiser)

    out = G.tools({"question": "q", "errors": ["earlier"]})
    assert out["chunks"] == []
    assert out["errors"][0] == "earlier"
    assert "make ingest" in out["errors"][1]
    assert out["errors"][-1] == "tool phase stopped: max"


def test_a_failed_tool_phase_costs_the_context_not_the_report(kattegat, monkeypatch):
    """Documentary context is a bonus; the correlation is the report. A model
    that cannot be reached must not end the run."""

    def boom(*_a: Any, **_k: Any):
        raise RuntimeError("connect timeout")

    monkeypatch.setattr("nightglass.tools.chaining.chain", boom)
    out = G.tools({"question": "q", "errors": []})
    assert out["steps"] == [] and out["chunks"] == []
    assert "tool phase failed: RuntimeError" in out["errors"][0]


# -- correlate and draft -------------------------------------------------------


def _correlation() -> CorrelationResult:
    return CorrelationResult(
        aoi_name="kattegat",
        bbox=[10.5, 55.5, 12.5, 57.5],
        start=datetime(2026, 7, 17, tzinfo=UTC),
        end=datetime(2026, 7, 18, tzinfo=UTC),
        scenes=[
            Scene(
                id="s1",
                acquisition_time=datetime(2026, 7, 17, 5, 23, tzinfo=UTC),
                mode="IW",
                polarizations=["VH"],
                footprint_wkt="POLYGON((10 55, 12 55, 12 57, 10 57, 10 55))",
            )
        ],
        detections=[Detection(id="s1:0", scene_id="s1", lon=11.0, lat=56.0)],
        matches=[Match(detection_id="s1:0", status="dark")],
    )


def test_a_refusing_correlation_reaches_the_analyst_as_a_reason(kattegat, monkeypatch):
    """§M3's tools refuse over an AOI with no AIS rather than calling every
    detection dark. That refusal has to survive the graph, not become an empty
    result somewhere in it."""

    def refuse(*_a: Any, **_k: Any):
        raise ToolError("no AIS loaded for kattegat; run `make load-ais`")

    monkeypatch.setattr(T, "correlate", refuse)
    out = G.correlate(
        {
            "bbox": [10.5, 55.5, 12.5, 57.5],
            "start": "2026-07-17T00:00:00+00:00",
            "end": "2026-07-18T00:00:00+00:00",
            "errors": [],
        }
    )
    assert out["correlation"] is None
    assert "make load-ais" in out["errors"][0]


def test_correlate_passes_the_parsed_window_and_scene(kattegat, monkeypatch):
    seen: dict[str, Any] = {}

    def record(bbox, start, end, scene_id=None):
        seen.update(bbox=bbox, start=start, end=end, scene_id=scene_id)
        return _correlation()

    monkeypatch.setattr(T, "correlate", record)
    out = G.correlate(
        {
            "bbox": [10.5, 55.5, 12.5, 57.5],
            "start": "2026-07-17T00:00:00+00:00",
            "end": "2026-07-18T00:00:00+00:00",
            "scene_id": "s1",
        }
    )
    assert seen["scene_id"] == "s1"
    assert seen["start"] == datetime(2026, 7, 17, tzinfo=UTC)
    assert out["correlation"]["aoi_name"] == "kattegat"


def test_no_correlation_short_circuits_the_drafter(monkeypatch):
    """Without a correlation there is nothing to template a finding from, so the
    drafter is not called at all — a report built from the chunks alone would be
    prose with no spatial claim behind it."""
    called = False

    def drafter(*_a: Any, **_k: Any):
        nonlocal called
        called = True
        raise AssertionError("draft_intrep must not run without a correlation")

    monkeypatch.setattr(T, "draft_intrep", drafter)
    assert G.draft({"correlation": None, "chunks": []}) == {"intrep": None}
    assert called is False


def test_draft_rebuilds_the_models_from_the_plain_state(kattegat, monkeypatch):
    seen: dict[str, Any] = {}

    def drafter(correlation, chunks):
        seen.update(correlation=correlation, chunks=chunks)
        return INTREP(
            title="t", generated_at=datetime(2026, 7, 17, tzinfo=UTC), aoi_name="kattegat"
        )

    monkeypatch.setattr(T, "draft_intrep", drafter)
    out = G.draft(
        {
            "correlation": _correlation().model_dump(mode="json"),
            "chunks": [_chunk("doc#1").model_dump(mode="json")],
        }
    )
    assert isinstance(seen["correlation"], CorrelationResult)
    assert isinstance(seen["chunks"][0], Chunk)
    assert out["intrep"]["releasable"] is False


# -- the gate ------------------------------------------------------------------


def test_the_gate_payload_counts_unsupported_claims(monkeypatch):
    """The reviewer is shown how many claims carry no reference, because that is
    the number that decides whether approving is safe."""
    captured: dict[str, Any] = {}

    def fake_interrupt(payload: dict) -> dict:
        captured.update(payload)
        return {"approved": True, "note": "ok"}

    monkeypatch.setattr("langgraph.types.interrupt", fake_interrupt)
    out = G.human_gate({"question": "q", "intrep": _intrep(), "errors": ["e"]})

    assert captured["gate"] == G.GATE
    assert captured["claims"] == 2
    assert captured["unsupported_claims"] == 1
    assert captured["marking"].endswith("DRAFT — NOT RELEASABLE")
    assert out == {"approved": True, "reviewer_note": "ok"}


def test_the_gate_accepts_a_bare_boolean_decision(monkeypatch):
    monkeypatch.setattr("langgraph.types.interrupt", lambda _p: False)
    assert G.human_gate({"question": "q", "intrep": None}) == {
        "approved": False,
        "reviewer_note": None,
    }


def test_a_run_with_no_report_still_reaches_the_gate(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "langgraph.types.interrupt", lambda p: captured.update(p) or {"approved": False}
    )
    G.human_gate({"question": "q", "intrep": None, "errors": ["no AIS"]})
    assert captured["marking"] == "NO REPORT"
    assert captured["claims"] == 0 and captured["caveats"] == []


# -- release -------------------------------------------------------------------


def test_only_an_approval_flips_releasable():
    """The drafter always returns releasable=False; this node is the only place
    in the system that may set it, and only through the interrupt's decision."""
    approved = G.release({"intrep": _intrep(), "approved": True})
    withheld = G.release({"intrep": _intrep(), "approved": False, "reviewer_note": "wrong AOI"})

    assert approved["intrep"]["releasable"] is True
    assert "releasable" not in withheld
    assert withheld["answer"].startswith("REPORT WITHHELD at the human gate")
    assert "wrong AOI" in withheld["answer"]


def test_a_withheld_report_without_a_note_says_only_that():
    answer = G.release({"intrep": _intrep(), "approved": None})["answer"]
    assert answer == "REPORT WITHHELD at the human gate — not released."


def test_the_released_text_is_built_from_claim_fields_not_prose():
    """Every line of the answer comes from a Claim that was templated from the
    correlation. This is what makes the M4 conflation bug unable to recur here."""
    text = G.release({"intrep": _intrep(), "approved": True})["answer"]
    assert "• 60 detections over one scene." in text
    assert "[refs: scene s1; 1 detections]" in text
    assert "[refs: none]" in text  # the unsupported claim is shown as unsupported
    assert text.splitlines()[0] == "INTREP — kattegat"


def test_no_report_answers_with_the_reasons_it_collected():
    answer = G.release({"intrep": None, "errors": ["no AIS loaded", "parse fell back"]})["answer"]
    assert answer.startswith("No report: the correlation could not be run.")
    assert "  - no AIS loaded" in answer and "  - parse fell back" in answer


def test_no_report_and_no_reason_still_says_so():
    assert "(no reason recorded)" in G.release({"intrep": None})["answer"]


def test_thread_ids_are_prefixed_and_unique():
    a, b = G.new_thread_id(), G.new_thread_id()
    assert a.startswith("ng-") and len(a) == 15
    assert a != b


# -- the whole graph, against langgraph's own in-process checkpointer -----------


@pytest.fixture
def wired(monkeypatch, kattegat):
    """Every outward call replaced; the topology, the interrupt and the
    checkpointer are langgraph's own."""
    monkeypatch.setattr(
        G, "_ask_json", lambda *a, **k: {"hours_back": 24, "on_date": None, "document_query": None}
    )
    monkeypatch.setattr("nightglass.tools.chaining.chain", lambda *a, **k: _ChainResult([]))
    monkeypatch.setattr(T, "doc_search", lambda *a, **k: [])
    monkeypatch.setattr(T, "correlate", lambda *a, **k: _correlation())
    monkeypatch.setattr(
        T,
        "draft_intrep",
        lambda correlation, chunks: INTREP(
            title="INTREP — kattegat",
            generated_at=datetime(2026, 7, 17, tzinfo=UTC),
            aoi_name="kattegat",
            claims=[Claim(text="1 detection.", scene_ids=["s1"])],
            caveats=["A lead, not a conclusion."],
        ),
    )


def _graph_and_config():
    from langgraph.checkpoint.memory import MemorySaver

    return G.build(MemorySaver()), {"configurable": {"thread_id": G.new_thread_id()}}


def test_the_graph_genuinely_halts_at_the_gate(wired):
    """Not a flag that says it stopped: `invoke` returns with the gate pending,
    `release` has not run, and the state is in the checkpointer."""
    graph, config = _graph_and_config()
    result = graph.invoke({"question": "any dark vessels?", "errors": []}, config=config)
    state = graph.get_state(config)

    assert state.next == (G.GATE,)
    assert "answer" not in result
    assert state.values["intrep"]["releasable"] is False
    assert state.values["correlation"]["aoi_name"] == "kattegat"


def test_a_resume_from_the_persisted_state_reaches_the_end(wired):
    from langgraph.types import Command

    graph, config = _graph_and_config()
    graph.invoke({"question": "any dark vessels?", "errors": []}, config=config)
    result = graph.invoke(Command(resume={"approved": True, "note": None}), config=config)

    assert graph.get_state(config).next == ()
    assert result["intrep"]["releasable"] is True
    assert "1 detection." in result["answer"]


def test_a_rejection_resumes_to_the_end_and_releases_nothing(wired):
    from langgraph.types import Command

    graph, config = _graph_and_config()
    graph.invoke({"question": "any dark vessels?", "errors": []}, config=config)
    result = graph.invoke(
        Command(resume={"approved": False, "note": "AOI is wrong"}), config=config
    )

    assert result["intrep"]["releasable"] is False
    assert result["answer"].startswith("REPORT WITHHELD")
    assert "AOI is wrong" in result["answer"]


# -- the CLI -------------------------------------------------------------------


class _Snapshot:
    def __init__(self, values: dict, nxt: tuple[str, ...] = ()) -> None:
        self.values, self.next = values, nxt


class _Graph:
    """A graph stub. Everything asserted about it is what `main.py` does with it."""

    def __init__(self, states: dict[str, _Snapshot], result: dict | None = None) -> None:
        self.states, self.result = states, result or {}
        self.invoked: list[Any] = []

    def invoke(self, payload: Any, config: dict) -> dict:
        self.invoked.append((payload, config["configurable"]["thread_id"]))
        return self.result

    def get_state(self, config: dict) -> _Snapshot:
        return self.states.get(config["configurable"]["thread_id"], _Snapshot({}))


class _Saver:
    def __init__(self, threads: list[str] | None = None) -> None:
        self.threads = threads or []
        self.setup_called = False

    def setup(self) -> None:
        self.setup_called = True

    def list(self, _config: Any):
        return [
            type("Tup", (), {"config": {"configurable": {"thread_id": t}}})()
            for t in self.threads
        ]


def _install(monkeypatch, graph: _Graph, saver: _Saver) -> None:
    from contextlib import contextmanager

    @contextmanager
    def saver_cm():
        yield saver

    monkeypatch.setattr(M, "_saver", saver_cm)
    monkeypatch.setattr(M, "build", lambda _s: graph)


def test_the_interrupt_payload_is_read_across_langgraphs_return_shapes():
    boxed = type("Interrupt", (), {"value": {"marking": "UNCLASSIFIED"}})()
    assert M._interrupt_payload({"__interrupt__": [boxed]}) == {"marking": "UNCLASSIFIED"}
    assert M._interrupt_payload({"__interrupt__": [{"marking": "X"}]}) == {"marking": "X"}
    assert M._interrupt_payload({}) is None
    assert M._interrupt_payload({"__interrupt__": ["a bare string"]}) is None


def test_a_pending_run_is_marked_draft_and_one_without_a_report_is_not():
    assert M._marking_of({"intrep": {"classification": "UNCLASSIFIED"}}) == "UNCLASSIFIED // DRAFT"
    assert M._marking_of({}) == "NO REPORT"


def test_ask_refuses_an_empty_question(capsys):
    assert M.cmd_ask(argparse.Namespace(question=["  "], thread=None)) == 2


def test_ask_warns_and_fails_when_the_gate_did_not_fire(kattegat, monkeypatch, capsys):
    """Every path through the graph goes through the gate, so a run that reached
    END in one invocation means the interrupt did not fire — an exit code, not a
    printed report."""
    graph = _Graph({"t1": _Snapshot({"plan": ["p"]}, ())}, result={"answer": "released!"})
    _install(monkeypatch, graph, _Saver())

    rc = M.cmd_ask(argparse.Namespace(question=["any", "dark", "vessels"], thread="t1"))
    out = capsys.readouterr().out

    assert rc == 1
    assert "completed without halting" in out


def test_ask_at_the_gate_prints_the_resume_commands(kattegat, monkeypatch, capsys):
    graph = _Graph(
        {"t1": _Snapshot({"plan": ["p"]}, (G.GATE,))},
        result={"__interrupt__": [{"marking": "UNCLASSIFIED // DRAFT", "claims": 3,
                                   "unsupported_claims": 0, "caveats": ["c"], "errors": []}]},
    )
    saver = _Saver()
    _install(monkeypatch, graph, saver)

    rc = M.cmd_ask(argparse.Namespace(question=["q"], thread="t1"))
    out = capsys.readouterr().out

    assert rc == 0
    assert saver.setup_called is True
    assert "nightglass-agent approve t1" in out
    assert "claims              3" in out


def test_pending_lists_each_thread_once_and_only_at_the_gate(monkeypatch, capsys):
    """`saver.list(None)` walks every checkpoint, so a thread appears many times;
    the queue is the distinct threads whose next node is the gate."""
    graph = _Graph(
        {
            "halted": _Snapshot({"question": "q1", "intrep": {"classification": "U"}}, (G.GATE,)),
            "done": _Snapshot({"question": "q2"}, ()),
        }
    )
    _install(monkeypatch, graph, _Saver(["halted", "done", "halted", "done"]))

    assert M.cmd_pending(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert out.count("halted") == 1
    assert "done" not in out
    assert "awaiting human review  (1)" in out


def test_pending_says_so_when_nothing_is_halted(monkeypatch, capsys):
    _install(monkeypatch, _Graph({}), _Saver([]))
    assert M.cmd_pending(argparse.Namespace()) == 0
    assert "nothing halted" in capsys.readouterr().out


def test_a_long_question_is_elided_in_the_queue(monkeypatch, capsys):
    question = "x" * 100
    graph = _Graph({"t": _Snapshot({"question": question}, (G.GATE,))})
    _install(monkeypatch, graph, _Saver(["t"]))
    M.cmd_pending(argparse.Namespace())
    out = capsys.readouterr().out
    assert "x" * 59 + "…" in out
    assert question not in out


def test_show_refuses_an_unknown_thread(monkeypatch, capsys):
    _install(monkeypatch, _Graph({}), _Saver())
    assert M.cmd_show(argparse.Namespace(thread="nope")) == 1
    assert "no such thread: nope" in capsys.readouterr().err


def test_show_prints_the_errors_when_no_report_was_drafted(monkeypatch, capsys):
    graph = _Graph({"t": _Snapshot({"question": "q", "errors": ["no AIS loaded"]}, (G.GATE,))})
    _install(monkeypatch, graph, _Saver())
    assert M.cmd_show(argparse.Namespace(thread="t")) == 0
    out = capsys.readouterr().out
    assert "no report was drafted" in out and "no AIS loaded" in out


def test_show_marks_the_draft_not_releasable(monkeypatch, capsys):
    graph = _Graph({"t": _Snapshot({"question": "q", "intrep": _intrep()}, (G.GATE,))})
    _install(monkeypatch, graph, _Saver())
    M.cmd_show(argparse.Namespace(thread="t"))
    out = capsys.readouterr().out
    assert "DRAFT — NOT RELEASABLE" in out
    assert "1 detections" in out  # the claim's refs, not the claim text


def test_resume_refuses_a_thread_that_is_not_at_the_gate(monkeypatch, capsys):
    graph = _Graph({"t": _Snapshot({"question": "q"}, ())})
    _install(monkeypatch, graph, _Saver())

    assert M.cmd_approve(argparse.Namespace(thread="t", note=None)) == 1
    err = capsys.readouterr().err
    assert "is not waiting at the gate" in err
    assert "already finished" in err
    assert graph.invoked == []


def test_resume_refuses_an_unknown_thread(monkeypatch, capsys):
    _install(monkeypatch, _Graph({}), _Saver())
    assert M.cmd_reject(argparse.Namespace(thread="ghost", note="x")) == 1
    assert "no such thread: ghost" in capsys.readouterr().err


def test_approve_and_reject_send_the_reviewers_decision(monkeypatch, capsys):
    graph = _Graph({"t": _Snapshot({"question": "q"}, (G.GATE,))}, result={"answer": "the report"})
    _install(monkeypatch, graph, _Saver())

    assert M.cmd_approve(argparse.Namespace(thread="t", note="looks right")) == 0
    assert M.cmd_reject(argparse.Namespace(thread="t", note="wrong window")) == 0

    payloads = [p.resume for p, _t in graph.invoked]
    assert payloads == [
        {"approved": True, "note": "looks right"},
        {"approved": False, "note": "wrong window"},
    ]
    out = capsys.readouterr().out
    assert "RELEASED" in out and "WITHHELD" in out


def test_progress_flags_an_errored_and_a_repeated_tool_call(capsys):
    M._print_progress(
        {
            "plan": ["area of interest: kattegat"],
            "steps": [
                {"tool": "doc_search", "arguments": {"query": "x"}, "error": "boom",
                 "repeated": False},
                {"tool": "doc_search", "arguments": {"query": "x"}, "error": None,
                 "repeated": True},
            ],
            "correlation": {
                "scenes": [{"id": "s1"}],
                "detections": [{"id": "s1:0"}, {"id": "s1:1"}],
                "matches": [{"status": "dark"}, {"status": "matched"}],
            },
        }
    )
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "REPEAT — told to advance" in out
    assert "1 scene(s), 2 detections, 1 matched, 1 unmatched" in out


def test_main_routes_each_subcommand_with_its_parsed_arguments(monkeypatch):
    seen: dict[str, argparse.Namespace] = {}
    for name in ("cmd_ask", "cmd_pending", "cmd_show", "cmd_approve", "cmd_reject"):
        monkeypatch.setattr(M, name, lambda args, _n=name: seen.setdefault(_n, args) and 0)

    assert M.main(["ask", "any", "dark", "vessels"]) == 0
    assert seen["cmd_ask"].question == ["any", "dark", "vessels"]
    assert seen["cmd_ask"].thread is None

    M.main(["approve", "ng-123"])
    assert seen["cmd_approve"].thread == "ng-123" and seen["cmd_approve"].note is None

    M.main(["reject", "ng-123", "--note", "why"])
    assert seen["cmd_reject"].note == "why"


def test_main_requires_a_subcommand():
    with pytest.raises(SystemExit):
        M.main([])
