"""`nightglass-spatial` — the thin wrappers, at the points where they decide.

Most of this file's 380 statements are argument plumbing in front of modules
tested elsewhere, so what is pinned here is what the CLI itself owns: the
error paths an operator actually meets, the skip list that keeps a fetch from
silently omitting a manifest entry, and the boundary that prints a handled
failure as a message instead of a traceback.

The backends are replaced at their own module attributes, which is where the
commands look them up — every `cmd_*` imports inside the function body.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nightglass.config import settings
from nightglass.spatial import cli as SCLI

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13.annotation.zip"
)


@pytest.fixture(autouse=True)
def kattegat(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AOI_KATTEGAT_BBOX", "10.5,55.5,12.5,57.5")
    monkeypatch.setenv("AOI_KATTEGAT_AIS_SOURCE", "dma")
    monkeypatch.setattr(settings, "aoi_name", "kattegat")


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *_a: object) -> bool:
        return False


@pytest.fixture
def connect(monkeypatch) -> _Conn:
    conn = _Conn()
    monkeypatch.setattr("nightglass.spatial.db.connect", lambda *a, **k: conn)
    return conn


# -- provisioning --------------------------------------------------------------


def test_fetch_coastline_says_so_when_no_aoi_is_configured(monkeypatch, capsys):
    monkeypatch.setattr(SCLI, "_configured_aoi_names", list)
    rc = SCLI.cmd_fetch_coastline(
        argparse.Namespace(aois=None, out="/tmp/x", resolution="f", force=False,
                           keep_archive=False)
    )
    assert rc == 1
    assert "no AOIs configured" in capsys.readouterr().err


def test_an_unresolvable_aoi_is_skipped_by_name_rather_than_stopping_the_fetch(
    monkeypatch, capsys
):
    fetched: dict[str, Any] = {}
    monkeypatch.setattr(
        "nightglass.spatial.coastline.fetch_and_clip",
        lambda aois, out, **kw: fetched.update(aois=aois, out=out),
    )
    rc = SCLI.cmd_fetch_coastline(
        argparse.Namespace(aois=["kattegat", "atlantis"], out="/tmp/x", resolution="f",
                           force=False, keep_archive=False)
    )
    assert rc == 0
    assert list(fetched["aois"]) == ["kattegat"]
    assert "skipping atlantis" in capsys.readouterr().err


MANIFEST = """
sar:
  licence: Contains modified Copernicus Sentinel data 2026.
  credentials: earthdata
  out: data/raw/sar
  items:
    - name: required.zip
      url: https://asf.test/required.zip
      sha256: aa
      bytes: 1000
      role: required
    - name: superseded.zip
      url: https://asf.test/superseded.zip
      sha256: bb
      bytes: 2000
      role: superseded
      note: replaced by the 05:23 acquisition
"""


def _fetch_args(tmp_path: Path, **kw: Any) -> argparse.Namespace:
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    base = {
        "manifest": str(manifest),
        "role": None,
        "all": False,
        "name": None,
        "force": False,
        "verify": False,
        "out": str(tmp_path / "out"),
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_what_is_not_fetched_is_printed_with_the_reason(tmp_path, monkeypatch, capsys):
    """Nothing is silently omitted: a manifest entry either appears in the fetch
    list or in the skip list, with the role and the note that explain it."""
    monkeypatch.setattr(
        "nightglass.spatial.archive.download",
        lambda item, out, **kw: (Path(out) / item.name, "cached"),
    )
    monkeypatch.setattr(
        "nightglass.spatial.archive.earthdata_credentials", lambda: ("user", "pw")
    )
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "required.zip").write_bytes(b"")

    paths = SCLI._fetch_section(_fetch_args(tmp_path), "sar", str(tmp_path / "out"))

    out = capsys.readouterr().out
    assert [p.name for p in paths] == ["required.zip"]
    assert "not fetched (1," in out
    assert "superseded: replaced by the 05:23 acquisition" in out
    assert "add --all for every entry" in out


def test_the_credential_is_not_resolved_when_every_file_is_already_there(
    tmp_path, monkeypatch, capsys
):
    """Asking for a password to confirm that nothing needs downloading is how a
    cached run fails on a machine with no Earthdata account."""

    def no_credentials():
        raise AssertionError("nothing to download means no credential is needed")

    monkeypatch.setattr("nightglass.spatial.archive.earthdata_credentials", no_credentials)
    monkeypatch.setattr(
        "nightglass.spatial.archive.download",
        lambda item, out, **kw: (Path(out) / item.name, "cached"),
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "required.zip").write_bytes(b"")

    SCLI._fetch_section(_fetch_args(tmp_path), "sar", str(out_dir))
    assert "Earthdata as" not in capsys.readouterr().out


def test_a_selection_that_matches_nothing_stops_rather_than_fetching_everything(tmp_path):
    with pytest.raises(SystemExit, match="nothing selected"):
        SCLI._fetch_section(_fetch_args(tmp_path, name="nonexistent"), "sar", str(tmp_path))


def test_a_download_failure_exits_with_the_archives_own_message(tmp_path, monkeypatch, capsys):
    from nightglass.spatial.archive import ArchiveError

    monkeypatch.setattr("nightglass.spatial.archive.earthdata_credentials", lambda: ("u", "p"))

    def refuse(*_a: Any, **_k: Any):
        raise ArchiveError("accept the ASF EULA at https://urs.earthdata.nasa.gov/profile")

    monkeypatch.setattr("nightglass.spatial.archive.download", refuse)
    with pytest.raises(SystemExit) as exc:
        SCLI._fetch_section(_fetch_args(tmp_path), "sar", str(tmp_path / "out"))

    assert exc.value.code == 1
    assert "accept the ASF EULA" in capsys.readouterr().err


# -- catalogue and query -------------------------------------------------------


def test_scenes_catalogues_a_real_granule_through_the_stac_item(tmp_path, monkeypatch, capsys):
    """End to end over the committed annotation fixture: SafeProduct reads the
    product, `stac_item()` builds the Item, and the catalogue write gets the
    granule path beside it."""
    written: dict[str, Any] = {}
    monkeypatch.setattr("nightglass.spatial.db.connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(
        "nightglass.spatial.db.upsert_scene",
        lambda conn, item, path: written.update(item=item, path=path),
    )

    rc = SCLI.cmd_scenes(argparse.Namespace(granule=[str(FIXTURE)], scene_dir="/nowhere"))
    out = capsys.readouterr().out

    assert rc == 0
    assert written["path"] == str(FIXTURE)
    assert written["item"]["properties"]["nightglass:range_bearing_deg"] == pytest.approx(286.842, abs=0.001)
    assert "2026-07-17 05:23:36 UTC  DESCENDING" in out
    assert "26564×16669" in out


def test_scenes_with_nothing_to_catalogue_names_the_directory_it_looked_in(tmp_path, capsys):
    rc = SCLI.cmd_scenes(argparse.Namespace(granule=[], scene_dir=str(tmp_path)))
    assert rc == 1
    assert f"no granules under {tmp_path}" in capsys.readouterr().err


def test_migrate_reports_what_it_applied(monkeypatch, connect, capsys):
    monkeypatch.setattr(
        "nightglass.spatial.db.migrate",
        lambda conn, drop=False: (["DROP SCHEMA detect, ais, stac"] if drop else [])
        + ["001_schema.sql"],
    )
    assert SCLI.cmd_migrate(argparse.Namespace(drop=True)) == 0
    out = capsys.readouterr().out
    assert "applied  DROP SCHEMA detect, ais, stac" in out
    assert "applied  001_schema.sql" in out


def test_load_ais_refuses_a_scene_that_was_never_catalogued(monkeypatch, connect, capsys):
    """The acquisition instant comes from the catalogue; without it there is no
    window to cut, and guessing one would load the wrong twenty minutes."""
    monkeypatch.setattr("nightglass.spatial.db.scene_row", lambda conn, scene_id: None)
    rc = SCLI.cmd_load_ais(
        argparse.Namespace(file="/x.zip", granule=None, scene_id="S1D_missing", aoi=None,
                           bbox=None, window_min=11.0)
    )
    assert rc == 1
    assert "unknown scene S1D_missing" in capsys.readouterr().err


DARK_ROWS = [
    {
        "detection_id": "scene:0", "lat": 56.0, "lon": 11.0, "length_m": 40.0,
        "confidence": 0.9, "status": "matched", "mmsi": "219000000", "distance_m": 120.0,
        "time_delta_s": -35.0,
    },
    {
        "detection_id": "scene:1", "lat": 56.1, "lon": 11.1, "length_m": None,
        "confidence": None, "status": "dark", "mmsi": None, "distance_m": None,
        "time_delta_s": None,
    },
]


def _dark_args(**kw: Any) -> argparse.Namespace:
    base = {
        "scene_id": "scene", "radius_m": 500.0, "window_min": 11.0,
        "no_azimuth_correction": False, "dark_only": False, "limit": 40, "json": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _dark(monkeypatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_dark_query(conn, **kw):
        seen.update(kw)
        return DARK_ROWS, {"detections": 2, "dark": 1, "rate is quotable": True}

    monkeypatch.setattr("nightglass.spatial.db.dark_query", fake_dark_query)
    return seen


def test_dark_prints_every_detection_with_its_nearest_ais_or_none(monkeypatch, connect, capsys):
    _dark(monkeypatch)
    assert SCLI.cmd_dark(_dark_args()) == 0
    out = capsys.readouterr().out

    assert "219000000  120 m  Δt -35 s" in out
    assert "— none in window" in out
    assert "rate is quotable" in out and "detections                   2" in out


def test_dark_only_shows_the_unmatched_ones(monkeypatch, connect, capsys):
    _dark(monkeypatch)
    SCLI.cmd_dark(_dark_args(dark_only=True))
    out = capsys.readouterr().out
    assert "56.10000" in out  # the dark row
    assert "219000000" not in out


def test_the_no_correction_flag_reaches_the_query(monkeypatch, connect):
    seen = _dark(monkeypatch)
    SCLI.cmd_dark(_dark_args(no_azimuth_correction=True, radius_m=100.0, window_min=5.0))
    assert seen == {
        "scene_id": "scene", "radius_m": 100.0, "window_min": 5.0, "correct_azimuth": False
    }


def test_dark_json_is_machine_readable(monkeypatch, connect, capsys):
    _dark(monkeypatch)
    SCLI.cmd_dark(_dark_args(json=True))
    out = capsys.readouterr().out
    rows = json.loads(out[out.index("[\n") :])  # the rule line also has a [scene] in it
    assert [r["detection_id"] for r in rows] == ["scene:0", "scene:1"]


# -- the comparison ------------------------------------------------------------


def test_gfw_compare_reads_the_reference_and_cites_its_licence(tmp_path, monkeypatch, capsys):
    from nightglass.schemas import Detection
    from nightglass.spatial.gfw import GfwDetection, write_reference

    granule = "S1A_IW_GRDH_1SDV_20260613T064316_20260613T064341_064947_082F4F_F72E"
    path = write_reference(
        [GfwDetection(granule, 11.0, 56.0, True)],
        tmp_path / "gfw_kattegat.json",
        aoi="kattegat",
        bbox=settings.aoi.bbox,
        start=datetime(2026, 6, 13, tzinfo=UTC),
        end=datetime(2026, 6, 14, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "nightglass.tools.detect_vessels",
        lambda scene_id, min_length: [
            Detection(id=f"{scene_id}:0", scene_id=scene_id, lon=11.0, lat=56.0)
        ],
    )

    rc = SCLI.cmd_gfw_compare(
        argparse.Namespace(aoi=None, bbox=None, scene_id=None, reference=str(path),
                           gfw_dir=str(tmp_path), radius_m=500.0, min_length_m=15.0)
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "both saw      1" in out
    assert "CC BY-NC 4.0" in out
    assert "Neither column is ground truth" in out


# -- the boundary --------------------------------------------------------------


def test_a_handled_failure_prints_its_message_and_not_a_traceback(monkeypatch, capsys):
    """Every failure this package raises deliberately is a RuntimeError subclass
    whose message is written for an operator. A traceback above it buries the
    only part the reader needs."""
    from nightglass.spatial.gfw import GfwError

    def refuse(_args):
        raise GfwError("no GFW reference at /app/data/gfw/gfw_kattegat.json. Run `make fetch-gfw`.")

    monkeypatch.setattr(SCLI, "cmd_migrate", refuse)
    monkeypatch.delenv("NIGHTGLASS_TRACEBACK", raising=False)

    assert SCLI.main(["migrate"]) == 1
    err = capsys.readouterr().err
    assert err.strip() == (
        "error: no GFW reference at /app/data/gfw/gfw_kattegat.json. Run `make fetch-gfw`."
    )


def test_the_traceback_comes_back_when_it_is_asked_for(monkeypatch):
    from nightglass.spatial.safe import SafeError

    def refuse(_args):
        raise SafeError("no such granule")

    monkeypatch.setattr(SCLI, "cmd_migrate", refuse)
    monkeypatch.setenv("NIGHTGLASS_TRACEBACK", "1")

    with pytest.raises(SafeError):
        SCLI.main(["migrate"])


def test_an_unexpected_failure_is_not_swallowed(monkeypatch):
    def broken(_args):
        raise KeyError("a bug, not a handled condition")

    monkeypatch.setattr(SCLI, "cmd_migrate", broken)
    with pytest.raises(KeyError):
        SCLI.main(["migrate"])


def test_the_parser_defaults_are_the_documented_ones(monkeypatch):
    seen: dict[str, argparse.Namespace] = {}
    for name in ("cmd_dark", "cmd_detect", "cmd_load_ais", "cmd_scenes"):
        monkeypatch.setattr(SCLI, name, lambda args, _n=name: seen.setdefault(_n, args) and 0)

    SCLI.main(["dark", "scene-1"])
    assert seen["cmd_dark"].radius_m == 500.0  # the validated match tolerance
    assert seen["cmd_dark"].window_min == 11.0
    assert seen["cmd_dark"].limit == 40
    assert seen["cmd_dark"].no_azimuth_correction is False

    SCLI.main(["detect", "/g.zip"])
    assert seen["cmd_detect"].pol == "VH"  # §3.2: VH for detection
    assert seen["cmd_detect"].coast_buffer_m == 1000.0
    assert seen["cmd_detect"].min_length_m is None  # falls back to settings

    SCLI.main(["load-ais", "/day.zip"])
    assert seen["cmd_load_ais"].window_min == 11.0


def test_main_requires_a_subcommand():
    with pytest.raises(SystemExit):
        SCLI.main([])
