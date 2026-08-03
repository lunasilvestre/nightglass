#!/usr/bin/env bash
# LOCAL SHORTCUT — copy model blobs from the host ollama into the enclave volume.
#
# This is NOT the documented provisioning path. `make pull-models` is, and it is
# what a cloner runs. This exists only because this machine already holds ~10 GB
# of blobs from pre-dev and re-downloading them is pure waste.
#
# Ollama stores models as content-addressed blobs plus a manifest tree, so a
# plain recursive copy of OLLAMA_MODELS is sufficient and order-independent.
# That property is also why the M7 bundler is a tar of one directory rather than
# an untangling of a HuggingFace cache — see the README design table.

set -euo pipefail

HOST_MODELS="${OLLAMA_MODELS:-/usr/share/ollama/.ollama/models}"
VOLUME="nightglass_ollama_models"

# /usr/share/ollama is mode 700 owned by the `ollama` user, so an unprivileged
# `[ -d ... ]` on anything beneath it returns false whether or not the path
# exists. Every existence check here therefore has to run under sudo, or it
# reports "missing" on a store that is sitting right there.
echo ">> The host model store is owned by the 'ollama' user. Reading it needs sudo."
sudo -v

if ! sudo test -d "$HOST_MODELS"; then
    echo >&2
    echo "No host model store at $HOST_MODELS." >&2
    echo "If ollama keeps models elsewhere, set OLLAMA_MODELS and re-run." >&2
    echo "Otherwise use the documented path:  make pull-models" >&2
    exit 1
fi

size=$(sudo du -sh "$HOST_MODELS" 2>/dev/null | cut -f1)
echo ">> host store   $HOST_MODELS  ($size)"
echo ">> destination  volume $VOLUME  ->  /root/.ollama/models in the enclave"
sudo find "$HOST_MODELS/manifests" -type f 2>/dev/null \
  | sed "s|$HOST_MODELS/manifests/|   model: |" || true
echo

docker volume create "$VOLUME" >/dev/null

# Copy host -> volume through a throwaway container: reading the store needs
# root on the host, writing into the volume happens as root inside the container.
sudo tar -C "$(dirname "$HOST_MODELS")" -cf - "$(basename "$HOST_MODELS")" \
  | docker run --rm -i -v "$VOLUME":/dest alpine:latest \
      tar -C /dest -xf -

echo ">> copied. verifying the volume contents"
docker run --rm -v "$VOLUME":/dest alpine:latest sh -c '
  printf "   blobs:     %s files, %s\n" \
    "$(find /dest/models/blobs -type f 2>/dev/null | wc -l)" \
    "$(du -sh /dest/models 2>/dev/null | cut -f1)"
  echo "   manifests:"
  find /dest/models/manifests -type f 2>/dev/null | sed "s|/dest/models/manifests/|     |"
'

# ollama rereads the manifest tree per request, but restart to be unambiguous.
if docker compose ps --status running --services 2>/dev/null | grep -qx ollama; then
    echo ">> restarting the enclave ollama so it picks them up"
    docker compose restart ollama >/dev/null
    docker compose exec -T ollama ollama list
fi
