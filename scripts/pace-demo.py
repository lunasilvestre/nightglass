#!/usr/bin/env python3
"""Retime an asciicast for viewing. Moves timestamps; never touches a byte.

    scripts/pace-demo.py docs/demo.cast build/demo.paced.cast

A live terminal recording is paced for the machine, not for a viewer, and this
one had both failure modes at once:

* **30.5 seconds of nothing** while the 14B model chose its tools. On screen
  that is a frozen frame, and a frozen frame in a video is indistinguishable
  from a stalled download.
* **33 lines in a single instant** the moment it finished — a 34-row screen
  filled in one event, immediately scrolled away by the next section. Anything
  in that block was on screen for under a second.

Both are fixed by rewriting timestamps. The rule this enforces is the one that
matters to a human rather than to a stopwatch:

    **every row stays on screen for at least MIN_DWELL seconds.**

A row is visible from the moment it is written until `height` further rows have
pushed it off the top, so that is a constraint on the emission times directly:
``t[n] >= t[n - height] + MIN_DWELL``. Enforcing it turns "dump a screen and
move on" into a scroll no faster than the eye can follow, without anyone having
to guess at a per-section delay.

Long waits are capped rather than removed, so the rhythm of a slow step survives
as a beat. Short ones get a floor, so a burst streams instead of blinking.

**The byte stream is identical.** Every chunk is re-emitted in its original
order with its original content; only the timestamps differ, and the output is a
valid asciicast that `asciinema play` will run. `docs/demo.cast` remains the
unretimed record and is the thing to check this against.
"""

from __future__ import annotations

import json
import sys

#: Seconds a row must remain on screen before anything is allowed to scroll it
#: off. The brief was three; this is the margin over it, because the check
#: downstream measures the rendered video rather than this arithmetic.
MIN_DWELL = 4.5

#: Longest pause kept. The model's ~35 s of thinking becomes a beat that reads
#: as "this took a while" instead of a stall. The demo says out loud how long it
#: really takes, and the unretimed cast is shipped beside the video.
IDLE_CAP = 2.5

#: Floor between consecutive chunks, so a burst arrives as a stream.
LINE_DELAY = 0.10

#: Hold on the final frame. The closing shot is the whole argument —
#: `Could not resolve host`, under everything that just ran — so it gets to sit.
TAIL = 8.0


def split_lines(data: str) -> list[str]:
    """One chunk per line, newlines kept. Concatenates back to the input."""
    out, buf = [], ""
    for ch in data:
        buf += ch
        if ch == "\n":
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def pace(cast: list[str]) -> list[str]:
    header = json.loads(cast[0])
    height = int(header.get("height", 24))
    events = [json.loads(line) for line in cast[1:] if line.strip()]

    rows: list[float] = []  # when each completed row was written
    out: list[str] = []
    t = 0.0
    prev_src = 0.0

    for src_t, kind, data in events:
        gap = min(src_t - prev_src, IDLE_CAP)
        prev_src = src_t
        if kind != "o":  # input/marker events: keep, do not pace
            out.append(json.dumps([round(t, 6), kind, data]))
            continue
        for i, chunk in enumerate(split_lines(data)):
            t += max(gap if i == 0 else 0.0, LINE_DELAY)
            # The row about to be pushed off the top is rows[-height]. It may
            # not go until it has had its time.
            if len(rows) >= height:
                t = max(t, rows[-height] + MIN_DWELL)
            out.append(json.dumps([round(t, 6), kind, chunk]))
            if chunk.endswith("\n"):
                rows.append(t)

    header.pop("idle_time_limit", None)
    return [json.dumps(header), *out]


def main(argv: list[str]) -> int:
    # `--tail` so the renderer can ask for the hold rather than repeat the
    # number: one source of truth for a value two scripts have to agree on.
    if len(argv) == 2 and argv[1] == "--tail":
        print(TAIL)
        return 0
    if len(argv) != 3:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    src, dst = argv[1], argv[2]
    cast = open(src, encoding="utf-8").read().splitlines()
    paced = pace(cast)

    # Sanity, every run: the bytes must be the same bytes.
    def payload(lines: list[str]) -> str:
        return "".join(json.loads(x)[2] for x in lines[1:] if json.loads(x)[1] == "o")

    if payload(cast) != payload(paced):
        print("pace-demo: the byte stream changed — refusing to write", file=sys.stderr)
        return 1

    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(paced) + "\n")

    # The tail is NOT appended as a trailing empty event. agg drops an event
    # with no bytes in it, so a cast that "ends" 8 s after its last output
    # renders 8 s shorter than it claims — and the dwell arithmetic, which
    # trusts the cast, then credits the closing rows with time the video never
    # gives them. It was reporting a comfortable 4.5 s floor over a video whose
    # final screen lasted 1 s. The renderer is told the hold instead, and
    # check-demo now measures the video rather than the cast.
    last = json.loads(paced[-1])[0]
    src_end = json.loads(cast[-1])[0]
    print(f"  {src}  {src_end:.1f} s  ->  {dst}  {last:.1f} s + {TAIL:g} s hold"
          f"   (dwell >= {MIN_DWELL:g} s, idle capped at {IDLE_CAP:g} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
