"""Structure-aware chunking.

A chunk is the unit an INTREP cites, so the design goal is not "roughly equal
sized pieces of text" -- it is **a span an analyst can be shown as the evidence
for a claim and will accept as such**. Those are different objectives and they
pull in different directions:

- Splitting on a fixed character count is easy and produces chunks that begin
  mid-sentence and end mid-table. Cited, they look like an error.
- Splitting on every heading produces clean but tiny chunks, so a claim needs
  five citations where one would do, and the retriever loses the surrounding
  context that made the passage findable.

So: split into blocks that are never broken internally (a paragraph, a whole
markdown table, a whole fenced block), then pack blocks up to a size budget,
preferring to break at a heading boundary once the chunk is already big enough
to be worth citing on its own.

The heading path is carried separately from the text rather than prepended to
it. It goes into the embedding input, because "5. Reliability" is meaningless
without "INTREP 2026/014 -- Operational Definition: Dark Vessel" above it -- but
it stays out of `Chunk.text`, because an analyst reading a citation should see
the source's words and not our scaffolding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nightglass.rag.documents import Document

# Defaults chosen against this corpus, not in the abstract. bge-m3 accepts up to
# 8192 tokens, so the ceiling here is nowhere near the model's -- it is set by
# what a person will read in a citation. ~1500 characters is roughly a long
# paragraph or a short section: big enough to stand alone, small enough that the
# claim it supports is obvious from it.
MAX_CHARS = 1500
MIN_CHARS = 400
OVERLAP_CHARS = 200

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Block:
    """An indivisible span of a document, with the headings above it."""

    text: str
    heading_path: tuple[str, ...]


@dataclass(frozen=True)
class TextChunk:
    """One embeddable, citable span."""

    doc_id: str
    chunk_id: str
    ordinal: int
    text: str
    heading: str
    char_start: int
    char_end: int

    def embedding_input(self, *, title: str) -> str:
        """What actually gets embedded.

        Title and heading path are included so that a short section is
        retrievable by what it is about rather than only by the words it happens
        to contain. This is the cheapest single improvement to retrieval quality
        available at ingest time.
        """
        header = title if not self.heading else f"{title} — {self.heading}"
        return f"{header}\n\n{self.text}"


def split_blocks(text: str) -> list[Block]:
    """Markdown -> indivisible blocks, each tagged with its heading path."""
    blocks: list[Block] = []
    stack: list[str] = []
    buf: list[str] = []
    in_fence = False
    fence_marker = ""

    def flush() -> None:
        body = "\n".join(buf).strip()
        buf.clear()
        if body:
            blocks.append(Block(text=body, heading_path=tuple(stack)))

    for line in text.splitlines():
        fence = _FENCE.match(line)
        if in_fence:
            buf.append(line)
            if fence and line.strip().startswith(fence_marker):
                in_fence = False
                flush()
            continue
        if fence:
            flush()
            in_fence = True
            fence_marker = fence.group(1)
            buf.append(line)
            continue

        heading = _HEADING.match(line)
        if heading:
            flush()
            depth = len(heading.group(1))
            del stack[depth - 1 :]
            stack.append(heading.group(2).strip())
            continue

        if not line.strip():
            # A blank line ends a block. Markdown tables survive this intact
            # because their rows carry no blank lines between them, so a whole
            # table accumulates and flushes as one indivisible block -- which
            # matters, since a table's header row is meaningless split away from
            # its body and the ICEYE glossary and imaging-mode pages are almost
            # entirely tables.
            flush()
            continue

        buf.append(line)

    if in_fence:  # unterminated fence: keep the text rather than dropping it
        flush()
    flush()
    return blocks


def chunk_document(
    doc: Document,
    *,
    max_chars: int = MAX_CHARS,
    min_chars: int = MIN_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
) -> list[TextChunk]:
    """Split one document into citable chunks with stable identifiers.

    `chunk_id` is `{doc_id}#{ordinal:04d}` and is derived only from position in
    the document, so re-ingesting an unchanged document produces the same IDs
    and updates the same points in place. Citations in an already-drafted report
    therefore survive a re-ingest, which they would not if IDs were random.
    """
    blocks = split_blocks(doc.text)
    if not blocks:
        return []

    chunks: list[TextChunk] = []
    current: list[Block] = []
    cursor = 0

    def size(bs: list[Block]) -> int:
        return sum(len(b.text) for b in bs) + 2 * max(len(bs) - 1, 0)

    def emit() -> None:
        nonlocal current, cursor
        if not current:
            return
        body = "\n\n".join(b.text for b in current)
        ordinal = len(chunks)
        chunks.append(
            TextChunk(
                doc_id=doc.doc_id,
                chunk_id=f"{doc.doc_id}#{ordinal:04d}",
                ordinal=ordinal,
                text=body,
                heading=" › ".join(current[0].heading_path),
                char_start=cursor,
                char_end=cursor + len(body),
            )
        )
        cursor += len(body)
        # Carry the tail of this chunk into the next one, so a claim that
        # straddles a boundary is still fully present in at least one chunk.
        tail: list[Block] = []
        for b in reversed(current):
            if size(tail) + len(b.text) > overlap_chars:
                break
            tail.insert(0, b)
        current = tail if len(tail) < len(current) else []

    for block in blocks:
        for piece in _hard_split(block, max_chars):
            starts_section = bool(current) and piece.heading_path != current[-1].heading_path
            over_budget = bool(current) and size(current) + len(piece.text) + 2 > max_chars
            # Break at a heading once the chunk is already worth citing on its
            # own; otherwise only when the budget forces it.
            if over_budget or (starts_section and size(current) >= min_chars):
                emit()
            current.append(piece)

    # Final flush, without the overlap carry that emit() performs.
    if current:
        body = "\n\n".join(b.text for b in current)
        ordinal = len(chunks)
        # Guard against the overlap carry alone producing a duplicate tail chunk.
        if not chunks or body != chunks[-1].text:
            chunks.append(
                TextChunk(
                    doc_id=doc.doc_id,
                    chunk_id=f"{doc.doc_id}#{ordinal:04d}",
                    ordinal=ordinal,
                    text=body,
                    heading=" › ".join(current[0].heading_path),
                    char_start=cursor,
                    char_end=cursor + len(body),
                )
            )
    return chunks


def _hard_split(block: Block, max_chars: int) -> list[Block]:
    """Break a single oversized block, preferring sentence boundaries.

    Reached by long legal recitals and by tables with many rows -- both real in
    this corpus. Splitting mid-word would be worse than splitting mid-sentence,
    and splitting mid-sentence is worse than not needing to; this does the least
    bad available thing and never loses characters.
    """
    if len(block.text) <= max_chars:
        return [block]

    out: list[Block] = []
    remaining = block.text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = max(window.rfind("\n"), window.rfind(". "), window.rfind("; "))
        if cut < max_chars // 3:  # no usable boundary -- take the whole window
            cut = max_chars
        else:
            cut += 1
        out.append(Block(text=remaining[:cut].strip(), heading_path=block.heading_path))
        remaining = remaining[cut:].lstrip()
    if remaining:
        out.append(Block(text=remaining, heading_path=block.heading_path))
    return out
