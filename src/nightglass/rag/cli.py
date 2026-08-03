"""`nightglass-corpus` — fetch, ingest, search, ask.

One entry point rather than four scripts, because these are four views of one
pipeline and the interesting thing about them is the seam: `fetch` is the only
subcommand that touches the network, and it runs in a different image on a
different network from the other three. Running `nightglass-corpus fetch` inside
the enclave fails at DNS resolution, which is the correct outcome and worth
seeing at least once.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nightglass.config import settings
from nightglass.rag.documents import CorpusStats, load_corpus
from nightglass.rag.embed import Embedder
from nightglass.rag.index import DocumentIndex


def _index() -> DocumentIndex:
    return DocumentIndex(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedder=Embedder(host=settings.ollama_host, model=settings.ollama_embed_model),
    )


def _rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n{'-' * max(len(title), 40)}")


# ---------------------------------------------------------------------------


def cmd_fetch(args: argparse.Namespace) -> int:
    # Imported here, not at module scope: this is the one code path that opens
    # a socket outward, and it should not be a transitive import of the enclave's
    # `ingest`, `search` or `ask`.
    from nightglass.rag.fetch import FetchError, fetch_corpus

    try:
        fetch_corpus(
            sources_path=Path(args.sources),
            out_root=Path(args.out or settings.corpus_dir),
            force=args.force,
            only=args.only,
        )
    except FetchError as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    roots = [Path(r) for r in args.root] if args.root else settings.corpus_roots
    docs = load_corpus(roots)
    if not docs:
        print(
            "no documents found in:\n  " + "\n  ".join(str(r) for r in roots) + "\n\n"
            "The synthetic half is committed; the fetched half needs `make fetch-corpus`.",
            file=sys.stderr,
        )
        return 1

    stats = CorpusStats.of(docs)
    _rule("corpus on disk")
    print(f"documents  {stats.documents}\ncharacters {stats.characters:,}")
    for label, table in (
        ("publisher", stats.by_publisher),
        ("origin", stats.by_origin),
        ("language", stats.by_language),
    ):
        print(f"  by {label}: " + ", ".join(f"{k}={v}" for k, v in sorted(table.items())))

    index = _index()
    if args.recreate:
        print(f"\nrecreating collection {settings.qdrant_collection}")
        index.ensure_collection(recreate=True)

    _rule("ingesting")
    report = index.ingest(docs, progress=args.verbose)
    _rule("indexed")
    print(report.render())
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    chunks = _index().search(
        args.query, k=args.k, filters=_filters(args.filter), min_score=args.min_score
    )
    if not chunks:
        print("no chunks retrieved")
        return 0
    _rule(f"top {len(chunks)} for: {args.query}")
    for c in chunks:
        print(f"\n\033[1m{c.score:.4f}\033[0m  [{c.chunk_id}]  ({c.classification})")
        print(f"        {c.title}")
        body = " ".join(c.text.split())
        print(f"        {body[: args.chars]}{'…' if len(body) > args.chars else ''}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from nightglass.rag.answer import answer_question, answer_ungrounded

    if args.ungrounded:
        _rule("UNGROUNDED — no retrieval, model priors only")
        print(
            answer_ungrounded(
                args.question,
                ollama_host=settings.ollama_host,
                chat_model=settings.ollama_chat_model,
            )
        )
        return 0

    result = answer_question(
        args.question,
        index=_index(),
        ollama_host=settings.ollama_host,
        chat_model=settings.ollama_chat_model,
        k=args.k,
        filters=_filters(args.filter),
        min_score=args.min_score if args.min_score is not None else settings.rag_min_score,
    )

    _rule(f"GROUNDED — {result.classification}")
    print(f"Q: {result.question}\n")
    if not result.answered:
        print(f"\033[1m{result.refusal}\033[0m")
        print(
            f"\n{len(result.retrieved)} chunk(s) were retrieved but none supported an answer."
            if result.retrieved
            else "\nNothing was retrieved."
        )
    else:
        for i, claim in enumerate(result.claims, 1):
            print(f"{i}. {claim.text}")
            print(f"   \033[2m{' '.join('[' + c + ']' for c in claim.chunk_ids)}\033[0m")

    if result.dropped_citations:
        print(
            f"\n\033[33mdropped {len(result.dropped_citations)} citation(s) not in the "
            f"retrieved set: {', '.join(result.dropped_citations)}\033[0m"
        )

    if args.sources and result.answered:
        _rule("sources")
        by_id = {c.chunk_id: c for c in result.retrieved}
        for cid in result.cited_chunk_ids:
            c = by_id[cid]
            print(f"[{cid}]  ({c.classification})  {c.title}")
            if c.source_url:
                print(f"         {c.source_url}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    index = _index()
    _rule("index")
    for k, v in index.stats().items():
        print(f"  {k:<14} {v}")
    if index.count():
        for key in ("publisher", "origin", "classification", "language", "doc_type"):
            _rule(f"by {key}")
            for value, n in index.facet(key).items():
                print(f"  {n:>5}  {value}")
    return 0


def _filters(pairs: list[str] | None) -> dict | None:
    if not pairs:
        return None
    out: dict[str, list[str] | str] = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"--filter expects key=value, got {p!r}")
        k, v = p.split("=", 1)
        if k in out:
            existing = out[k]
            out[k] = [*existing, v] if isinstance(existing, list) else [existing, v]
        else:
            out[k] = v
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nightglass-corpus", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="ONLINE. Download the public half of the corpus.")
    f.add_argument("--sources", default="/app/corpus/sources.yaml")
    f.add_argument("--out", default=None, help="corpus root (default: NIGHTGLASS_CORPUS_DIR)")
    f.add_argument("--force", action="store_true", help="re-download even if cached")
    f.add_argument("--only", nargs="*", help="doc_id or publisher to restrict to")
    f.set_defaults(func=cmd_fetch)

    i = sub.add_parser("ingest", help="Chunk, embed and index the corpus.")
    i.add_argument("--root", nargs="*", help="override the corpus roots")
    i.add_argument("--recreate", action="store_true", help="drop the collection first")
    i.add_argument("-v", "--verbose", action="store_true")
    i.set_defaults(func=cmd_ingest)

    s = sub.add_parser("search", help="doc_search: retrieve chunks for a query.")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=settings.rag_top_k)
    s.add_argument("--filter", action="append", help="key=value, repeatable")
    s.add_argument("--min-score", type=float, default=None)
    s.add_argument("--chars", type=int, default=280, help="preview length")
    s.set_defaults(func=cmd_search)

    a = sub.add_parser("ask", help="Answer a question, grounded and cited.")
    a.add_argument("question")
    a.add_argument("-k", type=int, default=settings.rag_top_k)
    a.add_argument("--filter", action="append", help="key=value, repeatable")
    a.add_argument("--min-score", type=float, default=None)
    a.add_argument("--sources", action="store_true", help="list the cited sources")
    a.add_argument(
        "--ungrounded",
        action="store_true",
        help="answer from model priors with no retrieval — the 'before' half of §M2",
    )
    a.set_defaults(func=cmd_ask)

    st = sub.add_parser("stats", help="What is in the index.")
    st.set_defaults(func=cmd_stats)

    args = ap.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
