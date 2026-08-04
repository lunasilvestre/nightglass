"""The external archives this deployment is built from — ASF and the DMA.

Model weights, documents and the shoreline all had an explicit online fetch step
from the milestone they were introduced in. The **granules did not**: 4.7 GB of
Sentinel-1 was staged by hand during pre-dev, `data/` is gitignored, and so
§M6's done-when — *"someone else could clone, `make up`, and reproduce the
demo"* — was false for everyone except the machine it was built on. This module
is that missing step, plus the same treatment for the Danish AIS day, which had
the identical problem for the identical reason.

Three properties are worth stating, because they are the difference between a
download script and a provisioning step:

**The manifest is committed; the bytes are not.** `data/sources.yaml` carries a
URL, a size and a sha256 for every external input. A fetch that produces a
different hash is not the same evidence the README's numbers were measured over,
so it fails loudly instead of quietly seeding a database with something else.

**The credential goes to exactly one host.** ASF's download is a redirect chain:
`datapool.asf.alaska.edu` → `urs.earthdata.nasa.gov` (authenticate) → a signed
CloudFront URL. The documented way to survive it is `curl --location-trusted`,
which means *send the credential to whatever host you are redirected to*. This
walks the chain by hand instead and attaches Basic auth only when the hop is
Earthdata itself. The signed URL at the end already carries its own
authorisation and has no business seeing a password.

**It resumes.** A 1 GB granule over a domestic link is a download that will be
interrupted, and ASF serves `Range` (verified: HTTP 206). A partial file is kept
as `.part` and continued, so an interrupted `make fetch-granules` costs the
interrupted file rather than the whole set.

ONLINE. This module runs in the `fetcher` image on the `provision` network and
never inside the enclave — the same posture as the weights, the corpus and the
coastline. Inside the enclave it fails at DNS resolution, which is correct.
"""

from __future__ import annotations

import hashlib
import netrc
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

#: The only host that may see the Earthdata credential. Everything else in the
#: chain is either a public redirector or a pre-signed URL.
URS_HOST = "urs.earthdata.nasa.gov"

#: Roles in fetch order of decreasing necessity. `required` is the default set:
#: a clone that fetches only these can run every proof and the demo.
Role = Literal["required", "optional", "superseded"]
ROLES: tuple[Role, ...] = ("required", "optional", "superseded")

_CHUNK = 1 << 20


class ArchiveError(RuntimeError):
    """Raised with remediation attached — these failures all have a next step."""


@dataclass(frozen=True)
class Item:
    """One external file: where it comes from, and what it must hash to."""

    name: str
    url: str
    sha256: str
    bytes: int
    aoi: str
    role: Role
    note: str = ""

    @property
    def mb(self) -> float:
        return self.bytes / 1e6


@dataclass(frozen=True)
class Section:
    """One archive — `sar` or `ais` — as `data/sources.yaml` declares it."""

    key: str
    licence: str
    credentials: str
    out: str
    items: tuple[Item, ...]

    def select(self, roles: tuple[str, ...]) -> tuple[tuple[Item, ...], tuple[Item, ...]]:
        """Split into (wanted, skipped). Both halves are reported, never dropped."""
        wanted = tuple(i for i in self.items if i.role in roles)
        return wanted, tuple(i for i in self.items if i.role not in roles)


def load_manifest(path: str | Path, section: str) -> Section:
    import yaml

    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArchiveError(
            f"no manifest at {path}. It is committed at data/sources.yaml; the "
            f"fetcher services mount it read-only."
        ) from exc
    if section not in (doc or {}):
        raise ArchiveError(f"{path}: no '{section}:' section (have: {sorted(doc or {})})")
    raw = doc[section]
    items = []
    for entry in raw.get("items", []):
        role = entry.get("role", "required")
        if role not in ROLES:
            raise ArchiveError(f"{path}: {entry.get('name')} has role {role!r}, expected {ROLES}")
        items.append(
            Item(
                name=entry["name"],
                url=entry["url"],
                sha256=entry["sha256"].strip().lower(),
                bytes=int(entry["bytes"]),
                aoi=entry.get("aoi", ""),
                role=role,
                note=" ".join(entry.get("note", "").split()),
            )
        )
    return Section(
        key=section,
        licence=" ".join(raw.get("licence", "").split()),
        credentials=raw.get("credentials", "none"),
        out=raw.get("out", ""),
        items=tuple(items),
    )


# -- credentials -------------------------------------------------------------


def earthdata_credentials() -> tuple[str, str]:
    """`EARTHDATA_USERNAME`/`_PASSWORD`, else `~/.netrc`, else a refusal.

    The environment comes first because that is how the credential reaches the
    container: `scripts/load-env.sh` resolves it on the host — from the same
    `~/.netrc` the ASF documentation tells you to write — and compose forwards
    the two variables by name with no value, exactly as it forwards `GFW_TOKEN`.
    The netrc branch is for running this outside compose.
    """
    user = os.environ.get("EARTHDATA_USERNAME", "").strip()
    password = os.environ.get("EARTHDATA_PASSWORD", "")
    if user and password:
        return user, password

    candidates = [Path(p) for p in (os.environ.get("NETRC"), "/app/.netrc") if p]
    candidates.append(Path.home() / ".netrc")
    for path in candidates:
        if not path.exists():
            continue
        try:
            auth = netrc.netrc(str(path)).authenticators(URS_HOST)
        except (netrc.NetrcParseError, OSError):
            continue
        if auth and auth[0] and auth[2]:
            return auth[0], auth[2]

    raise ArchiveError(
        "no Earthdata credential.\n"
        "Sentinel-1 is free and open, but ASF requires a login and an accepted EULA:\n"
        "  1. register at https://urs.earthdata.nasa.gov\n"
        "  2. accept the ASF EULA at https://urs.earthdata.nasa.gov/profile\n"
        "     — a valid password is NOT enough on its own, and the failure without\n"
        "       the EULA is a redirect loop rather than a 401\n"
        "  3. put it in ~/.netrc:\n"
        "       machine urs.earthdata.nasa.gov login <user> password <pass>\n"
        "     (chmod 600), or export EARTHDATA_USERNAME and EARTHDATA_PASSWORD.\n"
        "`make fetch-granules` reads either one and forwards it to the provision\n"
        "network only. Nothing inside the enclave ever sees it."
    )


# -- the transport -----------------------------------------------------------


def _resolve(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    auth: tuple[str, str] | None,
    max_hops: int = 20,
) -> httpx.Response:
    """Walk the redirect chain, attaching the credential only at `URS_HOST`.

    The real chain for one granule is six hops:

        datapool.asf.alaska.edu   307 ->  sentinel1.asf.alaska.edu
        sentinel1.asf.alaska.edu  302 ->  urs.earthdata.nasa.gov/oauth/authorize
        urs.earthdata.nasa.gov    302 ->  sentinel1.asf.alaska.edu/login?code=…   <- auth here
        sentinel1.asf.alaska.edu  301 ->  /GRD_HD/SD/<granule>.zip                <- and again
        sentinel1.asf.alaska.edu  303 ->  <signed CloudFront URL>
        cloudfront                206      the bytes

    Note hops 1 and 4: the OAuth round-trip **returns to a URL it has already
    visited**, this time holding a session cookie. So "we have seen this URL
    before" is not a loop — a first draft used it as the loop guard and refused
    every download with a confident and wrong diagnosis. Bounding the hop count
    is the guard that works.

    Returns the streamed final response, which the caller must close.
    """
    start = url
    for _ in range(max_hops):
        host = httpx.URL(url).host
        request = client.build_request("GET", url, headers=headers)
        response = client.send(
            request,
            stream=True,
            follow_redirects=False,
            auth=(auth if host == URS_HOST else None),
        )
        if not response.is_redirect:
            return response
        location = response.headers.get("location", "")
        response.close()
        if not location:
            raise ArchiveError(f"{host} returned {response.status_code} with no Location header")
        url = str(httpx.URL(url).join(location))
    raise ArchiveError(
        f"more than {max_hops} redirects starting at {start}\n"
        "A chain that never terminates is what an unaccepted EULA looks like: the\n"
        "login succeeds and the authorisation never does, so Earthdata and ASF hand\n"
        "each other the request forever. Accept the ASF EULA at\n"
        "https://urs.earthdata.nasa.gov/profile and try again."
    )


def _explain(response: httpx.Response, item: Item) -> str:
    if response.status_code in (401, 403):
        return (
            f"{item.name}: HTTP {response.status_code} from {response.url.host}.\n"
            "Either the Earthdata credential is wrong, or the ASF EULA has not been\n"
            "accepted at https://urs.earthdata.nasa.gov/profile. The second is far more\n"
            "common and does not look like an auth failure."
        )
    return f"{item.name}: HTTP {response.status_code} from {response.url.host}"


def download(
    item: Item,
    out_dir: str | Path,
    *,
    auth: tuple[str, str] | None = None,
    force: bool = False,
    verify_cached: bool = False,
    progress: bool = True,
) -> tuple[Path, str]:
    """Fetch one item, resuming a `.part` if there is one. Returns (path, outcome).

    `outcome` is one of `cached`, `verified`, `downloaded`, `resumed` — printed
    rather than summarised, because "it was already there" and "it was fetched
    and checked" are different claims about the same file.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / item.name
    part = final.with_suffix(final.suffix + ".part")

    if final.exists() and not force:
        size = final.stat().st_size
        if verify_cached:
            digest = sha256_file(final, progress=progress and size > 100e6)
            if digest != item.sha256:
                raise ArchiveError(
                    f"{item.name}: on disk but sha256 {digest[:16]}… != manifest "
                    f"{item.sha256[:16]}…. Delete it and re-fetch, or pass --force."
                )
            return final, "verified"
        if size == item.bytes:
            return final, "cached"
        raise ArchiveError(
            f"{item.name}: on disk at {size:,} bytes, manifest says {item.bytes:,}. "
            f"A truncated download looks exactly like this — delete it and re-fetch."
        )

    if force and part.exists():
        part.unlink()
    resume_from = part.stat().st_size if part.exists() else 0
    if resume_from >= item.bytes:  # a .part that is somehow complete: start over
        part.unlink()
        resume_from = 0

    headers = {"User-Agent": "nightglass/0.1 (+provisioning)"}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"

    digest = hashlib.sha256()
    if resume_from:
        with part.open("rb") as fh:
            for block in iter(lambda: fh.read(_CHUNK), b""):
                digest.update(block)

    with httpx.Client(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=False) as client:
        response = _resolve(client, item.url, headers=headers, auth=auth)
        try:
            if response.status_code not in (200, 206):
                raise ArchiveError(_explain(response, item))
            if resume_from and response.status_code == 200:
                # The server ignored the Range and is sending the whole file.
                # Starting over is the only correct response; appending would
                # produce a file that is the right size and the wrong bytes.
                resume_from = 0
                digest = hashlib.sha256()
                part.unlink(missing_ok=True)
            done = resume_from
            mode = "ab" if resume_from else "wb"
            # A carriage return is a progress bar on a terminal and a wall of
            # repeated lines in a log or a recording. `make fetch-granules`
            # legitimately runs both ways, so the shape follows the sink.
            tty = progress and sys.stdout.isatty()
            step = max(item.bytes // 10, 1)
            next_mark = (done // step + 1) * step
            with part.open(mode) as fh:
                for block in response.iter_bytes(_CHUNK):
                    fh.write(block)
                    digest.update(block)
                    done += len(block)
                    if not progress:
                        continue
                    pct = 100.0 * done / item.bytes if item.bytes else 0.0
                    line = (
                        f"   {item.name[:46]:46s} {done / 1e6:8.1f} /"
                        f" {item.mb:8.1f} MB  {pct:5.1f}%"
                    )
                    if tty:
                        print(f"\r{line}", end="", flush=True)
                    elif done >= next_mark:
                        print(line, flush=True)
                        next_mark += step
        finally:
            response.close()
    if progress and sys.stdout.isatty():
        print()

    got = digest.hexdigest()
    if got != item.sha256:
        raise ArchiveError(
            f"{item.name}: sha256 {got}\n"
            f"          manifest {item.sha256}\n"
            "The bytes differ from the ones every number in the README was measured\n"
            "over. The partial file is kept as .part for inspection; delete it to retry."
        )
    size = part.stat().st_size
    if size != item.bytes:
        raise ArchiveError(f"{item.name}: {size:,} bytes, manifest says {item.bytes:,}")
    part.rename(final)
    return final, ("resumed" if resume_from else "downloaded")


def sha256_file(path: str | Path, *, progress: bool = False) -> str:
    path = Path(path)
    total = path.stat().st_size
    digest = hashlib.sha256()
    done = 0
    tty = progress and sys.stdout.isatty()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(block)
            done += len(block)
            if tty:
                print(f"\r   hashing {path.name[:40]:40s} {100.0 * done / total:5.1f}%",
                      end="", flush=True)
    if tty:
        print("\r" + " " * 60 + "\r", end="")
    return digest.hexdigest()
