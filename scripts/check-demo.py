#!/usr/bin/env python3
"""Adversarial read of the paced recording: can a human actually follow it?

    scripts/check-demo.py build/demo.paced.cast [docs/demo.mp4]

Two independent measurements, because either alone can pass while the video is
still unwatchable.

**1. Per-row dwell, from the cast.** A row is on screen from the moment it is
written until `height` further rows push it off the top. That interval is
computed for every row and the distribution is reported, with the worst
offenders named. This is exact — it is arithmetic over the timeline, not a
sample — and it is the measurement that catches a screen dumped at once.

**2. A one-second slicer over the rendered video.** The check above trusts the
cast; this one trusts nothing and samples the artifact that actually ships. The
video is decoded at 1 fps and consecutive frames are compared. A row that is
visible for at least three seconds must appear in at least three consecutive
one-second samples, so the number of frames each screen state survives is a
direct check on the same claim from the other end. Frames that differ from
their neighbour by a large fraction of the image mean a screen that turned over
between samples — the failure the eye reads as "it flashed past".

Exit code 1 if any row falls under the floor, so this can gate a release.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

#: The brief: nothing on screen for less than this.
FLOOR = 3.0

#: How much of the screen may be replaced between two one-second samples. Above
#: this the viewer is being asked to read something that is already gone.
CHURN_CEILING = 0.30

#: Below this, two samples are the same screen. Not zero: the video is lossy.
STILL = 0.02

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def rows_from_cast(path: Path) -> tuple[list[tuple[float, str]], int, float]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    height = int(header.get("height", 24))
    rows: list[tuple[float, str]] = []
    buf = ""
    end = 0.0
    for raw in lines[1:]:
        t, kind, data = json.loads(raw)
        end = max(end, t)
        if kind != "o":
            continue
        for ch in data:
            if ch == "\n":
                rows.append((t, ANSI.sub("", buf).rstrip()))
                buf = ""
            else:
                buf += ch
    return rows, height, end


def check_dwell(rows, height, end) -> list[tuple[float, float, str]]:
    """(dwell, t, text) per row. A row lives until `height` rows later."""
    out = []
    for i, (t, text) in enumerate(rows):
        gone = rows[i + height][0] if i + height < len(rows) else end
        out.append((gone - t, t, text))
    return out


def video_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def slice_video(path: Path, fps: float = 1.0) -> list[bytes]:
    """One raw greyscale frame per second, straight out of ffmpeg."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    st = json.loads(probe.stdout)["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    # Downscale hard: this compares layouts, not glyphs, and a 1228x980 frame
    # per second is 60 MB of pipe for a question about whether the screen moved.
    sw, sh = w // 8, h // 8
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"fps={fps},scale={sw}:{sh},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    size = sw * sh
    return [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    cast = Path(argv[1])
    rows, height, end = rows_from_cast(cast)
    # When there is a video, IT decides when the last rows stop being visible.
    # The cast's own end is only where output stopped, and the renderer's
    # closing hold lives outside the timeline — trusting the cast credited the
    # final screen with 8 s it did not have, and the floor passed on a video
    # whose closing shot lasted one second.
    if len(argv) > 2:
        end = video_duration(Path(argv[2]))
    dwell = check_dwell(rows, height, end)

    source = "video" if len(argv) > 2 else "cast"
    print(f"\n\033[1mper-row dwell\033[0m  ({len(rows)} rows, {height}-row screen, "
          f"{end:.1f} s per the {source})\n{'-' * 60}")
    values = sorted(d for d, _, _ in dwell)
    for label, v in (("min", values[0]), ("p05", values[len(values) // 20]),
                     ("median", values[len(values) // 2]), ("max", values[-1])):
        print(f"  {label:>6}  {v:6.2f} s")

    under = [(d, t, s) for d, t, s in dwell if d < FLOOR]
    if under:
        print(f"\n  \033[31m{len(under)} row(s) below the {FLOOR:g} s floor:\033[0m")
        for d, t, s in sorted(under)[:12]:
            print(f"    {d:5.2f} s  at {t:6.1f}  {s[:66]}")
    else:
        print(f"\n  \033[32mevery row holds for at least {FLOOR:g} s"
              f" (worst {values[0]:.2f} s)\033[0m")

    if len(argv) < 3:
        return 1 if under else 0

    video = Path(argv[2])
    frames = slice_video(video)
    print(f"\n\033[1mone-second slicer\033[0m  ({video}, {len(frames)} samples)"
          f"\n{'-' * 60}")
    # Group consecutive identical-ish samples. Two frames a second apart that
    # share almost nothing mean the screen turned over between them.
    churn = []
    for i in range(1, len(frames)):
        a, b = frames[i - 1], frames[i]
        diff = sum(1 for x, y in zip(a, b) if abs(x - y) > 24) / len(a)
        churn.append(diff)
    ordered = sorted(churn)
    hot = [(i, d) for i, d in enumerate(churn, start=1) if d > CHURN_CEILING]
    print(f"  screen replaced between one sample and the next:")
    print(f"    median {ordered[len(ordered) // 2]:6.1%}"
          f"   p95 {ordered[int(len(ordered) * 0.95)]:6.1%}"
          f"   max {ordered[-1]:6.1%}")
    if hot:
        print(f"  \033[31m{len(hot)} sample(s) over the {CHURN_CEILING:.0%} ceiling:\033[0m")
        for i, d in hot[:10]:
            print(f"    {i:>3} s -> {i + 1:>3} s   {d:.0%} replaced in one second")
    else:
        print(f"  \033[32mnothing turns over more than {CHURN_CEILING:.0%} in a second"
              f" (worst {ordered[-1]:.0%})\033[0m")
    # Streaming text changes the screen every second by design, so "how many
    # distinct frames" says nothing. What matters is whether the screen ever
    # goes *dead* — a long unchanging run is the stall this pacing exists to
    # remove, and the closing hold is a deliberate one worth confirming.
    #
    # Compared with a threshold rather than by equality. A first version hashed
    # each frame and reported the longest identical run as 3 s over a video
    # whose final shot is held for 8: H.264 is lossy, so two decoded frames of a
    # motionless screen are never byte-identical and the metric was measuring
    # the encoder instead of the content.
    run = best = 0
    for d in churn:
        run = run + 1 if d < STILL else 0
        best = max(best, run)
    print(f"  longest unchanging stretch      {best} s   (the closing hold)")

    return 1 if (under or hot) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
