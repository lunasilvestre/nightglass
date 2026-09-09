# Testing

Two questions this page answers: what CI actually proves, and what the coverage
number leaves out. Both matter more than the number itself, because the parts of
this system that would be worth doubting — does the detector find ships, is the
azimuth correction's sign right, does the air gap hold — are not the parts a unit
test can settle. Those are answered by the seven `make *-proof` targets, and
five of them need a GPU and licensed data.

## What runs where

| | runs | proves |
|---|---|---|
| `lint` | CI, every push | `ruff check src tests` — the same command `make lint` runs inside the image |
| `test` | CI, every push | the whole suite on Python 3.13 against a real `postgis/postgis:17-3.5`, with a coverage gate |
| `go` | CI, every push | `make bundler-test` — `go vet` and `go test` for the bundler, inside the build container |
| `helm` | CI, every push | `make k8s-lint`, then an assertion over `make k8s-render` that the rendered NetworkPolicy still declares `Egress` and carries no allow-all rule |
| `bundle-proof` | CI, every push | `make bundle-proof` end to end: create, verify, four refusals, restore into an empty daemon |
| `badge` | CI, `main` only | writes the coverage badge's shields JSON to the orphan `badges` branch |

`helm` is a lint and is named as one. Enforcement of the boundary in Kubernetes
is `make k8s-proof`, which streams 2.33 GB of images into a privileged k3s
container; it stays on a host that can afford it.

## The seven proofs, and which one of them CI runs

One of the seven runs in CI: `make bundle-proof`, which needs nothing but a
runner. The other six run on the host. `make k8s-proof` is feasible on a runner
and is not worth those 2.33 GB per push; the remaining five — `air-gap-proof`,
`rag-proof`, `dark-proof`, `tool-proof`, `agent-proof` — need a 24 GB card, the
1.0 GB Kattegat granule and a licensed Danish AIS day, none of which a runner
has.

That is not a reason to take them on trust: the dated output of every one of the
seven, with the host, the card and the exact command, is committed in
[evidence/proofs.md](evidence/proofs.md). It is recorded real output, not a
mock — this project has no mock standing in for a proof anywhere, and it is not
going to grow one to make a badge greener.

## Running it locally

```
make test          # the suite in nightglass/app:test, --network none
make lint          # ruff, same image
```

`make test` mounts `tests/` read-only into the image and runs `pytest -q` with
no network at all. The suite needs no network, no database and no models, so
that is a claim rather than a convenience: anything that broke it would be a
test that had grown a dependency.

One test is skipped in the image and not on a developer host: the PDF
extraction success path shells out to `pdftotext`, which the `fetcher` stage
installs and the enclave runtime deliberately does not. The refusal
`rag/extract.py` raises without poppler is asserted everywhere; only the success
path is conditional. CI installs `poppler-utils` so its coverage figure is
comparable with the host's.

### The database tests

They are marked `postgis` and are skipped unless `NIGHTGLASS_TEST_POSTGIS` is
set. Against a throwaway container, from the repository root:

```
docker run --rm -d --name ng-test-postgis \
  -e POSTGRES_PASSWORD=test -e POSTGRES_USER=nightglass -e POSTGRES_DB=nightglass \
  -p 127.0.0.1:55432:5432 postgis/postgis:17-3.5

NIGHTGLASS_TEST_POSTGIS=1 POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 \
  POSTGRES_PASSWORD=test .venv/bin/python -m pytest -m postgis

docker rm -f ng-test-postgis
```

Three things about that invocation are deliberate.

**The environment goes in front of `pytest`, not inside it.** `settings` is a
module-level singleton built at import time from `.env` in the working
directory, so a `monkeypatch.setenv` after that would be ignored. The fixture
reads `os.environ` itself for the same reason.

**Never the enclave's database.** The fixture runs `migrate(conn, drop=True)`,
which drops the three schemas. It refuses outright to run against
`POSTGRES_HOST=postgis`, the compose service name, and there is no `make test-db`
target precisely so that nobody can point one at the running enclave by
accident. The enclave's PostGIS publishes no host port anyway — its network is
`internal: true` — but a check beats a coincidence.

**Set means run.** With `NIGHTGLASS_TEST_POSTGIS` set, a connection failure is an
error and not a skip. CI additionally runs `pytest -m postgis` as its own step
and fails if it collects nothing or skips anything, because the coverage gate
cannot tell a passing database suite from an absent one: the whole suite is
worth under two points, which is less than the gate's own margin.

## The coverage number, and the gate

453 tests, **72.42 % line coverage** over 4,322 statements, measured on
2026-09-09 on the host with the database suite running. The gate in CI is
`--cov-fail-under=70`: the measured figure minus two, as an integer, not rounded
up. Two points is enough slack that an unrelated refactor does not fail the
build, and small enough that deleting a test file would.

The gate is a floor with a date on it, not a target. Raise it only against a
figure a run actually produced.

## What the other 28 % is

Nothing in the list below is untested because it was awkward. Each part is
answered by something that is not a unit test, and the second column says by
what.

| uncovered | statements | what answers it instead |
|---|---|---|
| the pixel path — `spatial/detect.py`, `render.py`, `plots.py`, `validate.py`, `coastline.py` | 670 | `make dark-proof` over the real granule, `make validate-shift` against DMA ground truth, and the committed renders in `docs/evidence/` — a green suite over a coastline full of phantom vessels is exactly the failure this project already had |
| the live-database tool bodies — `tools/spatial.py`, `tools/intrep.py`, `tools/base.py` | 240 | `make tool-proof`, `make dark-proof`, `make agent-proof`, each against a catalogued scene with AIS loaded |
| command wiring that opens a database or reads a granule — `spatial/cli.py`, `rag/cli.py` | 153 | `make demo`, and the proofs, which drive those commands as an operator does |
| PDF extraction internals — `rag/extract.py` | 22 | `make fetch-corpus` over the real publishers, then `make rag-proof` |
| the entrypoint's config report — `config.py` | 17 | `make config` against the running container |

Pixel-level detection is deliberately out of scope for the unit suite, and the
tests say so in their own docstrings. The fixture in `tests/fixtures/` is the
Kattegat granule's manifest and VH annotation XML — 812 KB of real Copernicus
data — so `SafeProduct` is tested against the product's own acquisition
geometry, incidence grid, calibration and noise LUTs. The 886 MB measurement
TIFF is not there and no test opens it.

## The SQL tests are a regression guard, not a validation

`tests/test_postgis.py` runs `dark_vessels.sql` against a real PostGIS on
hand-built rows: a stationary vessel at the detection, a moving one placed where
the radar would have drawn it, a one-sided bracket, a vessel with no course
reported, one just outside the pre-filter envelope. Every expected number is
computed in the test from the same formula the SQL uses, with the formula
written out.

So what those tests prove is that the query implements that formula over
PostGIS's geography type. They do not prove the formula is right, and in
particular they cannot prove its *sign* is right — the SQL's own comment records
that the sign was "derived one way, measured the other, measurement won". The
claim is `make validate-shift`, which measures the correction against DMA ground
truth over the Danish validation scene. If those tests ever look like they
re-derive the physics, they are being read wrong.

## Style

The existing suite fakes nothing it can avoid faking, and that is worth keeping.
Where something is replaced it is replaced at a real seam — httpx's own
`MockTransport`, Qdrant's in-process local mode, langgraph's `MemorySaver`, a
throwaway PostGIS — and the assertion is about the decision the code makes, not
about what the replacement returned. A test that asserts a fake's own return
value is a defect, and it is what a reviewer should look for first.
