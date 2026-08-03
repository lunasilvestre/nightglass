"""Unit tests for the §5 tool layer (M4).

Like the rest of the suite these run with `--network none` and no database, so
they cover the parts that are pure functions of their inputs. For M4 that is a
better fit than it sounds, because the two things M4 actually adds are decisions
rather than computations, and both are checkable without a granule:

* the run-reuse predicate — when is a recorded detector run *identical* to the
  one a fresh call would perform;
* the rate guard — when may a proportion of unmatched detections be stated, and
  what happens to a generated claim that states one anyway.

The second is the one worth having. A prompt instruction not to quote a rate is
a request; `scrub_rate_claims` is a check, and a check is only worth trusting if
its failure modes are in a test rather than in production. The interesting case
is deliberately included: ground-truth AIS over Denmark, where the *source* side
of the guard passes and the answer is still "no", because the detector's coastal
precision is unvalidated. If those two ever collapse into one condition, the
Danish case is what fails here first.

What these do NOT cover is whether the tools return the right rows — that needs
PostGIS and a granule, and it is what `make tool-proof` does.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from nightglass.config import BBox
from nightglass.schemas import Claim, CorrelationResult, Detection, Match, Scene
from nightglass.tools.intrep import (
    DETECTOR_PRECISION_VALIDATED,
    rate_verdict,
    scrub_rate_claims,
    states_a_rate,
)
from nightglass.tools.spatial import (
    _as_bbox,
    _intersects,
    _rank,
    _same_bbox,
    _same_parameters,
    _within,
)

# -- run reuse ---------------------------------------------------------------


def _params(**overrides):
    from nightglass.spatial.detect import DetectorConfig

    return asdict(DetectorConfig(**overrides))


def test_identical_parameters_are_reusable():
    assert _same_parameters(_params(), _params())


def test_a_stored_run_at_a_smaller_threshold_is_reusable():
    # The whole basis of decision 1: `min_length_m` is applied by `_finalise`
    # after detection and after measurement, so the 15 m run's detections at
    # >= 30 m are exactly the 30 m run's detections.
    assert _same_parameters(_params(min_length_m=15.0), _params(min_length_m=30.0))


def test_a_stored_run_at_a_larger_threshold_is_not_reusable():
    # The other direction is not a filter, it is missing data.
    assert not _same_parameters(_params(min_length_m=30.0), _params(min_length_m=15.0))


def test_a_different_cfar_threshold_is_not_reusable():
    assert not _same_parameters(_params(k=6.0), _params(k=8.0))


def test_a_different_land_buffer_is_not_reusable():
    # Coastal masking is the single biggest lever on the unmatched count, so a
    # run masked differently is a different measurement, not a cached one.
    assert not _same_parameters(_params(land_dilate_blocks=4), _params())


def test_a_run_missing_a_parameter_is_not_reusable():
    # Recorded by an older build of DetectorConfig. Treating an absent key as
    # agreement is how a cache starts silently changing results.
    stored = _params()
    stored.pop("censor")
    assert not _same_parameters(stored, _params())


def test_a_run_with_an_extra_parameter_is_not_reusable():
    stored = _params() | {"speckle_filter": "lee"}
    assert not _same_parameters(stored, _params())


def test_bbox_identity_is_exact():
    box = BBox(10.5, 55.5, 12.5, 57.5)
    assert _same_bbox([10.5, 55.5, 12.5, 57.5], box)
    assert not _same_bbox([10.5, 55.5, 12.5, 57.4], box)
    assert not _same_bbox([10.5, 55.5, 12.5], box)
    assert not _same_bbox(None, box)


# -- geometry ----------------------------------------------------------------


def test_bbox_from_a_list_rejects_the_wrong_arity():
    from nightglass.tools.base import ToolError

    assert _as_bbox([10.5, 55.5, 12.5, 57.5]).max_lat == 57.5
    with pytest.raises(ToolError):
        _as_bbox([10.5, 55.5, 12.5])


def test_intersects_is_symmetric_and_rejects_disjoint_boxes():
    kattegat = BBox(10.5, 55.5, 12.5, 57.5)
    lisbon = BBox(-10.5, 38.0, -8.5, 39.5)
    assert _intersects(kattegat, kattegat)
    assert not _intersects(kattegat, lisbon)
    assert not _intersects(lisbon, kattegat)
    assert _intersects(BBox(11.0, 56.0, 13.0, 58.0), kattegat)


def test_detections_outside_the_requested_box_are_filtered():
    box = BBox(10.5, 55.5, 12.5, 57.5)
    inside = Detection(id="a", scene_id="s", lon=11.0, lat=56.0)
    outside = Detection(id="b", scene_id="s", lon=13.0, lat=56.0)
    assert _within(inside, box)
    assert not _within(outside, box)


def test_scene_ranking_prefers_coverage_over_recency():
    """The rule that replaced 'take the newest'.

    Consecutive granules from one pass both clip an AOI — which is the normal
    case, not an edge one — and the newer of the two can see less of it. Ranking
    by recency picks the wrong scene without ever looking wrong.
    """
    aoi = BBox(10.0, 55.0, 12.0, 57.0)
    wide = {
        "id": "wide",
        "acquisition_time": datetime(2026, 7, 17, 5, 23, tzinfo=UTC),
        "footprint_wkt": "POLYGON((10 55, 12 55, 12 57, 10 57, 10 55))",
    }
    sliver = {
        "id": "sliver-but-newer",
        "acquisition_time": datetime(2026, 7, 17, 5, 24, tzinfo=UTC),
        "footprint_wkt": "POLYGON((11.9 55, 12 55, 12 57, 11.9 57, 11.9 55))",
    }
    assert [r["id"] for r in _rank([sliver, wide], aoi)] == ["wide", "sliver-but-newer"]


def test_scene_ranking_falls_back_to_recency_on_a_tie():
    aoi = BBox(10.0, 55.0, 12.0, 57.0)
    poly = "POLYGON((10 55, 12 55, 12 57, 10 57, 10 55))"
    older = {
        "id": "older",
        "acquisition_time": datetime(2026, 7, 17, 5, 23, tzinfo=UTC),
        "footprint_wkt": poly,
    }
    newer = {
        "id": "newer",
        "acquisition_time": datetime(2026, 7, 17, 5, 24, tzinfo=UTC),
        "footprint_wkt": poly,
    }
    assert [r["id"] for r in _rank([older, newer], aoi)] == ["newer", "older"]


# -- the rate guard ----------------------------------------------------------


def _correlation(*, ground_truth: bool, n_matched: int = 3, n_dark: int = 1):
    matches = [
        Match(
            detection_id=f"s:det_{i:05d}",
            mmsi=f"2190000{i}",
            distance_m=100.0,
            status="matched",
            ais_source="dma" if ground_truth else "aisstream",
            source_is_ground_truth=ground_truth,
        )
        for i in range(n_matched)
    ] + [
        Match(
            detection_id=f"s:det_{n_matched + i:05d}",
            status="dark",
            ais_source="dma" if ground_truth else "aisstream",
            source_is_ground_truth=ground_truth,
        )
        for i in range(n_dark)
    ]
    return CorrelationResult(
        aoi_name="kattegat",
        bbox=[10.5, 55.5, 12.5, 57.5],
        start=datetime(2026, 7, 17, tzinfo=UTC),
        end=datetime(2026, 7, 18, tzinfo=UTC),
        scenes=[
            Scene(
                id="s",
                acquisition_time=datetime(2026, 7, 17, 5, 23, tzinfo=UTC),
                mode="IW",
                polarizations=["VH", "VV"],
                footprint_wkt="POLYGON((10 55, 12 55, 12 57, 10 57, 10 55))",
            )
        ],
        detections=[
            Detection(id=m.detection_id, scene_id="s", lon=11.0, lat=56.0, length_m=90.0)
            for m in matches
        ],
        matches=matches,
    )


def test_a_dark_row_does_not_disqualify_a_ground_truth_feed():
    """The bug this test exists to keep fixed.

    `dark_vessels.sql` returns COALESCE(is_ground_truth, false), which is false
    for every unmatched detection simply because there is no matched vessel to
    read the flag from. Copying that onto the Match makes `rate_is_quotable`
    false exactly when a dark detection exists — i.e. whenever the question is
    worth asking — and the guard reads as working while being stuck off.
    """
    assert _correlation(ground_truth=True, n_dark=5).rate_is_quotable


def test_ground_truth_ais_is_still_not_enough_to_quote_a_rate():
    # The case worth having. Denmark: the source side passes, and the answer is
    # still no. If the two conditions are ever collapsed into one, this fails.
    c = _correlation(ground_truth=True)
    assert c.rate_is_quotable, "precondition: the source side must pass"
    verdict = rate_verdict(c)
    assert not verdict.quotable
    assert any("precision" in r.lower() for r in verdict.reasons)


def test_a_thinned_feed_fails_for_the_other_reason_too():
    verdict = rate_verdict(_correlation(ground_truth=False))
    assert not verdict.quotable
    assert len(verdict.reasons) == 2  # source side AND precision side


def test_an_empty_correlation_is_not_quotable():
    empty = CorrelationResult(
        aoi_name="kattegat",
        bbox=[10.5, 55.5, 12.5, 57.5],
        start=datetime(2026, 7, 17, tzinfo=UTC),
        end=datetime(2026, 7, 18, tzinfo=UTC),
    )
    assert not rate_verdict(empty).quotable


def test_the_precision_flag_is_off():
    # Flipping this is a claim about measurement, so it should only ever change
    # in the same commit as the measurement. This test is the tripwire.
    assert DETECTOR_PRECISION_VALIDATED is False


def test_reasons_are_localised():
    pt = rate_verdict(_correlation(ground_truth=False), language="pt")
    assert any("ground truth" in r for r in pt.reasons)
    assert any("taxa de base publicada" in r for r in pt.reasons)


@pytest.mark.parametrize(
    "text",
    [
        "25% of detections had no AIS correspondence.",
        "The dark-vessel rate was one in four.",
        "Unmatched detections accounted for 25 per cent of the total.",
        "25% das deteções não têm correspondência AIS.",
        "A taxa de embarcações escuras foi de 25%.",
        "A fração sem AIS é significativa.",
        "The proportion of vessels that were dark is notable.",
        # The complement states the same number and must not walk through.
        "75% of detections were matched to a vessel reporting AIS.",
        "75% das deteções têm correspondência AIS.",
    ],
)
def test_rate_shaped_claims_are_caught(text):
    assert states_a_rate(text)


@pytest.mark.parametrize(
    "text",
    [
        "15 of 60 detections had no AIS correspondence.",
        "45 detections matched a vessel reporting AIS, median separation 119 m.",
        "15 deteções não têm correspondência AIS na fonte consultada.",
        "Detection det_00005 at 56.55897 N is put forward for adjudication.",
        "The match radius was 500 m and 88% of AIS vessels over 30 m were recovered.",
    ],
)
def test_counts_and_unrelated_percentages_survive(text):
    # The last case matters: recall is a measured, defensible percentage, and a
    # guard that ate it would push the honest numbers out of the report too.
    assert not states_a_rate(text)


def test_scrub_splits_claims_and_keeps_the_supported_ones():
    claims = [
        Claim(text="15 of 60 detections had no AIS correspondence.", chunk_ids=["a#1"]),
        Claim(text="That is 25% of the total, a high dark rate.", chunk_ids=["a#2"]),
    ]
    kept, removed = scrub_rate_claims(claims)
    assert [c.text for c in kept] == [claims[0].text]
    assert [c.text for c in removed] == [claims[1].text]


def test_the_caveat_explaining_the_guard_would_not_survive_the_guard():
    """Why caveats are assembled structurally and never scrubbed.

    The caveat has to use the words "dark-vessel rate" to say that this report
    does not quote one, so running the scrubber over it would delete the very
    sentence that documents the scrubber.
    """
    from nightglass.tools.intrep import PRECISION_CAVEAT

    assert states_a_rate(PRECISION_CAVEAT)
