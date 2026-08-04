#!/usr/bin/env bash
# The RAG proof, as a repeatable command.
#
#   "a question returns an answer whose factual claims map to retrievable chunk
#    IDs, and an unanswerable question produces an explicit 'not supported by
#    available sources' rather than a guess."
#
# Three parts, in this order, because the order is the argument:
#
#   1. UNGROUNDED  — the same question with no retrieval. The model has no prior
#                    for the central term of this project.
#   2. GROUNDED    — retrieval on, every claim carrying the chunk ids it came
#                    from, and the classification marking propagated up from the
#                    sources that were actually cited.
#   3. REFUSAL     — a question the corpus genuinely cannot answer. The gap is
#                    real and documented in corpus/README.md, not engineered per
#                    question.
#
# Raw output, not summarised: the capture goes in the README.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

Q_TERM="what is a dark vessel?"
Q_GAP="How many dark vessels were detected off Madeira in 2019?"

rule() { printf '\n\033[2m%s\033[0m\n' "----------------------------------------------------------------------"; }
run()  { echo "\$ $*"; docker compose exec -T api "$@"; }

echo "NIGHTGLASS — document RAG proof   $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

indexed=$(docker compose exec -T api nightglass-corpus stats 2>/dev/null | awk '/chunks/{print $2; exit}')
if [[ -z "${indexed:-}" || "${indexed}" == "0" ]]; then
    echo
    echo "Nothing is indexed. Run:  make fetch-corpus && make ingest"
    exit 1
fi
echo "index: ${indexed} chunks"

rule
echo "PART 1 — the same question, UNGROUNDED. No retrieval, model priors only."
rule
run nightglass-corpus ask "$Q_TERM" --ungrounded

rule
echo "PART 2 — GROUNDED. Same question, same model, retrieval on."
rule
run nightglass-corpus ask "$Q_TERM" --sources

rule
echo "PART 3 — REFUSAL. A question the corpus does not cover."
rule
run nightglass-corpus ask "$Q_GAP"

rule
cat <<'EOF'
Part 1 is why this milestone exists. The model has no prior for the operational
meaning of "dark vessel": across samples it either declares the term to have no
established maritime meaning or offers hull colour as a guess. Neither is the
answer, and neither is hedged in a way an analyst would notice.

Part 2 is the same model, same question, answered from retrieved sources — with
the chunk ids to check it against, and marked UNCLASSIFIED // SYNTHETIC because
the sources it actually cited carry that caveat. Nothing propagated that marking
by hand.

Part 3 is the part worth more than either: the system declining to answer. A
dark detection is a lead, not a conclusion, and a system that admits what it
cannot source is the only kind whose output can be graded at all.
EOF
