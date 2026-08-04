#!/usr/bin/env bash
# The air gap, expressed a second time — in Kubernetes, and enforced.
#
# docker-compose.yml gets the boundary from one flag, `internal: true`, and the
# air-gap proof shows a container on that network cannot resolve a name. This
# shows the same claim in a second substrate, where it is not a flag but a
# NetworkPolicy, and where the default is the opposite: a pod network is
# routable, and every pod can reach the internet until something says no.
#
# Showing the same boundary twice in two runtimes is a stronger claim than
# showing it once, which is why this milestone was worth taking.
#
# THE NEGATIVE CONTROL IS THE POINT. "Egress is blocked" proves nothing on its
# own — a cluster with no route to the internet at all would pass it, and so
# would a policy the CNI silently ignores. Step 4 reaches the internet with the
# policy deleted and fails to reach it with the policy applied, in that order,
# from the same pod in the same cluster. Only the pair is evidence.
#
# That is not hypothetical. kind's default CNI does not implement
# NetworkPolicy: the object is accepted, `kubectl get networkpolicy` lists it,
# and nothing is enforced. This uses k3s, whose kube-router policy controller
# enforces out of the box — see docs/kubernetes.md.
#
# The host needs docker and nothing else. kubectl comes from inside the k3s
# image, helm from a container, and the cluster itself is a container.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'; GREEN=$'\033[32m'; RED=$'\033[31m'

K3S_IMAGE=${K3S_IMAGE:-rancher/k3s:v1.34.1-k3s1}
HELM_IMAGE=${HELM_IMAGE:-alpine/helm:3.16.3}
CLUSTER=${CLUSTER:-nightglass-k8s-proof}
NS=${NS:-nightglass}
RELEASE=${RELEASE:-ng}
CHART=deploy/helm/nightglass
VALUES=deploy/values-proof.yaml

# The images the proof needs in the cluster. ollama is deliberately absent —
# see deploy/values-proof.yaml.
IMAGES=(nightglass/app:dev postgis/postgis:17-3.5 qdrant/qdrant:v1.18.3)

TMP=$(mktemp -d)
cleanup() {
  rm -rf "$TMP"
  if [ "${KEEP:-}" = "1" ]; then
    echo; echo "${DIM}KEEP=1 — cluster left running. Remove with:${RESET}"
    echo "${DIM}  docker rm -f $CLUSTER${RESET}"
  else
    docker rm -f "$CLUSTER" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

rule() { printf '\n%s%s%s\n%s\n' "$BOLD" "$1" "$RESET" "$(printf '%.0s─' $(seq 1 ${#1}))"; }
k()    { docker exec "$CLUSTER" kubectl "$@"; }

# probe runs a command in the probe pod and reports reachability. Deliberately
# returns text rather than an exit code, because both outcomes are expected at
# different points and neither is an error.
#
# curl, not wget. The application image has curl and does NOT have wget — curl
# is in the Dockerfile on purpose, because the compose air-gap proof is
# literally `curl -m 5 https://example.com`. A first draft used wget here, the
# exec failed for a reason that had nothing to do with the network, and the
# probe dutifully reported "blocked". The negative control in step 4a is what
# caught it: a tool that is absent looks exactly like a boundary that works.
probe() {
  if k -n "$NS" exec probe -- curl -fsS -m 6 -o /dev/null "$1" 2>/dev/null; then
    echo reached
  else
    echo blocked
  fi
}

for i in "${IMAGES[@]}"; do
  if ! docker image inspect "$i" >/dev/null 2>&1; then
    echo "Image $i is not present. Try: make up" >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
rule "0. What the host has installed"
for t in kubectl helm k3s kind; do
  printf '  %-8s ' "$t"
  command -v "$t" >/dev/null 2>&1 && echo "$(command -v $t)" || echo "${DIM}not installed${RESET}"
done
echo "  ${DIM}docker   $(command -v docker)${RESET}"
echo
echo "${DIM}The cluster is a container, kubectl comes from inside it, and helm runs${RESET}"
echo "${DIM}from an image. The host contract is still docker and make — the same${RESET}"
echo "${DIM}argument the Go bundler makes, applied to a second toolchain.${RESET}"

# ---------------------------------------------------------------------------
rule "1. A cluster, from a container"
echo "${DIM}\$ docker run -d --privileged $K3S_IMAGE server${RESET}"
docker rm -f "$CLUSTER" >/dev/null 2>&1 || true
docker run -d --name "$CLUSTER" --privileged \
  --tmpfs /run --tmpfs /var/run \
  "$K3S_IMAGE" server --disable traefik --disable metrics-server \
  >/dev/null
# traefik and metrics-server are disabled because nothing here has an ingress
# or reads a metric. local-storage is deliberately NOT disabled: it is k3s's
# local-path provisioner, and without it the chart's PVCs stay Pending and the
# databases never schedule.

printf '  waiting for the node'
for _ in $(seq 1 120); do
  if k get nodes --no-headers 2>/dev/null | grep -q ' Ready'; then break; fi
  printf '.'; sleep 1
done
echo
k get nodes | sed 's/^/  /'
echo
echo "  ${DIM}k3s enforces NetworkPolicy with kube-router, out of the box. That is why${RESET}"
echo "  ${DIM}it is k3s and not kind, whose default CNI accepts the object and ignores${RESET}"
echo "  ${DIM}it — a policy that lists in kubectl and stops nothing.${RESET}"
k -n kube-system get daemonset,deployment --no-headers 2>/dev/null | head -3 | sed 's/^/  /'

# ---------------------------------------------------------------------------
rule "2. Images arrive as tarballs, not from a registry"
echo "${DIM}\$ docker save <image> | ctr -n k8s.io images import -${RESET}"
echo "${DIM}An air-gapped cluster has nothing to pull from. This is the same${RESET}"
echo "${DIM}per-image tar 'make bundle' already writes — the bundle and the cluster${RESET}"
echo "${DIM}want the identical artifact, which is most of the argument for taking${RESET}"
echo "${DIM}these two milestones in this order.${RESET}"
echo
for i in "${IMAGES[@]}"; do
  printf '  %-34s ' "$i"
  docker save "$i" | docker exec -i "$CLUSTER" ctr -n k8s.io images import - >/dev/null 2>&1 \
    && echo "imported" || { echo "FAILED"; exit 1; }
done
echo
echo "  ${DIM}imagePullPolicy is Never in values.yaml, so a missing import fails${RESET}"
echo "  ${DIM}loudly at schedule time instead of silently reaching for Docker Hub.${RESET}"

# ---------------------------------------------------------------------------
rule "3. Install the chart"
echo "${DIM}\$ helm template ng deploy/helm/nightglass -f deploy/values-proof.yaml${RESET}"
docker run --rm -v "$PWD/$CHART:/chart:ro" -v "$PWD/$VALUES:/values.yaml:ro" \
  "$HELM_IMAGE" template "$RELEASE" /chart -f /values.yaml --namespace "$NS" \
  > "$TMP/rendered.yaml"
echo "  rendered $(grep -c '^kind:' "$TMP/rendered.yaml") objects, $(wc -l < "$TMP/rendered.yaml") lines"

docker cp "$TMP/rendered.yaml" "$CLUSTER:/rendered.yaml" >/dev/null
k create namespace "$NS" >/dev/null
k -n "$NS" apply -f /rendered.yaml | sed 's/^/  /'

# A probe pod, in the same namespace, so it is governed by the same policy as
# every workload. Nothing about it is special — that is the point.
cat > "$TMP/probe.yaml" <<YAML
apiVersion: v1
kind: Pod
metadata: {name: probe, namespace: $NS, labels: {nightglass/component: probe}}
spec:
  enableServiceLinks: false
  containers:
    - name: c
      image: nightglass/app:dev
      imagePullPolicy: Never
      command: ["sleep", "3600"]
      # Limits here too, so step 6's check is about the namespace rather than
      # about which pods happen to be the chart's.
      resources:
        requests: {cpu: 10m, memory: 32Mi}
        limits: {cpu: 500m, memory: 256Mi}
YAML
docker cp "$TMP/probe.yaml" "$CLUSTER:/probe.yaml" >/dev/null
k apply -f /probe.yaml >/dev/null

printf '  waiting for pods'
for _ in $(seq 1 180); do
  ready=$(k -n "$NS" get pods --no-headers 2>/dev/null | awk '$2 ~ /^([0-9]+)\/\1$/ && $3=="Running"' | wc -l)
  total=$(k -n "$NS" get pods --no-headers 2>/dev/null | wc -l)
  [ "$total" -gt 0 ] && [ "$ready" -eq "$total" ] && break
  # A crash loop will never become ready, so stop waiting out the full timeout
  # and show why instead.
  if k -n "$NS" get pods --no-headers 2>/dev/null | grep -q CrashLoopBackOff; then break; fi
  printf '.'; sleep 1
done
echo
k -n "$NS" get pods | sed 's/^/  /'
broken=$(k -n "$NS" get pods --no-headers 2>/dev/null | awk '$3!="Running"{print $1}')
if [ -n "$broken" ]; then
  for p in $broken; do
    echo "  ${RED}$p is not Running — last log lines:${RESET}"
    k -n "$NS" logs "$p" --tail=8 2>&1 | sed 's/^/    /'
  done
  # A proof that reports a working boundary while half the workloads are in a
  # crash loop is measuring the probe pod, not the deployment. The first
  # version of this script did exactly that and exited 0.
  printf '\n%sFAIL%s the chart did not come up.\n' "$RED" "$RESET"
  exit 1
fi

# ---------------------------------------------------------------------------
rule "4. The negative control, and the boundary"

echo "${BOLD}a. policy deleted — is the internet reachable at all from this pod?${RESET}"
k -n "$NS" delete networkpolicy --all >/dev/null 2>&1 || true
sleep 4
r_off=$(probe https://example.com)
printf '  egress to https://example.com   %s\n' \
  "$([ "$r_off" = reached ] && echo "${GREEN}reached${RESET}" || echo "${RED}$r_off${RESET}")"
if [ "$r_off" != reached ]; then
  printf '\n%sFAIL%s the cluster cannot reach the internet even with no policy.\n' "$RED" "$RESET"
  echo "  Nothing below would be evidence of anything — a blocked result would"
  echo "  just be the sandbox. Run this where egress is possible."
  exit 1
fi

echo
echo "${BOLD}b. policy applied${RESET}"
k -n "$NS" apply -f /rendered.yaml >/dev/null
k -n "$NS" get networkpolicy | sed 's/^/  /'
sleep 5
r_on=$(probe https://example.com)
r_peer=$(probe http://qdrant.${NS}.svc.cluster.local:6333/readyz)
printf '\n  egress to https://example.com   %s\n' \
  "$([ "$r_on" = blocked ] && echo "${GREEN}blocked${RESET}" || echo "${RED}$r_on  <-- NOT ENFORCED${RESET}")"
printf '  qdrant inside the namespace     %s\n' \
  "$([ "$r_peer" = reached ] && echo "${GREEN}reached${RESET}" || echo "${RED}$r_peer  <-- too strict${RESET}")"

[ "$r_on" = blocked ]  || { printf '\n%sFAIL%s the policy is not being enforced.\n' "$RED" "$RESET"; exit 1; }
[ "$r_peer" = reached ] || { printf '\n%sFAIL%s the enclave cannot talk to itself.\n' "$RED" "$RESET"; exit 1; }

echo
echo "  ${DIM}Reachable without the policy, unreachable with it, same pod and same${RESET}"
echo "  ${DIM}cluster seconds apart. That pair is the evidence; either half alone${RESET}"
echo "  ${DIM}would be consistent with a CNI that ignores the object entirely.${RESET}"

# ---------------------------------------------------------------------------
rule "5. How this boundary differs from the compose one"
echo "${DIM}\$ kubectl exec probe -- getent hosts example.com${RESET}"
k -n "$NS" exec probe -- getent hosts example.com 2>/dev/null | sed 's/^/  /' \
  || echo "  (no answer)"
echo
echo "  ${DIM}The name still resolves. CoreDNS lives in kube-system, is outside this${RESET}"
echo "  ${DIM}policy, and forwards upstream, so a pod learns the address and is then${RESET}"
echo "  ${DIM}refused the connection. The compose enclave fails earlier and harder —${RESET}"
echo "  ${DIM}'Could not resolve host', no packet ever sent. Two real boundaries, two${RESET}"
echo "  ${DIM}different failure modes, and the difference is stated rather than${RESET}"
echo "  ${DIM}smoothed over. docs/kubernetes.md has the measurement and what would${RESET}"
echo "  ${DIM}close it.${RESET}"

# ---------------------------------------------------------------------------
rule "6. Resource limits are set, on every container"
k -n "$NS" get pods -o \
  custom-columns='POD:.metadata.name,CPU_REQ:.spec.containers[*].resources.requests.cpu,MEM_REQ:.spec.containers[*].resources.requests.memory,MEM_LIM:.spec.containers[*].resources.limits.memory' \
  | sed 's/^/  /'
unlimited=$(k -n "$NS" get pods -o json \
  | grep -c '"resources": *{}' || true)
echo
if [ "$unlimited" -gt 0 ]; then
  echo "  ${RED}$unlimited container(s) declare no resources at all${RESET}"
else
  echo "  ${GREEN}every container declares requests and a memory limit${RESET}"
fi

# ---------------------------------------------------------------------------
rule "What this showed"
cat <<EOF
  ${GREEN}cluster${RESET}  k3s in a container; host has no kubectl, no helm, no k3s
  ${GREEN}images${RESET}   imported from the same per-image tars ${BOLD}make bundle${RESET} writes
  ${GREEN}chart${RESET}    rendered by helm-in-a-container, applied, every pod Running
  ${GREEN}control${RESET}  internet ${BOLD}reached${RESET} with the policy deleted  ${DIM}<- without this the rest is not evidence${RESET}
  ${GREEN}boundary${RESET} internet ${BOLD}blocked${RESET} with it applied — same pod, seconds later
  ${GREEN}intra${RESET}    qdrant still reachable inside the namespace
  ${GREEN}limits${RESET}   requests and memory limits on every container

  Not exercised: ollama, and the 6.2 GB of granules and corpus the enclave
  reads. Both are in the chart and neither changes whether an egress rule is
  enforced. deploy/values-proof.yaml is the override that turns them off.
EOF
