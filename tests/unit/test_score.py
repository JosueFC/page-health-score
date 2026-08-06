"""Tests for page_health.score.

fetch_page, fetch_pagespeed_data, and fetch_search_console_data are all
mocked -- no test here makes any real network call. Covers: the Unscored
short-circuit, full end-to-end scoring with exact projection math (Decision
C), that a rescaled component correctly shrinks the denominator rather than
the numerator, and that the fix list is sorted by points_upside.
"""

from unittest.mock import patch

from page_health.fetch import ConfidenceTier, FetchResult
from page_health.gsc_client import GSCResult
from page_health.pagespeed_client import PageSpeedResult
from page_health.score import score_page

PAGE_URL = "https://example.com/page"

CLEAN_HTML = """
<html><head>
<title>My Page</title>
<script type="application/ld+json">{"@type": "Organization", "name": "Acme", "url": "https://example.com"}</script>
</head><body>
<h1>Welcome</h1>
<h2>Section</h2>
""" + " ".join(["word"] * 200) + """
<h3>Sub</h3>
<ul><li>a</li><li>b</li><li>c</li></ul>
<a href="/a">a</a><a href="/b">b</a><a href="/c">c</a>
<link rel="canonical" href="https://example.com/page">
</body></html>
"""


def make_fetch_result(html=CLEAN_HTML, status_code=200, final_url=PAGE_URL, error=None):
    return FetchResult(
        original_url=PAGE_URL,
        final_url=final_url,
        status_code=status_code,
        headers={},
        html=html,
        error=error,
    )


def make_psi_result(seo=95, performance=95, error=None):
    return PageSpeedResult(seo_score=seo, performance_score=performance, error=error, status_code=200 if error is None else 500)


def make_gsc_result(impressions=1000, clicks=50, ctr=0.05, distinct_query_count=20, error=None):
    return GSCResult(impressions=impressions, clicks=clicks, ctr=ctr, distinct_query_count=distinct_query_count, error=error)


# --- Unscored short-circuit ------------------------------------------


@patch("page_health.score.fetch_page")
def test_connection_failure_short_circuits_to_unscored(mock_fetch):
    mock_fetch.return_value = make_fetch_result(html=None, error="Connection refused")

    result = score_page(PAGE_URL)

    assert result.confidence_tier == ConfidenceTier.UNSCORED
    assert result.score is None
    assert result.achieved_points is None
    assert result.crawlability is None


@patch("page_health.score.fetch_search_console_data")
@patch("page_health.score.fetch_pagespeed_data")
@patch("page_health.score.fetch_page")
def test_unscored_page_never_calls_psi_or_gsc(mock_fetch, mock_psi, mock_gsc):
    """Unscored should short-circuit before any external I/O for the other
    components -- no point calling PSI/GSC for a page that couldn't even be
    fetched."""
    mock_fetch.return_value = make_fetch_result(html=None, error="Connection refused")

    score_page(PAGE_URL)

    mock_psi.assert_not_called()
    mock_gsc.assert_not_called()


# --- full pipeline, exact projection math --------------------------------


@patch("page_health.score.fetch_search_console_data")
@patch("page_health.score.fetch_pagespeed_data")
@patch("page_health.score.fetch_page")
def test_fully_clean_page_scores_100(mock_fetch, mock_psi, mock_gsc):
    mock_fetch.return_value = make_fetch_result()
    mock_psi.return_value = make_psi_result()
    mock_gsc.return_value = make_gsc_result()

    result = score_page(PAGE_URL)

    assert result.achieved_points == 90  # 20+20+15+20+15
    assert result.achievable_max_points == 90
    assert result.score == 100


@patch("page_health.score.fetch_search_console_data")
@patch("page_health.score.fetch_pagespeed_data")
@patch("page_health.score.fetch_page")
def test_partial_score_projects_correctly(mock_fetch, mock_psi, mock_gsc):
    # Non-200 status -> Crawlability loses its 10 status points (10/20
    # instead of 20/20). Everything else clean, GSC available.
    mock_fetch.return_value = make_fetch_result(status_code=404)
    mock_psi.return_value = make_psi_result()
    mock_gsc.return_value = make_gsc_result()

    result = score_page(PAGE_URL)

    # crawlability: 10 (noindex+canonical still pass) + content 20 +
    # structured_data 15 + technical 20 + search_evidence 15 = 80
    assert result.achieved_points == 80
    assert result.achievable_max_points == 90
    assert result.score == round(80 / 90 * 100)  # 89


@patch("page_health.score.fetch_search_console_data")
@patch("page_health.score.fetch_pagespeed_data")
@patch("page_health.score.fetch_page")
def test_gsc_unavailable_shrinks_denominator_not_numerator(mock_fetch, mock_psi, mock_gsc):
    mock_fetch.return_value = make_fetch_result()
    mock_psi.return_value = make_psi_result()
    mock_gsc.return_value = make_gsc_result(error="No GSC credentials configured")

    result = score_page(PAGE_URL)

    # search_evidence contributes 0/0 (rescaled), everything else full:
    # 20+20+15+20 = 75 out of 75
    assert result.achieved_points == 75
    assert result.achievable_max_points == 75
    assert result.score == 100  # still a perfect score within the reduced ceiling
    assert result.search_evidence.gsc_available is False


@patch("page_health.score.fetch_search_console_data")
@patch("page_health.score.fetch_pagespeed_data")
@patch("page_health.score.fetch_page")
def test_psi_unavailable_shrinks_denominator_by_ten(mock_fetch, mock_psi, mock_gsc):
    mock_fetch.return_value = make_fetch_result()
    mock_psi.return_value = make_psi_result(seo=None, performance=None, error="PSI outage")
    mock_gsc.return_value = make_gsc_result()

    result = score_page(PAGE_URL)

    # technical contributes only its alt(5)+internal-linking(5) = 10/10 block
    assert result.technical_quality.max_points == 10
    assert result.achievable_max_points == 80  # 20+20+15+10+15


# --- confidence and score stay structurally separate ----------------------


@patch("page_health.score.fetch_search_console_data")
@patch("page_health.score.fetch_pagespeed_data")
@patch("page_health.score.fetch_page")
def test_low_confidence_page_can_still_score_normally(mock_fetch, mock_psi, mock_gsc):
    """A thin/low-confidence page still gets a real numeric score --
    confidence tier is informational, never folded into the score itself."""
    thin_html = "<html><head><title>t</title></head><body>short page</body></html>"
    mock_fetch.return_value = make_fetch_result(html=thin_html)
    mock_psi.return_value = make_psi_result()
    mock_gsc.return_value = make_gsc_result()

    result = score_page(PAGE_URL)

    assert result.confidence_tier == ConfidenceTier.SCORED_LOW_CONFIDENCE
    assert result.score is not None
    assert isinstance(result.score, int)


# --- fix list -------------------------------------------------------------


@patch("page_health.score.fetch_search_console_data")
@patch("page_health.score.fetch_pagespeed_data")
@patch("page_health.score.fetch_page")
def test_fix_list_sorted_by_points_upside_descending(mock_fetch, mock_psi, mock_gsc):
    broken_html = "<html><head></head><body>short</body></html>"  # fails almost everything
    mock_fetch.return_value = make_fetch_result(html=broken_html, status_code=404)
    mock_psi.return_value = make_psi_result(seo=10, performance=10)
    mock_gsc.return_value = make_gsc_result(impressions=0, clicks=0, ctr=0.0, distinct_query_count=0)

    result = score_page(PAGE_URL)

    upsides = [fix.points_upside for fix in result.fixes]
    assert upsides == sorted(upsides, reverse=True)
    assert len(result.fixes) > 0


@patch("page_health.score.fetch_search_console_data")
@patch("page_health.score.fetch_pagespeed_data")
@patch("page_health.score.fetch_page")
def test_gsc_unavailable_produces_no_search_evidence_fix(mock_fetch, mock_psi, mock_gsc):
    """Not the page's fault when GSC is unavailable -- no fix suggested for
    it."""
    mock_fetch.return_value = make_fetch_result()
    mock_psi.return_value = make_psi_result()
    mock_gsc.return_value = make_gsc_result(error="no credentials")

    result = score_page(PAGE_URL)

    assert not any(fix.component == "Search Evidence" for fix in result.fixes)


@patch("page_health.score.fetch_search_console_data")
@patch("page_health.score.fetch_pagespeed_data")
@patch("page_health.score.fetch_page")
def test_perfect_page_has_no_fixes(mock_fetch, mock_psi, mock_gsc):
    mock_fetch.return_value = make_fetch_result()
    mock_psi.return_value = make_psi_result()
    mock_gsc.return_value = make_gsc_result()

    result = score_page(PAGE_URL)

    assert result.fixes == []
