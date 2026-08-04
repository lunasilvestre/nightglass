#!/usr/bin/env bash
# The demo, as a repeatable command. ~60 seconds.
#
# The budget was 90 s. This comes in under it because the content ran out, not because
# anything was cut for time; the variable part is the ~35 s the 14B model spends
# choosing its tools in step 2, which is live and differs run to run.
#
# The demo records over the Lisbon AOI. The Lisbon AOI has no AIS, and that is not
# an oversight: Denmark is the only European state publishing free point-level
# *historical* AIS, which is exactly why Denmark is the validation AOI.
# So a recording of Lisbon alone would be a recording of a refusal — honest, and
# not a demo — or worse, one that let a viewer read 71 detections as 71 dark
# vessels.
#
# This shows both, in the order an analyst meets them. The deployment AOI takes
# the question, produces detections and WITHHOLDS the verdict; the claim about
# the matcher is then made where there is ground truth to make it against; and
# the detector is cross-checked over Lisbon against somebody else's. Two AOIs on
# screen is also the config-driven argument made visible instead of asserted — the AOI is
# configuration, and nothing in the code knows which one it is serving.
#
# Everything here is live. There is no pre-recorded output, no seeded answer,
# and the 14B model in step 2 is choosing its own tools while you watch.
#
#   scripts/demo.sh              run it
#   scripts/demo.sh --record     run it under asciinema into docs/demo.cast
#
# Prerequisites: `make up`, then the provisioning steps in the README, then any
# one of the proofs — the detector runs cold at 14–20 s per scene, and this
# script is timed against a warm database. It says so rather than being slow.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
YELL=$'\033[33m'; GREEN=$'\033[32m'; CYAN=$'\033[36m'

if [[ "${1:-}" == "--record" ]]; then
  out=${2:-docs/demo.cast}
  mkdir -p "$(dirname "$out")"
  rm -f "$out"
  # Deliberately no --idle-time-limit. The cast keeps real time, including the
  # ~35 s the 14B model spends choosing its tools, because that is how long it
  # takes and a recording that hides it is a recording that misleads. The GIF
  # rendered from it caps idle for readability and says so; the cast is the
  # artifact to check the GIF against.
  exec asciinema rec "$out" --cols 100 --rows 34 \
    --title "NIGHTGLASS — air-gapped dark-vessel detection, end to end" \
    --command "scripts/demo.sh"
fi

beat() { sleep "${DEMO_BEAT:-0.4}"; }
rule() { printf '\n%s%s%s\n%s\n' "$BOLD" "$1" "$RESET" "$(printf '%.0s─' $(seq 1 ${#1}))"; beat; }
say()  { printf '%s%s%s\n' "$DIM" "$1" "$RESET"; }

if ! docker compose ps --status running --services 2>/dev/null | grep -qx api; then
  echo "The enclave is not running. Try: make up" >&2
  exit 1
fi

api()   { docker compose exec -T -e NIGHTGLASS_AOI="$1" api "${@:2}"; }
agent() { docker compose --profile cli run --rm -T -e NIGHTGLASS_AOI="$1" agent "${@:2}" 2>/dev/null; }

Q=${Q:-"Were there any vessels with no AIS correspondence off Lisbon on 13 June 2026?"}

printf '\n%sNIGHTGLASS%s  %sSAR dark-vessel detection that runs with no route to the internet%s\n' \
  "$BOLD" "$RESET" "$DIM" "$RESET"

# ---------------------------------------------------------------------------
rule "1. One deployment, one AOI. Which one is configuration."
# The one-line-per-tool form. The second line of each entry is prose, and the
# contract — the tool signatures — is what is worth the screen space here.
api lisbon nightglass-tools list | sed '/^ \{10,\}/d'

# ---------------------------------------------------------------------------
rule "2. An analyst asks. The graph runs, drafts, and stops."
say "\$ nightglass-agent ask \"$Q\""
say "parse -> plan -> tools -> correlate -> draft_intrep -> HUMAN_GATE"
say "(the 14B model is choosing its own tools — ~35 s, live, nothing cached)"
# `tee` to a file rather than to /dev/stderr: the draft has to stream onto the
# screen as it is produced — watching the plan, then the tool choices, then the
# halt is most of the point — and routing it through stderr to capture the
# thread id reorders the whole script the moment stdout is not a terminal.
TRANSCRIPT=$(mktemp); trap 'rm -f "$TRANSCRIPT"' EXIT
agent lisbon ask "$Q" | tee "$TRANSCRIPT"
THREAD=$(grep -oE 'ng-[0-9a-f]{12}' "$TRANSCRIPT" | head -1)
[[ -n "${THREAD:-}" ]] || { echo "the agent did not reach the gate" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Read the count out of the run rather than writing it into the heading: a demo
# script that hardcodes a number is a demo script that will one day state a
# number the run did not produce.
N=$(grep -oE '[0-9]+ detections' "$TRANSCRIPT" | head -1 | cut -d' ' -f1)
rule "3. ${N:-The} detections, and no verdict. The refusal is the feature."
say "Counting them all as dark would have read as a spectacular finding. The"
say "caveat below is assembled from the correlation object, not generated:"
# Captured rather than piped straight through: `grep -m1` closes the pipe as
# soon as it matches, and under `pipefail` a perfectly good early exit upstream
# becomes a failed script. Same trap agent-proof.sh hit with `head`.
CAVEAT=$(agent lisbon show "$THREAD" | tr -s ' ' | grep "NO AIS was available" | sed -n 1p || true)
[[ -n "$CAVEAT" ]] || CAVEAT="(the no-AIS caveat is missing — that is a bug, not a demo)"
printf '%s\n' "$CAVEAT" | fold -s -w 74 | sed "s/^/  $CYAN/;s/\$/$RESET/"
beat

# ---------------------------------------------------------------------------
rule "4. Why a missing transponder is a lead and not a finding."
say "60 documents embedded locally with bge-m3. The answer is assembled from"
say "retrieved chunks, and every line carries the chunk it came from."
api lisbon nightglass-corpus ask \
  "when may a vessel lawfully switch its AIS off?" --sources | sed -n '4,9p'
beat
say "And asked something the corpus does not cover, it does not improvise:"
api lisbon nightglass-corpus ask \
  "How many dark vessels were detected off Madeira in 2019?" | sed -n '4,8p'

# ---------------------------------------------------------------------------
rule "5. A human releases it — in a different container, from Postgres."
say "The process that drafted it has already exited. Nothing is shared but the"
say "database, so the gate is a real halt rather than a blocked read on stdin."
agent lisbon approve "$THREAD" --note "positions checked against the chips" \
  | sed -n '1,12p'
printf '  %s^ the marking has lost "DRAFT — NOT RELEASABLE". draft_intrep always\n' "$DIM"
printf '    returns releasable=false; this node past the interrupt is the only\n'
printf '    place in the system that can flip it.%s\n' "$RESET"

# ---------------------------------------------------------------------------
rule "6. The same code, pointed at the AOI that has ground truth."
say "Denmark publishes free point-level historical AIS — the reason §3.1 made"
say "it the validation AOI. Nothing changes here but NIGHTGLASS_AOI."
say "One SQL query over granule S1D_…_BC13, 17 July 2026:"
api kattegat nightglass-spatial dark \
  S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13 --limit 3 \
  | sed -n '4,9p;13,17p'

# ---------------------------------------------------------------------------
rule "7. 40% unmatched — and it is still not a dark-vessel rate."
say "Two independent guards, over MCP, over the same stdio pipe Claude uses."
HOLD=3 AOI=kattegat scripts/mcp-stdio.sh nightglass_status | scripts/mcp-render.py call
printf '  %sThe AIS is ground truth, so that half passes. The detector is not\n' "$YELL"
printf '  precision-validated, so the rate is refused anyway.%s\n' "$RESET"
beat

# ---------------------------------------------------------------------------
rule "8. So over Lisbon, ask a second detector instead."
api lisbon nightglass-spatial gfw-compare | sed -n '/^ours/,/^ *median/p'
beat

# ---------------------------------------------------------------------------
rule "9. And none of that had a way out."
say "\$ docker compose exec api curl -m 5 https://example.com"
docker compose exec -T api curl -sS -m 5 https://example.com 2>&1 | head -1 || true
printf '\n  %sNot a timeout — an internal network resolves no external name, so no\n' "$GREEN"
printf '  packet is ever sent. Every number above came from the far side of that.%s\n\n' "$RESET"
