"""§M4's tool-chaining loop, and the `nightglass-tools` CLI.

The model is scripted here, which is the point: the two guards this loop exists
for were measured against a real model and then have to hold for any model. What
is pinned is the loop's own behaviour — that an identical call is recognised
however the model orders its arguments, that the second identical call stops the
run and forces an answer, that the last iteration is offered no tools — and the
compaction the model is shown, where `partial`, `sample_of` and
`may_state_a_rate` are the anti-over-generalisation mechanism the audit found
completely untested.

`_dispatch`'s one non-delegating decision is also here: `ais_match` takes the
tolerance from configuration and discards whatever the model asked for.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from nightglass import tools as T
from nightglass.config import settings
from nightglass.schemas import Chunk, CorrelationResult, Detection, Match, Scene
from nightglass.tools import chaining as C
from nightglass.tools import cli as CLI
from nightglass.tools.base import ToolError


@pytest.fixture(autouse=True)
def kattegat(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AOI_KATTEGAT_BBOX", "10.5,55.5,12.5,57.5")
    monkeypatch.setenv("AOI_KATTEGAT_AIS_SOURCE", "dma")
    monkeypatch.setattr(settings, "aoi_name", "kattegat")


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"function": {"name": name, "arguments": args}}


def _script(monkeypatch, replies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drive the loop with a fixed sequence of model replies. Records each ask."""
    asks: list[dict[str, Any]] = []
    pending = list(replies)

    def fake_ask(client, host, model, messages, num_ctx, *, tools):
        asks.append({"tools": tools, "messages": list(messages)})
        return pending.pop(0) if pending else {"content": "done"}

    monkeypatch.setattr(C, "_ask", fake_ask)
    return asks


def _detections(n: int) -> list[Detection]:
    return [Detection(id=f"s1:{i}", scene_id="s1", lon=11.0, lat=56.0, length_m=40.0) for i in range(n)]


# -- the loop ------------------------------------------------------------------


def test_a_tool_result_is_handed_back_to_the_model_as_a_tool_message(monkeypatch):
    asks = _script(
        monkeypatch,
        [{"tool_calls": [_call("doc_search", query="dark vessel")]}, {"content": "the answer"}],
    )
    monkeypatch.setattr(T, "doc_search", lambda q, k: [
        Chunk(doc_id="d", chunk_id="d#1", text="t", score=0.4, title="T")
    ])

    out = C.chain("q")

    assert out.answer == "the answer"
    assert out.tools_used == ["doc_search"]
    assert out.iterations == 2
    tool_messages = [m for m in asks[1]["messages"] if m.get("role") == "tool"]
    assert json.loads(tool_messages[0]["content"])["count"] == 1


def test_the_same_call_with_reordered_arguments_counts_as_a_repeat(monkeypatch):
    """The seen-key is the tool name plus its arguments sorted, so a model that
    re-issues the same call with the keys in a different order is caught. Without
    that, the guard is trivially defeated by JSON ordering."""
    asks = _script(
        monkeypatch,
        [
            {"tool_calls": [_call("stac_search", bbox=[1, 2, 3, 4], start="a", end="b")]},
            {"tool_calls": [_call("stac_search", end="b", bbox=[1, 2, 3, 4], start="a")]},
            {"content": "fine, here is the answer"},
        ],
    )
    monkeypatch.setattr(T, "stac_search", lambda bbox, start, end: [])

    out = C.chain("q")

    assert [s.repeated for s in out.steps] == [False, True]
    assert out.stopped is None  # one strike is a nudge, not a stop
    nudge = next(m for m in reversed(asks[2]["messages"]) if m.get("role") == "tool")["content"]
    assert "You already made this exact call" in nudge


def test_two_repeats_stop_the_tool_use_and_force_an_answer(monkeypatch):
    """Measured during pre-dev: fed a result that did not satisfy its request the
    model re-called four times with tweaked arguments. The second identical call
    ends tool use and asks for an answer with no tools offered."""
    asks = _script(
        monkeypatch,
        [
            {"tool_calls": [_call("doc_search", query="x")]},
            {"tool_calls": [_call("doc_search", query="x")]},
            {"tool_calls": [_call("doc_search", query="x")]},
            {"content": "answering with what I have"},
        ],
    )
    monkeypatch.setattr(T, "doc_search", lambda q, k: [])

    out = C.chain("q", max_iterations=8)

    assert out.stopped == "repeated the same call twice; forced an answer"
    assert out.answer == "answering with what I have"
    assert asks[-1]["tools"] is None
    assert len(out.steps) == 3


def test_the_last_iteration_is_offered_no_tools(monkeypatch):
    """An iteration budget that still advertised tools would end the run with an
    unanswered tool call instead of an answer."""
    asks = _script(
        monkeypatch,
        [
            {"tool_calls": [_call("doc_search", query="a")]},
            {"content": "final"},
        ],
    )
    monkeypatch.setattr(T, "doc_search", lambda q, k: [])

    out = C.chain("q", max_iterations=2)

    assert [a["tools"] is None for a in asks] == [False, True]
    assert out.stopped == "max_iterations=2 reached"
    assert out.answer == "final"


def test_a_model_that_never_answers_stops_at_the_budget(monkeypatch):
    _script(monkeypatch, [{"tool_calls": [_call("doc_search", query=str(i))]} for i in range(3)])
    monkeypatch.setattr(T, "doc_search", lambda q, k: [])
    out = C.chain("q", max_iterations=3)
    assert out.stopped == "max_iterations=3 reached"
    assert out.iterations == 3


def test_a_failing_tool_is_reported_to_the_model_rather_than_ending_the_run(monkeypatch):
    asks = _script(
        monkeypatch,
        [{"tool_calls": [_call("doc_search", query="x")]}, {"content": "I could not look it up"}],
    )

    def refuse(*_a: Any, **_k: Any):
        raise ToolError("the corpus is not ingested; run `make ingest`")

    monkeypatch.setattr(T, "doc_search", refuse)
    out = C.chain("q")

    assert out.steps[0].error.startswith("ToolError:")
    assert out.tools_used == []  # an errored step is not a tool successfully chained
    first_tool = next(m for m in asks[1]["messages"] if m.get("role") == "tool")
    payload = json.loads(first_tool["content"])
    assert "make ingest" in payload["error"]


def test_arguments_arriving_as_a_json_string_are_parsed(monkeypatch):
    """Ollama returns arguments as an object; other servers send a JSON string.
    Both have to reach the tool as a dict, and unparseable text must not crash."""
    assert C._as_dict('{"query": "x"}') == {"query": "x"}
    assert C._as_dict("not json") == {"_raw": "not json"}
    assert C._as_dict("[1, 2]") == {"_raw": "[1, 2]"}
    assert C._as_dict(None) == {}
    assert C._as_dict({"a": 1}) == {"a": 1}


# -- dispatch ------------------------------------------------------------------


def test_the_model_may_not_choose_the_match_tolerance(monkeypatch):
    """The radius is the whole boundary between matched and dark — 45 matched at
    500 m, 8 at 50 m over the same pixels. A model that could set it would be
    choosing its own evidence threshold mid-answer, so the dispatch discards it."""
    seen: dict[str, Any] = {}

    def fake_match(ids, window, radius):
        seen.update(ids=ids, window=window, radius=radius)
        return [Match(detection_id=ids[0], status="dark")]

    monkeypatch.setattr(T, "ais_match", fake_match)
    monkeypatch.setattr(C, "_scene_totals", lambda ids: {"s1": 1})
    monkeypatch.setattr(settings, "match_radius_m", 500.0)
    monkeypatch.setattr(settings, "match_window_min", 11.0)

    out = C._dispatch("ais_match", {"detections": ["s1:0"], "radius_m": 50.0, "time_window_min": 1})

    assert seen["radius"] == 500.0 and seen["window"] == 11
    assert "500 m and ±11 min — the validated setting, fixed" in out["match_tolerance"]


def test_an_unknown_tool_names_the_ones_that_exist():
    with pytest.raises(ToolError) as exc:
        C._dispatch("delete_everything", {})
    message = str(exc.value)
    assert "no tool named 'delete_everything'" in message
    for name in ("stac_search", "detect_vessels", "ais_match", "doc_search", "correlate"):
        assert name in message


def test_status_is_dispatched_to_the_mcp_servers_own_implementation():
    """The local model and Claude Desktop read the same text — the same function,
    not a second copy of it."""
    from nightglass.mcp.server import nightglass_status

    assert C._dispatch("nightglass_status", {}) == nightglass_status()


def test_scene_totals_asks_once_for_the_distinct_scenes(monkeypatch):
    """Sixty detection ids over two scenes are two scene ids, sorted, in one
    query — the totals are what makes `partial` decidable."""
    captured: dict[str, Any] = {}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql, params=None):
            captured.update(sql=sql, params=params)

        def fetchall(self):
            return [{"scene_id": "s1", "n": 60}, {"scene_id": "s2", "n": 4}]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("nightglass.spatial.db.connect", lambda *a, **k: _Conn())
    totals = C._scene_totals(["s2:1", "s1:0", "s1:9", "s2:1"])

    assert captured["params"] == (["s1", "s2"],)
    assert totals == {"s1": 60, "s2": 4}


def test_scene_totals_does_not_open_a_connection_for_unparseable_ids(monkeypatch):
    def no_connect(*_a: Any, **_k: Any):
        raise AssertionError("no scene ids means no query")

    monkeypatch.setattr("nightglass.spatial.db.connect", no_connect)
    assert C._scene_totals(["nonsense", ""]) == {}


# -- the compaction the model is shown -----------------------------------------


def test_a_detection_sample_says_it_is_not_the_working_set():
    """Measured failure: a bare `sample` of ten next to the full id list was used
    as the working list and a scene-wide conclusion drawn from the last batch. The
    key name and the payload's own instruction are the fix."""
    out = C._detections(_detections(60))
    assert out["count"] == 60
    assert len(out["detection_ids"]) == 60
    assert len(out["positions_sample_not_the_working_set"]) == C.SAMPLE
    assert "call ais_match once with ALL 60 ids" in out["next_step"]


def test_matching_a_subset_says_so_loudly():
    """Asked about a slice, the tool answers correctly about the slice and nothing
    in the answer says it was one — unless `partial` and its warning are there."""
    matches = [Match(detection_id="s1:0", status="dark")]
    out = C._matches(matches, {"s1": 60}, 500.0, 11.0)

    assert out["partial"] is True
    assert out["requested"] == 1
    assert "not the 60 in the scene(s)" in out["warning"]
    assert out["unmatched_detection_ids"] == ["s1:0"]
    assert out["may_state_a_rate"] is False


def test_a_complete_ais_match_carries_no_warning():
    matches = [
        Match(detection_id="s1:0", status="dark"),
        Match(detection_id="s1:1", status="matched", mmsi="219000000", distance_m=120.4),
    ]
    out = C._matches(matches, {"s1": 2}, 500.0, 11.0)

    assert out["partial"] is False
    assert "warning" not in out
    assert out["matched_count"] == 1 and out["unmatched_count"] == 1
    assert out["sample"][1]["distance_m"] == 120  # rounded for display, ids are not
    assert out["may_state_a_rate"] is False  # never true on this surface


def test_the_correlation_payload_carries_the_rate_verdicts_reasons():
    correlation = CorrelationResult(
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
        detections=_detections(3),
        matches=[
            Match(detection_id="s1:0", status="matched", mmsi="1", source_is_ground_truth=True),
            Match(detection_id="s1:1", status="dark"),
        ],
    )
    out = C._correlation(correlation)

    assert out["detection_count"] == 3
    assert out["matched_count"] == 1 and out["unmatched_count"] == 1
    assert out["unmatched_detection_ids"] == ["s1:1"]
    assert out["may_state_a_rate"] is False
    assert any("precision is not validated" in r for r in out["why_not"])
    assert out["correlated_scene"] == "s1"


def test_scene_compaction_keeps_the_identifiers_a_next_call_needs():
    out = C._scenes(
        [
            Scene(
                id="s1",
                acquisition_time=datetime(2026, 7, 17, 5, 23, 24, tzinfo=UTC),
                mode="IW",
                polarizations=["VV", "VH"],
                footprint_wkt="POLYGON((10 55, 12 55, 12 57, 10 57, 10 55))",
            )
        ]
    )
    assert out["scenes"][0]["scene_id"] == "s1"
    assert out["scenes"][0]["acquisition_time"] == "2026-07-17T05:23:24Z"


def test_chunk_compaction_keeps_the_citation_handles():
    out = C._chunks([Chunk(doc_id="d", chunk_id="d#3", text="t", score=0.4, title="Ti")])
    assert out["chunks"][0]["chunk_id"] == "d#3"
    assert out["chunks"][0]["classification"] == "UNCLASSIFIED"


def test_the_one_line_summary_prefers_counts_over_a_json_dump():
    assert C._one_line({"count": 3, "text": "x" * 500}) == "count=3"
    assert C._one_line({"matched_count": 1, "unmatched_count": 2}) == (
        "matched_count=1, unmatched_count=2"
    )
    assert len(C._one_line({"other": "x" * 500})) <= 120


def test_the_transcript_wraps_a_long_call_rather_than_slicing_it(monkeypatch):
    result = C.ChainResult(question="q", answer="a", model="m")
    result.steps = [
        C.Step(tool="correlate", arguments={"bbox": [-10.5, 38.0, -8.5, 39.5], "start": "x" * 90}),
        C.Step(tool="correlate", arguments={"bbox": [-10.5, 38.0, -8.5, 39.5], "start": "x" * 90},
               repeated=True),
        C.Step(tool="doc_search", arguments={"query": "x"}, error="ToolError: down"),
    ]
    text = result.render()

    assert "x" * 90 in text  # the argument is wrapped onto its own line, never cut
    assert "…" not in text and "..." not in text
    assert "ERROR  ToolError: down" in text
    assert "REPEAT  same tool, same arguments" in text
    assert "distinct tools chained  1  (correlate)" in text  # the errored step is not one


# -- the CLI -------------------------------------------------------------------


def test_list_names_every_tool_and_the_active_aoi(capsys):
    assert CLI.cmd_list(None) == 0
    out = capsys.readouterr().out
    for name, _sig, _what in CLI._SIGNATURES:
        assert name in out
    assert "kattegat" in out


def test_call_refuses_a_name_that_is_not_a_tool(capsys):
    import argparse

    rc = CLI.cmd_call(argparse.Namespace(tool="document_index", json=""))
    err = capsys.readouterr().err
    assert rc == 2
    assert "no tool 'document_index'" in err
    assert "draft_intrep" in err


def test_call_serialises_a_list_and_a_single_model_the_same_way(monkeypatch, capsys):
    import argparse

    monkeypatch.setattr(
        T, "doc_search", lambda **kw: [Chunk(doc_id="d", chunk_id="d#1", text="t", score=0.5)]
    )
    assert CLI.cmd_call(argparse.Namespace(tool="doc_search", json='{"query": "x"}')) == 0
    body = json.loads(capsys.readouterr().out)
    assert body[0]["chunk_id"] == "d#1"

    monkeypatch.setattr(
        T,
        "correlate",
        lambda **kw: CorrelationResult(
            aoi_name="kattegat",
            bbox=[1, 2, 3, 4],
            start=datetime(2026, 7, 17, tzinfo=UTC),
            end=datetime(2026, 7, 18, tzinfo=UTC),
        ),
    )
    assert CLI.cmd_call(argparse.Namespace(tool="correlate", json='{"bbox": [1,2,3,4]}')) == 0
    assert json.loads(capsys.readouterr().out)["aoi_name"] == "kattegat"


def test_chain_passes_when_three_distinct_tools_were_chained(monkeypatch, capsys):
    """§M4's bar, reported as an exit code rather than left for a reader to count
    off the transcript."""
    import argparse

    result = C.ChainResult(question="q", answer="a", model="m")
    result.steps = [
        C.Step(tool="stac_search", arguments={}),
        C.Step(tool="detect_vessels", arguments={}),
        C.Step(tool="ais_match", arguments={}),
    ]
    monkeypatch.setattr("nightglass.tools.chaining.chain", lambda *a, **k: result)
    rc = CLI.cmd_chain(argparse.Namespace(question=["q"], max_iterations=8, verbose=False))
    assert rc == 0
    assert "PASS" in capsys.readouterr().out


def test_chain_fails_below_the_bar(monkeypatch, capsys):
    import argparse

    result = C.ChainResult(question="q", answer="a", model="m")
    result.steps = [C.Step(tool="stac_search", arguments={})]
    monkeypatch.setattr("nightglass.tools.chaining.chain", lambda *a, **k: result)
    rc = CLI.cmd_chain(argparse.Namespace(question=["q"], max_iterations=8, verbose=False))
    assert rc == 1
    assert "BELOW BAR" in capsys.readouterr().out


def test_chain_refuses_an_empty_question(capsys):
    import argparse

    assert CLI.cmd_chain(argparse.Namespace(question=["  "], max_iterations=8, verbose=False)) == 2


def test_a_tool_error_prints_a_message_and_an_unexpected_error_keeps_its_traceback(
    monkeypatch, capsys
):
    """A ToolError always names what would fix it, so a traceback adds nothing.
    Anything else is a bug, and swallowing its traceback would hide it."""
    monkeypatch.setattr(T, "doc_search", lambda **kw: (_ for _ in ()).throw(ToolError("run `make ingest`")))
    assert CLI.main(["call", "doc_search", "--json", '{"query": "x"}']) == 1
    assert "run `make ingest`" in capsys.readouterr().err

    monkeypatch.setattr(T, "doc_search", lambda **kw: (_ for _ in ()).throw(KeyError("boom")))
    with pytest.raises(KeyError):
        CLI.main(["call", "doc_search", "--json", '{"query": "x"}'])


def test_main_requires_a_subcommand():
    with pytest.raises(SystemExit):
        CLI.main([])
