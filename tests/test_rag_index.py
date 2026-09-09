"""The index, the embedder and the grounded answer, against a real engine.

`QdrantClient(":memory:")` is Qdrant's own local mode — a real engine in this
process, not a stand-in — so ingest idempotency, payload filtering and the score
floor are the database's behaviour rather than a mock's. Only the embedder is
replaced, by a deterministic hashing one: bge-m3 needs a GPU and the properties
under test need vectors that are stable and separable, which is a different
requirement from being good.

`answer_question` is driven with a scripted model over `httpx.Client.post`. What
is pinned there is the one guarantee in the module: the model saying
`supported: true` does not make an answer supported.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest
from qdrant_client import QdrantClient

from nightglass.rag import answer as ANS
from nightglass.rag import cli as RCLI
from nightglass.rag import index as IDX
from nightglass.rag.documents import Document
from nightglass.rag.embed import DIMENSIONS, Embedder, EmbeddingError
from nightglass.schemas import Chunk, GroundedAnswer


class HashingEmbedder:
    """Deterministic bag-of-words vectors. Same text -> same vector, and two
    texts sharing words are closer than two that share none."""

    def embed(self, texts: list[str], *, progress: bool = False) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in text.lower().split():
            digest = hashlib.sha1(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0
        if not any(vector):
            vector[0] = 1.0
        return vector


@pytest.fixture
def index(monkeypatch) -> IDX.DocumentIndex:
    """A DocumentIndex over Qdrant's in-process local mode.

    `DocumentIndex.__init__` builds `QdrantClient(url=…, timeout=…)`, and
    `url=":memory:"` is not a URL — urllib3 refuses it. Local mode is reached by
    the positional form, so the constructor is redirected here and everything
    below runs against the real engine.
    """
    monkeypatch.setattr(IDX, "QdrantClient", lambda url, timeout=None: QdrantClient(url))
    return IDX.DocumentIndex(url=":memory:", embedder=HashingEmbedder(), collection="test_docs")


def _doc(doc_id: str, text: str, **kw: Any) -> Document:
    meta = {
        "title": doc_id.replace("-", " "),
        "classification": "UNCLASSIFIED",
        "publisher": "IMO",
        "doc_type": "regulation",
        "language": "en",
        "origin": "real",
    }
    meta.update(kw)
    return Document(doc_id=doc_id, text=text, **meta)  # type: ignore[arg-type]


DARK = _doc(
    "dark-vessels",
    "# Dark vessels\n\nA dark vessel is one whose AIS transponder is not "
    "reporting when a radar detection places it at sea.\n",
)
SEARCH = _doc(
    "search-rescue",
    "# Search and rescue\n\nA maritime rescue coordination centre tasks assets "
    "when a distress alert is received.\n",
    publisher="IMRF",
    language="pt",
    classification="UNCLASSIFIED // SYNTHETIC",
    origin="synthetic",
)


# -- ingest --------------------------------------------------------------------


def test_re_ingesting_an_unchanged_document_overwrites_rather_than_duplicates(index):
    """Point ids are uuid5 of a positional chunk id, so `make ingest` is safe to
    run after every corpus edit — which it will be."""
    first = index.ingest([DARK])
    after_first = index.count()
    second = index.ingest([DARK])

    assert after_first == first.chunks
    assert index.count() == after_first
    assert second.chunks == first.chunks


def test_the_report_counts_documents_chunks_and_characters(index):
    report = index.ingest([DARK, SEARCH])
    assert report.documents == 2
    assert report.chunks == index.count()
    assert report.characters > 0
    assert set(report.by_publisher) == {"IMO", "IMRF"}
    assert "mean chunk" in report.render()


def test_a_document_with_no_text_is_skipped_and_named(index):
    report = index.ingest([_doc("empty", "   ")])
    assert report.documents == 0
    assert report.skipped == ["empty (no text after chunking)"]
    assert index.count() == 0


def test_counting_an_index_that_was_never_created_is_zero_not_an_error(index):
    assert index.count() == 0
    assert index.stats() == {"collection": "test_docs", "exists": False, "chunks": 0}


def test_recreating_the_collection_empties_it(index):
    index.ingest([DARK])
    assert index.count() > 0
    index.ensure_collection(recreate=True)
    assert index.count() == 0
    assert index.stats()["exists"] is True
    assert index.stats()["dimensions"] == DIMENSIONS


# -- retrieval -----------------------------------------------------------------


def test_search_returns_chunks_carrying_the_fields_a_citation_needs(index):
    index.ingest([DARK, SEARCH])
    hits = index.search("dark vessel AIS transponder", k=1)

    assert len(hits) == 1
    assert hits[0].doc_id == "dark-vessels"
    assert hits[0].chunk_id.startswith("dark-vessels#")
    assert hits[0].classification == "UNCLASSIFIED"
    assert hits[0].title == "dark vessels"
    assert hits[0].score > 0


def test_a_filter_restricts_the_search_to_matching_payloads(index):
    index.ingest([DARK, SEARCH])
    assert [c.doc_id for c in index.search("vessel", filters={"language": "pt"})] == [
        "search-rescue"
    ]
    assert [c.doc_id for c in index.search("vessel", filters={"publisher": "IMO"})] == [
        "dark-vessels"
    ]


def test_a_list_valued_filter_means_any_of(index):
    index.ingest([DARK, SEARCH])
    hits = index.search("vessel", filters={"publisher": ["IMO", "IMRF"]})
    assert {c.doc_id for c in hits} == {"dark-vessels", "search-rescue"}


def test_a_score_floor_can_refuse_everything(index):
    """The floor is left unset by default precisely because a wrong one turns
    answerable questions into refusals — which looks like the honesty feature
    working. It has to be a real cut, so it is tested as one."""
    index.ingest([DARK])
    assert index.search("dark vessel", min_score=0.0) != []
    assert index.search("dark vessel", min_score=0.999999) == []


def test_a_none_valued_filter_key_is_ignored():
    assert IDX._build_filter(None) is None
    assert IDX._build_filter({}) is None
    assert IDX._build_filter({"language": None}) is None
    built = IDX._build_filter({"language": "pt", "aoi": ["kattegat", "lisbon"]})
    assert built is not None and len(built.must) == 2


def test_facets_count_every_value_including_list_valued_payloads(index):
    index.ingest([DARK, SEARCH, _doc("third", "# T\n\nvessel text\n", publisher="IMO")])
    by_publisher = index.facet("publisher")

    assert sum(by_publisher.values()) == index.count()
    assert next(iter(by_publisher)) == "IMO"  # ordered by count, descending
    assert set(index.facet("origin")) == {"real", "synthetic"}


def test_a_chunk_is_built_even_from_a_payload_missing_every_optional_field():
    point = type("P", (), {"payload": {}, "score": 0.5})()
    chunk = IDX._to_chunk(point)
    assert chunk.classification == "UNCLASSIFIED"
    assert chunk.source_url is None and chunk.doc_id == ""


# -- the embedder --------------------------------------------------------------


def _post_returning(*payloads: Any, status: int = 200):
    """Script `httpx.Client.post`. Each call takes the next scripted response."""
    queue = list(payloads)

    def fake_post(self, url, json=None, **kw):
        body = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(body, int):
            return httpx.Response(body, request=httpx.Request("POST", url))
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    return fake_post


def _vectors(n: int, dims: int = DIMENSIONS) -> dict[str, Any]:
    return {"embeddings": [[0.1] * dims for _ in range(n)]}


def test_embedding_nothing_makes_no_request(monkeypatch):
    def no_post(*_a: Any, **_k: Any):
        raise AssertionError("an empty batch must not reach ollama")

    monkeypatch.setattr(httpx.Client, "post", no_post)
    assert Embedder(host="http://ollama:11434", model="bge-m3").embed([]) == []


def test_texts_are_split_into_batches_of_the_configured_size(monkeypatch):
    sizes: list[int] = []

    def fake_post(self, url, json=None, **kw):
        sizes.append(len(json["input"]))
        return httpx.Response(200, json=_vectors(len(json["input"])), request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    out = Embedder(host="h", model="m", batch_size=16).embed(["t"] * 35)

    assert sizes == [16, 16, 3]
    assert len(out) == 35


def test_a_wrong_dimension_fails_loudly_rather_than_at_the_qdrant_boundary(monkeypatch):
    """A different dimension means a different model, and a collection built at
    1024 cannot accept vectors of 768. Better here than halfway through an ingest."""
    monkeypatch.setattr(httpx.Client, "post", _post_returning(_vectors(1, dims=768)))
    monkeypatch.setattr("time.sleep", lambda _s: None)

    with pytest.raises(EmbeddingError, match="Wrong embedding model"):
        Embedder(host="h", model="m", retries=1).embed(["t"])


def test_a_short_batch_of_vectors_is_a_failure_not_a_partial_result(monkeypatch):
    monkeypatch.setattr(httpx.Client, "post", _post_returning({"embeddings": [[0.1] * DIMENSIONS]}))
    monkeypatch.setattr("time.sleep", lambda _s: None)

    with pytest.raises(EmbeddingError, match="expected 2 embeddings, got 1"):
        Embedder(host="h", model="m", retries=1).embed(["a", "b"])


def test_a_failing_call_is_retried_and_then_reported_with_the_url(monkeypatch):
    calls = {"n": 0}

    def failing(self, url, json=None, **kw):
        calls["n"] += 1
        return httpx.Response(500, request=httpx.Request("POST", url))

    slept: list[float] = []
    monkeypatch.setattr(httpx.Client, "post", failing)
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    with pytest.raises(EmbeddingError, match="after 3 attempts against http://h/api/embed"):
        Embedder(host="http://h", model="m", retries=3).embed(["t"])

    assert calls["n"] == 3
    assert slept == [1, 2]  # backoff between attempts, none after the last


def test_a_recovered_call_returns_without_raising(monkeypatch):
    responses = [500, _vectors(1)]

    def flaky(self, url, json=None, **kw):
        body = responses.pop(0)
        if isinstance(body, int):
            return httpx.Response(body, request=httpx.Request("POST", url))
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", flaky)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    assert len(Embedder(host="h", model="m").embed_one("t")) == DIMENSIONS


# -- the grounded answer -------------------------------------------------------


def _chunks(n: int, chars: int = 100) -> list[Chunk]:
    return [
        Chunk(doc_id="d", chunk_id=f"d#{i}", text="x" * chars, score=1.0 - i / 100, title="T")
        for i in range(n)
    ]


def test_the_context_budget_excludes_a_whole_chunk_rather_than_cutting_one():
    """A chunk cut mid-sentence would be cited as if it said the whole thing."""
    context = ANS.build_context(_chunks(5, chars=100), max_chars=250)
    assert context.count("---") == 1  # two chunks fitted, the third did not
    assert "[d#2]" not in context
    assert "x" * 100 in context


def test_the_context_labels_each_chunk_with_its_id_and_marking():
    context = ANS.build_context(
        [Chunk(doc_id="d", chunk_id="d#0", text="body", score=0.5,
               classification="UNCLASSIFIED // SYNTHETIC", title="Ti")]
    )
    assert context.startswith("[d#0] (UNCLASSIFIED // SYNTHETIC) Ti\nbody")


class _Index:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.asked: dict[str, Any] = {}

    def search(self, query, *, k=8, filters=None, min_score=None):
        self.asked.update(query=query, k=k, filters=filters, min_score=min_score)
        return self.chunks


def _answering(monkeypatch, content: str) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def fake_post(self, url, json=None, **kw):
        sent.append(json)
        return httpx.Response(
            200, json={"message": {"content": content}}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    return sent


def test_nothing_retrieved_is_a_refusal_before_the_model_is_asked(monkeypatch):
    def no_post(*_a: Any, **_k: Any):
        raise AssertionError("with no context there is nothing to ground an answer in")

    monkeypatch.setattr(httpx.Client, "post", no_post)
    out = ANS.answer_question("q", index=_Index([]), ollama_host="http://h", chat_model="m")

    assert out.answered is False
    assert out.refusal == ANS.REFUSAL_TEXT
    assert out.retrieved == []


def test_the_model_calling_itself_supported_is_not_decisive(monkeypatch):
    """Every citation is checked against what was actually retrieved. A model
    that cites nothing real has not produced an answer, whatever it says about
    itself — this is the check that makes the prompt's request enforceable."""
    chunks = _chunks(2)
    _answering(
        monkeypatch,
        json.dumps({"supported": True, "claims": [{"text": "Invented.", "chunk_ids": ["d#99"]}]}),
    )
    out = ANS.answer_question("q", index=_Index(chunks), ollama_host="h", chat_model="m")

    assert out.answered is False
    assert out.claims == []
    assert out.dropped_citations == ["d#99"]
    assert out.refusal == ANS.REFUSAL_TEXT


def test_a_supported_answer_keeps_only_the_verified_citations(monkeypatch):
    chunks = _chunks(2)
    _answering(
        monkeypatch,
        json.dumps(
            {
                "supported": True,
                "claims": [{"text": "Real claim.", "chunk_ids": ["d#0", "d#42"]}],
            }
        ),
    )
    out = ANS.answer_question("q", index=_Index(chunks), ollama_host="h", chat_model="m")

    assert out.answered is True
    assert out.claims[0].chunk_ids == ["d#0"]
    assert out.dropped_citations == ["d#42"]
    assert out.cited_documents == ["d"]


def test_output_that_is_not_json_is_a_refusal_not_a_crash(monkeypatch):
    _answering(monkeypatch, "I think the answer is probably yes")
    out = ANS.answer_question("q", index=_Index(_chunks(1)), ollama_host="h", chat_model="m")

    assert out.answered is False
    assert out.retrieved != []  # what was retrieved is still reported


def test_the_retrieval_arguments_reach_the_index(monkeypatch):
    _answering(monkeypatch, json.dumps({"supported": False, "claims": []}))
    index = _Index(_chunks(1))
    ANS.answer_question(
        "q", index=index, ollama_host="h", chat_model="m", k=3,
        filters={"language": "pt"}, min_score=0.4,
    )
    assert index.asked == {"query": "q", "k": 3, "filters": {"language": "pt"}, "min_score": 0.4}


def test_the_grounded_call_constrains_the_model_to_the_citation_schema(monkeypatch):
    sent = _answering(monkeypatch, json.dumps({"supported": False, "claims": []}))
    ANS.answer_question("q", index=_Index(_chunks(1)), ollama_host="h", chat_model="m")

    assert sent[0]["format"] == ANS._SCHEMA
    assert sent[0]["options"]["temperature"] == 0
    assert "CONTEXT:" in sent[0]["messages"][1]["content"]


def test_the_ungrounded_answer_passes_through_with_no_schema_and_no_check(monkeypatch):
    """Deliberately the 'before' half of §M2's contrast: no retrieval, no schema,
    no verification — which is exactly what makes the comparison worth showing."""
    sent = _answering(monkeypatch, "  a confident paragraph  ")
    out = ANS.answer_ungrounded("what is a dark vessel?", ollama_host="h", chat_model="m")

    assert out == "a confident paragraph"
    assert "format" not in sent[0]
    assert len(sent[0]["messages"]) == 1


# -- the corpus CLI ------------------------------------------------------------


def test_filters_parse_into_a_dict_and_repeat_into_a_list():
    assert RCLI._filters(None) is None
    assert RCLI._filters(["language=pt"]) == {"language": "pt"}
    assert RCLI._filters(["origin=real", "origin=synthetic", "language=en"]) == {
        "origin": ["real", "synthetic"],
        "language": "en",
    }


def test_a_filter_without_an_equals_sign_is_refused():
    with pytest.raises(SystemExit, match="--filter expects key=value"):
        RCLI._filters(["language"])


def _corpus(tmp_path, doc_id: str = "dark-vessels") -> Any:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / f"{doc_id}.md").write_text(
        "---\n"
        f"doc_id: {doc_id}\n"
        "title: Dark vessels\n"
        "classification: UNCLASSIFIED\n"
        "publisher: IMO\n"
        "---\n\n"
        "# Dark vessels\n\nA dark vessel is one whose AIS is not reporting.\n",
        encoding="utf-8",
    )
    return root


def test_ingest_reads_the_corpus_off_disk_and_indexes_it(tmp_path, monkeypatch, index, capsys):
    import argparse

    monkeypatch.setattr(RCLI, "_index", lambda: index)
    root = _corpus(tmp_path)

    rc = RCLI.cmd_ingest(argparse.Namespace(root=[str(root)], recreate=True, verbose=False))
    out = capsys.readouterr().out

    assert rc == 0
    assert index.count() > 0
    assert "documents  1" in out
    assert "by publisher: IMO=1" in out


def test_ingest_says_what_to_run_when_the_corpus_is_missing(tmp_path, monkeypatch, capsys):
    import argparse

    def no_index():
        raise AssertionError("nothing to index means no connection to Qdrant")

    monkeypatch.setattr(RCLI, "_index", no_index)
    rc = RCLI.cmd_ingest(
        argparse.Namespace(root=[str(tmp_path / "nothing")], recreate=False, verbose=False)
    )
    assert rc == 1
    assert "make fetch-corpus" in capsys.readouterr().err


def test_search_prints_the_chunk_id_and_score_it_would_be_cited_by(
    tmp_path, monkeypatch, index, capsys
):
    import argparse

    monkeypatch.setattr(RCLI, "_index", lambda: index)
    index.ingest([DARK])
    rc = RCLI.cmd_search(
        argparse.Namespace(query="dark vessel", k=3, filter=None, min_score=None, chars=40)
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "dark-vessels#" in out


def test_search_says_so_when_nothing_comes_back(monkeypatch, index, capsys):
    import argparse

    monkeypatch.setattr(RCLI, "_index", lambda: index)
    index.ingest([DARK])
    RCLI.cmd_search(
        argparse.Namespace(query="dark vessel", k=3, filter=["language=zz"], min_score=None,
                           chars=40)
    )
    assert "no chunks retrieved" in capsys.readouterr().out


def test_stats_reports_the_facets_of_what_is_indexed(monkeypatch, index, capsys):
    import argparse

    monkeypatch.setattr(RCLI, "_index", lambda: index)
    index.ingest([DARK, SEARCH])
    RCLI.cmd_stats(argparse.Namespace())
    out = capsys.readouterr().out

    assert "chunks" in out
    assert "IMO" in out and "IMRF" in out
    assert "synthetic" in out


def test_ask_prints_a_refusal_and_the_dropped_citations(monkeypatch, index, capsys):
    import argparse

    monkeypatch.setattr(RCLI, "_index", lambda: index)
    monkeypatch.setattr(
        "nightglass.rag.answer.answer_question",
        lambda q, **kw: GroundedAnswer(
            question=q,
            answered=False,
            refusal=ANS.REFUSAL_TEXT,
            retrieved=_chunks(2),
            dropped_citations=["d#99"],
        ),
    )
    rc = RCLI.cmd_ask(
        argparse.Namespace(question="q", k=8, filter=None, min_score=None, sources=False,
                           ungrounded=False)
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert ANS.REFUSAL_TEXT in out
    assert "2 chunk(s) were retrieved but none supported an answer" in out
    assert "dropped 1 citation(s)" in out


def test_ask_ungrounded_takes_the_other_path_entirely(monkeypatch, capsys):
    import argparse

    monkeypatch.setattr(
        "nightglass.rag.answer.answer_question",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("--ungrounded must not retrieve")),
    )
    monkeypatch.setattr("nightglass.rag.answer.answer_ungrounded", lambda q, **kw: "priors only")

    rc = RCLI.cmd_ask(
        argparse.Namespace(question="q", k=8, filter=None, min_score=None, sources=False,
                           ungrounded=True)
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "UNGROUNDED — no retrieval, model priors only" in out
    assert "priors only" in out


def test_fetch_reports_a_failure_as_a_message_and_a_non_zero_exit(monkeypatch, capsys):
    import argparse

    from nightglass.rag.fetch import FetchError

    monkeypatch.setattr(
        "nightglass.rag.fetch.fetch_corpus",
        lambda **kw: (_ for _ in ()).throw(FetchError("8 of 39 document(s) did not arrive")),
    )
    rc = RCLI.cmd_fetch(
        argparse.Namespace(sources="corpus/sources.yaml", out=None, force=False, only=None)
    )
    assert rc == 1
    assert "8 of 39 document(s) did not arrive" in capsys.readouterr().err
