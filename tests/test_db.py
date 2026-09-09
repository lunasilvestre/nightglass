"""The Python side of the PostGIS layer, and the `nightglass-spatial` helpers.

Every function in `spatial/db.py` takes its connection as an explicit parameter,
so the statement sequence, the COPY rows and the summary arithmetic can be
checked without a server. Whether the SQL itself is correct is a different
question and a fake cursor cannot answer it — that is what `tests/test_postgis.py`
is for, and it needs a real database.

What is worth pinning here is the arithmetic with a documented prior bug in it:
`dark_query`'s ground-truth aggregation reads `source_is_ground_truth` off every
row, and the SQL COALESCEs that column to false on unmatched ones. Aggregating
over all rows instead of the matched ones would make one dark detection enough to
disqualify a Danish feed.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest

from nightglass.config import settings
from nightglass.schemas import Detection
from nightglass.spatial import cli as SCLI
from nightglass.spatial import db as DB


class FakeCursor:
    """Records statements; answers `fetchone`/`fetchall` from a script."""

    def __init__(self, answers: list[Any] | None = None) -> None:
        self.statements: list[tuple[str, Any]] = []
        self.answers = list(answers or [])
        self.copied: list[tuple[str, list[tuple]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def execute(self, sql: Any, params: Any = None) -> None:
        self.statements.append((str(sql), params))

    def fetchone(self) -> Any:
        return self.answers.pop(0)

    def fetchall(self) -> Any:
        return self.answers.pop(0)

    def copy(self, sql: str) -> Any:
        rows: list[tuple] = []
        self.copied.append((sql, rows))

        class _Copy:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_a):
                return False

            def write_row(self_inner, row: tuple) -> None:
                rows.append(row)

        return _Copy()


class FakeConn:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1


def _rows(**kw: Any) -> dict[str, Any]:
    row = {
        "detection_id": "s1:0",
        "status": "matched",
        "source_is_ground_truth": True,
        "distance_m": 100.0,
        "distance_uncorrected_m": 400.0,
        "mmsi": "219000000",
    }
    row.update(kw)
    return row


DARK_ROW = _rows(
    detection_id="s1:9",
    status="dark",
    mmsi=None,
    source_is_ground_truth=False,  # COALESCE(false) on an unmatched row
    distance_m=None,
    distance_uncorrected_m=None,
)


# -- migrate -------------------------------------------------------------------


def test_migrations_are_applied_in_filename_order(monkeypatch):
    cursor = FakeCursor()
    applied = DB.migrate(FakeConn(cursor), drop=False)

    assert applied == sorted(applied)
    assert applied[0] == "001_schema.sql"
    assert "CREATE TABLE IF NOT EXISTS stac.scenes" in cursor.statements[0][0]


def test_drop_removes_the_three_schemas_before_recreating_them():
    """`drop` is not a down-migration and does not pretend to be one: the scenes
    and detections are recomputable from the granules on disk."""
    cursor = FakeCursor()
    applied = DB.migrate(FakeConn(cursor), drop=True)

    assert applied[0] == "DROP SCHEMA detect, ais, stac"
    assert [s for s, _p in cursor.statements[:3]] == [
        "DROP SCHEMA IF EXISTS detect CASCADE",
        "DROP SCHEMA IF EXISTS ais CASCADE",
        "DROP SCHEMA IF EXISTS stac CASCADE",
    ]


def test_the_shipped_sql_is_read_from_the_package_not_the_working_directory():
    text = DB.sql("dark_vessels.sql")
    assert "%(scene_id)s" in text and "%(radius_m)s" in text
    assert "ST_Project" in text


# -- dark_query ----------------------------------------------------------------


def test_the_summary_counts_both_halves_and_the_fraction_between_them():
    """§3.2's sanity check: published work finds ~5% of detections unmatched over
    Danish waters, so the fraction is computed and shown every time."""
    cursor = FakeCursor([[_rows(), _rows(detection_id="s1:1"), DARK_ROW, DARK_ROW]])
    _rows_out, summary = DB.dark_query(FakeConn(cursor), scene_id="s1")

    assert summary["detections"] == 4
    assert summary["matched"] == 2 and summary["dark"] == 2
    assert summary["unmatched_fraction"] == "50.0%"
    assert summary["azimuth correction helped"] == "2/2"


def test_an_unmatched_row_does_not_disqualify_a_ground_truth_feed():
    """The prior bug: the SQL COALESCEs `source_is_ground_truth` to false on
    unmatched rows, so aggregating over every row would let one dark detection
    turn a Danish feed into a non-quotable one."""
    cursor = FakeCursor([[_rows(), DARK_ROW]])
    _rows_out, summary = DB.dark_query(FakeConn(cursor), scene_id="s1")
    assert summary["rate is quotable"] is True


def test_a_thinned_feed_gets_an_explanation_rather_than_a_bare_false():
    cursor = FakeCursor([[_rows(source_is_ground_truth=False), DARK_ROW]])
    _rows_out, summary = DB.dark_query(FakeConn(cursor), scene_id="s1")

    assert summary["rate is quotable"] is not True  # it is a sentence, not a bool
    assert "never a dark rate" in summary["rate is quotable"]


def test_a_scene_with_no_matches_at_all_is_not_quotable():
    cursor = FakeCursor([[DARK_ROW]])
    _rows_out, summary = DB.dark_query(FakeConn(cursor), scene_id="s1")

    assert summary["matched"] == 0
    assert summary["azimuth correction helped"] == "n/a"
    assert "never a dark rate" in summary["rate is quotable"]


def test_a_scene_with_no_detections_reports_no_fraction_rather_than_zero():
    cursor = FakeCursor([[]])
    _rows_out, summary = DB.dark_query(FakeConn(cursor), scene_id="s1")
    assert summary["unmatched_fraction"] == "n/a"


def test_a_correction_that_did_not_help_is_not_counted_as_having_helped():
    cursor = FakeCursor([[_rows(distance_m=400.0, distance_uncorrected_m=100.0), _rows()]])
    _rows_out, summary = DB.dark_query(FakeConn(cursor), scene_id="s1")
    assert summary["azimuth correction helped"] == "1/2"


def test_the_tolerance_defaults_to_the_configured_one_and_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "match_radius_m", 500.0)
    monkeypatch.setattr(settings, "match_window_min", 11.0)
    cursor = FakeCursor([[_rows()]])
    _rows_out, summary = DB.dark_query(FakeConn(cursor), scene_id="s1")

    assert cursor.statements[0][1] == {
        "scene_id": "s1",
        "radius_m": 500.0,
        "window_s": 660.0,
        "correct_azimuth": True,
    }
    assert summary["match radius (m)"] == 500.0
    assert summary["time window (min)"] == "±11"
    assert summary["azimuth correction"] == "applied"


def test_turning_the_correction_off_is_visible_in_the_summary():
    """`--no-azimuth-correction` exists to show the difference, so a reader must
    never mistake an uncorrected run for a corrected one."""
    cursor = FakeCursor([[_rows()]])
    _rows_out, summary = DB.dark_query(
        FakeConn(cursor), scene_id="s1", radius_m=100.0, window_min=5.0, correct_azimuth=False
    )
    assert summary["azimuth correction"] == "OFF"
    assert cursor.statements[0][1]["correct_azimuth"] is False
    assert cursor.statements[0][1]["window_s"] == 300.0


# -- writes --------------------------------------------------------------------


class _Run:
    scene_id = "s1"
    detector = "nightglass-cfar"
    version = "0.3.1"
    polarization = "VH"
    started_at = datetime(2026, 7, 17, 6, tzinfo=UTC)
    seconds = 14.0
    aoi_bbox: ClassVar[list[float]] = [10.5, 55.5, 12.5, 57.5]
    coastline = "gshhg-h-L1"
    parameters: ClassVar[dict[str, Any]] = {"threshold_db": 6.0}
    pixels_examined = 10
    pixels_water = 9
    pixels_land_masked = 1
    candidates = 3
    detections = 2
    rejected_small = 1
    rejected_large = 0
    rejected_outside_aoi = 0
    rejected_on_coastline = 0
    water_sigma0_db = -22.0
    nesz_db = -24.0


class _Measurement:
    def __init__(self, detection_id: str) -> None:
        self.detection_id = detection_id
        self.width_m, self.row, self.col = 12.0, 100.0, 200.0
        self.incidence_deg, self.r_over_v_s, self.cfar_margin_db = 38.7, 115.3, 7.5


def test_a_rerun_replaces_the_previous_run_rather_than_accumulating():
    """Two runs of the same detector over the same granule are one observation
    computed twice; keeping both would double every count downstream."""
    cursor = FakeCursor([{"id": 7}])
    detections = [Detection(id="s1:0", scene_id="s1", lon=11.0, lat=56.0, length_m=40.0)]
    conn = FakeConn(cursor)

    n = DB.insert_detections(conn, _Run(), detections, [_Measurement("s1:0")])

    assert n == 1
    assert cursor.statements[0][0].startswith("DELETE FROM detect.runs")
    assert cursor.statements[0][1] == ("s1", "nightglass-cfar", "VH")
    assert "INSERT INTO detect.runs" in cursor.statements[1][0]
    assert conn.commits == 1


def test_a_detection_with_no_measurement_is_written_with_null_geometry_fields():
    """The measurement carries the pixel and beam geometry; a detection without
    one is still a detection and must not crash the load."""
    cursor = FakeCursor([{"id": 7}])
    detections = [
        Detection(id="s1:0", scene_id="s1", lon=11.0, lat=56.0, length_m=40.0),
        Detection(id="s1:1", scene_id="s1", lon=11.1, lat=56.1),
    ]
    DB.insert_detections(FakeConn(cursor), _Run(), detections, [_Measurement("s1:0")])

    _sql, rows = cursor.copied[0]
    assert rows[0][3] == "SRID=4326;POINT(11.0 56.0)"
    assert rows[0][10] == 38.7  # incidence, from the measurement
    assert rows[1][10] is None  # and absent for the one with none
    assert rows[1][8] is None and rows[1][9] is None


class _Position:
    def __init__(self, mmsi: str) -> None:
        self.mmsi, self.timestamp = mmsi, datetime(2026, 7, 17, 5, 23, tzinfo=UTC)
        self.lat, self.lon = 56.0, 11.0
        self.sog_ms = self.cog_deg = self.heading_deg = None
        self.name = self.ship_type = self.nav_status = None
        self.length_m = self.width_m = None
        self.source = "dma"


class _Source:
    is_ground_truth = True

    def __init__(self, n: int) -> None:
        self.n = n

    def positions(self, _bbox: Any, _start: Any, _end: Any):
        for i in range(self.n):
            yield _Position(str(i))


def test_positions_are_copied_in_batches_through_a_staging_table():
    """`ON CONFLICT DO NOTHING` cannot be used with COPY, so the load goes through
    an UNLOGGED staging table and one INSERT ... SELECT DISTINCT — the database
    enforcing the dedup rule rather than trusting the loader."""
    cursor = FakeCursor([{"n": 5}])
    conn = FakeConn(cursor)

    total, vessels = DB.insert_positions(
        conn,
        _Source(5),
        None,
        datetime(2026, 7, 17, tzinfo=UTC),
        datetime(2026, 7, 18, tzinfo=UTC),
        batch=2,
    )

    assert (total, vessels) == (5, 5)
    assert [len(rows) for _sql, rows in cursor.copied] == [2, 2, 1]
    statements = [s for s, _p in cursor.statements]
    assert "CREATE UNLOGGED TABLE IF NOT EXISTS ais._staging" in statements[0]
    assert statements[1] == "TRUNCATE ais._staging"
    assert "ON CONFLICT (mmsi, ts, lat, lon) DO NOTHING" in statements[2]
    assert statements[-1] == "DROP TABLE ais._staging"


def test_the_sources_ground_truth_flag_travels_onto_every_row():
    """`Match.source_is_ground_truth` and `rate_is_quotable` are computed from
    this column, so it is written per row rather than inferred later."""
    cursor = FakeCursor([{"n": 1}])
    DB.insert_positions(
        FakeConn(cursor),
        _Source(1),
        None,
        datetime(2026, 7, 17, tzinfo=UTC),
        datetime(2026, 7, 18, tzinfo=UTC),
    )
    _sql, rows = cursor.copied[0]
    assert rows[0][-1] is True
    assert rows[0][4] == "SRID=4326;POINT(11.0 56.0)"


def test_an_empty_feed_writes_nothing_but_still_cleans_up():
    cursor = FakeCursor([{"n": 0}])
    total, vessels = DB.insert_positions(
        FakeConn(cursor),
        _Source(0),
        None,
        datetime(2026, 7, 17, tzinfo=UTC),
        datetime(2026, 7, 18, tzinfo=UTC),
    )
    assert (total, vessels) == (0, 0)
    assert cursor.copied == []
    assert [s for s, _p in cursor.statements][-1] == "DROP TABLE ais._staging"


def test_counts_labels_map_one_to_one_onto_the_tables_queried():
    cursor = FakeCursor([{"n": 3}, {"n": 4}, {"n": 143}, {"n": 38925}])
    out = DB.counts(FakeConn(cursor))

    assert out == {"scenes": 3, "runs": 4, "detections": 143, "ais positions": 38925}
    tables = [s.split("FROM ")[1] for s, _p in cursor.statements]
    assert tables == ["stac.scenes", "detect.runs", "detect.detections", "ais.positions"]


def test_iter_rows_passes_the_parameters_through():
    cursor = FakeCursor([[{"n": 1}]])
    assert DB.iter_rows(FakeConn(cursor), "SELECT %s", ("x",)) == [{"n": 1}]
    assert cursor.statements == [("SELECT %s", ("x",))]


# -- the spatial CLI's helpers -------------------------------------------------


def test_an_explicit_bbox_beats_the_configured_aoi(monkeypatch):
    monkeypatch.setenv("AOI_KATTEGAT_BBOX", "10.5,55.5,12.5,57.5")
    monkeypatch.setenv("AOI_KATTEGAT_AIS_SOURCE", "dma")
    monkeypatch.setattr(settings, "aoi_name", "kattegat")

    name, bbox = SCLI._aoi_bbox(argparse.Namespace(bbox="1,2,3,4", aoi=None))
    assert (name, bbox.as_list()) == ("custom", [1.0, 2.0, 3.0, 4.0])

    name, bbox = SCLI._aoi_bbox(argparse.Namespace(bbox=None, aoi=None))
    assert (name, bbox.as_list()) == ("kattegat", [10.5, 55.5, 12.5, 57.5])


def test_a_named_aoi_overrides_the_configured_default(monkeypatch):
    monkeypatch.setenv("AOI_KATTEGAT_BBOX", "10.5,55.5,12.5,57.5")
    monkeypatch.setenv("AOI_KATTEGAT_AIS_SOURCE", "dma")
    monkeypatch.setenv("AOI_LISBON_BBOX", "-10.5,38.0,-8.5,39.5")
    monkeypatch.setenv("AOI_LISBON_AIS_SOURCE", "aisstream")
    monkeypatch.setattr(settings, "aoi_name", "kattegat")

    name, _bbox = SCLI._aoi_bbox(argparse.Namespace(bbox=None, aoi="lisbon"))
    assert name == "lisbon"


def test_configured_aoi_names_are_discovered_from_the_environment(monkeypatch):
    monkeypatch.setenv("AOI_KATTEGAT_BBOX", "10.5,55.5,12.5,57.5")
    monkeypatch.setenv("AOI_LISBON_BBOX", "-10.5,38.0,-8.5,39.5")
    monkeypatch.setenv("AOI_EMPTY_BBOX", "   ")
    names = SCLI._configured_aoi_names()

    assert "kattegat" in names and "lisbon" in names
    assert "empty" not in names  # a blank value is not a configured AOI
    assert names == sorted(names)


def test_roles_default_to_required_only():
    """A clone that fetches only the required role can run every proof and the
    demo, which is why that is the default rather than everything."""
    from nightglass.spatial.archive import ROLES

    assert SCLI._roles(argparse.Namespace(all=False, role=None)) == ("required",)
    assert SCLI._roles(argparse.Namespace(all=False, role=["optional"])) == ("optional",)
    assert SCLI._roles(argparse.Namespace(all=True, role=None)) == ROLES


def test_a_day_without_a_timezone_is_read_as_utc():
    assert SCLI._parse_day("2026-07-17") == datetime(2026, 7, 17, tzinfo=UTC)
    assert SCLI._parse_day("2026-07-17T05:23:00+02:00").utcoffset().total_seconds() == 7200


class _Gfw:
    def __init__(self, granule_id: str) -> None:
        self.granule_id = granule_id


def test_the_granule_compared_against_is_the_one_the_reference_covers(capsys):
    """An AOI clips several granules from one pass; comparing against the corner
    one would report a detector that misses almost everything."""
    theirs = [_Gfw("A")] * 66 + [_Gfw("B")] * 3
    chosen = SCLI._pick_granule({}, theirs)
    out = capsys.readouterr().out

    assert chosen == "A"
    assert "comparing over A (66 GFW detections)" in out
    assert "not compared: B (3)" in out
    assert "--scene-id" in out


def test_one_granule_in_the_reference_is_chosen_without_the_alternatives_note(capsys):
    assert SCLI._pick_granule({}, [_Gfw("A")]) == "A"
    assert capsys.readouterr().out == ""


def test_an_empty_reference_says_to_fetch_it():
    with pytest.raises(SystemExit, match="make fetch-gfw"):
        SCLI._pick_granule({}, [])
