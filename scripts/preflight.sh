#!/usr/bin/env bash
# Fail before `docker compose up` does, with a message that says what to fix.
#
# The VRAM check is the one that earns its keep on this machine: the host runs
# ollama as a systemd service with OLLAMA_KEEP_ALIVE=-1, pinning ~15 GB of the
# 3090 permanently. The enclave's own ollama then cannot load a 14B model, and
# the failure surfaces as an opaque CUDA error inside a container rather than
# as "the other ollama is holding the card".

#
# VRAM is a WARNING here, not a failure, and the distinction is real: the
# containers start perfectly well with the card occupied, because ollama only
# reserves VRAM when a model is actually loaded. It bites at inference. So
# `make up` warns and proceeds, while `make air-gap-proof` — which runs a chat
# completion — passes --require-vram and treats it as fatal.

set -uo pipefail

require_vram=0
[[ "${1:-}" == "--require-vram" ]] && require_vram=1

fail=0
warn=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=1; }
note() { printf '  \033[33m!\033[0m %s\n' "$1"; warn=1; }

cd "$(dirname "$0")/.." || exit 1

echo "NIGHTGLASS preflight"

# --- tooling ---------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    ok "docker $(docker --version | sed 's/Docker version //;s/,.*//')"
else
    bad "docker not found"
fi

if docker compose version >/dev/null 2>&1; then
    ok "compose $(docker compose version --short 2>/dev/null)"
else
    bad "docker compose v2+ not found"
fi

if ! docker info >/dev/null 2>&1; then
    bad "docker daemon not reachable (is it running? are you in the docker group?)"
fi

# --- GPU -------------------------------------------------------------------
if docker info 2>/dev/null | grep -q 'Runtimes:.*nvidia'; then
    ok "nvidia container runtime registered"
else
    note "nvidia runtime not registered — ollama will fall back to CPU and a 14B
      model will be unusably slow. Fix: nvidia-ctk runtime configure --runtime=docker"
fi

# Once the enclave's own ollama has the model resident, that VRAM is consumed by
# precisely the process that needs it, and free-VRAM arithmetic becomes the wrong
# question. Ask whether the model is loaded before asking whether there is room
# to load it -- otherwise this check blocks the very thing it exists to enable.
enclave_has_model=0
if docker compose ps --status running --services 2>/dev/null | grep -qx ollama; then
    if docker compose exec -T ollama ollama ps 2>/dev/null | tail -n +2 | grep -q '[^[:space:]]'; then
        enclave_has_model=1
        ok "enclave ollama already holds a model resident: $(
            docker compose exec -T ollama ollama ps 2>/dev/null | tail -n +2 \
            | awk '{print $1}' | paste -sd', ')"
    fi
fi

if (( enclave_has_model )); then
    :
elif command -v nvidia-smi >/dev/null 2>&1; then
    free_mib=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1)
    total_mib=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
    if [[ -n "${free_mib:-}" ]]; then
        # qwen2.5:14b at q4_K_M with a 32k context measures ~15 GB resident
        # (weights plus KV cache) and bge-m3 another ~0.7 GB. Below ~16 GB free
        # the enclave's ollama will not fit both.
        if (( free_mib < 16000 )); then
            if (( require_vram )); then
                bad "only ${free_mib} MiB of ${total_mib} MiB VRAM free — inference needs ~16000 MiB"
            else
                note "only ${free_mib} MiB of ${total_mib} MiB VRAM free — containers will
      start, but loading the 14B model needs ~16000 MiB"
            fi
            if systemctl is-active --quiet ollama 2>/dev/null; then
                printf '      The host ollama service is running and pinning the card\n'
                printf '      (OLLAMA_KEEP_ALIVE=-1 holds ~15 GB resident).\n'
                printf '      \033[1msudo systemctl stop ollama\033[0m   frees it.\n'
                printf '      It is a dev convenience; the enclave runs its own — see NOTES.md.\n'
            fi
        else
            ok "${free_mib} MiB of ${total_mib} MiB VRAM free"
        fi
    fi
else
    note "nvidia-smi not found — cannot check VRAM"
fi

# --- config ----------------------------------------------------------------
if [[ -f .env ]]; then
    ok ".env present"
    if grep -q '^POSTGRES_PASSWORD=change_me_locally$' .env; then
        note ".env still has the placeholder POSTGRES_PASSWORD"
    fi
    aoi=$(sed -nE 's/^NIGHTGLASS_AOI=(.*)$/\1/p' .env | tail -1)
    key=$(echo "${aoi:-}" | tr '[:lower:]' '[:upper:]')
    if [[ -n "$key" ]] && grep -q "^AOI_${key}_BBOX=." .env; then
        ok "AOI '${aoi}' resolves to $(sed -nE "s/^AOI_${key}_BBOX=(.*)$/\1/p" .env | tail -1)"
    else
        bad "NIGHTGLASS_AOI='${aoi:-}' has no matching AOI_${key}_BBOX in .env"
    fi
else
    bad ".env missing — cp .env.example .env and fill it in"
fi

# --- disk ------------------------------------------------------------------
avail_gb=$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')
if [[ -n "${avail_gb:-}" ]] && (( avail_gb < 20 )); then
    note "${avail_gb} GB free on / — model blobs alone are ~10 GB"
fi

echo
if (( fail )); then
    echo "preflight FAILED — fix the above, then re-run."
    exit 1
fi
(( warn )) && echo "preflight passed with warnings." || echo "preflight passed."
exit 0
