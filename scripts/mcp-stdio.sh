#!/usr/bin/env bash
# One JSON-RPC session against the MCP server, over the transport Claude Desktop
# attaches with.
#
#     scripts/mcp-stdio.sh tools/list
#     scripts/mcp-stdio.sh nightglass_status
#     scripts/mcp-stdio.sh correlate '{"bbox":[10.5,55.5,12.5,57.5],"start":"...","end":"..."}'
#     scripts/mcp-stdio.sh --raw <<< '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
#
# The transport is `docker exec -i nightglass-mcp nightglass-mcp stdio`. Not
# `docker compose exec`: this string is copied verbatim into Claude Desktop's
# config, which launches it with no working directory and a minimal PATH, so it
# must not depend on either. The container name is fixed in docker-compose.yml.
#
# Why a pipe and not a port: `mcp` sits on a network declared `internal: true`,
# and a container there silently gets no host port mapping even if one is
# written. There is nothing to connect to, by design. `docker exec` crosses the
# boundary without opening it.
#
# The one non-obvious detail is the hold at the end. The server exits on EOF, so
# closing stdin right after the last request means the replies never arrive --
# which looks precisely like a server that does not work.

set -euo pipefail

HOLD=${HOLD:-12}
AOI=${AOI:-}
RAW=0
[[ ${1:-} == "--raw" ]] && { RAW=1; shift; }

# `${2:-\{\}}` looks like it defaults to an empty JSON object and does not: inside
# double quotes bash leaves the backslashes alone, so the server receives
# "params":\{\} and answers "Internal Server Error" -- which reads exactly like a
# broken server rather than a broken probe. Default through a variable instead.
EMPTY='{}'

requests() {
  if (( RAW )); then
    cat
  elif [[ ${1:-} == */* ]]; then
    printf '{"jsonrpc":"2.0","id":2,"method":"%s","params":%s}\n' "$1" "${2:-$EMPTY}"
  else
    printf '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"%s","arguments":%s}}\n' \
      "$1" "${2:-$EMPTY}"
  fi
}

{
  printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"nightglass-probe","version":"1"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  requests "$@"
  sleep "$HOLD"
} | timeout $((HOLD + 60)) docker exec -i ${AOI:+-e NIGHTGLASS_AOI=$AOI} \
      nightglass-mcp nightglass-mcp stdio 2>/dev/null
