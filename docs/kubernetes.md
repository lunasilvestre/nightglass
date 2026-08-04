# The same boundary, twice: the air gap as a NetworkPolicy

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

The compose enclave gets its boundary from one flag. `internal: true` on the network, and Docker
attaches no default route and no NAT rule — there is no path to the internet to misconfigure, and
[the air-gap proof](air-gap.md) shows a container on it cannot even resolve a name.

Kubernetes has no such flag. A pod network is routable by default and every pod can reach the
internet until something says otherwise. So the same claim has to be made a second way, and the
object that makes it is [`deploy/helm/nightglass/templates/networkpolicy.yaml`](../deploy/helm/nightglass/templates/networkpolicy.yaml).
Showing one boundary in two substrates is a stronger claim than showing it in one, which is the
whole reason this was worth building.

```bash
make k8s-lint      # helm lint, in a container
make k8s-render    # read the rendered NetworkPolicy without standing anything up
make k8s-proof     # a cluster, the chart, and the boundary — about 4½ minutes
```

The host needs no `kubectl`, no `helm` and no `k3s`. The cluster is a container, kubectl lives
inside it, and helm runs from an image — the same argument [the bundler](bundle.md) makes about
Go, applied to a second toolchain. `scripts/preflight.sh` gained no line for this either.

## The policy

```yaml
spec:
  podSelector: {}          # every pod in the namespace
  policyTypes: [Egress]
  egress:
    - to: [{podSelector: {}}]              # the enclave talking to itself
    - to:                                  # CoreDNS, port 53, and nothing else
        - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}}
          podSelector: {matchLabels: {k8s-app: kube-dns}}
      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}]
```

An empty `podSelector` selects every pod in the namespace, and naming `Egress` in `policyTypes`
with a rule list that excludes the internet is what makes the default a deny. The two allowances
are the minimum a pod needs to be a pod: reach its own namespace, and resolve a name. There is
deliberately nothing for a registry, a proxy or an object store — if one is ever needed it belongs
in that file, where a reviewer will see it, rather than in a pod annotation.

The DNS rule names a **pod selector**, not just the namespace. `kube-system` holds a great deal
more than CoreDNS, and "egress to kube-system" would quietly authorise all of it.

## The negative control is the evidence

"Egress is blocked" proves nothing on its own. A cluster with no route to the internet would
report it. So would a CNI that accepts the policy object and ignores it — and that is not
hypothetical: **kind's default CNI does not implement NetworkPolicy.** The object is accepted,
`kubectl get networkpolicy` lists it happily, and nothing is enforced. A proof built on kind
without a CNI swap would have passed while demonstrating the opposite of its claim.

So `make k8s-proof` does it in both directions, from the same pod in the same cluster, seconds
apart:

```
a. policy deleted — is the internet reachable at all from this pod?
   egress to https://example.com   reached

b. policy applied
   egress to https://example.com   blocked
   qdrant inside the namespace     reached
```

The script fails loudly if step (a) comes back blocked, because at that point nothing below it
would be evidence of anything.

This is why the chart targets **k3s** rather than kind. k3s ships kube-router's network-policy
controller and enforces out of the box; measured here, a cluster is `Ready` about eight seconds
after `docker run`.

## How this boundary differs from the compose one

It is weaker in one specific, nameable way, and the difference is worth stating rather than
smoothing over.

| | compose, `internal: true` | Kubernetes, NetworkPolicy |
|---|---|---|
| what fails | name resolution | the connection |
| observed | `curl: (6) Could not resolve host` | `curl: (7) Failed to connect to 104.20.23.154` |
| packets sent | none, ever | none to the destination — but the DNS query left |

A pod under this policy still resolves `example.com` to a real address. CoreDNS lives in
`kube-system`, sits outside the policy, and forwards upstream, so the name comes back and *then*
the connection is refused. `make k8s-proof` prints the resolved address rather than hiding it.

The obvious fix does not work. A second policy restricting CoreDNS's own egress to the cluster
CIDRs was tried and **broke in-cluster service resolution**, because CoreDNS needs paths that
allow-list did not cover; external names kept resolving from its 30-second cache while
`svc.cluster.local` stopped working. It is not shipped. On this topology the upstream resolver
reached `192.168.8.1` — the host's own LAN router, an RFC1918 address — so the naive
"allow private, deny public" idiom would not have closed it either.

What actually closes it is the deployment: **a real air-gapped site has no reachable upstream
resolver**, so CoreDNS's forward fails and external names do not resolve. The gap measured here is
an artifact of proving this on a networked laptop, not a property of the design. Which is a fair
thing to say and still not the same as having demonstrated it.

## What the chart contains, and what the proof leaves out

Thirteen objects: the NetworkPolicy, a Secret and ConfigMap, PVCs, ClusterIP Services and
Deployments for `api`, `mcp`, `postgis`, `qdrant`, and `ollama` when enabled.

**No service publishes a port.** No NodePort, no LoadBalancer, no Ingress. The compose enclave is
reached with `docker compose exec` and the cluster equivalent is `kubectl exec`; a ClusterIP is
reachable inside the namespace and nowhere else, which is the same posture rather than a weaker
one.

**Images arrive as tarballs.** `imagePullPolicy: Never`, and the proof imports with
`docker save <image> | ctr -n k8s.io images import -`. That is the *same per-image tar*
[`make bundle`](bundle.md) already writes — the bundle and the offline cluster want the identical
artifact, which is most of the argument for having taken these two in this order. A missing import
fails at schedule time instead of silently reaching for Docker Hub.

**Resource limits on every container**, verified by the proof rather than asserted. `ollama` is
the one exception to the pattern and deliberately: memory is capped, CPU is not, because a CPU
limit on an inference server buys a throttled model rather than a protected node.

Two things the proof does **not** exercise, both stated in
[`deploy/values-proof.yaml`](../deploy/values-proof.yaml) rather than left to be discovered:

- **`ollama`.** A 3.26 GB image and a 10.15 GB volume of weights, none of which changes whether an
  egress rule is enforced. Moving 13 GB to make a point about a firewall rule is how a proof
  becomes a thing nobody runs.
- **The 6.2 GB of granules, AIS and corpus.** The compose enclave bind-mounts them read-only from
  the host; a cluster has no host to bind from. The chart gives `/app/data` an `emptyDir`, so the
  services start and the tools that read pixels have nothing to read. A real deployment gives it a
  volume populated from the transfer bundle — `nightglass-bundle restore` writes exactly that
  tree. What runs today is the boundary, the services and their wiring.

## Two things that cost an afternoon

**Kubernetes injects `<SERVICE>_PORT=tcp://ip:port` for every service in the namespace.** A
service named `qdrant` produces `QDRANT_PORT=tcp://10.43.14.154:6333`; the application has its own
`QDRANT_PORT` setting and expects an integer, so every pod died in pydantic validation before it
started. `enableServiceLinks: false` is both the fix and the better posture — nothing here
resolves a peer any way but by DNS, and an enclave pod should not carry env vars nothing asked
for.

**A probe that uses a tool the image lacks reports a working boundary.** The first version of the
proof used `wget`, which the application image does not have; the exec failed for reasons that had
nothing to do with the network, and the probe dutifully reported `blocked`. The negative control
caught it — an absent tool looks exactly like an enforced policy. The image has `curl`, and has it
on purpose, because the compose air-gap proof is literally `curl -m 5 https://example.com`.
