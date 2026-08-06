"""Tests for page_health.search_evidence.

Covers Day 5 sign-off: Decision A (component-level rescale when GSC is
unavailable), Decision B (impressions gates CTR and query diversity rather
than an independently-invented threshold), and that raw values are always
reported when GSC data was retrieved, even when gated to zero points.
"""

from page_health.gsc_client import GSCResult
from page_health.search_evidence import (
    CTR_THRESHOLD,
    IMPRESSIONS_THRESHOLD,
    QUERY_DIVERSITY_THRESHOLD,
    score_search_evidence,
)


def make_gsc_result(impressions=None, clicks=None, ctr=None, distinct_query_count=None, error=None):
    return GSCResult(
        impressions=impressions,
        clicks=clicks,
        ctr=ctr,
        distinct_query_count=distinct_query_count,
        error=error,
    )


# --- Decision A: component-level rescale ----------------------------------


def test_gsc_unavailable_rescales_entire_component_to_zero_max():
    gsc_result = make_gsc_result(error="No GSC credentials configured (GSC_CREDENTIALS_PATH not set)")
    result = score_search_evidence(gsc_result)
    assert result.gsc_available is False
    assert result.gsc_error is not None
    assert result.points == 0
    assert result.max_points == 0  # rescaled out entirely, not scored 0/15


def test_gsc_unavailable_reports_no_raw_values():
    gsc_result = make_gsc_result(error="auth failed")
    result = score_search_evidence(gsc_result)
    assert result.impressions is None
    assert result.ctr is None
    assert result.distinct_query_count is None


# --- Decision B: impressions cascades to CTR and query diversity ---------


def test_impressions_at_threshold_scores_full_points():
    gsc_result = make_gsc_result(impressions=IMPRESSIONS_THRESHOLD, clicks=10, ctr=0.05, distinct_query_count=10)
    result = score_search_evidence(gsc_result)
    assert result.impressions_points == 5


def test_impressions_below_threshold_gates_ctr_and_diversity_to_zero():
    """Even with a great CTR and many distinct queries, failing the
    impressions gate zeroes out CTR and query diversity too -- no
    separately-invented floor, impressions IS the floor."""
    gsc_result = make_gsc_result(
        impressions=IMPRESSIONS_THRESHOLD - 1,
        clicks=5,
        ctr=0.99,  # would easily pass on its own
        distinct_query_count=50,  # would easily pass on its own
    )
    result = score_search_evidence(gsc_result)
    assert result.impressions_points == 0
    assert result.ctr_points == 0
    assert result.query_diversity_points == 0
    assert result.points == 0


def test_raw_ctr_and_query_count_still_reported_even_when_gated_to_zero():
    """A noisy 99% CTR on low traffic should be visible in diagnostics even
    though it scores zero -- the scoring logic doesn't compensate, but it
    doesn't hide the number either."""
    gsc_result = make_gsc_result(impressions=3, clicks=3, ctr=0.99, distinct_query_count=1)
    result = score_search_evidence(gsc_result)
    assert result.ctr == 0.99
    assert result.distinct_query_count == 1
    assert result.ctr_points == 0
    assert result.query_diversity_points == 0


def test_good_impressions_but_low_ctr_scores_partial():
    gsc_result = make_gsc_result(
        impressions=500,
        clicks=2,
        ctr=CTR_THRESHOLD - 0.001,
        distinct_query_count=QUERY_DIVERSITY_THRESHOLD,
    )
    result = score_search_evidence(gsc_result)
    assert result.impressions_points == 5
    assert result.ctr_points == 0
    assert result.query_diversity_points == 5
    assert result.points == 10


def test_good_impressions_but_low_diversity_scores_partial():
    gsc_result = make_gsc_result(
        impressions=500,
        clicks=20,
        ctr=CTR_THRESHOLD + 0.01,
        distinct_query_count=QUERY_DIVERSITY_THRESHOLD - 1,
    )
    result = score_search_evidence(gsc_result)
    assert result.impressions_points == 5
    assert result.ctr_points == 5
    assert result.query_diversity_points == 0
    assert result.points == 10


def test_all_three_signals_pass_scores_max():
    gsc_result = make_gsc_result(
        impressions=1000,
        clicks=50,
        ctr=0.05,
        distinct_query_count=20,
    )
    result = score_search_evidence(gsc_result)
    assert result.points == 15
    assert result.max_points == 15
