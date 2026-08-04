#!/usr/bin/env bash
# EXECUTION_SPEC §M4's proof, as a repeatable command.
#
#   "Done when: the tools are callable over MCP from Claude Desktop, *and* the
#    local Qwen model chains at least three of them to answer a query."
#
# Both halves, because the pair is the point: the same six functions serve a
# frontier model over a pipe from outside the enclave and a 14B model running
# inside it, and a tool surface that only works with one of those is not an
# air-gapped capability. §M4 calls that "a good part of the story. Worth
# demonstrating both."
#
# The MCP half is proven by speaking the protocol, not by asserting it. Claude
# Desktop is a GUI and cannot be scripted, so this drives the identical command
# Desktop is configured with -- `docker exec -i nightglass-mcp nightglass-mcp
# stdio` -- through a raw JSON-RPC handshake and calls a tool over it. If this
# passes, what Desktop does next is the same bytes.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'; YELL=$'\033[33m'; GREEN=$'\033[32m'

AOI=${AOI:-kattegat}
BBOX=${BBOX:-10.5,55.5,12.5,57.5}
START=${START:-2026-07-17T00:00:00Z}
END=${END:-2026-07-18T00:00:00Z}
# The question asks two things a single tool cannot answer together: what is in
# the water, and what an unmatched detection means. The first is spatial, the
# second is documentary. Asked only the first, the model reasonably reaches for
# `correlate` — which does all three spatial steps in one call and is the right
# choice, but demonstrates no chaining. §M4's bar is about chaining, so the
# question has to be one a real analyst would ask in full.
QUESTION=${QUESTION:-"First, which Sentinel-1 scenes cover the area of interest on 17 July 2026? Then, for the scene acquired at 05:23, how many vessels did the detector find, and which of those have no AIS correspondence?"}

rule() { printf '\n%s%s%s\n%s\n' "$BOLD" "$1" "$RESET" "$(printf '%.0s─' $(seq 1 ${#1}))"; }

if ! docker compose ps --status running --services 2>/dev/null | grep -qx mcp; then
  echo "The enclave is not running. Try: make up" >&2
  exit 1
fi

export AOI

rule "0. The enclave still has no way out"
echo "${DIM}\$ docker compose exec mcp curl -m 5 https://example.com${RESET}"
docker compose exec -T mcp curl -m 5 https://example.com 2>&1 | head -2 || true
echo "${DIM}Everything below crosses that boundary over a pipe, not a port.${RESET}"

rule "1. The MCP surface, over the transport Claude Desktop uses"
echo "${DIM}\$ docker exec -i nightglass-mcp nightglass-mcp stdio${RESET}"
echo "${DIM}A container on an internal network gets no host port mapping at all, so${RESET}"
echo "${DIM}there is no port to attach to. A pipe through docker exec crosses the${RESET}"
echo "${DIM}boundary without opening one.${RESET}"
echo
HOLD=10 scripts/mcp-stdio.sh tools/list | scripts/mcp-render.py tools

rule "2. Calling one over that same pipe"
HOLD=45 scripts/mcp-stdio.sh --raw <<EOF | scripts/mcp-render.py call
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"nightglass_status","arguments":{}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"correlate","arguments":{"bbox":[${BBOX}],"start":"${START}","end":"${END}"}}}
EOF

rule "3. The same functions, driven by the 14B model inside the enclave"
echo "${DIM}Different consumer, same tools. No stubs: every result below came from${RESET}"
echo "${DIM}PostGIS and the SAR pixels.${RESET}"
echo
echo "${YELL}The ERROR and REPEAT lines below are expected, and are the point.${RESET}"
echo "${DIM}The model opens by inventing a scene id and ten detection ids. Both are${RESET}"
echo "${DIM}refused by name, the repeat detector catches the identical retry that${RESET}"
echo "${DIM}follows, and it then reaches the hand-checked numbers using real ids. A${RESET}"
echo "${DIM}tool surface that accepts a plausible invented id has a provenance chain${RESET}"
echo "${DIM}that means nothing.${RESET}"
docker compose exec -T -e NIGHTGLASS_AOI="$AOI" api nightglass-tools chain "$QUESTION"

rule "4. What the report may and may not say"
docker compose exec -T -e NIGHTGLASS_AOI="$AOI" api python - <<PY
from nightglass.mcp.server import draft_intrep
r = draft_intrep(bbox=[${BBOX}], start="${START}", end="${END}",
                 query="dark vessel AIS interpretation")
print(f"  marking   {r.marking}")
print(f"  claims    {len(r.claims)}, of which unsupported: {len(r.unsupported_claims)}")
print("\n  ${BOLD}every claim carries its references${RESET}")
for c in r.claims[:4]:
    refs = []
    if c.scene_ids: refs.append(f"scene×{len(c.scene_ids)}")
    if c.detection_ids: refs.append(f"det×{len(c.detection_ids)}")
    if c.chunk_ids: refs.append("chunk=" + ",".join(c.chunk_ids))
    print(f"    • {c.text[:150]}")
    print(f"        [{'; '.join(refs)}]")
print(f"    … {len(r.claims) - 4} more")
print("\n  ${BOLD}and the caveats are computed, not remembered${RESET}")
for c in r.caveats:
    print(f"    - {c[:190]}")
PY

cat <<EOF

${BOLD}The one number this milestone is careful about${RESET}
  The AIS over Denmark ${GREEN}is${RESET} ground truth, so ${BOLD}rate_is_quotable is true${RESET} -- the
  source side of the guard passes. The report still refuses to state a rate,
  because the ${BOLD}detector's precision is not validated${RESET}: 40% of detections
  are unmatched against a published base rate of ~5%, and the excess is coastal
  clutter and isolated false alarms, not dark vessels.

  Two independent conditions, checked separately, and only one of them was
  guarded before M4. See ${DIM}src/nightglass/tools/intrep.py${RESET}.

${BOLD}To attach Claude Desktop${RESET}
  ~/.config/Claude/claude_desktop_config.json:
${DIM}    { "mcpServers": { "nightglass": {
        "command": "/usr/bin/docker",
        "args": ["exec", "-i", "nightglass-mcp", "nightglass-mcp", "stdio"] } } }${RESET}
  Then restart Claude Desktop. Section 1 above already spoke the protocol over
  that exact command, so what Desktop sends next is the same bytes.
EOF
