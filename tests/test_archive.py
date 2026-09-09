"""Provisioning downloads: the credential boundary, and the resume.

Two properties are the reason this module exists rather than a `curl` line, and
both are tested here against httpx's own `MockTransport` — a scripted server, not
a stub of our own code.

**The credential goes to exactly one host.** ASF's chain ends at a pre-signed
CloudFront URL that has no business seeing a password, and the OAuth round trip
comes back to a URL it has already visited, so "seen before" is not a loop. A
first draft used it as the loop guard and refused every download.

**A partial file is continued, not appended to blindly.** A server that ignores
`Range` and sends the whole file again would otherwise produce a file of exactly
the right size and entirely the wrong bytes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from nightglass.spatial import archive as A

MANIFEST = """
sar:
  licence: |
    Contains modified Copernicus Sentinel data 2026.
  credentials: earthdata
  out: data/raw/sar
  items:
    - name: required.zip
      url: https://datapool.asf.alaska.edu/GRD_HD/SD/required.zip
      sha256: "AABBCC"
      bytes: 10
      aoi: kattegat
      role: required
      note: |
        the validation
        granule
    - name: optional.zip
      url: https://datapool.asf.alaska.edu/GRD_HD/SD/optional.zip
      sha256: ddeeff
      bytes: 20
      role: optional
"""


def _manifest(tmp_path: Path, text: str = MANIFEST) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _item(**kw: Any) -> A.Item:
    base = {
        "name": "granule.zip",
        "url": "https://datapool.asf.alaska.edu/granule.zip",
        "sha256": "",
        "bytes": 0,
        "aoi": "kattegat",
        "role": "required",
    }
    base.update(kw)
    return A.Item(**base)  # type: ignore[arg-type]


# -- the manifest --------------------------------------------------------------


def test_the_manifest_is_read_with_its_hashes_lowercased_and_notes_flattened(tmp_path):
    """The sha256 in the file is compared against one computed here, so case has
    to be settled at the boundary; a note wrapped over two lines is one sentence."""
    section = A.load_manifest(_manifest(tmp_path), "sar")
    assert section.key == "sar" and section.credentials == "earthdata"
    assert section.items[0].sha256 == "aabbcc"
    assert section.items[0].note == "the validation granule"
    assert section.licence == "Contains modified Copernicus Sentinel data 2026."
    assert section.items[0].mb == pytest.approx(1e-5)


def test_a_missing_manifest_says_where_the_committed_one_lives(tmp_path):
    with pytest.raises(A.ArchiveError, match="data/sources.yaml"):
        A.load_manifest(tmp_path / "gone.yaml", "sar")


def test_an_unknown_section_lists_the_ones_that_exist(tmp_path):
    with pytest.raises(A.ArchiveError, match=r"no 'ais:' section \(have: \['sar'\]\)"):
        A.load_manifest(_manifest(tmp_path), "ais")


def test_an_unknown_role_is_named_alongside_the_ones_that_exist(tmp_path):
    bad = MANIFEST.replace("role: optional", "role: nice-to-have")
    with pytest.raises(A.ArchiveError, match="role 'nice-to-have'"):
        A.load_manifest(_manifest(tmp_path, bad), "sar")


def test_selecting_roles_reports_the_skipped_half_rather_than_dropping_it(tmp_path):
    """A fetch that quietly leaves items out is a fetch nobody can reconcile
    against the manifest, so both halves come back."""
    section = A.load_manifest(_manifest(tmp_path), "sar")
    wanted, skipped = section.select(("required",))
    assert [i.name for i in wanted] == ["required.zip"]
    assert [i.name for i in skipped] == ["optional.zip"]
    assert len(wanted) + len(skipped) == len(section.items)


# -- credentials ---------------------------------------------------------------


@pytest.fixture
def no_ambient_netrc(monkeypatch, tmp_path):
    """This host's own ~/.netrc must not decide the outcome of these tests."""
    monkeypatch.delenv("EARTHDATA_USERNAME", raising=False)
    monkeypatch.delenv("EARTHDATA_PASSWORD", raising=False)
    monkeypatch.delenv("NETRC", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "nohome"))


def test_the_environment_beats_a_netrc(monkeypatch, tmp_path, no_ambient_netrc):
    """That order is how the credential reaches the container: the host resolves
    it and compose forwards the two variables by name."""
    netrc = tmp_path / ".netrc"
    netrc.write_text(f"machine {A.URS_HOST} login from-netrc password np\n")
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    monkeypatch.setenv("EARTHDATA_USERNAME", "from-env")
    monkeypatch.setenv("EARTHDATA_PASSWORD", "ep")

    assert A.earthdata_credentials() == ("from-env", "ep")


def test_a_netrc_is_read_when_the_environment_is_empty(monkeypatch, tmp_path, no_ambient_netrc):
    netrc = tmp_path / ".netrc"
    netrc.write_text(f"machine {A.URS_HOST} login from-netrc password np\n")
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))

    assert A.earthdata_credentials() == ("from-netrc", "np")


def test_a_netrc_for_another_host_is_not_a_credential(monkeypatch, tmp_path, no_ambient_netrc):
    netrc = tmp_path / ".netrc"
    netrc.write_text("machine example.com login someone password pw\n")
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))

    with pytest.raises(A.ArchiveError, match="no Earthdata credential"):
        A.earthdata_credentials()


def test_a_malformed_netrc_is_stepped_over_not_raised(monkeypatch, tmp_path, no_ambient_netrc):
    broken = tmp_path / "broken.netrc"
    broken.write_text("this is not a netrc file at all {{{\n")
    broken.chmod(0o600)
    monkeypatch.setenv("NETRC", str(broken))

    with pytest.raises(A.ArchiveError) as exc:
        A.earthdata_credentials()
    assert "accept the ASF EULA" in str(exc.value)


def test_the_refusal_gives_the_three_steps_that_fix_it(monkeypatch, no_ambient_netrc):
    with pytest.raises(A.ArchiveError) as exc:
        A.earthdata_credentials()
    message = str(exc.value)
    assert "1. register at" in message
    assert "2. accept the ASF EULA" in message
    assert "3. put it in ~/.netrc" in message
    assert "redirect loop rather than a 401" in message


# -- the redirect chain --------------------------------------------------------

CHAIN = {
    "https://datapool.asf.alaska.edu/granule.zip": (307, "https://sentinel1.asf.alaska.edu/x"),
    "https://sentinel1.asf.alaska.edu/x": (302, f"https://{A.URS_HOST}/oauth/authorize"),
    f"https://{A.URS_HOST}/oauth/authorize": (302, "https://sentinel1.asf.alaska.edu/x?code=1"),
    "https://sentinel1.asf.alaska.edu/x?code=1": (303, "https://cloudfront.net/signed"),
}


def _chain_client(seen: list[tuple[str, str | None]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization")))
        hop = CHAIN.get(str(request.url))
        if hop is None:
            return httpx.Response(200, content=b"bytes")
        return httpx.Response(hop[0], headers={"location": hop[1]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_only_earthdata_is_shown_the_credential():
    """The signed URL at the end already carries its own authorisation. Sending a
    password to it would be `curl --location-trusted`, which is what this walks
    the chain by hand to avoid."""
    seen: list[tuple[str, str | None]] = []
    with _chain_client(seen) as client:
        response = A._resolve(
            client,
            "https://datapool.asf.alaska.edu/granule.zip",
            headers={"User-Agent": "nightglass"},
            auth=("user", "pass"),
        )
    assert response.status_code == 200
    authorised = [url for url, auth in seen if auth is not None]
    assert authorised == [f"https://{A.URS_HOST}/oauth/authorize"]
    assert seen[-1][0] == "https://cloudfront.net/signed"


def test_returning_to_a_url_already_visited_is_not_a_loop():
    """Hops 2 and 4 are the same host and path; the OAuth round trip comes back
    holding a session cookie. Bounding the hop count is the guard that works."""
    seen: list[tuple[str, str | None]] = []
    with _chain_client(seen) as client:
        A._resolve(
            client,
            "https://datapool.asf.alaska.edu/granule.zip",
            headers={},
            auth=("u", "p"),
        )
    visited = [url.split("?")[0] for url, _ in seen]
    assert visited.count("https://sentinel1.asf.alaska.edu/x") == 2


def test_an_endless_chain_is_diagnosed_as_an_unaccepted_eula():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://a.example/next"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(
        A.ArchiveError
    ) as exc:
        A._resolve(client, "https://a.example/start", headers={}, auth=None, max_hops=4)
    assert "more than 4 redirects" in str(exc.value)
    assert "Accept the ASF EULA" in str(exc.value)


def test_a_redirect_without_a_location_names_the_host_that_sent_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(
        A.ArchiveError, match="a.example returned 302 with no Location"
    ):
        A._resolve(client, "https://a.example/start", headers={}, auth=None)


@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_status_is_explained_as_the_eula_first(status: int):
    """The common cause is an unaccepted EULA and it does not look like an auth
    failure, so the message says so rather than sending the reader after a typo."""
    response = httpx.Response(status, request=httpx.Request("GET", "https://urs.example/x"))
    text = A._explain(response, _item())
    assert "ASF EULA has not been" in text


def test_any_other_status_is_reported_plainly():
    response = httpx.Response(500, request=httpx.Request("GET", "https://urs.example/x"))
    assert A._explain(response, _item()) == "granule.zip: HTTP 500 from urs.example"


# -- download ------------------------------------------------------------------

BODY = b"the granule bytes" * 4
DIGEST = hashlib.sha256(BODY).hexdigest()


def _serve(monkeypatch, *responses: httpx.Response) -> list[dict[str, str]]:
    """Replace the transport `download` builds inline, and record its headers."""
    requests: list[dict[str, str]] = []
    queue = list(responses)

    def fake_resolve(client, url, *, headers, auth, max_hops=20):
        requests.append(dict(headers))
        return queue.pop(0)

    monkeypatch.setattr(A, "_resolve", fake_resolve)
    return requests


def _response(status: int, body: bytes) -> httpx.Response:
    return httpx.Response(
        status, content=body, request=httpx.Request("GET", "https://cloudfront.net/signed")
    )


def test_a_complete_file_of_the_right_size_is_reported_as_cached(tmp_path, monkeypatch):
    _serve(monkeypatch)
    (tmp_path / "granule.zip").write_bytes(BODY)
    path, outcome = A.download(_item(sha256=DIGEST, bytes=len(BODY)), tmp_path, progress=False)
    assert outcome == "cached" and path.read_bytes() == BODY


def test_a_truncated_file_is_not_quietly_treated_as_cached(tmp_path, monkeypatch):
    """A truncated download and a complete one are the same file on disk apart
    from its size, which is why the manifest carries the size."""
    _serve(monkeypatch)
    (tmp_path / "granule.zip").write_bytes(BODY[:5])
    with pytest.raises(A.ArchiveError, match="A truncated download looks exactly like this"):
        A.download(_item(sha256=DIGEST, bytes=len(BODY)), tmp_path, progress=False)


def test_verify_cached_hashes_the_file_and_says_verified(tmp_path, monkeypatch):
    _serve(monkeypatch)
    (tmp_path / "granule.zip").write_bytes(BODY)
    _path, outcome = A.download(
        _item(sha256=DIGEST, bytes=len(BODY)), tmp_path, verify_cached=True, progress=False
    )
    assert outcome == "verified"


def test_verify_cached_refuses_a_file_whose_bytes_are_not_the_manifests(tmp_path, monkeypatch):
    _serve(monkeypatch)
    (tmp_path / "granule.zip").write_bytes(b"different bytes entirely")
    with pytest.raises(A.ArchiveError, match="!= manifest"):
        A.download(
            _item(sha256=DIGEST, bytes=len(b"different bytes entirely")),
            tmp_path,
            verify_cached=True,
            progress=False,
        )


def test_a_fresh_download_verifies_the_hash_and_renames_off_part(tmp_path, monkeypatch):
    requests = _serve(monkeypatch, _response(200, BODY))
    path, outcome = A.download(_item(sha256=DIGEST, bytes=len(BODY)), tmp_path, progress=False)

    assert outcome == "downloaded"
    assert path.read_bytes() == BODY
    assert not (tmp_path / "granule.zip.part").exists()
    assert "Range" not in requests[0]


def test_bytes_that_do_not_match_the_manifest_are_refused_and_kept_for_inspection(
    tmp_path, monkeypatch
):
    """The hash is what ties the download to the evidence every README number was
    measured over, so a mismatch fails loudly rather than seeding the database."""
    _serve(monkeypatch, _response(200, b"something else"))
    with pytest.raises(A.ArchiveError, match="The bytes differ"):
        A.download(_item(sha256=DIGEST, bytes=len(BODY)), tmp_path, progress=False)
    assert (tmp_path / "granule.zip.part").read_bytes() == b"something else"
    assert not (tmp_path / "granule.zip").exists()


def test_a_part_file_is_resumed_with_a_range_request(tmp_path, monkeypatch):
    (tmp_path / "granule.zip.part").write_bytes(BODY[:10])
    requests = _serve(monkeypatch, _response(206, BODY[10:]))

    path, outcome = A.download(_item(sha256=DIGEST, bytes=len(BODY)), tmp_path, progress=False)

    assert outcome == "resumed"
    assert requests[0]["Range"] == "bytes=10-"
    assert path.read_bytes() == BODY  # the digest covered the bytes already on disk


def test_a_server_that_ignores_range_restarts_instead_of_appending(tmp_path, monkeypatch):
    """Appending a whole file to a partial one gives a file of the wrong size and
    the wrong bytes; answering 200 to a Range request means starting over."""
    (tmp_path / "granule.zip.part").write_bytes(BODY[:10])
    _serve(monkeypatch, _response(200, BODY))

    path, outcome = A.download(_item(sha256=DIGEST, bytes=len(BODY)), tmp_path, progress=False)

    assert outcome == "downloaded"
    assert path.read_bytes() == BODY


def test_a_part_file_that_is_already_complete_is_started_over(tmp_path, monkeypatch):
    (tmp_path / "granule.zip.part").write_bytes(BODY + b"extra")
    requests = _serve(monkeypatch, _response(200, BODY))

    _path, outcome = A.download(_item(sha256=DIGEST, bytes=len(BODY)), tmp_path, progress=False)

    assert "Range" not in requests[0]
    assert outcome == "downloaded"


def test_force_discards_a_complete_file_and_its_part(tmp_path, monkeypatch):
    (tmp_path / "granule.zip").write_bytes(BODY)
    (tmp_path / "granule.zip.part").write_bytes(b"stale")
    requests = _serve(monkeypatch, _response(200, BODY))

    _path, outcome = A.download(
        _item(sha256=DIGEST, bytes=len(BODY)), tmp_path, force=True, progress=False
    )
    assert outcome == "downloaded"
    assert "Range" not in requests[0]


def test_a_refusing_server_is_reported_with_the_eula_hint(tmp_path, monkeypatch):
    _serve(monkeypatch, _response(401, b""))
    with pytest.raises(A.ArchiveError, match="ASF EULA"):
        A.download(_item(sha256=DIGEST, bytes=len(BODY)), tmp_path, progress=False)


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(BODY)
    assert A.sha256_file(path) == DIGEST
