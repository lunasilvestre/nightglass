#!/usr/bin/env bash
# docs/demo.cast -> docs/demo.gif + docs/demo.mp4, paced for a human.
#
# Three artifacts and one intermediate, because a recording made by a machine is
# paced for a machine:
#
#   docs/demo.cast    THE RECORD. Real time, nothing touched, ~62 s. Check the
#                     others against this one. `asciinema play docs/demo.cast`
#   (paced cast)      the record with its TIMESTAMPS rewritten and not one byte
#                     of its content — see scripts/pace-demo.py. Built here into
#                     a temp directory; it is derived, so it is not committed.
#   docs/demo.mp4     the one to watch. Play, pause, scrub.
#   docs/demo.gif     inline in the README, for people who will not click.
#
# What the pacing fixes, both measured on the raw cast:
#
#   * 30.5 s of frozen screen while the 14B model chose its tools. In a video a
#     frozen frame is indistinguishable from a stall, so it is capped to a beat.
#     The demo says out loud how long the step really takes, and the uncapped
#     cast ships beside the video.
#   * 33 lines arriving in ONE event immediately afterwards — a 34-row screen
#     filled and scrolled away faster than anyone could read it.
#
# `scripts/check-demo.py` then measures the result from both ends: exact per-row
# dwell from the timeline, and a one-second slice of the encoded video. It exits
# non-zero if any row is on screen for under three seconds, so this script does
# not "finish" on a video nobody can follow.
#
# ffmpeg does the GIF -> MP4 step because agg only writes GIFs. `-tune
# stillimage` is the right x264 tuning for a terminal: almost every frame equals
# the last, and the detail that matters is the edge of a glyph.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

CAST=${CAST:-docs/demo.cast}
[[ -f "$CAST" ]] || { echo "no recording at $CAST — run scripts/demo.sh --record" >&2; exit 1; }

for tool in agg ffmpeg python3; do
  command -v "$tool" >/dev/null || { echo "$tool is not on PATH" >&2; exit 1; }
done

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
PACED="$tmp/demo.paced.cast"

# The closing hold belongs to the renderer, not to the cast: agg drops a
# trailing event that carries no bytes, so a hold written into the timeline
# silently disappears. Asked for rather than repeated, so there is one number.
HOLD=$(scripts/pace-demo.py --tail)

echo ">> pacing"
scripts/pace-demo.py "$CAST" "$PACED"

# --idle-time-limit is passed explicitly even though the paced cast has no idle
# left worth capping: agg's default is 5 SECONDS, not "off", and omitting it
# silently re-compresses what was just spread out. A first attempt at a
# "full fidelity" render lost a quarter of the recording to that default.
echo ">> docs/demo.mp4"
# 8 fps is plenty for text that arrives a line at a time, and it is the single
# biggest lever on file size now that the pacing keeps the screen always moving:
# at 10 fps the same video is 6.4 MB, at 8 it is 3.6 MB and looks the same.
agg --idle-time-limit 600 --fps-cap 8 --font-size 18 --theme asciinema \
  --last-frame-duration "$HOLD" "$PACED" "$tmp/full.gif"
ffmpeg -y -loglevel error -i "$tmp/full.gif" \
  -vf "fps=8,scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=neighbor" \
  -c:v libx264 -preset slower -tune stillimage -crf 22 \
  -pix_fmt yuv420p -movflags +faststart docs/demo.mp4

echo ">> docs/demo.gif"
# Smaller and coarser than the MP4 on purpose: this one is inlined in a README
# that people load on a phone, and a GIF has no codec to lean on.
agg --idle-time-limit 600 --fps-cap 4 --font-size 14 --theme asciinema \
  --last-frame-duration "$HOLD" "$PACED" docs/demo.gif

echo
for f in docs/demo.cast docs/demo.gif docs/demo.mp4; do
  printf '  %-18s %6.1f MB\n' "$f" "$(stat -c%s "$f" | awk '{print $1/1e6}')"
done

scripts/check-demo.py "$PACED" docs/demo.mp4
