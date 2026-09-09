"""AIS source adapters over real files: the zip reader, the replay, the slice.

Nothing is faked here — the fixtures are actual zips and CSVs in `tmp_path`,
because every one of these paths is stdlib file handling and a fake would only
prove the fake works. What is pinned is the behaviour the rest of the system
depends on: dedup at the boundary (71% of raw DMA rows are rebroadcast
duplicates), the window and box filters, and that `slice_day` copies rows
**verbatim** — a slice that deduplicated would be a different input silently
pretending to be the same one.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nightglass.config import BBox
from nightglass.spatial import ais as AIS

BOX = BBox.parse("10.5,55.5,12.5,57.5", origin="test")
START = datetime(2026, 7, 17, 5, 12, tzinfo=UTC)
END = datetime(2026, 7, 17, 5, 34, tzinfo=UTC)

HEADER = ["# Timestamp", "Type of mobile", "MMSI", "Latitude", "Longitude"]


def _row(
    stamp: str = "17/07/2026 05:23:00",
    mmsi: str = "257843840",
    lat: str = "56.489370",
    lon: str = "10.951233",
    mobile: str = "Class A",
) -> list[str]:
    row = ["" for _ in range(26)]
    row[0], row[1], row[2], row[3], row[4] = stamp, mobile, mmsi, lat, lon
    row[7], row[8], row[9] = "7.0", "354.6", "180"
    row[12], row[13] = "BORREGUTT", "Sailing"
    row[15], row[16] = "4", "12"
    return row


def _csv(path: Path, rows: list[list[str]], header: bool = True) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        if header:
            writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def _zip(path: Path, members: dict[str, list[list[str]]]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, rows in members.items():
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(HEADER)
            writer.writerows(rows)
            z.writestr(name, buffer.getvalue())
    return path


# -- DMAFileSource -------------------------------------------------------------


def test_a_missing_file_is_a_configuration_error_not_an_empty_feed(tmp_path):
    """"No feed to search" and "searched and found nothing" must never be the
    same answer: the second would report every detection as dark."""
    with pytest.raises(AIS.NotConfigured, match="DMA file not found"):
        AIS.DMAFileSource(tmp_path / "absent.zip")


def test_positions_are_read_out_of_the_zip_without_unpacking_it(tmp_path):
    path = _zip(tmp_path / "day.zip", {"aisdk-2026-07-17.csv": [_row(), _row(mmsi="219000000")]})
    positions = list(AIS.DMAFileSource(path).positions(BOX, START, END))

    assert [p.mmsi for p in positions] == ["257843840", "219000000"]
    assert positions[0].source == "dma"
    assert AIS.DMAFileSource(path).is_ground_truth is True


def test_a_zip_holding_more_than_one_csv_is_refused(tmp_path):
    """Which of two files is the day? Guessing here would silently load half a
    day, and the count would look plausible."""
    path = _zip(tmp_path / "day.zip", {"a.csv": [_row()], "b.csv": [_row()]})
    with pytest.raises(AIS.NotConfigured, match="expected 1 CSV"):
        list(AIS.DMAFileSource(path).positions(BOX, START, END))


def test_a_plain_csv_is_read_the_same_way(tmp_path):
    path = _csv(tmp_path / "slice.csv", [_row()])
    assert len(list(AIS.DMAFileSource(path).positions(BOX, START, END))) == 1


def test_the_hash_header_line_is_not_a_row(tmp_path):
    """The DMA file's first column is literally named `# Timestamp`."""
    path = _csv(tmp_path / "slice.csv", [_row()])
    assert path.read_text().startswith("# Timestamp")
    assert len(list(AIS.DMAFileSource(path).positions(BOX, START, END))) == 1


def test_rebroadcast_duplicates_are_collapsed_at_the_boundary(tmp_path):
    """21 identical copies of one message were measured in the real feed;
    duplicate-weighted nearest-in-time logic is distorted by them."""
    path = _csv(tmp_path / "slice.csv", [_row(), _row(), _row()])
    positions = list(AIS.DMAFileSource(path).positions(BOX, START, END))
    assert len(positions) == 1


def test_a_report_from_the_same_vessel_at_another_instant_is_not_a_duplicate(tmp_path):
    path = _csv(tmp_path / "slice.csv", [_row(), _row(stamp="17/07/2026 05:24:00")])
    assert len(list(AIS.DMAFileSource(path).positions(BOX, START, END))) == 2


def test_rows_outside_the_window_or_the_box_are_dropped(tmp_path):
    rows = [
        _row(stamp="17/07/2026 04:00:00"),                    # before the window
        _row(stamp="17/07/2026 06:00:00", mmsi="2"),          # after it
        _row(lat="60.0", mmsi="3"),                           # north of the box
        _row(lon="4.0", mmsi="4"),                            # west of it
        _row(mmsi="5"),                                       # inside both
    ]
    path = _csv(tmp_path / "slice.csv", rows)
    assert [p.mmsi for p in AIS.DMAFileSource(path).positions(BOX, START, END)] == ["5"]


def test_base_stations_and_navigation_aids_never_become_positions(tmp_path):
    path = _csv(tmp_path / "slice.csv", [_row(mobile="Base Station"), _row(mobile="AtoN", mmsi="2")])
    assert list(AIS.DMAFileSource(path).positions(BOX, START, END)) == []


# -- CustomerFeedSource --------------------------------------------------------

AISSTREAM = "utc,MMSI,lat,lon,sog,cog,name\n"


def _aisstream(path: Path, body: str) -> Path:
    path.write_text(AISSTREAM + body, encoding="utf-8")
    return path


def test_an_unconfigured_replay_says_it_has_no_archive():
    """aisstream is real-time only, so a past acquisition cannot be served from
    it — and saying that is the difference between 'no data' and 'no vessels'."""
    with pytest.raises(AIS.NotConfigured, match="real-time only"):
        list(AIS.CustomerFeedSource(None).positions(BOX, START, END))


def test_a_recorded_capture_is_replayed_with_knots_converted(tmp_path):
    path = _aisstream(
        tmp_path / "cap.csv",
        "2026-07-17 05:23:00 UTC,263000000,56.4,11.0,7.0,354.6,VASCO\n",
    )
    positions = list(AIS.CustomerFeedSource(path).positions(BOX, START, END))

    assert len(positions) == 1
    assert positions[0].mmsi == "263000000"
    assert positions[0].sog_ms == pytest.approx(7.0 * AIS.KNOTS_TO_MS)
    assert positions[0].source == "aisstream"
    assert AIS.CustomerFeedSource(path).is_ground_truth is False


def test_a_lowercase_mmsi_column_is_accepted(tmp_path):
    path = tmp_path / "cap.csv"
    path.write_text(
        "utc,mmsi,lat,lon\n2026-07-17T05:23:00+00:00,263000000,56.4,11.0\n", encoding="utf-8"
    )
    assert [p.mmsi for p in AIS.CustomerFeedSource(path).positions(BOX, START, END)] == [
        "263000000"
    ]


def test_an_unparseable_replay_row_is_skipped_rather_than_ending_the_stream(tmp_path):
    path = _aisstream(
        tmp_path / "cap.csv",
        "not-a-time,1,56.4,11.0,,,\n2026-07-17 05:23:00 UTC,2,not-a-lat,11.0,,,\n"
        "2026-07-17 05:23:00 UTC,3,56.4,11.0,,,\n",
    )
    assert [p.mmsi for p in AIS.CustomerFeedSource(path).positions(BOX, START, END)] == ["3"]


def test_the_replay_filters_and_dedups_like_the_dma_reader(tmp_path):
    path = _aisstream(
        tmp_path / "cap.csv",
        "2026-07-17 05:23:00 UTC,1,56.4,11.0,,,\n"
        "2026-07-17 05:23:00 UTC,1,56.4,11.0,,,\n"   # exact duplicate
        "2026-07-17 04:00:00 UTC,2,56.4,11.0,,,\n"   # outside the window
        "2026-07-17 05:23:00 UTC,3,60.0,11.0,,,\n",  # outside the box
    )
    assert [p.mmsi for p in AIS.CustomerFeedSource(path).positions(BOX, START, END)] == ["1"]


# -- windows and slices --------------------------------------------------------


def test_the_acquisition_window_is_symmetric_around_the_instant():
    centre = datetime(2026, 7, 17, 5, 23, 36, tzinfo=UTC)
    start, end = AIS.acquisition_window(centre, 11.0)
    assert centre - start == timedelta(minutes=11)
    assert end - centre == timedelta(minutes=11)


def test_a_slice_is_named_after_what_is_in_it(tmp_path):
    path = AIS.slice_path(tmp_path, "Kattegat", START, END)
    assert path.name == "ais_kattegat_20260717_0512-0534.csv"


def test_the_slice_is_a_verbatim_cut_including_the_duplicates(tmp_path):
    """Dedup belongs to `DMAFileSource`, which is what reads this file back.
    Deduplicating here would produce a file that is not a cut of the original."""
    rows = [_row(), _row(), _row(stamp="17/07/2026 05:24:00", mmsi="2")]
    day = _zip(tmp_path / "day.zip", {"aisdk.csv": rows})
    out = tmp_path / "slice.csv"

    kept, scanned = AIS.slice_day(day, out, BOX, START, END, progress=False)

    assert (kept, scanned) == (3, 4)  # the header line is scanned, not kept
    written = list(csv.reader(out.open()))
    assert written == rows
    assert not (tmp_path / "slice.part").exists()


def test_the_slice_keeps_lf_line_endings(tmp_path):
    """The DMA file is LF and the slice is a cut of it: `\\r\\n` would make a
    byte-level diff report every row as changed."""
    day = _csv(tmp_path / "day.csv", [_row()])
    out = tmp_path / "slice.csv"
    AIS.slice_day(day, out, BOX, START, END, progress=False)
    assert b"\r\n" not in out.read_bytes()


def test_the_slice_drops_rows_outside_the_window_and_the_box(tmp_path):
    rows = [
        _row(stamp="17/07/2026 04:00:00"),
        _row(stamp="17/07/2026 05:23:00", mmsi="2"),
        _row(lon="4.0", mmsi="3"),
    ]
    day = _csv(tmp_path / "day.csv", rows)
    out = tmp_path / "slice.csv"
    kept, _scanned = AIS.slice_day(day, out, BOX, START, END, progress=False)

    assert kept == 1
    assert [r[2] for r in csv.reader(out.open())] == ["2"]


def test_a_window_crossing_midnight_falls_back_to_parsing_every_row(tmp_path):
    """The lexicographic fast path is only valid inside one day; the fallback has
    to keep the row on the other side of midnight."""
    rows = [
        _row(stamp="17/07/2026 23:58:00", mmsi="1"),
        _row(stamp="18/07/2026 00:02:00", mmsi="2"),
        _row(stamp="18/07/2026 06:00:00", mmsi="3"),
    ]
    day = _csv(tmp_path / "day.csv", rows)
    out = tmp_path / "slice.csv"
    kept, _scanned = AIS.slice_day(
        day,
        out,
        BOX,
        datetime(2026, 7, 17, 23, 50, tzinfo=UTC),
        datetime(2026, 7, 18, 0, 10, tzinfo=UTC),
        progress=False,
    )

    assert kept == 2
    assert [r[2] for r in csv.reader(out.open())] == ["1", "2"]


def test_a_row_the_parser_rejects_is_not_written_to_the_slice(tmp_path):
    rows = [_row(lat="not-a-number", mmsi="1"), _row(mmsi="2")]
    day = _csv(tmp_path / "day.csv", rows)
    out = tmp_path / "slice.csv"
    kept, _scanned = AIS.slice_day(day, out, BOX, START, END, progress=False)
    assert kept == 1


def test_every_source_is_registered_under_the_name_configuration_uses():
    """`AOI_<NAME>_AIS_SOURCE` is the config key, and this dict is what resolves
    it — a name that did not appear here would be a config error at deploy time."""
    assert set(AIS.SOURCES) == {"dma", "gfw", "aisstream"}
    assert AIS.SOURCES["dma"] is AIS.DMAFileSource
