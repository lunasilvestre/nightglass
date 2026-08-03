#!/usr/bin/env bash
# EXECUTION_SPEC §M1's proof, as a repeatable command.
#
#   "docker compose exec api curl -m 5 https://example.com fails while a chat
#    completion against Ollama succeeds, in the same session."
#
# Both halves in one run, in that order, with the raw output shown rather than
# summarised — the terminal capture goes in the README and the recording, so it
# has to be the real thing.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# The chat half needs the card free — unlike `make up`, which does not.
scripts/preflight.sh --require-vram >/dev/null 2>&1 || {
    scripts/preflight.sh --require-vram
    echo
    echo "Not running the proof: the chat completion would fail for an unrelated reason."
    exit 1
}

rule() { printf '\n\033[2m%s\033[0m\n' "----------------------------------------------------------------------"; }

echo "NIGHTGLASS — air-gap proof   $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
rule
echo '$ docker compose exec api curl -m 5 https://example.com'
if docker compose exec -T api curl -m 5 https://example.com; then
    printf '\n\033[31m✗ EGRESS SUCCEEDED — the enclave is not sealed.\033[0m\n'
    egress_blocked=0
else
    printf '\n\033[32m✓ blocked (curl exit %s)\033[0m\n' "$?"
    egress_blocked=1
fi

rule
echo '$ docker compose exec api curl -m 5 http://ollama:11434/api/tags   # inside the enclave'
docker compose exec -T api curl -sS -m 5 http://ollama:11434/api/tags \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("models:", ", ".join(m["name"] for m in d["models"]) or "(none — run: make pull-models)")' \
  || echo "could not reach ollama"

rule
echo '$ chat completion against the local model'
# The prompt is deliberately NOT "o que é uma embarcação escura?". Asked that
# ungrounded, the model answers that it is a boat painted in dark colours --
# fluent, confident, and domain-wrong. That answer is worth a great deal, but as
# the M2 before/after contrast, not sitting underneath the words "inference
# works" in the M1 capture. See NOTES.md.
docker compose exec -T api curl -sS -m 180 http://ollama:11434/api/chat \
  -d "$(python3 -c '
import json, os
print(json.dumps({
  "model": os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:14b-instruct-q4_K_M"),
  "messages": [{"role": "user",
                "content": "Responde numa frase, em portugues: o que e radar de "
                           "abertura sintetica e porque funciona de noite?"}],
  "stream": False,
}))' )" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["message"]["content"].strip())' \
  || echo "chat completion failed — have you run: make pull-models ?"

rule
if (( egress_blocked )); then
    echo "No route to the internet. Inference ran anyway."
else
    echo "FAILED: the api container reached the internet."
    exit 1
fi
