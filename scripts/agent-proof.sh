#!/usr/bin/env bash
# EXECUTION_SPEC §M5's proof, as a repeatable command.
#
#   "Done when: an analyst question runs through to a drafted INTREP,
#    halts at the gate, resumes on approval."
#
# The halt is the milestone, so it is demonstrated the only way that means
# anything: the drafting process EXITS at the gate, the state is left in
# Postgres, and a SEPARATE process picks it up and finishes. If the gate were a
# bare `input()` those could not be two processes, and "genuinely halts with
# inspectable persisted state" would describe a blocked read.
#
# Denmark, because the milestone is about the graph rather than about the AOI,
# and Denmark is the only AOI with AIS on disk — so there is a correlation to
# put in front of a reviewer. `AGENT_AOI=lisbon scripts/agent-proof.sh` runs the
# same graph over the demo AOI, where the tools refuse the correlation and the
# report says so; that is a good thing to look at too.

# `head` closes the pipe early, which under `pipefail` turns a perfectly good
# truncated display into a failed script. `sed -n '1,Np'` reads its input to the
# end and truncates the output instead. Found the hard way on step 4.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'; YELL=$'\033[33m'; GREEN=$'\033[32m'

AOI=${AGENT_AOI:-kattegat}
Q=${Q:-"Were there any vessels with no AIS correspondence in the area of interest on 17 July 2026? What does that mean?"}

# Every call is a FRESH CONTAINER, not an exec into a running one. That is the
# demonstration: the container that drafts the report is gone by the time the
# one that releases it starts, and the only thing connecting them is Postgres.
agent() { docker compose --profile cli run --rm -T -e NIGHTGLASS_AOI="$AOI" agent "$@" 2>/dev/null; }
psql_() { docker compose exec -T postgis psql -U nightglass -d nightglass "$@"; }

rule() { printf '\n%s%s%s\n%s\n' "$BOLD" "$1" "$RESET" "$(printf '%.0s─' $(seq 1 ${#1}))"; }

if ! docker compose ps --status running --services 2>/dev/null | grep -qx api; then
  echo "The enclave is not running. Try: make up" >&2
  exit 1
fi

rule "0. The enclave still has no way out"
echo "${DIM}\$ docker compose exec api curl -m 5 https://example.com${RESET}"
docker compose exec -T api curl -m 5 https://example.com 2>&1 | head -2 || true

rule "1. An analyst question, run to the gate — and this process then exits"
THREAD=$(agent ask "$Q" 2>&1 | tee /dev/stderr | grep -oE 'ng-[0-9a-f]{12}' | head -1)
if [[ -z "${THREAD:-}" ]]; then
  echo "${YELL}The agent did not reach the gate.${RESET}" >&2
  exit 1
fi

rule "2. The drafting CONTAINER is gone. The run is not."
echo "${DIM}Everything from here on is a different container, reading Postgres.${RESET}"
echo
echo "${DIM}\$ psql -c \"select thread_id, count(*) from checkpoint_blobs group by 1\"${RESET}"
psql_ -c "SELECT thread_id, count(*) AS channels, pg_size_pretty(sum(length(blob))::bigint) AS state
          FROM checkpoint_blobs WHERE thread_id = '$THREAD' AND blob IS NOT NULL
          GROUP BY thread_id;"
echo "${DIM}The values are msgpack, not JSON — readable through the checkpointer,${RESET}"
echo "${DIM}not through convert_from(). What plain SQL gives you is that the run${RESET}"
echo "${DIM}exists, where it stopped, and how much state it is holding.${RESET}"

rule "3. The review queue, from a fresh process"
agent pending

rule "4. What the reviewer actually sees"
agent show "$THREAD" 2>&1 | sed -n '1,32p'
echo "${DIM}    … full draft above; caveats follow it${RESET}"

rule "5. Rejecting is a real outcome, not an error path"
agent reject "$THREAD" --note "spot-check the two coastal detections first" 2>&1 | tail -4

rule "6. The gate cannot be walked through twice"
echo "${DIM}\$ nightglass-agent approve $THREAD${RESET}"
# Not through agent(), which sends stderr to /dev/null: the refusal IS the
# output here, and it goes to stderr like every other error in the CLI.
docker compose --profile cli run --rm -T -e NIGHTGLASS_AOI="$AOI" agent \
  approve "$THREAD" 2>&1 | grep -v '^ Container' | tail -2 || true

rule "7. A second run, approved — resumed from persisted state"
THREAD2=$(agent ask "$Q" 2>&1 | grep -oE 'ng-[0-9a-f]{12}' | head -1)
echo "${DIM}drafted by one process as ${THREAD2}; released by the next${RESET}"
agent approve "$THREAD2" --note "positions checked against the chips" 2>&1 | sed -n '1,24p'

cat <<EOF

${BOLD}What this shows${RESET}
  The graph runs ${DIM}parse → plan → tools → correlate → draft_intrep →${RESET}
  ${DIM}HUMAN_GATE → release${RESET} and stops dead in the middle. Approval happens in a
  different process, minutes or days later, from state in Postgres.

  ${BOLD}The findings are not the model's arithmetic.${RESET} ${DIM}correlate${RESET} runs
  deterministically over the configured AOI whatever the model did, and every
  claim is templated from the CorrelationResult. That is the fix for a measured
  failure: at M4 the model got every per-scene count right and still conflated
  two scenes while summarising them.

  ${BOLD}Only this path can mark a report releasable.${RESET} ${DIM}draft_intrep${RESET} always
  returns releasable=false; the ${DIM}release${RESET} node is the one place that flips it,
  and it is reachable only through the interrupt.
EOF
