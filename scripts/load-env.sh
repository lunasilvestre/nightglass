#!/usr/bin/env bash
# NIGHTGLASS — credential + config loader.
#
#   source scripts/load-env.sh
#
# Source this; do not execute it. Executing it loads variables into a child
# shell that exits immediately, which does nothing useful.
#
# Load order (later wins):
#   1. ~/.config/eo-credentials.env   provider identity, shared across projects
#   2. ./.env                         this project's config and any override
#
# ---------------------------------------------------------------------------
# These credentials never reach the enclave.
#
# They exist for provisioning -- fetching Sentinel-1 granules from ASF and
# reference detections from GFW -- which happens on the `provision` network,
# in a profile-gated fetcher service that `make up` cannot start.
#
# So they ARE passed into docker compose, and only ever into those services:
# `granule-fetcher` takes EARTHDATA_USERNAME/EARTHDATA_PASSWORD, `gfw-fetcher`
# takes GFW_TOKEN, both declared with no value so compose forwards the name
# and this repo holds no secret. The enclave services take none of them: they
# run on an `internal: true` network, so a credential in there would be
# useless anyway.
#
# If you ever find yourself adding one of these to `api`, `mcp` or `agent`,
# stop: it means something inside the enclave is trying to reach the network.
# ---------------------------------------------------------------------------

# Resolve the project root from this script's own location, so the loader
# works regardless of the caller's working directory.
_ng_this="${BASH_SOURCE[0]:-$0}"
NIGHTGLASS_ROOT="$(cd "$(dirname "$_ng_this")/.." && pwd)"
export NIGHTGLASS_ROOT

_ng_central="${EO_CREDENTIALS_FILE:-$HOME/.config/eo-credentials.env}"
_ng_project="$NIGHTGLASS_ROOT/.env"

# Remember the caller's allexport setting so we restore it exactly.
_ng_had_allexport=0
case "$-" in *a*) _ng_had_allexport=1 ;; esac

_ng_load() {
    [ -f "$1" ] || return 1
    # Warn on loose permissions rather than silently accepting them.
    if [ "$(stat -c '%a' "$1" 2>/dev/null)" != "600" ] && [ "$1" = "$_ng_central" ]; then
        printf '  ! %s is not chmod 600\n' "$1" >&2
    fi
    set -a
    # shellcheck disable=SC1090
    . "$1"
    set +a
    return 0
}

_ng_load "$_ng_central" && _ng_central_ok=1 || _ng_central_ok=0

# Snapshot the non-empty values the central file just supplied.
#
# The project .env legitimately ships blank credential keys as documentation
# (GFW_TOKEN= with nothing after it). Because the project file loads second
# and would otherwise win, a blank line there would CLOBBER a perfectly good
# central credential -- and the failure would look like "token not set"
# rather than "token was overwritten by an empty string". So: a blank in the
# project file means "no opinion, defer to central", while a non-empty value
# still overrides as intended.
_ng_central_keys=""
if [ "$_ng_central_ok" = "1" ]; then
    _ng_central_keys=$(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' \
                       "$_ng_central" | sort -u)
    for _ng_k in $_ng_central_keys; do
        eval "_ng_v=\${$_ng_k:-}"
        [ -n "$_ng_v" ] && eval "_NG_SAVED_$_ng_k=\$_ng_v"
    done
fi

_ng_load "$_ng_project" && _ng_project_ok=1 || _ng_project_ok=0

# Restore anything the project file blanked out.
for _ng_k in $_ng_central_keys; do
    eval "_ng_now=\${$_ng_k:-}"
    eval "_ng_saved=\${_NG_SAVED_$_ng_k:-}"
    if [ -z "$_ng_now" ] && [ -n "$_ng_saved" ]; then
        eval "export $_ng_k=\$_ng_saved"
    fi
    unset "_NG_SAVED_$_ng_k"
done
unset _ng_k _ng_v _ng_now _ng_saved _ng_central_keys

# ---------------------------------------------------------------------------
# Earthdata, from ~/.netrc.
#
# ASF's own documentation tells you to put the credential in ~/.netrc, and the
# machine that built this already has it there. But a netrc is a file, and
# mounting one into a container means either a conditional bind mount (compose
# has no such thing, and a missing source silently becomes an empty directory)
# or a path that has to exist. Reading it here and forwarding two variables is
# the same shape as GFW_TOKEN, and it keeps the container's contract to
# "environment in, no host paths".
#
# Values are never printed. `nightglass_env_status` reports set-or-not.
# ---------------------------------------------------------------------------
_ng_netrc="${NETRC:-$HOME/.netrc}"
if [ -z "${EARTHDATA_USERNAME:-}" ] || [ -z "${EARTHDATA_PASSWORD:-}" ]; then
    if [ -r "$_ng_netrc" ]; then
        # Tokenised rather than line-matched: netrc entries legitimately span
        # lines, and `grep -A1 machine` gets that wrong on the real file.
        _ng_pair=$(awk -v host="urs.earthdata.nasa.gov" '
            { for (i = 1; i <= NF; i++) tok[++n] = $i }
            END {
                for (i = 1; i <= n; i++) {
                    if (tok[i] == "machine")      { m = tok[++i]; continue }
                    if (tok[i] == "default")      { m = "default"; continue }
                    if (m != host)                { if (tok[i] ~ /^(login|password|account)$/) i++; continue }
                    if (tok[i] == "login")        { u = tok[++i] }
                    else if (tok[i] == "password"){ p = tok[++i] }
                }
                printf "%s\n%s\n", u, p
            }' "$_ng_netrc" 2>/dev/null)
        _ng_user=$(printf '%s' "$_ng_pair" | sed -n 1p)
        _ng_pass=$(printf '%s' "$_ng_pair" | sed -n 2p)
        if [ -n "$_ng_user" ] && [ -n "$_ng_pass" ]; then
            export EARTHDATA_USERNAME="$_ng_user"
            export EARTHDATA_PASSWORD="$_ng_pass"
        fi
        unset _ng_pair _ng_user _ng_pass
    fi
fi
unset _ng_netrc

# Restore the caller's allexport state.
[ "$_ng_had_allexport" = "1" ] && set -a || set +a

# Report what loaded. Never print a value -- only whether it is set.
nightglass_env_status() {
    printf 'NIGHTGLASS environment\n'
    printf '  root      %s\n' "$NIGHTGLASS_ROOT"
    if [ "$_ng_central_ok" = "1" ]; then
        printf '  central   %s\n' "$_ng_central"
    else
        printf '  central   (absent) %s\n' "$_ng_central"
    fi
    if [ "$_ng_project_ok" = "1" ]; then
        printf '  project   %s\n' "$_ng_project"
    else
        printf '  project   (absent -- cp .env.example .env)\n'
    fi
    printf '\n  credentials\n'
    if [ -n "${GFW_TOKEN:-}" ]; then
        printf '    GFW_TOKEN        set (%d chars)\n' "${#GFW_TOKEN}"
    else
        printf '    GFW_TOKEN        MISSING -> https://globalfishingwatch.org/our-apis/tokens\n'
    fi
    if [ -n "${EARTHDATA_USERNAME:-}" ] && [ -n "${EARTHDATA_PASSWORD:-}" ]; then
        printf '    Earthdata        set as %s\n' "$EARTHDATA_USERNAME"
        printf '                     NB: valid login is not enough -- the ASF EULA\n'
        printf '                     must be accepted at urs.earthdata.nasa.gov/profile,\n'
        printf '                     and without it the failure is a redirect loop\n'
    else
        printf '    Earthdata        MISSING -> no urs.earthdata.nasa.gov in ~/.netrc,\n'
        printf '                     and EARTHDATA_USERNAME/PASSWORD are unset\n'
    fi
    printf '\n  active AOI  %s\n' "${NIGHTGLASS_AOI:-<unset>}"
}

unset _ng_this _ng_had_allexport
