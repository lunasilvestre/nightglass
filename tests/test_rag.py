"""M2 unit tests. No network, no GPU, no Qdrant — `make test` runs with
`--network none`, so anything here that needed a service would be a test that
cannot run.

What is worth testing is the logic that decides whether an answer may be
published: front-matter and marking parsing, classification propagation, chunk
identity, and citation verification. The retrieval quality itself is not
unit-testable and is demonstrated by `make rag-proof` instead.
"""

from __future__ import annotations

import pytest

from nightglass.rag.answer import combined_marking, verify_claims
from nightglass.rag.chunking import chunk_document, split_blocks
from nightglass.rag.documents import CorpusError, Document, Marking, parse_document
from nightglass.rag.extract import _is_caps_heading, _rewrap, _strip_running_headers
from nightglass.schemas import Chunk

# ---------------------------------------------------------------------------
# front-matter and markings
# ---------------------------------------------------------------------------

MEMO = """\
---
doc_id: intrep-test-001
title: A Test Memo
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: test
doc_type: intrep
language: en
aoi: [lisbon]
---

UNCLASSIFIED // SYNTHETIC

# Heading One

Body of the first section.

## Sub A

More text.
"""


def test_parse_document_reads_front_matter():
    doc = parse_document(MEMO)
    assert doc.doc_id == "intrep-test-001"
    assert doc.classification == "UNCLASSIFIED // SYNTHETIC"
    assert doc.aoi == ("lisbon",)
    assert doc.is_synthetic
    assert doc.text.startswith("UNCLASSIFIED // SYNTHETIC")


@pytest.mark.parametrize(
    "raw, why",
    [
        ("no front matter at all", "missing delimiters"),
        ("---\ndoc_id: x\n---\n\nbody", "missing title and classification"),
        ("---\ndoc_id: x\ntitle: t\nclassification: BANANA\n---\n\nbody", "unknown level"),
        ("---\ndoc_id: x\ntitle: t\nclassification: UNCLASSIFIED\n---\n\n", "empty body"),
    ],
)
def test_parse_document_rejects_malformed(raw, why):
    with pytest.raises(CorpusError):
        parse_document(raw)


def test_marking_combine_keeps_caveats():
    """A report citing synthetic material says so, even if most sources are real.

    This is §7's propagation requirement. It is computed rather than left to a
    drafter, because the failure mode -- an UNCLASSIFIED report quietly built on
    synthetic sources -- is invisible in the output.
    """
    combined = Marking.combine(
        [Marking.parse("UNCLASSIFIED"), Marking.parse("UNCLASSIFIED // SYNTHETIC")]
    )
    assert str(combined) == "UNCLASSIFIED // SYNTHETIC"


def test_marking_combine_takes_the_highest_level():
    combined = Marking.combine([Marking.parse("UNCLASSIFIED"), Marking.parse("SECRET // NOFORN")])
    assert str(combined) == "SECRET // NOFORN"


def test_marking_combine_of_nothing_is_unclassified():
    assert str(Marking.combine([])) == "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------


def _doc(text: str, doc_id: str = "d1") -> Document:
    return Document(
        doc_id=doc_id,
        title="T",
        text=text,
        classification="UNCLASSIFIED",
        publisher="p",
        doc_type="t",
        language="en",
    )


def test_split_blocks_tracks_heading_path():
    blocks = split_blocks("# A\n\npara one\n\n## B\n\npara two\n\n# C\n\npara three")
    assert [b.heading_path for b in blocks] == [("A",), ("A", "B"), ("C",)]


def test_split_blocks_keeps_a_fenced_block_whole():
    blocks = split_blocks("# A\n\n```\nline1\n\nline2\n```\n\nafter")
    fenced = [b for b in blocks if b.text.startswith("```")]
    assert len(fenced) == 1
    assert "line1" in fenced[0].text and "line2" in fenced[0].text


def test_chunk_ids_are_stable_and_positional():
    """Re-ingest must update points in place, not duplicate them.

    Point IDs derive from chunk IDs, so a random or content-hashed chunk ID
    would orphan every citation already written down the moment a typo upstream
    was fixed.
    """
    text = "# A\n\n" + ("sentence. " * 200) + "\n\n## B\n\n" + ("other. " * 200)
    first = chunk_document(_doc(text))
    second = chunk_document(_doc(text))
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert first[0].chunk_id == "d1#0000"
    assert all(c.chunk_id == f"d1#{c.ordinal:04d}" for c in first)


def test_chunking_respects_the_size_budget():
    text = "# A\n\n" + "\n\n".join(f"Paragraph number {i}. " * 12 for i in range(40))
    chunks = chunk_document(_doc(text), max_chars=1500)
    assert len(chunks) > 1
    # The budget may be exceeded only by an indivisible block, and _hard_split
    # guarantees there are none above the limit.
    assert max(len(c.text) for c in chunks) <= 1500


def test_oversized_single_block_is_split_not_dropped():
    text = "# A\n\n" + ("word " * 2000)
    chunks = chunk_document(_doc(text), max_chars=800)
    assert len(chunks) > 1
    assert sum(len(c.text) for c in chunks) >= 0.9 * len(text.split("# A\n\n")[1])


def test_embedding_input_carries_title_and_heading_but_text_does_not():
    """Scaffolding helps the retriever and would mislead an analyst.

    The heading path materially improves retrieval of short sections, but a
    chunk shown as the evidence for a claim must contain the source's words and
    not ours.
    """
    chunks = chunk_document(_doc("# Section One\n\nThe body sentence."))
    c = chunks[0]
    assert "Section One" not in c.text
    assert "Section One" in c.embedding_input(title="My Title")
    assert "My Title" in c.embedding_input(title="My Title")


# ---------------------------------------------------------------------------
# PDF normalisation
# ---------------------------------------------------------------------------


_PROSE = [
    "The officer of the watch should remain aware of surrounding traffic.",
    "Transponder output is not a substitute for visual lookout.",
    "Coastal stations receive within line of sight and no further.",
    "Vessels below the carriage threshold transmit nothing at all.",
    "Sea state raises the background and the smallest targets drop first.",
    "Draught is entered by hand and is therefore frequently stale.",
    "Class B equipment yields its transmission slots to Class A.",
    "Interpolation to the acquisition instant beats nearest-in-time.",
]


_TOPICS = [
    "azimuth", "radiometry", "geolocation", "speckle",
    "incidence", "polarisation", "clutter", "revisit",
]


def _body(n: int) -> list[str]:
    """Page-specific prose, made distinct by WORDS rather than by numbers.

    Real body text differs from page to page, and that difference is the entire
    signal the header detector keys on. It has to differ lexically: the
    detector's key is digit-blind on purpose (so `L 229/2` and `L 229/3` collapse
    to one running head), which means text differing only in its numbers is
    indistinguishable from a header — see the test below that pins that down.
    """
    topic = _TOPICS[n % len(_TOPICS)]
    return [f"{s} Discussion of {topic} and {_TOPICS[j]} follows." for j, s in enumerate(_PROSE)]


def _page(n: int) -> str:
    """A page shaped like a real one: header, blank line, body, footer.

    The blank line matters — it is what the original bug tripped over.
    """
    return "\n\nA 29/Res.1106\nPage {}\n\n{}\n\nhttps://edocs.imo.org/doc.doc\n".format(
        n, "\n".join(_body(n))
    )


def test_running_headers_are_stripped_even_after_a_blank_line():
    """The bug this exists to prevent: counting margins over non-empty lines but
    removing against raw line indices left 17 copies of a page header in one IMO
    resolution, where they then competed with the content for retrieval."""
    out = _strip_running_headers([_page(i) for i in range(1, 11)])
    assert "A 29/Res.1106" not in out
    assert "edocs.imo.org" not in out
    assert _body(7)[4] in out


def test_running_header_stripping_keeps_body_text():
    pages = [_page(i) for i in range(8)]
    out = _strip_running_headers(pages)
    for i in range(8):
        for sentence in _body(i):
            assert out.count(sentence) == 1, f"lost or duplicated: {sentence}"


def test_lines_differing_only_in_digits_are_treated_as_one_running_head():
    """A known and deliberate cost of the digit-blind key.

    Collapsing digits is what catches `L 229/2`, `L 229/3`, `L 229/4` as a single
    running head — an exact-match count would see three distinct strings and
    strip none of them. The price is that text differing ONLY in its numbers
    looks the same to the detector. That is why the margin is capped at a third
    of the page: the mistake stays confined to the page edges, where headers
    live, instead of reaching body text.
    """
    pages = [f"L 229/{i}\nreal body sentence with words in it\nEN" for i in range(2, 12)]
    out = _strip_running_headers(pages)
    assert "L 229/" not in out
    assert out.count("real body sentence with words in it") == 10


def test_a_running_head_never_eats_more_than_a_third_of_a_page():
    """Body text that differs only in its numbers must survive.

    "Article 5" and "Article 6" normalise to the same digit-blind key, so on a
    short page — where an unbounded margin covers everything — a numbered list
    is indistinguishable from a running head. Capping the margin at a third of
    the page is what keeps that from deleting content.
    """
    pages = [f"HEAD\nArticle {i}\nThe text of article {i} follows here.\nFOOT" for i in range(8)]
    out = _strip_running_headers(pages)
    assert "HEAD" not in out and "FOOT" not in out
    for i in range(8):
        assert f"The text of article {i}" in out


@pytest.mark.parametrize(
    "line, expected",
    [
        ("INHERENT LIMITATIONS OF AIS", True),
        ("HAS ADOPTED THIS REGULATION:", True),
        ("PURPOSE", True),
        ("EN", False),  # too short — the Official Journal language marker
        ("The master should restart the AIS.", False),
        ("(a) SOME CAPS IN A LIST", False),
    ],
)
def test_caps_heading_detection(line, expected):
    assert _is_caps_heading(line) is expected


def test_rewrap_joins_wrapped_lines_and_dehyphenates():
    text = "These measures fall within the scope of the Treaty and, there­\nfore, apply imme­\ndiately."
    out = _rewrap(text)
    assert "therefore" in out
    assert "immediately" in out
    assert "\n" not in out.strip()


def test_rewrap_promotes_caps_headings_so_chunks_get_boundaries():
    out = _rewrap("PURPOSE\nThese Guidelines have been developed.\nCAUTION\nNot all ships carry AIS.")
    assert "## PURPOSE" in out
    assert "## CAUTION" in out
    blocks = split_blocks(out)
    assert ("PURPOSE",) in [b.heading_path for b in blocks]


# ---------------------------------------------------------------------------
# citation verification — the load-bearing check
# ---------------------------------------------------------------------------


def _chunk(cid: str, classification: str = "UNCLASSIFIED") -> Chunk:
    return Chunk(
        doc_id=cid.split("#")[0], chunk_id=cid, text="t", score=0.7, classification=classification
    )


RETRIEVED = [_chunk("doc-a#0000"), _chunk("doc-b#0001", "UNCLASSIFIED // SYNTHETIC")]


def test_fabricated_citations_are_dropped():
    claims, dropped = verify_claims(
        {"supported": True, "claims": [{"text": "x", "chunk_ids": ["doc-a#0000", "invented#9999"]}]},
        RETRIEVED,
    )
    assert len(claims) == 1
    assert claims[0].chunk_ids == ["doc-a#0000"]
    assert dropped == ["invented#9999"]


def test_a_claim_whose_every_citation_is_fabricated_is_removed():
    claims, dropped = verify_claims(
        {"supported": True, "claims": [{"text": "confident nonsense", "chunk_ids": ["nope#0000"]}]},
        RETRIEVED,
    )
    assert claims == []
    assert dropped == ["nope#0000"]


def test_a_claim_with_no_citations_at_all_is_removed():
    claims, _ = verify_claims(
        {"supported": True, "claims": [{"text": "unsourced assertion", "chunk_ids": []}]},
        RETRIEVED,
    )
    assert claims == []


def test_model_claiming_support_with_nothing_verifiable_is_a_refusal():
    """`supported: true` is the model's opinion of itself and is not decisive."""
    claims, _ = verify_claims(
        {"supported": True, "claims": [{"text": "x", "chunk_ids": ["ghost#0000"]}]}, RETRIEVED
    )
    assert not (bool(claims) and True), "no verifiable claim survived, so this must refuse"


def test_marking_comes_from_cited_chunks_not_retrieved_ones():
    """Citing only the real chunk must not import the synthetic caveat."""
    claims, _ = verify_claims(
        {"supported": True, "claims": [{"text": "x", "chunk_ids": ["doc-a#0000"]}]}, RETRIEVED
    )
    assert str(combined_marking(claims, RETRIEVED)) == "UNCLASSIFIED"


def test_marking_propagates_when_a_synthetic_chunk_is_cited():
    claims, _ = verify_claims(
        {"supported": True, "claims": [{"text": "x", "chunk_ids": ["doc-b#0001"]}]}, RETRIEVED
    )
    assert str(combined_marking(claims, RETRIEVED)) == "UNCLASSIFIED // SYNTHETIC"


def test_malformed_model_output_does_not_raise():
    claims, dropped = verify_claims({"supported": True, "claims": ["not a dict", {}, None]}, RETRIEVED)
    assert claims == [] and dropped == []
