#!/usr/bin/env bash
# EXECUTION_SPEC §M3's proof, as a repeatable command.
#
#   "Done when: one SQL query returns detections with no AIS correspondence
#    inside a space-time window."
#
# The milestone's own advice is followed literally -- the join is plain SQL in
# src/nightglass/spatial/sql/dark_vessels.sql, hand-checked, before the agent
# ever touches it. This script runs the whole chain over the Danish validation
# AOI and then does the part a SQL result cannot do: it renders the pixels, so
# the detections can be looked at rather than counted.
#
# Denmark, not Portugal, and that is the point of having two AOIs (§3.1). Denmark
# is the only one with free point-level historical AIS, so it is the only place a
# claim about the matcher can be checked instead of asserted.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'; YELL=$'\033[33m'

SCENE=${SCENE:-/app/data/raw/sar/S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13.zip}
AIS=${AIS:-/app/data/interim/ais_kattegat_20260717_0453-0553.csv}
AOI=${AOI:-kattegat}
SCENE_ID=$(basename "${SCENE%.zip}")

run() { docker compose exec -T -e NIGHTGLASS_AOI="$AOI" api nightglass-spatial "$@"; }

rule() { printf '\n%s%s%s\n%s\n' "$BOLD" "$1" "$RESET" "$(printf '%.0s─' $(seq 1 ${#1}))"; }

if ! docker compose ps --status running --services 2>/dev/null | grep -qx api; then
  echo "The enclave is not running. Try: make up" >&2
  exit 1
fi

if [[ ! -f data/coastline/coastline_${AOI}.geojson ]]; then
  echo "${YELL}No coastline for ${AOI}. Run 'make fetch-coastline' first.${RESET}" >&2
  echo "${YELL}Without it the detector cannot tell a skerry from a hull, and the${RESET}" >&2
  echo "${YELL}unmatched rate below would be dominated by rocks.${RESET}" >&2
  exit 1
fi

rule "0. The enclave still has no way out"
echo "${DIM}\$ docker compose exec api curl -m 5 https://example.com${RESET}"
docker compose exec -T api curl -m 5 https://example.com 2>&1 | head -2 || true
echo "${DIM}Everything below runs on the far side of that.${RESET}"

rule "1. Schema"
run migrate

rule "2. The scene, as a STAC item"
run scenes "$SCENE"

rule "3. Our own detector, over real pixels"
run detect "$SCENE"

rule "4. AIS for the acquisition window"
run load-ais "$AIS" --granule "$SCENE"

rule "5. §M3 — one SQL query, detections with no AIS correspondence"
run dark "$SCENE_ID" --limit 15

rule "6. The correction that makes the match work, measured not asserted"
run validate-shift "$SCENE" "$AIS"

rule "7. The evidence — look at the pixels, not the table"
run render "$SCENE"

cat <<EOF

${BOLD}What to look at${RESET}
  data/out/chips_top.png        every detection at native resolution
  data/out/overview.png         the AOI in radar geometry, land mask drawn on it
  data/out/azimuth_correction.png   the sign, settled by measurement
  data/out/length_agreement.png     detected extent vs AIS-reported length

${DIM}A detector that is only ever counted is a detector nobody has checked. Both
failures found while building this one — a land mask tracing the coastline, and
a CFAR whose statistics had collapsed — were invisible in the numbers and
obvious in the first render.${RESET}
EOF
