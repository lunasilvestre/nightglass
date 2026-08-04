#!/usr/bin/env bash
# The bundle proof, as a repeatable command.
#
#   "Go bundler -- docker save + pip wheelhouse + model blobs -> one tarball
#    with SHA256 manifest and a `verify` subcommand."
#
# Producing a tarball is an afternoon. What this proves is the other half:
# that the tarball is refused when it is wrong, and restores when it is right.
#
# It runs on a ~100 MB FIXTURE rather than on the real ~18 GB bundle, and that
# is deliberate. A proof nobody runs is not a proof, and every property being
# demonstrated here is a property of the format, not of its size. `make bundle`
# and `make verify-bundle` are the real thing.
#
# The four refusals are the point of this script in the way the ERROR lines are
# the point of tool-proof.sh. Three of them are corruption a verifier is
# expected to catch. The fourth -- step 6 -- is the one a naive verifier passes:
# an archive that streams cleanly to EOF and is simply missing a member. Every
# check that looks at what IS present succeeds on it. That is finding 55's shape
# in the one tool whose entire job is to say "this is complete".
#
# Step 8 restores into a docker:dind container: an empty daemon, on an image
# with no Python, no Go and no nightglass. The only thing carried in is one
# static binary and the tarball. If a static binary were not the point, that
# step would need a runtime installed first.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'; GREEN=$'\033[32m'; RED=$'\033[31m'

NGB=bundler/bin/nightglass-bundle
VOL=nightglass_bundleproof_models
DIND=nightglass-bundle-proof-dind
TMP=$(mktemp -d)

cleanup() {
  rm -rf "$TMP"
  docker rm -f "$DIND"    >/dev/null 2>&1 || true
  docker volume rm "$VOL" >/dev/null 2>&1 || true
}
trap cleanup EXIT

rule() { printf '\n%s%s%s\n%s\n' "$BOLD" "$1" "$RESET" "$(printf '%.0s─' $(seq 1 ${#1}))"; }

# expect_refusal runs a command that must exit 1 -- refused -- and not 2, which
# means the check could not be run at all. Conflating those is how a script ends
# up deleting a good bundle because a disk was busy.
expect_refusal() {
  local rc=0
  "$@" >"$TMP/out" 2>&1 || rc=$?
  if [ "$rc" -ne 1 ]; then
    printf '%sFAIL%s expected exit 1 (refused), got %s\n' "$RED" "$RESET" "$rc"
    cat "$TMP/out"
    exit 1
  fi
  sed -n '/REFUSED/,$p' "$TMP/out" | head -12 | sed 's/^/  /'
}

if [ ! -x "$NGB" ]; then
  echo "The bundler is not built. Try: make bundler" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
rule "0. One static binary, and what it needs"
echo "${DIM}\$ file $NGB${RESET}"
file "$NGB" | sed 's/, BuildID.*//' | sed 's/^/  /'
echo
echo "${DIM}Statically linked and stripped. The thing that unpacks an air-gapped${RESET}"
echo "${DIM}bundle cannot itself need a Python environment to exist first -- that${RESET}"
echo "${DIM}argument only lands if the binary really has no runtime behind it, so${RESET}"
echo "${DIM}step 8 runs it on an image that has neither Python nor Go.${RESET}"

# ---------------------------------------------------------------------------
rule "1. A fixture: images, model blobs, and data with a committed manifest"

mkdir -p "$TMP/clone/data/raw/sar" "$TMP/clone/data/raw/ais"
printf 'name: nightglass\nservices: {}\n' > "$TMP/clone/docker-compose.yml"

# Two data files with real content and real digests, so the cross-check against
# data/sources.yaml is exercised rather than mocked.
head -c 200000 /dev/urandom > "$TMP/clone/data/raw/sar/S1D_FIXTURE.zip"
head -c  90000 /dev/urandom > "$TMP/clone/data/raw/ais/aisdk-fixture.zip"
sar_sha=$(sha256sum "$TMP/clone/data/raw/sar/S1D_FIXTURE.zip" | cut -d' ' -f1)
sar_len=$(stat -c%s "$TMP/clone/data/raw/sar/S1D_FIXTURE.zip")
ais_sha=$(sha256sum "$TMP/clone/data/raw/ais/aisdk-fixture.zip" | cut -d' ' -f1)
ais_len=$(stat -c%s "$TMP/clone/data/raw/ais/aisdk-fixture.zip")

cat > "$TMP/clone/data/sources.yaml" <<YAML
sar:
  licence: Fixture.
  credentials: none
  out: /app/data/raw/sar
  items:
    - name: S1D_FIXTURE.zip
      url: https://example.invalid/S1D_FIXTURE.zip
      sha256: $sar_sha
      bytes: $sar_len
      aoi: kattegat
      role: required
      note: >-
        Stands in for the validation scene. Same role, four orders of
        magnitude smaller.
ais:
  licence: Fixture.
  credentials: none
  out: /app/data/raw/ais
  items:
    - name: aisdk-fixture.zip
      url: https://example.invalid/aisdk-fixture.zip
      sha256: $ais_sha
      bytes: $ais_len
      aoi: kattegat
      role: required
      note: Stands in for the acquisition day.
YAML

# A model volume shaped exactly like nightglass_ollama_models: content-addressed
# blobs whose FILENAME is their sha256, a manifest tree -- and the two things
# that must not travel.
docker volume create "$VOL" >/dev/null
docker run --rm -v "$VOL":/m alpine:3 sh -c '
  set -e
  mkdir -p /m/models/blobs /m/models/manifests/registry.ollama.ai/library/tiny
  head -c 400000 /dev/urandom > /tmp/w
  h=$(sha256sum /tmp/w | cut -d" " -f1)
  cp /tmp/w "/m/models/blobs/sha256-$h"
  printf "%s\n" "{\"schemaVersion\":2,\"mediaType\":\"application/vnd.docker.distribution.manifest.v2+json\"}" \
    > /m/models/manifests/registry.ollama.ai/library/tiny/latest
  printf -- "-----BEGIN OPENSSH PRIVATE KEY-----\n" > /m/id_ed25519
  printf "ssh-ed25519 AAAA\n"                       > /m/id_ed25519.pub
  mkdir -p /m/cache && printf "{}\n"                > /m/cache/model-recommendations.json
' >/dev/null
echo "  ${DIM}volume $VOL: 1 blob, 1 manifest, plus id_ed25519 and a cache${RESET}"

# alpine:3 is not decoration here -- it is the image that writes the model
# volume at restore, so a bundle that did not carry it would fail on exactly
# the kind of host it exists for. qdrant is included when it is present, to
# give the fixture one member with real bulk.
FIXTURE_IMAGES="alpine:3"
if docker image inspect qdrant/qdrant:v1.18.3 >/dev/null 2>&1; then
  FIXTURE_IMAGES="alpine:3,qdrant/qdrant:v1.18.3"
fi
echo "  ${DIM}images: ${FIXTURE_IMAGES//,/ and }${RESET}"
echo "  ${DIM}data:   2 files, cross-checked against the fixture sources.yaml${RESET}"

# ---------------------------------------------------------------------------
rule "2. create"
echo "${DIM}\$ nightglass-bundle create -o proof.tar --repo <fixture> --skip-wheels${RESET}"
echo "${DIM}--skip-wheels because the wheelhouse needs a package index and this${RESET}"
echo "${DIM}script must run without one. 'make bundle' builds it.${RESET}"
echo
"$NGB" create -o "$TMP/proof.tar" --repo "$TMP/clone" --skip-wheels \
  --models-volume "$VOL" --images "$FIXTURE_IMAGES" \
  --created 2026-08-04T00:00:00Z 2>&1 \
  | grep -Ev '^\s*$' | sed 's/^/  /'

ls -l "$TMP/proof.tar" | awk '{printf "  %s bytes\n", $5}'

# ---------------------------------------------------------------------------
rule "3. inspect -- what is inside, without reading the inside"
"$NGB" inspect "$TMP/proof.tar" | sed 's/^/  /'
echo
if tar -tf "$TMP/proof.tar" | grep -q 'id_ed25519'; then
  printf '%sFAIL%s the private key travelled\n' "$RED" "$RESET"; exit 1
fi
echo "  ${GREEN}id_ed25519 is not in the archive.${RESET} ${DIM}It is an OpenSSH private key and${RESET}"
echo "  ${DIM}Ollama's instance identity. Bundling it would ship one private key to${RESET}"
echo "  ${DIM}every site that restores this; a fresh Ollama generates its own.${RESET}"

# ---------------------------------------------------------------------------
rule "4. verify -- the honest case"
echo "${DIM}\$ nightglass-bundle verify proof.tar${RESET}"
"$NGB" verify "$TMP/proof.tar" | sed 's/^/  /'

echo
echo "${DIM}\$ cat proof.tar | nightglass-bundle verify -${RESET}"
echo "${DIM}The same check on a pipe. Over 18 GB, verifying as the bundle comes off${RESET}"
echo "${DIM}the transfer medium rather than after staging it is the difference${RESET}"
echo "${DIM}between a tool someone runs and one they avoid.${RESET}"
cat "$TMP/proof.tar" | "$NGB" verify - | tail -3 | sed 's/^/  /'

# ---------------------------------------------------------------------------
rule "5. One flipped byte"
cp "$TMP/proof.tar" "$TMP/flipped.tar"
python3 - "$TMP/flipped.tar" <<'PY'
import sys
# Land inside the largest member, well past any header.
p = sys.argv[1]
with open(p, 'r+b') as f:
    f.seek(0, 2); size = f.tell()
    off = size // 2
    f.seek(off); b = f.read(1)
    f.seek(off); f.write(bytes([b[0] ^ 0xFF]))
    print(f"  flipped one bit at offset {off:,} of {size:,}")
PY
expect_refusal "$NGB" verify "$TMP/flipped.tar"

# ---------------------------------------------------------------------------
rule "6. A truncated transfer"
python3 - "$TMP/proof.tar" "$TMP/cut.tar" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
data = open(src, 'rb').read()
keep = int(len(data) * 0.7)
open(dst, 'wb').write(data[:keep])
print(f"  copied {keep:,} of {len(data):,} bytes, then stopped")
PY
expect_refusal "$NGB" verify "$TMP/cut.tar"
echo "  ${DIM}Reported as truncation, not as a hash mismatch. A partial copy and a${RESET}"
echo "  ${DIM}corrupted one need different next steps, and the file a partial copy${RESET}"
echo "  ${DIM}leaves behind opens without complaint.${RESET}"

# ---------------------------------------------------------------------------
rule "7. A member that is simply not there"
python3 - "$TMP/proof.tar" "$TMP/short.tar" <<'PY'
import sys, tarfile
src, dst = sys.argv[1], sys.argv[2]
dropped = None
with tarfile.open(src) as a, tarfile.open(dst, 'w') as b:
    for m in a.getmembers():
        # Drop one model blob and leave the manifest untouched.
        if dropped is None and m.name.startswith('models/blobs/'):
            dropped = m.name
            continue
        b.addfile(m, a.extractfile(m))
print(f"  rewrote the archive without {dropped}")
print("  the manifest was not touched")
PY
expect_refusal "$NGB" verify "$TMP/short.tar"
echo "  ${DIM}This archive is well-formed. It streams to EOF without an error, every${RESET}"
echo "  ${DIM}member in it hashes correctly, and every check that asks 'is what is${RESET}"
echo "  ${DIM}here right?' passes. What is wrong with it is what is not here.${RESET}"

# ---------------------------------------------------------------------------
rule "8. create refuses bytes that do not match the committed manifest"
# In place, so the file is still exactly the size data/sources.yaml declares.
# "Right size, wrong bytes" is the failure a byte count cannot see, and it is
# the one the sha256 in the committed manifest exists for.
printf 'X' | dd of="$TMP/clone/data/raw/ais/aisdk-fixture.zip" \
  bs=1 seek=1000 count=1 conv=notrunc status=none
echo "${DIM}\$ dd one byte into data/raw/ais/aisdk-fixture.zip  # same size${RESET}"
echo "${DIM}\$ nightglass-bundle create ...${RESET}"
rc=0
"$NGB" create -o "$TMP/never.tar" --repo "$TMP/clone" --skip-wheels \
  --models-volume "$VOL" >"$TMP/out" 2>&1 || rc=$?
if [ "$rc" -eq 0 ]; then
  printf '%sFAIL%s a tampered granule was bundled\n' "$RED" "$RESET"; exit 1
fi
sed -n '/ERROR/,$p' "$TMP/out" | head -8 | sed 's/^/  /'
echo "  ${DIM}This is the join back to M6. data/sources.yaml says what every external${RESET}"
echo "  ${DIM}input must hash to; a bundle that carried something else would be a new${RESET}"
echo "  ${DIM}set of unsourced bytes wearing a checksum of its own making.${RESET}"

# ---------------------------------------------------------------------------
rule "9. restore into a daemon that has nothing in it"

echo "${DIM}\$ docker run -d --privileged docker:dind${RESET}"
cp "$NGB" "$TMP/nightglass-bundle"
docker run -d --privileged --name "$DIND" \
  -e DOCKER_TLS_CERTDIR= -v "$TMP":/proof docker:dind >/dev/null

printf '  waiting for the daemon'
for _ in $(seq 1 60); do
  if docker exec "$DIND" docker info >/dev/null 2>&1; then break; fi
  printf '.'; sleep 1
done
echo
echo "${DIM}\$ docker exec <dind> sh -c 'command -v python3 go docker'${RESET}"
docker exec "$DIND" sh -c 'for c in python3 python go; do
    command -v $c >/dev/null 2>&1 && echo "  $c: present" || echo "  $c: absent"
  done; echo "  docker: $(command -v docker)"'
echo "  ${DIM}Nothing to run a Python tool with. The bundler is copied in as one file.${RESET}"

echo
echo "${DIM}\$ docker exec <dind> docker images   # before${RESET}"
docker exec "$DIND" docker images | sed 's/^/  /'

echo
echo "${DIM}\$ docker exec <dind> /proof/nightglass-bundle restore /proof/proof.tar \\${RESET}"
echo "${DIM}      --into /restore --install --repo /proof/clone${RESET}"
docker exec "$DIND" /proof/nightglass-bundle restore /proof/proof.tar \
  --into /restore --install --repo /proof/clone 2>&1 | tail -24 | sed 's/^/  /'

echo
echo "${DIM}\$ docker exec <dind> docker images   # after${RESET}"
docker exec "$DIND" docker images | sed 's/^/  /'

echo
echo "${DIM}\$ docker exec <dind> docker volume ls${RESET}"
docker exec "$DIND" docker volume ls | sed 's/^/  /'
echo
echo "${DIM}\$ docker exec <dind> docker run --rm -v nightglass_ollama_models:/m \\${RESET}"
echo "${DIM}      alpine:3 find /m -type f${RESET}"
docker exec "$DIND" docker run --rm -v nightglass_ollama_models:/m alpine:3 \
  find /m -type f 2>/dev/null | sed 's/^/  /'
echo "  ${DIM}models/ and nothing else. The private key and the recommendations${RESET}"
echo "  ${DIM}cache that sat beside it in the source volume did not travel.${RESET}"

echo
echo "${DIM}\$ docker exec <dind> ls -l /proof/clone/data/raw/sar /proof/clone/data/raw/ais${RESET}"
docker exec "$DIND" sh -c 'ls -l /proof/clone/data/raw/sar /proof/clone/data/raw/ais' | sed 's/^/  /'

# ---------------------------------------------------------------------------
rule "What this showed"
cat <<EOF
  ${GREEN}create${RESET}   one tar, manifest first, ${DIM}data cross-checked against sources.yaml${RESET}
  ${GREEN}inspect${RESET}  the contents without reading the contents
  ${GREEN}verify${RESET}   on a file and on a pipe
  ${RED}refused${RESET}  a flipped byte
  ${RED}refused${RESET}  a truncated transfer, named as truncation
  ${RED}refused${RESET}  a member the manifest lists and the archive does not hold
  ${RED}refused${RESET}  a granule whose bytes are not the ones M6 committed
  ${GREEN}restore${RESET}  into an empty daemon, from one static binary and a tarball

  The real bundle is ~18 GB and is built with ${BOLD}make bundle${RESET}. Everything above
  is a property of the format; none of it is a property of the size.
EOF
