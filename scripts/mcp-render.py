#!/usr/bin/env python3
"""Pretty-print a JSON-RPC session from `scripts/mcp-stdio.sh`.

A file rather than a `python3 -c` inside the proof script, for a boring reason
that cost time: the formatting wants f-strings over dictionary keys, the shell
wants its own quotes, and the two escape each other into a syntax error that
only appears at run time. A file has no shell in the middle of it.

    scripts/mcp-stdio.sh tools/list | scripts/mcp-render.py tools
"""

from __future__ import annotations

import json
import sys

DIM = "\033[2m"
RESET = "\033[0m"


def messages():
    for line in sys.stdin:
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                print(f"  {DIM}non-JSON on stdout: {line[:120]}{RESET}")


def render_handshake(m):
    r = m["result"]
    info = r["serverInfo"]
    print(f"  serverInfo   {info['name']} {info['version']}   protocol {r['protocolVersion']}")


def render_tools(m):
    tools = m["result"]["tools"]
    print(f"  tools/list   {len(tools)}")
    for t in tools:
        required = ", ".join(t["inputSchema"].get("required", []))
        print(f"      {t['name']:<18} ({required})")


def render_status(sc):
    print(
        f"  nightglass_status   aoi={sc['aoi']}  ais={sc['ais_source']}  "
        f"ground_truth={sc['ais_source_is_ground_truth']}"
    )
    print(
        f"                      detector_precision_validated="
        f"{sc['detector_precision_validated']}"
        f"  ->  dark_rate_quotable={sc['dark_rate_quotable']}"
    )


def render_correlation(sc):
    dark = [x for x in sc["matches"] if x["status"] == "dark"]
    print(
        f"  correlate           {len(sc['scenes'])} scene(s) found, "
        f"{len(sc['detections'])} detections, "
        f"{len(sc['matches']) - len(dark)} matched, {len(dark)} unmatched"
    )
    for s in sc["scenes"]:
        print(f"      {s['id'][-4:]}  {s['provenance']['note'][:98]}")
    first = next((x for x in sc["matches"] if x["status"] == "matched"), None)
    if first:
        print("      provenance travels with the value, not a log line:")
        print(f"        {first['provenance']['note']}")
    first_dark = next(iter(dark), None)
    if first_dark:
        print(f"        {first_dark['provenance']['note'][:150]}…")


def main() -> int:
    """Render, and fail loudly if there was nothing to render.

    The silent-success trap is real here: a malformed request gets an
    "Internal Server Error" notification and no result, and a renderer that only
    knows how to print results prints nothing at all — which looks like a clean
    run with an empty section. Server-side errors are surfaced, and a session
    that produced no answers is a non-zero exit.
    """
    mode = sys.argv[1] if len(sys.argv) > 1 else "tools"
    rendered = 0
    failed = False

    for m in messages():
        if m.get("error"):
            print(f"  ERROR  {m['error']}")
            failed = True
            continue
        if m.get("method") == "notifications/message":
            params = m.get("params") or {}
            if params.get("level") in ("error", "critical"):
                print(f"  SERVER ERROR  {params.get('data')}")
                failed = True
            continue
        if m.get("id") == 1:
            render_handshake(m)
        elif mode == "tools" and m.get("id") == 2:
            render_tools(m)
            rendered += 1
        elif mode == "call":
            sc = (m.get("result") or {}).get("structuredContent") or {}
            if m.get("id") == 2:
                render_status(sc)
                rendered += 1
            elif m.get("id") == 3:
                render_correlation(sc)
                rendered += 1

    if failed or not rendered:
        if not rendered and not failed:
            print("  NO RESULTS — the server answered the handshake and nothing else.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
