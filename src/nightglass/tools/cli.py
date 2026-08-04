"""`nightglass-tools` — the §5 tools from a terminal.

    nightglass-tools list                         what exists, and its signature
    nightglass-tools call correlate --json '{…}'  one tool, raw JSON out
    nightglass-tools chain "Were there any …?"    the local model, choosing for itself

The third is §M4's second "done when". The first two exist because a tool
surface that can only be exercised through a model is a tool surface nobody can
debug: when the chain does something odd, the question is always whether the
tool or the model got it wrong, and `call` answers it in one command.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from nightglass.tools.base import ToolError

_SIGNATURES = [
    ("stac_search", "bbox, start, end", "scenes covering an area in a window"),
    ("detect_vessels", "scene_id, min_length_m=15.0", "our CFAR detector over one granule"),
    ("ais_match", "detections, time_window_min=60, radius_m=500.0", "the space–time join"),
    ("doc_search", "query, k=8, filters=None", "corpus retrieval with citations"),
    ("correlate", "bbox, start, end, min_length_m=15.0", "the three above, chained"),
    ("draft_intrep", "correlation, context_chunks", "the report, with its caveats"),
]


def _rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n{'-' * max(len(title), 44)}")


def cmd_list(_args: argparse.Namespace) -> int:
    from nightglass.config import settings

    _rule("§5 tools")
    for name, sig, what in _SIGNATURES:
        print(f"  {name:<15} ({sig})\n                  {what}")
    aoi = settings.aoi
    print(f"\n  AOI  {aoi.name}  {aoi.bbox}   AIS {aoi.ais_source}")
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    from nightglass import tools as T

    payload: dict[str, Any] = json.loads(args.json) if args.json else {}
    fn = getattr(T, args.tool, None)
    if fn is None or args.tool not in {n for n, _, _ in _SIGNATURES}:
        print(
            f"no tool {args.tool!r}. One of: "
            f"{', '.join(n for n, _, _ in _SIGNATURES)}",
            file=sys.stderr,
        )
        return 2

    result = fn(**payload)
    if isinstance(result, list):
        out = [r.model_dump(mode="json") for r in result]
    elif hasattr(result, "model_dump"):
        out = result.model_dump(mode="json")
    else:  # pragma: no cover — every §5 tool returns pydantic
        out = result
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_chain(args: argparse.Namespace) -> int:
    from nightglass.tools.chaining import chain

    question = " ".join(args.question).strip()
    if not question:
        print("nothing to ask", file=sys.stderr)
        return 2

    _rule("the local model, choosing its own tools")
    result = chain(question, max_iterations=args.max_iterations, verbose=args.verbose)
    print(result.render())

    # §M4 asks for at least three distinct tools chained, so the command reports
    # against that bar rather than leaving a reader to count the transcript.
    n = len(result.tools_used)
    print(
        f"\n\033[1m{'PASS' if n >= 3 else 'BELOW BAR'}\033[0m  "
        f"§M4 wants at least 3 distinct tools chained; this run used {n}."
    )
    return 0 if n >= 3 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nightglass-tools", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="the six §5 tools and the active AOI")
    ls.set_defaults(func=cmd_list)

    c = sub.add_parser("call", help="call one tool directly, raw JSON out")
    c.add_argument("tool")
    c.add_argument("--json", help="arguments as a JSON object", default="")
    c.set_defaults(func=cmd_call)

    ch = sub.add_parser("chain", help="let the local model pick and chain tools")
    ch.add_argument("question", nargs="+")
    ch.add_argument("--max-iterations", type=int, default=8)
    ch.add_argument("-v", "--verbose", action="store_true")
    ch.set_defaults(func=cmd_chain)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ToolError as exc:
        # A ToolError always names what would fix it, so it is a message, not a
        # traceback. Anything else keeps its traceback — an unexpected failure
        # that prints one tidy line is a failure nobody can debug.
        print(f"\n\033[33m{exc}\033[0m", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
