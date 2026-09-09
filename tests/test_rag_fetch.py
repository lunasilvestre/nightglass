"""Corpus acquisition: the manifest flattening, the licence guard, and the rule
that a partial corpus is not a fetched corpus.

The publishers are replaced by `httpx.MockTransport` — a scripted server, not a
stub of our own code — and everything else is real: the files land in `tmp_path`,
the extractors run, `MANIFEST.json` is written and read back.

The one thing that changes between here and CI is `pdftotext`: it is on this
host and absent from the enclave's runtime image by design, so the PDF success
path is skipped where it is missing while the refusal it raises is asserted
everywhere.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import httpx
import pytest

from nightglass.rag import fetch as F
from nightglass.rag.extract import ExtractionError

#: Captured before any test patches the name, so a second patch inside one test
#: session still builds a real client rather than recursing into the last stub.
_REAL_CLIENT = httpx.Client

MARKDOWN = (
    "# AIS carriage\n\n"
    + "SOLAS Chapter V requires an automatic identification system aboard ships "
    "of 300 gross tonnage and upwards engaged on international voyages. " * 4
)

EMS = json.dumps(
    {
        "results": [
            {
                "code": "EMSR777",
                "name": "Storm in the Kattegat",
                "category": "Storm",
                "activator": "DEMA",
                "eventTime": "2026-07-16T00:00:00Z",
                "countries": [{"name": "Denmark"}],
                "aois": [
                    {
                        "number": 1,
                        "name": "Aarhus",
                        "products": [{"images": [{"sensorName": "Sentinel-1", "sensorType": "sar"}]}],
                    }
                ],
                "reason": "Severe coastal flooding was reported along the Kattegat coast. " * 4,
            }
        ]
    }
).encode()

SOURCES = """
groups:
  - publisher: IMO
    doc_type: regulation
    format: markdown
    language: en
    classification: UNCLASSIFIED
    licence: >-
      Reuse authorised
      with attribution.
    fetch_base: "https://example.test/raw/"
    source_base: "https://example.test/page/"
    aoi: [kattegat]
    headers:
      Accept: "application/pdf, application/pdf;type=pdfa1a"
      Accept-Language: en
    items:
      - doc_id: ais-carriage
        title: "AIS   carriage\\n  requirements"
        path: "ais.md"
        page: "ais"
  - publisher: Copernicus EMS
    doc_type: activation
    format: copernicus-ems-activation
    fetch_base: "https://ems.test/api/"
    redistributable: false
    items:
      - doc_id: emsr777
        title: EMSR777
        path: "activation/777"
        aoi: [kattegat, lisbon]
"""


def _sources(tmp_path: Path, text: str = SOURCES) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _serving(monkeypatch, routes: dict[str, tuple[int, bytes]]) -> list[httpx.Request]:
    """Serve `routes` through the client `fetch_corpus` builds for itself."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = routes.get(str(request.url), (404, b""))
        return httpx.Response(status, content=body)

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)
    )
    return seen


# -- the manifest --------------------------------------------------------------


def test_group_defaults_are_folded_into_every_item(tmp_path):
    imo, ems = F.load_sources(_sources(tmp_path))

    assert imo.doc_id == "ais-carriage"
    assert imo.title == "AIS carriage requirements"  # whitespace normalised
    assert imo.fetch_url == "https://example.test/raw/ais.md"
    assert imo.source_url == "https://example.test/page/ais"  # the human page, not the fetch url
    assert imo.licence == "Reuse authorised with attribution."
    assert imo.aoi == ("kattegat",)
    assert imo.redistributable is True
    assert imo.headers == {
        "Accept": "application/pdf, application/pdf;type=pdfa1a",
        "Accept-Language": "en",
    }

    assert ems.source_url is None  # no source_base in that group
    assert ems.redistributable is False
    assert ems.aoi == ("kattegat", "lisbon")  # the item's own list wins
    assert ems.headers == {}


def test_a_duplicate_doc_id_is_refused_because_it_is_the_citation_key(tmp_path):
    doubled = SOURCES.replace("doc_id: emsr777", "doc_id: ais-carriage")
    with pytest.raises(F.FetchError, match="duplicate doc_id 'ais-carriage'"):
        F.load_sources(_sources(tmp_path, doubled))


def test_an_unknown_format_names_the_ones_that_exist(tmp_path):
    bad = SOURCES.replace("format: markdown", "format: docx")
    with pytest.raises(F.FetchError, match="unknown format 'docx'"):
        F.load_sources(_sources(tmp_path, bad))


def test_a_manifest_without_groups_is_refused(tmp_path):
    with pytest.raises(F.FetchError, match="expected a mapping with a 'groups' key"):
        F.load_sources(_sources(tmp_path, "items: []\n"))


# -- fetching ------------------------------------------------------------------


def test_a_full_fetch_writes_raw_bytes_normalised_text_and_a_manifest(tmp_path, monkeypatch):
    """Raw bytes are kept beside the normalised text so the corpus can be
    re-normalised without going back online, and so the recorded sha256 refers to
    something that still exists."""
    _serving(
        monkeypatch,
        {
            "https://example.test/raw/ais.md": (200, MARKDOWN.encode()),
            "https://ems.test/api/activation/777": (200, EMS),
        },
    )
    entries = F.fetch_corpus(sources_path=_sources(tmp_path), out_root=tmp_path / "corpus")

    root = tmp_path / "corpus"
    assert (root / "raw" / "ais-carriage.md").read_bytes() == MARKDOWN.encode()
    assert (root / "raw" / "emsr777.json").exists()
    assert "AIS carriage" in (root / "normalized" / "ais-carriage.md").read_text()
    assert "EMSR777" in (root / "normalized" / "emsr777.md").read_text()

    manifest = json.loads((root / "MANIFEST.json").read_text())
    assert manifest["documents"] == 2 and manifest["failures"] == []
    assert {e["doc_id"] for e in manifest["entries"]} == {"ais-carriage", "emsr777"}
    assert len(entries) == 2
    assert entries[0].sha256 == hashlib.sha256(MARKDOWN.encode()).hexdigest()


def test_per_source_headers_are_sent_and_others_are_not(tmp_path, monkeypatch):
    """The EU group needs an Accept and an Accept-Language to get PDF bytes out
    of the Publications Office; every other publisher gets the plain request."""
    seen = _serving(
        monkeypatch,
        {
            "https://example.test/raw/ais.md": (200, MARKDOWN.encode()),
            "https://ems.test/api/activation/777": (200, EMS),
        },
    )
    F.fetch_corpus(sources_path=_sources(tmp_path), out_root=tmp_path / "corpus")

    by_url = {str(r.url): r for r in seen}
    assert by_url["https://example.test/raw/ais.md"].headers["accept-language"] == "en"
    assert "pdfa1a" in by_url["https://example.test/raw/ais.md"].headers["accept"]
    assert by_url["https://ems.test/api/activation/777"].headers.get("accept-language") != "en"
    assert "nightglass-corpus-fetcher" in by_url["https://ems.test/api/activation/777"].headers[
        "user-agent"
    ]


def test_one_dead_url_does_not_cost_the_other_documents_but_does_fail_the_run(
    tmp_path, monkeypatch
):
    """The momentum rule and the honesty rule together: everything reachable is
    fetched and recorded, and then the run fails, because `make ingest` reporting
    52 of 60 documents under a heading that said "indexed" is how this was found."""
    _serving(
        monkeypatch,
        {
            "https://example.test/raw/ais.md": (200, MARKDOWN.encode()),
            "https://ems.test/api/activation/777": (202, b"<challenge>"),
        },
    )
    with pytest.raises(F.FetchError) as exc:
        F.fetch_corpus(sources_path=_sources(tmp_path), out_root=tmp_path / "corpus")

    assert "1 of 2 document(s) did not arrive" in str(exc.value)
    assert "everything already fetched is cached" in str(exc.value).replace("\n", " ")

    manifest = json.loads((tmp_path / "corpus" / "MANIFEST.json").read_text())
    assert manifest["documents"] == 1
    assert manifest["failures"] and "emsr777" in manifest["failures"][0]
    assert (tmp_path / "corpus" / "normalized" / "ais-carriage.md").exists()


def test_a_second_run_reuses_what_is_on_disk(tmp_path, monkeypatch):
    routes = {
        "https://example.test/raw/ais.md": (200, MARKDOWN.encode()),
        "https://ems.test/api/activation/777": (200, EMS),
    }
    _serving(monkeypatch, routes)
    F.fetch_corpus(sources_path=_sources(tmp_path), out_root=tmp_path / "corpus")

    seen = _serving(monkeypatch, routes)
    entries = F.fetch_corpus(sources_path=_sources(tmp_path), out_root=tmp_path / "corpus")

    assert seen == []  # nothing was downloaded twice
    assert len(entries) == 2


def test_force_re_downloads_what_is_already_cached(tmp_path, monkeypatch):
    routes = {
        "https://example.test/raw/ais.md": (200, MARKDOWN.encode()),
        "https://ems.test/api/activation/777": (200, EMS),
    }
    _serving(monkeypatch, routes)
    F.fetch_corpus(sources_path=_sources(tmp_path), out_root=tmp_path / "corpus")

    seen = _serving(monkeypatch, routes)
    F.fetch_corpus(sources_path=_sources(tmp_path), out_root=tmp_path / "corpus", force=True)
    assert len(seen) == 2


def test_only_restricts_the_fetch_by_doc_id_or_publisher(tmp_path, monkeypatch):
    seen = _serving(monkeypatch, {"https://example.test/raw/ais.md": (200, MARKDOWN.encode())})
    F.fetch_corpus(
        sources_path=_sources(tmp_path), out_root=tmp_path / "corpus", only=["ais-carriage"]
    )
    assert [str(r.url) for r in seen] == ["https://example.test/raw/ais.md"]

    with pytest.raises(F.FetchError, match="no sources matched nothing-like-this"):
        F.fetch_corpus(
            sources_path=_sources(tmp_path),
            out_root=tmp_path / "corpus",
            only=["nothing-like-this"],
        )


def test_an_empty_body_is_a_failure_rather_than_an_empty_document(tmp_path, monkeypatch):
    _serving(monkeypatch, {"https://example.test/raw/ais.md": (200, b"")})
    with pytest.raises(F.FetchError):
        F.fetch_corpus(
            sources_path=_sources(tmp_path), out_root=tmp_path / "corpus", only=["ais-carriage"]
        )
    manifest = json.loads((tmp_path / "corpus" / "MANIFEST.json").read_text())
    assert "returned an empty body" in manifest["failures"][0]


def test_a_document_that_extracts_to_almost_nothing_is_a_failure(tmp_path, monkeypatch):
    """Extraction silently producing three characters is the failure mode that
    would otherwise reach the index as a document."""
    _serving(monkeypatch, {"https://example.test/raw/ais.md": (200, b"# hi\n")})
    with pytest.raises(F.FetchError):
        F.fetch_corpus(
            sources_path=_sources(tmp_path), out_root=tmp_path / "corpus", only=["ais-carriage"]
        )
    manifest = json.loads((tmp_path / "corpus" / "MANIFEST.json").read_text())
    assert "extraction probably failed" in manifest["failures"][0]


def test_an_unwritable_destination_says_which_half_of_the_system_you_are_in(tmp_path):
    """Inside the enclave the corpus is mounted read-only and no publisher is
    reachable, so this is the first thing that fails and it should say why."""
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    try:
        with pytest.raises(F.FetchError, match="PROVISIONING step"):
            F.fetch_corpus(sources_path=_sources(tmp_path), out_root=read_only / "corpus")
    finally:
        read_only.chmod(0o700)


# -- the licence guard ---------------------------------------------------------


def test_the_corpus_directory_is_made_to_ignore_everything(tmp_path, monkeypatch):
    _serving(monkeypatch, {"https://example.test/raw/ais.md": (200, MARKDOWN.encode())})
    F.fetch_corpus(
        sources_path=_sources(tmp_path), out_root=tmp_path / "corpus", only=["ais-carriage"]
    )
    text = (tmp_path / "corpus" / ".gitignore").read_text()
    assert text.startswith("*\n")
    assert "Fetching is not redistribution" in text


def test_restricted_material_is_refused_when_the_ignore_file_is_gone(tmp_path):
    """The guard is checked again immediately before each restricted download, so
    an ignore file removed after the run started still stops the write. The
    guarantee is local to the directory holding the material."""
    root = tmp_path / "corpus"
    root.mkdir()
    _imo, ems = F.load_sources(_sources(tmp_path))
    assert ems.redistributable is False

    (root / ".gitignore").write_text("# nothing ignored here\n")
    with pytest.raises(F.FetchError, match="refusing to write emsr777"):
        F._assert_gitignored(root, ems)

    (root / ".gitignore").write_text("*\n")
    F._assert_gitignored(root, ems)  # restored: no refusal


def test_an_existing_ignore_file_is_left_alone(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / ".gitignore").write_text("*\n# mine\n")
    F._ensure_gitignored(root)
    assert (root / ".gitignore").read_text() == "*\n# mine\n"


# -- extraction dispatch -------------------------------------------------------


def _pdf_source() -> F.Source:
    return F.Source(
        doc_id="reg",
        title="A regulation",
        fetch_url="https://publications.europa.eu/resource/celex/32014R0909",
        source_url=None,
        publisher="European Union",
        doc_type="regulation",
        format="pdf",
        language="en",
        classification="UNCLASSIFIED",
        licence=None,
        redistributable=True,
    )


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler-utils is not installed")
def test_a_pdf_is_extracted_through_poppler(tmp_path):
    """Only the `fetcher` image installs poppler; the enclave runtime does not,
    and this project does not add it there to satisfy a test."""
    pdf = tmp_path / "reg.pdf"
    pdf.write_bytes(
        _one_page_pdf("Article 1 This Regulation lays down carriage requirements. " * 8)
    )

    text = F._extract(_pdf_source(), pdf.read_bytes(), pdf)
    assert "carriage requirements" in text


def test_a_pdf_without_poppler_refuses_and_says_which_image_has_it(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(ExtractionError, match="poppler-utils"):
        F._extract(_pdf_source(), b"%PDF-1.4", tmp_path / "reg.pdf")


def test_a_format_with_no_extractor_is_refused(tmp_path):
    src = F.Source(**{**_pdf_source().__dict__, "format": "docx"})
    with pytest.raises(F.FetchError, match="no extractor for format 'docx'"):
        F._extract(src, b"", tmp_path / "x")


def _one_page_pdf(text: str) -> bytes:
    """The smallest PDF that `pdftotext` will read, built by hand."""
    escaped = text.replace("(", r"\(").replace(")", r"\)")
    lines = [escaped[i : i + 70] for i in range(0, len(escaped), 70)]
    stream = "BT /F1 10 Tf 40 750 Td 12 TL\n" + "".join(f"({ln}) Tj T*\n" for ln in lines) + "ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{off:010d} 00000 n \n" for off in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    )
    return out.encode("latin-1")
