"""Turning what a publisher serves into text worth embedding.

Three input formats, three problems.

**PDF.** Extraction is done by poppler's `pdftotext`, not by a Python library,
and that is a measured decision rather than a preference. `pypdf` on the EUR-Lex
Official Journal PDFs inserts spurious spaces inside words -- `REGUL A TIONS`,
`concer ning restr ictiv e measures` -- because of how those files encode
character spacing. Counting the artefact across two regulations: **676 and 114
occurrences with pypdf, 0 with pdftotext**. It also gets two-column Official
Journal reading order right. Mangled words would poison the embeddings and look
like a bug in any citation that quoted them.

The cost is a native dependency, and it is confined to the `fetcher` image stage
-- the enclave runtime cannot parse a PDF at all, because it never sees one.

**Markdown.** mkdocs-flavoured source carries admonition markers, figure tags
and bibliography keys that are layout instructions, not content.

**JSON.** The Copernicus activation API returns structured data; a narrative has
to be composed from it, because the human-facing pages are a JavaScript-rendered
single-page app whose HTML contains none of the text.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

_DIGITS = re.compile(r"\d+")
_PAGE_NUMBER = re.compile(r"^\s*[-—–\[(]?\s*(?:page\s*)?\d{1,4}\s*(?:/\s*\d{1,4})?\s*[-—–\])]?\s*$", re.IGNORECASE)
_ADMONITION = re.compile(r'^(\s*)(?:!!!|\?\?\?\+?)\s+(\w+)(?:\s+"([^"]*)")?\s*$')
_LIST_LEAD = re.compile(r"^(?:[-*•]|\(\w{1,3}\)|\d{1,3}[.)]|\|)\s")
# Lines at each page edge eligible to be a running head. Six rather than three,
# measured: the IMO resolutions carry a SIX-line running header (document
# symbol, page number, resolution number, adoption date, and a two-line title),
# so a three-line margin sees only the top half of it and the rest survives into
# the text -- 17 copies of "A 29/Res.1106" in one 35k-character document, which
# then compete with the content for retrieval.
_MARGIN = 6
_RUNNING_HEAD_FRACTION = 0.3
_RUNNING_HEAD_MAX_CHARS = 100  # a running head is short; a paragraph is not
_CITEKEY = re.compile(r"\[@[^\]]+\]")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG = re.compile(r"</?(?:figure|figcaption|div|span|br|small)[^>]*>", re.IGNORECASE)
_ATTR_LIST = re.compile(r"\{\s*[.#][^}]*\}\s*$")


class ExtractionError(RuntimeError):
    """A document could not be turned into text."""


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def extract_pdf(path: Path) -> str:
    if not shutil.which("pdftotext"):
        raise ExtractionError(
            "pdftotext not found. PDF sources are only fetched from the `fetcher` "
            "image stage, which installs poppler-utils; the enclave runtime does not."
        )
    try:
        proc = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", "-q", str(path), "-"],
            capture_output=True,
            check=True,
            timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        raise ExtractionError(f"pdftotext failed on {path.name}: {exc.stderr.decode()[:300]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError(f"pdftotext timed out on {path.name}") from exc

    pages = proc.stdout.decode("utf-8", errors="replace").split("\f")
    text = _strip_running_headers(pages)
    return _rewrap(text)


def _strip_running_headers(pages: list[str]) -> str:
    """Drop the lines that appear at the top or bottom of most pages.

    Running heads in this corpus vary per page -- `L 229/2`, `L 229/3` -- so an
    exact-match frequency count misses them entirely. Counting on a digit-blind
    normalisation (`L #/#`) catches the family. Without this, the phrase "Official
    Journal of the European Union" appears once per page and becomes one of the
    strongest signals in the whole document, which is precisely backwards.
    """
    if len(pages) < 3:
        return "\n".join(pages)

    # Margins are counted in NON-EMPTY lines, and the removal pass has to use
    # the same definition. Taking the first three non-empty lines to build the
    # frequency table but then the first three *raw* lines to remove against
    # silently misses every header preceded by a blank line -- which, in a PDF,
    # is most of them.
    def margins(page: str) -> list[tuple[int, str]]:
        idx = [(i, ln.strip()) for i, ln in enumerate(page.splitlines()) if ln.strip()]
        # A running head is never more than a third of the page. Without this
        # cap a short page is entirely margin, and body text that happens to
        # differ only in its numbers -- "Article 5", "Article 6" -- normalises
        # to one repeated key and gets deleted as if it were a header. The
        # digit-blind key is what makes header detection work at all, and it is
        # also what makes this failure possible, so the bound is not optional.
        m = max(1, min(_MARGIN, len(idx) // 3))
        return idx[:m] + idx[-m:]

    counts: Counter[str] = Counter()
    for page in pages:
        for _, text in margins(page):
            counts[_DIGITS.sub("#", text)] += 1

    threshold = max(3, int(len(pages) * _RUNNING_HEAD_FRACTION))
    running = {
        key
        for key, n in counts.items()
        if n >= threshold and len(key) <= _RUNNING_HEAD_MAX_CHARS
    }

    kept: list[str] = []
    for page in pages:
        lines = page.splitlines()
        margin_idx = {i for i, _ in margins(page)}
        for i, ln in enumerate(lines):
            s = ln.strip()
            if i in margin_idx and (_DIGITS.sub("#", s) in running or _PAGE_NUMBER.match(s)):
                continue
            kept.append(ln)
        kept.append("")
    return "\n".join(kept)


def _is_caps_heading(line: str) -> bool:
    """A short, all-capitals line — how IMO and EU instruments mark sections.

    `PURPOSE`, `CAUTION`, `INHERENT LIMITATIONS OF AIS`, `HAS ADOPTED THIS
    REGULATION:`. Promoting these to markdown headings is worth more than it
    looks: without them a 35,000-character extracted PDF is one undifferentiated
    run of prose, every chunk boundary falls at an arbitrary sentence, and no
    chunk carries any indication of which part of the instrument it came from.
    With them the chunker gets real section boundaries and each chunk gets a
    heading path that goes into its embedding.
    """
    s = line.strip()
    if not (3 <= len(s) <= 80):
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 3:
        return False
    return all(c.isupper() for c in letters) and not _LIST_LEAD.match(s)


def _rewrap(text: str) -> str:
    """Undo the PDF's hard line breaks, keeping paragraph structure.

    A PDF has no paragraphs, only lines placed on a page. Embedding text that
    breaks every 90 characters works, but every chunk boundary then lands
    mid-sentence and the quoted evidence reads as damaged.
    """
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append(" ".join(para))
            para.clear()

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            flush()
            if out and out[-1] != "":
                out.append("")
            continue

        if _is_caps_heading(stripped):
            flush()
            out += ["", f"## {stripped}", ""]
            continue

        # A table-ish or list-ish line keeps its own break: joining "(a) ..." onto
        # the previous line destroys the enumeration that gives it meaning.
        if _LIST_LEAD.match(stripped):
            flush()
            para.append(stripped)
            flush()
            continue

        if para and (para[-1].endswith("­") or para[-1].endswith("-")):
            para[-1] = para[-1].rstrip("­-") + stripped
        else:
            para.append(stripped)
    flush()

    collapsed = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", collapsed).strip()


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def extract_markdown(raw: str) -> str:
    """mkdocs-flavoured markdown -> plain markdown.

    Strips what is presentation (admonition markers, figure wrappers, attribute
    lists, image embeds, bibliography keys) and keeps what is content, including
    tables -- the ICEYE glossary and imaging-mode pages are almost entirely
    tables and are among the most useful pages in the corpus.
    """
    lines = raw.splitlines()
    out: list[str] = []
    admonition_indent: int | None = None

    for line in lines:
        m = _ADMONITION.match(line)
        if m:
            indent, kind, title = m.group(1), m.group(2), m.group(3)
            admonition_indent = len(indent)
            out.append(f"**{title or kind.title()}:**")
            continue

        if admonition_indent is not None:
            if line.strip() and (len(line) - len(line.lstrip())) > admonition_indent:
                out.append(line.strip())
                continue
            if line.strip():
                admonition_indent = None
            else:
                out.append("")
                continue

        cleaned = _IMAGE.sub("", line)
        cleaned = _CITEKEY.sub("", cleaned)
        cleaned = _HTML_TAG.sub("", cleaned)
        cleaned = _ATTR_LIST.sub("", cleaned)
        out.append(cleaned.rstrip())

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Copernicus EMS activation JSON
# ---------------------------------------------------------------------------


def extract_ems_activation(raw: bytes) -> str:
    """The activation API's JSON -> a readable activation memo.

    Composed rather than scraped: the activation pages are a JavaScript-rendered
    SPA and their HTML contains none of this text. What is kept is what an
    analyst would use -- what happened, where, when, who asked, and which
    sensors were flown -- and not the several hundred kilobytes of AOI polygon
    geometry, which is real data but is not prose and would be pure noise in a
    text index.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"activation response is not JSON: {exc}") from exc

    results = payload.get("results") or []
    if not results:
        raise ExtractionError("activation response contained no results")
    a = results[0]

    countries = ", ".join(c.get("name", "") for c in a.get("countries", [])) or "not stated"
    aois = a.get("aois") or []
    products = [p for aoi in aois for p in (aoi.get("products") or [])]
    sensors = sorted(
        {
            f"{img.get('sensorName')} ({img.get('sensorType', '?').upper()})"
            for p in products
            for img in (p.get("images") or [])
            if img.get("sensorName")
        }
    )

    lines = [
        f"# {a.get('code', '')} — {a.get('name', '')}",
        "",
        "## Activation summary",
        "",
        f"- **Activation code:** {a.get('code', '')}",
        f"- **Event type:** {a.get('category', 'not stated')} / {a.get('subCategory', 'not stated')}",
        f"- **Countries affected:** {countries}",
        f"- **Continent:** {a.get('continent', 'not stated')}",
        f"- **Event time (UTC):** {a.get('eventTime', 'not stated')}",
        f"- **Activation time (UTC):** {a.get('activationTime', 'not stated')}",
        f"- **Requesting authority:** {a.get('activator', 'not stated')}",
        f"- **Status:** {'closed' if a.get('closed') else 'open'}",
        f"- **Areas of interest mapped:** {len(aois)}",
        f"- **Mapping products delivered:** {len(products)}",
    ]
    if sensors:
        lines.append(f"- **Sensors tasked:** {', '.join(sensors)}")
    if a.get("reportLink"):
        lines.append(f"- **Situational report:** {a['reportLink']}")

    if a.get("reason"):
        lines += ["", "## Reason for activation", "", a["reason"].strip()]

    if aois:
        lines += ["", "## Areas of interest", ""]
        for aoi in aois:
            n = len(aoi.get("products") or [])
            lines.append(f"- **AOI {aoi.get('number')}** — {aoi.get('name')} ({n} products)")

    lines += [
        "",
        "## Note on use in this corpus",
        "",
        (
            "This is a Copernicus Emergency Management Service rapid mapping activation. "
            "It is included as dated, citable environmental and situational context for the "
            "area of interest — sea state, storm activity and coastal impact around specific "
            "dates — and not as a maritime security product. It reports no vessel detections "
            "and no AIS data."
        ),
    ]
    return "\n".join(lines)
