"""Agent entrypoint.

M0 placeholder. The graph lands at M5:

    parse → plan → tools → correlate → draft_intrep → HUMAN_GATE → release

Two things are already decided and worth stating here so they don't get lost.

**The gate uses LangGraph's interrupt, not `input()`.** A bare prompt blocks a
thread; an interrupt genuinely halts the graph with inspectable, persisted
state that can be resumed in a different process. That distinction is the
interesting part of the milestone, and "how does the interrupt actually persist
state" is on §9's list of expected questions.

**The graph needs a loop-breaker.** Measured during pre-dev: when a tool result
contradicted what the model asked for — a 17 Jul scene returned after it
requested 31 Jul–3 Aug — Qwen re-called `stac_search` four times with tweaked
date ranges instead of advancing. That is arguably correct behaviour, since the
result genuinely did not satisfy the request, but unbounded it burns the whole
context window. Needs a max-iterations guard plus a same-tool-same-args repeat
detector.

This is a one-shot process, not a daemon — hence `profiles: ["cli"]` in the
compose file. A graph that runs to a human gate and halts has nothing to serve
between invocations, and wrapping it in a server would exist only to satisfy a
healthcheck.
"""

from __future__ import annotations

import argparse

from nightglass.config import settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nightglass-agent",
        description="Ask a question about the active AOI (M5).",
    )
    parser.add_argument("question", nargs="*", help="analyst question, any language")
    args = parser.parse_args(argv)

    aoi = settings.aoi
    question = " ".join(args.question).strip()

    print(f"NIGHTGLASS agent — AOI {aoi.name} {aoi.bbox}")
    if question:
        print(f"question: {question}")
    print("\nNot implemented yet: the LangGraph agent arrives at M5.")
    print("Available now: `docker compose exec api curl -s localhost:8000/config`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
