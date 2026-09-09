"""Shared fixtures. The only one is the PostGIS connection.

Two rules, both deliberate.

**It never touches the enclave's database.** The DSN is built here from the
environment rather than from `nightglass.config.settings`, and a host of
`postgis` — the compose service name — is refused outright, because the fixture
runs `migrate(drop=True)` and the enclave volume holds the scenes, detections
and AIS positions every proof reads.

**When it is asked for, it either runs or fails.** With
`NIGHTGLASS_TEST_POSTGIS` unset these tests skip, which is what a laptop with no
database does. With it set, a connection failure is an error and not a skip: a
suite that silently skipped in CI would be indistinguishable from one that
passed, and the coverage gate cannot tell the difference either (the whole
database suite is worth under two points).

The environment has to be set *before* pytest starts. `settings` is a
module-level singleton built at import time from `.env` in the working
directory, so a `monkeypatch.setenv` here would come too late for anything that
reads it — which is also why this fixture reads `os.environ` itself.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

ENV_FLAG = "NIGHTGLASS_TEST_POSTGIS"
ENCLAVE_HOST = "postgis"

#: Run once against a fresh database, exactly as the compose service does. The
#: numbered migrations create no extensions, on purpose: that hook fires once on
#: an empty data directory and then never again.
EXTENSIONS = Path(__file__).resolve().parents[1] / "docker" / "postgis" / "initdb" / "01-extensions.sql"


def _dsn() -> str:
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    if host == ENCLAVE_HOST:
        raise RuntimeError(
            f"POSTGRES_HOST={host!r} is the enclave's compose service. These tests run "
            "`migrate(drop=True)` and would destroy the demo database. Point them at a "
            "throwaway container — docs/testing.md has the four lines."
        )
    user = os.environ.get("POSTGRES_USER", "nightglass")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    db = os.environ.get("POSTGRES_DB", "nightglass")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


@pytest.fixture
def postgis(request: pytest.FixtureRequest) -> Iterator[object]:
    """A connection to an empty, migrated schema. Fails rather than skips."""
    if not os.environ.get(ENV_FLAG):
        pytest.skip(f"{ENV_FLAG} is unset; see docs/testing.md to run these against a container")

    import psycopg
    from psycopg.rows import dict_row

    from nightglass.spatial.db import migrate

    dsn = _dsn()
    try:
        conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10)
    except psycopg.Error as exc:  # not a skip: the suite was explicitly asked for
        raise RuntimeError(
            f"{ENV_FLAG} is set but connecting to {dsn.rsplit('@', 1)[-1]} failed: {exc}"
        ) from exc

    with conn:
        with conn.cursor() as cur:
            cur.execute(EXTENSIONS.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        conn.commit()
        migrate(conn, drop=True)
        yield conn
