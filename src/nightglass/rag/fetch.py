"""ONLINE. Corpus acquisition — the document half of `make pull-models`.

This is the only module in `nightglass.rag` that opens a socket to the outside
world, and nothing the enclave runs imports it. It executes in the `fetcher`
image on the `provision` network, exactly as `model-puller` does: provisioning
is online and explicit, operation is offline and sealed.

What it produces:

    data/corpus/.gitignore      '*' -- written first, and load-bearing (see below)
    data/corpus/raw/<id>.<ext>  the bytes as served, unmodified
    data/corpus/normalized/<id>.md   front-matter + extracted text
    data/corpus/MANIFEST.json   url, sha256, size, licence, retrieved_at, per doc

Raw bytes are kept alongside the normalised text on purpose. Extraction is
lossy and its rules will change; keeping the original means the corpus can be
re-normalised without going back online, which is the whole point of an
air-gapped deployment. It also means the recorded sha256 refers to something
that still exists and can be checked.

**The licence guard.** Some publishers in `sources.yaml` grant no reuse licence,
marked `redistributable: false`. Fetching those onto the machine that reads them
is fine; committing them to a public repository is not. Rather than trusting the
top-level `.gitignore` to stay correct forever, this writes a `.gitignore`
containing `*` into the corpus root and refuses to write restricted content if
that file is not in place. The guarantee is then local to the directory holding
the material, and survives someone reorganising ignore rules elsewhere.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from nightglass.rag.documents import Document, dump_document
from nightglass.rag.extract import (
    ExtractionError,
    extract_ems_activation,
    extract_markdown,
    extract_pdf,
)

_EXTENSIONS = {"markdown": ".md", "pdf": ".pdf", "copernicus-ems-activation": ".json"}
_USER_AGENT = "nightglass-corpus-fetcher/0.1 (+https://github.com/; research use)"


class FetchError(RuntimeError):
    """Acquisition failed in a way worth stopping for."""


@dataclass
class ManifestEntry:
    doc_id: str
    title: str
    publisher: str
    format: str
    fetch_url: str
    source_url: str | None
    licence: str | None
    redistributable: bool
    sha256: str
    bytes: int
    extracted_chars: int
    retrieved_at: str


@dataclass
class Source:
    """One item from `sources.yaml`, with its group defaults already folded in."""

    doc_id: str
    title: str
    fetch_url: str
    source_url: str | None
    publisher: str
    doc_type: str
    format: str
    language: str
    classification: str
    licence: str | None
    redistributable: bool
    aoi: tuple[str, ...] = ()


def load_sources(path: Path) -> list[Source]:
    """Flatten the grouped manifest into a list of individually fetchable items."""
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or "groups" not in spec:
        raise FetchError(f"{path}: expected a mapping with a 'groups' key")

    sources: list[Source] = []
    seen: dict[str, str] = {}
    for gi, group in enumerate(spec["groups"]):
        fetch_base = str(group.get("fetch_base", ""))
        source_base = group.get("source_base")
        fmt = str(group.get("format", "markdown"))
        if fmt not in _EXTENSIONS:
            raise FetchError(
                f"{path}: group {gi} has unknown format {fmt!r}. "
                f"Known: {', '.join(sorted(_EXTENSIONS))}"
            )
        for item in group.get("items", []):
            doc_id = str(item["doc_id"])
            if doc_id in seen:
                raise FetchError(f"{path}: duplicate doc_id {doc_id!r} (also in group {seen[doc_id]})")
            seen[doc_id] = str(group.get("publisher", gi))
            page = item.get("page", item["path"])
            sources.append(
                Source(
                    doc_id=doc_id,
                    title=" ".join(str(item["title"]).split()),
                    fetch_url=fetch_base + str(item["path"]),
                    source_url=(f"{source_base}{page}" if source_base else None),
                    publisher=str(group.get("publisher", "unknown")),
                    doc_type=str(group.get("doc_type", "document")),
                    format=fmt,
                    language=str(group.get("language", "en")),
                    classification=str(group.get("classification", "UNCLASSIFIED")),
                    licence=_clean(group.get("licence")),
                    redistributable=bool(group.get("redistributable", True)),
                    aoi=tuple(str(a) for a in (item.get("aoi") or group.get("aoi") or [])),
                )
            )
    return sources


def fetch_corpus(
    *,
    sources_path: Path,
    out_root: Path,
    force: bool = False,
    only: list[str] | None = None,
) -> list[ManifestEntry]:
    sources = load_sources(sources_path)
    if only:
        wanted = set(only)
        sources = [s for s in sources if s.doc_id in wanted or s.publisher in wanted]
        if not sources:
            raise FetchError(f"no sources matched {', '.join(only)}")

    raw_dir = out_root / "raw"
    norm_dir = out_root / "normalized"
    # Prove the destination is writable before downloading 35 MB into it. Run
    # inside the enclave -- where the corpus is mounted read-only and there is
    # no route to any publisher anyway -- this is the first thing that fails,
    # and it should fail saying which of the two halves of the system the caller
    # is standing in rather than with a traceback from the manifest write at the
    # very end.
    try:
        for d in (out_root, raw_dir, norm_dir):
            d.mkdir(parents=True, exist_ok=True)
        _ensure_gitignored(out_root)
        probe = out_root / ".write-probe"
        probe.write_text("")
        probe.unlink()
    except OSError as exc:
        raise FetchError(
            f"cannot write to {out_root}: {exc}\n"
            "`fetch` is a PROVISIONING step: it runs in the corpus-fetcher service, on the "
            "provision network, via `make fetch-corpus`. Inside the enclave this directory is "
            "mounted read-only and no publisher is reachable."
        ) from exc

    entries: list[ManifestEntry] = []
    failures: list[str] = []

    with httpx.Client(
        timeout=180.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    ) as client:
        for i, src in enumerate(sources, 1):
            print(f"[{i:>2}/{len(sources)}] {src.doc_id}", flush=True)
            try:
                entries.append(
                    _fetch_one(client, src, raw_dir=raw_dir, norm_dir=norm_dir, force=force)
                )
            except (httpx.HTTPError, FetchError, ExtractionError) as exc:
                # Momentum rule: one dead URL must not cost the other 38
                # documents. Record it, keep going, report at the end.
                print(f"          FAILED: {type(exc).__name__}: {exc}", flush=True)
                failures.append(f"{src.doc_id}: {exc}")

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources_file": str(sources_path),
        "documents": len(entries),
        "failures": failures,
        "entries": [asdict(e) for e in entries],
    }
    (out_root / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")

    print(f"\n{len(entries)} document(s) into {norm_dir}")
    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
    return entries


def _fetch_one(
    client: httpx.Client, src: Source, *, raw_dir: Path, norm_dir: Path, force: bool
) -> ManifestEntry:
    raw_path = raw_dir / f"{src.doc_id}{_EXTENSIONS[src.format]}"

    if raw_path.exists() and not force:
        body = raw_path.read_bytes()
        print(f"          cached ({len(body):,} bytes)", flush=True)
    else:
        if not src.redistributable:
            _assert_gitignored(raw_dir.parent, src)
        r = client.get(src.fetch_url)
        r.raise_for_status()
        body = r.content
        if not body:
            raise FetchError(f"{src.fetch_url} returned an empty body")
        raw_path.write_bytes(body)
        print(f"          {len(body):,} bytes from {src.fetch_url}", flush=True)

    digest = hashlib.sha256(body).hexdigest()
    text = _extract(src, body, raw_path)
    if len(text) < 200:
        raise FetchError(f"extracted only {len(text)} characters — extraction probably failed")

    doc = Document(
        doc_id=src.doc_id,
        title=src.title,
        text=text,
        classification=src.classification,
        publisher=src.publisher,
        doc_type=src.doc_type,
        language=src.language,
        origin="real",
        source_url=src.source_url or src.fetch_url,
        licence=src.licence,
        aoi=src.aoi,
        redistributable=src.redistributable,
        sha256=digest,
        retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    (norm_dir / f"{src.doc_id}.md").write_text(dump_document(doc), encoding="utf-8")
    print(f"          -> {len(text):,} chars normalised", flush=True)

    return ManifestEntry(
        doc_id=src.doc_id,
        title=src.title,
        publisher=src.publisher,
        format=src.format,
        fetch_url=src.fetch_url,
        source_url=src.source_url,
        licence=src.licence,
        redistributable=src.redistributable,
        sha256=digest,
        bytes=len(body),
        extracted_chars=len(text),
        retrieved_at=doc.retrieved_at or "",
    )


def _extract(src: Source, body: bytes, raw_path: Path) -> str:
    if src.format == "pdf":
        return extract_pdf(raw_path)
    if src.format == "markdown":
        return extract_markdown(body.decode("utf-8", errors="replace"))
    if src.format == "copernicus-ems-activation":
        return extract_ems_activation(body)
    raise FetchError(f"no extractor for format {src.format!r}")


def _ensure_gitignored(root: Path) -> None:
    """Make the corpus directory ignore everything, and say why in the file."""
    gitignore = root / ".gitignore"
    if gitignore.exists() and gitignore.read_text(encoding="utf-8").strip().startswith("*"):
        return
    gitignore.write_text(
        "*\n"
        "# Written by `make fetch-corpus`. Everything here was downloaded from a\n"
        "# publisher, and some of it carries no reuse licence -- see corpus/README.md.\n"
        "# Fetching is not redistribution; committing would be. Do not remove this file:\n"
        "# the fetcher refuses to write restricted material without it.\n",
        encoding="utf-8",
    )


def _assert_gitignored(root: Path, src: Source) -> None:
    gitignore = root / ".gitignore"
    ok = gitignore.exists() and gitignore.read_text(encoding="utf-8").strip().startswith("*")
    if not ok:
        raise FetchError(
            f"refusing to write {src.doc_id} ({src.publisher}): it is marked "
            f"redistributable: false and {gitignore} does not ignore this directory. "
            "Restore it, or drop the source."
        )


def _clean(v: Any) -> str | None:
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s or None
