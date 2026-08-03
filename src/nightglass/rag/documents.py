"""A corpus document, and how one is read from disk.

Every document in the corpus -- fetched or authored -- is a markdown file with
YAML front-matter. One format for both halves, so the ingest pipeline has no
idea which documents were downloaded and which were written, and cannot
accidentally treat them differently.

The front-matter is not decoration. `classification` is read from it and
propagated into every chunk and from there into any report that cites one, which
is §7's requirement that markings travel from source into product. A document
with no marking is a document whose product cannot be marked, so parsing is
strict about it rather than defaulting quietly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# A marking is a level, optionally followed by caveats, e.g.
#     UNCLASSIFIED // SYNTHETIC
# Levels are ranked so a product citing several sources can take the most
# restrictive rather than the first. This project only ever produces
# UNCLASSIFIED material; the rest of the ladder is here so that the propagation
# logic is not silently wrong the first time it meets anything else.
_LEVEL_RANK = {
    "UNCLASSIFIED": 0,
    "RESTRICTED": 1,
    "CONFIDENTIAL": 2,
    "SECRET": 3,
    "TOP SECRET": 4,
}

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class CorpusError(RuntimeError):
    """Raised while reading the corpus, naming the file that is wrong."""


@dataclass(frozen=True)
class Marking:
    """A classification marking, split into its level and its caveats.

    Kept as a type rather than a string because the interesting operation --
    combining the markings of several cited sources -- is not string
    concatenation. `UNCLASSIFIED` combined with `UNCLASSIFIED // SYNTHETIC` is
    the latter: the caveat survives, and a report that cites synthetic material
    has to say so even when everything else in it is real.
    """

    level: str
    caveats: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: str) -> Marking:
        parts = [p.strip().upper() for p in raw.split("//") if p.strip()]
        if not parts:
            raise CorpusError(f"empty classification marking: {raw!r}")
        level, *caveats = parts
        if level not in _LEVEL_RANK:
            raise CorpusError(
                f"unknown classification level {level!r} in {raw!r}. "
                f"Known: {', '.join(_LEVEL_RANK)}"
            )
        return cls(level=level, caveats=tuple(dict.fromkeys(caveats)))

    @classmethod
    def combine(cls, markings: list[Marking]) -> Marking:
        """The marking a product must carry, given everything it cites.

        Highest level wins; caveats accumulate. Never reduces.
        """
        if not markings:
            return cls(level="UNCLASSIFIED")
        level = max((m.level for m in markings), key=lambda x: _LEVEL_RANK[x])
        caveats: dict[str, None] = {}
        for m in markings:
            for c in m.caveats:
                caveats[c] = None
        return cls(level=level, caveats=tuple(sorted(caveats)))

    def __str__(self) -> str:
        return " // ".join((self.level, *self.caveats))


@dataclass(frozen=True)
class Document:
    """One corpus document, normalised. The unit that gets chunked."""

    doc_id: str
    title: str
    text: str
    classification: str
    publisher: str
    doc_type: str
    language: str
    origin: str = "real"  # "real" | "synthetic"
    source_url: str | None = None
    licence: str | None = None
    date: str | None = None
    aoi: tuple[str, ...] = ()
    redistributable: bool = True
    sha256: str | None = None
    retrieved_at: str | None = None
    path: Path | None = None

    @property
    def marking(self) -> Marking:
        return Marking.parse(self.classification)

    @property
    def is_synthetic(self) -> bool:
        return self.origin == "synthetic" or "SYNTHETIC" in self.marking.caveats

    def payload(self) -> dict[str, Any]:
        """The document-level fields that ride along on every chunk."""
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "classification": self.classification,
            "publisher": self.publisher,
            "doc_type": self.doc_type,
            "language": self.language,
            "origin": self.origin,
            "source_url": self.source_url,
            "licence": self.licence,
            "date": self.date,
            "aoi": list(self.aoi),
        }


def dump_document(doc: Document, *, body: str | None = None) -> str:
    """Serialise back to front-matter + markdown, for the fetcher's output."""
    meta = {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "classification": doc.classification,
        "origin": doc.origin,
        "publisher": doc.publisher,
        "doc_type": doc.doc_type,
        "language": doc.language,
        "date": doc.date,
        "aoi": list(doc.aoi),
        "source_url": doc.source_url,
        "licence": doc.licence,
        "redistributable": doc.redistributable,
        "sha256": doc.sha256,
        "retrieved_at": doc.retrieved_at,
    }
    meta = {k: v for k, v in meta.items() if v is not None}
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=100)
    return f"---\n{front}---\n\n{body if body is not None else doc.text}"


def parse_document(raw: str, *, path: Path | None = None) -> Document:
    """Read one front-matter markdown file into a Document."""
    origin = str(path) if path else "<string>"
    m = _FRONT_MATTER.match(raw)
    if not m:
        raise CorpusError(f"{origin}: no YAML front-matter (file must start with '---')")

    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise CorpusError(f"{origin}: front-matter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise CorpusError(f"{origin}: front-matter must be a mapping")

    body = raw[m.end() :].strip()
    if not body:
        raise CorpusError(f"{origin}: front-matter present but body is empty")

    missing = [k for k in ("doc_id", "title", "classification") if not meta.get(k)]
    if missing:
        raise CorpusError(f"{origin}: front-matter missing required key(s): {', '.join(missing)}")

    # Validate the marking here rather than at citation time. A document whose
    # marking cannot be parsed cannot have its marking propagated, and finding
    # that out while drafting a report is far too late.
    Marking.parse(str(meta["classification"]))

    aoi = meta.get("aoi") or []
    if isinstance(aoi, str):
        aoi = [aoi]

    return Document(
        doc_id=str(meta["doc_id"]),
        title=str(meta["title"]),
        text=body,
        classification=str(meta["classification"]),
        publisher=str(meta.get("publisher", "unknown")),
        doc_type=str(meta.get("doc_type", "document")),
        language=str(meta.get("language", "en")),
        origin=str(meta.get("origin", "real")),
        source_url=_opt_str(meta.get("source_url")),
        licence=_opt_str(meta.get("licence")),
        date=_opt_str(meta.get("date")),
        aoi=tuple(str(a) for a in aoi),
        redistributable=bool(meta.get("redistributable", True)),
        sha256=_opt_str(meta.get("sha256")),
        retrieved_at=_opt_str(meta.get("retrieved_at")),
        path=path,
    )


def load_corpus(roots: list[Path]) -> list[Document]:
    """Every document under every root, sorted by doc_id.

    Roots that do not exist are skipped rather than raising: the synthetic half
    is committed and always present, but the fetched half legitimately does not
    exist until `make fetch-corpus` has been run, and "you have not fetched yet"
    is a better message from the caller than a traceback from here.
    """
    docs: dict[str, Document] = {}
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.md")):
            doc = parse_document(p.read_text(encoding="utf-8"), path=p)
            if doc.doc_id in docs:
                raise CorpusError(
                    f"duplicate doc_id {doc.doc_id!r}: {docs[doc.doc_id].path} and {p}. "
                    "doc_id is the citation key and must be unique across the corpus."
                )
            docs[doc.doc_id] = doc
    return [docs[k] for k in sorted(docs)]


def _opt_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


@dataclass
class CorpusStats:
    """What is on disk, for `nightglass-corpus stats`."""

    documents: int = 0
    characters: int = 0
    by_publisher: dict[str, int] = field(default_factory=dict)
    by_origin: dict[str, int] = field(default_factory=dict)
    by_language: dict[str, int] = field(default_factory=dict)

    @classmethod
    def of(cls, docs: list[Document]) -> CorpusStats:
        s = cls(documents=len(docs), characters=sum(len(d.text) for d in docs))
        for d in docs:
            s.by_publisher[d.publisher] = s.by_publisher.get(d.publisher, 0) + 1
            s.by_origin[d.origin] = s.by_origin.get(d.origin, 0) + 1
            s.by_language[d.language] = s.by_language.get(d.language, 0) + 1
        return s
