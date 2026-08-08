# Architecture: one line of compose, and what follows from it

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

![NIGHTGLASS architecture — the sealed enclave, the host beside it, and the profile-gated provision network](architecture.svg)

The boundary is one line of `docker-compose.yml`: the `enclave` network is declared
`internal: true`, so Docker attaches no default route and installs no NAT rule. There is no
egress path to misconfigure, and nothing to keep in sync as services are added. Default deny by
construction rather than by a firewall rule somebody has to maintain.

Two consequences follow, and both are deliberate.

### No service publishes a port

Verified on Docker 29.7.1: a container on an internal network started with `-p 18099:80` comes
up fine, `docker ps` shows `80/tcp` with no host mapping, and the port is dead — with no warning
emitted anywhere. So ports are not merely omitted for tidiness; they do not work. Access is
`docker compose exec`, and MCP reaches Claude Desktop over stdio through `docker exec` rather
than over a socket.

### Nothing inside can fetch its own inputs at runtime

Correct, and it applies to every input: weights via `model-puller`, documents via
`corpus-fetcher`, the Sentinel-1 granules via `granule-fetcher`, the Danish AIS day via
`ais-fetcher`, the shoreline via `coastline-fetcher`, the GFW cross-check layer via
`gfw-fetcher`. Every one is a profile-gated service on the `provision` network, invoked
explicitly by a `make fetch-*` target, and none is running during operation. Provisioning and
operation are different security postures and the compose file says so out loud rather than
blurring them.

That the list is six items long and not three is itself a finding. The detector's own land mask
structurally cannot separate a 100 m skerry from a 100 m hull, so a real air-gapped deployment
has to ship a coastline with it; and validating a detector needs someone else's detections, so
it has to ship those too. Both are clipped or filtered online — the enclave carries 713 KB of
shoreline rather than a 149 MB archive, and 69 detections rather than a global layer.

The two data fetchers exist because without them this repository was not *reproducible*:
granules and AIS had originally been staged by hand, and `data/` is gitignored — so every number
in the README was true and none of it could be regenerated anywhere but on one machine.
[`data/sources.yaml`](../data/sources.yaml) is the committed half — a URL, a byte count and a
sha256 for all eight external files — and the fetchers refuse anything that hashes differently.
A number you cannot trace to a byte is a number you are asking to be taken on trust.

### Credentials follow the same split

`GFW_TOKEN` and the Earthdata login are provider identities, so they live outside the repository
— `~/.config/eo-credentials.env` (chmod 600) and `~/.netrc` respectively — and are forwarded to
the provision service by the invoking shell, never written into `.env`, and never reachable from
the enclave, where they would be useless anyway. ASF's own documentation reaches for
`curl --location-trusted`, which means *send the password to whatever host redirects you*;
[`spatial/archive.py`](../src/nightglass/spatial/archive.py) walks the redirect chain itself and
attaches the credential only when the hop is `urs.earthdata.nasa.gov`.

---

**See also:** [the air-gap proof](air-gap.md) · [the same boundary in Kubernetes](kubernetes.md)
· [crossing the gap with a bundle](bundle.md) · [design decisions](design-decisions.md)
